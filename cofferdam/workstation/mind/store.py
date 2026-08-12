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

#: The lifecycle.
#:
#:      pending ──claim──► applying ──write──► applied
#:         ▲                   │
#:         │                   └──write failed──► pending
#:         │                   └──interrupted, bytes not landed──► interrupted
#:         └───────────────────── interrupted ◄──────────────────────┘
#:
#: plus the two refusals that never touch a document: `rejected` and `stale`.
STATE_PENDING = "pending"

#: **The durable claim.** Written and committed *before* the filesystem is
#: touched, and it is what makes the protocol crash-truthful in both directions.
#:
#: The earlier design committed `applied` and then wrote. That ordering gave the
#: concurrency guarantee — two accepts cannot both pass a compare-and-set — at
#: the cost of a window where the store durably said a change had been applied
#: while the document still held its old bytes. A record that claims a change
#: which is not on disk is precisely the lie this whole path exists to prevent,
#: so the claim is now a *statement of intent* rather than of completion, and
#: `applied` is written only once the rename has returned.
STATE_APPLYING = "applying"

STATE_APPLIED = "applied"
STATE_REJECTED = "rejected"

#: The document moved underneath a pending proposal. Terminal on purpose: a
#: stale proposal is **not** silently refreshed into a fresh one, because the
#: diff a person reviewed is not the diff that would now land, and re-reviewing
#: is the entire point of the base hash. Also used when the *authority* moved —
#: the role now names a different document — with the reason recorded.
STATE_STALE = "stale"

#: An apply was claimed and did not finish, and recovery proved the bytes never
#: landed. **Decidable, not terminal**: the document is at its pre-apply content,
#: so accepting again is a legitimate thing for a person to do — and it has to be
#: a person, on the private surface. Recovery itself never performs the write.
STATE_INTERRUPTED = "interrupted"

PROPOSAL_STATES: Tuple[str, ...] = (
    STATE_PENDING,
    STATE_APPLYING,
    STATE_INTERRUPTED,
    STATE_APPLIED,
    STATE_REJECTED,
    STATE_STALE,
)

#: The states a person may act on. `applying` is deliberately absent: a claimed
#: apply belongs to whoever claimed it until it finishes or recovery classifies
#: it, and a second acceptance arriving meanwhile is refused rather than queued.
DECIDABLE_STATES: Tuple[str, ...] = (STATE_PENDING, STATE_INTERRUPTED)

DECIDED_STATES: Tuple[str, ...] = (STATE_APPLIED, STATE_REJECTED, STATE_STALE)

#: Why a proposal reached the state it is in, when the state alone does not say.
#: A closed vocabulary, because "why is this stale" is a question somebody asks
#: months later and a free-form string would answer it differently every time.
REASON_CONTENT_DRIFTED = "content_drifted"
REASON_TARGET_MISSING = "target_missing"
REASON_AUTHORITY_CHANGED = "authority_changed"
REASON_RECOVERY_CONFLICTED = "recovery_conflicted"
REASON_RECOVERED_APPLIED = "recovered_applied"
REASON_INTERRUPTED = "interrupted"
DECIDED_REASONS: Tuple[str, ...] = (
    REASON_CONTENT_DRIFTED,
    REASON_TARGET_MISSING,
    REASON_AUTHORITY_CHANGED,
    REASON_RECOVERY_CONFLICTED,
    REASON_RECOVERED_APPLIED,
    REASON_INTERRUPTED,
)

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
-- `target_binding_hash` is the second half of what acceptance is bound to. The
-- base hash answers "is this still the text I reviewed"; it cannot answer "is
-- this still the same document". Remap a role to a byte-identical file and a
-- content-only check sees no drift at all. This column is an opaque
-- domain-separated fingerprint of the host authority that resolved the target —
-- never a path, and not reversible into one.
CREATE TABLE IF NOT EXISTS memory_proposals (
    proposal_id         TEXT PRIMARY KEY,
    scope               TEXT    NOT NULL,
    workspace_id        TEXT,
    role                TEXT    NOT NULL,
    operation           TEXT    NOT NULL,
    content             TEXT    NOT NULL,
    content_hash        TEXT    NOT NULL,
    base_hash           TEXT    NOT NULL,
    base_bytes          INTEGER NOT NULL,
    target_binding_hash TEXT    NOT NULL,
    reason              TEXT    NOT NULL,
    source              TEXT    NOT NULL,
    state               TEXT    NOT NULL,
    decided_reason      TEXT,
    created_at          TEXT    NOT NULL,
    claimed_at          TEXT,
    decided_at          TEXT,
    applied_hash        TEXT
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
    target_binding_hash: str
    reason: str
    source: str
    state: str
    decided_reason: Optional[str]
    created_at: str
    claimed_at: Optional[str]
    decided_at: Optional[str]
    applied_hash: Optional[str]

    @property
    def pending(self) -> bool:
        return self.state == STATE_PENDING

    @property
    def decidable(self) -> bool:
        """Whether a person may act on this proposal now.

        `interrupted` is decidable and `applying` is not. An interrupted apply
        left the document at its pre-apply bytes, so accepting again is a
        legitimate thing for a person to do; a claimed apply belongs to whoever
        claimed it until it finishes or recovery classifies it.
        """
        return self.state in DECIDABLE_STATES

    def to_dict(self, *, include_content: bool = False) -> Dict[str, Any]:
        """The client-facing shape. **No path, and no content unless asked.**

        A listing renders many proposals at once and does not need the text of
        each; the single-proposal read does. Keeping content out of the list is
        not only bandwidth — it keeps somebody's draft out of a payload that a
        client is most likely to poll and cache.

        `target_binding_hash` is **not** published. It is an opaque fingerprint
        and publishing it would hand a client a stable identifier for a host
        location it is otherwise never told about — which is the same reasoning
        that keeps the project root out of `TaskProject.to_dict`. The client
        needs to know *that* the target moved, which `decided_reason` and the
        refusal code both say; it does not need the fingerprint that proved it.
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
            "decided_reason": self.decided_reason,
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
        target_binding_hash=row["target_binding_hash"],
        reason=row["reason"],
        source=row["source"],
        state=row["state"],
        decided_reason=row["decided_reason"],
        created_at=row["created_at"],
        claimed_at=row["claimed_at"],
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
        target_binding_hash: str,
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
                    target_binding_hash, reason, source, state, decided_reason,
                    created_at, claimed_at, decided_at, applied_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, NULL)
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
                    target_binding_hash,
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
            target_binding_hash=target_binding_hash,
            reason=reason,
            source=source,
            state=STATE_PENDING,
            decided_reason=None,
            created_at=stamp,
            claimed_at=None,
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

    def _transition(
        self,
        proposal_id: str,
        *,
        to_state: str,
        from_states: Tuple[str, ...],
        decided_reason: Optional[str] = None,
        applied_hash: Optional[str] = None,
        stamp_claim: bool = False,
        stamp_decision: bool = False,
    ) -> Optional[MemoryProposal]:
        """One durable compare-and-set on a proposal's state.

        **Every** state change goes through here, and the permitted source
        states are always in the ``UPDATE`` predicate rather than in a preceding
        read. That is what makes the protocol safe under concurrency without a
        lock held across the filesystem: two callers racing on the same proposal
        both issue the same conditional update, SQLite serializes them, exactly
        one changes a row, and the loser gets ``None`` and can report what
        actually happened. It is the single-use shape the Trust Core's approval
        consume has, expressed in SQL.
        """
        stamp = now_iso()
        placeholders = ", ".join("?" for _ in from_states)
        assignments = ["state = ?", "decided_reason = ?"]
        values: list = [to_state, decided_reason]
        if stamp_claim:
            assignments.append("claimed_at = ?")
            values.append(stamp)
        if stamp_decision:
            assignments.append("decided_at = ?")
            values.append(stamp)
        assignments.append("applied_hash = ?")
        values.append(applied_hash)
        with self._write() as connection:
            cursor = connection.execute(
                "UPDATE memory_proposals SET "
                + ", ".join(assignments)
                + " WHERE proposal_id = ? AND state IN ("
                + placeholders
                + ")",
                tuple(values) + (proposal_id,) + tuple(from_states),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM memory_proposals WHERE proposal_id = ?", (proposal_id,)
            ).fetchone()
        return _row_to_proposal(row) if row is not None else None

    def claim(self, proposal_id: str) -> Optional[MemoryProposal]:
        """Take ownership of an apply, durably, **before** the filesystem is touched.

        This is the concurrency boundary and the crash-truth boundary at once.

        *Concurrency*: the transition is conditional on the proposal being
        decidable, so of two accepts arriving together exactly one becomes the
        writer and the other is told the proposal is no longer decidable.

        *Crash truth*: what is committed here is an **intent**, not a
        completion. The earlier design committed `applied` and then wrote, which
        bought the same exclusivity at the cost of a window where the store
        durably claimed a change that was not on disk. `applying` says only that
        somebody started, which is true at the instant it is written and stays
        true however the process ends.
        """
        return self._transition(
            proposal_id,
            to_state=STATE_APPLYING,
            from_states=DECIDABLE_STATES,
            stamp_claim=True,
        )

    def finalize_applied(self, proposal_id: str, *, applied_hash: str,
                         reason: Optional[str] = None) -> Optional[MemoryProposal]:
        """Record that the bytes landed. Only ever from ``applying``."""
        return self._transition(
            proposal_id,
            to_state=STATE_APPLIED,
            from_states=(STATE_APPLYING,),
            applied_hash=applied_hash,
            decided_reason=reason,
            stamp_decision=True,
        )

    def release(self, proposal_id: str, *, to_state: str, reason: Optional[str] = None):
        """Give a claim back, to ``pending`` or ``interrupted``. Never to applied.

        Used when the write failed (the document is byte-identical, so the
        proposal is decidable again) and by recovery when an interrupted apply
        provably did not land.
        """
        if to_state not in DECIDABLE_STATES:  # pragma: no cover - callers pass constants
            raise ValueError("release targets a decidable state: " + str(to_state))
        return self._transition(
            proposal_id,
            to_state=to_state,
            from_states=(STATE_APPLYING,),
            decided_reason=reason,
        )

    def decide(
        self,
        proposal_id: str,
        *,
        state: str,
        reason: Optional[str] = None,
        from_states: Optional[Tuple[str, ...]] = None,
    ) -> Optional[MemoryProposal]:
        """Refuse a proposal — ``rejected`` or ``stale``. Touches no document.

        ``from_states`` defaults to the decidable ones. Recovery passes
        ``(STATE_APPLYING,)`` so it can retire a claim it has classified as
        conflicted without going through :meth:`release` first.
        """
        if state not in (STATE_REJECTED, STATE_STALE):  # pragma: no cover
            raise ValueError("not a refusal state: " + str(state))
        return self._transition(
            proposal_id,
            to_state=state,
            from_states=from_states or DECIDABLE_STATES,
            decided_reason=reason,
            stamp_decision=True,
        )

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
    "DECIDABLE_STATES",
    "DECIDED_REASONS",
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
    "REASON_AUTHORITY_CHANGED",
    "REASON_CONTENT_DRIFTED",
    "REASON_INTERRUPTED",
    "REASON_RECOVERED_APPLIED",
    "REASON_RECOVERY_CONFLICTED",
    "REASON_TARGET_MISSING",
    "STATE_APPLIED",
    "STATE_APPLYING",
    "STATE_INTERRUPTED",
    "STATE_PENDING",
    "STATE_REJECTED",
    "STATE_STALE",
    "MemoryProposal",
    "MindStore",
]
