"""Connection status, playback snapshots, and device resolution.

This is the layer that turns "we hold a refresh token" into a state a person can
act on, and it is deliberately conservative about what it claims.

Connection status is derived, not stored
----------------------------------------
There is no ``connected = true`` flag anywhere. A stored refresh token means
*we were authorized once*; whether that is still true is only known by asking
Spotify. So the status is computed: no file means ``disconnected``, a file whose
scopes fall short means ``missing_required_scopes``, a refresh Spotify rejects
means ``refresh_failed``, and only a working call means ``connected``.

Premium, honestly
-----------------
Cofferdam does not request ``user-read-private``. The ``product`` field it would
unlock is the documented way to read a subscription tier, but the current docs
mark it **deprecated**, and asking for a subscription-details scope to read a
deprecated field is a poor trade for a fact the player endpoints will tell us
anyway. So ``premium_required`` is reported when Spotify refuses a player call
*and* identifies that as the reason. When it refuses without saying why, the
status is ``provider_rejected`` and the message names both documented causes —
an account without Premium, or an app in development mode whose five-user
allowlist does not include this account. Guessing between them would send
someone to fix the wrong thing.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..runtime.identity import detect_host_identity, now_iso
from .client import SpotifyPlayerClient
from .errors import (
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    STATUS_MISSING_SCOPES,
    STATUS_PREMIUM_REQUIRED,
    STATUS_PROVIDER_REJECTED,
    STATUS_REFRESH_FAILED,
    STATUS_TEMPORARILY_UNAVAILABLE,
    DeviceRestricted,
    DeviceUnknown,
    MissingScopes,
    NoActiveDevice,
    NotConnected,
    PremiumRequired,
    ProviderRejected,
    ProviderUnavailable,
    RateLimited,
    SpotifyPlayerError,
)
from .models import (
    PLAYBACK_LIMITATIONS,
    NowPlaying,
    PlaybackSnapshot,
    SpotifyDevice,
    now_playing_from_item,
    parse_devices,
)
from .mutestate import MuteStateStore
from .oauth import REQUIRED_SCOPES
from .tokens import TokenStore, UserTokens

# Playback moves under you, so a snapshot is only briefly reusable. Short enough
# that a progress bar is not visibly wrong; long enough that two widgets asking
# at once cost one provider call, which matters under a 30-second rolling limit.
DEFAULT_CACHE_SECONDS = 2.0


class SpotifyPlayerService:
    """Reads Spotify playback state and resolves device handles."""

    def __init__(
        self,
        config,
        credential_store,
        token_store: Optional[TokenStore] = None,
        client: Optional[SpotifyPlayerClient] = None,
        mute_state: Optional[MuteStateStore] = None,
        cache_seconds: float = DEFAULT_CACHE_SECONDS,
        clock=time.monotonic,
    ) -> None:
        self._config = config
        self._credentials = credential_store
        self._tokens = token_store or TokenStore(config)
        self._mute = mute_state or MuteStateStore(config)
        self._client = client or SpotifyPlayerClient(self._client_id, self._tokens)
        self._cache_seconds = max(0.0, cache_seconds)
        self._clock = clock
        self._host_id = detect_host_identity().host_id

        self._lock = threading.Lock()
        self._cached: Optional[PlaybackSnapshot] = None
        self._cached_at = 0.0
        self._pending_status: Optional[str] = None

    # -- wiring ------------------------------------------------------------

    @property
    def tokens(self) -> TokenStore:
        return self._tokens

    @property
    def client(self) -> SpotifyPlayerClient:
        return self._client

    @property
    def mute_state(self) -> MuteStateStore:
        return self._mute

    @property
    def host_id(self) -> str:
        return self._host_id

    def _client_id(self) -> Optional[str]:
        """The application's client id, from the existing catalogue credentials.

        PKCE needs the id and **not** the secret, so the secret sitting in the
        same file never enters the authorization path. It is never returned by
        any route; only this module reads it, to build an authorization URL.
        """
        try:
            credentials = self._credentials.load("spotify")
        except Exception:
            return None
        return getattr(credentials, "client_id", None)

    def set_pending(self, pending: bool) -> None:
        """Mark an authorization attempt as in flight, for status reporting."""
        with self._lock:
            self._pending_status = "authorization_pending" if pending else None

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None
            self._cached_at = 0.0

    # -- connection --------------------------------------------------------

    def connection_status(self) -> Dict[str, Any]:
        """A bounded description of the account connection.

        Never contains a token, a hash, a prefix, or a length.
        """
        tokens = self._tokens.load()
        if tokens is None:
            status = self._pending_status or STATUS_DISCONNECTED
            return {
                "status": status,
                "scopes": [],
                "display_name": None,
                "connected_at": None,
                "required_scopes": list(REQUIRED_SCOPES),
                "missing_scopes": [],
                "detail": None,
            }

        missing = tokens.missing_scopes(REQUIRED_SCOPES)
        base = tokens.public_view()
        base["required_scopes"] = list(REQUIRED_SCOPES)
        base["missing_scopes"] = list(missing)
        base["detail"] = None
        if missing:
            base["status"] = STATUS_MISSING_SCOPES
            base["detail"] = "reconnect the account to grant the missing permissions"
        else:
            base["status"] = STATUS_CONNECTED
        return base

    def _authorized_tokens(self) -> UserTokens:
        """The stored authorization, or a refusal explaining why not."""
        tokens = self._tokens.load()
        if tokens is None:
            raise NotConnected()
        missing = tokens.missing_scopes(REQUIRED_SCOPES)
        if missing:
            raise MissingScopes(missing)
        return tokens

    # -- playback ----------------------------------------------------------

    def snapshot(self, refresh: bool = False) -> PlaybackSnapshot:
        with self._lock:
            if not refresh and self._cached is not None:
                if self._clock() - self._cached_at < self._cache_seconds:
                    return self._cached
            snapshot = self._collect()
            self._cached = snapshot
            self._cached_at = self._clock()
            return snapshot

    def _disconnected_snapshot(self, connection: Dict[str, Any]) -> PlaybackSnapshot:
        return PlaybackSnapshot(
            observed_at=now_iso(),
            connection=connection,
            playback_available=False,
            devices_available=False,
            capabilities=self._capabilities(connected=False),
            limitations=PLAYBACK_LIMITATIONS,
        )

    def _collect(self) -> PlaybackSnapshot:
        connection = self.connection_status()
        if connection["status"] not in (STATUS_CONNECTED,):
            return self._disconnected_snapshot(connection)

        try:
            tokens = self._authorized_tokens()
        except SpotifyPlayerError as exc:
            connection = dict(connection)
            connection["status"] = STATUS_MISSING_SCOPES
            connection["detail"] = exc.detail
            return self._disconnected_snapshot(connection)

        warnings: List[str] = []
        note = self._tokens.permissions_note()
        if note:
            warnings.append(note)

        # Devices first: the documented list is the authority for "is there
        # anywhere to play", and the playback endpoint's 204 does not
        # distinguish "nothing playing" from "no device".
        try:
            device_payload = self._client.devices(tokens)
            devices = parse_devices(self._host_id, device_payload)
            devices_available = True
        except SpotifyPlayerError as exc:
            return self._provider_problem_snapshot(connection, exc, warnings)

        try:
            state = self._client.playback_state(tokens)
        except SpotifyPlayerError as exc:
            return self._provider_problem_snapshot(connection, exc, warnings, devices=devices)

        self._tokens.persist_if_changed(tokens)

        active = next((d for d in devices if d.is_active), None)
        muted, restore = self._mute_view(active)

        if state is None:
            # 204: connected, answered, nothing playing anywhere.
            return PlaybackSnapshot(
                observed_at=now_iso(),
                connection=connection,
                playback_available=False,
                devices=devices,
                devices_available=devices_available,
                active_device_resource_id=active.resource_id if active else None,
                muted_by_cofferdam=muted,
                restore_volume_percent=restore,
                capabilities=self._capabilities(connected=True, active=active),
                limitations=PLAYBACK_LIMITATIONS,
                warnings=tuple(warnings),
            )

        item = state.get("item")
        now_playing: Optional[NowPlaying] = None
        if isinstance(item, Mapping):
            now_playing = now_playing_from_item(item, state.get("currently_playing_type"))

        # The playback payload carries its own device object; prefer the
        # devices-list entry so one identity rule produces every handle.
        device_entry = state.get("device")
        if active is None and isinstance(device_entry, Mapping):
            provider_id = device_entry.get("id")
            if isinstance(provider_id, str):
                active = next((d for d in devices if d.provider_device_id == provider_id), None)
            if active is not None:
                muted, restore = self._mute_view(active)

        progress = state.get("progress_ms")
        repeat = state.get("repeat_state")
        shuffle = state.get("shuffle_state")

        return PlaybackSnapshot(
            observed_at=now_iso(),
            connection=connection,
            playback_available=True,
            is_playing=state.get("is_playing") is True,
            progress_ms=progress if isinstance(progress, int) and not isinstance(progress, bool) else None,
            repeat_state=repeat if isinstance(repeat, str) else None,
            shuffle_state=shuffle if isinstance(shuffle, bool) else None,
            active_device_resource_id=active.resource_id if active else None,
            devices=devices,
            devices_available=devices_available,
            now_playing=now_playing,
            muted_by_cofferdam=muted,
            restore_volume_percent=restore,
            capabilities=self._capabilities(connected=True, active=active),
            limitations=PLAYBACK_LIMITATIONS,
            warnings=tuple(warnings),
        )

    def _provider_problem_snapshot(
        self,
        connection: Dict[str, Any],
        exc: SpotifyPlayerError,
        warnings: List[str],
        devices: Tuple[SpotifyDevice, ...] = (),
    ) -> PlaybackSnapshot:
        """A snapshot that reports a provider failure without losing the account."""
        connection = dict(connection)
        if isinstance(exc, PremiumRequired):
            connection["status"] = STATUS_PREMIUM_REQUIRED
        elif isinstance(exc, RateLimited):
            connection["status"] = STATUS_TEMPORARILY_UNAVAILABLE
        elif isinstance(exc, ProviderUnavailable):
            connection["status"] = STATUS_TEMPORARILY_UNAVAILABLE
        elif isinstance(exc, ProviderRejected):
            # A rejected refresh token is the one that means the connection is
            # over, rather than merely unwell.
            connection["status"] = STATUS_REFRESH_FAILED
        else:
            connection["status"] = STATUS_PROVIDER_REJECTED
        connection["detail"] = exc.detail or exc.message
        return PlaybackSnapshot(
            observed_at=now_iso(),
            connection=connection,
            playback_available=False,
            devices=devices,
            devices_available=bool(devices),
            capabilities=self._capabilities(connected=False),
            limitations=PLAYBACK_LIMITATIONS,
            warnings=tuple(warnings),
            # Carried so an action taken against this snapshot refuses with the
            # reason the read actually found — rate limited, Premium required,
            # provider unreachable — rather than with whatever can be inferred
            # from a device list that was never populated.
            problem=exc,
        )

    def _mute_view(self, active: Optional[SpotifyDevice]) -> Tuple[bool, Optional[int]]:
        """Whether *Cofferdam* muted the active device, and what it can restore.

        Both halves must hold: a stored restore level, and a device actually at
        zero. A device that someone turned back up from the Spotify app is not
        muted, and the stale record is dropped so a later unmute cannot restore
        a level from a mute the user already undid.
        """
        if active is None:
            return False, None
        restore = self._mute.restore_value(active.resource_id)
        if restore is None:
            return False, None
        if active.volume_percent is None:
            return False, restore
        if active.volume_percent > 0:
            self._mute.forget(active.resource_id)
            return False, None
        return True, restore

    def _capabilities(
        self, connected: bool, active: Optional[SpotifyDevice] = None
    ) -> Dict[str, Any]:
        """What the UI may offer, decided from documented device fields."""
        controllable = bool(active and active.controllable)
        return {
            "transport": connected and controllable,
            "volume": connected and controllable and bool(active and active.supports_volume),
            "mute": connected and controllable and bool(active and active.supports_volume),
            "transfer": connected,
            "play_result": connected,
            "queue_result": connected,
        }

    def require_playable(self, snapshot: PlaybackSnapshot) -> None:
        """Refuse, with the *right* reason, unless this snapshot can be acted on.

        Every action calls this before it looks at devices, and the ordering is
        the point. A disconnected account, a short authorization and a rate-limit
        all produce a snapshot with no devices in it; resolving a device first
        would collapse all three into "no active device", which is a true
        statement about an empty list and a false one about the world. Somebody
        would go and switch on a speaker when what they needed was to authorize
        an account.
        """
        if snapshot.problem is not None:
            raise snapshot.problem
        if snapshot.connection.get("status") == STATUS_CONNECTED:
            return
        # Not connected, or connected without enough permission. Ask the store
        # rather than the snapshot, so the refusal names the specific scopes.
        self._authorized_tokens()
        raise NotConnected()

    # -- device resolution -------------------------------------------------

    def resolve_device(
        self, snapshot: PlaybackSnapshot, resource_id: object, *, require_controllable: bool = True
    ) -> SpotifyDevice:
        """Turn an opaque handle into a live device, or refuse.

        No fallback to matching a device *name*: two speakers can share one, and
        a name is what a user typed into a phone once. A handle that does not
        resolve against the freshly-read list is stale, and stale is refused.
        """
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise DeviceUnknown("no Spotify device was named in this request")
        device = snapshot.device_by_resource_id(resource_id)
        if device is None:
            raise DeviceUnknown()
        if require_controllable and not device.controllable:
            raise DeviceRestricted()
        return device

    def target_device(
        self, snapshot: PlaybackSnapshot, resource_id: object = None
    ) -> SpotifyDevice:
        """The explicitly chosen device, or the active one, or a refusal.

        "No active device" is a documented, ordinary situation — Spotify needs
        somewhere to send audio and there may be nowhere right now — so it is a
        named state with instructions, never an error.
        """
        if resource_id:
            return self.resolve_device(snapshot, resource_id)
        active = snapshot.active_device()
        if active is None:
            raise NoActiveDevice()
        if not active.controllable:
            raise DeviceRestricted()
        return active


__all__ = ["DEFAULT_CACHE_SECONDS", "SpotifyPlayerService"]
