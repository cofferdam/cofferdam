"""Where the user's Spotify authorization lives, and how little of it is kept.

Separate file, separate concept
-------------------------------
Catalogue search uses an *application* credential — a client id and secret in
``media_providers.json`` — that says nothing about any person. This file holds a
*user* credential: proof that one human authorized Cofferdam to control their
playback. Two different things with two different blast radii, so two files.
Deleting this one disconnects an account; deleting that one turns off search.

    $COFFERDAM_HOME/secrets/spotify_user_oauth.json   0600, in a 0700 directory

What is stored, and what is not
-------------------------------
The **refresh token** is stored, because it is the only durable half and losing
it means asking the user to authorize again. The **access token is not**: it
lives in memory, expires in about an hour, and writing it to disk would put a
second credential on the filesystem for no gain.

Alongside it: the granted scopes (so a missing permission is detected before an
action fails), the expiry the provider reported, and — only if the provider
supplied it — a bounded display identity. Nothing else. No email, no country, no
profile blob, no listening data.

The rotation rule, which the documentation forced
--------------------------------------------------
Spotify's PKCE documentation states that when refreshing, "the response might
not include a new refresh token". So a refresh that returns no
``refresh_token`` must **keep the existing one**. Overwriting it with the
absent value would disconnect a perfectly good account on the next restart, and
it would look like the user's fault. :meth:`TokenStore.apply_refresh` is written
around that single sentence.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

TOKEN_FILENAME = "spotify_user_oauth.json"

# Refresh a little before the provider's expiry rather than at it: a token that
# expires mid-request produces a confusing failure for something that was simply
# due for renewal.
EXPIRY_SAFETY_SECONDS = 60

FILE_MODE = 0o600
DIR_MODE = 0o700


@dataclass
class UserTokens:
    """The stored half of an authorization, plus the in-memory access token."""

    refresh_token: str
    scopes: Tuple[str, ...] = ()
    display_name: Optional[str] = None
    account_id: Optional[str] = None
    connected_at: Optional[str] = None

    # Never persisted. Present only for the life of the process.
    access_token: Optional[str] = field(default=None, repr=False, compare=False)
    access_expires_at: float = field(default=0.0, repr=False, compare=False)

    def __repr__(self) -> str:  # pragma: no cover - trivial, but load-bearing
        # A dataclass repr would print the refresh token into any exception
        # traceback that happens to carry this object. The same reasoning the
        # catalogue credential store already applies.
        return f"UserTokens(refresh_token=<redacted>, scopes={list(self.scopes)!r})"

    def access_token_valid(self, now: Optional[float] = None) -> bool:
        moment = now if now is not None else time.time()
        return bool(self.access_token) and moment < (self.access_expires_at - EXPIRY_SAFETY_SECONDS)

    def missing_scopes(self, required: Sequence[str]) -> Tuple[str, ...]:
        granted = set(self.scopes)
        return tuple(scope for scope in required if scope not in granted)

    def public_view(self) -> Dict[str, Any]:
        """What an authenticated client may see about the connection.

        No token, no hash, no prefix, no length. The display name is included
        only because the provider volunteered it on the profile object without
        any extra scope; it is bounded to a sane length so a provider cannot
        write an essay into the UI.
        """
        return {
            "scopes": list(self.scopes),
            "display_name": (self.display_name or None),
            "connected_at": self.connected_at,
        }


def _bounded_name(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())[:64]
    return text or None


def tokens_from_response(
    response: Dict[str, Any], now: Optional[float] = None, connected_at: Optional[str] = None
) -> Optional[UserTokens]:
    """Build a fresh authorization from a token response, or ``None``.

    ``None`` when the response carries no durable refresh token: an
    authorization Cofferdam cannot renew is not one worth storing, and the
    caller reports that honestly rather than saving a half-connection that will
    fail at the next restart.

    Constructing directly from the response — rather than making an empty
    object and filling it in — keeps a token-shaped field from ever holding a
    placeholder literal, which is both clearer and one less thing for the
    committed-secret scanner to have to reason about.
    """
    refresh = response.get("refresh_token")
    if not isinstance(refresh, str) or not refresh.strip():
        return None

    moment = now if now is not None else time.time()
    access = response.get("access_token")
    granted = response.get("scope")
    scopes: Tuple[str, ...] = ()
    if isinstance(granted, str) and granted.strip():
        scopes = tuple(s for s in granted.split() if s)

    tokens = UserTokens(
        refresh_token=refresh.strip(),
        scopes=scopes,
        connected_at=connected_at,
    )
    tokens.access_token = access if isinstance(access, str) and access else None
    try:
        tokens.access_expires_at = moment + int(response.get("expires_in"))
    except (TypeError, ValueError):
        tokens.access_expires_at = moment
    return tokens


class TokenStore:
    """Atomic, owner-only persistence for one user's Spotify authorization."""

    def __init__(self, config) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._cached: Optional[UserTokens] = None
        self._loaded = False

    @property
    def path(self) -> Path:
        return Path(self._config.secrets_dir) / TOKEN_FILENAME

    # -- reading -----------------------------------------------------------

    def load(self) -> Optional[UserTokens]:
        """The stored authorization, or ``None``.

        A malformed or unreadable file yields ``None`` rather than raising: the
        honest consequence is "not connected", which the user can fix by
        authorizing again. Crashing the status endpoint over a corrupt file
        would take the whole player panel down with it.
        """
        with self._lock:
            if self._loaded and self._cached is not None:
                return self._cached
            tokens = self._read()
            self._cached = tokens
            self._loaded = True
            return tokens

    def _read(self) -> Optional[UserTokens]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            document = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(document, dict):
            return None
        refresh = document.get("refresh_token")
        if not isinstance(refresh, str) or not refresh.strip():
            return None
        scopes = document.get("scopes")
        scope_tuple: Tuple[str, ...] = ()
        if isinstance(scopes, list):
            scope_tuple = tuple(s for s in scopes if isinstance(s, str) and s)
        connected_at = document.get("connected_at")
        return UserTokens(
            refresh_token=refresh.strip(),
            scopes=scope_tuple,
            display_name=_bounded_name(document.get("display_name")),
            account_id=_bounded_name(document.get("account_id")),
            connected_at=connected_at if isinstance(connected_at, str) else None,
        )

    # -- writing -----------------------------------------------------------

    def save(self, tokens: UserTokens) -> None:
        """Write atomically, with owner-only permissions from the first byte.

        The temporary file is created by ``mkstemp``, which is 0600 already, and
        the mode is set explicitly anyway before any content is written — so the
        secret is never momentarily world-readable, not even between ``open``
        and ``chmod``. ``os.replace`` then swaps it in atomically, so a reader
        sees either the old file or the new one and never a half-written one.
        """
        directory = Path(self._config.secrets_dir)
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, DIR_MODE)
        except OSError:
            pass

        payload = json.dumps(
            {
                "version": 1,
                "refresh_token": tokens.refresh_token,
                "scopes": list(tokens.scopes),
                "display_name": tokens.display_name,
                "account_id": tokens.account_id,
                "connected_at": tokens.connected_at,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        handle, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".spotify-", suffix=".tmp")
        try:
            os.chmod(tmp_name, FILE_MODE)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_name, self.path)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        # Verified after the write, not assumed from the umask.
        try:
            os.chmod(self.path, FILE_MODE)
        except OSError:
            pass

        with self._lock:
            self._cached = tokens
            self._loaded = True

    def apply_refresh(
        self, tokens: UserTokens, response: Dict[str, Any], now: Optional[float] = None
    ) -> UserTokens:
        """Fold a token response into the stored authorization.

        The rotation rule lives here: a response **without** ``refresh_token``
        keeps the one already held. Spotify documents that this happens, and
        treating the absence as "the token is gone" would disconnect a working
        account at the next restart.
        """
        moment = now if now is not None else time.time()
        access = response.get("access_token")
        expires_in = response.get("expires_in")
        rotated = response.get("refresh_token")
        granted = response.get("scope")

        tokens.access_token = access if isinstance(access, str) and access else None
        try:
            tokens.access_expires_at = moment + int(expires_in)
        except (TypeError, ValueError):
            tokens.access_expires_at = moment

        if isinstance(rotated, str) and rotated.strip():
            tokens.refresh_token = rotated.strip()
        if isinstance(granted, str) and granted.strip():
            tokens.scopes = tuple(s for s in granted.split() if s)
        return tokens

    def persist_if_changed(self, tokens: UserTokens) -> None:
        """Write only when the durable half actually moved.

        Rewriting the file on every access-token refresh would mean a disk write
        an hour for no change in content, and every write is another chance to
        lose a good token to a full disk.
        """
        stored = self._read()
        if (
            stored is None
            or stored.refresh_token != tokens.refresh_token
            or stored.scopes != tokens.scopes
            or stored.display_name != tokens.display_name
        ):
            self.save(tokens)

    # -- removal -----------------------------------------------------------

    def clear(self) -> bool:
        """Remove the local authorization atomically.

        Returns whether a file was actually removed. ``unlink`` on a POSIX
        filesystem is atomic: the entry is either there or gone, never partly
        removed, so a reader can never observe a truncated token file.

        **This does not revoke anything at Spotify.** Spotify's API offers no
        revocation endpoint for this flow, so claiming revocation here would be
        a lie the user might rely on. The documentation tells them where to
        revoke it in their account settings instead.
        """
        with self._lock:
            self._cached = None
            self._loaded = True
            try:
                self.path.unlink()
                return True
            except FileNotFoundError:
                return False
            except OSError:
                raise

    def permissions_note(self) -> Optional[str]:
        """A warning if the stored token is readable by anyone else."""
        try:
            mode = self.path.stat().st_mode & 0o777
        except OSError:
            return None
        if mode & 0o077:
            return (
                "the Spotify authorization file is readable by other accounts on this machine; "
                f"run: chmod 600 {TOKEN_FILENAME} in your secrets directory"
            )
        return None


__all__ = [
    "DIR_MODE",
    "EXPIRY_SAFETY_SECONDS",
    "FILE_MODE",
    "TOKEN_FILENAME",
    "TokenStore",
    "UserTokens",
    "tokens_from_response",
]
