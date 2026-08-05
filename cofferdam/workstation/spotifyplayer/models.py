"""The published Spotify playback shapes.

Two rules, both inherited from milestones that earned them.

**Bounded fields, chosen by name.** Every dictionary here is built from an
allowlist. Spotify's track object carries external URLs, image sets, market
lists, available-market arrays and a full album blob; none of that is needed to
show "what is playing", and copying the object and deleting the unwanted parts
would be one API change away from leaking something new.

**A provider device id is not an identity.** The documentation says a device id
is "unique and persistent to some extent" and may be ``null``. "To some extent"
is not an identity, so the client is given an opaque ``resource_id`` and the
provider id stays server-side. This is the same rule the audio milestone applied
to PipeWire node ids, for the same reason: an id that is *usually* stable is the
most dangerous kind, because it works in testing.

Track titles
------------
The currently playing title is allowed in the authenticated PWA — it is the
whole point of the panel — but it is **operational-log poison**. Nothing in this
module writes it anywhere; the audit path records the operation and the result
type only. See ``docs/SPOTIFY_PLAYBACK.md`` for the privacy treatment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..runtime.identity import fingerprint

# Bumped when the shape below changes incompatibly.
SPOTIFY_PLAYBACK_VERSION = 1

# -- device types ------------------------------------------------------------
#
# Spotify's `type` is free-form provider text ("computer", "smartphone",
# "speaker", "tv", "avr", "stb", "audio_dongle", "game_console", "cast_video",
# "cast_audio", "automobile", "unknown"). It is passed through lowercased and
# bounded rather than mapped, because an unrecognised device type should still
# render as itself rather than as "unknown".
MAX_DEVICE_TYPE = 32
MAX_DEVICE_NAME = 64
MAX_TEXT = 200
MAX_ARTISTS = 8
MAX_DEVICES = 32


def _text(value: object, limit: int = MAX_TEXT) -> Optional[str]:
    """Bounded, whitespace-collapsed provider text, or nothing."""
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())[:limit]
    return collapsed or None


def _non_negative_int(value: object) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def device_resource_id(host_id: str, provider_device_id: str) -> str:
    """The opaque handle a client uses to name a Spotify device.

    Host-scoped so two machines never produce the same handle for the same
    speaker, and digested so the provider's own id — which is a stable-ish
    identifier for a device in someone's home — never travels to a client or
    into an audit record.
    """
    return "spdev-" + fingerprint("cofferdam.spotify.device", host_id, provider_device_id)


@dataclass(frozen=True)
class SpotifyDevice:
    """One normalized Spotify Connect device.

    ``provider_device_id`` is deliberately **not** in :meth:`to_dict`. It is the
    server's half, used to address the provider, and it never leaves this
    process.
    """

    resource_id: str
    provider_device_id: str
    name: Optional[str]
    device_type: Optional[str]
    is_active: bool
    is_restricted: bool
    is_private_session: bool
    volume_percent: Optional[int]
    supports_volume: bool

    @property
    def controllable(self) -> bool:
        """Whether Spotify will accept Web API commands for this device.

        ``is_restricted`` is documented as: "if this is true then no Web API
        commands will be accepted by this device". That is a flat refusal, not a
        degradation, so it gates every device-targeted action.
        """
        return not self.is_restricted

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "name": self.name,
            "device_type": self.device_type,
            "is_active": self.is_active,
            "is_restricted": self.is_restricted,
            "is_private_session": self.is_private_session,
            "volume_percent": self.volume_percent,
            "supports_volume": self.supports_volume,
            "controllable": self.controllable,
            # Said out loud on every device, because a client caching this list
            # needs to know it is caching something transient.
            "identity_stability": "provider_session",
        }


def device_from_payload(host_id: str, entry: Mapping[str, Any]) -> Optional[SpotifyDevice]:
    """One device from the provider's object, or ``None`` if unusable.

    A device with a ``null`` id is dropped rather than published: the
    documentation allows it, and a device that cannot be addressed cannot be
    offered as a target. Publishing it would put a button in the UI that could
    never work.
    """
    provider_id = entry.get("id")
    if not isinstance(provider_id, str) or not provider_id.strip():
        return None
    provider_id = provider_id.strip()

    volume = entry.get("volume_percent")
    if isinstance(volume, bool) or not isinstance(volume, int) or not (0 <= volume <= 100):
        volume = None

    return SpotifyDevice(
        resource_id=device_resource_id(host_id, provider_id),
        provider_device_id=provider_id,
        name=_text(entry.get("name"), MAX_DEVICE_NAME),
        device_type=_text(entry.get("type"), MAX_DEVICE_TYPE),
        is_active=entry.get("is_active") is True,
        is_restricted=entry.get("is_restricted") is True,
        is_private_session=entry.get("is_private_session") is True,
        volume_percent=volume,
        supports_volume=entry.get("supports_volume") is True,
    )


@dataclass(frozen=True)
class NowPlaying:
    """The currently playing item, reduced to what a person reads."""

    item_type: Optional[str]
    track_id: Optional[str]
    title: Optional[str]
    artists: Tuple[str, ...]
    album: Optional[str]
    duration_ms: Optional[int]
    explicit: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_type": self.item_type,
            # The verified item handle. It is the provider's track id, which the
            # search path already validates and publishes as a result handle, so
            # it adds no new exposure — and it is what lets the client tell
            # "the track I asked for is the track now playing".
            "track_id": self.track_id,
            "title": self.title,
            "artists": list(self.artists),
            "album": self.album,
            "duration_ms": self.duration_ms,
            "explicit": self.explicit,
        }


def now_playing_from_item(item: Mapping[str, Any], item_type: Optional[str]) -> NowPlaying:
    artists: list = []
    raw_artists = item.get("artists")
    if isinstance(raw_artists, list):
        for entry in raw_artists[:MAX_ARTISTS]:
            if isinstance(entry, Mapping):
                name = _text(entry.get("name"), MAX_DEVICE_NAME)
                if name:
                    artists.append(name)

    album = None
    raw_album = item.get("album")
    if isinstance(raw_album, Mapping):
        album = _text(raw_album.get("name"))

    track_id = item.get("id")
    if not isinstance(track_id, str) or not track_id:
        track_id = None

    return NowPlaying(
        item_type=_text(item_type, MAX_DEVICE_TYPE),
        track_id=track_id,
        title=_text(item.get("name")),
        artists=tuple(artists),
        album=album,
        duration_ms=_non_negative_int(item.get("duration_ms")),
        explicit=item.get("explicit") is True,
    )


@dataclass(frozen=True)
class PlaybackSnapshot:
    """One observation of the user's Spotify playback.

    ``playback_available`` is the distinction Spotify's 204 forces: the account
    is connected and the provider answered, and there is simply nothing playing
    anywhere. That is neither an error nor "paused" — a client that conflated
    them would show a pause button for a player that does not exist.
    """

    observed_at: str
    connection: Mapping[str, Any]
    playback_available: bool = False
    is_playing: bool = False
    progress_ms: Optional[int] = None
    repeat_state: Optional[str] = None
    shuffle_state: Optional[bool] = None
    active_device_resource_id: Optional[str] = None
    devices: Tuple[SpotifyDevice, ...] = ()
    devices_available: bool = False
    now_playing: Optional[NowPlaying] = None
    muted_by_cofferdam: bool = False
    restore_volume_percent: Optional[int] = None
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    limitations: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    version: int = SPOTIFY_PLAYBACK_VERSION

    # The refusal that produced this snapshot, when one did. Deliberately **not**
    # in :meth:`to_dict` — it is a server-side carrier so an action can fail with
    # the reason the *read* found, rather than re-deriving one from an empty
    # device list. Without it, a disconnected account refuses every action with
    # "no active device", which is true of the empty list and false about the
    # world: it sends someone to switch on a speaker when what they need is to
    # authorize an account.
    problem: Optional[Exception] = field(default=None, repr=False, compare=False)

    def device_by_resource_id(self, resource_id: object) -> Optional[SpotifyDevice]:
        if not isinstance(resource_id, str) or not resource_id:
            return None
        for device in self.devices:
            if device.resource_id == resource_id:
                return device
        return None

    def active_device(self) -> Optional[SpotifyDevice]:
        for device in self.devices:
            if device.is_active:
                return device
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "observed_at": self.observed_at,
            "connection": dict(self.connection),
            "playback_available": self.playback_available,
            "is_playing": self.is_playing,
            "progress_ms": self.progress_ms,
            "repeat_state": self.repeat_state,
            "shuffle_state": self.shuffle_state,
            "active_device_resource_id": self.active_device_resource_id,
            "devices_available": self.devices_available,
            "devices": [device.to_dict() for device in self.devices],
            "now_playing": self.now_playing.to_dict() if self.now_playing else None,
            # Named so it can never be read as a Spotify feature. Spotify has no
            # mute endpoint; this flag means Cofferdam set the volume to zero.
            "muted_by_cofferdam": self.muted_by_cofferdam,
            "restore_volume_known": self.restore_volume_percent is not None,
            "capabilities": dict(self.capabilities),
            "limitations": list(self.limitations),
            "warnings": list(self.warnings),
        }


PLAYBACK_LIMITATIONS: Tuple[str, ...] = (
    "Spotify publishes no mute operation, so muting sets the device volume to zero and "
    "remembers the level to restore",
    "a Spotify device id is documented as persistent only 'to some extent', so device handles "
    "last for this session and are re-resolved before every action",
    "controlling playback requires a Spotify Premium account",
    "choosing a Spotify device changes where Spotify plays; it does not change this computer's "
    "own audio output",
)


def parse_devices(host_id: str, payload: Mapping[str, Any]) -> Tuple[SpotifyDevice, ...]:
    entries = payload.get("devices")
    if not isinstance(entries, list):
        return ()
    devices = []
    for entry in entries[:MAX_DEVICES]:
        if isinstance(entry, Mapping):
            device = device_from_payload(host_id, entry)
            if device is not None:
                devices.append(device)
    return tuple(devices)


__all__ = [
    "MAX_DEVICES",
    "NowPlaying",
    "PLAYBACK_LIMITATIONS",
    "PlaybackSnapshot",
    "SPOTIFY_PLAYBACK_VERSION",
    "SpotifyDevice",
    "device_from_payload",
    "device_resource_id",
    "now_playing_from_item",
    "parse_devices",
]
