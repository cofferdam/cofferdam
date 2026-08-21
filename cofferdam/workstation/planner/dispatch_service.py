"""Handing one approved prompt to one bounded worker. The only such path.

The signature is the security property
--------------------------------------

::

    dispatch_approved_worker_prompt(planner_request_id, *, provenance)

There is no ``prompt`` parameter. No ``cwd``, no ``repository``, no ``worktree``,
no ``branch``, no ``command``, no ``argv``, no ``executable``, no ``model``, no
``tools``, no ``mcp_config`` and no ``adapter``. The caller names *which approved
decision to discharge*; Cofferdam loads the prompt, resolves the project, cuts
the worktree and chooses the adapter itself.

That absence is the answer to the one attack this layer exists to prevent:
approve prompt A, dispatch prompt B. There is no argument through which B could
arrive, so the substitution is not something to validate against — it is not
expressible.

Where the project comes from
----------------------------

Not from the caller, and not from the planner's own text. The planner request
carries the ``workspace_id`` and ``project_id`` that were durably recorded when
the context was built, and those resolve through host-owned configuration:

    planner request  →  workspace_id  →  Workspace  →  project_id
                                                    →  TaskProject  →  verified root

Every hop is a file somebody wrote on the workstation. The chain fails closed at
any hop, and a project that does not permit the worker adapter fails at the last
one.

What this service does not own
------------------------------

Execution. Task Core does — its lifecycle, its cancellation, its events, its
idempotency. This service creates *one durable linkage* saying which approved
subject a task is discharging, and then gets out of the way. There is no state
column here, no polling loop, and no second job system.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..tasks.errors import ProjectDisabled, ProjectRootInvalid, ProjectUnknown
from ..worker import reconcile
from .authority import AuthorityProvenance, derive_gate
from .dispatch import (
    REFUSE_NO_REQUEST,
    REFUSE_PROJECT_INELIGIBLE,
    REFUSE_PROJECT_UNRESOLVED,
    WORKER_KIND_CLAUDE_CODE,
    DispatchDecision,
    dispatch_request_key,
    evaluate_dispatch,
    new_dispatch_id,
    worker_prompt_digest,
)
from .errors import PlannerAuthorityInvalid, PlannerError
from .store import DispatchReconciliation, PlannerStore, WorkerDispatch

#: The adapter this build dispatches to. Code-owned, and there is no parameter
#: anywhere above that selects one — "which agent runs my approved prompt" is
#: not a caller's decision.
WORKER_ADAPTER_ID = "claude-code-worker"


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class WorkerDispatchRefused(PlannerError):
    """This approved result may not become a worker, and says which rule stopped it.

    Raised **before** any task, worktree or process exists. Every refusal in this
    module happens on the read side, so a refused dispatch has started nothing
    and left nothing behind.
    """

    reason_code = "worker_dispatch_refused"

    def __init__(self, message: str, *, reason: str, detail: Optional[str] = None) -> None:
        super().__init__(message, detail=detail)
        self.reason = reason

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        payload["reason"] = self.reason
        return payload


@dataclass(frozen=True)
class DispatchView:
    """What a caller — and, later, a status screen — gets back.

    Three sections, never merged: what the planner proposed, what the person
    authorized and Cofferdam dispatched, and what Task Core says the worker is
    doing now. The last is read live from Task Core rather than copied, because a
    status stored here would be right for a few seconds and then wrong with
    nothing announcing it.
    """

    dispatch: WorkerDispatch
    planner_summary: Optional[str]
    task: Optional[Any] = None
    worktree: Optional[Dict[str, Any]] = None
    #: What a restart found, if this dispatch ever survived one. Durable and
    #: read back rather than recomputed: it is a record of what was true just
    #: after the crash, and re-deriving it now would answer a different question.
    reconciliation: Optional[Any] = None

    @property
    def task_id(self) -> str:
        return self.dispatch.task_id

    def to_dict(self) -> Dict[str, Any]:
        """The safe read shape for a "what is Cofferdam doing" surface.

        The approved prompt is **not** here. It is durable and retrievable by
        asking for it (:meth:`WorkerDispatchService.dispatched_prompt`), the same
        rule the planner's request payload follows: a routine status read should
        not carry the whole prompt, and a caller that wants it should have to
        ask.

        No path appears anywhere. Not the project root, not the worktree, not the
        worker home — a client learns the branch and the project id, which name
        things rather than locate them.
        """
        task = self.task
        state = getattr(task, "state", None)
        payload: Dict[str, Any] = {
            "dispatch": self.dispatch.to_dict(),
            "planner_summary": self.planner_summary,
            "worker": {
                "adapter_id": self.dispatch.adapter_id,
                "task_id": self.dispatch.task_id,
                "state": state,
                "started_at": getattr(task, "started_at", None),
                "completed_at": getattr(task, "completed_at", None),
                "latest_activity": getattr(task, "latest_activity", None),
                "final_result": getattr(task, "final_result", None),
                # Flattened rather than passed through. The status surface this
                # feeds is JSON, and a read model that cannot be serialized is
                # not a read model — found by the service-level smoke, which
                # tried to serialize it and could not.
                "failure": _failure_dict(getattr(task, "failure", None)),
            },
            "worktree": self.worktree,
            # What a Stop button should render. Derived from the live state
            # rather than stored, so it cannot disagree with reality.
            "cancellable": state in ("running", "starting", "created"),
            # The boundary, stated in the read model so nothing downstream has to
            # rediscover it: a finished worker is a finished *process*, not an
            # accepted change. PR1f does not soften this — a *recovered* worker
            # is even further from accepted, because nothing observed it finish.
            "worker_completion_is_not_acceptance": True,
            "human_action_needed": False,
        }

        # -- restart facts. Absent entirely when this dispatch never survived a
        # -- restart, so a routine read is unchanged and a client can treat the
        # -- key's presence as the question "was this interrupted".
        found = self.reconciliation
        if found is not None:
            payload["recovery"] = found.to_dict()
            payload["recovery"]["sentence"] = RECOVERY_SENTENCES.get(
                found.outcome
            )
            payload["restart_occurred"] = True
            payload["human_action_needed"] = bool(found.needs_attention)
            # Hoisted to the top level because these are the two questions asked
            # of an unattended run — *is my work still there* and *did anything
            # land* — and a client should not have to know the shape of the
            # recovery block to answer them.
            payload["partial_work_preserved"] = (
                found.outcome == reconcile.OUTCOME_PARTIAL_WORK_PRESERVED
            )
            payload["worktree_retained"] = bool(found.worktree_retained)
            payload["recovered_commit"] = found.recovered_commit
        else:
            payload["restart_occurred"] = False
        return payload


class WorkerDispatchService:
    """The gate, the project resolution, and one linkage row."""

    def __init__(
        self,
        *,
        store: PlannerStore,
        tasks,
        workspaces=None,
        clock=_utc_now,
    ) -> None:
        # ``tasks`` is the Task Core service — the execution owner. This service
        # holds a reference to it and no process of its own; there is no Popen,
        # no thread and no queue in this file.
        self._store = store
        self._tasks = tasks
        self._workspaces = workspaces
        self._clock = clock

    # -- reads ---------------------------------------------------------------

    def view(self, planner_request_id: str) -> Optional[DispatchView]:
        dispatch = self._store.dispatch(planner_request_id)
        if dispatch is None:
            return None
        record = self._store.get(planner_request_id)
        return DispatchView(
            dispatch=dispatch,
            planner_summary=getattr(record, "summary", None),
            task=self._task(dispatch.task_id),
            worktree={"branch": dispatch.branch, "project_id": dispatch.project_id},
            reconciliation=self._store.reconciliation(dispatch.dispatch_id),
        )

    def dispatched_prompt(self, planner_request_id: str) -> Optional[str]:
        """The exact prompt this dispatch handed a worker. The audit path.

        Answers *"Worker'a gönderilen prompt buydu"* from durable state: the
        persisted approved prompt, re-verified against the digest recorded when
        it was dispatched. A mismatch returns ``None`` rather than the current
        text, because the honest answer to "what did you send" is never "here is
        what is there now".
        """
        dispatch = self._store.dispatch(planner_request_id)
        record = self._store.get(planner_request_id)
        if dispatch is None or record is None or record.worker_prompt is None:
            return None
        if worker_prompt_digest(record.worker_prompt) != dispatch.worker_prompt_sha256:
            return None
        return record.worker_prompt

    def _task(self, task_id: str):
        for name in ("get_task", "get"):
            getter = getattr(self._tasks, name, None)
            if getter is not None:
                try:
                    return getter(task_id)
                except Exception:
                    return None
        return None

    # -- the one write -------------------------------------------------------

    def dispatch_approved_worker_prompt(
        self, planner_request_id: str, *, provenance: AuthorityProvenance
    ) -> DispatchView:
        """Run the gate, resolve the project, create at most one worker.

        Idempotent by construction. A second call for the same approved result
        returns the existing dispatch without creating anything — and so does a
        call that follows a crash between task creation and linkage, because the
        Task Core request key is derived rather than minted (see
        :func:`~.dispatch.dispatch_request_key`).
        """
        if not isinstance(provenance, AuthorityProvenance):
            raise PlannerAuthorityInvalid("provenance must be an AuthorityProvenance")

        existing = self._store.dispatch(planner_request_id)
        if existing is not None:
            return self._existing(existing)

        record = self._store.get(planner_request_id)
        if record is None:
            raise WorkerDispatchRefused(
                "no planner request by that id",
                reason=REFUSE_NO_REQUEST,
                detail=planner_request_id,
            )

        gate = derive_gate(record, event=self._store.authority_event(planner_request_id))
        decision = evaluate_dispatch(record, gate)
        if not decision.allowed:
            raise WorkerDispatchRefused(
                _refusal_message(decision), reason=decision.reason, detail=decision.detail
            )

        project = self._resolve_project(decision)

        request_key = dispatch_request_key(
            planner_request_id=planner_request_id,
            subject_fingerprint=decision.subject_fingerprint,
            worker_kind=WORKER_KIND_CLAUDE_CODE,
        )

        # Task Core owns the execution. The prompt handed over is the persisted
        # approved prompt, byte for byte — `decision.worker_prompt` came off the
        # planner row and nothing has reformatted it.
        task, _created = self._tasks.create_task(
            project_id=project.project_id,
            adapter_id=WORKER_ADAPTER_ID,
            prompt=decision.worker_prompt,
            client_request_id=request_key,
            origin="cli",
            title="Approved development step",
        )

        dispatch = WorkerDispatch(
            dispatch_id=new_dispatch_id(),
            planner_request_id=planner_request_id,
            authority_event_id=decision.authority_event_id,
            subject_fingerprint=decision.subject_fingerprint,
            worker_prompt_sha256=worker_prompt_digest(decision.worker_prompt),
            project_id=project.project_id,
            workspace_id=decision.workspace_id,
            adapter_id=WORKER_ADAPTER_ID,
            task_id=task.task_id,
            request_key=request_key,
            branch=_branch_for(task.task_id),
            actor=provenance.actor,
            source=provenance.source,
            created_at=self._clock(),
        )
        stored = self._store.record_dispatch(dispatch)
        if stored is None:
            # Another caller linked this request while we were creating the task.
            # Because the request key is derived, their task and ours are the
            # same row — so there is one worker, and theirs is the record.
            stored = self._store.dispatch(planner_request_id)
        return self._existing(stored)

    def cancel(self, planner_request_id: str):
        """Stop the worker for one dispatch, through Task Core's own cancellation.

        Scoped by dispatch, and therefore by task: the id handed to Task Core
        came out of this dispatch's own row, so a cancel cannot reach another
        project's work. There is no second cancellation architecture here and no
        process handling of any kind — Task Core asks the adapter, and the
        adapter stops the process it started for that task.
        """
        dispatch = self._store.dispatch(planner_request_id)
        if dispatch is None:
            raise WorkerDispatchRefused(
                "there is no dispatch to cancel",
                reason=REFUSE_NO_REQUEST,
                detail=planner_request_id,
            )
        self._tasks.cancel_task(dispatch.task_id)
        return self.view(planner_request_id)

    # -- helpers -------------------------------------------------------------

    def _existing(self, dispatch: WorkerDispatch) -> DispatchView:
        record = self._store.get(dispatch.planner_request_id)
        return DispatchView(
            dispatch=dispatch,
            planner_summary=getattr(record, "summary", None),
            task=self._task(dispatch.task_id),
            worktree={"branch": dispatch.branch, "project_id": dispatch.project_id},
        )

    # -- recovery ------------------------------------------------------------

    def reconcile_after_restart(self, *, state_dir=None) -> Dict[str, int]:
        """Settle every worker dispatch a restart left in ``recovery_required``.

        **Nothing is re-executed.** No worker is launched, no prompt is re-sent,
        no check is rerun, no commit is made, no worktree is created, reset or
        deleted. Each dispatch is *classified* from durable state plus Git — see
        :mod:`...worker.reconcile` — the classification is recorded, and the task
        is moved to the terminal state that classification actually supports.

        Where this sits, and why it is not in Task Core
        ------------------------------------------------

        Task Core's own :meth:`TaskService.recover_after_restart` already ran, at
        start-up, before this. It did the part that is genuinely its own: it
        found tasks the database still believed were unfinished and, because this
        adapter now declares ``recover_after_restart``, parked them in
        ``recovery_required`` — the state its docstring describes as *waiting for
        a decision this milestone does not implement*.

        This method is that decision, and it lives here because everything it
        needs to make it lives here. Task Core knows about tasks; it does not
        know what a dispatch is, which planner result authorized one, where a
        worktree was cut or what a worker commit looks like. Moving this in there
        would mean teaching the lifecycle owner about development workers, which
        is the coupling the adapter boundary exists to prevent.

        So the two halves are: Task Core decides *that* a task stopped and needs
        a decision; the dispatch layer decides *what the decision is*. Neither
        duplicates the other, and there is no second scanner — this reads the
        task ids Task Core already parked.

        Why no task is ever reconciled to ``completed``
        -----------------------------------------------

        There is no such edge in the transition graph, and that is correct rather
        than an obstacle to work around. ``completed`` asserts Cofferdam observed
        a worker run to a successful end. After a crash it did not — the worker's
        own report was never captured, and a commit existing is not the execution
        contract being satisfied. A dispatch whose commit is recovered therefore
        settles as ``interrupted`` *with the commit recorded beside it*, which is
        the pair of facts a person actually needs: the run was interrupted, and
        the work was not lost.

        Adding a ``recovery_required → completed`` edge would let a restart
        manufacture the one claim nobody was there to verify.

        Idempotent, and bounded. Reconciliations are ``INSERT OR IGNORE`` keyed by
        dispatch, so restarting repeatedly records the first answer and no more;
        and once the task leaves ``recovery_required`` it is terminal, so the
        second pass finds nothing to do at all.
        """
        directory = Path(state_dir) if state_dir is not None else _default_state_dir()
        tally: Dict[str, int] = {}

        parked = self._recovery_required_tasks()
        if not parked:
            return tally

        for dispatch in self._store.dispatches_for_tasks(
            [row.task_id for row in parked]
        ):
            if dispatch.adapter_id != WORKER_ADAPTER_ID:
                # Another adapter's task that happens to have a dispatch row is
                # not this method's to settle.
                continue
            outcome = self._reconcile_one(dispatch, directory)
            tally[outcome] = tally.get(outcome, 0) + 1
        return tally

    def _recovery_required_tasks(self) -> List[Any]:
        """The tasks Task Core parked. Read from it, never re-derived here."""
        lister = getattr(self._tasks, "tasks_awaiting_recovery", None)
        if lister is None:  # pragma: no cover - a double may not expose it
            return []
        try:
            return list(lister())
        except Exception:  # pragma: no cover - start-up must not fail
            return []

    def _reconcile_one(self, dispatch, state_dir: Path) -> str:
        """Classify one dispatch, record it, settle its task. Never raises."""
        try:
            project_root = self._recovery_project_root(dispatch)
        except Exception:
            project_root = None

        if project_root is None:
            # The project no longer resolves. Cofferdam cannot inspect the
            # worktree, so it says so rather than guessing that nothing happened.
            found = reconcile.Reconciliation(
                outcome=reconcile.OUTCOME_UNDETERMINED,
                detail="this dispatch's project no longer resolves on this host",
            )
        else:
            found = reconcile.classify(
                project_id=dispatch.project_id,
                task_id=dispatch.task_id,
                project_root=project_root,
                state_dir=state_dir,
            )

        settled = self._settle_task(dispatch, found)
        self._store.record_reconciliation(
            DispatchReconciliation(
                dispatch_id=dispatch.dispatch_id,
                task_id=dispatch.task_id,
                project_id=dispatch.project_id,
                outcome=found.outcome,
                furthest_phase=found.furthest_phase,
                open_intents=",".join(found.open_intents) or None,
                recovered_commit=found.recovered_commit,
                checks_observed=int(found.checks_observed),
                check_exit_zero=(
                    None if found.check_exit_zero is None else int(found.check_exit_zero)
                ),
                changed_files=int(found.changed_files),
                worktree_retained=int(found.worktree_retained),
                needs_attention=int(found.needs_attention),
                detail=found.detail,
                task_state=settled,
                reconciled_at=self._clock(),
            )
        )
        return found.outcome

    def _recovery_project_root(self, dispatch) -> Optional[Path]:
        """Re-resolve this dispatch's project through the durable registry.

        **The project id comes from the dispatch row, never from a caller**, and
        it is turned into a directory by the same Task Core registry a live
        dispatch uses. Recovery accepts no path from anywhere: a recovery-time
        path parameter would be the one place in this feature where a location
        could be supplied from outside, which is exactly the property PR1e spent
        its effort removing.

        The workspace layer is deliberately *not* consulted here. A workspace is
        the identity a plan was built under and it can be switched between the
        dispatch and the restart; the project id recorded on the dispatch row is
        the thing that was actually worked in, and that is what must be inspected.
        """
        registry = self._tasks.projects
        if callable(registry):  # pragma: no cover - a double may expose a method
            registry = registry()
        project = registry.get(dispatch.project_id)
        root = getattr(project, "root", None)
        return Path(root) if root is not None else None

    def _settle_task(self, dispatch, found) -> Optional[str]:
        """Move the task out of ``recovery_required`` to what the evidence says.

        Two destinations only, and ``completed`` is not one of them — see
        :meth:`reconcile_after_restart`. ``interrupted`` is the honest terminal
        state for a run the daemon stopped underneath, and it is what every
        outcome here settles as; the *difference* between outcomes is carried by
        the reconciliation record beside it, not by inventing lifecycle states
        Task Core does not have.

        A task that is no longer in ``recovery_required`` is left completely
        alone. That is what keeps a **cancellation** dominant: a person who
        cancelled during the outage owns that decision, and a recovery pass that
        promoted `cancelled` into something else on the strength of files on disk
        would be overruling the one authority this system treats as final.
        """
        settle = getattr(self._tasks, "settle_recovered_task", None)
        if settle is None:  # pragma: no cover - a double may not expose it
            return None
        try:
            return settle(
                dispatch.task_id,
                detail=_recovery_sentence(found),
            )
        except Exception:  # pragma: no cover - start-up must not fail
            return None

    def _resolve_project(self, decision: DispatchDecision):
        """Project identity from durable state, through host-owned configuration.

        The workspace is consulted first when one was recorded, because that is
        the identity the planner's context was actually built from; the project
        id on the row is used when there is no workspace layer to cross. Either
        way the answer comes out of the Task Core project registry, which is the
        only thing on this host that turns an id into a directory.
        """
        project_id = decision.project_id
        if self._workspaces is not None and decision.workspace_id:
            workspace = self._find_workspace(decision.workspace_id)
            if workspace is None:
                raise WorkerDispatchRefused(
                    "the workspace this plan was built for no longer resolves",
                    reason=REFUSE_PROJECT_UNRESOLVED,
                    detail=decision.workspace_id,
                )
            project_id = workspace.project_id

        registry = self._tasks.projects
        if callable(registry):  # pragma: no cover - a double may expose a method
            registry = registry()
        try:
            project = registry.get(project_id)
        except (ProjectUnknown, ProjectDisabled) as exc:
            raise WorkerDispatchRefused(
                "that project is not available for development work",
                reason=REFUSE_PROJECT_UNRESOLVED,
                detail=project_id,
            ) from exc
        except ProjectRootInvalid as exc:
            raise WorkerDispatchRefused(
                "that project's folder is not usable right now",
                reason=REFUSE_PROJECT_UNRESOLVED,
                detail=project_id,
            ) from exc

        if not project.permits(WORKER_ADAPTER_ID):
            # The project has not authorized a development worker. Refused
            # rather than run under some other adapter: "which agent runs here"
            # is the project's decision and a fallback would overrule it.
            raise WorkerDispatchRefused(
                "this project does not permit a development worker",
                reason=REFUSE_PROJECT_INELIGIBLE,
                detail=project.project_id,
            )
        return project

    def _find_workspace(self, workspace_id: str):
        for name in ("find", "get"):
            getter = getattr(self._workspaces, name, None)
            if getter is None:
                continue
            try:
                found = getter(workspace_id)
            except Exception:
                return None
            if found is not None:
                return found
        return None


#: What a status screen should be able to say instead of "failed".
#:
#: Step 17's requirement, and the reason this table is data rather than a chain
#: of ``if`` in a template: the sentence a person reads after an unattended
#: overnight run is the entire product of this milestone, and it should be
#: reviewable in one place next to the outcome it belongs to.
RECOVERY_SENTENCES: Dict[str, str] = {
    reconcile.OUTCOME_NEVER_STARTED: (
        "Cofferdam restarted before this worker began. Nothing was run and "
        "nothing was changed."
    ),
    reconcile.OUTCOME_NO_WORK_FOUND: (
        "Cofferdam restarted while this worker was running. Its worktree was "
        "prepared but nothing had been changed in it."
    ),
    reconcile.OUTCOME_PARTIAL_WORK_PRESERVED: (
        "Cofferdam restarted while this worker was editing. The unfinished "
        "changes are preserved in its worktree and were not committed, reset "
        "or discarded."
    ),
    reconcile.OUTCOME_COMMIT_RECOVERED: (
        "This worker had already committed before Cofferdam restarted. The "
        "commit was found and recorded; it was not made a second time. The run "
        "itself was interrupted and its result was never verified."
    ),
    reconcile.OUTCOME_WORKTREE_MISSING: (
        "Cofferdam restarted while this worker was running and its worktree is "
        "no longer on disk. Nothing was recreated and nothing was rerun."
    ),
    reconcile.OUTCOME_WORKTREE_MISMATCHED: (
        "Cofferdam found something other than this dispatch's worktree where "
        "it belongs. It was left untouched and this needs a person."
    ),
    reconcile.OUTCOME_CONTRADICTORY: (
        "Cofferdam's record of this worker and the repository disagree. "
        "Nothing was changed and this needs a person."
    ),
    reconcile.OUTCOME_UNDETERMINED: (
        "Cofferdam could not determine what this worker had done before the "
        "restart. Nothing was rerun and nothing was removed."
    ),
}


def _recovery_sentence(found) -> str:
    """The plain sentence for one outcome. Never a bare "failed"."""
    return RECOVERY_SENTENCES.get(
        found.outcome,
        "Cofferdam restarted while this worker was running. It was reconciled, "
        "not resumed.",
    )


def _failure_dict(failure) -> Optional[Dict[str, Any]]:
    """A task failure as plain data, or ``None``.

    Uses the object's own ``to_dict`` when it has one and falls back to a
    bounded pair of strings, so a future failure type cannot make this surface
    unserializable again.
    """
    if failure is None:
        return None
    converter = getattr(failure, "to_dict", None)
    if callable(converter):
        try:
            return converter()
        except Exception:  # pragma: no cover - a defensive fallback
            pass
    return {
        "code": str(getattr(failure, "code", "") or "")[:200] or None,
        "message": str(getattr(failure, "message", "") or "")[:1000] or None,
    }


def _branch_for(task_id: str) -> Optional[str]:
    """The branch the worker will use, derived the same way the adapter derives it.

    Computed here so the durable dispatch record can name it before the worker
    has started. Both sides call the same code-owned function, so they cannot
    disagree — and neither is reading it from anything a model produced.
    """
    from ..worker.worktree import WorktreeError, branch_name

    try:
        return branch_name(task_id)
    except WorktreeError:  # pragma: no cover - task ids always validate
        return None


def _refusal_message(decision: DispatchDecision) -> str:
    return {
        "planner_invocation_did_not_succeed": "that planning turn did not produce a result",
        "planner_result_is_not_a_prepared_prompt": "that planner result is not a prepared worker prompt",
        "prepared_prompt_missing": "that planner result carries no prompt to run",
        "result_schema_unsupported": "that result speaks a contract this build does not",
        "not_approved": "that planner result has not been approved by a person",
        "approval_rejected": "a person refused this prepared prompt",
        "awaiting_human_confirmation": "this prepared prompt is still waiting for a person",
        "approval_does_not_bind_current_prompt": (
            "the approval does not bind the prompt that is stored now"
        ),
        "project_identity_missing": "this plan records no project to work in",
    }.get(decision.reason or "", "this approved result may not be dispatched")


__all__ = [
    "WORKER_ADAPTER_ID",
    "DispatchView",
    "WorkerDispatchRefused",
    "WorkerDispatchService",
]
