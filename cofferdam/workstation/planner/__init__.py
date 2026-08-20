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

**Planner output is data, never execution** (D-2026-08-20-2). Text resembling a
command, a tool call or an instruction to another worker is inert text;
``PREPARE_WORKER_PROMPT`` means "store this for a worker a person will confirm".
"""

from __future__ import annotations

from .contract import PLANNER_CONTRACT
from .errors import (
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
from .store import (
    PLANNER_SCHEMA_VERSION,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    PlannerRecord,
    PlannerStore,
)

__all__ = [
    "ACTION_ASK_USER",
    "ACTION_PREPARE_WORKER_PROMPT",
    "ACTION_STOP",
    "FORBIDDEN_RESULT_KEYS",
    "PLANNER_ACTIONS",
    "PLANNER_CONTRACT",
    "PLANNER_RESULT_SCHEMA",
    "PLANNER_SCHEMA_VERSION",
    "PLANNER_RESULT_SCHEMA_VERSION",
    "DevelopmentPlanner",
    "DevelopmentRequest",
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
    "new_planner_request_id",
    "ProviderExecution",
    "validate_planner_result",
]
