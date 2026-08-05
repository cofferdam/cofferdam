"""Short-lived, bounded search sessions — the server's memory of a search.

The problem this solves
-----------------------

A user searches, sees five cards, taps one. Something has to turn "the third
card" into "``spotify:track:xyz``". The tempting shortcut is to send the URI to
the phone with the card and let the phone send it back on tap — and that
shortcut would make Cofferdam accept a caller-supplied URI, which is exactly the
capability the whole typed-action boundary exists to withhold.

So the server remembers instead. A search produces a session holding the results
*and* the private :class:`~.results.ProviderItem` for each one. The client gets
opaque handles. On open, the server looks the item up in its own session and
rebuilds the launch target. **At no point does a destination travel through the
client.**

Why sessions expire, and why they die with the process
------------------------------------------------------

They are in memory only, and are gone on restart. That is a feature, not a
limitation to apologise for:

* A search query and its results reveal what someone was looking for. Holding
  that on disk means a record of a person's interests outliving the moment they
  had it, for no functional gain.
* A restarted daemon that still honoured old ``search_id`` values would be
  claiming knowledge it no longer has. Expiry after restart is simply true, and
  the client's response — search again — is the same one it already handles for
  a timed-out session.

Bounds
------

TTL, a cap on concurrent sessions with oldest-first eviction, and a cap on
results per session (already enforced upstream). Together these give a hard
ceiling on memory: sessions x results x bounded field sizes. Eviction is
oldest-first so a burst of searches cannot push out the one the user is
currently looking at before their own newer ones.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .errors import ResultNotFound, SearchSessionExpired, SearchSessionNotFound
from .results import MediaResult, MediaSearchOutcome, ProviderItem

# Long enough to read five cards, discuss them, and tap one; short enough that a
# forgotten phone in a pocket is not holding a record of a search all day.
SEARCH_SESSION_TTL_SECONDS = 600

# A single user driving one phone and one tablet. Well above real concurrent
# use, and low enough that the worst case stays trivially small.
MAX_SEARCH_SESSIONS = 32

_ID_BYTES = 16


def _utc_iso(epoch_seconds: float) -> str:
    return (
        datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class SearchSession:
    """One search, its results, and the private items behind them."""

    search_id: str
    provider_id: str
    query: str
    results: Tuple[MediaResult, ...]
    items: Tuple[ProviderItem, ...]
    created_at: float
    expires_at: float

    def expired(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at

    def to_dict(self) -> dict:
        """The client-facing view. ``items`` is **not** in it.

        This omission is the whole design. Serializing the provider items would
        hand the client the launch targets and make every guarantee in this
        module advisory.
        """
        return {
            "search_id": self.search_id,
            "provider_id": self.provider_id,
            "query": self.query,
            "observed_at": _utc_iso(self.created_at),
            "expires_at": _utc_iso(self.expires_at),
            "result_count": len(self.results),
            "results": [result.to_dict() for result in self.results],
        }


class SearchSessionStore:
    """In-memory, bounded, thread-safe. One per running service."""

    def __init__(
        self,
        ttl_seconds: int = SEARCH_SESSION_TTL_SECONDS,
        max_sessions: int = MAX_SEARCH_SESSIONS,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._sessions: Dict[str, SearchSession] = {}
        # Actions run in a worker thread pool; two searches can land at once.
        self._lock = threading.Lock()

    # -- writing -------------------------------------------------------------

    def create(self, outcome: MediaSearchOutcome, *, now: Optional[float] = None) -> SearchSession:
        moment = now if now is not None else time.time()
        session = SearchSession(
            # Unguessable rather than sequential: a session id is the handle to
            # someone's search results, and a counter would let one be guessed.
            search_id=secrets.token_urlsafe(_ID_BYTES),
            provider_id=outcome.provider_id,
            query=outcome.query,
            results=outcome.results,
            items=outcome.items,
            created_at=moment,
            expires_at=moment + self._ttl,
        )
        with self._lock:
            self._evict(moment)
            self._sessions[session.search_id] = session
        return session

    def _evict(self, now: float) -> None:
        """Drop expired sessions, then the oldest until under the cap."""
        for search_id in [sid for sid, s in self._sessions.items() if s.expired(now)]:
            self._sessions.pop(search_id, None)
        while len(self._sessions) >= self._max:
            oldest = min(self._sessions.values(), key=lambda s: s.created_at)
            self._sessions.pop(oldest.search_id, None)

    # -- reading -------------------------------------------------------------

    def get(self, search_id: object, *, now: Optional[float] = None) -> SearchSession:
        """Resolve a session, or raise a refusal that says which kind it is.

        "Expired" and "unknown" are separate because the user-facing answers
        differ — search again, versus something is wrong. An expired session is
        also *removed* here, so the same id cannot come back later.
        """
        if not isinstance(search_id, str) or not search_id:
            raise SearchSessionNotFound(
                "no such search", "the search may have expired; run the search again"
            )
        moment = now if now is not None else time.time()
        with self._lock:
            session = self._sessions.get(search_id)
            if session is None:
                raise SearchSessionNotFound(
                    "no such search", "the search may have expired; run the search again"
                )
            if session.expired(moment):
                self._sessions.pop(search_id, None)
                raise SearchSessionExpired(
                    "that search has expired", "run the search again to pick a result"
                )
            return session

    def resolve(
        self,
        search_id: object,
        result_id: object,
        *,
        provider_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> Tuple[SearchSession, MediaResult, ProviderItem]:
        """The one way a result becomes something openable.

        ``provider_id``, when given, must match the session's. That check is what
        stops a YouTube result being opened through the Spotify path: without
        it, a caller who knew a valid search id and result id could route a
        video id into the native-URI adapter.
        """
        session = self.get(search_id, now=now)
        if provider_id is not None and session.provider_id != provider_id:
            raise ResultNotFound(
                "that result does not belong to this provider",
                "a result can only be opened through the provider that produced it",
            )
        if not isinstance(result_id, str) or not result_id:
            raise ResultNotFound("no such result in that search")
        for index, result in enumerate(session.results):
            if result.result_id == result_id:
                return session, result, session.items[index]
        raise ResultNotFound(
            "no such result in that search", "the result list may have changed; search again"
        )

    def first(
        self, search_id: object, *, provider_id: Optional[str] = None, now: Optional[float] = None
    ) -> Tuple[SearchSession, MediaResult, ProviderItem]:
        """Index 0 of a verified session — the "Open first result" action.

        A zero-result search raises rather than returning something: there is no
        first result, and inventing one is how a button that should have been
        unavailable opens the wrong thing.
        """
        session = self.get(search_id, now=now)
        if provider_id is not None and session.provider_id != provider_id:
            raise ResultNotFound(
                "that search does not belong to this provider",
                "a result can only be opened through the provider that produced it",
            )
        if not session.results:
            raise ResultNotFound(
                "that search returned no results",
                "there is no first result to open; try different words",
            )
        return session, session.results[0], session.items[0]

    # -- introspection (tests and diagnostics) -------------------------------

    def count(self, *, now: Optional[float] = None) -> int:
        moment = now if now is not None else time.time()
        with self._lock:
            return sum(1 for session in self._sessions.values() if not session.expired(moment))

    def purge(self, *, now: Optional[float] = None) -> int:
        moment = now if now is not None else time.time()
        with self._lock:
            stale: List[str] = [
                sid for sid, session in self._sessions.items() if session.expired(moment)
            ]
            for search_id in stale:
                self._sessions.pop(search_id, None)
        return len(stale)


__all__ = [
    "MAX_SEARCH_SESSIONS",
    "SEARCH_SESSION_TTL_SECONDS",
    "SearchSession",
    "SearchSessionStore",
]
