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
from .store import IngressKey, PlannerRecord, PlannerStore

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


class ContextSource(Protocol):
    """Builds the rich local pack for the active workspace."""

    def build(self, current_message: object, **kwargs: Any) -> object:
        ...  # pragma: no cover - structural


class ProjectionSource(Protocol):
    """Turns a local pack into the bounded object eligible to leave the host.

    Typed as protocols so the service does not import either component directly
    and cannot be tempted to construct one with its own policy. The real
    ``ContextBuilder`` and ``ContextProjector`` satisfy them, and the integration
    test uses the real ones precisely because the policy is the point.
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
        context: ContextSource,
        projector: ProjectionSource,
        clock=_utc_now,
    ) -> None:
        self._store = store
        self._planner = planner
        self._context = context
        self._projector = projector
        self._clock = clock

    # -- startup
    def reconcile_interrupted(self) -> int:
        """Tell the truth about invocations this host did not finish.

        Called at start. It marks, and it does not rerun: re-invoking a provider
        because a process died would spend a second call and assert that the
        first never happened, neither of which this host knows to be true.

        The external request receipts are settled on the same terms and in the
        same breath (M2M PR4). A claim with no planner request is a claim whose
        owner is gone; leaving it open would let the next retry reuse it, and
        reusing it is exactly the second invocation this method exists to refuse.
        Only the planner rows are counted in the return value, which is what the
        number has always meant.
        """
        marked = self._store.mark_interrupted(completed_at=self._clock())
        abandon = getattr(self._store, "abandon_open_ingress", None)
        if abandon is not None:
            abandon(abandoned_at=self._clock())
        return marked

    # -- the one operation
    def prepare_development_step(
        self,
        *,
        user_intent: str,
        current_objective: Optional[str] = None,
        plan_checkpoint: Optional[str] = None,
        expected_next_step: Optional[str] = None,
        research_notes: Optional[str] = None,
        prompt_writing_guidance: Optional[str] = None,
        authority_boundary: Optional[str] = None,
        ingress: Optional[IngressKey] = None,
    ) -> PlanningOutcome:
        """Ask the planner for one step, durably.

        **Cofferdam owns the context pipeline, not the caller.** A caller
        supplies semantic development intent; this method builds the local pack
        and projects it. The earlier shape took a ready-made
        ``CloudContextProjection`` as an argument, which quietly put the caller
        in charge of what left the host — the one decision the egress boundary
        exists to make for them. There is likewise no parameter here for a path,
        a working directory, a command, a model or a provider flag.

        ``ingress`` names the external request this invocation is being made for,
        so the request row and the mapping a retry reconciles to are written in
        one transaction (M2M PR4). It is two opaque strings. It selects nothing,
        enables nothing and is not read by anything below this method — it is
        forwarded to the store and never consulted here — so it cannot become a
        route by which a caller influences what is asked or what is sent.
        """
        pack = self._context.build(user_intent)
        projection = self._projector.project(pack)
        if not isinstance(projection, CloudContextProjection):
            # A projector that returned something else is a defect, not a
            # caller error — but it is still the boundary, so it still refuses.
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
            ingress=ingress,
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
    "ContextSource",
    "PlannerService",
    "PlanningOutcome",
    "ProjectionSource",
    "new_planner_request_id",
]
