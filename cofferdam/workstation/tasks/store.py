"""Durable task state: SQLite, transactional, append-only where it matters.

Why a database, when every other store in this service is a JSON file
---------------------------------------------------------------------

``store.py`` for actions says it plainly: a capped list of recent records is
honestly served by an atomically-replaced JSON file, and "if task/update records
outgrow it, they get SQLite". This is that moment, and the reasons are specific
rather than a preference for databases:

* **A state change and its event must land together or not at all.** A snapshot
  that says ``completed`` with no completion event, or an event with no snapshot
  change, is a history that disagrees with itself. Rewriting a whole JSON file
  cannot express "these two facts are one write"; a transaction can, and every
  state change in this module goes through :meth:`TaskStore.transition`.
* **Event sequence numbers must be monotonic under concurrency.** Two adapter
  callbacks arriving together must not both become sequence 7. The sequence is
  allocated inside the same transaction as the insert.
* **Restart must find non-terminal tasks cheaply**, by state, without loading
  every task that ever ran.
* **Idempotency records need lookup by key**, not a scan.

SQLite is in the standard library, which keeps the stdlib-only CI path intact —
Task Core adds no dependency.

What is *not* in here
---------------------

No secrets, ever. No tokens, no credentials, no adapter authentication state.
The database holds task content — prompts, follow-ups, results — which is
private but is not a credential, and it is stored under ``$COFFERDAM_HOME`` with
owner-only permissions on both the file and its directory.

Journaling and durability
-------------------------

WAL, ``synchronous=FULL``, and ``foreign_keys=ON``. WAL because a reader (the
list view polling) must not block a writer (an adapter reporting progress).
FULL rather than NORMAL because the failure this milestone cares most about is
losing the *last* write before a crash — that write is usually the one that says
a task stopped, and losing it is exactly the false "still running" the restart
semantics exist to prevent.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from ..runtime.identity import now_iso
from .clarifications import (
    ANSWER_SOURCES,
    CLARIFICATION_STATUSES,
    MAX_ANSWER_CHARS,
    MAX_PENDING_PER_TASK,
    OUTCOME_ACCEPTED,
    STATUS_PENDING,
    AnswerProvenance,
    ClarificationAnswer,
    PendingClarification,
)
from .delegated import ANSWER_MODES, ANSWER_MODE_UNKNOWN, ClarificationOption
from .errors import IdempotencyConflict, StoreUnavailable, TaskUnknown
from .identity import new_correlation_id, new_task_id
from .lifecycle import IllegalTransition, check_transition
from .models import (
    EVENT_MEANINGFUL_OUTPUT,
    EVENT_PROGRESS,
    EVENT_TASK_CREATED,
    MAX_ACTIVITY_CHARS,
    MAX_EVENT_PAGE,
    MAX_EVIDENCE_IDENTIFIER_CHARS,
    MAX_EVIDENCE_ITEMS,
    MAX_FAILURE_CHARS,
    MAX_OUTPUT_CHARS,
    MAX_RESULT_CHARS,
    MAX_TASK_PAGE,
    MEANINGFUL_EVENT_TYPES,
    SOURCE_COFFERDAM,
    STATE_COMPLETED,
    STATE_CREATED,
    TERMINAL_STATES,
    EvidenceReference,
    TaskEvent,
    TaskFailure,
    bounded_line,
    bounded_text,
)
from .turns import FOLLOWUP_SOURCES, MAX_TURNS_PER_TASK, TURN_OUTCOMES, TaskTurn

#: Bumped whenever the schema below changes shape. A database written by a newer
#: build than the one reading it is refused rather than migrated backwards: a
#: rollback that silently dropped columns would lose task history.
#:
#: Version 2 adds ``task_clarifications``. Version 3 adds ``task_turns``. Both
#: changes are **additive only** — no column of an existing table moved, changed
#: type or gained a constraint — which is what makes each upgrade a
#: ``CREATE TABLE IF NOT EXISTS`` and the downgrade survivable: an older build
#: opening a newer database sees every table it knows about, exactly as it left
#: them, and simply never looks at the new one. The version is still refused in
#: that direction, because "survivable" is not "correct" and a build that cannot
#: see pending questions, or cannot see that a task has produced three turns,
#: should not be quietly answering or continuing it.
#:
#: Upgrading from 2 to 3 needs no data migration and writes no rows. A task that
#: predates ``task_turns`` simply has none, and every reader here treats "no
#: turns" as the ordinary answer for a task from an older build rather than as a
#: missing record — see :meth:`TaskStore.latest_completed_turn`.
SCHEMA_VERSION = 3

#: Where the database lives, under COFFERDAM_HOME. Its own directory so the file
#: and its WAL/shm siblings stay together and can be permissioned as a unit.
TASKS_DIRNAME = "tasks"
DATABASE_FILENAME = "tasks.sqlite3"

#: How long a writer waits for the lock before giving up. Long enough for a slow
#: local disk, short enough that a stuck writer surfaces as an error instead of
#: an API request that never answers.
BUSY_TIMEOUT_MS = 5000

#: Idempotency records older than this are pruned. A retry that arrives a day
#: later is not a retry; it is a new intention that happens to reuse a string.
IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id            TEXT PRIMARY KEY,
    correlation_id     TEXT NOT NULL,
    parent_task_id     TEXT,
    origin             TEXT NOT NULL,
    adapter_id         TEXT NOT NULL,
    project_id         TEXT NOT NULL,
    state              TEXT NOT NULL,
    waiting_reason     TEXT,
    lifecycle_revision INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    started_at         TEXT,
    updated_at         TEXT NOT NULL,
    completed_at       TEXT,
    title              TEXT,
    prompt             TEXT NOT NULL,
    latest_activity    TEXT,
    latest_output      TEXT,
    final_result       TEXT,
    failure_json       TEXT,
    cancellation_json  TEXT,
    event_cursor       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS tasks_by_state   ON tasks (state, created_at DESC);
CREATE INDEX IF NOT EXISTS tasks_by_created ON tasks (created_at DESC);

CREATE TABLE IF NOT EXISTS task_events (
    task_id            TEXT NOT NULL,
    sequence           INTEGER NOT NULL,
    event_type         TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    actor              TEXT NOT NULL,
    source             TEXT NOT NULL,
    lifecycle_revision INTEGER NOT NULL,
    correlation_id     TEXT,
    state              TEXT,
    text               TEXT,
    detail             TEXT,
    evidence_json      TEXT,
    PRIMARY KEY (task_id, sequence),
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS idempotency (
    scope        TEXT NOT NULL,
    request_key  TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    task_id      TEXT NOT NULL,
    result_json  TEXT,
    created_at   TEXT NOT NULL,
    created_ts   REAL NOT NULL,
    PRIMARY KEY (scope, request_key)
);

CREATE INDEX IF NOT EXISTS idempotency_by_age ON idempotency (created_ts);

-- One question a delegated session asked, and the answer if one was given.
--
-- Its own table rather than columns on `tasks`, for three reasons that are all
-- about honesty rather than tidiness. A task can be asked more than one thing
-- over its life and the earlier questions are history worth keeping. A question
-- has its own status that is not the task's — `superseded` is a thing that
-- happens to a question, not to a task. And a row here is written inside the
-- same transaction as the state change it causes, which is only expressible if
-- it is a row.
--
-- There is deliberately **no tool approval table**, and there is not going to be
-- one. A tool approval is decided on a trusted surface at the workstation; the
-- way to keep that true under later refactoring is to give the
-- remotely-answerable path no row to write into.
CREATE TABLE IF NOT EXISTS task_clarifications (
    task_id             TEXT NOT NULL,
    question_id         TEXT NOT NULL,
    provider            TEXT NOT NULL,
    provider_session_id TEXT,
    provider_event_id   TEXT,
    provider_sequence   INTEGER NOT NULL DEFAULT 0,
    question            TEXT NOT NULL,
    answer_mode         TEXT NOT NULL,
    options_json        TEXT,
    schema_verified     INTEGER NOT NULL DEFAULT 0,
    requested_at        TEXT NOT NULL,
    status              TEXT NOT NULL,
    answered_at         TEXT,
    answer_json         TEXT,
    PRIMARY KEY (task_id, question_id),
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS clarifications_by_status
    ON task_clarifications (task_id, status, provider_sequence);

-- Duplicate suppression, enforced by the database rather than by a check the
-- caller has to remember. A provider that re-sends the same question event
-- cannot open a second pending question for it. SQLite permits many NULLs in a
-- unique index, so a question that arrived without a provider event id simply
-- has no suppression — the safe direction, since the alternative would be
-- inventing an id and suppressing something that was not a duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS clarifications_by_provider_event
    ON task_clarifications (task_id, provider_event_id);

-- One provider turn: a user message in, a terminal outcome out.
--
-- Its own table for the reason `task_clarifications` has one, and then for a
-- reason of its own. A task can have several turns and the earlier ones are
-- evidence worth keeping — but more than that, `tasks.final_result` is written
-- with COALESCE, so a second turn's result *replaces* the first one's. That is
-- right for the single-turn tasks Task Core was built for and destroys history
-- for a conversation. Here, a completed turn is never written again: the
-- update is guarded on `completed_at IS NULL`.
--
-- `turn_number` is Cofferdam's, allocated as MAX+1 inside the same transaction
-- that inserts the row, so two concurrent follow-ups cannot both read 1 and
-- both write 2. The primary key is the backstop: if that allocation were ever
-- wrong the second insert raises rather than silently overwriting a turn.
--
-- There is deliberately no transcript column, no message list and no payload
-- column. What a turn keeps is what a person could be shown and an auditor
-- could check.
CREATE TABLE IF NOT EXISTS task_turns (
    task_id                TEXT    NOT NULL,
    turn_number            INTEGER NOT NULL,
    provider               TEXT    NOT NULL,
    provider_session_id    TEXT,
    provider_turn_sequence INTEGER NOT NULL DEFAULT 0,
    source                 TEXT    NOT NULL,
    followup_request_id    TEXT,
    started_at             TEXT    NOT NULL,
    completed_at           TEXT,
    outcome                TEXT,
    result                 TEXT,
    failure_code           TEXT,
    failure_summary        TEXT,
    PRIMARY KEY (task_id, turn_number),
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS turns_by_completion
    ON task_turns (task_id, completed_at, turn_number);

-- Duplicate suppression for follow-ups, enforced by the database rather than by
-- a check a caller has to remember. A retry that reaches the adapter twice
-- cannot open a second turn for one follow-up. SQLite permits many NULLs in a
-- unique index, so the first turn — opened by the prompt, with no request id —
-- is unaffected, and so is a follow-up sent without one.
CREATE UNIQUE INDEX IF NOT EXISTS turns_by_followup_request
    ON task_turns (task_id, followup_request_id);
"""


@dataclass(frozen=True)
class TaskRow:
    """One durable task, as stored. Not the published shape.

    Holds ``prompt``, which :class:`~.models.TaskSnapshot` deliberately does not:
    the prompt is needed to run a task and is shown in the task detail view, but
    it is not part of the snapshot every list response carries.
    """

    task_id: str
    correlation_id: str
    parent_task_id: Optional[str]
    origin: str
    adapter_id: str
    project_id: str
    state: str
    waiting_reason: Optional[str]
    lifecycle_revision: int
    created_at: str
    started_at: Optional[str]
    updated_at: str
    completed_at: Optional[str]
    title: Optional[str]
    prompt: str
    latest_activity: Optional[str]
    latest_output: Optional[str]
    final_result: Optional[str]
    failure: Optional[TaskFailure]
    cancellation: Optional[Dict[str, Any]]
    event_cursor: int


def _row_to_task(row: sqlite3.Row) -> TaskRow:
    failure_json = row["failure_json"]
    cancellation_json = row["cancellation_json"]
    failure = None
    if failure_json:
        try:
            data = json.loads(failure_json)
            failure = TaskFailure(
                code=str(data.get("code") or "task_failed"),
                message=str(data.get("message") or "the task failed"),
                detail=data.get("detail"),
            )
        except ValueError:  # pragma: no cover - written by this module only
            failure = None
    cancellation = None
    if cancellation_json:
        try:
            parsed = json.loads(cancellation_json)
            cancellation = parsed if isinstance(parsed, dict) else None
        except ValueError:  # pragma: no cover
            cancellation = None
    return TaskRow(
        task_id=row["task_id"],
        correlation_id=row["correlation_id"],
        parent_task_id=row["parent_task_id"],
        origin=row["origin"],
        adapter_id=row["adapter_id"],
        project_id=row["project_id"],
        state=row["state"],
        waiting_reason=row["waiting_reason"],
        lifecycle_revision=row["lifecycle_revision"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        title=row["title"],
        prompt=row["prompt"],
        latest_activity=row["latest_activity"],
        latest_output=row["latest_output"],
        final_result=row["final_result"],
        failure=failure,
        cancellation=cancellation,
        event_cursor=row["event_cursor"],
    )


#: The only event types a repeat of which is suppressed.
#:
#: Observations, and nothing else. ``progress`` and ``meaningful_output`` are
#: an adapter saying "here is how things are", and asking twice and getting the
#: same answer is not news. Every other type is a *lifecycle claim* — created,
#: started, cancelled, failed, interrupted — and each one is a distinct fact
#: that happened at a moment. Those are never suppressed, however alike two of
#: them look, because losing one would put a hole in the history.
REPEATABLE_EVENT_TYPES = frozenset({EVENT_PROGRESS, EVENT_MEANINGFUL_OUTPUT})


def _is_repeatable(event_type: str) -> bool:
    return event_type in REPEATABLE_EVENT_TYPES


def _bounded_evidence(evidence: Sequence[EvidenceReference]) -> Optional[str]:
    """Evidence, capped and normalized field by field.

    Never ``json.dumps`` of whatever an adapter handed over. An adapter that
    returned a large nested object would otherwise put it in the database and
    then in every event response — the unbounded-payload problem the models
    module exists to prevent.
    """
    if not evidence:
        return None
    items: List[Dict[str, Any]] = []
    for reference in list(evidence)[:MAX_EVIDENCE_ITEMS]:
        items.append(
            {
                "evidence_type": reference.evidence_type,
                "source": reference.source,
                "identifier": bounded_line(
                    reference.identifier, MAX_EVIDENCE_IDENTIFIER_CHARS
                ),
                "operation": bounded_line(reference.operation, 60),
                "result": bounded_line(reference.result, 60),
                "observed_at": bounded_line(reference.observed_at, 40),
            }
        )
    return json.dumps(items, ensure_ascii=False)


def _evidence_from_json(raw: Optional[str]) -> Tuple[EvidenceReference, ...]:
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except ValueError:  # pragma: no cover
        return ()
    if not isinstance(parsed, list):  # pragma: no cover
        return ()
    return tuple(
        EvidenceReference(
            evidence_type=str(item.get("evidence_type") or "artifact"),
            source=str(item.get("source") or "adapter_reported"),
            identifier=item.get("identifier"),
            operation=item.get("operation"),
            result=item.get("result"),
            observed_at=item.get("observed_at"),
        )
        for item in parsed
        if isinstance(item, dict)
    )


def _clarification_options_json(
    options: Sequence[ClarificationOption],
) -> Optional[str]:
    """Options, capped and normalized field by field.

    Never ``json.dumps`` of whatever an adapter handed over — the same rule
    :func:`_bounded_evidence` follows, for the same reason: an adapter that
    returned a large nested object would otherwise put it in the database and
    then in every clarification response.
    """
    if not options:
        return None
    return json.dumps(
        [
            {
                "option_id": option.option_id,
                "label": bounded_line(option.label, 120),
                "value": bounded_line(option.value, 120),
                "description": bounded_line(option.description, 240),
            }
            for option in list(options)[:8]
        ],
        ensure_ascii=False,
    )


def _clarification_options_from_json(raw: Optional[str]) -> Tuple[ClarificationOption, ...]:
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except ValueError:  # pragma: no cover - written by this module only
        return ()
    if not isinstance(parsed, list):  # pragma: no cover
        return ()
    return tuple(
        ClarificationOption(
            label=str(item.get("label") or ""),
            value=str(item.get("value") or item.get("label") or ""),
            option_id=item.get("option_id"),
            description=item.get("description"),
        )
        for item in parsed
        if isinstance(item, dict) and item.get("label")
    )


def _answer_json(answer: Optional[ClarificationAnswer]) -> Optional[str]:
    """One answer, bounded field by field. Never a whole request body.

    The provenance is written as three closed-vocabulary words and a timestamp.
    There is no branch here that could store a header, a token, an address or a
    caller-supplied name, because there is no field on the dataclass holding one.
    """
    if answer is None:
        return None
    provenance = answer.provenance
    return json.dumps(
        {
            "option_ids": list(answer.option_ids[:8]),
            "text": bounded_text(answer.text, MAX_ANSWER_CHARS),
            "provenance": {
                "actor": provenance.actor if provenance else None,
                "source": provenance.source if provenance else None,
                "received_at": provenance.received_at if provenance else None,
                "outcome": provenance.outcome if provenance else None,
                "rejection_reason": provenance.rejection_reason if provenance else None,
            },
        },
        ensure_ascii=False,
    )


def _answer_from_json(raw: Optional[str]) -> Optional[ClarificationAnswer]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:  # pragma: no cover
        return None
    if not isinstance(parsed, dict):  # pragma: no cover
        return None
    record = parsed.get("provenance")
    provenance = None
    if isinstance(record, dict) and record.get("source") in ANSWER_SOURCES:
        provenance = AnswerProvenance(
            actor=str(record.get("actor") or "user"),
            source=str(record.get("source")),
            received_at=str(record.get("received_at") or ""),
            outcome=str(record.get("outcome") or OUTCOME_ACCEPTED),
            rejection_reason=record.get("rejection_reason"),
        )
    ids = parsed.get("option_ids")
    return ClarificationAnswer(
        option_ids=tuple(str(item) for item in ids) if isinstance(ids, list) else (),
        text=parsed.get("text"),
        provenance=provenance,
    )


def _row_to_clarification(row: sqlite3.Row) -> PendingClarification:
    mode = row["answer_mode"]
    return PendingClarification(
        question_id=row["question_id"],
        task_id=row["task_id"],
        provider=row["provider"],
        question=row["question"],
        answer_mode=mode if mode in ANSWER_MODES else ANSWER_MODE_UNKNOWN,
        options=_clarification_options_from_json(row["options_json"]),
        provider_session_id=row["provider_session_id"],
        provider_event_id=row["provider_event_id"],
        provider_sequence=int(row["provider_sequence"] or 0),
        schema_verified=bool(row["schema_verified"]),
        requested_at=row["requested_at"],
        status=row["status"],
        answered_at=row["answered_at"],
        answer=_answer_from_json(row["answer_json"]),
    )


@dataclass(frozen=True)
class _TurnDraft:
    """What :meth:`TaskStore.transition` needs to open a turn.

    Deliberately not a :class:`~.turns.TaskTurn`: that class carries a
    ``turn_number``, and the number is the store's to allocate inside the
    transaction. A caller that could supply one could supply a wrong one.
    """

    provider: str
    source: str
    started_at: str
    provider_session_id: Optional[str] = None
    followup_request_id: Optional[str] = None


@dataclass(frozen=True)
class _TurnClose:
    """What :meth:`TaskStore.transition` needs to finish the open turn.

    Also not a :class:`~.turns.TaskTurn`, and for the mirror-image reason: which
    turn is being closed is not a caller's choice either. It is whichever one is
    open, found in the same statement that writes it.
    """

    outcome: str
    completed_at: str
    provider_session_id: Optional[str] = None
    provider_turn_sequence: int = 0
    result: Optional[str] = None
    failure_code: Optional[str] = None
    failure_summary: Optional[str] = None


def _row_to_turn(row: sqlite3.Row) -> TaskTurn:
    outcome = row["outcome"]
    return TaskTurn(
        task_id=row["task_id"],
        turn_number=int(row["turn_number"]),
        provider=row["provider"],
        source=row["source"],
        started_at=row["started_at"],
        provider_session_id=row["provider_session_id"],
        provider_turn_sequence=int(row["provider_turn_sequence"] or 0),
        followup_request_id=row["followup_request_id"],
        completed_at=row["completed_at"],
        # Read back through the vocabulary rather than trusted. A value written
        # by a newer build, or corrupted, becomes `None` — "this turn ended and
        # nobody here can say how" — rather than a word a caller might branch on.
        outcome=outcome if outcome in TURN_OUTCOMES else None,
        result=row["result"],
        failure_code=row["failure_code"],
        failure_summary=row["failure_summary"],
    )


class TaskStore:
    """The durable home of every task, and the only thing that changes a state.

    One connection, guarded by one lock. That is not a performance compromise
    to apologise for: this is a single-user workstation service, the write rate
    is a handful of events per second at most, and one connection makes "was
    this state change transactional with its event" answerable by reading a
    single method rather than by reasoning about a pool.
    """

    def __init__(self, config, *, path: Optional[Path] = None) -> None:
        self._config = config
        self._path = Path(path) if path is not None else self._default_path(config)
        self._lock = threading.RLock()
        self._connection: Optional[sqlite3.Connection] = None

    @staticmethod
    def _default_path(config) -> Path:
        return Path(config.state_dir) / TASKS_DIRNAME / DATABASE_FILENAME

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
            # database is never briefly world-readable on a fresh install.
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
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=" + str(BUSY_TIMEOUT_MS))
        connection.executescript(_SCHEMA)
        self._apply_schema_version(connection)
        self._restrict_files()
        self._connection = connection
        return connection

    def _restrict_files(self) -> None:
        """Owner-only on the database **and its WAL/shm siblings**.

        The siblings matter and are easy to miss: SQLite creates them with the
        process umask, which on an ordinary Ubuntu account means ``0644`` — and
        the write-ahead log holds recently written task content, which is
        somebody's prompts and results.

        The directory is ``0700``, so nothing else can traverse into them and
        this is defence in depth rather than the boundary. It is still worth
        doing: a directory permission is easy to lose when a file is copied,
        moved, or restored from a backup, and a mode on the file itself travels
        with it.
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
            raise StoreUnavailable("the task database records an unreadable schema version")
        if found > SCHEMA_VERSION:
            # Forward-only. A newer database opened by an older build is a
            # rollback, and the safe answer is to refuse rather than to write
            # rows the newer schema will not understand.
            raise StoreUnavailable(
                "the task database was written by a newer version of Cofferdam"
            )
        if found < SCHEMA_VERSION:
            # The upgrade already happened: the schema script above runs
            # ``CREATE TABLE IF NOT EXISTS`` on every start, so an older database
            # gained the new tables a moment ago. All this does is record that it
            # did, so the next start does not think it is still on the old
            # version. Nothing is altered, dropped or rewritten — an additive
            # schema is the only kind this line is correct for, and
            # :data:`SCHEMA_VERSION` says so.
            connection.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )

    def _tighten_new_siblings(self) -> None:
        """Re-apply the file modes after a write that may have created a sibling.

        The WAL and shm files appear on the first write rather than at connect
        time, so restricting once at open is not enough on a fresh database.
        Cheap — three ``chmod`` calls on already-open paths — and it runs where
        it can see the files exist.
        """
        self._restrict_files()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One transaction, committed on success and rolled back on anything else.

        ``IMMEDIATE`` so the write lock is taken at BEGIN rather than at the
        first write: with a deferred transaction, two writers can both start,
        both read, and one can then fail at commit — which for a state machine
        means the losing transition has already been decided against stale state.
        """
        with self._lock:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            self._tighten_new_siblings()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._connect()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    # -- creating ------------------------------------------------------------

    def create_task(
        self,
        *,
        origin: str,
        adapter_id: str,
        project_id: str,
        prompt: str,
        title: Optional[str] = None,
        parent_task_id: Optional[str] = None,
        idempotency_scope: Optional[str] = None,
        request_key: Optional[str] = None,
        payload_hash: Optional[str] = None,
    ) -> Tuple[TaskRow, bool]:
        """Insert one task and its ``task_created`` event, atomically.

        Returns ``(task, created)``. ``created`` is ``False`` when an
        idempotency key matched an earlier identical request, in which case the
        *existing* task is returned and nothing was written — which is what makes
        a double tap produce one task rather than two.
        """
        with self._write() as connection:
            if request_key is not None:
                existing = self._lookup_idempotent(
                    connection, idempotency_scope or "task_create", request_key, payload_hash
                )
                if existing is not None:
                    row = connection.execute(
                        "SELECT * FROM tasks WHERE task_id = ?", (existing,)
                    ).fetchone()
                    if row is not None:
                        return _row_to_task(row), False
                    # The key survived its task, which can only happen if a
                    # database was edited by hand. Treat the key as spent rather
                    # than resurrecting a task that no longer exists.
                    connection.execute(
                        "DELETE FROM idempotency WHERE scope = ? AND request_key = ?",
                        (idempotency_scope or "task_create", request_key),
                    )

            task_id = new_task_id()
            correlation_id = new_correlation_id()
            timestamp = now_iso()
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, correlation_id, parent_task_id, origin, adapter_id,
                    project_id, state, waiting_reason, lifecycle_revision,
                    created_at, started_at, updated_at, completed_at, title,
                    prompt, latest_activity, latest_output, final_result,
                    failure_json, cancellation_json, event_cursor
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, NULL, ?, NULL, ?, ?,
                          NULL, NULL, NULL, NULL, NULL, 0)
                """,
                (
                    task_id,
                    correlation_id,
                    parent_task_id,
                    origin,
                    adapter_id,
                    project_id,
                    STATE_CREATED,
                    timestamp,
                    timestamp,
                    title,
                    prompt,
                ),
            )
            self._append_event_locked(
                connection,
                task_id=task_id,
                event_type=EVENT_TASK_CREATED,
                actor="user",
                source=SOURCE_COFFERDAM,
                lifecycle_revision=0,
                correlation_id=correlation_id,
                state=STATE_CREATED,
                # No prompt here, and none anywhere in the event stream's text
                # fields for creation: the prompt is stored once, on the task,
                # and shown once, in the detail view.
                text=None,
            )
            if request_key is not None:
                self._record_idempotent(
                    connection,
                    idempotency_scope or "task_create",
                    request_key,
                    payload_hash or "",
                    task_id,
                )
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            return _row_to_task(row), True

    # -- idempotency ---------------------------------------------------------

    def _lookup_idempotent(
        self,
        connection: sqlite3.Connection,
        scope: str,
        request_key: str,
        payload_hash: Optional[str],
    ) -> Optional[str]:
        self._prune_idempotency(connection)
        row = connection.execute(
            "SELECT payload_hash, task_id FROM idempotency WHERE scope = ? AND request_key = ?",
            (scope, request_key),
        ).fetchone()
        if row is None:
            return None
        if payload_hash is not None and row["payload_hash"] != payload_hash:
            # Same key, different request. Neither answer is safe to guess at.
            raise IdempotencyConflict()
        return row["task_id"]

    def _record_idempotent(
        self,
        connection: sqlite3.Connection,
        scope: str,
        request_key: str,
        payload_hash: str,
        task_id: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        connection.execute(
            """
            INSERT OR REPLACE INTO idempotency
                (scope, request_key, payload_hash, task_id, result_json, created_at, created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope,
                request_key,
                payload_hash,
                task_id,
                json.dumps(result, ensure_ascii=False) if result else None,
                now_iso(),
                _monotonic_wall(),
            ),
        )

    def _prune_idempotency(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "DELETE FROM idempotency WHERE created_ts < ?",
            (_monotonic_wall() - IDEMPOTENCY_TTL_SECONDS,),
        )

    def remember_followup(
        self, task_id: str, scope: str, request_key: str, payload_hash: str
    ) -> Optional[str]:
        """Record a follow-up idempotency key, or report the earlier match.

        Returns ``None`` when this is the first time the key has been seen, and
        the task id it belongs to when it is a repeat. Raises
        :class:`~.errors.IdempotencyConflict` for the same key with different
        content — the case where guessing would either lose a message or send it
        twice.
        """
        with self._write() as connection:
            existing = self._lookup_idempotent(connection, scope, request_key, payload_hash)
            if existing is not None:
                return existing
            self._record_idempotent(connection, scope, request_key, payload_hash, task_id)
            return None

    # -- events --------------------------------------------------------------

    def _append_event_locked(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        event_type: str,
        actor: str,
        source: str,
        lifecycle_revision: int,
        correlation_id: Optional[str] = None,
        state: Optional[str] = None,
        text: Optional[str] = None,
        detail: Optional[str] = None,
        evidence: Sequence[EvidenceReference] = (),
    ) -> int:
        """Append one event and return its sequence. Caller holds the transaction.

        The sequence is allocated from the task row inside the same transaction
        as the insert, so two concurrent appends cannot both read 6 and both
        write 7.
        """
        row = connection.execute(
            "SELECT event_cursor FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise TaskUnknown()

        if _is_repeatable(event_type) and self._repeats_last_locked(
            connection,
            task_id=task_id,
            event_type=event_type,
            state=state,
            text=text,
            detail=detail,
            evidence=evidence,
        ):
            # Identical to the event immediately before it, so it says nothing
            # new and is not written. Returning the existing cursor keeps every
            # caller's contract: a sequence number that is real, and a history
            # that grew by nothing because nothing happened.
            #
            # This lives in the store rather than in an adapter because it has
            # to be **transactional and restart-safe**. An adapter that
            # remembered its last report in memory would forget across a restart
            # and start duplicating again; the comparison here reads the row
            # that is actually there, inside the transaction that would have
            # written the duplicate.
            return int(row["event_cursor"])

        sequence = int(row["event_cursor"]) + 1
        connection.execute(
            """
            INSERT INTO task_events (
                task_id, sequence, event_type, created_at, actor, source,
                lifecycle_revision, correlation_id, state, text, detail, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                sequence,
                event_type,
                now_iso(),
                actor,
                source,
                lifecycle_revision,
                correlation_id,
                state,
                bounded_text(text, MAX_OUTPUT_CHARS),
                bounded_line(detail, MAX_FAILURE_CHARS),
                _bounded_evidence(evidence),
            ),
        )
        connection.execute(
            "UPDATE tasks SET event_cursor = ?, updated_at = ? WHERE task_id = ?",
            (sequence, now_iso(), task_id),
        )
        return sequence

    def _repeats_last_locked(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        event_type: str,
        state: Optional[str],
        text: Optional[str],
        detail: Optional[str],
        evidence: Sequence[EvidenceReference],
    ) -> bool:
        """Whether this event is byte-for-byte the previous one for this task.

        Compared on what a reader would see — type, state, text, detail and
        evidence — and deliberately **not** on the timestamp, because the
        timestamp is the one field that always differs and is exactly what made
        a repeated observation look like news.
        """
        previous = connection.execute(
            "SELECT event_type, state, text, detail, evidence_json FROM task_events"
            " WHERE task_id = ? ORDER BY sequence DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if previous is None:
            return False
        return (
            previous["event_type"] == event_type
            and previous["state"] == state
            and previous["text"] == bounded_text(text, MAX_OUTPUT_CHARS)
            and previous["detail"] == bounded_line(detail, MAX_FAILURE_CHARS)
            and previous["evidence_json"] == _bounded_evidence(evidence)
        )

    def append_event(
        self,
        task_id: str,
        event_type: str,
        *,
        actor: str,
        source: str,
        text: Optional[str] = None,
        detail: Optional[str] = None,
        evidence: Sequence[EvidenceReference] = (),
    ) -> int:
        """Append an event that changes no state — progress, output, a rejection."""
        with self._write() as connection:
            row = connection.execute(
                "SELECT state, lifecycle_revision, correlation_id FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskUnknown()
            sequence = self._append_event_locked(
                connection,
                task_id=task_id,
                event_type=event_type,
                actor=actor,
                source=source,
                lifecycle_revision=row["lifecycle_revision"],
                correlation_id=row["correlation_id"],
                state=row["state"],
                text=text,
                detail=detail,
                evidence=evidence,
            )
            self._refresh_activity_locked(connection, task_id, event_type, text)
            return sequence

    def _refresh_activity_locked(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        event_type: str,
        text: Optional[str],
    ) -> None:
        """Keep the two "latest" columns current.

        They are denormalized on purpose: the list view would otherwise need a
        correlated subquery over the event table for every row, which is a lot of
        work to render one line of text a phone reads at a glance.
        """
        activity = bounded_line(text, MAX_ACTIVITY_CHARS)
        if activity is None:
            return
        if event_type in MEANINGFUL_EVENT_TYPES:
            connection.execute(
                "UPDATE tasks SET latest_activity = ?, latest_output = ? WHERE task_id = ?",
                (activity, bounded_text(text, MAX_OUTPUT_CHARS), task_id),
            )
        else:
            connection.execute(
                "UPDATE tasks SET latest_activity = ? WHERE task_id = ?",
                (activity, task_id),
            )

    # -- transitions ---------------------------------------------------------

    def transition(
        self,
        task_id: str,
        new_state: str,
        *,
        event_type: str,
        actor: str,
        source: str,
        expected_state: Optional[str] = None,
        waiting_reason: Optional[str] = None,
        text: Optional[str] = None,
        detail: Optional[str] = None,
        final_result: Optional[str] = None,
        failure: Optional[TaskFailure] = None,
        cancellation: Optional[Dict[str, Any]] = None,
        evidence: Sequence[EvidenceReference] = (),
        open_clarification: Optional[PendingClarification] = None,
        close_clarifications: Sequence[PendingClarification] = (),
        open_turn: Optional["_TurnDraft"] = None,
        close_turn: Optional["_TurnClose"] = None,
    ) -> "TaskRow":
        """Move a task to ``new_state`` and record the event. One transaction.

        This is the only method in the codebase that writes ``tasks.state``, and
        it never writes it without appending an event in the same transaction —
        which is what makes "the snapshot and the history cannot disagree" a
        property of the schema rather than a convention callers follow.

        ``expected_state`` gives a caller optimistic concurrency: a cancel that
        was decided against ``running`` will not apply to a task that has since
        completed.

        ``open_clarification`` and ``close_clarifications`` extend that guarantee
        to questions, and they are parameters of *this* method rather than
        separate calls for exactly one reason: a task that says
        ``waiting_for_user`` with no pending question, or a pending question on a
        task that says ``cancelled``, is a disagreement between two rows that a
        person would have to resolve by guessing. Passing them here makes the
        state change and the question one write or neither.

        ``open_turn`` and ``close_turn`` are here for the same reason and it is
        the sharper case. A turn that completed while the task did not move, or
        a task reported ``ready_for_followup`` with no turn recorded as having
        produced anything, is a result somebody can be shown that the history
        cannot account for. The state change, the event, and the turn's outcome
        are one write.
        """
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise TaskUnknown()
            current = row["state"]
            if expected_state is not None and current != expected_state:
                raise IllegalTransition(
                    current,
                    new_state,
                    "the task changed to " + current + " while this was being decided",
                )
            check_transition(current, new_state)

            revision = int(row["lifecycle_revision"]) + 1
            timestamp = now_iso()
            started_at = row["started_at"]
            if new_state == "running" and started_at is None:
                started_at = timestamp
            completed_at = row["completed_at"]
            if new_state in TERMINAL_STATES and completed_at is None:
                completed_at = timestamp

            connection.execute(
                """
                UPDATE tasks
                   SET state = ?, waiting_reason = ?, lifecycle_revision = ?,
                       started_at = ?, completed_at = ?, updated_at = ?,
                       final_result = COALESCE(?, final_result),
                       failure_json = COALESCE(?, failure_json),
                       cancellation_json = COALESCE(?, cancellation_json)
                 WHERE task_id = ?
                """,
                (
                    new_state,
                    waiting_reason,
                    revision,
                    started_at,
                    completed_at,
                    timestamp,
                    bounded_text(final_result, MAX_RESULT_CHARS),
                    json.dumps(failure.to_dict(), ensure_ascii=False) if failure else None,
                    json.dumps(cancellation, ensure_ascii=False) if cancellation else None,
                    task_id,
                ),
            )
            self._append_event_locked(
                connection,
                task_id=task_id,
                event_type=event_type,
                actor=actor,
                source=source,
                lifecycle_revision=revision,
                correlation_id=row["correlation_id"],
                state=new_state,
                text=text,
                detail=detail,
                evidence=evidence,
            )
            for closing in close_clarifications:
                self._save_clarification_locked(connection, closing)
            if open_clarification is not None:
                self._save_clarification_locked(connection, open_clarification)
            # Closed before opened, and the order is not arbitrary: a follow-up
            # that ends one turn and begins the next does both in this one
            # transaction, and closing first means the MAX+1 allocation below
            # counts a turn that is already finished rather than racing it.
            if close_turn is not None:
                self._close_turn_locked(connection, task_id, close_turn)
            if open_turn is not None:
                self._open_turn_locked(connection, task_id, open_turn)
            self._refresh_activity_locked(connection, task_id, event_type, text)
            updated = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            return _row_to_task(updated)

    # -- clarifications ------------------------------------------------------

    def _save_clarification_locked(
        self, connection: sqlite3.Connection, clarification: PendingClarification
    ) -> None:
        """Insert or update one question. The caller holds the transaction.

        ``INSERT OR REPLACE`` on the natural key, because both callers want the
        same thing: the row for this task and this question id, as it is now. The
        *rules* about which transitions are legal — a pending question cannot be
        answered twice, a closed one cannot reopen — live in
        :mod:`.clarifications` and are applied before a value reaches here, which
        keeps this method a write rather than a second policy.
        """
        if clarification.status not in CLARIFICATION_STATUSES:  # pragma: no cover
            raise StoreUnavailable("unknown clarification status")
        connection.execute(
            """
            INSERT OR REPLACE INTO task_clarifications (
                task_id, question_id, provider, provider_session_id,
                provider_event_id, provider_sequence, question, answer_mode,
                options_json, schema_verified, requested_at, status,
                answered_at, answer_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clarification.task_id,
                clarification.question_id,
                clarification.provider,
                clarification.provider_session_id,
                clarification.provider_event_id,
                int(clarification.provider_sequence),
                bounded_text(clarification.question, MAX_OUTPUT_CHARS),
                clarification.answer_mode,
                _clarification_options_json(clarification.options),
                1 if clarification.schema_verified else 0,
                clarification.requested_at,
                clarification.status,
                clarification.answered_at,
                _answer_json(clarification.answer),
            ),
        )

    def save_clarification(self, clarification: PendingClarification) -> None:
        """Write one question outside a state change.

        Used where a question changes but the task does not — a supersession, an
        answer that is being recorded before the adapter has said what happens
        next. State changes carry their questions with them through
        :meth:`transition`; this is for the rest.
        """
        with self._write() as connection:
            row = connection.execute(
                "SELECT task_id FROM tasks WHERE task_id = ?",
                (clarification.task_id,),
            ).fetchone()
            if row is None:
                raise TaskUnknown()
            self._save_clarification_locked(connection, clarification)

    def clarifications(
        self, task_id: str, *, status: Optional[str] = None
    ) -> List[PendingClarification]:
        """Every question asked of one task, oldest first, optionally filtered."""
        with self._read() as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM task_clarifications WHERE task_id = ?"
                    " ORDER BY provider_sequence ASC, requested_at ASC",
                    (task_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM task_clarifications WHERE task_id = ? AND status = ?"
                    " ORDER BY provider_sequence ASC, requested_at ASC",
                    (task_id, status),
                ).fetchall()
            return [_row_to_clarification(row) for row in rows]

    def pending_clarifications(self, task_id: str) -> List[PendingClarification]:
        return self.clarifications(task_id, status=STATUS_PENDING)

    def find_clarification(
        self, task_id: str, question_id: object
    ) -> Optional[PendingClarification]:
        """One question of one task, or ``None``.

        Scoped to the task in the query itself rather than fetched and then
        checked. That is what makes "an answer cannot target another task"
        structural: a question id from a different task does not match a row
        here, so there is nothing to compare and nothing to get wrong.
        """
        if not isinstance(question_id, str):
            return None
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM task_clarifications WHERE task_id = ? AND question_id = ?",
                (task_id, question_id),
            ).fetchone()
            return _row_to_clarification(row) if row is not None else None

    def clarification_for_provider_event(
        self, task_id: str, provider_event_id: object
    ) -> Optional[PendingClarification]:
        """The question a provider event already produced, if it produced one.

        The read half of duplicate suppression. The write half is the unique
        index, which is what actually stops a second row; this exists so the
        caller can recognise the repeat and say nothing new happened rather than
        catching a constraint error and guessing what it meant.
        """
        if not isinstance(provider_event_id, str) or not provider_event_id:
            return None
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM task_clarifications"
                " WHERE task_id = ? AND provider_event_id = ?",
                (task_id, provider_event_id),
            ).fetchone()
            return _row_to_clarification(row) if row is not None else None

    def pending_clarification_count(self, task_id: str) -> int:
        with self._read() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM task_clarifications"
                " WHERE task_id = ? AND status = ?",
                (task_id, STATUS_PENDING),
            ).fetchone()
            return int(row["total"]) if row is not None else 0

    def clarification_room(self, task_id: str) -> bool:
        """Whether one more question may be recorded for this task."""
        return self.pending_clarification_count(task_id) < MAX_PENDING_PER_TASK

    # -- turns ---------------------------------------------------------------

    def _open_turn_locked(
        self, connection: sqlite3.Connection, task_id: str, draft: "_TurnDraft"
    ) -> None:
        """Allocate a turn number and insert the row. Caller holds the transaction.

        ``MAX(turn_number) + 1``, read and written inside the one transaction
        that also moves the task — so two follow-ups arriving together cannot
        both read 1 and both write 2. The primary key is the backstop rather
        than the mechanism: if the allocation were ever wrong the insert raises,
        which is a loud failure instead of a turn quietly overwriting another.

        A plain ``INSERT``, never ``INSERT OR REPLACE``. That choice is the
        "a later turn cannot overwrite an earlier one" rule, written where it
        is enforced.
        """
        existing = connection.execute(
            "SELECT COALESCE(MAX(turn_number), 0) AS highest,"
            " COUNT(*) AS total FROM task_turns WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        highest = int(existing["highest"] or 0)
        total = int(existing["total"] or 0)
        if total >= MAX_TURNS_PER_TASK:
            raise TurnLimitReached()
        if draft.followup_request_id:
            # Read half of the follow-up duplicate suppression; the unique index
            # is the write half. Checked here so a retry is recognised as "this
            # turn already exists" rather than surfacing as a constraint error
            # somebody has to interpret.
            already = connection.execute(
                "SELECT turn_number FROM task_turns"
                " WHERE task_id = ? AND followup_request_id = ?",
                (task_id, draft.followup_request_id),
            ).fetchone()
            if already is not None:
                return
        connection.execute(
            """
            INSERT INTO task_turns
                (task_id, turn_number, provider, provider_session_id,
                 provider_turn_sequence, source, followup_request_id,
                 started_at, completed_at, outcome, result,
                 failure_code, failure_summary)
            VALUES (?, ?, ?, ?, 0, ?, ?, ?, NULL, NULL, NULL, NULL, NULL)
            """,
            (
                task_id,
                highest + 1,
                draft.provider,
                draft.provider_session_id,
                draft.source,
                draft.followup_request_id,
                draft.started_at,
            ),
        )

    def _close_turn_locked(
        self, connection: sqlite3.Connection, task_id: str, closing: "_TurnClose"
    ) -> None:
        """Finish the task's open turn, if it has one. Caller holds the transaction.

        ``WHERE completed_at IS NULL`` is the whole method. A turn that already
        finished is not written again — not by a duplicate provider event, not
        by a late result arriving after a cancellation, and not by a second
        settle of the same log. The update simply matches no rows, and the
        earlier outcome stands.

        ``provider_session_id`` is filled in only when the row does not have one
        yet, for the reason :func:`~.turns.close_turn` gives: an id learned late
        is worth recording, and an id that *changed* means the stream is no
        longer this turn's session — a mismatch to report, never to adopt.
        """
        connection.execute(
            """
            UPDATE task_turns
               SET completed_at = ?,
                   outcome = ?,
                   result = ?,
                   failure_code = ?,
                   failure_summary = ?,
                   provider_turn_sequence = ?,
                   provider_session_id = COALESCE(provider_session_id, ?)
             WHERE task_id = ?
               AND completed_at IS NULL
               AND turn_number = (
                   SELECT MAX(turn_number) FROM task_turns
                    WHERE task_id = ? AND completed_at IS NULL
               )
            """,
            (
                closing.completed_at,
                closing.outcome,
                bounded_text(closing.result, MAX_RESULT_CHARS),
                closing.failure_code,
                bounded_text(closing.failure_summary, MAX_FAILURE_CHARS),
                max(0, int(closing.provider_turn_sequence or 0)),
                closing.provider_session_id,
                task_id,
                task_id,
            ),
        )

    def open_turn(
        self,
        task_id: str,
        *,
        provider: str,
        source: str,
        started_at: str,
        provider_session_id: Optional[str] = None,
        followup_request_id: Optional[str] = None,
    ) -> "TaskTurn":
        """Open a turn on its own, outside a state change.

        Used for the **first** turn, which begins when an adapter starts and is
        not a transition anybody makes: ``_start`` moves the task through
        ``queued`` and ``starting`` for reasons that have nothing to do with
        turns. Every later turn is opened inside the transition that a follow-up
        causes, where it belongs.
        """
        if source not in FOLLOWUP_SOURCES:  # pragma: no cover - callers pass a constant
            raise StoreUnavailable("unknown turn source")
        with self._write() as connection:
            if connection.execute(
                "SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone() is None:
                raise TaskUnknown()
            self._open_turn_locked(
                connection,
                task_id,
                _TurnDraft(
                    provider=provider,
                    source=source,
                    started_at=started_at,
                    provider_session_id=provider_session_id,
                    followup_request_id=followup_request_id,
                ),
            )
        current = self.current_turn(task_id)
        if current is None:  # pragma: no cover - the insert just succeeded
            raise StoreUnavailable("the turn could not be opened")
        return current

    def turns(self, task_id: str) -> List["TaskTurn"]:
        """Every turn this task has had, oldest first. Bounded by the row limit."""
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM task_turns WHERE task_id = ? ORDER BY turn_number ASC"
                " LIMIT ?",
                (task_id, MAX_TURNS_PER_TASK),
            ).fetchall()
        return [_row_to_turn(row) for row in rows]

    def turn_count(self, task_id: str) -> int:
        with self._read() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM task_turns WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return int(row["total"]) if row is not None else 0

    def current_turn(self, task_id: str) -> Optional["TaskTurn"]:
        """The turn that is open, if one is. At most one ever is."""
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM task_turns WHERE task_id = ? AND completed_at IS NULL"
                " ORDER BY turn_number DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return _row_to_turn(row) if row is not None else None

    def latest_completed_turn(self, task_id: str) -> Optional["TaskTurn"]:
        """The most recent turn that **succeeded**, or ``None``.

        Successful specifically, not merely finished, and the difference is the
        one case where it shows: a task whose first turn answered and whose
        second was cancelled has two finished turns, and the answer somebody
        should be able to read is the first one. A query for "the last turn
        that ended" would return the cancelled one and report no result for a
        task that plainly produced one.

        How a task *ended* is not lost by this — that is the task row's own
        state, and the published result carries it separately.

        ``None`` is an ordinary answer and means one of three unremarkable
        things: the task is still on its first turn, it ended before producing
        anything, or it predates schema version 3. None of them is a missing
        record, and the caller treats all three the same way.
        """
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM task_turns WHERE task_id = ?"
                " AND completed_at IS NOT NULL AND outcome = ?"
                " ORDER BY turn_number DESC LIMIT 1",
                (task_id, STATE_COMPLETED),
            ).fetchone()
        return _row_to_turn(row) if row is not None else None

    def turn_for_followup(
        self, task_id: str, followup_request_id: object
    ) -> Optional["TaskTurn"]:
        """The turn one follow-up request id already opened, if it opened one."""
        if not isinstance(followup_request_id, str) or not followup_request_id:
            return None
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM task_turns WHERE task_id = ? AND followup_request_id = ?",
                (task_id, followup_request_id),
            ).fetchone()
        return _row_to_turn(row) if row is not None else None

    # -- reading -------------------------------------------------------------

    def get(self, task_id: object) -> TaskRow:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise TaskUnknown()
            return _row_to_task(row)

    def find(self, task_id: object) -> Optional[TaskRow]:
        try:
            return self.get(task_id)
        except TaskUnknown:
            return None

    def list_tasks(
        self, *, states: Optional[Sequence[str]] = None, limit: int = MAX_TASK_PAGE
    ) -> List[TaskRow]:
        """Newest first, bounded. There is no "all"."""
        bounded = max(1, min(int(limit), MAX_TASK_PAGE))
        with self._read() as connection:
            if states:
                placeholders = ",".join("?" for _ in states)
                rows = connection.execute(
                    "SELECT * FROM tasks WHERE state IN (" + placeholders + ")"
                    " ORDER BY created_at DESC, task_id DESC LIMIT ?",
                    (*states, bounded),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC, task_id DESC LIMIT ?",
                    (bounded,),
                ).fetchall()
            return [_row_to_task(row) for row in rows]

    def events(
        self, task_id: str, *, after: int = 0, limit: int = MAX_EVENT_PAGE
    ) -> List[TaskEvent]:
        """Events after a cursor, bounded.

        ``after`` is a sequence number, so the query is an index range scan
        rather than an offset. That is the difference between a phone asking
        "what is new" cheaply and asking the database to count past ten thousand
        rows to find out.
        """
        bounded = max(1, min(int(limit), MAX_EVENT_PAGE))
        cursor = max(0, int(after))
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM task_events WHERE task_id = ? AND sequence > ?"
                " ORDER BY sequence ASC LIMIT ?",
                (task_id, cursor, bounded),
            ).fetchall()
            return [
                TaskEvent(
                    task_id=row["task_id"],
                    sequence=row["sequence"],
                    event_type=row["event_type"],
                    created_at=row["created_at"],
                    actor=row["actor"],
                    source=row["source"],
                    lifecycle_revision=row["lifecycle_revision"],
                    correlation_id=row["correlation_id"],
                    state=row["state"],
                    text=row["text"],
                    detail=row["detail"],
                    evidence=_evidence_from_json(row["evidence_json"]),
                )
                for row in rows
            ]

    def non_terminal_tasks(self) -> List[TaskRow]:
        """Every task the database believes is unfinished.

        Used by restart recovery, and its whole point is that the answer is
        *not* trusted: a row saying ``running`` after a restart is a row about a
        process that no longer exists.
        """
        with self._read() as connection:
            placeholders = ",".join("?" for _ in TERMINAL_STATES)
            rows = connection.execute(
                "SELECT * FROM tasks WHERE state NOT IN (" + placeholders + ")"
                " ORDER BY created_at ASC",
                tuple(sorted(TERMINAL_STATES)),
            ).fetchall()
            return [_row_to_task(row) for row in rows]

    def counts_by_state(self) -> Dict[str, int]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS total FROM tasks GROUP BY state"
            ).fetchall()
            return {row["state"]: int(row["total"]) for row in rows}

    def storage_health(self) -> Dict[str, Any]:
        """Non-content facts about the store, for the status surface.

        Deliberately without the path: the database location is host detail, and
        publishing it would put a filesystem path in an authenticated response
        for no operational gain a count does not already give.
        """
        counts = self.counts_by_state()
        return {
            "schema_version": SCHEMA_VERSION,
            "task_count": sum(counts.values()),
            "counts_by_state": counts,
        }


def _monotonic_wall() -> float:
    """Wall-clock seconds, for TTL arithmetic only.

    Not ``time.monotonic``: these values are persisted, and a monotonic clock
    means nothing across a restart.
    """
    import time

    return time.time()


def database_permissions(path: Path) -> Optional[int]:
    """The database file's mode bits, or ``None`` if it does not exist yet.

    Exposed for the tests that assert the store is not world-readable, so that
    assertion does not have to reach into private state.
    """
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None


__all__ = [
    "BUSY_TIMEOUT_MS",
    "DATABASE_FILENAME",
    "IDEMPOTENCY_TTL_SECONDS",
    "SCHEMA_VERSION",
    "TASKS_DIRNAME",
    "TaskRow",
    "TaskStore",
    "database_permissions",
]
