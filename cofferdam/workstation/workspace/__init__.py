"""Workspaces and Working Context: what are we working on right now.

Every surface in Cofferdam has so far answered that question by re-deriving it
per request. The PWA knows because somebody is looking at a task list. The
Custom GPT knows because a conversation mentioned a project. Nothing *owned* the
answer, so nothing could be asked for it, and nothing survived a restart.

This package owns it. Two halves, deliberately different in kind:

* :mod:`.models` — the **workspace**, host-owned configuration beside the M2A
  registries and ``task-projects.json``. It binds a stable workspace id to a
  project that already exists. It is edited in a text editor on the workstation
  and there is no route that writes it, for the same reason there is no route
  that writes a project.
* :mod:`.store` — the **Working Context**, Cofferdam-owned durable *state*. The
  active workspace, the current objective and its history, and a small set of
  bounded continuity references. SQLite under ``state/``, because it is state
  and not memory: D-2026-08-11-3 is explicit that this must never become a
  second Markdown authority.

What this package will never be
-------------------------------

**A second task lifecycle.** Working Context can point at the active task; Task
Core remains the authority for whether that task exists, what state it is in,
and whether its session is still there. Every read here re-derives those facts
rather than storing a copy — see :mod:`.service`.

**A second adapter authority.** A workspace names a project; the project names
the adapters and, since PR #34, which one is delegated. A workspace entry
carrying ``adapters``, ``delegated_adapter``, ``model`` or ``provider`` is
refused *by name*, because a configuration file that could select an agent is
exactly the ordering-is-authority mistake M2I.5 PR3 removed.

**A path authority.** A workspace has no ``root``. It reaches a directory only
through the project it names, which is where root validation already lives.
"""

from .errors import (
    CODE_ACTIVE_WORKSPACE_UNSET,
    CODE_CONTEXT_FIELD_INVALID,
    CODE_TASK_NOT_IN_WORKSPACE,
    CODE_WORKSPACE_DISABLED,
    CODE_WORKSPACE_PROJECT_MISSING,
    CODE_WORKSPACE_UNKNOWN,
    ActiveWorkspaceUnset,
    ContextFieldInvalid,
    TaskNotInWorkspace,
    WorkspaceDisabled,
    WorkspaceError,
    WorkspaceProjectMissing,
    WorkspaceUnknown,
)
from .models import (
    FORBIDDEN_WORKSPACE_FIELDS,
    MAX_WORKSPACES,
    MAX_WORKSPACE_ID_CHARS,
    WORKSPACES_FILENAME,
    Workspace,
    WorkspaceRegistry,
    load_workspaces,
    valid_workspace_id,
)
from .service import WorkspaceService
from .store import (
    MAX_OBJECTIVE_CHARS,
    MAX_OBJECTIVE_HISTORY,
    MAX_REFERENCE_CHARS,
    SCHEMA_VERSION,
    WorkingContextRow,
    WorkspaceStore,
)

__all__ = [
    "ActiveWorkspaceUnset",
    "CODE_ACTIVE_WORKSPACE_UNSET",
    "CODE_CONTEXT_FIELD_INVALID",
    "CODE_TASK_NOT_IN_WORKSPACE",
    "CODE_WORKSPACE_DISABLED",
    "CODE_WORKSPACE_PROJECT_MISSING",
    "CODE_WORKSPACE_UNKNOWN",
    "ContextFieldInvalid",
    "FORBIDDEN_WORKSPACE_FIELDS",
    "MAX_OBJECTIVE_CHARS",
    "MAX_OBJECTIVE_HISTORY",
    "MAX_REFERENCE_CHARS",
    "MAX_WORKSPACES",
    "MAX_WORKSPACE_ID_CHARS",
    "SCHEMA_VERSION",
    "TaskNotInWorkspace",
    "WORKSPACES_FILENAME",
    "WorkingContextRow",
    "Workspace",
    "WorkspaceDisabled",
    "WorkspaceError",
    "WorkspaceProjectMissing",
    "WorkspaceRegistry",
    "WorkspaceService",
    "WorkspaceStore",
    "WorkspaceUnknown",
    "load_workspaces",
    "valid_workspace_id",
]
