"""The authenticated Spotify player client: one place that speaks to Spotify.

It reuses :mod:`cofferdam.workstation.mediasearch.transport`, which already
enforces the network policy this milestone requires: a code-owned host
allowlist, HTTPS with certificate verification, bounded connect/read timeouts, a
bounded response body, **no redirects**, and **no retry loop**. Adding a second
HTTP path for playback would have meant a second set of those properties to keep
true, which is how one of them eventually stops being true.

Because the transport never follows a redirect, the "never forward an
Authorization header across a redirect to another host" rule holds by
construction rather than by inspection: there is no hop to forward anything on.

One refresh per operation
-------------------------
An expired access token is refreshed **at most once** per operation. If the
retried call still returns 401, that is reported rather than retried again: a
loop here would turn a rejected refresh token into a burst of requests against
an account that has already said no, and Spotify rate limits over a rolling
30-second window.

Status handling
---------------
Player writes answer ``204 No Content`` on success. Anything else is mapped into
the bounded vocabulary in :mod:`.errors`, and provider text is never forwarded —
a provider must not be able to write the words a user reads.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..mediasearch.transport import Response, TransportError, form_body
from ..mediasearch.transport import request as default_request
from .errors import (
    DeviceRestricted,
    NotConnected,
    PremiumRequired,
    ProviderRejected,
    ProviderUnavailable,
    RateLimited,
    SpotifyPlayerError,
)
from .oauth import REDIRECT_URI
from .tokens import UserTokens

ACCOUNTS_HOST = "accounts.spotify.com"
API_HOST = "api.spotify.com"

TOKEN_PATH = "/api/token"
PLAYER_PATH = "/v1/me/player"
DEVICES_PATH = "/v1/me/player/devices"
PLAY_PATH = "/v1/me/player/play"
PAUSE_PATH = "/v1/me/player/pause"
NEXT_PATH = "/v1/me/player/next"
PREVIOUS_PATH = "/v1/me/player/previous"
VOLUME_PATH = "/v1/me/player/volume"
QUEUE_PATH = "/v1/me/player/queue"
PROFILE_PATH = "/v1/me"

# Reason hints Spotify has historically sent on player errors. They are **not**
# in the current documentation — the API-calls concept page documents only
# `QUOTA_EXCEEDED` — so they are treated as hints that may improve a message and
# never as something whose absence changes a decision. An unrecognised reason
# falls through to the fail-closed branch.
HINT_PREMIUM = "PREMIUM_REQUIRED"
HINT_RESTRICTED = ("DEVICE_NOT_CONTROLLABLE", "VOLUME_CONTROL_DISALLOWED")


class SpotifyPlayerClient:
    """Authenticated calls to the official Spotify player endpoints."""

    def __init__(self, client_id_provider, token_store, request=default_request, clock=time.time):
        self._client_id_provider = client_id_provider
        self._tokens = token_store
        self._request = request
        self._clock = clock

    # -- token lifecycle ---------------------------------------------------

    def _client_id(self) -> str:
        client_id = self._client_id_provider()
        if not client_id:
            raise NotConnected(
                "the Spotify application's client id is not configured on this host"
            )
        return client_id

    def exchange_code(self, code: str, verifier: str) -> Dict[str, Any]:
        """Swap an authorization code for tokens. PKCE: no client secret."""
        body, headers = form_body(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": self._client_id(),
                "code_verifier": verifier,
            }
        )
        response = self._call(ACCOUNTS_HOST, TOKEN_PATH, method="POST", headers=headers, body=body)
        if response.status != 200:
            raise ProviderRejected(
                "Spotify did not accept the authorization; it may have expired — try again"
            )
        payload = self._json(response)
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise ProviderRejected("Spotify returned an unusable token response")
        return payload

    def refresh(self, tokens: UserTokens) -> Dict[str, Any]:
        """Renew the access token from the stored refresh token."""
        body, headers = form_body(
            {
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
                "client_id": self._client_id(),
            }
        )
        response = self._call(ACCOUNTS_HOST, TOKEN_PATH, method="POST", headers=headers, body=body)
        if response.status in (400, 401):
            # invalid_grant: the user revoked access, or the token was rotated
            # away. Not retried, and never reported as a transient failure —
            # the honest outcome is that the connection is gone.
            raise ProviderRejected(
                "Spotify rejected the stored authorization; connect the account again"
            )
        if response.status != 200:
            raise self._map_status(response)
        payload = self._json(response)
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise ProviderRejected("Spotify returned an unusable token response")
        return payload

    def _authorized_headers(self, tokens: UserTokens) -> Dict[str, str]:
        return {"Authorization": "Bearer " + str(tokens.access_token)}

    def _ensure_access(self, tokens: UserTokens) -> None:
        if tokens.access_token_valid(self._clock()):
            return
        payload = self.refresh(tokens)
        self._tokens.apply_refresh(tokens, payload, now=self._clock())
        self._tokens.persist_if_changed(tokens)

    # -- the one call path -------------------------------------------------

    def _authorized(
        self,
        tokens: UserTokens,
        path: str,
        *,
        method: str = "GET",
        query: Optional[Mapping[str, str]] = None,
        body: Optional[bytes] = None,
        json_body: Optional[Mapping[str, Any]] = None,
        allow_refresh: bool = True,
    ) -> Response:
        """One authorized request, refreshing at most once on a 401."""
        self._ensure_access(tokens)

        headers = self._authorized_headers(tokens)
        payload = body
        if json_body is not None:
            import json as _json

            payload = _json.dumps(dict(json_body)).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(payload))

        response = self._call(API_HOST, path, method=method, query=query, headers=headers, body=payload)
        if response.status == 401 and allow_refresh:
            # The token expired between the check and the call, or was revoked.
            # Exactly one more attempt, then the answer stands.
            tokens.access_token = None
            return self._authorized(
                tokens,
                path,
                method=method,
                query=query,
                body=body,
                json_body=json_body,
                allow_refresh=False,
            )
        return response

    def _call(self, host: str, path: str, **kwargs) -> Response:
        try:
            return self._request(host, path, **kwargs)
        except TransportError as exc:
            # The transport's messages are code-owned and safe, but they describe
            # a transport, not a product state.
            raise ProviderUnavailable(str(exc)) from None

    @staticmethod
    def _json(response: Response) -> Any:
        try:
            return response.json()
        except TransportError:
            raise ProviderRejected("Spotify returned a response Cofferdam could not read") from None

    def _map_status(self, response: Response) -> SpotifyPlayerError:
        """Turn a non-success status into a bounded, justified refusal."""
        if response.status == 429:
            return RateLimited(response.retry_after_seconds)
        if response.status in (500, 502, 503, 504):
            return ProviderUnavailable("Spotify reported a server-side problem")

        hint = self._reason_hint(response)
        if response.status == 403:
            if hint == HINT_PREMIUM:
                return PremiumRequired()
            if hint in HINT_RESTRICTED:
                return DeviceRestricted()
            return ProviderRejected()
        if response.status == 404:
            # Historically "no active device". Undocumented now, so the devices
            # list is the authority and this stays a plain refusal.
            return ProviderRejected(
                "Spotify could not find something this request referred to — the device list may "
                "have changed; refresh and try again"
            )
        return ProviderRejected()

    @staticmethod
    def _reason_hint(response: Response) -> Optional[str]:
        """The provider's ``reason``, if it sent a recognisable one.

        Read defensively and used only to *improve* a message. Nothing branches
        on its absence, because the current documentation does not promise it.
        """
        try:
            payload = response.json()
        except TransportError:
            return None
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if not isinstance(error, dict):
            return None
        reason = error.get("reason")
        return reason if isinstance(reason, str) else None

    # -- reads -------------------------------------------------------------

    def playback_state(self, tokens: UserTokens) -> Optional[Dict[str, Any]]:
        """Current playback, or ``None`` when Spotify answers 204.

        204 means the account is fine and nothing is playing anywhere. It is a
        real state, not an error, and the caller renders it as such.
        """
        response = self._authorized(tokens, PLAYER_PATH)
        if response.status == 204:
            return None
        if response.status != 200:
            raise self._map_status(response)
        payload = self._json(response)
        return payload if isinstance(payload, dict) else None

    def devices(self, tokens: UserTokens) -> Dict[str, Any]:
        response = self._authorized(tokens, DEVICES_PATH)
        if response.status != 200:
            raise self._map_status(response)
        payload = self._json(response)
        return payload if isinstance(payload, dict) else {}

    def profile(self, tokens: UserTokens) -> Dict[str, Any]:
        """The account's public profile.

        Called once at connect time for the display name, which the endpoint
        returns without any additional scope. ``product`` is not read: it needs
        ``user-read-private`` and is marked deprecated in the current docs.
        """
        response = self._authorized(tokens, PROFILE_PATH)
        if response.status != 200:
            return {}
        payload = self._json(response)
        return payload if isinstance(payload, dict) else {}

    # -- writes ------------------------------------------------------------
    #
    # Each returns None on success and raises a bounded error otherwise.
    # None of them reports success: the caller re-reads state and decides.

    def _write(self, tokens: UserTokens, path: str, **kwargs) -> None:
        response = self._authorized(tokens, path, **kwargs)
        if response.status not in (200, 202, 204):
            raise self._map_status(response)

    def play_uris(
        self, tokens: UserTokens, uris: Sequence[str], device_id: Optional[str] = None
    ) -> None:
        query = {"device_id": device_id} if device_id else None
        self._write(tokens, PLAY_PATH, method="PUT", query=query, json_body={"uris": list(uris)})

    def resume(self, tokens: UserTokens, device_id: Optional[str] = None) -> None:
        # Resume is the same endpoint with no body: Spotify continues whatever
        # was loaded rather than starting something new.
        query = {"device_id": device_id} if device_id else None
        self._write(tokens, PLAY_PATH, method="PUT", query=query)

    def pause(self, tokens: UserTokens, device_id: Optional[str] = None) -> None:
        query = {"device_id": device_id} if device_id else None
        self._write(tokens, PAUSE_PATH, method="PUT", query=query)

    def next_track(self, tokens: UserTokens, device_id: Optional[str] = None) -> None:
        query = {"device_id": device_id} if device_id else None
        self._write(tokens, NEXT_PATH, method="POST", query=query)

    def previous_track(self, tokens: UserTokens, device_id: Optional[str] = None) -> None:
        query = {"device_id": device_id} if device_id else None
        self._write(tokens, PREVIOUS_PATH, method="POST", query=query)

    def set_volume(
        self, tokens: UserTokens, volume_percent: int, device_id: Optional[str] = None
    ) -> None:
        if not isinstance(volume_percent, int) or isinstance(volume_percent, bool):
            raise ProviderRejected("invalid volume")
        if volume_percent < 0 or volume_percent > 100:
            # Structural backstop. The action layer refuses out-of-range input
            # long before here; this makes it impossible for a future caller to
            # reach an out-of-range volume through this client by mistake.
            raise ProviderRejected("volume out of range")
        query = {"volume_percent": str(volume_percent)}
        if device_id:
            query["device_id"] = device_id
        self._write(tokens, VOLUME_PATH, method="PUT", query=query)

    def transfer(self, tokens: UserTokens, device_id: str, play: bool = False) -> None:
        # `device_ids` takes exactly one element; more than one is a documented
        # 400. `play` is passed explicitly rather than omitted so the intent is
        # in the request instead of in a default that could change.
        self._write(
            tokens, PLAYER_PATH, method="PUT", json_body={"device_ids": [device_id], "play": play}
        )

    def queue(self, tokens: UserTokens, uri: str, device_id: Optional[str] = None) -> None:
        query = {"uri": uri}
        if device_id:
            query["device_id"] = device_id
        self._write(tokens, QUEUE_PATH, method="POST", query=query)


__all__ = [
    "ACCOUNTS_HOST",
    "API_HOST",
    "DEVICES_PATH",
    "PAUSE_PATH",
    "PLAYER_PATH",
    "PLAY_PATH",
    "PREVIOUS_PATH",
    "NEXT_PATH",
    "QUEUE_PATH",
    "SpotifyPlayerClient",
    "TOKEN_PATH",
    "VOLUME_PATH",
]
