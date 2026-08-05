"""YouTube video search through the official Data API v3.

Verified against the official documentation on 2026-08-05
(``developers.google.com/youtube/v3``):

* **Search** — ``GET https://www.googleapis.com/youtube/v3/search`` with
  ``part=snippet``; ``type=video`` restricts results to videos; ``maxResults``
  accepts 0–50 and defaults to 5.
* **Authorization** — an API key is sufficient. OAuth is required only for
  ``forContentOwner``/``forDeveloper``/``forMine``, none of which this milestone
  uses, so **no user account is involved and no user data is reachable**.
* **Quota** — the documented default allocation is *100 ``search.list`` calls
  per day*, alongside 10,000 units/day for other endpoints. That is a real
  ceiling a person will meet, so it gets its own error state and its own
  sentence on the phone.
* **Response shape** — ``items[].id.kind`` (``youtube#video`` for a video),
  ``items[].id.videoId``, and ``items[].snippet`` carrying ``title``,
  ``channelTitle``, ``publishedAt`` and ``liveBroadcastContent``.

Only videos
-----------

``type=video`` is sent, **and** every item is re-checked for
``id.kind == "youtube#video"`` with a well-formed ``videoId`` before it becomes
a card. A channel or playlist result has no watch URL, so a card for one could
only produce a broken open — and the parameter alone is a request, while the
kind check is a verification.

Duration is absent on purpose
-----------------------------

``search.list`` does not return duration; it needs a follow-up ``videos.list``
call. That call is cheap, but it is a second network round trip on the phone's
critical path for a field nobody chooses a video by. It is left out, and the
result model simply reports no duration for YouTube rather than inventing one.

Titles are text, never markup
-----------------------------

``snippet.title`` arrives HTML-escaped (``&amp;``, ``&#39;``). It is *not*
unescaped here: it is bounded as text and the PWA escapes again on render, so
there is no step at which a provider-supplied title could become markup.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .credentials import YouTubeCredentials
from .errors import (
    ProviderMalformedResponse,
    ProviderRateLimited,
    ProviderRejected,
    ProviderTemporarilyUnavailable,
    ProviderUnconfigured,
)
from .results import (
    MAX_RESULTS,
    MAX_SUBTITLE_LENGTH,
    MAX_TITLE_LENGTH,
    RESULT_TYPE_VIDEO,
    MediaResult,
    MediaSearchOutcome,
    ProviderItem,
    bounded_metadata,
    bounded_text,
)
from .transport import YOUTUBE_API_HOST, Response, TransportError, request

PROVIDER_ID = "youtube"

SEARCH_PATH = "/youtube/v3/search"

KIND_VIDEO = "youtube#video"

DEFAULT_TYPES: Tuple[str, ...] = (RESULT_TYPE_VIDEO,)

# Google does not publish a formal grammar for video ids, but every id in the
# documentation and in practice is 11 characters of the URL-safe base-64
# alphabet. Enforced as a *defensive* bound rather than a documented guarantee:
# this value becomes part of a URL handed to a browser, so an id that does not
# match the observed shape is refused instead of navigated to.
_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")

WATCH_URL_PREFIX = "https://www.youtube.com/watch?v="


def valid_video_id(video_id: object) -> bool:
    return isinstance(video_id, str) and bool(_VIDEO_ID_PATTERN.match(video_id))


def build_watch_url(video_id: str) -> str:
    """The official watch URL, from a constant prefix and a validated id.

    No tracking parameters, no playlist context, no ``feature=`` — just the
    canonical address of one video. Re-validates at every call, including at
    open time.
    """
    if not valid_video_id(video_id):
        raise ValueError("malformed youtube video id")
    return WATCH_URL_PREFIX + video_id


class YouTubeSearchAdapter:
    """Video search only. No user data, no playback, no channel management."""

    provider_id = PROVIDER_ID

    def __init__(self, credential_store) -> None:
        self._credentials = credential_store

    def search(self, query: str, types: Tuple[str, ...] = DEFAULT_TYPES) -> MediaSearchOutcome:
        """Official video search, normalized and capped.

        ``types`` exists for signature symmetry with Spotify. YouTube's video
        flow is video-only by design, so anything else is ignored rather than
        quietly widening the search to channels and playlists.
        """
        credentials = self._credentials.load(PROVIDER_ID)
        if credentials is None:
            raise ProviderUnconfigured(
                "YouTube structured search is not configured on this host",
                "add an API key to the media provider credential file",
            )
        response = self._search_call(query, credentials)

        if response.status in (401,):
            raise ProviderRejected(
                "YouTube refused the configured API key",
                "the key was not accepted; check it in the Google Cloud console",
            )
        if response.status == 403:
            # 403 is overloaded here: it covers a disabled API, a restricted
            # key, *and* an exhausted quota. The reason string decides, because
            # "you are out of quota until midnight Pacific" and "your key is
            # wrong" send a person to completely different places.
            raise self._classify_403(response)
        if response.status == 429:
            raise ProviderRateLimited(
                "YouTube is rate limiting this key",
                "too many requests; try again shortly",
                retry_after_seconds=response.retry_after_seconds,
            )
        if response.status != 200:
            raise ProviderTemporarilyUnavailable(
                "YouTube search is unavailable right now",
                "the search endpoint answered with status " + str(response.status),
            )

        return _normalize(query, _decoded(response))

    def _search_call(self, query: str, credentials: YouTubeCredentials) -> Response:
        try:
            return request(
                YOUTUBE_API_HOST,
                SEARCH_PATH,
                query={
                    "part": "snippet",
                    # The user's phrase as a value; every parameter name here is
                    # this module's own constant.
                    "q": query,
                    "type": "video",
                    "maxResults": str(MAX_RESULTS),
                    "key": credentials.api_key,
                },
            )
        except TransportError as exc:
            if exc.timeout:
                raise ProviderTemporarilyUnavailable(
                    "YouTube did not respond in time", "the request timed out"
                ) from None
            raise ProviderTemporarilyUnavailable(
                "YouTube could not be reached", exc.reason
            ) from None

    @staticmethod
    def _classify_403(response: Response):
        """Split quota exhaustion from key rejection, using only reason codes.

        Reads Google's machine-readable ``errors[].reason`` and matches it
        against a closed set. The provider's human ``message`` is never
        forwarded — it is text from outside that would end up on a phone screen.
        """
        quota_reasons = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded",
                         "userRateLimitExceeded"}
        reason = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    entries = error.get("errors")
                    if isinstance(entries, list) and entries:
                        first = entries[0]
                        if isinstance(first, dict) and isinstance(first.get("reason"), str):
                            reason = first["reason"]
        except TransportError:
            reason = ""

        if reason in quota_reasons:
            return ProviderRateLimited(
                "YouTube's daily search quota for this key is used up",
                "the default allocation is about 100 searches a day and resets at midnight "
                "Pacific time",
                retry_after_seconds=response.retry_after_seconds,
            )
        return ProviderRejected(
            "YouTube refused the configured API key",
            "the key may be restricted, or the YouTube Data API may not be enabled for it",
        )


def _decoded(response: Response) -> dict:
    try:
        payload = response.json()
    except TransportError as exc:
        raise ProviderMalformedResponse("YouTube returned an unreadable response", exc.reason) from None
    if not isinstance(payload, dict):
        raise ProviderMalformedResponse("YouTube returned an unexpected response shape")
    return payload


def _normalize(query: str, payload: dict) -> MediaSearchOutcome:
    results: List[MediaResult] = []
    items: List[ProviderItem] = []

    entries = payload.get("items")
    if not isinstance(entries, list):
        entries = []

    for entry in entries:
        if len(results) >= MAX_RESULTS:
            break
        built = _normalize_one(entry, len(results))
        if built is None:
            continue
        result, item = built
        results.append(result)
        items.append(item)

    return MediaSearchOutcome(
        provider_id=PROVIDER_ID,
        query=query,
        results=tuple(results[:MAX_RESULTS]),
        items=tuple(items[:MAX_RESULTS]),
    )


def _normalize_one(entry: object, index: int):
    """One search item to one video card, or ``None``.

    Three independent reasons to drop an item, each of which would otherwise
    produce a card that cannot be opened: it is not a video, its id is not
    well-formed, or it has no usable title.
    """
    if not isinstance(entry, dict):
        return None

    identity = entry.get("id")
    if not isinstance(identity, dict):
        return None
    # Verified, not assumed — ``type=video`` is what we asked for, this is what
    # we got.
    if identity.get("kind") != KIND_VIDEO:
        return None
    video_id = identity.get("videoId")
    if not valid_video_id(video_id):
        return None

    snippet = entry.get("snippet")
    if not isinstance(snippet, dict):
        return None
    title = bounded_text(snippet.get("title"), MAX_TITLE_LENGTH)
    if not title:
        return None

    channel = bounded_text(snippet.get("channelTitle"), MAX_SUBTITLE_LENGTH)

    published: Optional[str] = None
    raw_published = snippet.get("publishedAt")
    if isinstance(raw_published, str) and len(raw_published) >= 10:
        # Date only. The time of day is noise on a result card, and a shorter
        # field is a smaller thing to have to bound.
        candidate = raw_published[:10]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", candidate):
            published = candidate

    live_state: Optional[str] = None
    raw_live = snippet.get("liveBroadcastContent")
    # Closed vocabulary: "none" carries no information worth a badge.
    if raw_live in ("live", "upcoming"):
        live_state = raw_live

    result = MediaResult(
        provider_id=PROVIDER_ID,
        result_id="r" + str(index),
        result_type=RESULT_TYPE_VIDEO,
        title=title,
        subtitle=channel,
        creators=(channel,) if channel else (),
        # search.list carries no duration; see the module docstring.
        duration_seconds=None,
        published=published,
        live_state=live_state,
        provider_metadata=bounded_metadata([]),
    )
    return result, ProviderItem(
        provider_id=PROVIDER_ID, item_type=RESULT_TYPE_VIDEO, item_id=video_id
    )


__all__ = [
    "DEFAULT_TYPES",
    "KIND_VIDEO",
    "PROVIDER_ID",
    "WATCH_URL_PREFIX",
    "YouTubeSearchAdapter",
    "build_watch_url",
    "valid_video_id",
]
