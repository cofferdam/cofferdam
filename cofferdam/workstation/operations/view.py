"""Joining five owners into one operational answer. Reads only.

What this service is
--------------------

One method worth the name: :meth:`OperationsService.overview`, which answers
*what is Cofferdam doing right now* for every project the host has registered,
and :meth:`OperationsService.project`, which answers it for one.

It holds **no state**. Every field is read live from whichever component owns it
and joined on ids that are already durable:

    planner_request → authority_event → dispatch → task → reconciliation
                                                        → publication

No table, no cache, no status column, no writer. That is what makes this view
incapable of disagreeing with the systems it summarises — there is nothing here
that could be stale, because there is nothing here that is stored.

Projects do not mix
-------------------

Every read is scoped by ``project_id`` at the point the rows are selected, not
filtered afterwards. Two projects with work in flight produce two independent
views, and a test asserts that one project's dispatch, commit, branch and pull
request never appear in the other's.

What is deliberately *not* here
--------------------------------

**The prompt.** A status read happens often and a worker prompt is large, so the
approved text is not in this payload — the view carries a
``prompt_available`` flag and the ids needed to ask for it, and
``WorkerDispatchService.dispatched_prompt`` remains the way to get it. That is
the same rule the dispatch view already follows.

**Anything a model said.** Worker prose is a *claim*. It travels in a clearly
labelled ``claims`` block, never in ``machine``, and it never decides a phase —
see :mod:`.phases`. Mixing the two is the failure this whole milestone's
vocabulary exists to prevent.

**Any host path**, any credential, any remote URL. Asserted by test.

**Actions.** The view reports which controls *would* apply to the current phase
so a future cockpit can render buttons from one source of truth, and implements
none of them. Nothing in this package can answer, approve, cancel or publish.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import phases

#: The controls a future remote surface may offer. Named here so the set is one
#: decision, and so a client cannot invent one: a phase maps to the actions that
#: are *meaningful* in it, and anything not listed is not offered.
ACTION_ANSWER = "answer"
ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"
ACTION_CANCEL = "cancel"
ACTION_INSPECT_PROMPT = "inspect_prompt"
ACTION_INSPECT_RESULT = "inspect_result"
ACTION_PUBLISH = "publish"
ACTION_OPEN_PULL_REQUEST = "open_pull_request"
ACTION_RECONCILE = "reconcile"
ACTION_SIGN_IN = "sign_in"

#: Which controls make sense in which phase. **Availability, not authority.**
#:
#: A phase appearing here does not grant anything — every one of these still
#: goes through its own gate when it is eventually implemented. This exists so
#: that a cockpit renders the same buttons Cofferdam would accept, rather than
#: guessing and discovering the refusal after a tap.
AVAILABLE_ACTIONS: Dict[str, tuple] = {
    phases.PHASE_IDLE: (),
    phases.PHASE_PLANNER_PREPARING: (ACTION_CANCEL,),
    phases.PHASE_AWAITING_USER_ANSWER: (ACTION_ANSWER, ACTION_INSPECT_RESULT),
    # Answered, and deliberately without `answer`: the question this request
    # holds already has a durable answer, and offering the control again would
    # render a button whose only outcome is a conflict refusal. There is no
    # replan, continue or dispatch control here either — the next step is a new
    # development request, which is not an action on this operation.
    phases.PHASE_ANSWERED: (ACTION_INSPECT_RESULT,),
    phases.PHASE_AWAITING_APPROVAL: (
        ACTION_APPROVE, ACTION_REJECT, ACTION_INSPECT_PROMPT, ACTION_INSPECT_RESULT,
    ),
    phases.PHASE_REJECTED: (ACTION_INSPECT_PROMPT,),
    phases.PHASE_STOPPED: (ACTION_INSPECT_RESULT,),
    phases.PHASE_WORKER_QUEUED: (ACTION_CANCEL, ACTION_INSPECT_PROMPT),
    phases.PHASE_WORKER_RUNNING: (ACTION_CANCEL, ACTION_INSPECT_PROMPT),
    phases.PHASE_RECOVERY_REQUIRED: (ACTION_RECONCILE, ACTION_INSPECT_PROMPT),
    phases.PHASE_RECOVERY_RECONCILED: (ACTION_INSPECT_PROMPT, ACTION_INSPECT_RESULT),
    phases.PHASE_WORKER_INTERRUPTED: (ACTION_INSPECT_PROMPT, ACTION_INSPECT_RESULT),
    phases.PHASE_COMMIT_READY: (
        ACTION_PUBLISH, ACTION_INSPECT_PROMPT, ACTION_INSPECT_RESULT,
    ),
    phases.PHASE_PUBLISHING: (ACTION_INSPECT_RESULT,),
    phases.PHASE_PR_READY: (ACTION_OPEN_PULL_REQUEST, ACTION_INSPECT_RESULT),
    phases.PHASE_FAILED: (ACTION_INSPECT_RESULT,),
    phases.PHASE_CANCELLED: (ACTION_INSPECT_PROMPT,),
    phases.PHASE_AUTH_REQUIRED: (ACTION_SIGN_IN,),
    phases.PHASE_NEEDS_ATTENTION: (ACTION_INSPECT_RESULT,),
}


@dataclass(frozen=True)
class ProjectOperations:
    """One project's current operational state. Safe to serialize and send."""

    project_id: str
    display_name: str
    phase: phases.Phase
    #: Ids a later control needs to act. Not secrets, not paths — every one is
    #: already in some read model, and together they are what makes an action
    #: addressable without a client having to search for "the latest" anything.
    handles: Dict[str, Any] = field(default_factory=dict)
    #: What Cofferdam itself observed by running Git or reading its own rows.
    machine: Dict[str, Any] = field(default_factory=dict)
    #: What a model said. Never an input to :attr:`phase`.
    claims: Dict[str, Any] = field(default_factory=dict)
    actions: tuple = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "display_name": self.display_name,
            **self.phase.to_dict(),
            "handles": self.handles,
            "machine": self.machine,
            "claims": self.claims,
            "available_actions": list(self.actions),
        }


class OperationsService:
    """The projection. Constructed from the components, owning nothing."""

    def __init__(self, *, planner_store, tasks, publisher=None, state_dir=None) -> None:
        # Everything is held by reference and read on demand. Nothing is copied
        # at construction, so a service built once at start-up never goes stale.
        self._store = planner_store
        self._tasks = tasks
        self._publisher = publisher
        self._state_dir = state_dir

    # -- reads ---------------------------------------------------------------

    def overview(self) -> List[ProjectOperations]:
        """Every registered project, most-needing-a-person first.

        Ordered by :func:`phases.rank` rather than by time, so the project that
        cannot move without you leads — see :mod:`.phases` on why that judgement
        lives there rather than in a client.
        """
        found = [self.project(project.project_id) for project in self._projects()]
        return sorted(found, key=lambda item: (item.phase.rank, item.project_id))

    def project(self, project_id: str) -> ProjectOperations:
        """One project, joined from whoever owns each fact.

        Scoped at selection: the planner request is chosen *by project*, and
        every later hop follows ids from that request. Nothing is read globally
        and filtered afterwards, which is what keeps two projects' work apart.
        """
        registered = self._project(project_id)
        display = getattr(registered, "display_name", None) or project_id

        record = self._latest_request(project_id)
        dispatch = None
        authority = None
        task = None
        reconciliation = None
        publication = None

        if record is not None:
            authority = self._authority(record.planner_request_id)
            dispatch = self._dispatch(record.planner_request_id)
        if dispatch is not None:
            task = self._task(dispatch.task_id)
            reconciliation = self._reconciliation(dispatch.dispatch_id)
            publication = self._publication(dispatch.dispatch_id)

        phase = phases.derive(
            planner_status=getattr(record, "status", None),
            planner_action=getattr(record, "action", None),
            authority_action=getattr(authority, "authority_action", None),
            task_state=getattr(task, "state", None),
            reconciliation_outcome=getattr(reconciliation, "outcome", None),
            reconciliation_needs_attention=bool(
                getattr(reconciliation, "needs_attention", 0)
            ),
            publication_state=getattr(publication, "state", None),
            publication_failure=getattr(publication, "failure_reason", None),
            has_commit=self._has_commit(reconciliation, publication, task),
        )

        return ProjectOperations(
            project_id=project_id,
            display_name=display,
            phase=phase,
            handles=self._handles(record, dispatch, publication),
            machine=self._machine(
                record, authority, task, reconciliation, publication
            ),
            claims=self._claims(record, task),
            actions=AVAILABLE_ACTIONS.get(phase.phase, ()),
        )

    # -- the three blocks ----------------------------------------------------

    def _handles(self, record, dispatch, publication) -> Dict[str, Any]:
        """Ids a control needs. No "latest" lookup anywhere downstream."""
        return {
            "planner_request_id": getattr(record, "planner_request_id", None),
            "dispatch_id": getattr(dispatch, "dispatch_id", None),
            "task_id": getattr(dispatch, "task_id", None),
            "publication_id": getattr(publication, "publication_id", None),
            # The prompt is not in this payload. This says whether asking for it
            # would return anything, so a client can offer the control without
            # every status read carrying the text -- see the module docstring.
            "prompt_available": bool(getattr(record, "worker_prompt", None)),
        }

    def _machine(self, record, authority, task, reconciliation, publication) -> Dict[str, Any]:
        """What **Cofferdam** observed. Never a word a model wrote.

        Every value here was either written by Cofferdam's own code or read out
        of Git by it. That is the whole distinction M2K's vocabulary rests on,
        and this block exists so a client cannot accidentally render a claim as
        an observation by reaching for a convenient top-level field.
        """
        machine: Dict[str, Any] = {
            "planner_status": getattr(record, "status", None),
            "planner_action": getattr(record, "action", None),
            "approved": getattr(authority, "authority_action", None) == "approve",
            "approved_at": getattr(authority, "recorded_at", None),
            "worker_state": getattr(task, "state", None),
            "worker_started_at": getattr(task, "started_at", None),
            "worker_completed_at": getattr(task, "completed_at", None),
        }
        if reconciliation is not None:
            machine["restart"] = {
                "occurred": True,
                "outcome": reconciliation.outcome,
                "checks_observed": bool(reconciliation.checks_observed),
                "check_exit_zero": (
                    None
                    if reconciliation.check_exit_zero is None
                    else bool(reconciliation.check_exit_zero)
                ),
                "recovered_commit": reconciliation.recovered_commit,
                "work_preserved": bool(reconciliation.worktree_retained),
            }
        else:
            machine["restart"] = {"occurred": False}

        if publication is not None:
            machine["publication"] = {
                "state": publication.state,
                "repository": publication.repository,
                "branch": publication.branch,
                "base_branch": publication.base_branch,
                "commit": publication.commit_sha,
                "pull_request": (
                    None
                    if publication.pull_request_number is None
                    else {
                        "number": publication.pull_request_number,
                        "url": publication.pull_request_url,
                        "state": publication.pull_request_state,
                    }
                ),
                "failure": publication.failure_reason,
            }
        else:
            machine["publication"] = None
            machine["branch"] = getattr(
                self._dispatch_branch(publication), "branch", None
            )
        # Stated in every payload rather than left for a reader to remember: a
        # finished worker is a finished process, and a passing check is one
        # command's exit status. Neither is an acceptance judgement.
        machine["worker_completion_is_not_acceptance"] = True
        return machine

    def _claims(self, record, task) -> Dict[str, Any]:
        """What a model said, labelled as such.

        Bounded, because a status read is not the place to carry a full result,
        and separated, because "the worker says the tests pass" and "Cofferdam
        ran the tests" are different sentences with different weight.
        """
        summary = getattr(record, "summary", None)
        result = getattr(task, "final_result", None)
        return {
            "planner_summary": _bounded(summary),
            "worker_report": _bounded(result),
            "source": "model_authored",
        }

    # -- joins, each scoped ---------------------------------------------------

    def _projects(self):
        registry = self._tasks.projects
        if callable(registry):  # pragma: no cover - a double may expose a method
            registry = registry()
        # `enabled_projects` rather than `projects`: a project the operator has
        # disabled is not something Cofferdam is doing anything about, and
        # listing it would put a permanently idle row in front of a person
        # every time they asked what was happening.
        for name in ("enabled_projects", "projects"):
            found = getattr(registry, name, None)
            if found is None:
                continue
            try:
                return list(found() if callable(found) else found)
            except Exception:  # pragma: no cover - defensive
                continue
        return []

    def _project(self, project_id: str):
        registry = self._tasks.projects
        if callable(registry):  # pragma: no cover
            registry = registry()
        try:
            return registry.get(project_id)
        except Exception:
            return None

    def _latest_request(self, project_id: str):
        """The most recent planner request **for this project**.

        Scoped in the selection rather than filtered after, so a busy host does
        not have to read every project's history to answer one project's
        question, and so a paging bug could never leak one into another.
        """
        lister = getattr(self._store, "recent_for_project", None)
        if lister is not None:
            found = lister(project_id, limit=1)
            return found[0] if found else None
        # Fall back to the general listing, still filtered by project.
        for record in self._store.recent(limit=200):
            if getattr(record, "project_id", None) == project_id:
                return record
        return None

    def _authority(self, planner_request_id: str):
        try:
            return self._store.authority_event(planner_request_id)
        except Exception:  # pragma: no cover - defensive
            return None

    def _dispatch(self, planner_request_id: str):
        try:
            return self._store.dispatch(planner_request_id)
        except Exception:  # pragma: no cover
            return None

    def _dispatch_branch(self, publication):  # pragma: no cover - trivial
        return publication

    def _task(self, task_id: str):
        for name in ("get_task", "get"):
            getter = getattr(self._tasks, name, None)
            if getter is None:
                continue
            try:
                return getter(task_id)
            except Exception:
                return None
        return None

    def _reconciliation(self, dispatch_id: str):
        getter = getattr(self._store, "reconciliation", None)
        if getter is None:  # pragma: no cover
            return None
        try:
            return getter(dispatch_id)
        except Exception:  # pragma: no cover
            return None

    def _publication(self, dispatch_id: str):
        getter = getattr(self._store, "publication", None)
        if getter is None:  # pragma: no cover
            return None
        try:
            return getter(dispatch_id)
        except Exception:  # pragma: no cover
            return None

    def _has_commit(self, reconciliation, publication, task) -> bool:
        """Whether there is a real commit to publish. **A machine fact.**

        Read from what Cofferdam recorded, never from a worker saying it
        committed: a publication carries the exact SHA it published, and a
        reconciliation carries the exact SHA a restart verified in Git.

        A task that merely reached ``completed`` is *not* treated as having one,
        because the adapter completes whether or not there was anything to
        commit — `commit_all` returning ``None`` is a real outcome. The publisher
        checks Git directly before it acts, and this view stays consistent with
        that rather than guessing ahead of it.
        """
        if publication is not None and publication.commit_sha:
            return True
        if reconciliation is not None and reconciliation.recovered_commit:
            return True
        return False


def _bounded(text: Optional[str], limit: int = 600) -> Optional[str]:
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = [
    "ACTION_ANSWER",
    "ACTION_APPROVE",
    "ACTION_CANCEL",
    "ACTION_INSPECT_PROMPT",
    "ACTION_INSPECT_RESULT",
    "ACTION_OPEN_PULL_REQUEST",
    "ACTION_PUBLISH",
    "ACTION_RECONCILE",
    "ACTION_REJECT",
    "ACTION_SIGN_IN",
    "AVAILABLE_ACTIONS",
    "OperationsService",
    "ProjectOperations",
]
