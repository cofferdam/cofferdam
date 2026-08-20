"""Durable planner invocations. Its own database, beside the others.

``planner.sqlite3`` sits next to ``tasks.sqlite3``, ``workspace.sqlite3`` and
``mind.sqlite3``, and the reason is the same one that gave Mind its own file: the
component that owns a lifecycle owns its persistence. The planner is
deliberately **not** a ``TaskAdapter`` and owns no Task Core lifecycle, so
putting planner rows in ``tasks.sqlite3`` would create exactly the semantic
coupling PR1c-a's protocol decision exists to prevent — a schema shared with the
lifecycle graph is a short step from a planner that looks like it may move one.

Two things are kept apart in the schema, because collapsing them is the mistake
this table is shaped to make impossible:

**Lifecycle** is what the *invocation* did: ``pending``, ``running``,
``succeeded``, ``failed``. **Action** is what the *model decided*: ``ASK_USER``,
``PREPARE_WORKER_PROMPT``, ``STOP``. ``STOP`` is a successful invocation whose
result was a refusal to plan; a provider failure is not. Storing them in one
column would make those two indistinguishable a week later.

Crash truth: a request row is committed **before** the provider is invoked, so an
invocation that dies mid-flight is visible as ``running`` with no result rather
than as something that never happened. Nothing here reruns it.

A third thing is kept apart, in its own table (PR1d): **what a person decided.**
``planner_authority_events`` holds answers, approvals and rejections. It is not
columns on ``planner_requests``, and that is the whole point — a human decision
recorded on the model's own row would overwrite the evidence of what the model
produced, and *what was proposed* and *what was authorized* are two facts a
person needs both of. Nothing in this module ever updates a planner row from an
authority event.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import PlannerError
from .models import PlannerResult
from .protocol import ProviderExecution

DATABASE_FILENAME = "planner.sqlite3"

#: This database's own version, independent of Task Core's. Forward-only: a file
#: written by a newer build is refused rather than written to.
#:
#: v1 → v2 (PR1d) adds ``planner_authority_events`` and nothing else. No column
#: on ``planner_requests`` changed, no value was rewritten, and every v1 row
#: reads back identically — a v2 database holding no authority events is a v1
#: database with an empty table beside it.
#:
#: v2 → v3 (PR1e) adds ``planner_worker_dispatches``, on the same terms:
#: additive only, nothing existing altered. A v3 database with no dispatches is
#: a v2 database with an empty table beside it.
PLANNER_SCHEMA_VERSION = 3

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
#: A row found in ``running`` at startup. The process that owned it is gone, and
#: this host will not guess whether the provider finished — see
#: :meth:`PlannerStore.mark_interrupted`.
STATUS_INTERRUPTED = "interrupted"

PLANNER_STATUSES = (
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
)

TERMINAL_STATUSES = (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_INTERRUPTED)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS planner_requests (
    planner_request_id   TEXT PRIMARY KEY,
    workspace_id         TEXT,
    project_id           TEXT,
    status               TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    started_at           TEXT,
    completed_at         TEXT,

    -- request. The bounded packet the provider actually received is stored
    -- whole: a reference to mutable local sources could not prove, later, what
    -- the model was given, and a durable record that cannot answer that is not
    -- an audit record. It is the CloudContextProjection-derived payload and
    -- nothing else, so it carries only what was already eligible to leave.
    user_intent          TEXT NOT NULL,
    request_payload_json TEXT NOT NULL,
    projection_policy_id TEXT,
    projection_built_at  TEXT,

    -- result (model-authored, validated before it arrives here)
    result_schema_version INTEGER,
    action               TEXT,
    summary              TEXT,
    confidence           REAL,
    worker_prompt        TEXT,
    user_question        TEXT,
    decision_basis       TEXT,

    -- provenance (provider-authored, never mixed with the result above)
    provider_id          TEXT,
    requested_model      TEXT,
    actual_model         TEXT,
    models_used_json     TEXT,
    session_id           TEXT,
    duration_ms          INTEGER,
    ttft_ms              INTEGER,
    input_tokens         INTEGER,
    output_tokens        INTEGER,
    provider_reported_cost_estimate_usd REAL,

    -- failure
    failure_code         TEXT,
    failure_message      TEXT
);

CREATE INDEX IF NOT EXISTS planner_requests_created
    ON planner_requests (created_at);
CREATE INDEX IF NOT EXISTS planner_requests_status
    ON planner_requests (status);

-- What a *person* decided. A separate table, not columns above, because a
-- planner result is model-authored data and an approval is human authority, and
-- writing the second over the first destroys the only record of what was
-- actually proposed.
--
-- Append-only: nothing here is ever UPDATEd or DELETEd. The unique index on
-- `planner_request_id` is what makes a gate terminal — of two decisions racing
-- on the same request exactly one INSERT succeeds and the other is told what
-- already happened. That is the compare-and-set Mind's proposal transitions get
-- from a conditional UPDATE, expressed as a constraint instead, because here
-- there is no prior row to compare against.
--
-- `subject_fingerprint` binds the decision to the exact model output authorized.
-- A dispatcher recomputes it from what it is holding; if it differs, this record
-- does not authorize that.
--
-- The CHECK constraints are not belt-and-braces. `authority_action` has three
-- legal values and none of them is an execution word: there is no row shape in
-- this database that spells `dispatch`, and a future writer that tried would be
-- refused by SQLite rather than by a code path somebody could forget.
CREATE TABLE IF NOT EXISTS planner_authority_events (
    authority_event_id    TEXT PRIMARY KEY,
    planner_request_id    TEXT NOT NULL,
    gate_kind             TEXT NOT NULL,
    authority_action      TEXT NOT NULL,
    subject_fingerprint   TEXT NOT NULL,
    result_schema_version INTEGER NOT NULL,
    answer_text           TEXT,
    rejection_reason      TEXT,
    actor                 TEXT NOT NULL,
    source                TEXT NOT NULL,
    recorded_at           TEXT NOT NULL,

    FOREIGN KEY (planner_request_id)
        REFERENCES planner_requests (planner_request_id),

    CHECK (gate_kind IN ('answer', 'confirmation')),
    CHECK (authority_action IN ('answer', 'approve', 'reject')),
    CHECK (actor = 'user'),
    -- An answer carries text and the other two never do; a reason belongs only
    -- to a refusal. One artefact per authority action, at the storage layer.
    CHECK ((authority_action = 'answer') = (answer_text IS NOT NULL)),
    CHECK (rejection_reason IS NULL OR authority_action = 'reject')
);

CREATE UNIQUE INDEX IF NOT EXISTS planner_authority_one_per_request
    ON planner_authority_events (planner_request_id);

-- That an approved prompt was handed to a worker. A third fact, kept apart from
-- the first two for the reason the second was kept apart from the first:
-- approval is what a person authorized, dispatch is what Cofferdam then did with
-- it, and completion is what the worker made of it. Collapsing any pair loses
-- the ability to say which one failed.
--
-- The row is a *linkage*, not a copy of Task Core. There is no state column, no
-- started_at, no result: Task Core owns the execution lifecycle and duplicating
-- it here would create two answers to "is it running" that drift apart. What is
-- here is what Task Core cannot know — which approved planner subject this
-- execution is discharging, and the fingerprint that was verified when it began.
--
-- `subject_fingerprint` is stored again rather than joined, deliberately. A
-- dispatcher must be able to prove, from this row alone, that what it launched
-- was what a person approved; a value reachable only by following a foreign key
-- into another table is a value that a later schema change could quietly
-- reinterpret.
--
-- Unique on `planner_request_id`: one approved planner result yields at most one
-- logical dispatch. Fan-out, a second competing worker and automatic rerun are
-- all absent, and their absence is a constraint rather than a policy somebody
-- has to remember.
CREATE TABLE IF NOT EXISTS planner_worker_dispatches (
    dispatch_id           TEXT PRIMARY KEY,
    planner_request_id    TEXT NOT NULL,
    authority_event_id    TEXT NOT NULL,
    subject_fingerprint   TEXT NOT NULL,
    worker_prompt_sha256  TEXT NOT NULL,
    project_id            TEXT NOT NULL,
    workspace_id          TEXT,
    adapter_id            TEXT NOT NULL,
    task_id               TEXT NOT NULL,
    request_key           TEXT NOT NULL,
    branch                TEXT,
    actor                 TEXT NOT NULL,
    source                TEXT NOT NULL,
    created_at            TEXT NOT NULL,

    FOREIGN KEY (planner_request_id)
        REFERENCES planner_requests (planner_request_id),

    CHECK (actor = 'user')
);

CREATE UNIQUE INDEX IF NOT EXISTS planner_dispatch_one_per_request
    ON planner_worker_dispatches (planner_request_id);
CREATE UNIQUE INDEX IF NOT EXISTS planner_dispatch_by_task
    ON planner_worker_dispatches (task_id);
"""


class PlannerStoreUnavailable(PlannerError):
    reason_code = "planner_store_unavailable"


@dataclass(frozen=True)
class PlannerRecord:
    """The read model. Safe fields only — this is what a caller may see."""

    planner_request_id: str
    workspace_id: Optional[str]
    project_id: Optional[str]
    status: str
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    user_intent: str
    #: Which result contract the stored result speaks. Published because a
    #: reader deciding whether it may act on a result needs to know that before
    #: it reads one, and because the human authority gate refuses to bind a
    #: decision to a result whose contract this build does not speak.
    result_schema_version: Optional[int] = None
    action: Optional[str] = None
    summary: Optional[str] = None
    confidence: Optional[float] = None
    worker_prompt: Optional[str] = None
    user_question: Optional[str] = None
    decision_basis: Optional[str] = None
    provider_id: Optional[str] = None
    requested_model: Optional[str] = None
    actual_model: Optional[str] = None
    duration_ms: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    provider_reported_cost_estimate_usd: Optional[float] = None
    projection_policy_id: Optional[str] = None
    projection_built_at: Optional[str] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None

    @property
    def needs_user_input(self) -> bool:
        return self.action == "ASK_USER"

    @property
    def has_prepared_prompt(self) -> bool:
        return self.action == "PREPARE_WORKER_PROMPT" and bool(self.worker_prompt)

    def to_dict(self) -> Dict[str, Any]:
        """Deliberately not ``__dict__``: the field list is the allowlist.

        The request payload, which holds the whole projected context, is *not*
        here. It is durable for audit and reachable through
        :meth:`PlannerStore.request_payload`; a routine read of a planner result
        should not hand back the entire context packet.
        """
        return {
            "planner_request_id": self.planner_request_id,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "user_intent": self.user_intent,
            "result_schema_version": self.result_schema_version,
            "action": self.action,
            "summary": self.summary,
            "confidence": self.confidence,
            "worker_prompt": self.worker_prompt,
            "user_question": self.user_question,
            "decision_basis": self.decision_basis,
            "needs_user_input": self.needs_user_input,
            "has_prepared_prompt": self.has_prepared_prompt,
            "provider": {
                "provider_id": self.provider_id,
                "requested_model": self.requested_model,
                "actual_model": self.actual_model,
                "duration_ms": self.duration_ms,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "provider_reported_cost_estimate_usd": (
                    self.provider_reported_cost_estimate_usd
                ),
            },
            "context_provenance": {
                "projection_policy_id": self.projection_policy_id,
                "projection_built_at": self.projection_built_at,
            },
            "failure": (
                {"code": self.failure_code, "message": self.failure_message}
                if self.failure_code
                else None
            ),
        }


@dataclass(frozen=True)
class AuthorityEvent:
    """One durable human decision, exactly as the table holds it.

    A plain record, deliberately. What the decision *means* — which gate state it
    produces, which actions were permitted to reach it, whether it still binds
    what is persisted now — lives in :mod:`.authority`, so that the storage layer
    stays a storage layer and the semantics have one home rather than two that
    can drift.
    """

    authority_event_id: str
    planner_request_id: str
    gate_kind: str
    authority_action: str
    subject_fingerprint: str
    result_schema_version: int
    actor: str
    source: str
    recorded_at: str
    answer_text: Optional[str] = None
    rejection_reason: Optional[str] = None

    def to_dict(self, *, include_answer: bool = True) -> Dict[str, Any]:
        """The safe shape. ``subject_fingerprint`` is published on purpose.

        Unlike Mind's ``target_binding_hash`` — an opaque fingerprint of a *host
        location* a client is never told about — this digest covers model output
        the reader is already holding. Publishing it hands over nothing new and
        is what lets a later dispatcher prove the prompt it has is the prompt
        somebody approved.
        """
        payload: Dict[str, Any] = {
            "authority_event_id": self.authority_event_id,
            "planner_request_id": self.planner_request_id,
            "gate_kind": self.gate_kind,
            "authority_action": self.authority_action,
            "authorized_subject_fingerprint": self.subject_fingerprint,
            "result_schema_version": self.result_schema_version,
            "decided_at": self.recorded_at,
            "provenance": {"actor": self.actor, "source": self.source},
            "rejection_reason": self.rejection_reason,
        }
        if include_answer:
            payload["answer"] = self.answer_text
        return payload


@dataclass(frozen=True)
class WorkerDispatch:
    """One approved planner subject, handed to one worker execution.

    A linkage row and nothing more. What the worker is *doing* lives in Task
    Core, which owns the lifecycle; asking this record for a status would be
    asking the wrong table, and there is no status column here to ask.
    """

    dispatch_id: str
    planner_request_id: str
    authority_event_id: str
    subject_fingerprint: str
    worker_prompt_sha256: str
    project_id: str
    workspace_id: Optional[str]
    adapter_id: str
    task_id: str
    request_key: str
    actor: str
    source: str
    created_at: str
    branch: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dispatch_id": self.dispatch_id,
            "planner_request_id": self.planner_request_id,
            "authority_event_id": self.authority_event_id,
            "approved_subject_fingerprint": self.subject_fingerprint,
            "worker_prompt_sha256": self.worker_prompt_sha256,
            "project_id": self.project_id,
            "workspace_id": self.workspace_id,
            "adapter_id": self.adapter_id,
            "task_id": self.task_id,
            "branch": self.branch,
            "provenance": {"actor": self.actor, "source": self.source},
            "created_at": self.created_at,
        }


_DISPATCH_COLUMNS = (
    "dispatch_id, planner_request_id, authority_event_id, subject_fingerprint, "
    "worker_prompt_sha256, project_id, workspace_id, adapter_id, task_id, "
    "request_key, branch, actor, source, created_at"
)


_READ_COLUMNS = (
    "planner_request_id, workspace_id, project_id, status, created_at, started_at, "
    "completed_at, user_intent, result_schema_version, action, summary, "
    "confidence, worker_prompt, "
    "user_question, decision_basis, provider_id, requested_model, actual_model, "
    "duration_ms, input_tokens, output_tokens, "
    "provider_reported_cost_estimate_usd, projection_policy_id, "
    "projection_built_at, failure_code, failure_message"
)


class PlannerStore:
    """Small, synchronous, and honest about what it did not finish."""

    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._path = self._directory / DATABASE_FILENAME
        self._lock = threading.RLock()
        self._initialize()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        # A planner result that a power cut turns into a lie is worse than a
        # slow write, and this host has already taken one hard power loss.
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _stored_version(self, connection: sqlite3.Connection) -> Optional[int]:
        """The version on disk, or ``None`` for a database that has no schema yet."""
        present = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
        ).fetchone()
        if present is None:
            return None
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            return None
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            raise PlannerStoreUnavailable(
                "the planner database records an unreadable schema version"
            )

    def _initialize(self) -> None:
        """Open, refuse a future database, migrate forward, never backward.

        The version is read **before** any DDL runs. The earlier ordering created
        the tables first and then refused, which meant merely opening a database
        from a newer build left tables behind in it — a refusal that modified the
        thing it was refusing to touch.

        v1 → v2 is additive: ``planner_authority_events`` appears, no existing
        column or value is altered, and every v1 planner row reads back
        unchanged. So the migration *is* the ``CREATE TABLE IF NOT EXISTS``, and
        the version bump follows it. A crash between the two leaves a database
        with the new table and the old version number, which the next open fixes
        by doing exactly the same idempotent thing again.
        """
        with self._lock, self._connect() as connection:
            found = self._stored_version(connection)
            if found is not None and found > PLANNER_SCHEMA_VERSION:
                # Forward-only, for the same reason Task Core is: an older build
                # writing rows a newer schema defined is how a rollback becomes
                # data loss.
                raise PlannerStoreUnavailable(
                    "the planner database was written by a newer version of "
                    "Cofferdam"
                )

            connection.executescript(_SCHEMA)

            if found is None:
                connection.execute(
                    "INSERT OR REPLACE INTO schema_meta (key, value) VALUES "
                    "('schema_version', ?)",
                    (str(PLANNER_SCHEMA_VERSION),),
                )
            elif found < PLANNER_SCHEMA_VERSION:
                connection.execute(
                    "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                    (str(PLANNER_SCHEMA_VERSION),),
                )
        try:
            os.chmod(self._path, 0o600)
        except OSError:  # pragma: no cover - best effort on odd filesystems
            pass

    # -- writes ---------------------------------------------------------------
    def create_request(
        self,
        *,
        planner_request_id: str,
        workspace_id: Optional[str],
        project_id: Optional[str],
        user_intent: str,
        request_payload: Dict[str, Any],
        projection_policy_id: Optional[str],
        projection_built_at: Optional[str],
        created_at: str,
    ) -> None:
        """Commit the request **before** the provider is invoked.

        The ordering is the crash-truth property: a row exists, in ``pending``,
        from the moment Cofferdam decided to ask. A process that dies during the
        call leaves evidence that it was asked, which is the honest record.
        """
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO planner_requests (planner_request_id, workspace_id, "
                "project_id, status, created_at, user_intent, request_payload_json, "
                "projection_policy_id, projection_built_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    planner_request_id,
                    workspace_id,
                    project_id,
                    STATUS_PENDING,
                    created_at,
                    user_intent,
                    json.dumps(request_payload, ensure_ascii=False),
                    projection_policy_id,
                    projection_built_at,
                ),
            )

    def mark_running(self, planner_request_id: str, *, started_at: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE planner_requests SET status = ?, started_at = ? "
                "WHERE planner_request_id = ? AND status = ?",
                (STATUS_RUNNING, started_at, planner_request_id, STATUS_PENDING),
            )

    def record_success(
        self,
        planner_request_id: str,
        *,
        result: PlannerResult,
        execution: ProviderExecution,
        completed_at: str,
    ) -> None:
        """Result, provenance and completion in **one** statement.

        Not three updates: a crash between them could leave a row that says
        ``succeeded`` with no action, which is precisely the shape that would let
        Cofferdam later claim a planning result it never received.
        """
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE planner_requests SET status = ?, completed_at = ?, "
                "result_schema_version = ?, action = ?, summary = ?, confidence = ?, "
                "worker_prompt = ?, user_question = ?, decision_basis = ?, "
                "provider_id = ?, requested_model = ?, actual_model = ?, "
                "models_used_json = ?, session_id = ?, duration_ms = ?, ttft_ms = ?, "
                "input_tokens = ?, output_tokens = ?, "
                "provider_reported_cost_estimate_usd = ? "
                "WHERE planner_request_id = ?",
                (
                    STATUS_SUCCEEDED,
                    completed_at,
                    result.schema_version,
                    result.action,
                    result.summary,
                    result.confidence,
                    result.worker_prompt,
                    result.user_question,
                    result.decision_basis,
                    execution.provider_id,
                    execution.requested_model,
                    execution.actual_model,
                    json.dumps(list(execution.models_used)),
                    execution.session_id,
                    execution.duration_ms,
                    execution.ttft_ms,
                    execution.input_tokens,
                    execution.output_tokens,
                    execution.provider_reported_cost_estimate_usd,
                    planner_request_id,
                ),
            )

    def record_failure(
        self,
        planner_request_id: str,
        *,
        failure_code: str,
        failure_message: str,
        completed_at: str,
        execution: Optional[ProviderExecution] = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE planner_requests SET status = ?, completed_at = ?, "
                "failure_code = ?, failure_message = ?, provider_id = ?, "
                "requested_model = ?, duration_ms = ? "
                "WHERE planner_request_id = ?",
                (
                    STATUS_FAILED,
                    completed_at,
                    failure_code,
                    failure_message[:2000],
                    execution.provider_id if execution else None,
                    execution.requested_model if execution else None,
                    execution.duration_ms if execution else None,
                    planner_request_id,
                ),
            )

    def mark_interrupted(self, *, completed_at: str) -> int:
        """Called at startup. Says what is true, and reruns nothing.

        A row left ``pending`` or ``running`` belonged to a process that is gone.
        Whether the provider actually answered is unknowable from here, so the
        status says ``interrupted`` rather than ``failed`` — and rather than
        being quietly retried, which would be a second charge, a second
        invocation and a claim that the first never happened.
        """
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE planner_requests SET status = ?, completed_at = ?, "
                "failure_code = ?, failure_message = ? "
                "WHERE status IN (?, ?)",
                (
                    STATUS_INTERRUPTED,
                    completed_at,
                    "planner_interrupted",
                    "the process that owned this invocation exited before it completed",
                    STATUS_PENDING,
                    STATUS_RUNNING,
                ),
            )
            return cursor.rowcount or 0

    # -- reads ----------------------------------------------------------------
    def get(self, planner_request_id: str) -> Optional[PlannerRecord]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                f"SELECT {_READ_COLUMNS} FROM planner_requests "
                "WHERE planner_request_id = ?",
                (planner_request_id,),
            ).fetchone()
        return PlannerRecord(**dict(row)) if row else None

    def recent(self, limit: int = 50) -> tuple:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT {_READ_COLUMNS} FROM planner_requests "
                "ORDER BY created_at DESC LIMIT ?",
                (min(int(limit), 200),),
            ).fetchall()
        return tuple(PlannerRecord(**dict(row)) for row in rows)

    def request_payload(self, planner_request_id: str) -> Optional[Dict[str, Any]]:
        """The exact bounded packet the provider received. Audit path, not routine.

        Kept off :meth:`PlannerRecord.to_dict` on purpose: it is the whole
        projected context, it is durable so that "what did the model see" has a
        real answer, and a caller that wants it should have to ask.
        """
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT request_payload_json FROM planner_requests "
                "WHERE planner_request_id = ?",
                (planner_request_id,),
            ).fetchone()
        return json.loads(row["request_payload_json"]) if row else None

    def schema_version(self) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
        return int(row["value"])

    # -- human authority ------------------------------------------------------
    #
    # Two methods, one INSERT and one SELECT. There is deliberately no update and
    # no delete: an authority row is a durable statement about what a person did,
    # and neither correcting it in place nor removing it is something this build
    # is allowed to do quietly.

    def record_authority_event(
        self,
        *,
        authority_event_id: str,
        planner_request_id: str,
        gate_kind: str,
        authority_action: str,
        subject_fingerprint: str,
        result_schema_version: int,
        actor: str,
        source: str,
        recorded_at: str,
        answer_text: Optional[str] = None,
        rejection_reason: Optional[str] = None,
        expected_action: Optional[str] = None,
    ) -> Optional[AuthorityEvent]:
        """Write one decision, or report the decision that was already there.

        Returns the stored row on success and ``None`` when a terminal decision
        already existed — the caller reads it back and decides whether that is an
        idempotent retry or a contradiction.

        **The exclusivity is SQLite's, not a preceding read.** Two approvals
        arriving together both reach the ``INSERT``; the unique index on
        ``planner_request_id`` lets exactly one through and gives the other an
        ``IntegrityError``. A check-then-write would have a window between the
        check and the write, and a double tap is precisely the traffic that finds
        it.

        ``expected_action`` is re-verified **inside** the transaction against the
        planner row, so a decision cannot be recorded against a result whose
        action is not the one the caller validated against. In this build a
        succeeded planner row is already terminal and cannot move, which makes
        this belt-and-braces today and the thing that stays correct the day
        something else can write there.
        """
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status, action FROM planner_requests "
                    "WHERE planner_request_id = ?",
                    (planner_request_id,),
                ).fetchone()
                if row is None:
                    raise PlannerStoreUnavailable(
                        "no planner request to record a decision against"
                    )
                if expected_action is not None and row["action"] != expected_action:
                    raise PlannerStoreUnavailable(
                        "the planner result changed while the decision was in flight"
                    )
                connection.execute(
                    "INSERT INTO planner_authority_events ("
                    "authority_event_id, planner_request_id, gate_kind, "
                    "authority_action, subject_fingerprint, result_schema_version, "
                    "answer_text, rejection_reason, actor, source, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        authority_event_id,
                        planner_request_id,
                        gate_kind,
                        authority_action,
                        subject_fingerprint,
                        int(result_schema_version),
                        answer_text,
                        rejection_reason,
                        actor,
                        source,
                        recorded_at,
                    ),
                )
            except sqlite3.IntegrityError:
                # Either the unique index (a decision already exists) or a CHECK
                # (a row shape this database does not have). Both roll back
                # whole; the caller distinguishes them by reading back.
                connection.execute("ROLLBACK")
                if self._authority_row(connection, planner_request_id) is not None:
                    return None
                raise
            except Exception:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            stored = self._authority_row(connection, planner_request_id)
        return stored

    @staticmethod
    def _authority_row(
        connection: sqlite3.Connection, planner_request_id: str
    ) -> Optional["AuthorityEvent"]:
        row = connection.execute(
            "SELECT authority_event_id, planner_request_id, gate_kind, "
            "authority_action, subject_fingerprint, result_schema_version, "
            "answer_text, rejection_reason, actor, source, recorded_at "
            "FROM planner_authority_events WHERE planner_request_id = ?",
            (planner_request_id,),
        ).fetchone()
        return AuthorityEvent(**dict(row)) if row else None

    def authority_event(self, planner_request_id: str) -> Optional["AuthorityEvent"]:
        """The terminal decision on this request, if a person has made one."""
        with self._lock, self._connect() as connection:
            return self._authority_row(connection, planner_request_id)

    # -- worker dispatch ------------------------------------------------------

    def record_dispatch(self, dispatch: "WorkerDispatch") -> Optional["WorkerDispatch"]:
        """Link one approved subject to one worker execution, or report the link.

        Returns the stored row, or ``None`` when a dispatch already existed —
        the same shape :meth:`record_authority_event` uses, and for the same
        reason: the caller reads back and decides whether that is an idempotent
        retry or a conflict, rather than this method guessing.
        """
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO planner_worker_dispatches (" + _DISPATCH_COLUMNS + ") "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        dispatch.dispatch_id,
                        dispatch.planner_request_id,
                        dispatch.authority_event_id,
                        dispatch.subject_fingerprint,
                        dispatch.worker_prompt_sha256,
                        dispatch.project_id,
                        dispatch.workspace_id,
                        dispatch.adapter_id,
                        dispatch.task_id,
                        dispatch.request_key,
                        dispatch.branch,
                        dispatch.actor,
                        dispatch.source,
                        dispatch.created_at,
                    ),
                )
            except sqlite3.IntegrityError:
                connection.execute("ROLLBACK")
                if self._dispatch_row(connection, dispatch.planner_request_id) is not None:
                    return None
                raise
            except Exception:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            return self._dispatch_row(connection, dispatch.planner_request_id)

    @staticmethod
    def _dispatch_row(
        connection: sqlite3.Connection, planner_request_id: str
    ) -> Optional["WorkerDispatch"]:
        row = connection.execute(
            "SELECT " + _DISPATCH_COLUMNS + " FROM planner_worker_dispatches "
            "WHERE planner_request_id = ?",
            (planner_request_id,),
        ).fetchone()
        return WorkerDispatch(**dict(row)) if row else None

    def dispatch(self, planner_request_id: str) -> Optional["WorkerDispatch"]:
        """The dispatch for one planner request, if one was ever made."""
        with self._lock, self._connect() as connection:
            return self._dispatch_row(connection, planner_request_id)

    def dispatch_by_task(self, task_id: str) -> Optional["WorkerDispatch"]:
        """The dispatch that produced one task. The reverse link, for recovery."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT " + _DISPATCH_COLUMNS + " FROM planner_worker_dispatches "
                "WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return WorkerDispatch(**dict(row)) if row else None

    def recent_dispatches(self, limit: int = 50) -> tuple:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT " + _DISPATCH_COLUMNS + " FROM planner_worker_dispatches "
                "ORDER BY created_at DESC LIMIT ?",
                (min(int(limit), 200),),
            ).fetchall()
        return tuple(WorkerDispatch(**dict(row)) for row in rows)


__all__ = [
    "DATABASE_FILENAME",
    "PLANNER_SCHEMA_VERSION",
    "PLANNER_STATUSES",
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "STATUS_SUCCEEDED",
    "STATUS_FAILED",
    "STATUS_INTERRUPTED",
    "TERMINAL_STATUSES",
    "AuthorityEvent",
    "PlannerRecord",
    "WorkerDispatch",
    "PlannerStore",
    "PlannerStoreUnavailable",
]
