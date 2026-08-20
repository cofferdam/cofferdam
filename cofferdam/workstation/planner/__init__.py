"""M2L — the development planner role.

Cloud Coworker Planning and Orchestration (D-2026-08-20-1). A planner reasons
over a bounded request and returns one typed decision. It is not a worker: it
has no lifecycle, no tools, no filesystem and no way to start anything.

Five modules:

:mod:`.models`
    The two closed contracts — :class:`DevelopmentRequest` in,
    :class:`PlannerResult` out — and the strict host-side validator.
:mod:`.protocol`
    The provider-neutral role, and the provider-reported execution metadata kept
    deliberately separate from model output.
:mod:`.contract`
    The code-owned instructions. Not the security boundary.
:mod:`.errors`
    Truthful failures. None of them degrades into an action.
:mod:`.providers`
    Where vendor names are allowed to exist.
:mod:`.store`
    ``planner.sqlite3``, its own database beside the others, keeping invocation
    lifecycle and semantic action in separate columns.
:mod:`.service`
    The application layer: project, ask, persist, hand back a record.
:mod:`.hashing`
    The domain-separated digest a human decision is bound to.
:mod:`.authority`
    What a *person* decided, kept as its own fact beside what the model said —
    gate kinds, gate states, provenance, and the one derivation from a persisted
    result to the decision it is waiting for.
:mod:`.authority_service`
    Recording those decisions. Built with the store alone, so it has no planner,
    no context and no provider to reach.

**Planner output is data, never execution** (D-2026-08-20-2). Text resembling a
command, a tool call or an instruction to another worker is inert text;
``PREPARE_WORKER_PROMPT`` means "store this for a worker a person will confirm".

**A human decision is never written over model output** (PR1d). An answer, an
approval and a rejection each become a separate authority record bound to the
exact bytes they authorized. What the planner said and what the person
authorized are two facts, and both are kept.
"""

from __future__ import annotations

from .authority import (
    ACCEPTED_AUTHORITY_SOURCES,
    AUTHORITY_ACTIONS,
    AUTHORITY_ANSWER,
    AUTHORITY_APPROVE,
    AUTHORITY_REJECT,
    AUTHORITY_SOURCES,
    GATE_ANSWER,
    GATE_CONFIRMATION,
    GATE_KINDS,
    GATE_NONE,
    GATE_STATE_ANSWERED,
    GATE_STATE_APPROVED,
    GATE_STATE_AWAITING_ANSWER,
    GATE_STATE_AWAITING_CONFIRMATION,
    GATE_STATE_NOT_REQUIRED,
    GATE_STATE_REJECTED,
    GATE_STATES,
    MAX_ANSWER_CHARS,
    MAX_REJECTION_REASON_CHARS,
    SOURCE_INTERNAL_TEST,
    SOURCE_LOCAL_CALL,
    SOURCE_WORKSTATION_PWA,
    AuthorityProvenance,
    HumanGate,
    derive_gate,
    new_authority_event_id,
)
from .authority_service import PlannerAuthorityService, PlannerGateView
from .contract import PLANNER_CONTRACT
from .errors import (
    PlannerAuthorityConflict,
    PlannerAuthorityError,
    PlannerAuthorityInvalid,
    PlannerAuthorityRefused,
    PlannerAuthorityStale,
    PlannerContextRefused,
    PlannerEnvelopeInvalid,
    PlannerError,
    PlannerInvocationFailed,
    PlannerResultInvalid,
    PlannerResultMissing,
    PlannerTimeout,
    PlannerUnavailable,
)
from .models import (
    ACTION_ASK_USER,
    ACTION_PREPARE_WORKER_PROMPT,
    ACTION_STOP,
    FORBIDDEN_RESULT_KEYS,
    PLANNER_ACTIONS,
    PLANNER_RESULT_SCHEMA,
    PLANNER_RESULT_SCHEMA_VERSION,
    DevelopmentRequest,
    PlannerResult,
    validate_planner_result,
)
from .protocol import (
    DevelopmentPlanner,
    PlannerCapabilities,
    PlanningTurn,
    ProviderExecution,
)
from .service import (
    ContextSource,
    PlannerService,
    PlanningOutcome,
    ProjectionSource,
    new_planner_request_id,
)
from .hashing import authority_subject_fingerprint, valid_fingerprint
from .store import (
    PLANNER_SCHEMA_VERSION,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    AuthorityEvent,
    PlannerRecord,
    PlannerStore,
)

__all__ = [
    "ACCEPTED_AUTHORITY_SOURCES",
    "ACTION_ASK_USER",
    "ACTION_PREPARE_WORKER_PROMPT",
    "ACTION_STOP",
    "AUTHORITY_ACTIONS",
    "AUTHORITY_ANSWER",
    "AUTHORITY_APPROVE",
    "AUTHORITY_REJECT",
    "AUTHORITY_SOURCES",
    "FORBIDDEN_RESULT_KEYS",
    "GATE_ANSWER",
    "GATE_CONFIRMATION",
    "GATE_KINDS",
    "GATE_NONE",
    "GATE_STATES",
    "GATE_STATE_ANSWERED",
    "GATE_STATE_APPROVED",
    "GATE_STATE_AWAITING_ANSWER",
    "GATE_STATE_AWAITING_CONFIRMATION",
    "GATE_STATE_NOT_REQUIRED",
    "GATE_STATE_REJECTED",
    "MAX_ANSWER_CHARS",
    "MAX_REJECTION_REASON_CHARS",
    "PLANNER_ACTIONS",
    "PLANNER_CONTRACT",
    "PLANNER_RESULT_SCHEMA",
    "PLANNER_SCHEMA_VERSION",
    "PLANNER_RESULT_SCHEMA_VERSION",
    "SOURCE_INTERNAL_TEST",
    "SOURCE_LOCAL_CALL",
    "SOURCE_WORKSTATION_PWA",
    "AuthorityEvent",
    "AuthorityProvenance",
    "DevelopmentPlanner",
    "DevelopmentRequest",
    "HumanGate",
    "PlannerAuthorityConflict",
    "PlannerAuthorityError",
    "PlannerAuthorityInvalid",
    "PlannerAuthorityRefused",
    "PlannerAuthorityService",
    "PlannerAuthorityStale",
    "PlannerCapabilities",
    "PlannerContextRefused",
    "PlannerEnvelopeInvalid",
    "PlannerError",
    "PlannerInvocationFailed",
    "PlannerResult",
    "PlannerResultInvalid",
    "PlannerResultMissing",
    "PlannerTimeout",
    "PlannerUnavailable",
    "ContextSource",
    "PlannerGateView",
    "PlannerRecord",
    "PlannerService",
    "PlannerStore",
    "PlanningOutcome",
    "ProjectionSource",
    "PlanningTurn",
    "STATUS_FAILED",
    "STATUS_INTERRUPTED",
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "STATUS_SUCCEEDED",
    "authority_subject_fingerprint",
    "derive_gate",
    "new_authority_event_id",
    "new_planner_request_id",
    "valid_fingerprint",
    "ProviderExecution",
    "validate_planner_result",
]
