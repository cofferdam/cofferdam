"""The planner application layer: project, ask, persist, hand back a record.

One operation, and the ordering inside it is the part that matters. The request
row is committed before the provider is invoked, the result and its provenance
land in a single statement, and every failure writes a truthful row rather than
being swallowed. Nothing here dispatches a worker, creates a task, or touches
Task Core — a prepared prompt is a string in a column.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from ..context.projection.model import CloudContextProjection
from .errors import PlannerContextRefused, PlannerError
from .models import DevelopmentRequest
from .protocol import DevelopmentPlanner
from .store import PlannerRecord, PlannerStore

PLANNER_REQUEST_ID_PREFIX = "plan_"


def new_planner_request_id() -> str:
    """Opaque, unique, and not derived from any content.

    Takes no argument, so there is no parameter an intent could arrive in — the
    same property Task Core's ``new_task_id`` is built around, for the same
    reason.
    """
    return PLANNER_REQUEST_ID_PREFIX + secrets.token_hex(13)


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ProjectionSource(Protocol):
    """Whatever produces the external-safe context. Injected, never built here.

    Typed as a protocol so the service does not import the projector directly
    and cannot be tempted to construct one with its own policy. The real
    ``ContextProjector`` satisfies it; so does a test double, and the integration
    test uses the real one precisely because the policy is the point.
    """

    def project(self, pack: object, *, budget_bytes: Optional[int] = ...) -> CloudContextProjection:
        ...  # pragma: no cover - structural


@dataclass(frozen=True)
class PlanningOutcome:
    """What the caller gets back: the durable record, and whether it succeeded."""

    record: PlannerRecord
    ok: bool

    @property
    def planner_request_id(self) -> str:
        return self.record.planner_request_id


class PlannerService:
    """Owns the planner invocation lifecycle, and therefore its persistence."""

    def __init__(
        self,
        *,
        store: PlannerStore,
        planner: DevelopmentPlanner,
        clock=_utc_now,
    ) -> None:
        self._store = store
        self._planner = planner
        self._clock = clock

    # -- startup
    def reconcile_interrupted(self) -> int:
        """Tell the truth about invocations this host did not finish.

        Called at start. It marks, and it does not rerun: re-invoking a provider
        because a process died would spend a second call and assert that the
        first never happened, neither of which this host knows to be true.
        """
        return self._store.mark_interrupted(completed_at=self._clock())

    # -- the one operation
    def prepare_development_step(
        self,
        *,
        projection: CloudContextProjection,
        user_intent: str,
        current_objective: Optional[str] = None,
        plan_checkpoint: Optional[str] = None,
        expected_next_step: Optional[str] = None,
        research_notes: Optional[str] = None,
        prompt_writing_guidance: Optional[str] = None,
        authority_boundary: Optional[str] = None,
    ) -> PlanningOutcome:
        """Ask the planner for one step, durably.

        ``projection`` is required and typed. There is no parameter here for a
        path, a working directory, a command, a model or a provider flag — the
        provider is host-configured, and a caller chooses none of it.
        """
        if not isinstance(projection, CloudContextProjection):
            raise PlannerContextRefused(
                "an external planner requires a CloudContextProjection",
                detail=type(projection).__name__,
            )

        request_id = new_planner_request_id()
        request = DevelopmentRequest(
            request_id=request_id,
            user_intent=user_intent,
            projection=projection,
            current_objective=current_objective,
            plan_checkpoint=plan_checkpoint,
            expected_next_step=expected_next_step,
            research_notes=research_notes,
            prompt_writing_guidance=prompt_writing_guidance,
            authority_boundary=authority_boundary,
        )
        payload = request.to_prompt_payload()

        # Committed before the provider is touched. Everything after this point
        # can crash and still leave an honest row.
        self._store.create_request(
            planner_request_id=request_id,
            workspace_id=projection.workspace_id,
            project_id=projection.project_id,
            user_intent=user_intent,
            request_payload=payload,
            projection_policy_id=projection.policy_id,
            projection_built_at=projection.built_at,
            created_at=self._clock(),
        )
        self._store.mark_running(request_id, started_at=self._clock())

        try:
            turn = self._planner.prepare_development_step(request)
        except PlannerError as exc:
            self._store.record_failure(
                request_id,
                failure_code=exc.reason_code,
                failure_message=str(exc),
                completed_at=self._clock(),
            )
            return PlanningOutcome(record=self._require(request_id), ok=False)
        except Exception as exc:  # an unexpected provider defect is still a failure
            self._store.record_failure(
                request_id,
                failure_code="planner_error",
                failure_message=f"{type(exc).__name__}: {exc}",
                completed_at=self._clock(),
            )
            return PlanningOutcome(record=self._require(request_id), ok=False)

        # A STOP is a *successful invocation* whose action was a refusal to
        # plan. Recording it as a failure would lose the distinction between
        # "the model declined" and "the provider broke".
        self._store.record_success(
            request_id,
            result=turn.result,
            execution=turn.execution,
            completed_at=self._clock(),
        )
        return PlanningOutcome(record=self._require(request_id), ok=True)

    # -- reads
    def get(self, planner_request_id: str) -> Optional[PlannerRecord]:
        return self._store.get(planner_request_id)

    def recent(self, limit: int = 50):
        return self._store.recent(limit)

    def request_payload(self, planner_request_id: str) -> Optional[dict]:
        return self._store.request_payload(planner_request_id)

    def _require(self, planner_request_id: str) -> PlannerRecord:
        record = self._store.get(planner_request_id)
        if record is None:  # pragma: no cover - would mean the row vanished
            raise PlannerError("planner record disappeared after write")
        return record


__all__ = [
    "PLANNER_REQUEST_ID_PREFIX",
    "PlannerService",
    "PlanningOutcome",
    "ProjectionSource",
    "new_planner_request_id",
]
