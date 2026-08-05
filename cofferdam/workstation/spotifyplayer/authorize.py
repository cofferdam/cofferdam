"""Running one authorization: open the page, listen on loopback, exchange once.

The whole flow is deliberately workstation-bound. Spotify's redirect rules allow
a loopback URI and forbid ``localhost``, so the registered target is
``http://127.0.0.1:8888/callback`` — and ``127.0.0.1`` on a phone is the *phone*.
There is no arrangement in which a browser on the phone can complete this, so
Cofferdam does not pretend otherwise: it opens the authorization page in Opera
**on the workstation** and the PWA says, in as many words, to continue there.

The alternative would be binding the callback to the Tailscale address, which
would make the registered loopback URI a lie and would put an authorization
endpoint on a network. It is not done, and :mod:`.callback` refuses to bind
anything but loopback so it cannot be done by accident later.

Ordering
--------
The listener starts **before** the browser opens. A fast redirect arriving at a
socket that is not yet listening is a connection refused and a confusing failure
page, and the window is real when the browser is already running and the account
is already signed in — which is the common case on a workstation.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

from ..runtime.identity import now_iso
from .callback import CallbackListener, CallbackResult
from .errors import CODE_AUTHORIZATION_FAILED, ProviderRejected, SpotifyPlayerError
from .oauth import ATTEMPT_TTL_SECONDS, REQUIRED_SCOPES, AttemptRegistry, AuthorizationAttempt
from .tokens import tokens_from_response

# The browser the authorization page opens in. Opera is the product's browser
# for signed-in services (M2A), and the Spotify account is likely already signed
# in there — which is what turns this into two clicks rather than a password.
AUTHORIZATION_BROWSER = "opera"


class AuthorizationRunner:
    """Drives one authorization attempt to a conclusion, in the background.

    Runs on its own thread so the HTTP request that starts it returns
    immediately: the user has to go and click things in a browser, and holding a
    request open for that long would tie up a worker and time out on the phone.
    """

    def __init__(self, service, adapter, registry: Optional[AttemptRegistry] = None) -> None:
        self._service = service
        self._adapter = adapter
        self._registry = registry or AttemptRegistry()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._outcome: Optional[Dict[str, Any]] = None

    @property
    def registry(self) -> AttemptRegistry:
        return self._registry

    def status(self) -> Dict[str, Any]:
        """Bounded view of any attempt in flight.

        Carries no state, no verifier, no challenge, and no authorization URL —
        only whether something is pending and how long it has left.
        """
        attempt = self._registry.current()
        with self._lock:
            outcome = dict(self._outcome) if self._outcome else None
        return {
            "pending": attempt is not None,
            "expires_in_seconds": attempt.public_view()["expires_in_seconds"] if attempt else None,
            "last_outcome": outcome,
        }

    def start(self) -> Dict[str, Any]:
        """Begin an attempt and open the browser. Returns immediately."""
        client_id = self._service._client_id()
        if not client_id:
            raise SpotifyPlayerError(
                CODE_AUTHORIZATION_FAILED,
                "Spotify is not configured on this workstation",
                "the catalogue credentials must be set up first — see the provider setup guide",
            )

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise SpotifyPlayerError(
                    CODE_AUTHORIZATION_FAILED,
                    "an authorization attempt is already in progress",
                    "finish it in Opera on the workstation, or cancel it first",
                )
            self._outcome = None

        attempt = self._registry.start(client_id, REQUIRED_SCOPES)
        self._service.set_pending(True)

        thread = threading.Thread(
            target=self._run, args=(attempt,), name="cofferdam-spotify-authorize", daemon=True
        )
        with self._lock:
            self._thread = thread
        thread.start()

        return {
            "pending": True,
            "expires_in_seconds": attempt.public_view()["expires_in_seconds"],
            # The instruction the phone shows. The URL is not included: it can
            # only be completed on the workstation, and handing it to a phone
            # would invite a failure that looks like Cofferdam's fault.
            "message": "Continue authorization in Opera on the workstation.",
        }

    def cancel(self) -> bool:
        cancelled = self._registry.cancel()
        self._service.set_pending(False)
        return cancelled

    # -- the attempt -------------------------------------------------------

    def _run(self, attempt: AuthorizationAttempt) -> None:
        try:
            with CallbackListener() as listener:
                listener.start(lambda result: self._accepts(attempt, result))
                self._open_browser(attempt)
                received = listener.wait(self._remaining(attempt))
            if received is None:
                self._finish(
                    "timed_out",
                    "Authorization was not completed in time. Nothing was changed.",
                )
                return
            self._exchange(attempt, received)
        except SpotifyPlayerError as exc:
            self._finish("failed", exc.message)
        except Exception:
            # Never let a background thread die silently and leave the UI
            # pending forever; the bounded state is the whole point.
            self._finish("failed", "Authorization could not be completed.")
        finally:
            self._service.set_pending(False)

    @staticmethod
    def _remaining(attempt: AuthorizationAttempt) -> float:
        return max(1.0, attempt.expires_at - time.time())

    def _accepts(self, attempt: AuthorizationAttempt, result: CallbackResult) -> bool:
        """Whether this callback belongs to the live attempt.

        Runs on the listener thread. State is compared in constant time, and a
        mismatch does not cancel the attempt — an unrelated request to the
        loopback port must not be able to interrupt a real authorization.
        """
        from .oauth import states_match

        if result.error:
            return False
        if not result.code:
            return False
        return states_match(attempt.state, result.state)

    def _open_browser(self, attempt: AuthorizationAttempt) -> None:
        """Open the official authorization page on the workstation.

        The URL is built by :mod:`.oauth` from constants plus this attempt's
        PKCE values; nothing about it comes from a client request.
        """
        try:
            self._adapter.open_url(attempt.authorize_url, AUTHORIZATION_BROWSER)
        except Exception:
            # A browser that will not open is a real failure, but the listener
            # is already up: a user who opens the page themselves can still
            # finish. Recorded rather than fatal.
            self._note("the authorization page could not be opened automatically")

    def _exchange(self, attempt: AuthorizationAttempt, received: CallbackResult) -> None:
        """Consume the attempt and swap the code for tokens, exactly once."""
        consumed = self._registry.consume(received.state)
        if consumed is None:
            # Expired between delivery and here, or already used. Either way the
            # code is not exchanged: a replay must not mint a second token.
            self._finish("failed", "That authorization is no longer valid. Try again.")
            return

        try:
            payload = self._service.client.exchange_code(str(received.code), consumed.verifier)
        except SpotifyPlayerError as exc:
            self._finish("failed", exc.message)
            return

        tokens = tokens_from_response(payload, connected_at=now_iso())
        if tokens is None:
            # No refresh token means nothing renewable to store. Saving the
            # access token alone would give an hour of working playback and then
            # a mysterious disconnection.
            self._finish("failed", "Spotify did not return a durable authorization. Try again.")
            return

        # One profile read for the display name, which this endpoint returns
        # without any extra scope. A failure here is not a failure to connect.
        try:
            profile = self._service.client.profile(tokens)
            display = profile.get("display_name")
            if isinstance(display, str) and display.strip():
                tokens.display_name = " ".join(display.split())[:64]
        except Exception:
            pass

        self._service.tokens.save(tokens)
        self._service.invalidate()

        missing = tokens.missing_scopes(REQUIRED_SCOPES)
        if missing:
            self._finish(
                "missing_scopes",
                "Spotify was connected but some permissions were not granted. Reconnect and "
                "accept all of them.",
            )
            return
        self._finish("connected", "Spotify is connected.")

    def _finish(self, state: str, message: str) -> None:
        with self._lock:
            self._outcome = {"state": state, "message": message, "at": now_iso()}

    def _note(self, message: str) -> None:
        with self._lock:
            self._outcome = {"state": "note", "message": message, "at": now_iso()}


__all__ = ["AUTHORIZATION_BROWSER", "AuthorizationRunner"]
