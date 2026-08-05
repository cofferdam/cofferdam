"""The one component that turns "the third card" into something openable.

This module owns the sequence that matters:

1. a validated provider id and a validated phrase arrive;
2. the official adapter for that provider returns normalized results;
3. the results plus their private provider items become a bounded session;
4. the client is handed opaque handles;
5. on open, the session is re-resolved **server-side** and the launch target is
   rebuilt from validated identifiers.

Step 5 never trusts step 4. The client's open request names a search and a
result, and this module looks both up in its own memory — so a request cannot
carry a destination, and a result cannot be opened through a provider that did
not produce it.

Nothing here launches anything. It builds a :class:`~..media.MediaTarget` and
hands it back to the action executor, which owns the adapter boundary. Keeping
the launch out of this module means the network-facing code and the
process-launching code stay separated by the same typed seam the rest of the
product uses.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from ..media import (
    TARGET_APPLICATION_URI,
    TARGET_URL,
    MediaTarget,
    get_provider,
    validate_query,
)
from .credentials import CredentialStore
from .errors import ProviderUnconfigured
from .results import RESULT_TYPES, MediaResult
from .sessions import SearchSession, SearchSessionStore
from .spotify import PROVIDER_ID as SPOTIFY_ID
from .spotify import SpotifySearchAdapter, build_uri
from .youtube import PROVIDER_ID as YOUTUBE_ID
from .youtube import YouTubeSearchAdapter, build_watch_url

# Adapter key -> class. Code-owned and closed, exactly like the application
# allowlist: a provider whose ``structured_search_key`` is not in here has no
# official search, and no registry or request can add one.
_ADAPTERS = {
    SPOTIFY_ID: SpotifySearchAdapter,
    YOUTUBE_ID: YouTubeSearchAdapter,
}

# The result types a *client* may request, per provider. Narrower than what each
# API supports: a type is listed only where this build normalizes it and can
# open it.
ALLOWED_REQUEST_TYPES: Dict[str, Tuple[str, ...]] = {
    SPOTIFY_ID: ("track", "album", "artist", "playlist", "show", "episode"),
    YOUTUBE_ID: ("video",),
}

MAX_REQUESTED_TYPES = 3


class MediaSearchService:
    """Official search, bounded sessions, and server-side target construction."""

    def __init__(self, config, sessions: Optional[SearchSessionStore] = None) -> None:
        self._credentials = CredentialStore(config)
        self.sessions = sessions or SearchSessionStore()
        self._adapters: Dict[str, object] = {}

    # -- configuration state -------------------------------------------------

    @property
    def credentials(self) -> CredentialStore:
        return self._credentials

    def configured_providers(self) -> Tuple[str, ...]:
        """Ids whose official search is usable right now. Never a secret."""
        return tuple(
            provider_id
            for provider_id in _ADAPTERS
            if self._credentials.configured(provider_id)
        )

    def diagnostics(self) -> Dict[str, object]:
        """Status words only — see :mod:`.credentials` for why that is all.

        The permissions and environment notes are the two things worth warning
        about that can be said without revealing anything: that the credential
        file is world-readable, and that someone put a key in the environment
        where Cofferdam will not read it.
        """
        payload: Dict[str, object] = {
            "providers": self._credentials.describe(tuple(_ADAPTERS)),
        }
        notes = []
        permissions = self._credentials.permissions_note()
        if permissions:
            notes.append(permissions)
        from .credentials import redact_environment_note

        environment = redact_environment_note()
        if environment:
            notes.append(environment)
        payload["notes"] = notes
        return payload

    # -- searching -----------------------------------------------------------

    def _adapter(self, provider_id: str):
        provider = get_provider(provider_id)
        key = provider.structured_search_key
        if key is None or key not in _ADAPTERS:
            # Netflix, Prime Video and TV+ land here, and must: this is the
            # single place that decides a provider has official search at all.
            raise ProviderUnconfigured(
                f"{provider.name} does not have official structured search in this build",
                "only Spotify and YouTube expose an official catalogue search API here",
            )
        adapter = self._adapters.get(key)
        if adapter is None:
            adapter = _ADAPTERS[key](self._credentials)
            self._adapters[key] = adapter
        return adapter

    def allowed_types(self, provider_id: str) -> Tuple[str, ...]:
        return ALLOWED_REQUEST_TYPES.get(provider_id, ())

    def validate_types(self, provider_id: str, requested) -> Tuple[str, ...]:
        """Reduce a client's type list to the allowlist, or refuse.

        An unknown type is **rejected**, not dropped. Silently ignoring it would
        run a different search from the one that was asked for and present the
        results as though they answered the request.
        """
        allowed = self.allowed_types(provider_id)
        if requested is None:
            return ()
        if not isinstance(requested, (list, tuple)):
            raise ValueError("types must be a list")
        if len(requested) > MAX_REQUESTED_TYPES:
            raise ValueError(f"at most {MAX_REQUESTED_TYPES} result types may be requested")
        selected = []
        for entry in requested:
            if not isinstance(entry, str) or entry not in RESULT_TYPES:
                raise ValueError("unknown result type")
            if entry not in allowed:
                raise ValueError(
                    "that result type is not available for this provider; "
                    "available: " + ", ".join(allowed)
                )
            if entry not in selected:
                selected.append(entry)
        return tuple(selected)

    def search(self, provider_id: str, query: str, types=()) -> SearchSession:
        """Run an official search and remember it. Raises for every failure."""
        adapter = self._adapter(provider_id)
        # Re-validated here even though the action schema already did it: this
        # method is reachable from tests and future code paths, and the query
        # rules are the boundary, not a formality.
        phrase = validate_query(query)
        outcome = adapter.search(phrase, types or ())
        return self.sessions.create(outcome)

    # -- turning a chosen result into a launch target ------------------------

    def target_for(
        self, search_id: str, result_id: Optional[str], *, provider_id: str, first: bool = False
    ) -> Tuple[SearchSession, MediaResult, MediaTarget]:
        """Resolve a result from the server's own session and build its target.

        The only route from a client request to something openable. Both the
        session lookup and the target construction happen here, from validated
        identifiers the server stored itself — the request contributed a session
        handle and a result handle, and nothing else.
        """
        if first:
            session, result, item = self.sessions.first(search_id, provider_id=provider_id)
        else:
            session, result, item = self.sessions.resolve(
                search_id, result_id, provider_id=provider_id
            )

        if item.provider_id == SPOTIFY_ID:
            # Rebuilt from a validated type and id, never a stored URI string.
            target = MediaTarget(
                kind=TARGET_APPLICATION_URI,
                value=build_uri(item.item_type, item.item_id),
                application_key="spotify",
            )
        elif item.provider_id == YOUTUBE_ID:
            target = MediaTarget(kind=TARGET_URL, value=build_watch_url(item.item_id))
        else:  # pragma: no cover - unreachable while _ADAPTERS holds two keys
            raise ProviderUnconfigured(
                "that provider cannot open a selected result in this build"
            )
        return session, result, target


__all__ = [
    "ALLOWED_REQUEST_TYPES",
    "MAX_REQUESTED_TYPES",
    "MediaSearchService",
]
