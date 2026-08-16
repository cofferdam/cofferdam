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

# -- clarifications ----------------------------------------------------------
#
# Five codes for one small surface, and each one sends somebody somewhere
# different. "That question is not open any more" and "that answer is not the
# shape the question wanted" are the same HTTP status and completely different
# problems — one means reload, the other means retype.
#
# None of them is a tool-approval code, and there is no tool-approval code in
# this module at all. A tool approval is not refused through this API; it has no
# route to be refused *by*.

CODE_CLARIFICATION_UNKNOWN = "task_clarification_unknown"
CODE_CLARIFICATION_CLOSED = "task_clarification_closed"
CODE_CLARIFICATION_INVALID = "task_clarification_invalid"
CODE_CLARIFICATION_UNSUPPORTED = "task_clarification_unsupported"
CODE_CLARIFICATION_NOT_DELIVERED = "task_clarification_not_delivered"

# -- turns, results and same-session follow-up (M2I PR3) ----------------------
#
# Five codes, and the reason there are five rather than one "cannot do that" is
# the reason this milestone exists. A phone showing "follow-up unavailable" has
# told somebody nothing. Each of these sends them somewhere different: wait,
# answer the question that is open, decide something at the workstation, start a
# new task, or accept that this conversation is over.

#: The task is real and has produced nothing to return yet.
CODE_RESULT_NOT_READY = "task_result_not_ready"
#: A question is open. The clarification route is the way forward, not this one.
CODE_CLARIFICATION_PENDING = "task_clarification_pending"
#: The provider session this task owns is gone — the helper died, or the daemon
#: restarted. Distinct from "the task finished": the work may have succeeded and
#: its result is still retrievable; what is unavailable is *continuing* it.
CODE_SESSION_UNAVAILABLE = "task_session_unavailable"
#: A follow-up is already being delivered. At most one turn per task, ever.
CODE_FOLLOWUP_IN_FLIGHT = "task_followup_in_flight"
#: This task has had as many turns as one task may have.
CODE_TURN_LIMIT_REACHED = "task_turn_limit_reached"
#: The acceptance criteria supplied for this turn will not be stored (M2K PR6).
CODE_CRITERIA_INVALID = "task_criteria_invalid"
#: Criteria were supplied, could not be made durable, and the dispatch stopped
#: rather than running a worker against requirements nobody recorded.
CODE_CRITERIA_UNRECORDED = "task_criteria_unrecorded"
CODE_CONTINUITY_UNRECORDED = "task_continuity_unrecorded"
CODE_CONTINUITY_INVALID = "task_continuity_invalid"
#: A second evaluation of one turn disagreed with the stored one (M2K PR7).
CODE_EVALUATION_CONFLICT = "task_evaluation_conflict"


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


class ClarificationUnknown(TaskError):
    """No such question on that task.

    Deliberately the same answer for "that id does not exist" and "that id
    belongs to a different task", because they are the same fact from the
    client's side and distinguishing them would let somebody learn which
    question ids exist elsewhere by watching which refusal came back.
    """

    def __init__(self) -> None:
        super().__init__(
            CODE_CLARIFICATION_UNKNOWN,
            "no such question on this task",
            "it may already have been cleared, or it belongs to another task",
        )


class ClarificationClosed(TaskError):
    """That question is no longer waiting for an answer."""

    def __init__(self, status: str) -> None:
        super().__init__(
            CODE_CLARIFICATION_CLOSED,
            "that question is already " + status,
            "a question is answered once; open the task to see what happened next",
        )
        self.status = status


class ClarificationAnswerInvalid(TaskError):
    """The answer was not the shape the question asked for.

    Its detail is the only place a clarification refusal explains itself in more
    than a sentence, and it never repeats the submitted answer back — an error
    response that echoed rejected input would be a way to get arbitrary text into
    whatever renders it.
    """

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_CLARIFICATION_INVALID, "that answer cannot be accepted", detail
        )


class ClarificationUnsupported(TaskError):
    """This task's adapter does not ask questions, so it cannot be answered."""

    def __init__(self, adapter_id: str) -> None:
        super().__init__(
            CODE_CLARIFICATION_UNSUPPORTED,
            "that adapter does not ask questions",
            "the " + adapter_id + " adapter declares no clarification capability",
        )


class ClarificationNotDelivered(TaskError):
    """The answer was valid and the provider did not take it.

    A refusal rather than a silent success, and the task is left where it is.
    Recording a question as answered when the session never received the answer
    would be the false success this design exists to refuse — the person would
    see their answer accepted and the agent would sit there waiting for it.
    """

    def __init__(self) -> None:
        # No product named, and that is a rule rather than a style choice: this
        # module is provider-neutral and a guard asserts it. Which agent was
        # asked is the adapter's business, and a sentence naming one would have
        # to be rewritten for every transport that ever reaches this code.
        super().__init__(
            CODE_CLARIFICATION_NOT_DELIVERED,
            "the agent did not take that answer",
            "the session may have moved on or stopped; open the task to see",
        )


class ResultNotReady(TaskError):
    """The task exists and has produced nothing to return yet.

    Distinct from "no such task" and distinct from a failure. A client that gets
    this should come back, and the state name in the detail tells it whether
    coming back is likely to help.
    """

    def __init__(self, state: str) -> None:
        super().__init__(
            CODE_RESULT_NOT_READY,
            "this task has no result yet",
            "it is " + state,
        )


class ClarificationPending(TaskError):
    """A question is open, so a follow-up is refused rather than queued.

    The two are different acts and this is the boundary between them. Somebody
    who sends a new instruction while the agent is blocked on a question has not
    answered the question, and delivering it as though they had would put words
    in their mouth at the one moment the agent is waiting to be told something
    specific. Answer it, or cancel the task.
    """

    def __init__(self) -> None:
        super().__init__(
            CODE_CLARIFICATION_PENDING,
            "this task is waiting for an answer to a question",
            "answer the open question instead of sending a new message",
        )


class SessionUnavailable(TaskError):
    """The provider session this task owned is gone.

    Truthful about what is lost and what is not. The task's result, if it
    produced one, is still there and still retrievable — what cannot happen is
    another turn, because the conversation it would continue no longer exists.
    There is no recovery by session id in this build, and inventing one would
    mean claiming a context nobody has verified survives.
    """

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_SESSION_UNAVAILABLE,
            "this task's session is no longer available",
            detail or "start a new task; any result it produced is still readable",
        )


class FollowupInFlight(TaskError):
    """One follow-up is already being delivered.

    A second concurrent message would become a second provider turn on one
    conversation, interleaved at a point nobody chose. Refused rather than
    queued: a queue here would mean accepting something now and delivering it
    into a context that has since changed.
    """

    def __init__(self) -> None:
        super().__init__(
            CODE_FOLLOWUP_IN_FLIGHT,
            "a follow-up is already being delivered to this task",
            "wait for it to finish before sending another",
        )


class TurnLimitReached(TaskError):
    """This task has had as many turns as one task may have."""

    def __init__(self) -> None:
        super().__init__(
            CODE_TURN_LIMIT_REACHED,
            "this conversation has gone on as long as one task may",
            "start a new task to carry on",
        )


class CriteriaInvalid(TaskError):
    """Acceptance criteria that will not be stored (M2K PR6).

    Raised **before** anything durable is written and before any adapter is
    invoked, so a refused criteria set leaves no task half-started and no worker
    running against requirements Cofferdam declined to record.

    ``detail`` is a closed reason code from
    :mod:`~cofferdam.workstation.tasks.criteria` and a criterion position at
    most. It never echoes the submitted value: a refusal that repeats a rejected
    path is a way to describe the host's filesystem one attempt at a time, and a
    refusal that repeats a rejected description hands the submission back.
    """

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_CRITERIA_INVALID,
            "those acceptance criteria cannot be accepted",
            detail,
        )


class CriteriaUnrecorded(TaskError):
    """Criteria were supplied and could not be made durable before dispatch.

    The dispatch stops here rather than continuing. This is the one place where
    Task Core treats an evidence-adjacent write as fatal, and the asymmetry is
    deliberate: a missing Git baseline costs a later observation, while a missing
    criteria snapshot would leave a worker running against requirements no future
    evaluation can ever see — which is the silent disappearance of acceptance
    criteria that :data:`~cofferdam.workstation.tasks.criteria.MAX_CRITERIA_PER_TURN`
    exists to prevent, arriving by a different door.

    A turn for which **no** criteria were supplied does not take this path: there
    is nothing to lose, and a store that cannot write ``not_provided`` will fail
    the task at its next transition anyway.
    """

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_CRITERIA_UNRECORDED,
            "the acceptance criteria for this turn could not be recorded",
            detail or "nothing was dispatched; the task was not started",
        )


class ContinuityInvalid(TaskError):
    """A continuity declaration Cofferdam will not store, with a closed reason.

    Raised before anything durable is written and before the adapter is reached,
    so a refused declaration never leaves a worker running against lineage that
    was rejected. Covers both halves of the check: the structural one in
    :func:`~.continuity.validate_declaration`, and the relational one in
    :meth:`~.store.TaskStore.reserve_turn_continuity` that needs the database to
    decide — an unknown predecessor, one belonging to another task or a later
    turn, or a superseded criterion that is not in the predecessor snapshot.

    The detail is a closed reason code. The submitted value never travels back
    out, exactly as :class:`CriteriaInvalid` keeps criteria text out of a refusal.
    """

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_CONTINUITY_INVALID,
            "that criteria continuity declaration cannot be accepted",
            detail,
        )


class ContinuityUnrecorded(TaskError):
    """A turn's criteria continuity could not be made durable before dispatch.

    Fatal for the same reason :class:`CriteriaUnrecorded` is, and the reasoning
    transfers without weakening. A worker dispatched against unrecorded lineage
    produces a turn whose relationship to every earlier turn is unknowable
    afterwards — and unlike a missing Git baseline, nothing can reconstruct it,
    because the fact was an intent rather than an observation.

    It is also raised when a declaration is **refused**: an unknown predecessor,
    a predecessor from another task or a later turn, a superseded criterion that
    is not in the predecessor snapshot, more relations than the bound allows.
    Every one of those is decided before the adapter is reached, so a refused
    declaration never leaves a worker running.

    A turn for which **no** declaration was made does not take this path. That is
    an ordinary durable ``not_declared`` row, which is the state nearly every
    turn in this build will legitimately have.
    """

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_CONTINUITY_UNRECORDED,
            "the criteria continuity for this turn could not be recorded",
            detail or "nothing was dispatched; the task was not started",
        )


class EvaluationConflict(TaskError):
    """A second evaluation of one turn and version disagreed with the stored one.

    **This is not an ordinary refusal and must not be smoothed over.** The inputs
    to an evaluation are immutable by construction: a frozen criteria snapshot, a
    bundle derived from rows inside a closed event window, and a code-owned
    evaluator version. Deriving the same turn twice must therefore produce the
    same judgement, and it is not possible for it not to.

    So a mismatch means one of the things this milestone spent five PRs making
    impossible has happened anyway: a criteria snapshot changed after dispatch, a
    closed turn's evidence window moved, the assembler produced different inputs
    from the same rows, or the evaluator disagreed with itself. Every one of those
    is a defect worth investigating rather than a state to reconcile.

    The two wrong answers are equally wrong. **Returning the stored record** would
    report success for an operation that did not happen and hide the drift.
    **Overwriting it** would destroy the evidence that anything was wrong, and
    replace an immutable judgement somebody may already have read. So this fails
    closed, writes nothing, and leaves the original exactly as it was.
    """

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_EVALUATION_CONFLICT,
            "a stored evaluation for this turn disagrees with a new one",
            detail or "the stored evaluation was left unchanged; this needs investigation",
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
    "CODE_CLARIFICATION_CLOSED",
    "CODE_CLARIFICATION_INVALID",
    "CODE_CLARIFICATION_NOT_DELIVERED",
    "CODE_CLARIFICATION_PENDING",
    "CODE_CLARIFICATION_UNKNOWN",
    "CODE_CLARIFICATION_UNSUPPORTED",
    "CODE_CRITERIA_INVALID",
    "CODE_CONTINUITY_INVALID",
    "CODE_CONTINUITY_UNRECORDED",
    "CODE_CRITERIA_UNRECORDED",
    "CODE_EVALUATION_CONFLICT",
    "CODE_FOLLOWUP_INVALID",
    "CODE_FOLLOWUP_IN_FLIGHT",
    "CODE_FOLLOWUP_NOT_WAITING",
    "CODE_FOLLOWUP_UNSUPPORTED",
    "CODE_IDEMPOTENCY_CONFLICT",
    "CODE_ILLEGAL_TRANSITION",
    "CODE_PROJECT_DISABLED",
    "CODE_PROJECT_ROOT_INVALID",
    "CODE_PROJECT_UNKNOWN",
    "CODE_PROMPT_INVALID",
    "CODE_REQUEST_ID_INVALID",
    "CODE_RESULT_NOT_READY",
    "CODE_SESSION_UNAVAILABLE",
    "CODE_STORE_UNAVAILABLE",
    "CODE_TASK_TERMINAL",
    "CODE_TASK_UNKNOWN",
    "CODE_TURN_LIMIT_REACHED",
    "AdapterFailed",
    "AdapterNotPermitted",
    "AdapterUnknown",
    "CancelUnsupported",
    "ClarificationAnswerInvalid",
    "ClarificationClosed",
    "ClarificationNotDelivered",
    "ClarificationPending",
    "ClarificationUnknown",
    "ClarificationUnsupported",
    "CriteriaInvalid",
    "ContinuityInvalid",
    "ContinuityUnrecorded",
    "CriteriaUnrecorded",
    "EvaluationConflict",
    "FollowupInFlight",
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
    "ResultNotReady",
    "SessionUnavailable",
    "StoreUnavailable",
    "TaskAlreadyFinished",
    "TaskError",
    "TaskUnknown",
    "TurnLimitReached",
]
