"""Replay protection for external Actions. A mapping table, not a task store.

Why the bridge needs one at all
-------------------------------

Task Core is already idempotent where it can be: ``create_task`` and
``send_followup`` both take a ``client_request_id`` and both refuse to do the
work twice. Three of the bridge's five mutations have no such parameter
upstream — ``submit_choice_answer``, ``cancel_task`` and ``finish_task`` are
protected by state instead, which is *correct* and *unhelpful to a retrying
caller*: answering an already-answered question is refused, so a dropped
response turns a successful answer into "that question is closed".

This table turns those refusals back into the truth. It records that a given
external request id already did its work, so the retry returns the same
normalized state with ``replayed: true`` instead of an error about a question
somebody did in fact answer.

What it is not
--------------

**Not a second task database.** It stores no state, no text and no lifecycle. A
row is `(operation, scope, request_id) → (body digest, task id)` and Task Core
remains the only thing that knows what any of those tasks are doing. On replay
the bridge re-reads the current state from Task Core rather than returning a
stored response — so a replayed answer reports where the task is *now*, not
where it was when the first attempt landed.

**Not a body store.** Only a SHA-256 digest of the canonical request is kept.
Comparing is the whole job, and a table that could describe a request is a table
holding somebody's task text in a second place with different retention.

Concurrency
-----------

Two identical requests can be in flight at once — ChatGPT retries, and a phone
on a bad network will produce this. The claim is a single ``INSERT`` under a
``BEGIN IMMEDIATE`` transaction with a primary key across the three columns, so
exactly one caller wins. The loser sees a row with no ``task_id`` yet and gets
``request_in_flight`` rather than a duplicate mutation.

Durability
----------

The table is a real SQLite file under ``state/actions-bridge/``, so a bridge
restart does not forget what it accepted a second ago. Rows older than
:data:`RETENTION_SECONDS` are pruned on write, because replay protection has a
useful lifetime measured in minutes and a table that only grows is a table
somebody eventually deletes by hand.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .limits import MAX_CLIENT_REQUEST_ID_CHARS, MIN_CLIENT_REQUEST_ID_CHARS

#: How long a claimed request id is remembered. Long enough to cover every retry
#: a network or a model provider will make; short enough that the table stays
#: small without maintenance.
RETENTION_SECONDS = 24 * 60 * 60

#: How long a claim may sit unfinished before another attempt may take it over.
#: A bridge killed mid-mutation would otherwise leave a request id permanently
#: unusable, and the caller cannot pick a new one — it is retrying.
STALE_CLAIM_SECONDS = 120

#: Printable, bounded, and no separator that means something in a URL, a log
#: line or a SQL identifier. A model composes these, so the grammar has to be
#: one a model can follow without being told twice.
_REQUEST_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{6,63}\Z")


def valid_request_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and MIN_CLIENT_REQUEST_ID_CHARS <= len(value) <= MAX_CLIENT_REQUEST_ID_CHARS
        and bool(_REQUEST_ID.match(value))
    )


def body_digest(payload: Dict[str, Any]) -> str:
    """A stable digest of a request's meaningful fields.

    Canonical JSON with sorted keys and NFC-normalized text, so the same request
    typed twice on two devices produces the same digest and a genuinely
    different one does not. It is a hash of user content: never published, never
    logged, and never turned back into anything.
    """

    def _normalize(value: Any) -> Any:
        if isinstance(value, str):
            return unicodedata.normalize("NFC", value)
        if isinstance(value, dict):
            return {key: _normalize(inner) for key, inner in value.items()}
        if isinstance(value, list):
            return [_normalize(inner) for inner in value]
        return value

    canonical = json.dumps(
        _normalize(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyConflict(Exception):
    """The same request id arrived with a different body."""


class RequestInFlight(Exception):
    """The same request id is being processed right now."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS bridge_requests (
    operation   TEXT NOT NULL,
    scope       TEXT NOT NULL,
    request_id  TEXT NOT NULL,
    digest      TEXT NOT NULL,
    task_id     TEXT,
    claimed_at  REAL NOT NULL,
    settled_at  REAL,
    PRIMARY KEY (operation, scope, request_id)
);
CREATE INDEX IF NOT EXISTS bridge_requests_claimed
    ON bridge_requests (claimed_at);
"""


class IdempotencyStore:
    """The bridge's own small table. One file, one connection, one lock."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(
            str(path), check_same_thread=False, isolation_level=None
        )
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- the two-phase claim -------------------------------------------------

    def claim(
        self, *, operation: str, scope: str, request_id: str, digest: str
    ) -> Tuple[bool, Optional[str]]:
        """Claim this request id, or report what already happened under it.

        Returns ``(fresh, task_id)``.

        * ``(True, None)`` — the claim is ours; do the work and then
          :meth:`settle`.
        * ``(False, task_id)`` — this exact request already completed. Re-read
          that task and answer with ``replayed: true``.

        Raises :class:`IdempotencyConflict` when the id was used for a different
        body, and :class:`RequestInFlight` when another attempt holds it now.

        The whole decision happens inside one ``BEGIN IMMEDIATE``. Two
        concurrent identical requests therefore serialize at the database rather
        than at a check-then-act in Python, which is where this would otherwise
        be wrong exactly when it matters.
        """
        now = time.time()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            committed = False
            try:
                row = self._db.execute(
                    "SELECT digest, task_id, claimed_at, settled_at "
                    "FROM bridge_requests "
                    "WHERE operation = ? AND scope = ? AND request_id = ?",
                    (operation, scope, request_id),
                ).fetchone()

                if row is not None:
                    stored_digest, task_id, claimed_at, settled_at = row
                    if stored_digest != digest:
                        # Same key, different request. Refused rather than
                        # treated as a retry: returning the first task for the
                        # second instruction would be the worst possible answer.
                        raise IdempotencyConflict()
                    if settled_at is not None:
                        return False, task_id
                    if now - float(claimed_at) < STALE_CLAIM_SECONDS:
                        raise RequestInFlight()
                    # An abandoned claim. Taken over rather than left forever:
                    # the caller is retrying and cannot choose a different id.
                    self._db.execute(
                        "UPDATE bridge_requests SET claimed_at = ? "
                        "WHERE operation = ? AND scope = ? AND request_id = ?",
                        (now, operation, scope, request_id),
                    )
                    self._db.execute("COMMIT")
                    committed = True
                    return True, None

                self._db.execute(
                    "INSERT INTO bridge_requests "
                    "(operation, scope, request_id, digest, task_id, claimed_at, "
                    " settled_at) VALUES (?, ?, ?, ?, NULL, ?, NULL)",
                    (operation, scope, request_id, digest, now),
                )
                self._db.execute("COMMIT")
                committed = True
                return True, None
            finally:
                # Every exit that did not commit ends the transaction here, and
                # ``finally`` rather than a set of ``except`` clauses because the
                # path that got this wrong first was neither: the *replay* return
                # is a success that writes nothing, and an early ``return`` skips
                # an ``except``. It left the transaction open, and the next
                # ``BEGIN IMMEDIATE`` failed with "cannot start a transaction
                # within a transaction" — a bug that only appears on the second
                # replayed request, which is why the end-to-end suite is where it
                # showed up rather than a unit test of one call.
                if not committed:
                    self._db.execute("ROLLBACK")

    def settle(
        self, *, operation: str, scope: str, request_id: str, task_id: Optional[str]
    ) -> None:
        """Record that the claimed work completed, and prune old rows."""
        now = time.time()
        with self._lock:
            self._db.execute(
                "UPDATE bridge_requests SET task_id = ?, settled_at = ? "
                "WHERE operation = ? AND scope = ? AND request_id = ?",
                (task_id, now, operation, scope, request_id),
            )
            self._db.execute(
                "DELETE FROM bridge_requests WHERE claimed_at < ?",
                (now - RETENTION_SECONDS,),
            )

    def release(self, *, operation: str, scope: str, request_id: str) -> None:
        """Drop an unsettled claim after the work failed.

        Deleted rather than marked failed. The caller received an error and may
        legitimately retry the identical request; a row that survived would turn
        that retry into a conflict about a mutation that never happened.

        Guarded on ``settled_at IS NULL`` so this can never remove a completed
        record, even if a later refactor calls it on the wrong path.
        """
        with self._lock:
            self._db.execute(
                "DELETE FROM bridge_requests "
                "WHERE operation = ? AND scope = ? AND request_id = ? "
                "AND settled_at IS NULL",
                (operation, scope, request_id),
            )


__all__ = [
    "IdempotencyConflict",
    "IdempotencyStore",
    "RequestInFlight",
    "RETENTION_SECONDS",
    "STALE_CLAIM_SECONDS",
    "body_digest",
    "valid_request_id",
]
