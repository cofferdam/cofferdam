"""The published YouTube dedicated-player shapes.

Three rules, all inherited from milestones that earned them.

**Bounded fields, chosen by name.** The IFrame Player API can be asked for a
video URL, an embed code, playback quality lists, spherical properties and raw
event objects. None of that is needed to answer "what is playing and is it
playing", and copying an event object and deleting the unwanted parts would be
one API change away from leaking something new. Every dictionary here is built
field by field from an allowlist.

**A video id is not an identity a client may hold.** The eleven-character
YouTube id *is* a launch target: anything holding one can construct a watch URL.
So the client is given ``queue_item_id`` and ``video_handle`` — Cofferdam-scoped
opaque handles — and the id stays server-side, exactly as the Spotify milestone
kept provider device ids and the search milestone kept provider items. This is
what makes "the client cannot submit a video id" structural rather than merely
validated.

**Titles are shown, never logged.** The authenticated PWA may display the
current video's title and channel — that is the whole point of the panel. They
are operational-log poison: nothing in this module writes them anywhere, and the
audit path records the operation and the outcome only. See
``docs/YOUTUBE_PLAYER.md`` for the privacy treatment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, Tuple

from ..runtime.identity import fingerprint, now_iso

#: Bumped when the wire shape below changes in a way a client could notice.
#: The PWA reads it so an older cached shell can say "reload me" rather than
#: mis-render a panel it does not understand.
YOUTUBE_PLAYER_VERSION = 1

# -- connection states -------------------------------------------------------
#
# Closed vocabulary describing the *tab*, not the video. A client renders these,
# and an unrecognised one would render as nothing.

CONNECTION_UNAVAILABLE = "unavailable"
CONNECTION_DISCONNECTED = "disconnected"
CONNECTION_LAUNCHING = "launching"
CONNECTION_WAITING = "waiting_for_player"
CONNECTION_READY = "ready"

CONNECTION_STATES: Tuple[str, ...] = (
    CONNECTION_UNAVAILABLE,
    CONNECTION_DISCONNECTED,
    CONNECTION_LAUNCHING,
    CONNECTION_WAITING,
    CONNECTION_READY,
)

# -- playback states ---------------------------------------------------------
#
# Cofferdam's own vocabulary, mapped from the documented IFrame Player API
# numeric states in :mod:`.channel`. Deliberately *not* the raw numbers: -1, 0,
# 1, 2, 3 and 5 are an API detail, and a client branching on them would be
# coupled to YouTube's constants rather than to Cofferdam's contract.

PLAYBACK_IDLE = "idle"
PLAYBACK_CUEING = "cueing"
PLAYBACK_BUFFERING = "buffering"
PLAYBACK_PLAYING = "playing"
PLAYBACK_PAUSED = "paused"
PLAYBACK_ENDED = "ended"
PLAYBACK_AUTOPLAY_BLOCKED = "autoplay_blocked"
PLAYBACK_ERROR = "error"

PLAYBACK_STATES: Tuple[str, ...] = (
    PLAYBACK_IDLE,
    PLAYBACK_CUEING,
    PLAYBACK_BUFFERING,
    PLAYBACK_PLAYING,
    PLAYBACK_PAUSED,
    PLAYBACK_ENDED,
    PLAYBACK_AUTOPLAY_BLOCKED,
    PLAYBACK_ERROR,
)

#: The states in which a video is loaded and the player is not going to start it
#: without help. Kept as a set because two different call sites need the same
#: answer and neither should re-derive it.
BLOCKED_STATES = frozenset({PLAYBACK_AUTOPLAY_BLOCKED})

# -- bounds ------------------------------------------------------------------

MAX_TITLE = 200
MAX_CHANNEL = 120

#: Twenty-five is well past what a person queues in an evening and small enough
#: that the whole queue — handles, bounded titles, bounded channels — stays a
#: few kilobytes. The bound is enforced on insert, not on render.
MAX_QUEUE_ITEMS = 25

#: A video longer than this is almost certainly a stream with a nonsense
#: duration rather than a film. Reported as unknown rather than as a number that
#: would render as "5124:33".
MAX_DURATION_SECONDS = 24 * 60 * 60


def bounded_display_text(value: object, limit: int) -> Optional[str]:
    """Bounded, control-free, single-line display text, or nothing.

    Provider titles arrive HTML-escaped from the Data API and are **not**
    unescaped: they are carried as text and the PWA escapes again on render, so
    there is no step at which a title could become markup.
    """
    if not isinstance(value, str):
        return None
    cleaned = "".join(
        character
        for character in value
        if not (ord(character) < 0x20 or ord(character) == 0x7F or 0x80 <= ord(character) <= 0x9F)
    ).strip()
    if not cleaned:
        return None
    collapsed = " ".join(cleaned.split())
    if len(collapsed) > limit:
        collapsed = collapsed[: limit - 1].rstrip() + "…"
    return collapsed


def bounded_seconds(value: object) -> Optional[int]:
    """A whole non-negative second count inside a sane bound, or nothing.

    The player reports floats, and a live stream reports ``0`` for duration.
    Both are normalised here so no client has to guess what a bare ``0`` means:
    it means the player has no duration to report.
    """
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):  # NaN / infinity
        return None
    whole = int(value)
    if whole < 0 or whole > MAX_DURATION_SECONDS:
        return None
    return whole


def bounded_percent(value: object) -> Optional[int]:
    """A whole 0–100 percentage, or nothing. Never clamped."""
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    whole = int(value)
    return whole if 0 <= whole <= 100 else None


def video_handle(host_id: str, video_id: str) -> str:
    """The opaque handle a client sees instead of a YouTube video id.

    Host-scoped and digested, for the same reason Spotify device ids are: the
    eleven-character id is a launch target, and a client holding one could
    construct a watch URL for something the search never returned. The handle is
    stable within a host, so the PWA can tell "the queue item I am looking at"
    from "a different one", and useless anywhere else.
    """
    return "ytv-" + fingerprint("cofferdam.youtube.video", host_id, video_id)


@dataclass(frozen=True)
class VideoMetadata:
    """The bounded description of one video, as shown on a phone.

    Assembled from the *search result* Cofferdam already normalized — never from
    the player, which is not asked for a title and has no route to supply one.
    That keeps one bounding path instead of two, and means a compromised player
    page cannot inject display text.
    """

    handle: str
    title: Optional[str] = None
    channel: Optional[str] = None
    published: Optional[str] = None

    @classmethod
    def build(
        cls,
        host_id: str,
        video_id: str,
        title: object = None,
        channel: object = None,
        published: object = None,
    ) -> "VideoMetadata":
        return cls(
            handle=video_handle(host_id, video_id),
            title=bounded_display_text(title, MAX_TITLE),
            channel=bounded_display_text(channel, MAX_CHANNEL),
            published=bounded_display_text(published, 10),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_handle": self.handle,
            "title": self.title,
            "channel": self.channel,
            "published": self.published,
        }


@dataclass(frozen=True)
class QueueItem:
    """One video waiting in the *Cofferdam* queue.

    ``video_id`` is the server's half and is **not** in :meth:`to_dict`. A queue
    item on the wire is a handle plus bounded display text; the thing that names
    something loadable never leaves this process.
    """

    queue_item_id: str
    video_id: str
    metadata: VideoMetadata

    def to_dict(self) -> Dict[str, Any]:
        payload = {"queue_item_id": self.queue_item_id}
        payload.update(self.metadata.to_dict())
        return payload


@dataclass(frozen=True)
class PlayerObservation:
    """What the player page last reported about itself.

    Every field is what the *player* said, normalized and bounded. Nothing here
    is ever populated from what Cofferdam asked for — that separation is the
    whole reason this is a distinct type from a command.
    """

    observed_at: str = field(default_factory=now_iso)
    playback_state: str = PLAYBACK_IDLE
    video_handle: Optional[str] = None
    current_time_seconds: Optional[int] = None
    duration_seconds: Optional[int] = None
    volume_percent: Optional[int] = None
    muted: Optional[bool] = None
    error: Optional[Dict[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "playback_state": self.playback_state,
            "video_handle": self.video_handle,
            "current_time_seconds": self.current_time_seconds,
            "duration_seconds": self.duration_seconds,
            "volume_percent": self.volume_percent,
            "muted": self.muted,
            "error": dict(self.error) if self.error else None,
        }


#: What this player can and cannot do, stated once so the PWA never has to infer
#: it. ``limitations`` are facts about the official API and the browser, not
#: apologies for unfinished work — see ``docs/YOUTUBE_PLAYER.md``.
CAPABILITIES: Dict[str, bool] = {
    "play_search_result": True,
    "queue_search_result": True,
    "pause": True,
    "resume": True,
    "next": True,
    "previous": True,
    "set_volume": True,
    "mute": True,
    "seek": False,
    "automatic_queue_continuation": False,
}

LIMITATIONS: Tuple[str, ...] = (
    "Volume and mute here are the YouTube player's own, not this computer's speaker.",
    "The browser may require one click on the workstation before sound can start.",
    "Next and Previous move through the Cofferdam queue, never YouTube's suggestions.",
    "A video whose owner disallows embedding cannot play here — use Open in YouTube.",
    "The queue is held in memory and is empty again after the service restarts.",
)


@dataclass(frozen=True)
class PlayerSnapshot:
    """One versioned, authenticated observation of the dedicated player.

    Assembled by the service from three separate things — the connection state
    it tracks, the last observation the player reported, and the queue Cofferdam
    owns — and never from a command that was sent.
    """

    connection_state: str
    observed_at: str = field(default_factory=now_iso)
    player_resource_id: Optional[str] = None
    current_result_handle: Optional[str] = None
    current_video: Optional[VideoMetadata] = None
    observation: Optional[PlayerObservation] = None
    queue: Sequence[QueueItem] = ()
    queue_index: Optional[int] = None
    last_error: Optional[Dict[str, str]] = None

    @property
    def connected(self) -> bool:
        return self.connection_state == CONNECTION_READY

    def to_dict(self) -> Dict[str, Any]:
        observation = self.observation or PlayerObservation()
        return {
            "version": YOUTUBE_PLAYER_VERSION,
            "observed_at": self.observed_at,
            "connection": {
                "state": self.connection_state,
                "connected": self.connected,
                # Said out loud, because it is the one thing a person watching a
                # browser window would otherwise assume: a running Opera is not
                # a connected player, and this panel never treats it as one.
                "identity_basis": "player_heartbeat",
                "player_resource_id": self.player_resource_id,
            },
            "current": {
                "result_handle": self.current_result_handle,
                "video": self.current_video.to_dict() if self.current_video else None,
                "playback_state": observation.playback_state,
                "current_time_seconds": observation.current_time_seconds,
                "duration_seconds": observation.duration_seconds,
            },
            "volume": {
                "volume_percent": observation.volume_percent,
                "muted": observation.muted,
                # Repeated in the payload rather than only in the docs: this is
                # the field a client is most likely to wire to the wrong slider.
                "scope": "youtube_player_only",
            },
            "queue": {
                "length": len(self.queue),
                "max_length": MAX_QUEUE_ITEMS,
                "index": self.queue_index,
                "items": [item.to_dict() for item in self.queue],
            },
            "capabilities": dict(CAPABILITIES),
            "limitations": list(LIMITATIONS),
            "last_error": dict(self.last_error) if self.last_error else None,
        }


__all__ = [
    "BLOCKED_STATES",
    "CAPABILITIES",
    "CONNECTION_DISCONNECTED",
    "CONNECTION_LAUNCHING",
    "CONNECTION_READY",
    "CONNECTION_STATES",
    "CONNECTION_UNAVAILABLE",
    "CONNECTION_WAITING",
    "LIMITATIONS",
    "MAX_CHANNEL",
    "MAX_QUEUE_ITEMS",
    "MAX_TITLE",
    "PLAYBACK_AUTOPLAY_BLOCKED",
    "PLAYBACK_BUFFERING",
    "PLAYBACK_CUEING",
    "PLAYBACK_ENDED",
    "PLAYBACK_ERROR",
    "PLAYBACK_IDLE",
    "PLAYBACK_PAUSED",
    "PLAYBACK_PLAYING",
    "PLAYBACK_STATES",
    "PlayerObservation",
    "PlayerSnapshot",
    "QueueItem",
    "VideoMetadata",
    "YOUTUBE_PLAYER_VERSION",
    "bounded_display_text",
    "bounded_percent",
    "bounded_seconds",
    "video_handle",
]
