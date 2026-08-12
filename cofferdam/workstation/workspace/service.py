"""One producer for "what are we working on", and the boundary that keeps it honest.

Every route, and later the Context Builder and the planner, reads the workspace
snapshot from here rather than assembling one. That is not tidiness: a second
assembler is a second place for a *derived* fact to be written down, and the
single most important property of this component is that it never stores a copy
of somebody else's truth.

Persisted here versus derived on read
-------------------------------------

Persisted (:mod:`.store`) — things a person or Cofferdam decided:

    active workspace · objective and its history · active task *reference* ·
    plan checkpoint · pending decision reference · latest evidence reference ·
    expected next step

Derived every time (never written down):

    whether the workspace still exists and is enabled · whether its project
    exists and is enabled · the delegated adapter and its delegation status ·
    the active task's state, bucket and terminality · display names

The derived half is why this file exists. A stored ``task_state`` would be
correct for a few seconds and wrong afterwards, and nothing about it would
announce that it had gone stale — the exact failure Task Core's own restart
reconciliation exists to prevent, reintroduced one layer up.

Authority, restated for a component that could quietly take some
---------------------------------------------------------------

**Task Core owns tasks.** Working Context points at one. Whether it exists, what
state it is in, whether its session is alive: asked of Task Core, every read.
Nothing here creates, starts, continues, cancels or finishes a task, and setting
``expected_next_step`` to a sentence never causes anything to happen —
D-2026-08-11-11 is explicit that the loop stays human-directed.

**The project owns the worker.** ``delegated_worker`` in the snapshot is read
through the workspace's project to :meth:`~..tasks.projects.TaskProject.delegation`
and is never stored. PR #34 made that an explicit host decision after finding
ordering had been the authority; a workspace that could persist its own answer
would be a second authority with a stale copy, which is strictly worse than the
bug that was fixed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..tasks.errors import TaskUnknown
from ..tasks.models import TERMINAL_STATES, bucket_of
from ..tasks.projects import DELEGATION_OK
from .errors import (
    ActiveWorkspaceUnset,
    ContextFieldInvalid,
    TaskNotInWorkspace,
    WorkspaceProjectMissing,
    WorkspaceUnknown,
)
from .models import WorkspaceRegistry, load_workspaces, valid_workspace_id
from .store import SOURCE_USER, WorkspaceStore

#: The version of the workspace payload shape, published so a client can branch
#: on it rather than sniffing for keys — the same contract Task Core publishes.
WORKSPACE_API_VERSION = 1

#: What a stored task reference can resolve to. A closed vocabulary, because
#: "the task is gone" and "the task finished" are different sentences and a
#: client that has to infer the difference will infer it wrong.
TASK_REF_LIVE = "live"
TASK_REF_TERMINAL = "terminal"
TASK_REF_MISSING = "missing"


@dataclass(frozen=True)
class ActiveTaskView:
    """The active task as Cofferdam can honestly describe it right now.

    ``status`` says how much of this is real: ``live`` and ``terminal`` come from
    a task that exists, ``missing`` from a reference whose task Task Core does
    not have. A missing task keeps its id — the reference is still what somebody
    chose, and blanking it would hide that a task was deleted rather than
    reporting it.
    """

    task_id: str
    status: str
    state: Optional[str] = None
    bucket: Optional[str] = None
    terminal: Optional[bool] = None
    title: Optional[str] = None
    adapter_id: Optional[str] = None
    project_id: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "state": self.state,
            "bucket": self.bucket,
            "terminal": self.terminal,
            "title": self.title,
            "adapter_id": self.adapter_id,
            "project_id": self.project_id,
            "updated_at": self.updated_at,
        }


class WorkspaceService:
    """Resolves workspaces against projects and assembles the bounded snapshot.

    Takes callables rather than objects for its two dependencies so that a
    configuration reload is visible without reconstructing anything — the
    project registry is already reloadable that way, and a workspace file edited
    while the daemon runs should take effect the same way.
    """

    def __init__(
        self,
        config,
        store: WorkspaceStore,
        *,
        projects: Callable[[], Any],
        tasks: Optional[Any] = None,
    ) -> None:
        self._config = config
        self._store = store
        self._projects = projects
        self._tasks = tasks
        self._registry: Optional[WorkspaceRegistry] = None

    @property
    def store(self) -> WorkspaceStore:
        return self._store

    # -- configuration -------------------------------------------------------

    @property
    def workspaces(self) -> WorkspaceRegistry:
        if self._registry is None:
            self._registry = load_workspaces(self._config)
        return self._registry

    def reload_workspaces(self) -> WorkspaceRegistry:
        self._registry = load_workspaces(self._config)
        return self._registry

    def _project_for(self, workspace) -> Optional[Any]:
        """The workspace's project if it is usable, else ``None``.

        Disabled counts as unusable. A workspace over a project somebody turned
        off should report that rather than letting work be pointed at it.
        """
        registry = self._projects()
        for candidate in registry.projects:
            if candidate.project_id == workspace.project_id:
                return candidate if candidate.enabled else None
        return None

    def require_project(self, workspace) -> Any:
        project = self._project_for(workspace)
        if project is None:
            raise WorkspaceProjectMissing(
                "the project '" + workspace.project_id + "' is not configured or is disabled"
            )
        return project

    def list_workspaces(self) -> Dict[str, Any]:
        """Every configured workspace, with whether its project is usable.

        ``project_available`` is computed here rather than stored, because it is
        a fact about ``task-projects.json`` that can change without this file
        being touched.
        """
        registry = self.workspaces
        payload = registry.to_dict()
        payload["workspaces"] = [
            workspace.to_dict(project_available=self._project_for(workspace) is not None)
            for workspace in registry.enabled_workspaces()
        ]
        payload["active_workspace_id"] = self._store.active_workspace_id()
        payload["version"] = WORKSPACE_API_VERSION
        return payload

    # -- activation ----------------------------------------------------------

    def activate(self, workspace_id: object) -> Dict[str, Any]:
        """Make a configured workspace the active one, or refuse.

        Refuses before writing, in this order: the id must name a workspace that
        exists and is enabled, and that workspace's project must be usable.
        Activating a workspace whose project is missing would create a state
        where "what are we working on" names something nothing can run in, and
        the honest moment to say so is now rather than at the next task.
        """
        if not valid_workspace_id(workspace_id):
            raise WorkspaceUnknown()
        workspace = self.workspaces.get(workspace_id)
        self.require_project(workspace)
        self._store.set_active_workspace(workspace.workspace_id)
        return self.current()

    def deactivate(self) -> Dict[str, Any]:
        self._store.clear_active_workspace()
        return self.current()

    def _active_workspace(self):
        """The active workspace record, or ``None`` if unset or unresolvable.

        Deliberately total: a stored id naming a workspace that was renamed or
        removed is a real state of a host whose config file a person edits by
        hand, and a read must describe it rather than raise.
        """
        workspace_id = self._store.active_workspace_id()
        if workspace_id is None:
            return None, None
        return workspace_id, self.workspaces.find(workspace_id)

    def require_active_workspace(self):
        """The active workspace, or a refusal. For mutations only."""
        workspace_id, workspace = self._active_workspace()
        if workspace_id is None:
            raise ActiveWorkspaceUnset()
        if workspace is None:
            raise WorkspaceUnknown(
                "the active workspace '" + workspace_id + "' is no longer configured"
            )
        if not workspace.enabled:
            from .errors import WorkspaceDisabled

            raise WorkspaceDisabled()
        return workspace

    # -- working context -----------------------------------------------------

    def set_objective(self, objective: object, *, source: str = SOURCE_USER) -> Dict[str, Any]:
        workspace = self.require_active_workspace()
        self._store.set_objective(workspace.workspace_id, objective, source=source)
        return self.current()

    def update_context(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Set or clear bounded continuity fields on the active workspace.

        ``active_task_id`` is handled here rather than in the store because
        validating it needs Task Core and the project registry: the task must
        exist, and it must belong to *this* workspace's project.
        """
        workspace = self.require_active_workspace()
        payload = dict(fields)
        active_task: object = ...
        if "active_task_id" in payload:
            active_task = self._resolve_task_reference(workspace, payload.pop("active_task_id"))
        self._store.update_context(
            workspace.workspace_id, fields=payload, active_task_id=active_task
        )
        return self.current()

    def _resolve_task_reference(self, workspace, value: object) -> Optional[str]:
        """Validate a task the caller wants to point at, or refuse.

        Three refusals, and the third is the interesting one. An id that is not
        shaped like one, and an id Task Core does not have, are ordinary. An id
        belonging to a *different project* is refused because a workspace
        tracking another workspace's task is the cross-workspace confusion this
        whole model exists to remove — and it would be invisible afterwards,
        since the reference would render perfectly well.
        """
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ContextFieldInvalid("active_task_id", "expected a task id or null")
        task_id = value.strip()
        if self._tasks is None:
            raise ContextFieldInvalid(
                "active_task_id", "this build cannot resolve task references"
            )
        try:
            row = self._tasks.get_task(task_id)
        except TaskUnknown:
            raise ContextFieldInvalid("active_task_id", "no such task on this workstation")
        if row.project_id != workspace.project_id:
            raise TaskNotInWorkspace()
        return row.task_id

    # -- the snapshot --------------------------------------------------------

    def _active_task_view(self, task_id: Optional[str]) -> Optional[ActiveTaskView]:
        """Resolve a stored reference against Task Core, right now.

        Never cached and never stored. A terminal task stays visible with
        ``status: terminal``: it finished, which is a fact somebody came back to
        read, and clearing the reference on completion would delete the answer at
        the moment it became interesting.
        """
        if not task_id:
            return None
        if self._tasks is None:
            return ActiveTaskView(task_id=task_id, status=TASK_REF_MISSING)
        try:
            row = self._tasks.get_task(task_id)
        except TaskUnknown:
            return ActiveTaskView(task_id=task_id, status=TASK_REF_MISSING)
        except Exception:  # pragma: no cover - a store failure is not a workspace failure
            return ActiveTaskView(task_id=task_id, status=TASK_REF_MISSING)
        terminal = row.state in TERMINAL_STATES
        return ActiveTaskView(
            task_id=row.task_id,
            status=TASK_REF_TERMINAL if terminal else TASK_REF_LIVE,
            state=row.state,
            bucket=bucket_of(row.state),
            terminal=terminal,
            title=row.title,
            adapter_id=row.adapter_id,
            project_id=row.project_id,
            updated_at=row.updated_at,
        )

    def current(self) -> Dict[str, Any]:
        """The bounded snapshot: one workspace, its context, and derived truth.

        Never raises for an unconfigured or unresolvable state. "No workspace is
        active" and "the active workspace is no longer configured" are both
        answers a client must be able to render, and turning either into an error
        would mean a host in a perfectly ordinary state had a broken endpoint.
        """
        workspace_id, workspace = self._active_workspace()
        payload: Dict[str, Any] = {
            "version": WORKSPACE_API_VERSION,
            "active": workspace is not None and workspace.enabled,
            "workspace": None,
            "working_context": None,
            "problem": None,
        }

        if workspace_id is None:
            payload["problem"] = "no_active_workspace"
            payload["configured"] = len(self.workspaces.workspaces)
            return payload

        if workspace is None:
            # Stored, and no longer in the file. Reported with the id so somebody
            # can see which name to restore, and without inventing a substitute.
            payload["problem"] = "active_workspace_unconfigured"
            payload["workspace"] = {"workspace_id": workspace_id}
            return payload

        project = self._project_for(workspace)
        delegated_adapter = None
        delegation = None
        if project is not None:
            delegated_adapter, delegation = project.delegation()

        payload["workspace"] = {
            "workspace_id": workspace.workspace_id,
            "display_name": workspace.display_name,
            "enabled": workspace.enabled,
            "notes": workspace.notes,
            "project_id": workspace.project_id,
            # Derived, every read. The project's display name is in its file, and
            # a copy here would go stale the first time somebody renamed it.
            "project_display_name": project.display_name if project is not None else None,
            "project_available": project is not None,
            # Derived from the project, never stored. See the module docstring:
            # a persisted worker would be a second adapter authority.
            "delegated_worker": delegated_adapter,
            "delegation": delegation,
            "worker_available": delegation == DELEGATION_OK,
        }
        if not workspace.enabled:
            payload["problem"] = "active_workspace_disabled"
        elif project is None:
            payload["problem"] = "active_workspace_project_missing"

        row = self._store.context(workspace.workspace_id)
        active_task = self._active_task_view(row.active_task_id)
        payload["working_context"] = {
            "workspace_id": row.workspace_id,
            "objective": row.objective,
            "objective_set_at": row.objective_set_at,
            "objective_source": row.objective_source,
            "active_task": active_task.to_dict() if active_task is not None else None,
            "plan_checkpoint": row.plan_checkpoint,
            "pending_decision_ref": row.pending_decision_ref,
            "latest_evidence_ref": row.latest_evidence_ref,
            "expected_next_step": row.expected_next_step,
            "revision": row.revision,
            "updated_at": row.updated_at,
            # Said in the payload rather than only in the docs, the way Task Core
            # publishes its own limitations — a client should never have to infer
            # how much of this is real.
            "limitations": list(LIMITATIONS),
        }
        return payload

    def objective_history(self, *, limit: int = 20) -> Dict[str, Any]:
        workspace = self.require_active_workspace()
        entries = self._store.objective_history(workspace.workspace_id, limit=limit)
        return {
            "version": WORKSPACE_API_VERSION,
            "workspace_id": workspace.workspace_id,
            "history": [entry.to_dict() for entry in entries],
        }

    def health(self) -> Dict[str, Any]:
        registry = self.workspaces
        return {
            "store": self._store.health(),
            "configured": len(registry.workspaces),
            "problems": len(registry.problems),
            "source_present": registry.source_present,
            "active": self._store.active_workspace_id() is not None,
        }


#: What this PR can and cannot say, published in the payload. These are facts
#: about a foundation, not apologies for unfinished work.
LIMITATIONS = (
    "Working Context records what was chosen; Task Core remains the authority for task state.",
    "The delegated worker is derived from the workspace's project and is never stored here.",
    "Plan, decision and evidence references are opaque in this milestone — nothing resolves them yet.",
    "An expected next step is a note for a person. Cofferdam never executes it.",
)


__all__ = [
    "ActiveTaskView",
    "LIMITATIONS",
    "TASK_REF_LIVE",
    "TASK_REF_MISSING",
    "TASK_REF_TERMINAL",
    "WORKSPACE_API_VERSION",
    "WorkspaceService",
]
