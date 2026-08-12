"""The durable proposal queue: SQLite, its own database, workflow state only.

What this database is
---------------------

**Workflow state, and nothing else.** D-2026-08-08-6 is unambiguous that Markdown
is canonical memory and that anything else is derived, rebuildable and
discardable. A row here says *somebody proposed a change and it has not been
decided yet*. It is not a copy of memory, it is not an index of memory, and if it
disagrees with the Markdown the Markdown is right.

Which makes the deletion story precise: **remove ``state/mind/`` and the host
forgets the pending proposals.** Every document in every project and in the vault
is untouched, because they were never in here. That is the rollback property, and
it is only true because the files are separate.

Its own database, beside ``tasks.sqlite3`` and ``workspace.sqlite3``
--------------------------------------------------------------------

The same argument :mod:`..workspace.store` makes. Task Core's store has one job
and a strong invariant — a state change and its event land in one transaction —
and rows with a different lifetime under that lock would make "can this file be
deleted?" a question with two answers. It has three separate answers now, and
each is a plain sentence.

Posture copied rather than re-decided: WAL, ``synchronous=FULL`` because the
write most worth not losing is the last one, ``foreign_keys=ON``, ``0700`` on the
directory and ``0600`` on the database and its WAL/shm siblings — the
write-ahead log holds proposed document text, which is somebody's own words.

What a row may never hold
-------------------------

No path, no root, no vault location: a proposal names a **scope and a role**, and
the mapping from those to a file is host configuration that is re-read at apply
time. Storing the resolved path would make the row a second authority holding a
stale copy — and worse, one that survives an edit to the configuration that was
supposed to move the target.

No provider session id, no model name, no adapter id, no credential, no
reasoning transcript. A proposal records what changed, why, and against which
base; the machinery that produced it is not durable state.
"""

from __future__ import annotations

import sqlite3
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..runtime.identity import now_iso
from ..tasks.errors import StoreUnavailable

#: Version 1: the first schema. The same additive-only discipline Task Core and
#: the workspace store use — a change that is not a ``CREATE TABLE IF NOT
#: EXISTS`` or a new nullable column needs a migration and a decision, not a
#: version bump.
SCHEMA_VERSION = 1

MIND_DIRNAME = "mind"
DATABASE_FILENAME = "mind.sqlite3"

BUSY_TIMEOUT_MS = 5000

#: The only mutation a proposal can describe. **One word, and the list is the
#: assertion**: there is no `delete`, no `rename`, no `move`, no `create`, and
#: no `append`. D-2026-08-11-4 point 6 says deletion of durable memory is never
#: planner-proposable, and the honest form of that is not a check that refuses
#: the word — it is a vocabulary that does not contain it.
OPERATION_REPLACE = "replace_document"
OPERATIONS: Tuple[str, ...] = (OPERATION_REPLACE,)

#: The lifecycle. `pending` is the only state anything can be decided from.
STATE_PENDING = "pending"
STATE_APPLIED = "applied"
STATE_REJECTED = "rejected"
#: The document moved underneath a pending proposal. Terminal on purpose: a
#: stale proposal is **not** silently refreshed into a fresh one, because the
#: diff a person reviewed is not the diff that would now land, and re-reviewing
#: is the entire point of the base hash.
STATE_STALE = "stale"

PROPOSAL_STATES: Tuple[str, ...] = (STATE_PENDING, STATE_APPLIED, STATE_REJECTED, STATE_STALE)
DECIDED_STATES: Tuple[str, ...] = (STATE_APPLIED, STATE_REJECTED, STATE_STALE)

#: Who proposed it. A closed vocabulary, assigned from the authenticated surface
#: and never from a request body — the reason task `origin` is assigned that way.
#: `planner` is reserved and unwritable in this milestone: no planner exists, and
#: nothing in this build can produce it.
SOURCE_USER = "user"
SOURCE_COFFERDAM = "cofferdam"
SOURCE_PLANNER = "planner"
PROPOSAL_SOURCES: Tuple[str, ...] = (SOURCE_USER, SOURCE_COFFERDAM, SOURCE_PLANNER)

#: How many proposals a listing returns at most, and the default.
MAX_LIST_LIMIT = 100
DEFAULT_LIST_LIMIT = 20

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One intended mutation to one approved semantic target.
--
-- `scope` + `role` (+ `workspace_id` for project scope) IS the target. There is
-- deliberately no path column: the mapping from a role to a file is host
-- configuration, re-read at apply time, and a stored path would be a second
-- authority holding a copy that an edit to that configuration would silently
-- invalidate.
--
-- `base_hash` is what makes acceptance mean something. It is the hash of the
-- document as it was when somebody was shown the change, and the apply refuses
-- unless the document still hashes to it.
CREATE TABLE IF NOT EXISTS memory_proposals (
    proposal_id   TEXT PRIMARY KEY,
    scope         TEXT    NOT NULL,
    workspace_id  TEXT,
    role          TEXT    NOT NULL,
    operation     TEXT    NOT NULL,
    content       TEXT    NOT NULL,
    content_hash  TEXT    NOT NULL,
    base_hash     TEXT    NOT NULL,
    base_bytes    INTEGER NOT NULL,
    reason        TEXT    NOT NULL,
    source        TEXT    NOT NULL,
    state         TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    decided_at    TEXT,
    applied_hash  TEXT
);

CREATE INDEX IF NOT EXISTS memory_proposals_by_state
    ON memory_proposals (state, created_at DESC);

CREATE INDEX IF NOT EXISTS memory_proposals_by_created
    ON memory_proposals (created_at DESC);
"""


@dataclass(frozen=True)
class MemoryProposal:
    """One proposal as stored. Not the published shape.

    The published shape is assembled in :mod:`.service` and adds one derived
    fact — whether the target has drifted *right now* — which is deliberately
    not a column: a stored `stale` flag would be correct for a few seconds and
    then wrong with nothing announcing it.
    """

    proposal_id: str
    scope: str
    workspace_id: Optional[str]
    role: str
    operation: str
    content: str
    content_hash: str
    base_hash: str
    base_bytes: int
    reason: str
    source: str
    state: str
    created_at: str
    decided_at: Optional[str]
    applied_hash: Optional[str]

    @property
    def pending(self) -> bool:
        return self.state == STATE_PENDING

    def to_dict(self, *, include_content: bool = False) -> Dict[str, Any]:
        """The client-facing shape. **No path, and no content unless asked.**

        A listing renders many proposals at once and does not need the text of
        each; the single-proposal read does. Keeping content out of the list is
        not only bandwidth — it keeps somebody's draft out of a payload that a
        client is most likely to poll and cache.
        """
        payload: Dict[str, Any] = {
            "proposal_id": self.proposal_id,
            "scope": self.scope,
            "workspace_id": self.workspace_id,
            "role": self.role,
            "operation": self.operation,
            "content_hash": self.content_hash,
            "base_hash": self.base_hash,
            "base_bytes": self.base_bytes,
            "reason": self.reason,
            "source": self.source,
            "state": self.state,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "applied_hash": self.applied_hash,
        }
        if include_content:
            payload["content"] = self.content
        return payload


def _row_to_proposal(row: sqlite3.Row) -> MemoryProposal:
    return MemoryProposal(
        proposal_id=row["proposal_id"],
        scope=row["scope"],
        workspace_id=row["workspace_id"],
        role=row["role"],
        operation=row["operation"],
        content=row["content"],
        content_hash=row["content_hash"],
        base_hash=row["base_hash"],
        base_bytes=int(row["base_bytes"]),
        reason=row["reason"],
        source=row["source"],
        state=row["state"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        applied_hash=row["applied_hash"],
    )


class MindStore:
    """The durable home of the memory-proposal queue.

    One connection under one lock, for the reason :class:`~..tasks.store.TaskStore`
    gives: this is a single-user workstation service, and one connection makes
    "was that transactional" answerable by reading a method.
    """

    def __init__(self, config, *, path: Optional[Path] = None) -> None:
        self._config = config
        self._path = Path(path) if path is not None else self._default_path(config)
        self._lock = threading.RLock()
        self._connection: Optional[sqlite3.Connection] = None

    @staticmethod
    def _default_path(config) -> Path:
        return Path(config.state_dir) / MIND_DIRNAME / DATABASE_FILENAME

    @property
    def path(self) -> Path:
        return self._path

    # -- connection ----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Owner-only on the directory *before* the file is created, so the
            # database is never briefly group- or world-readable on a fresh
            # install. The ordering Task Core and the workspace store both use.
            try:
                self._path.parent.chmod(stat.S_IRWXU)
            except OSError:  # pragma: no cover - platform dependent
                pass
            connection = sqlite3.connect(
                str(self._path),
                timeout=BUSY_TIMEOUT_MS / 1000.0,
                isolation_level=None,  # explicit BEGIN, so transactions are ours
                check_same_thread=False,
            )
        except sqlite3.Error as exc:
            raise StoreUnavailable(type(exc).__name__) from None
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=" + str(BUSY_TIMEOUT_MS))
            connection.executescript(_SCHEMA)
            self._apply_schema_version(connection)
        except BaseException:
            # The refusal path is reachable — a database written by a newer
            # build raises out of the version check — and it runs on a
            # connection that is already open. Closing it here keeps this
            # process from holding a handle and a lock on a database it has
            # just decided it must not touch.
            connection.close()
            raise
        self._restrict_files()
        self._connection = connection
        return connection

    def _restrict_files(self) -> None:
        """Owner-only on the database and its WAL/shm siblings.

        The siblings are the part worth doing explicitly: SQLite creates them at
        the process umask, which on an ordinary account is ``0644``, and the
        write-ahead log holds proposed document text.
        """
        for suffix in ("", "-wal", "-shm"):
            sibling = self._path.with_name(self._path.name + suffix)
            try:
                sibling.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:  # pragma: no cover - absent, or a platform without modes
                continue

    def _apply_schema_version(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            return
        try:
            found = int(row["value"])
        except (TypeError, ValueError):
            raise StoreUnavailable("the mind database records an unreadable schema version")
        if found > SCHEMA_VERSION:
            # Forward-only, the rule both other stores apply: a newer database
            # opened by an older build is a rollback, and refusing is safer than
            # writing rows the newer schema will not understand.
            raise StoreUnavailable(
                "the mind database was written by a newer version of Cofferdam"
            )
        if found < SCHEMA_VERSION:
            connection.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            self._restrict_files()

    @contextmanager
    def _read_if_exists(self) -> Iterator[Optional[sqlite3.Connection]]:
        """The connection, or ``None`` when this host has no database yet.

        **A read must never create one.** A client polls the proposal list, and
        an ordinary connect would manufacture a state directory and an empty
        database out of somebody looking at a screen. An unconfigured host is
        *untouched*, not touched harmlessly — the same promise
        :mod:`..workspace.store` makes.
        """
        with self._lock:
            if self._connection is None and not self._path.exists():
                yield None
                return
            yield self._connect()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    # -- proposals -----------------------------------------------------------

    def create_proposal(
        self,
        *,
        proposal_id: str,
        scope: str,
        workspace_id: Optional[str],
        role: str,
        content: str,
        content_hash: str,
        base_hash: str,
        base_bytes: int,
        reason: str,
        source: str,
    ) -> MemoryProposal:
        """Record one pending proposal. Writes no Markdown, by construction.

        This method cannot touch a document: it holds a database connection and
        nothing else, and the only filesystem path it knows is its own.
        """
        stamp = now_iso()
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO memory_proposals (
                    proposal_id, scope, workspace_id, role, operation,
                    content, content_hash, base_hash, base_bytes,
                    reason, source, state, created_at, decided_at, applied_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    proposal_id,
                    scope,
                    workspace_id,
                    role,
                    OPERATION_REPLACE,
                    content,
                    content_hash,
                    base_hash,
                    int(base_bytes),
                    reason,
                    source,
                    STATE_PENDING,
                    stamp,
                ),
            )
        return MemoryProposal(
            proposal_id=proposal_id,
            scope=scope,
            workspace_id=workspace_id,
            role=role,
            operation=OPERATION_REPLACE,
            content=content,
            content_hash=content_hash,
            base_hash=base_hash,
            base_bytes=int(base_bytes),
            reason=reason,
            source=source,
            state=STATE_PENDING,
            created_at=stamp,
            decided_at=None,
            applied_hash=None,
        )

    def get_proposal(self, proposal_id: object) -> Optional[MemoryProposal]:
        if not isinstance(proposal_id, str) or not proposal_id:
            return None
        with self._read_if_exists() as connection:
            if connection is None:
                return None
            row = connection.execute(
                "SELECT * FROM memory_proposals WHERE proposal_id = ?", (proposal_id,)
            ).fetchone()
        return _row_to_proposal(row) if row is not None else None

    def list_proposals(
        self, *, state: Optional[str] = None, limit: int = DEFAULT_LIST_LIMIT
    ) -> List[MemoryProposal]:
        """Proposals, newest first, bounded. An unknown state matches nothing."""
        bound = max(1, min(int(limit), MAX_LIST_LIMIT))
        with self._read_if_exists() as connection:
            if connection is None:
                return []
            if state is None:
                rows = connection.execute(
                    "SELECT * FROM memory_proposals ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (bound,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM memory_proposals WHERE state = ?"
                    " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (state, bound),
                ).fetchall()
        return [_row_to_proposal(row) for row in rows]

    def decide(
        self, proposal_id: str, *, state: str, applied_hash: Optional[str] = None
    ) -> Optional[MemoryProposal]:
        """Move a **pending** proposal to a decided state, once.

        The ``state = 'pending'`` predicate is in the ``UPDATE`` rather than in a
        read-then-write, so two accepts racing on the same proposal cannot both
        see `pending` and both proceed: exactly one row is changed and the loser
        gets ``None``. That is the same single-use shape the Trust Core's
        approval consume has, expressed in SQL.
        """
        if state not in DECIDED_STATES:  # pragma: no cover - callers pass constants
            raise ValueError("not a decided state: " + str(state))
        stamp = now_iso()
        with self._write() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_proposals
                   SET state = ?, decided_at = ?, applied_hash = ?
                 WHERE proposal_id = ? AND state = ?
                """,
                (state, stamp, applied_hash, proposal_id, STATE_PENDING),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM memory_proposals WHERE proposal_id = ?", (proposal_id,)
            ).fetchone()
        return _row_to_proposal(row) if row is not None else None

    def reopen(self, proposal_id: str) -> Optional[MemoryProposal]:
        """Put an **applied** proposal back to pending, after a failed write.

        The narrow inverse of :meth:`decide`, and it exists for exactly one
        caller: the apply claims `applied` before touching the filesystem, so
        that two concurrent accepts cannot both reach it. If the write then
        fails, the document is byte-identical to what it was and the row is a
        lie — this puts it back.

        Deliberately not a general "undo". The predicate is ``state =
        'applied'`` and it clears the applied hash, so it cannot revive a
        rejected or stale proposal, which are decisions a person made.
        """
        with self._write() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_proposals
                   SET state = ?, decided_at = NULL, applied_hash = NULL
                 WHERE proposal_id = ? AND state = ?
                """,
                (STATE_PENDING, proposal_id, STATE_APPLIED),
            )
            if cursor.rowcount != 1:  # pragma: no cover - only one caller, under lock
                return None
            row = connection.execute(
                "SELECT * FROM memory_proposals WHERE proposal_id = ?", (proposal_id,)
            ).fetchone()
        return _row_to_proposal(row) if row is not None else None

    def counts(self) -> Dict[str, int]:
        """How many proposals sit in each state. Empty on an unconfigured host."""
        tally = {state: 0 for state in PROPOSAL_STATES}
        with self._read_if_exists() as connection:
            if connection is None:
                return tally
            for row in connection.execute(
                "SELECT state, COUNT(*) AS total FROM memory_proposals GROUP BY state"
            ):
                if row["state"] in tally:
                    tally[row["state"]] = int(row["total"])
        return tally


__all__ = [
    "DECIDED_STATES",
    "DEFAULT_LIST_LIMIT",
    "MAX_LIST_LIMIT",
    "MIND_DIRNAME",
    "OPERATIONS",
    "OPERATION_REPLACE",
    "PROPOSAL_SOURCES",
    "PROPOSAL_STATES",
    "SCHEMA_VERSION",
    "SOURCE_COFFERDAM",
    "SOURCE_PLANNER",
    "SOURCE_USER",
    "STATE_APPLIED",
    "STATE_PENDING",
    "STATE_REJECTED",
    "STATE_STALE",
    "MemoryProposal",
    "MindStore",
]
