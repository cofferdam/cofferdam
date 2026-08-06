"""Refusals at the Task Core boundary.

Every code here names something Cofferdam checked and declined, and each maps to
a different thing for a person to do next — which is the test for whether a code
deserves to exist. "That project is not configured" and "that project's root has
moved" are the same HTTP status and completely different problems.

Two groups deserve reading together.

**Authority refusals** are about what the request tried to name: an unknown
project, a disabled adapter, a root that is no longer where the registry says.
They fail closed, and none of them falls back to a default — a task that ran
somewhere other than where its project points would be worse than a task that
did not run.

**Lifecycle refusals** are about *when*: a follow-up to a finished task, a
cancel of something already cancelled, a transition the graph does not contain.
They are refusals rather than silent no-ops because a client that gets a silent
success learns to keep sending.
"""

from __future__ import annotations

from typing import Optional

# -- authority ---------------------------------------------------------------

CODE_PROJECT_UNKNOWN = "task_project_unknown"
CODE_PROJECT_DISABLED = "task_project_disabled"
CODE_PROJECT_ROOT_INVALID = "task_project_root_invalid"
CODE_ADAPTER_UNKNOWN = "task_adapter_unknown"
CODE_ADAPTER_DISABLED = "task_adapter_disabled"
CODE_ADAPTER_NOT_PERMITTED = "task_adapter_not_permitted_for_project"

# -- request shape -----------------------------------------------------------

CODE_PROMPT_INVALID = "task_prompt_invalid"
CODE_FOLLOWUP_INVALID = "task_followup_invalid"
CODE_REQUEST_ID_INVALID = "task_request_id_invalid"
CODE_IDEMPOTENCY_CONFLICT = "task_idempotency_conflict"

# -- lifecycle ---------------------------------------------------------------

CODE_TASK_UNKNOWN = "task_unknown"
CODE_ILLEGAL_TRANSITION = "task_illegal_transition"
CODE_TASK_TERMINAL = "task_already_finished"
CODE_FOLLOWUP_UNSUPPORTED = "task_followup_unsupported"
CODE_FOLLOWUP_NOT_WAITING = "task_not_waiting_for_input"
CODE_CANCEL_UNSUPPORTED = "task_cancel_unsupported"
CODE_ADAPTER_FAILED = "task_adapter_failed"
CODE_STORE_UNAVAILABLE = "task_store_unavailable"


class TaskError(Exception):
    """A refusal a person should see, with a stable code to branch on.

    Deliberately not an HTTP concern: the service layer maps codes to statuses,
    the same shape the audio, Spotify and YouTube boundaries already use.
    """

    def __init__(self, code: str, message: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class ProjectUnknown(TaskError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_PROJECT_UNKNOWN,
            "that project is not configured on this workstation",
            detail or "projects are configured on the host, not chosen from a phone",
        )


class ProjectDisabled(TaskError):
    def __init__(self) -> None:
        super().__init__(
            CODE_PROJECT_DISABLED,
            "that project is turned off",
            "enable it in the task project configuration on the workstation",
        )


class ProjectRootInvalid(TaskError):
    """The configured root is not somewhere a task may run *right now*.

    Checked at task creation rather than only at load, because a directory can
    be deleted, replaced with a symlink, or moved between the two moments, and
    the check that matters is the one closest to the work.
    """

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_PROJECT_ROOT_INVALID,
            "that project's folder cannot be used right now",
            detail or "the configured folder is missing, unreadable, or not a real directory",
        )


class AdapterUnknown(TaskError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_ADAPTER_UNKNOWN,
            "that task adapter is not available on this workstation",
            detail or "adapters are registered in code and enabled on the host",
        )


class AdapterNotPermitted(TaskError):
    def __init__(self) -> None:
        super().__init__(
            CODE_ADAPTER_NOT_PERMITTED,
            "that adapter is not enabled for that project",
            "each project lists the adapters it allows",
        )


class PromptInvalid(TaskError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_PROMPT_INVALID, "that prompt cannot be accepted", detail
        )


class FollowupInvalid(TaskError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_FOLLOWUP_INVALID, "that follow-up cannot be accepted", detail
        )


class RequestIdInvalid(TaskError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_REQUEST_ID_INVALID,
            "that request id cannot be accepted",
            detail or "a client request id is short opaque text of the client's choosing",
        )


class IdempotencyConflict(TaskError):
    """Same key, different request.

    The one idempotency case that must never be resolved by guessing. Returning
    the first task would answer a question the client did not ask; creating a
    second would defeat the key. Both are worse than saying so.
    """

    def __init__(self) -> None:
        super().__init__(
            CODE_IDEMPOTENCY_CONFLICT,
            "that request id was already used for a different request",
            "use a new request id, or send the original request again unchanged",
        )


class TaskUnknown(TaskError):
    def __init__(self) -> None:
        super().__init__(
            CODE_TASK_UNKNOWN, "no such task", "it may have been created on another host"
        )


class IllegalTransitionError(TaskError):
    def __init__(self, current: str, requested: str, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_ILLEGAL_TRANSITION,
            "that is not something this task can do now",
            detail or ("the task is " + current + " and cannot become " + requested),
        )
        self.current = current
        self.requested = requested


class TaskAlreadyFinished(TaskError):
    def __init__(self, state: str) -> None:
        super().__init__(
            CODE_TASK_TERMINAL,
            "that task is already " + state,
            "a finished task cannot be changed; start a new task instead",
        )
        self.state = state


class FollowupUnsupported(TaskError):
    def __init__(self, adapter_id: str) -> None:
        super().__init__(
            CODE_FOLLOWUP_UNSUPPORTED,
            "that adapter does not accept follow-up messages",
            "the " + adapter_id + " adapter declares no follow-up capability",
        )


class FollowupNotWaiting(TaskError):
    def __init__(self, state: str) -> None:
        super().__init__(
            CODE_FOLLOWUP_NOT_WAITING,
            "that task is not waiting for an answer",
            "it is " + state + "; a follow-up is accepted only while it is waiting",
        )


class CancelUnsupported(TaskError):
    def __init__(self, adapter_id: str) -> None:
        super().__init__(
            CODE_CANCEL_UNSUPPORTED,
            "that adapter cannot cancel a running task",
            "the " + adapter_id + " adapter declares no cancel capability",
        )


class AdapterFailed(TaskError):
    """The adapter refused or could not do what was asked.

    Its message is Cofferdam's, not the adapter's: an adapter is not trusted to
    write the sentence a person reads, and a raw exception string is exactly the
    kind of thing that ends up carrying a path or a value it should not.
    """

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_ADAPTER_FAILED,
            "the task adapter could not do that",
            detail,
        )


class StoreUnavailable(TaskError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_STORE_UNAVAILABLE,
            "the task store cannot be reached",
            detail or "tasks are unavailable until it can be opened again",
        )


__all__ = [
    "CODE_ADAPTER_DISABLED",
    "CODE_ADAPTER_FAILED",
    "CODE_ADAPTER_NOT_PERMITTED",
    "CODE_ADAPTER_UNKNOWN",
    "CODE_CANCEL_UNSUPPORTED",
    "CODE_FOLLOWUP_INVALID",
    "CODE_FOLLOWUP_NOT_WAITING",
    "CODE_FOLLOWUP_UNSUPPORTED",
    "CODE_IDEMPOTENCY_CONFLICT",
    "CODE_ILLEGAL_TRANSITION",
    "CODE_PROJECT_DISABLED",
    "CODE_PROJECT_ROOT_INVALID",
    "CODE_PROJECT_UNKNOWN",
    "CODE_PROMPT_INVALID",
    "CODE_REQUEST_ID_INVALID",
    "CODE_STORE_UNAVAILABLE",
    "CODE_TASK_TERMINAL",
    "CODE_TASK_UNKNOWN",
    "AdapterFailed",
    "AdapterNotPermitted",
    "AdapterUnknown",
    "CancelUnsupported",
    "FollowupInvalid",
    "FollowupNotWaiting",
    "FollowupUnsupported",
    "IdempotencyConflict",
    "IllegalTransitionError",
    "ProjectDisabled",
    "ProjectRootInvalid",
    "ProjectUnknown",
    "PromptInvalid",
    "RequestIdInvalid",
    "StoreUnavailable",
    "TaskAlreadyFinished",
    "TaskError",
    "TaskUnknown",
]
