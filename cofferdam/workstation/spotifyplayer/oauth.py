"""Authorization Code with PKCE, and the loopback callback that completes it.

Spotify's current documentation recommends this flow wherever a client secret
cannot be safely stored, and it needs **no client secret at all**. That is the
reason it is used here rather than plain Authorization Code: the catalogue
search secret already on this host never has to travel anywhere near a browser,
and nothing in the authorization path can leak it because the path never has it.

The redirect target is a loopback address, which Spotify's redirect-URI rules
explicitly permit: HTTPS is required "unless you are using a loopback address,
when HTTP is permitted", and the documentation requires the explicit IPv4 or
IPv6 form — ``localhost`` is *not* allowed. So the registered URI is
``http://127.0.0.1:8888/callback`` and the listener binds to ``127.0.0.1`` only.

Why the phone cannot finish this
--------------------------------
``127.0.0.1`` on a phone is the phone. The authorization has to be completed in
a browser **on the workstation**, so Cofferdam opens the authorization page in
Opera there and the PWA says so in as many words. Binding the callback to the
Tailscale address instead would make the loopback URI a lie and would expose an
authorization endpoint to the network; neither is acceptable, and neither is
needed.

What an attempt is
------------------
One short-lived attempt, held in memory, carrying a random ``state``, a random
PKCE verifier, and a deadline. It never touches disk: an attempt that does not
complete should leave nothing behind. Starting a new attempt replaces the old
one, so at most one is ever live — the tightest form of the "one active attempt"
rule, and the one that makes replay trivially detectable, because a consumed
attempt is gone.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple

# -- constants ---------------------------------------------------------------

AUTHORIZE_HOST = "accounts.spotify.com"
AUTHORIZE_PATH = "/authorize"

# The loopback callback. Both parts are code-owned: the client never supplies a
# redirect URI, and nothing derives one from a request header.
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 8888
CALLBACK_PATH = "/callback"
REDIRECT_URI = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}"

# Exactly what the player needs, and nothing else.
#
# `user-read-playback-state`  — playback state *and* the Connect devices list
# `user-read-currently-playing` — the currently playing item
# `user-modify-playback-state`  — pause/resume/next/previous/volume/queue/transfer
#
# Deliberately absent: `streaming` (that is the Web Playback SDK, which
# Cofferdam does not use), `user-read-email`, and `user-read-private`. The last
# one would report the account's `product` tier, which is the documented way to
# know whether an account is Premium — but the field is **marked deprecated** in
# the current documentation, and asking for a subscription-details scope to read
# a deprecated field is a poor trade. Premium is instead reported from what the
# player endpoints actually do. See errors.py.
REQUIRED_SCOPES: Tuple[str, ...] = (
    "user-read-playback-state",
    "user-read-currently-playing",
    "user-modify-playback-state",
)

# Long enough to sign in and approve on a desktop, short enough that an
# abandoned attempt cannot sit around being replayable.
ATTEMPT_TTL_SECONDS = 300

# 64 bytes -> 86 url-safe base64 characters, inside Spotify's documented 43–128
# range for a code verifier and comfortably above the entropy floor.
VERIFIER_BYTES = 64
STATE_BYTES = 32


def _b64url(raw: bytes) -> str:
    """Base64url without padding, which is what PKCE specifies."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate_state() -> str:
    return _b64url(secrets.token_bytes(STATE_BYTES))


def generate_verifier() -> str:
    """A PKCE code verifier.

    ``token_bytes`` then base64url keeps the result inside the documented
    unreserved alphabet (``[A-Za-z0-9-._~]``) by construction rather than by
    filtering, which is what makes the length predictable.
    """
    return _b64url(secrets.token_bytes(VERIFIER_BYTES))


def challenge_for(verifier: str) -> str:
    """The S256 challenge: base64url(SHA256(verifier)), unpadded."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64url(digest)


@dataclass(frozen=True)
class AuthorizationAttempt:
    """One in-flight authorization. Never persisted."""

    state: str
    verifier: str
    challenge: str
    created_at: float
    expires_at: float
    authorize_url: str

    def expired(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at

    def public_view(self, now: Optional[float] = None) -> dict:
        """What may be shown to an authenticated client.

        Note what is absent: the state, the verifier, the challenge, and the
        authorization URL. The URL carries the challenge and the state, and the
        PWA has no use for it — Cofferdam opens it in Opera on the workstation
        itself. Handing it to the phone would invite someone to complete a
        loopback flow that can only ever work on the workstation.
        """
        remaining = max(0, int(self.expires_at - (now if now is not None else time.time())))
        return {"expires_in_seconds": remaining}


def build_authorize_url(client_id: str, state: str, challenge: str, scopes: Sequence[str]) -> str:
    """The official authorization URL, assembled from constants plus PKCE values."""
    from urllib.parse import urlencode

    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": state,
            "scope": " ".join(scopes),
        }
    )
    return f"https://{AUTHORIZE_HOST}{AUTHORIZE_PATH}?{query}"


def states_match(expected: str, received: object) -> bool:
    """Constant-time state comparison.

    ``compare_digest`` needs both sides to be ``str`` of the same kind; a
    non-string arrives from a hand-made callback and is rejected before it can
    reach the comparison.
    """
    if not isinstance(received, str) or not expected:
        return False
    return hmac.compare_digest(expected, received)


class AttemptRegistry:
    """At most one live authorization attempt, in memory.

    Replacing rather than accumulating is the design: a second "Authorize"
    press invalidates the first attempt, so a stale callback for it cannot be
    completed. :meth:`consume` removes the attempt as it returns it, which makes
    a replayed callback fail on the second delivery without needing a separate
    used-code ledger.
    """

    def __init__(self, ttl_seconds: int = ATTEMPT_TTL_SECONDS, clock=time.time) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._attempt: Optional[AuthorizationAttempt] = None

    def start(self, client_id: str, scopes: Sequence[str] = REQUIRED_SCOPES) -> AuthorizationAttempt:
        now = self._clock()
        state = generate_state()
        verifier = generate_verifier()
        challenge = challenge_for(verifier)
        attempt = AuthorizationAttempt(
            state=state,
            verifier=verifier,
            challenge=challenge,
            created_at=now,
            expires_at=now + self._ttl,
            authorize_url=build_authorize_url(client_id, state, challenge, scopes),
        )
        with self._lock:
            self._attempt = attempt
        return attempt

    def current(self) -> Optional[AuthorizationAttempt]:
        """The live attempt, or ``None`` once it has expired."""
        with self._lock:
            attempt = self._attempt
            if attempt is None:
                return None
            if attempt.expired(self._clock()):
                self._attempt = None
                return None
            return attempt

    def consume(self, state: object) -> Optional[AuthorizationAttempt]:
        """Take the attempt matching ``state``, removing it either way it fails.

        A mismatched state does **not** clear the attempt: an unrelated or
        hostile request to the loopback listener must not be able to cancel a
        legitimate authorization the user is part-way through. An expired one is
        cleared, because it is already useless.
        """
        with self._lock:
            attempt = self._attempt
            if attempt is None:
                return None
            if attempt.expired(self._clock()):
                self._attempt = None
                return None
            if not states_match(attempt.state, state):
                return None
            self._attempt = None
            return attempt

    def cancel(self) -> bool:
        with self._lock:
            had = self._attempt is not None
            self._attempt = None
            return had


__all__ = [
    "ATTEMPT_TTL_SECONDS",
    "AUTHORIZE_HOST",
    "AUTHORIZE_PATH",
    "AttemptRegistry",
    "AuthorizationAttempt",
    "CALLBACK_HOST",
    "CALLBACK_PATH",
    "CALLBACK_PORT",
    "REDIRECT_URI",
    "REQUIRED_SCOPES",
    "build_authorize_url",
    "challenge_for",
    "generate_state",
    "generate_verifier",
    "states_match",
]
