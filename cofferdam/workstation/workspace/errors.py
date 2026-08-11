"""Refusals a person should see, with stable codes to branch on.

The same shape as :mod:`..tasks.errors`: not an HTTP concern, because the
service layer maps a code to a status exactly once and every other boundary in
this product already works that way.
"""

from __future__ import annotations

from typing import Optional

#: The workspace id is not configured on this host. Never a fallback to another
#: one — an invalid selection resolving to *some* workspace is how work lands in
#: the wrong project.
CODE_WORKSPACE_UNKNOWN = "workspace_unknown"

#: Configured, and switched off. Distinct from unknown so the operator can tell
#: "I never wrote that down" from "I turned that off".
CODE_WORKSPACE_DISABLED = "workspace_disabled"

#: The workspace names a project that the project registry does not have, or
#: that is disabled. The workspace loads and stays visible — see
#: :class:`~.models.Workspace` — but nothing can be activated in it.
CODE_WORKSPACE_PROJECT_MISSING = "workspace_project_missing"

#: Something asked for the current workspace and there is not one. An answer,
#: not a failure: a host with no workspace configured is the shipped default.
CODE_ACTIVE_WORKSPACE_UNSET = "workspace_active_unset"

#: A Working Context field was malformed, too long, or of the wrong type.
CODE_CONTEXT_FIELD_INVALID = "workspace_context_field_invalid"

#: The task exists but belongs to a different project than this workspace does.
#: Refused rather than accepted, because a Working Context pointing at another
#: workspace's task is precisely the cross-workspace confusion this model exists
#: to remove.
CODE_TASK_NOT_IN_WORKSPACE = "workspace_task_not_in_workspace"


class WorkspaceError(Exception):
    """A refusal with a stable code, message and optional detail."""

    def __init__(self, code: str, message: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class WorkspaceUnknown(WorkspaceError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_WORKSPACE_UNKNOWN,
            "that workspace is not configured on this workstation",
            detail or "workspaces are configured on the host, not created from a client",
        )


class WorkspaceDisabled(WorkspaceError):
    def __init__(self) -> None:
        super().__init__(
            CODE_WORKSPACE_DISABLED,
            "that workspace is turned off",
            "enable it in the workspace configuration on the workstation",
        )


class WorkspaceProjectMissing(WorkspaceError):
    """The workspace points at a project that cannot be used right now.

    Deliberately its own code rather than reusing ``task_project_unknown``. The
    workspace is fine; the thing it names is not, and telling somebody their
    workspace is unknown when the real problem is a renamed project sends them
    to edit the wrong file.
    """

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_WORKSPACE_PROJECT_MISSING,
            "that workspace names a project this workstation cannot use",
            detail or "check the project id in the workspace configuration",
        )


class ActiveWorkspaceUnset(WorkspaceError):
    def __init__(self) -> None:
        super().__init__(
            CODE_ACTIVE_WORKSPACE_UNSET,
            "no workspace is active",
            "activate one of the configured workspaces first",
        )


class ContextFieldInvalid(WorkspaceError):
    def __init__(self, field: str, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_CONTEXT_FIELD_INVALID,
            "that working context value cannot be stored as written",
            detail or ("the field " + field + " is missing, too long, or the wrong type"),
        )
        self.field = field


class TaskNotInWorkspace(WorkspaceError):
    def __init__(self) -> None:
        super().__init__(
            CODE_TASK_NOT_IN_WORKSPACE,
            "that task belongs to a different project than this workspace",
            "a workspace tracks work in its own project only",
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
    "TaskNotInWorkspace",
    "WorkspaceDisabled",
    "WorkspaceError",
    "WorkspaceProjectMissing",
    "WorkspaceRegistryError",
    "WorkspaceUnknown",
]

#: Alias kept for symmetry with the tasks package, where the loader's problems
#: are data rather than exceptions. Nothing raises this; it exists so an import
#: of it fails loudly rather than silently succeeding against a typo.
WorkspaceRegistryError = WorkspaceError
