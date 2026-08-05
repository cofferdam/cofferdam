"""Test doubles for Spotify playback with user OAuth.

Same choice the media-search doubles made, for the same reason: the fake is a
**transport**, not an adapter. Everything above the socket — token lifecycle,
device normalization, the opaque-handle mapping, the post-action re-read, the
error classification — is the code that ships and is the code under test. A fake
at the client level would have proved only that the tests agree with themselves.

The fake is *stateful* on purpose. Every action in this milestone acts and then
re-reads, so a scripted queue of replies would have to be written in the exact
order the implementation happens to call things, and would then pass or fail on
call ordering rather than on behaviour. This double models a small Spotify
instead: a device list, a playing item, a volume. A pause really stops it, and
the re-read really observes that.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from cofferdam.workstation.mediasearch.transport import Response

# Obviously fake, and distinctive enough that a leak test can grep the world for
# them. Nothing here resembles a real Spotify credential.
FAKE_REFRESH_TOKEN = "test-refresh-token-not-a-real-credential"
FAKE_ROTATED_REFRESH_TOKEN = "test-rotated-refresh-token-not-a-real-credential"
FAKE_ACCESS_TOKEN = "test-access-token-not-a-real-credential"
FAKE_AUTHORIZATION_CODE = "test-authorization-code-not-a-real-credential"

ALL_FAKE_OAUTH_SECRETS = (
    FAKE_REFRESH_TOKEN,
    FAKE_ROTATED_REFRESH_TOKEN,
    FAKE_ACCESS_TOKEN,
    FAKE_AUTHORIZATION_CODE,
)

GRANTED_SCOPES = "user-read-playback-state user-read-currently-playing user-modify-playback-state"

# A real-looking Spotify id: 22 base62 characters, which is what the search
# adapter's validator already accepts.
TRACK_ID = "3n3Ppam7vgaVa1iaRUc9Lp"
OTHER_TRACK_ID = "7ouMYWpwJ422jRcDASZB7P"


def json_response(status: int, payload: Any, headers: Optional[Dict[str, str]] = None) -> Response:
    return Response(status=status, body=json.dumps(payload).encode("utf-8"), headers=headers or {})


def empty_response(status: int, headers: Optional[Dict[str, str]] = None) -> Response:
    return Response(status=status, body=b"", headers=headers or {})


def device(
    device_id: str = "dev-workstation",
    name: str = "Workstation",
    device_type: str = "Computer",
    is_active: bool = True,
    is_restricted: bool = False,
    supports_volume: bool = True,
    volume_percent: Optional[int] = 60,
    is_private_session: bool = False,
) -> Dict[str, Any]:
    return {
        "id": device_id,
        "is_active": is_active,
        "is_private_session": is_private_session,
        "is_restricted": is_restricted,
        "name": name,
        "type": device_type,
        "volume_percent": volume_percent,
        "supports_volume": supports_volume,
    }


def track_item(track_id: str = TRACK_ID, name: str = "Gönül Dağı") -> Dict[str, Any]:
    return {
        "id": track_id,
        "name": name,
        "duration_ms": 245000,
        "explicit": False,
        "uri": "spotify:track:" + track_id,
        "artists": [{"name": "Neşet Ertaş"}],
        "album": {"name": "Gönül Dağı"},
        # Deliberately present, and deliberately never published: the model is
        # an allowlist, so a test that asserts these are absent is asserting
        # something the fake actually supplied.
        "external_urls": {"spotify": "https://open.spotify.com/track/" + track_id},
        "available_markets": ["TR", "DE", "GB"],
        "preview_url": "https://p.scdn.co/mp3-preview/whatever",
    }


class FakeSpotify:
    """A very small Spotify, reachable only through the transport interface.

    ``calls`` records every request — host, path, method, query, headers and
    body — which is what lets a test prove that an ``Authorization`` header went
    only to the two official hosts, that a device id reached the provider as a
    query value, and that nothing else did.
    """

    def __init__(
        self,
        devices: Optional[List[Dict[str, Any]]] = None,
        *,
        is_playing: bool = True,
        item: Optional[Dict[str, Any]] = None,
        progress_ms: int = 61000,
        playback_available: bool = True,
        refresh_status: int = 200,
        rotate_refresh_token: bool = False,
        granted_scopes: str = GRANTED_SCOPES,
    ) -> None:
        self.devices = devices if devices is not None else [device()]
        self.is_playing = is_playing
        self.item = item if item is not None else track_item()
        self.progress_ms = progress_ms
        self.playback_available = playback_available
        self.refresh_status = refresh_status
        self.rotate_refresh_token = rotate_refresh_token
        self.granted_scopes = granted_scopes

        self.calls: List[Dict[str, Any]] = []
        self.queued_uris: List[str] = []

        # -- eventual consistency, which is the whole reason M2D.1 exists ----
        #
        # Spotify's player endpoints do not serve a write back immediately, and
        # real validation on the phone hit that on every volume change and on
        # the first Play now. These two counters model it: after a write, the
        # relevant read endpoint keeps describing the *previous* world for this
        # many reads before catching up.
        self.lag_device_reads = 0
        self.lag_playback_reads = 0
        self._pending_volume: Optional[Any] = None
        self._pending_item: Optional[Dict[str, Any]] = None
        self._device_reads_left = 0
        self._playback_reads_left = 0

        # A desktop application that takes this many device reads to register
        # with Spotify Connect after being launched. ``None`` devices until then.
        self.devices_after_launch: Optional[List[Dict[str, Any]]] = None
        self.launch_delay_reads = 0
        self._launch_reads_left = 0
        # path -> (status, payload). Forces one endpoint to fail without
        # disturbing any other, which is how the refusal states are exercised.
        self.failures: Dict[str, Any] = {}
        # When true, a write is accepted with 204 and changes nothing — the
        # "Spotify said yes and the speaker did not move" case that every action
        # in this milestone has to detect rather than report as success.
        self.ignore_writes = False
        self.token_calls = 0

    # -- helpers -----------------------------------------------------------

    def active(self) -> Optional[Dict[str, Any]]:
        for entry in self.devices:
            if entry.get("is_active"):
                return entry
        return None

    def by_id(self, device_id: Optional[str]) -> Optional[Dict[str, Any]]:
        for entry in self.devices:
            if entry.get("id") == device_id:
                return entry
        return None

    def fail(self, path: str, status: int, payload: Any = None) -> "FakeSpotify":
        self.failures[path] = (status, payload)
        return self

    def launched(self) -> None:
        """Called by the fake adapter: Spotify is starting, devices to follow."""
        if self.devices_after_launch is None:
            self.devices_after_launch = [device()]
        self._launch_reads_left = self.launch_delay_reads
        if self.launch_delay_reads == 0:
            # An application that registers instantly. Unrealistic, and the
            # simplest baseline to write the "it did recover" assertion against.
            self.devices = list(self.devices_after_launch)

    def _settle_devices(self) -> None:
        """Advance the devices endpoint one read towards the truth."""
        if self._launch_reads_left > 0:
            self._launch_reads_left -= 1
            if self._launch_reads_left == 0 and self.devices_after_launch is not None:
                self.devices = list(self.devices_after_launch)
        if self._pending_volume is not None:
            if self._device_reads_left > 0:
                self._device_reads_left -= 1
            else:
                target, value = self._pending_volume
                target["volume_percent"] = value
                self._pending_volume = None

    def _settle_playback(self) -> None:
        if self._pending_item is None:
            return
        if self._playback_reads_left > 0:
            self._playback_reads_left -= 1
        else:
            self.item = self._pending_item
            self.is_playing = True
            self.playback_available = True
            self._pending_item = None

    def _maybe_fail(self, path: str) -> Optional[Response]:
        if path not in self.failures:
            return None
        status, payload = self.failures[path]
        # Lower-cased, because that is what the real transport hands upward
        # after filtering the header block down to what this package acts on.
        headers = {"retry-after": "7"} if status == 429 else {}
        if payload is None:
            return empty_response(status, headers)
        return json_response(status, payload, headers)

    # -- the transport interface -------------------------------------------

    def __call__(self, host, path, *, method="GET", query=None, headers=None, body=None):
        self.calls.append(
            {
                "host": host,
                "path": path,
                "method": method,
                "query": dict(query or {}),
                "headers": dict(headers or {}),
                "body": body,
            }
        )

        if host == "accounts.spotify.com":
            return self._token(body)

        failed = self._maybe_fail(path)
        if failed is not None:
            return failed

        if path == "/v1/me":
            return json_response(200, {"display_name": "Test Listener", "id": "testlistener"})
        if path == "/v1/me/player/devices":
            self._settle_devices()
            return json_response(200, {"devices": list(self.devices)})
        if path == "/v1/me/player" and method == "GET":
            self._settle_playback()
            return self._playback()
        if path == "/v1/me/player" and method == "PUT":
            return self._transfer(body)
        if path == "/v1/me/player/play":
            return self._play(query, body)
        if path == "/v1/me/player/pause":
            return self._set_playing(False)
        if path == "/v1/me/player/next":
            return self._skip(OTHER_TRACK_ID)
        if path == "/v1/me/player/previous":
            return self._skip(OTHER_TRACK_ID)
        if path == "/v1/me/player/volume":
            return self._volume(query)
        if path == "/v1/me/player/queue":
            return self._queue(query)
        raise AssertionError(f"unexpected Spotify call: {method} {host}{path}")

    # -- endpoints ---------------------------------------------------------

    def _token(self, body) -> Response:
        self.token_calls += 1
        if self.refresh_status != 200:
            return json_response(self.refresh_status, {"error": "invalid_grant"})
        payload: Dict[str, Any] = {
            "access_token": FAKE_ACCESS_TOKEN,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": self.granted_scopes,
        }
        decoded = (body or b"").decode("utf-8")
        if "grant_type=authorization_code" in decoded:
            payload["refresh_token"] = FAKE_REFRESH_TOKEN
        elif self.rotate_refresh_token:
            payload["refresh_token"] = FAKE_ROTATED_REFRESH_TOKEN
        return json_response(200, payload)

    def _playback(self) -> Response:
        if not self.playback_available:
            # Spotify's documented "nothing is playing anywhere" answer.
            return empty_response(204)
        return json_response(
            200,
            {
                "device": self.active(),
                "repeat_state": "off",
                "shuffle_state": False,
                "timestamp": 1770000000000,
                "progress_ms": self.progress_ms,
                "is_playing": self.is_playing,
                "item": self.item,
                "currently_playing_type": "track",
                "actions": {"disallows": {"resuming": True}},
            },
        )

    def _set_playing(self, playing: bool) -> Response:
        if not self.ignore_writes:
            self.is_playing = playing
            self.playback_available = True
        return empty_response(204)

    def _play(self, query, body) -> Response:
        if self.ignore_writes:
            return empty_response(204)
        payload = json.loads((body or b"{}").decode("utf-8"))
        uris = payload.get("uris") or []
        if uris:
            started = track_item(str(uris[0]).rsplit(":", 1)[-1])
            if self.lag_playback_reads:
                # Accepted, and the state endpoint keeps describing the previous
                # track for a few reads. Exactly what the phone hit.
                self._pending_item = started
                self._playback_reads_left = self.lag_playback_reads
                return empty_response(204)
            self.item = started
            self.progress_ms = 0
        self.is_playing = True
        self.playback_available = True
        return empty_response(204)

    def _skip(self, track_id: str) -> Response:
        if not self.ignore_writes:
            self.item = track_item(track_id, name="Zahidem")
            self.progress_ms = 0
            self.is_playing = True
            self.playback_available = True
        return empty_response(204)

    def _volume(self, query) -> Response:
        if self.ignore_writes:
            return empty_response(204)
        target = self.by_id((query or {}).get("device_id")) or self.active()
        if target is not None:
            value = int((query or {})["volume_percent"])
            if self.lag_device_reads:
                # Accepted, and the devices endpoint keeps reporting the old
                # level for a few reads. This is the 50 → 80 → "device says 50"
                # failure, reproduced.
                self._pending_volume = (target, value)
                self._device_reads_left = self.lag_device_reads
            else:
                target["volume_percent"] = value
        return empty_response(204)

    def _transfer(self, body) -> Response:
        if self.ignore_writes:
            return empty_response(204)
        payload = json.loads((body or b"{}").decode("utf-8"))
        wanted = (payload.get("device_ids") or [None])[0]
        for entry in self.devices:
            entry["is_active"] = entry.get("id") == wanted
        return empty_response(204)

    def _queue(self, query) -> Response:
        self.queued_uris.append((query or {}).get("uri"))
        return empty_response(204)


class FakeApplicationAdapter:
    """The allowlisted application launcher, as far as playback can see it.

    Records what was launched, and optionally tells the fake Spotify that a
    desktop client is now starting — which is how the cold-start tests model a
    device that appears a few seconds *after* the launch rather than instantly.
    """

    def __init__(self, spotify: Optional[FakeSpotify] = None, fail: bool = False) -> None:
        self.launches: List[str] = []
        self._spotify = spotify
        self._fail = fail

    def open_application(self, application: str):
        self.launches.append(application)
        if self._fail:
            from cofferdam.workstation.errors import AdapterUnsupported

            raise AdapterUnsupported("application not installed: " + application)
        if self._spotify is not None:
            self._spotify.launched()
        return object()


def write_user_tokens(config, *, scopes: str = GRANTED_SCOPES, refresh: str = FAKE_REFRESH_TOKEN):
    """Put a stored authorization on disk the way a real connect would."""
    from cofferdam.workstation.spotifyplayer.tokens import TokenStore, UserTokens

    store = TokenStore(config)
    store.save(
        UserTokens(
            refresh_token=refresh,
            scopes=tuple(scopes.split()) if scopes else (),
            display_name="Test Listener",
            connected_at="2026-08-05T11:00:00.000Z",
        )
    )
    return store


def instant_recovery(service, adapter, *, appearance_attempts: int = 5,
                     activation_attempts: int = 3):
    """A :class:`DeviceRecovery` whose bounded waits take no wall time.

    The windows are still *bounded* — the count is what the tests assert on —
    but the interval is zero, so a twenty-second cold start runs in a
    millisecond. A test that actually slept would be measuring ``time.sleep``.
    """
    from cofferdam.workstation.spotifyplayer.coldstart import DeviceRecovery, SpotifyLauncher
    from cofferdam.workstation.spotifyplayer.confirm import ConfirmWindow

    return DeviceRecovery(
        service,
        SpotifyLauncher(adapter),
        sleeper=lambda seconds: None,
        appearance_window=ConfirmWindow(attempts=appearance_attempts, interval_seconds=0.0),
        activation_window=ConfirmWindow(attempts=activation_attempts, interval_seconds=0.0),
    )


__all__ = [
    "ALL_FAKE_OAUTH_SECRETS",
    "FakeApplicationAdapter",
    "instant_recovery",
    "FAKE_ACCESS_TOKEN",
    "FAKE_AUTHORIZATION_CODE",
    "FAKE_REFRESH_TOKEN",
    "FAKE_ROTATED_REFRESH_TOKEN",
    "FakeSpotify",
    "GRANTED_SCOPES",
    "OTHER_TRACK_ID",
    "TRACK_ID",
    "device",
    "empty_response",
    "json_response",
    "track_item",
    "write_user_tokens",
]
