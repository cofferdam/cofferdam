"""Spotify catalogue search through the official Web API.

Verified against the official documentation on 2026-08-05
(``developer.spotify.com/documentation/web-api``):

* **Search** — ``GET https://api.spotify.com/v1/search``, requiring ``q`` and
  ``type``; ``limit`` accepts 0–10 and defaults to 5, which is exactly the cap
  this milestone wants, so one call answers one search with no trimming.
* **Authorization** — the client-credentials flow against
  ``https://accounts.spotify.com/api/token``: HTTP Basic with the client id and
  secret, ``grant_type=client_credentials``, returning a bearer token that lasts
  about an hour. The documentation is explicit that this flow reaches only
  endpoints that do not touch user information — which is precisely the
  catalogue, and precisely *not* playback. **The authorization model itself
  makes playback control unreachable here**, which is a stronger guarantee than
  a promise not to call it.
* **Development mode** — a Spotify app starts in development mode, where
  *authenticated users* must be allowlisted. Client-credentials tokens carry no
  user, so that allowlist does not apply to catalogue search; the shared quota
  bucket does, and shows up as a 429.
* **Item identity** — every object carries ``uri`` in the form
  ``spotify:<type>:<id>``.

The URI is rebuilt, not forwarded
---------------------------------

The adapter does **not** store or pass through the ``uri`` string Spotify
returned. It validates the type against a closed tuple and the id against the
base-62 shape, keeps those two values, and reconstructs ``spotify:<type>:<id>``
at open time. A forwarded string would mean the thing eventually handed to a
native application came from a network response; a reconstructed one can only
ever be two validated tokens joined by a constant.
"""

from __future__ import annotations

import base64
import re
import time
from typing import Dict, List, Optional, Tuple

from .credentials import SpotifyCredentials
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
    RESULT_TYPE_ALBUM,
    RESULT_TYPE_ARTIST,
    RESULT_TYPE_EPISODE,
    RESULT_TYPE_PLAYLIST,
    RESULT_TYPE_SHOW,
    RESULT_TYPE_TRACK,
    MediaResult,
    MediaSearchOutcome,
    ProviderItem,
    bounded_creators,
    bounded_metadata,
    bounded_text,
)
from .transport import (
    SPOTIFY_ACCOUNTS_HOST,
    SPOTIFY_API_HOST,
    Response,
    TransportError,
    form_body,
    request,
)

PROVIDER_ID = "spotify"

TOKEN_PATH = "/api/token"
SEARCH_PATH = "/v1/search"

# Result types this milestone offers, mapped to the response envelope key the
# documentation specifies. Deliberately a subset: ``audiobook`` exists in the
# API but its availability is market-dependent, and offering a type that
# silently returns nothing in a user's market is worse than not offering it.
SEARCH_TYPES: Dict[str, str] = {
    RESULT_TYPE_TRACK: "tracks",
    RESULT_TYPE_ALBUM: "albums",
    RESULT_TYPE_ARTIST: "artists",
    RESULT_TYPE_PLAYLIST: "playlists",
    RESULT_TYPE_SHOW: "shows",
    RESULT_TYPE_EPISODE: "episodes",
}

DEFAULT_TYPES: Tuple[str, ...] = (RESULT_TYPE_TRACK,)

# Spotify ids are base-62 and 22 characters in every documented example. Checked
# because this value is half of a URI that will be handed to a native
# application: anything outside this shape is refused rather than launched.
_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{22}$")

# The token is cached in memory for the life of the process, refreshed a little
# before it expires. Never written to disk: it is derived from a credential, and
# persisting it would create a second secret with no second place to protect it.
_TOKEN_EARLY_REFRESH_SECONDS = 60


class _TokenCache:
    """One bearer token, in memory, with its expiry."""

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._expires_at: float = 0.0

    def get(self) -> Optional[str]:
        if self._token and time.monotonic() < self._expires_at:
            return self._token
        return None

    def set(self, token: str, expires_in: int) -> None:
        self._token = token
        self._expires_at = time.monotonic() + max(0, expires_in - _TOKEN_EARLY_REFRESH_SECONDS)

    def clear(self) -> None:
        self._token = None
        self._expires_at = 0.0


def valid_item_id(item_id: object) -> bool:
    return isinstance(item_id, str) and bool(_ID_PATTERN.match(item_id))


def build_uri(item_type: str, item_id: str) -> str:
    """``spotify:<type>:<id>`` from two validated tokens and a constant.

    Re-validates both on every call, including at open time. The check is cheap
    and this is the last gate before a string reaches the native-URI adapter.
    """
    if item_type not in SEARCH_TYPES:
        raise ValueError("unsupported spotify item type")
    if not valid_item_id(item_id):
        raise ValueError("malformed spotify item id")
    return "spotify:" + item_type + ":" + item_id


class SpotifySearchAdapter:
    """Catalogue search only. There is no method here that can play anything."""

    provider_id = PROVIDER_ID

    def __init__(self, credential_store) -> None:
        self._credentials = credential_store
        self._tokens = _TokenCache()

    # -- authorization -------------------------------------------------------

    def _fetch_token(self, credentials: SpotifyCredentials) -> str:
        """Client-credentials token. The secret appears only in this header."""
        pair = credentials.client_id + ":" + credentials.client_secret
        encoded = base64.b64encode(pair.encode("utf-8")).decode("ascii")
        body, headers = form_body({"grant_type": "client_credentials"})
        headers["Authorization"] = "Basic " + encoded

        try:
            response = request(
                SPOTIFY_ACCOUNTS_HOST, TOKEN_PATH, method="POST", headers=headers, body=body
            )
        except TransportError as exc:
            raise _transport_failure(exc) from None

        if response.status in (400, 401, 403):
            # Spotify answers a bad client id/secret with 400 or 401 here.
            self._tokens.clear()
            raise ProviderRejected(
                "Spotify refused the configured credentials",
                "the client id or client secret was not accepted; check the app in the Spotify "
                "developer dashboard",
            )
        if response.status == 429:
            raise ProviderRateLimited(
                "Spotify is rate limiting this app",
                "too many requests; try again shortly",
                retry_after_seconds=response.retry_after_seconds,
            )
        if response.status != 200:
            raise ProviderTemporarilyUnavailable(
                "Spotify could not issue an access token",
                "the token endpoint answered with status " + str(response.status),
            )

        payload = _decoded(response)
        token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if not isinstance(token, str) or not token:
            raise ProviderMalformedResponse(
                "Spotify returned a token response this build could not read"
            )
        self._tokens.set(token, int(expires_in) if isinstance(expires_in, int) else 3600)
        return token

    def _token(self, *, force_refresh: bool = False) -> str:
        credentials = self._credentials.load(PROVIDER_ID)
        if credentials is None:
            raise ProviderUnconfigured(
                "Spotify structured search is not configured on this host",
                "add a client id and secret to the media provider credential file",
            )
        if not force_refresh:
            cached = self._tokens.get()
            if cached:
                return cached
        return self._fetch_token(credentials)

    # -- search --------------------------------------------------------------

    def search(self, query: str, types: Tuple[str, ...] = DEFAULT_TYPES) -> MediaSearchOutcome:
        """Official catalogue search, normalized and capped.

        ``types`` is already validated against the allowlist by the caller; it
        is re-checked here so this adapter is safe to call directly from a test
        or a future code path.
        """
        selected = tuple(t for t in types if t in SEARCH_TYPES) or DEFAULT_TYPES

        response = self._search_call(query, selected)

        # One retry, for one specific cause: a cached token that expired between
        # the cache check and the call. Not a general retry loop.
        if response.status == 401:
            self._tokens.clear()
            response = self._search_call(query, selected, force_refresh=True)

        if response.status in (401, 403):
            raise ProviderRejected(
                "Spotify refused this request",
                "the configured credentials were not accepted for catalogue search",
            )
        if response.status == 429:
            raise ProviderRateLimited(
                "Spotify is rate limiting this app",
                "the app has exceeded its request quota; try again shortly",
                retry_after_seconds=response.retry_after_seconds,
            )
        if response.status != 200:
            raise ProviderTemporarilyUnavailable(
                "Spotify search is unavailable right now",
                "the search endpoint answered with status " + str(response.status),
            )

        return _normalize(query, _decoded(response), selected)

    def _search_call(
        self, query: str, types: Tuple[str, ...], *, force_refresh: bool = False
    ) -> Response:
        token = self._token(force_refresh=force_refresh)
        try:
            return request(
                SPOTIFY_API_HOST,
                SEARCH_PATH,
                query={
                    # The user's phrase, percent-encoded by the transport. It is
                    # a *value*; the parameter names are this module's.
                    "q": query,
                    "type": ",".join(types),
                    "limit": str(MAX_RESULTS),
                },
                headers={"Authorization": "Bearer " + token},
            )
        except TransportError as exc:
            raise _transport_failure(exc) from None


def _transport_failure(exc: TransportError):
    if exc.timeout:
        return ProviderTemporarilyUnavailable(
            "Spotify did not respond in time", "the request timed out"
        )
    return ProviderTemporarilyUnavailable("Spotify could not be reached", exc.reason)


def _decoded(response: Response) -> dict:
    try:
        payload = response.json()
    except TransportError as exc:
        raise ProviderMalformedResponse("Spotify returned an unreadable response", exc.reason) from None
    if not isinstance(payload, dict):
        raise ProviderMalformedResponse("Spotify returned an unexpected response shape")
    return payload


def _normalize(query: str, payload: dict, types: Tuple[str, ...]) -> MediaSearchOutcome:
    """Build results field by field. Nothing is copied wholesale."""
    results: List[MediaResult] = []
    items: List[ProviderItem] = []

    # Interleaved by type in the order requested, preserving Spotify's own
    # ranking within each type. The overall cap is applied last, so asking for
    # two types cannot return ten cards.
    for result_type in types:
        envelope_key = SEARCH_TYPES[result_type]
        section = payload.get(envelope_key)
        if not isinstance(section, dict):
            continue
        entries = section.get("items")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if len(results) >= MAX_RESULTS:
                break
            built = _normalize_one(result_type, entry, len(results))
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


def _normalize_one(result_type: str, entry: object, index: int):
    """One provider object to one result, or ``None`` if it cannot be trusted.

    A Spotify item that lacks a well-formed id is dropped rather than shown: an
    unopenable card is a card that can only disappoint, and the id is what makes
    the card openable at all.
    """
    if not isinstance(entry, dict):
        return None
    item_id = entry.get("id")
    if not valid_item_id(item_id):
        return None
    title = bounded_text(entry.get("name"), MAX_TITLE_LENGTH)
    if not title:
        return None

    creators: Tuple[str, ...] = ()
    subtitle: Optional[str] = None
    duration_seconds: Optional[int] = None
    explicit: Optional[bool] = None
    metadata_pairs = []

    if result_type in (RESULT_TYPE_TRACK, RESULT_TYPE_EPISODE):
        duration_ms = entry.get("duration_ms")
        if isinstance(duration_ms, int) and 0 < duration_ms < 24 * 3600 * 1000:
            duration_seconds = duration_ms // 1000
        if isinstance(entry.get("explicit"), bool):
            explicit = entry["explicit"]

    if result_type in (RESULT_TYPE_TRACK, RESULT_TYPE_ALBUM):
        artists = entry.get("artists")
        if isinstance(artists, list):
            creators = bounded_creators(
                artist.get("name") for artist in artists if isinstance(artist, dict)
            )

    if result_type == RESULT_TYPE_TRACK:
        album = entry.get("album")
        if isinstance(album, dict):
            subtitle = bounded_text(album.get("name"), MAX_SUBTITLE_LENGTH)
            release = bounded_text(album.get("release_date"), 10)
            if release:
                metadata_pairs.append(("album_release", release))
    elif result_type == RESULT_TYPE_ALBUM:
        release = bounded_text(entry.get("release_date"), 10)
        if release:
            metadata_pairs.append(("release", release))
        total = entry.get("total_tracks")
        if isinstance(total, int) and 0 < total < 10000:
            metadata_pairs.append(("tracks", str(total)))
    elif result_type == RESULT_TYPE_ARTIST:
        genres = entry.get("genres")
        if isinstance(genres, list):
            first = bounded_text(next((g for g in genres if isinstance(g, str)), None), 60)
            if first:
                metadata_pairs.append(("genre", first))
    elif result_type == RESULT_TYPE_PLAYLIST:
        owner = entry.get("owner")
        if isinstance(owner, dict):
            subtitle = bounded_text(owner.get("display_name"), MAX_SUBTITLE_LENGTH)
    elif result_type in (RESULT_TYPE_SHOW, RESULT_TYPE_EPISODE):
        publisher = entry.get("publisher")
        if isinstance(publisher, str):
            subtitle = bounded_text(publisher, MAX_SUBTITLE_LENGTH)

    result = MediaResult(
        provider_id=PROVIDER_ID,
        # Positional and session-scoped: meaningless outside its own search, and
        # carrying none of the provider's identity.
        result_id="r" + str(index),
        result_type=result_type,
        title=title,
        subtitle=subtitle,
        creators=creators,
        duration_seconds=duration_seconds,
        explicit=explicit,
        provider_metadata=bounded_metadata(metadata_pairs),
    )
    return result, ProviderItem(provider_id=PROVIDER_ID, item_type=result_type, item_id=item_id)


__all__ = [
    "DEFAULT_TYPES",
    "PROVIDER_ID",
    "SEARCH_TYPES",
    "SpotifySearchAdapter",
    "build_uri",
    "valid_item_id",
]
