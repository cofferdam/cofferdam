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
PLANNER_SCHEMA_VERSION = 1

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


_READ_COLUMNS = (
    "planner_request_id, workspace_id, project_id, status, created_at, started_at, "
    "completed_at, user_intent, action, summary, confidence, worker_prompt, "
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

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(_SCHEMA)
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO schema_meta (key, value) VALUES "
                    "('schema_version', ?)",
                    (str(PLANNER_SCHEMA_VERSION),),
                )
            else:
                try:
                    found = int(row["value"])
                except (TypeError, ValueError):
                    raise PlannerStoreUnavailable(
                        "the planner database records an unreadable schema version"
                    )
                if found > PLANNER_SCHEMA_VERSION:
                    # Forward-only, for the same reason Task Core is: an older
                    # build writing rows a newer schema defined is how a
                    # rollback becomes data loss.
                    raise PlannerStoreUnavailable(
                        "the planner database was written by a newer version of "
                        "Cofferdam"
                    )
                if found < PLANNER_SCHEMA_VERSION:
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
    "PlannerRecord",
    "PlannerStore",
    "PlannerStoreUnavailable",
]
