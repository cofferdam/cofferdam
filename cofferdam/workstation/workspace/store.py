"""Durable Working Context: SQLite, its own database, per workspace.

Why its own database, beside ``tasks.sqlite3`` rather than inside it
--------------------------------------------------------------------

Task Core's store has one job and a very strong invariant: a state change and
its event land in one transaction, and nothing else writes task state. Adding
workspace tables to it would put rows with a completely different lifetime under
that lock and inside those backups, and would make "can this file be deleted?"
a question with two answers.

It also matters for rollback. This database is deletable: remove it and the host
forgets which workspace was active, while every task, turn, clarification and
result is untouched. That is the property the replan asked for in as many words,
and it is only true if the files are separate.

The posture is copied deliberately, not re-decided: WAL so a poll does not block
a write, ``synchronous=FULL`` because the write most worth not losing is the last
one, ``foreign_keys=ON``, ``0700`` on the directory and ``0600`` on the database
and its WAL/shm siblings.

Per workspace, not global
-------------------------

The obvious shape is one row: active workspace, objective, next step. It is
wrong, and the failure is specific. Switch from the Cofferdam workspace to a
research workspace and the objective *stays* — now reading "Implement M2J
workspace foundation" while you are looking at something else. Switch back and
the original objective is gone.

So context is keyed by workspace and one small table names the active one.
Switching is then a pointer move that disturbs nothing, and each workspace keeps
its own objective, its own expected next step and its own references. That is
also what makes the cross-workspace confusion in the threat model structurally
impossible rather than a rule somebody has to remember.

What is *not* in here
---------------------

No task state. No adapter or worker id. No display names. Every one of those is
derived at read time in :mod:`.service`, because storing them would be storing a
copy of somebody else's truth, and a copy is a thing that goes stale silently.

No secrets, no provider session ids, no paths. The references this table holds
are short opaque strings whose *meaning* arrives in later PRs; what it can
promise today is that they are bounded and that absence is stored as absence.
"""

from __future__ import annotations

import sqlite3
import stat
import threading
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from ..runtime.identity import now_iso
from ..tasks.errors import StoreUnavailable
from .errors import ContextFieldInvalid

#: Version 1: the first schema. Bumped whenever the shape below changes, with
#: the same additive-only discipline Task Core uses — a change that is not a
#: ``CREATE TABLE IF NOT EXISTS`` or a new nullable column needs a migration and
#: a decision, not a version bump.
SCHEMA_VERSION = 1

#: Its own directory under ``state/`` so the database and its WAL/shm siblings
#: stay together and can be permissioned, backed up and deleted as a unit.
WORKSPACE_DIRNAME = "workspace"
DATABASE_FILENAME = "workspace.sqlite3"

BUSY_TIMEOUT_MS = 5000

#: An objective is a sentence, not a plan. The bound is generous enough for a
#: real one in Turkish or English and small enough that it cannot become a
#: document — if somebody needs a document, that is `PLAN.md`, and PR2 is where
#: Cofferdam learns to read it.
MAX_OBJECTIVE_CHARS = 500

#: The same, for the expected next step.
MAX_NEXT_STEP_CHARS = 500

#: Continuity references — plan checkpoint, pending decision, latest evidence.
#: Short opaque strings in PR1: something a later PR can resolve, bounded now so
#: that the shape cannot become a payload.
MAX_REFERENCE_CHARS = 200

#: How many previous objectives are kept per workspace. Bounded because this is
#: state and not an archive; the oldest are pruned in the same transaction that
#: writes a new one, so the table cannot grow without limit on a busy host.
MAX_OBJECTIVE_HISTORY = 50

#: Who set a value. A closed vocabulary, because "where did this come from" is a
#: question the planner will ask in M2L and a free-form string would answer it
#: differently every time. ``planner`` is reserved and unused in PR1 — no
#: planner exists, and nothing here can write it yet.
SOURCE_USER = "user"
SOURCE_COFFERDAM = "cofferdam"
SOURCE_PLANNER = "planner"
CONTEXT_SOURCES: Tuple[str, ...] = (SOURCE_USER, SOURCE_COFFERDAM, SOURCE_PLANNER)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Which workspace is active. At most one row, pinned by the CHECK: a table that
-- could hold two would make "which is active" a question about ordering, and
-- ordering is authority nowhere in this product.
--
-- The workspace id is NOT a foreign key to anything. Workspaces live in a
-- host-owned JSON file that a person edits with the daemon stopped, so the
-- active id can legitimately name a workspace that was renamed or removed since
-- it was chosen. That is a truthful state to report, not a corruption to
-- prevent, and it is resolved on read.
CREATE TABLE IF NOT EXISTS active_workspace (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    workspace_id TEXT    NOT NULL,
    changed_at   TEXT    NOT NULL
);

-- One workspace's durable context. Keyed by workspace id so switching is a
-- pointer move and nothing leaks between workspaces.
--
-- Every column here is something a person or Cofferdam *decided*. There is
-- deliberately no task state, no adapter id, no display name and no timestamp
-- copied from another store: those are derived at read time, because a stored
-- copy of somebody else's truth is a copy that goes stale without saying so.
CREATE TABLE IF NOT EXISTS workspace_context (
    workspace_id         TEXT PRIMARY KEY,
    objective            TEXT,
    objective_set_at     TEXT,
    objective_source     TEXT,
    active_task_id       TEXT,
    active_task_set_at   TEXT,
    plan_checkpoint      TEXT,
    pending_decision_ref TEXT,
    latest_evidence_ref  TEXT,
    expected_next_step   TEXT,
    revision             INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

-- Previous objectives, newest last by sequence. Bounded per workspace.
--
-- Its own table rather than a JSON column for the reason `task_turns` has one:
-- an objective that was replaced is history somebody may want to read, and a
-- blob that has to be rewritten whole to append is a blob that loses an entry
-- the first time two writes race.
CREATE TABLE IF NOT EXISTS objective_history (
    workspace_id TEXT    NOT NULL,
    sequence     INTEGER NOT NULL,
    objective    TEXT    NOT NULL,
    set_at       TEXT    NOT NULL,
    source       TEXT    NOT NULL,
    ended_at     TEXT,
    PRIMARY KEY (workspace_id, sequence)
);

CREATE INDEX IF NOT EXISTS objective_history_by_workspace
    ON objective_history (workspace_id, sequence DESC);
"""


def bounded_context_text(value: object, limit: int, field: str) -> Optional[str]:
    """One stored line of somebody's own words, or a refusal.

    Refused rather than truncated, which is the opposite of what
    :func:`~..tasks.models.bounded_text` does for adapter output — and the
    difference is deliberate. Adapter text is *reported* and truncating it loses
    nothing a person chose. This text is *authored*: silently storing half of
    somebody's objective would show them a sentence they did not write and let
    them believe Cofferdam had it right.

    ``None`` is a value here, not a failure: it clears the field.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContextFieldInvalid(field, "expected a string or null")
    cleaned = " ".join(
        "".join(
            character
            for character in unicodedata.normalize("NFC", value)
            if not (
                ord(character) < 0x20
                or ord(character) == 0x7F
                or 0x80 <= ord(character) <= 0x9F
            )
        ).split()
    )
    if not cleaned:
        # An empty or whitespace-only string clears the field. Storing "" would
        # make "set to nothing" and "never set" two different states that render
        # identically, and every reader would have to know which it had.
        return None
    if len(cleaned) > limit:
        raise ContextFieldInvalid(
            field, "at most " + str(limit) + " characters, and this is " + str(len(cleaned))
        )
    return cleaned


@dataclass(frozen=True)
class WorkingContextRow:
    """One workspace's durable context, as stored. Not the published shape.

    The published shape adds derived facts and lives in :mod:`.service`; keeping
    them apart is what stops a derived value being written back by accident.
    """

    workspace_id: str
    objective: Optional[str]
    objective_set_at: Optional[str]
    objective_source: Optional[str]
    active_task_id: Optional[str]
    active_task_set_at: Optional[str]
    plan_checkpoint: Optional[str]
    pending_decision_ref: Optional[str]
    latest_evidence_ref: Optional[str]
    expected_next_step: Optional[str]
    revision: int
    created_at: str
    updated_at: str

    @property
    def empty(self) -> bool:
        """Whether anything has ever been recorded for this workspace."""
        return not any(
            (
                self.objective,
                self.active_task_id,
                self.plan_checkpoint,
                self.pending_decision_ref,
                self.latest_evidence_ref,
                self.expected_next_step,
            )
        )


@dataclass(frozen=True)
class ObjectiveHistoryEntry:
    sequence: int
    objective: str
    set_at: str
    source: str
    ended_at: Optional[str]

    def to_dict(self) -> dict:
        return {
            "sequence": self.sequence,
            "objective": self.objective,
            "set_at": self.set_at,
            "source": self.source,
            "ended_at": self.ended_at,
        }


def _row_to_context(row: sqlite3.Row) -> WorkingContextRow:
    return WorkingContextRow(
        workspace_id=row["workspace_id"],
        objective=row["objective"],
        objective_set_at=row["objective_set_at"],
        objective_source=row["objective_source"],
        active_task_id=row["active_task_id"],
        active_task_set_at=row["active_task_set_at"],
        plan_checkpoint=row["plan_checkpoint"],
        pending_decision_ref=row["pending_decision_ref"],
        latest_evidence_ref=row["latest_evidence_ref"],
        expected_next_step=row["expected_next_step"],
        revision=int(row["revision"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class WorkspaceStore:
    """The durable home of the active workspace and each workspace's context.

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
        return Path(config.state_dir) / WORKSPACE_DIRNAME / DATABASE_FILENAME

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
            # install. The same ordering Task Core uses, for the same reason.
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
            # The refusal path is reachable — a database written by a newer build
            # raises out of the version check — and it runs on a connection that
            # is already open. Leaving it to the garbage collector holds a file
            # handle and a lock on a database this process has just decided it
            # must not touch, which is the wrong thing to be uncertain about.
            connection.close()
            raise
        self._restrict_files()
        self._connection = connection
        return connection

    def _restrict_files(self) -> None:
        """Owner-only on the database and its WAL/shm siblings.

        The siblings are the part worth doing explicitly: SQLite creates them at
        the process umask, which on an ordinary account is ``0644``, and the
        write-ahead log holds recently written objectives — somebody's own words
        about their own work.
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
            raise StoreUnavailable("the workspace database records an unreadable schema version")
        if found > SCHEMA_VERSION:
            # Forward-only, the same rule Task Core applies: a newer database
            # opened by an older build is a rollback, and refusing is safer than
            # writing rows the newer schema will not understand.
            raise StoreUnavailable(
                "the workspace database was written by a newer version of Cofferdam"
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
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._connect()

    @contextmanager
    def _read_if_exists(self) -> Iterator[Optional[sqlite3.Connection]]:
        """The connection, or ``None`` when this host has no database yet.

        **A read must never create one.** The PWA polls, so on a host that has
        never activated a workspace an ordinary ``_connect`` would manufacture a
        state directory and an empty database out of somebody looking at a
        screen — and the backward-compatibility promise this PR makes is that an
        unconfigured host is *untouched*, not that it is touched harmlessly.

        Every read below answers the empty case from nothing: no active
        workspace, an empty context row, no history. Those are the same answers
        the database would give, arrived at without writing a file.
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

    # -- active workspace ----------------------------------------------------

    def active_workspace_id(self) -> Optional[str]:
        """Which workspace is active, or ``None``. Never guesses one."""
        with self._read_if_exists() as connection:
            if connection is None:
                return None
            row = connection.execute(
                "SELECT workspace_id FROM active_workspace WHERE id = 1"
            ).fetchone()
        return row["workspace_id"] if row is not None else None

    def set_active_workspace(self, workspace_id: str) -> str:
        """Point at a workspace and return when it happened.

        The id is stored as given. Whether it names a workspace that exists,
        is enabled, and has a usable project is the service's question, asked
        before this is called and again on every read — because the file can
        change while the daemon runs.
        """
        stamp = now_iso()
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO active_workspace (id, workspace_id, changed_at)
                VALUES (1, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    workspace_id = excluded.workspace_id,
                    changed_at   = excluded.changed_at
                """,
                (workspace_id, stamp),
            )
            self._ensure_context(connection, workspace_id, stamp)
        return stamp

    def clear_active_workspace(self) -> None:
        """Forget which workspace is active, keeping every workspace's context.

        Deliberately not a delete of the context rows. Deactivating is not
        forgetting, and somebody who deactivates and reactivates should find
        their objective where they left it.
        """
        with self._write() as connection:
            connection.execute("DELETE FROM active_workspace WHERE id = 1")

    # -- working context -----------------------------------------------------

    @staticmethod
    def _ensure_context(connection: sqlite3.Connection, workspace_id: str, stamp: str) -> None:
        connection.execute(
            """
            INSERT INTO workspace_context (workspace_id, created_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (workspace_id) DO NOTHING
            """,
            (workspace_id, stamp, stamp),
        )

    def context(self, workspace_id: str) -> WorkingContextRow:
        """One workspace's stored context, empty rather than absent.

        A workspace that has never been written returns a row of ``None``s
        instead of raising. "Nothing has been recorded here yet" is an answer,
        and making every caller handle a missing row is how a caller ends up
        inventing a default.
        """
        with self._read_if_exists() as connection:
            row = (
                None
                if connection is None
                else connection.execute(
                    "SELECT * FROM workspace_context WHERE workspace_id = ?",
                    (workspace_id,),
                ).fetchone()
            )
        if row is None:
            stamp = now_iso()
            return WorkingContextRow(
                workspace_id=workspace_id,
                objective=None,
                objective_set_at=None,
                objective_source=None,
                active_task_id=None,
                active_task_set_at=None,
                plan_checkpoint=None,
                pending_decision_ref=None,
                latest_evidence_ref=None,
                expected_next_step=None,
                revision=0,
                created_at=stamp,
                updated_at=stamp,
            )
        return _row_to_context(row)

    def set_objective(
        self, workspace_id: str, objective: Optional[str], *, source: str = SOURCE_USER
    ) -> WorkingContextRow:
        """Replace the current objective, keeping the previous one as history.

        The history append and the field write are one transaction. Two writes
        would allow a state where the new objective is current and the old one
        was never recorded, which is the one thing the history exists to prevent.
        """
        if source not in CONTEXT_SOURCES:
            raise ContextFieldInvalid("source", "unknown source")
        cleaned = bounded_context_text(objective, MAX_OBJECTIVE_CHARS, "objective")
        stamp = now_iso()
        with self._write() as connection:
            self._ensure_context(connection, workspace_id, stamp)
            previous = connection.execute(
                "SELECT objective, objective_set_at, objective_source"
                " FROM workspace_context WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            if previous is not None and previous["objective"]:
                if previous["objective"] != cleaned:
                    self._append_history(
                        connection,
                        workspace_id,
                        previous["objective"],
                        previous["objective_set_at"] or stamp,
                        previous["objective_source"] or SOURCE_USER,
                        stamp,
                    )
            connection.execute(
                """
                UPDATE workspace_context
                   SET objective = ?, objective_set_at = ?, objective_source = ?,
                       revision = revision + 1, updated_at = ?
                 WHERE workspace_id = ?
                """,
                (cleaned, stamp if cleaned else None, source if cleaned else None, stamp, workspace_id),
            )
        return self.context(workspace_id)

    @staticmethod
    def _append_history(
        connection: sqlite3.Connection,
        workspace_id: str,
        objective: str,
        set_at: str,
        source: str,
        ended_at: str,
    ) -> None:
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS top FROM objective_history WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        sequence = int(row["top"]) + 1
        connection.execute(
            """
            INSERT INTO objective_history
                (workspace_id, sequence, objective, set_at, source, ended_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (workspace_id, sequence, objective, set_at, source, ended_at),
        )
        # Prune inside the same transaction, so the bound is a property of the
        # table rather than of a maintenance job somebody has to remember to run.
        connection.execute(
            """
            DELETE FROM objective_history
             WHERE workspace_id = ?
               AND sequence <= ?
            """,
            (workspace_id, sequence - MAX_OBJECTIVE_HISTORY),
        )

    def objective_history(
        self, workspace_id: str, *, limit: int = MAX_OBJECTIVE_HISTORY
    ) -> List[ObjectiveHistoryEntry]:
        """Previous objectives, newest first, bounded."""
        capped = max(1, min(int(limit), MAX_OBJECTIVE_HISTORY))
        with self._read_if_exists() as connection:
            if connection is None:
                return []
            rows = connection.execute(
                """
                SELECT sequence, objective, set_at, source, ended_at
                  FROM objective_history
                 WHERE workspace_id = ?
                 ORDER BY sequence DESC
                 LIMIT ?
                """,
                (workspace_id, capped),
            ).fetchall()
        return [
            ObjectiveHistoryEntry(
                sequence=int(row["sequence"]),
                objective=row["objective"],
                set_at=row["set_at"],
                source=row["source"],
                ended_at=row["ended_at"],
            )
            for row in rows
        ]

    #: The Working Context fields a caller may set directly, with their bounds.
    #: ``objective`` is not here — it has its own method because it keeps
    #: history, and folding it in would make one of these writes quietly
    #: different from the others.
    SETTABLE_FIELDS = {
        "plan_checkpoint": MAX_REFERENCE_CHARS,
        "pending_decision_ref": MAX_REFERENCE_CHARS,
        "latest_evidence_ref": MAX_REFERENCE_CHARS,
        "expected_next_step": MAX_NEXT_STEP_CHARS,
    }

    def update_context(
        self,
        workspace_id: str,
        *,
        fields: dict,
        active_task_id: object = ...,
    ) -> WorkingContextRow:
        """Set or clear bounded context fields in one transaction.

        Only keys present in ``fields`` are touched — a partial update, so a
        client setting the next step cannot blank an objective it never sent.
        ``None`` clears; absence leaves alone. Those being different is the whole
        contract, and it is why the route rejects unknown keys rather than
        ignoring them.

        ``active_task_id`` is separate because it carries its own timestamp and
        because whether the task may be pointed at is a question for the service,
        which has the task store and the project registry to answer it.
        """
        unknown = sorted(set(fields) - set(self.SETTABLE_FIELDS))
        if unknown:
            raise ContextFieldInvalid(unknown[0], "not a settable working context field")

        cleaned = {
            name: bounded_context_text(value, self.SETTABLE_FIELDS[name], name)
            for name, value in fields.items()
        }
        stamp = now_iso()
        with self._write() as connection:
            self._ensure_context(connection, workspace_id, stamp)
            assignments = [name + " = ?" for name in cleaned]
            values: List[object] = list(cleaned.values())
            if active_task_id is not ...:
                assignments.append("active_task_id = ?")
                values.append(active_task_id)
                assignments.append("active_task_set_at = ?")
                values.append(stamp if active_task_id is not None else None)
            if not assignments:
                return self.context(workspace_id)
            assignments.append("revision = revision + 1")
            assignments.append("updated_at = ?")
            values.append(stamp)
            values.append(workspace_id)
            connection.execute(
                "UPDATE workspace_context SET " + ", ".join(assignments) + " WHERE workspace_id = ?",
                tuple(values),
            )
        return self.context(workspace_id)

    def clear_active_task_everywhere(self, task_id: str) -> int:
        """Forget a task reference wherever it is held. Returns rows changed.

        Not called on task completion — a completed task is still the answer to
        "what are we working on", and erasing it the moment it finished would
        delete the thing somebody came back to read. This exists for the one
        case where the reference is genuinely meaningless: a task that no longer
        exists at all.
        """
        stamp = now_iso()
        with self._write() as connection:
            cursor = connection.execute(
                """
                UPDATE workspace_context
                   SET active_task_id = NULL, active_task_set_at = NULL,
                       revision = revision + 1, updated_at = ?
                 WHERE active_task_id = ?
                """,
                (stamp, task_id),
            )
            return int(cursor.rowcount or 0)

    def health(self) -> dict:
        """Whether the store is openable, without saying where it lives.

        A host with no database yet is ``available`` and ``present: false``.
        Reporting that as unavailable would make an ordinary untouched host look
        broken, and health checks are read on every poll — this one must not be
        the thing that creates the file it is reporting on.
        """
        try:
            with self._read_if_exists() as connection:
                if connection is None:
                    return {"available": True, "present": False}
                connection.execute("SELECT 1").fetchone()
        except StoreUnavailable as unavailable:
            return {"available": False, "reason": unavailable.code}
        except sqlite3.Error:
            return {"available": False, "reason": "workspace_store_unavailable"}
        return {"available": True, "present": True, "schema_version": SCHEMA_VERSION}


__all__ = [
    "CONTEXT_SOURCES",
    "DATABASE_FILENAME",
    "MAX_NEXT_STEP_CHARS",
    "MAX_OBJECTIVE_CHARS",
    "MAX_OBJECTIVE_HISTORY",
    "MAX_REFERENCE_CHARS",
    "ObjectiveHistoryEntry",
    "SCHEMA_VERSION",
    "SOURCE_COFFERDAM",
    "SOURCE_PLANNER",
    "SOURCE_USER",
    "WORKSPACE_DIRNAME",
    "WorkingContextRow",
    "WorkspaceStore",
    "bounded_context_text",
]
