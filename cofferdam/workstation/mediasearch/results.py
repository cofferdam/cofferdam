"""The provider-neutral result model, and the bounds that keep it neutral.

Why normalize at all
--------------------

A Spotify track object and a YouTube search result share almost nothing, and
both carry far more than a person choosing between five cards needs. Passing
either through untouched would mean the phone renders whatever the provider
sent — including fields nobody reviewed, URLs nobody intended to be navigable,
and text of unbounded length.

So every provider response is reduced, here, to the same small shape. **A field
that is not in :class:`MediaResult` does not reach the client.** That is a
whitelist, not a filter: the normalizers below construct results field by field
and never copy a provider dictionary wholesale.

What is deliberately absent
---------------------------

No access tokens, no authorization data, no cookies, no user-account data, no
internal provider URLs, no HTML, no tracking parameters, and **no playable URL
or URI of any kind**. The launch target is not part of the result the client
sees: the server keeps the provider's item identity privately in the search
session and rebuilds the target when the user picks something. A result the
client could read a URI out of would let a client skip the server's resolution
step entirely, which is the property this milestone exists to protect.

Bounds
------

Every string is truncated at a field-appropriate cap, every list is capped, and
the result count is capped. Truncation is preferred to rejection for *display*
text, because a film with a very long title should still be pickable; it is
marked with an ellipsis so nothing silently pretends to be complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

# Bumped when the wire shape changes in a way a client could notice. The PWA
# reads it so an older cached shell can say "reload me" rather than mis-render.
MEDIA_RESULT_MODEL_VERSION = 1

# The closed vocabulary of result types across all providers.
RESULT_TYPE_TRACK = "track"
RESULT_TYPE_ALBUM = "album"
RESULT_TYPE_ARTIST = "artist"
RESULT_TYPE_PLAYLIST = "playlist"
RESULT_TYPE_SHOW = "show"
RESULT_TYPE_EPISODE = "episode"
RESULT_TYPE_VIDEO = "video"

RESULT_TYPES: Tuple[str, ...] = (
    RESULT_TYPE_TRACK,
    RESULT_TYPE_ALBUM,
    RESULT_TYPE_ARTIST,
    RESULT_TYPE_PLAYLIST,
    RESULT_TYPE_SHOW,
    RESULT_TYPE_EPISODE,
    RESULT_TYPE_VIDEO,
)

# Five is the whole point of the design: enough to disambiguate "Gönül Dağı" by
# four different artists, few enough to read on a phone without scrolling, and
# small enough that one search is one cheap provider call.
MAX_RESULTS = 5

MAX_TITLE_LENGTH = 200
MAX_SUBTITLE_LENGTH = 200
MAX_CREATOR_LENGTH = 120
MAX_CREATORS = 4
MAX_METADATA_VALUE_LENGTH = 80
MAX_METADATA_ENTRIES = 6


def bounded_text(value: Any, limit: int) -> Optional[str]:
    """Coerce provider text to a bounded, control-free, single-line string.

    Control characters are stripped rather than rejected here — unlike a user's
    own query, which is refused. The asymmetry is deliberate: the user's typing
    is a statement to be preserved exactly or refused, while a provider's title
    is data being displayed, and dropping one stray character from it beats
    discarding an otherwise good result.

    HTML is *not* interpreted or unescaped. YouTube returns titles containing
    entities like ``&amp;``; they are passed through as text and the PWA escapes
    on render, so nothing here can produce markup.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    cleaned = "".join(
        character
        for character in value
        if not (ord(character) < 0x20 or ord(character) == 0x7F or 0x80 <= ord(character) <= 0x9F)
    ).strip()
    if not cleaned:
        return None
    if len(cleaned) > limit:
        # Marked, so a truncated title never passes for a complete one.
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned


@dataclass(frozen=True)
class MediaResult:
    """One thing a person can pick, in provider-neutral form.

    ``result_id`` is a Cofferdam-scoped opaque handle, not a provider id. The
    client sends it back to open the result; the server maps it to the provider
    item it privately remembers. Deliberately not the Spotify id or the YouTube
    video id — those *are* launch targets, and a client that held one could
    construct an open request for something the search never returned.
    """

    provider_id: str
    result_id: str
    result_type: str
    title: str
    subtitle: Optional[str] = None
    creators: Tuple[str, ...] = ()
    duration_seconds: Optional[int] = None
    published: Optional[str] = None
    explicit: Optional[bool] = None
    live_state: Optional[str] = None
    provider_metadata: Dict[str, str] = field(default_factory=dict)
    selectable: bool = True
    open_action_supported: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """The client-facing shape. Nothing openable, nothing unbounded."""
        return {
            "provider_id": self.provider_id,
            "result_id": self.result_id,
            "result_type": self.result_type,
            "title": self.title,
            "subtitle": self.subtitle,
            "creators": list(self.creators),
            "duration_seconds": self.duration_seconds,
            "published": self.published,
            "explicit": self.explicit,
            "live_state": self.live_state,
            "provider_metadata": dict(self.provider_metadata),
            "selectable": self.selectable,
            "open_action_supported": self.open_action_supported,
        }


@dataclass(frozen=True)
class ProviderItem:
    """What the *server* remembers, so it can rebuild a launch target.

    Kept beside each result inside the search session and **never serialized to
    a client**. This is the half of a result that names something openable, and
    keeping it server-side is what makes "the client cannot submit a URL"
    structural rather than merely validated.

    ``item_type`` and ``item_id`` are the provider's own validated identifiers;
    the adapter rebuilds a URI or watch URL from them at open time rather than
    storing a ready-made string, so the construction rules are applied once, in
    one place, and are re-checked on every open.
    """

    provider_id: str
    item_type: str
    item_id: str


@dataclass(frozen=True)
class MediaSearchOutcome:
    """A completed structured search: what to show, and what to remember."""

    provider_id: str
    query: str
    results: Tuple[MediaResult, ...]
    items: Tuple[ProviderItem, ...]

    def __post_init__(self) -> None:
        if len(self.results) != len(self.items):  # pragma: no cover - construction invariant
            raise ValueError("each result must have exactly one provider item")


def bounded_metadata(pairs) -> Dict[str, str]:
    """A small, flat, string-only metadata map.

    Exists so an adapter can carry a genuinely useful extra ("album", "channel")
    without that becoming a hole through which arbitrary provider structure
    reaches the client. Nested values are dropped rather than flattened.
    """
    metadata: Dict[str, str] = {}
    for key, value in pairs:
        if len(metadata) >= MAX_METADATA_ENTRIES:
            break
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        text = bounded_text(value, MAX_METADATA_VALUE_LENGTH)
        if text:
            metadata[key] = text
    return metadata


def bounded_creators(names) -> Tuple[str, ...]:
    """Up to :data:`MAX_CREATORS` bounded creator names."""
    creators = []
    for name in names:
        if len(creators) >= MAX_CREATORS:
            break
        text = bounded_text(name, MAX_CREATOR_LENGTH)
        if text:
            creators.append(text)
    return tuple(creators)


__all__ = [
    "MAX_CREATORS",
    "MAX_CREATOR_LENGTH",
    "MAX_METADATA_ENTRIES",
    "MAX_METADATA_VALUE_LENGTH",
    "MAX_RESULTS",
    "MAX_SUBTITLE_LENGTH",
    "MAX_TITLE_LENGTH",
    "MEDIA_RESULT_MODEL_VERSION",
    "RESULT_TYPES",
    "RESULT_TYPE_ALBUM",
    "RESULT_TYPE_ARTIST",
    "RESULT_TYPE_EPISODE",
    "RESULT_TYPE_PLAYLIST",
    "RESULT_TYPE_SHOW",
    "RESULT_TYPE_TRACK",
    "RESULT_TYPE_VIDEO",
    "MediaResult",
    "MediaSearchOutcome",
    "ProviderItem",
    "bounded_creators",
    "bounded_metadata",
    "bounded_text",
]
