"""Refusals at the native-session boundary.

Same shape as the Task Core refusals in :mod:`..tasks.errors` — a stable code, a
sentence for a person, an optional detail — and deliberately a *separate* set of
codes. A native Remote Control host is not a task, and a client that learned to
branch on ``task_project_unknown`` for one and the other would be treating two
different lanes as one thing.

Nothing here carries a path, a journal excerpt, an environment value or a
command line. The detail strings are written in this file and are constants; the
only variable a refusal ever interpolates is a project id, which is already
constrained to lowercase letters, digits, dash and underscore by
:func:`..tasks.projects.valid_project_id`.
"""

from __future__ import annotations

from typing import Optional

# -- authority ---------------------------------------------------------------

CODE_PROJECT_UNKNOWN = "remote_control_project_unknown"
CODE_PROJECT_DISABLED = "remote_control_project_disabled"
CODE_NOT_ENABLED = "remote_control_not_enabled_for_project"
CODE_PROJECT_ROOT_INVALID = "remote_control_project_root_invalid"

# -- backend -----------------------------------------------------------------

CODE_BACKEND_UNAVAILABLE = "remote_control_backend_unavailable"
CODE_BACKEND_REFUSED = "remote_control_backend_refused"

# -- host entry point --------------------------------------------------------

CODE_EXECUTABLE_MISSING = "remote_control_executable_missing"


class RemoteControlError(Exception):
    """A refusal a person should see, with a stable code to branch on."""

    def __init__(self, code: str, message: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class SessionProjectUnknown(RemoteControlError):
    def __init__(self) -> None:
        super().__init__(
            CODE_PROJECT_UNKNOWN,
            "that project is not configured on this workstation",
            "projects are configured on the host, not chosen from a phone",
        )


class SessionProjectDisabled(RemoteControlError):
    def __init__(self) -> None:
        super().__init__(
            CODE_PROJECT_DISABLED,
            "that project is turned off",
            "enable it in the task project configuration on the workstation",
        )


class RemoteControlNotEnabled(RemoteControlError):
    """The project exists and is enabled, but has not opted in to Lane A.

    Separate from :class:`SessionProjectDisabled` on purpose. "The project is
    off" and "the project is on but may not host an interactive Claude session"
    are different sentences with different fixes, and collapsing them would mean
    the one thing somebody needs to know — which line to add — is missing.
    """

    def __init__(self) -> None:
        super().__init__(
            CODE_NOT_ENABLED,
            "that project has not enabled native Remote Control",
            'set "remote_control_enabled": true for it in the host project configuration',
        )


class SessionRootInvalid(RemoteControlError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_PROJECT_ROOT_INVALID,
            "that project's folder cannot be used right now",
            detail or "the configured folder is missing, unreadable, or not a real directory",
        )


class BackendUnavailable(RemoteControlError):
    """``systemctl`` could not be run at all, or did not answer in time."""

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_BACKEND_UNAVAILABLE,
            "the session supervisor cannot be reached right now",
            detail or "the user service manager did not answer",
        )


class BackendRefused(RemoteControlError):
    """``systemctl`` ran and returned non-zero."""

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_BACKEND_REFUSED,
            "the session supervisor refused that",
            detail or "the user service manager reported a failure",
        )


class ExecutableMissing(RemoteControlError):
    def __init__(self) -> None:
        super().__init__(
            CODE_EXECUTABLE_MISSING,
            "the Claude command is not installed where this workstation looks for it",
            "install it for this user, or make it reachable on PATH",
        )


__all__ = [
    "CODE_BACKEND_REFUSED",
    "CODE_BACKEND_UNAVAILABLE",
    "CODE_EXECUTABLE_MISSING",
    "CODE_NOT_ENABLED",
    "CODE_PROJECT_DISABLED",
    "CODE_PROJECT_ROOT_INVALID",
    "CODE_PROJECT_UNKNOWN",
    "BackendRefused",
    "BackendUnavailable",
    "ExecutableMissing",
    "RemoteControlError",
    "RemoteControlNotEnabled",
    "SessionProjectDisabled",
    "SessionProjectUnknown",
    "SessionRootInvalid",
]
