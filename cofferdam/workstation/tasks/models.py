"""The task vocabulary: states, transitions, events, and what may be published.

This module is the part of Task Core that other layers read, so it is
deliberately the part with no behaviour in it. It holds five closed vocabularies
and the bounds that keep an adapter from producing an unbounded API response.

Three rules shaped everything here.

**A state is what was observed, never what was asked for.** ``running`` means an
adapter reported that it started. ``completed`` means an adapter reported a
result. Nothing in this package sets a terminal state because a request arrived:
a cancel request produces ``cancelling``, and only the adapter's own report — or
the absence of one after a restart — produces what comes next. This is the same
rule the audio, Spotify and YouTube milestones each had to learn separately.

**A task is not a process.** Task Core never names an executable, a pid, a unit,
or a working directory, and there is no field here to put one in. What runs a
task is an *adapter*, addressed by an id from a code-owned table; where it runs
is a *project*, resolved server-side from a registry. That separation is what
makes "the client cannot ask the machine to run something" structural rather
than validated.

**Prompts and results are somebody's private thinking.** They are user content:
bounded, validated, stored, shown to an authenticated client — and never put in
a log line, an audit record, a task id, a URL or a process argument. The bounds
below are the *only* place their size is decided.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, Tuple

#: Bumped when the published task shape changes in a way a client could notice.
#: The PWA reads it so an older cached shell can say "reload me" rather than
#: mis-render a panel whose fields it does not understand.
TASK_API_VERSION = 1

# -- lifecycle ---------------------------------------------------------------
#
# Eleven states, and the count is not an accident: every one of them is a
# different sentence on a phone and a different set of things a person can do
# next. A design with fewer would have to overload one of them, and the two that
# would be overloaded first — "failed" swallowing "interrupted", "running"
# swallowing "waiting" — are exactly the two that must stay distinct. A task
# that stopped because the daemon restarted did not fail; a task waiting for an
# answer is not working.

STATE_CREATED = "created"
STATE_QUEUED = "queued"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_WAITING_FOR_USER = "waiting_for_user"
STATE_CANCELLING = "cancelling"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
STATE_INTERRUPTED = "interrupted"
STATE_RECOVERY_REQUIRED = "recovery_required"

STATES: Tuple[str, ...] = (
    STATE_CREATED,
    STATE_QUEUED,
    STATE_STARTING,
    STATE_RUNNING,
    STATE_WAITING_FOR_USER,
    STATE_CANCELLING,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_CANCELLED,
    STATE_INTERRUPTED,
    STATE_RECOVERY_REQUIRED,
)

#: Terminal: nothing moves out of these, ever. A transition attempt from one of
#: them is refused rather than ignored — see :mod:`.lifecycle`.
TERMINAL_STATES = frozenset(
    {STATE_COMPLETED, STATE_FAILED, STATE_CANCELLED, STATE_INTERRUPTED}
)

#: Non-terminal and not running either. ``recovery_required`` is the one state
#: whose exit needs a decision this milestone does not make, so it is modelled
#: as *pending a decision* rather than as terminal — and, because no recovery
#: path exists yet, nothing in this foundation produces it for tasks whose
#: adapter cannot recover. Those become ``interrupted``, which is honest and
#: final. See ``docs/AGENT_TASK_CORE.md``.
RECOVERABLE_STATES = frozenset({STATE_RECOVERY_REQUIRED})

#: States in which the task is doing something, or about to.
ACTIVE_STATES = frozenset(
    {STATE_CREATED, STATE_QUEUED, STATE_STARTING, STATE_RUNNING, STATE_CANCELLING}
)

#: What the list view groups by. Three buckets, because three is what fits on a
#: phone and matches the three questions a person actually asks: what is going,
#: what needs me, what is done.
BUCKET_ACTIVE = "active"
BUCKET_WAITING = "waiting"
BUCKET_FINISHED = "finished"

BUCKETS: Tuple[str, ...] = (BUCKET_ACTIVE, BUCKET_WAITING, BUCKET_FINISHED)


def bucket_of(state: str) -> str:
    """Which of the three list buckets a state belongs in.

    ``recovery_required`` is grouped with *waiting*, not with finished: it is a
    task that stopped and wants a person, which is what that bucket means.
    """
    if state == STATE_WAITING_FOR_USER or state in RECOVERABLE_STATES:
        return BUCKET_WAITING
    if state in TERMINAL_STATES:
        return BUCKET_FINISHED
    return BUCKET_ACTIVE


# -- why a task is waiting ---------------------------------------------------
#
# Structured and extensible, and deliberately wider than what this milestone can
# do. The vocabulary is reserved now so that the day an adapter needs to say
# "waiting for a password" there is already a truthful word for it — and no
# temptation to reuse "clarification" for a secret.
#
# Reserving the word is not implementing the mechanism. There is no secret input
# in this milestone and no field that would carry one: passwords, OTPs and
# passkeys need a dedicated ephemeral channel that never touches a prompt, a
# task history, an event payload or an audit record. See the documentation.

WAITING_CLARIFICATION = "clarification"
WAITING_APPROVAL = "approval"
WAITING_AUTHENTICATION = "authentication"
WAITING_PRIVILEGED_ACTION = "privileged_action"
WAITING_ADAPTER_INPUT = "adapter_input"
WAITING_UNKNOWN = "unknown"

WAITING_REASONS: Tuple[str, ...] = (
    WAITING_CLARIFICATION,
    WAITING_APPROVAL,
    WAITING_AUTHENTICATION,
    WAITING_PRIVILEGED_ACTION,
    WAITING_ADAPTER_INPUT,
    WAITING_UNKNOWN,
)

#: Waiting reasons that will one day require the secure-input channel rather
#: than the ordinary follow-up box. Named here so the PWA can refuse to offer a
#: plain text field for them before that channel exists.
SECRET_BEARING_WAITING_REASONS = frozenset({WAITING_AUTHENTICATION})

# -- events ------------------------------------------------------------------

EVENT_TASK_CREATED = "task_created"
EVENT_TASK_QUEUED = "task_queued"
EVENT_ADAPTER_STARTING = "adapter_starting"
EVENT_TASK_STARTED = "task_started"
EVENT_PROGRESS = "progress"
EVENT_MEANINGFUL_OUTPUT = "meaningful_output"
EVENT_WAITING_FOR_USER = "waiting_for_user"
EVENT_FOLLOWUP_RECEIVED = "followup_received"
EVENT_CANCELLATION_REQUESTED = "cancellation_requested"
EVENT_TASK_CANCELLED = "task_cancelled"
EVENT_TASK_COMPLETED = "task_completed"
EVENT_TASK_FAILED = "task_failed"
EVENT_TASK_INTERRUPTED = "task_interrupted"
EVENT_RECOVERY_REQUIRED = "recovery_required"
EVENT_ACTION_REJECTED = "action_rejected"

EVENT_TYPES: Tuple[str, ...] = (
    EVENT_TASK_CREATED,
    EVENT_TASK_QUEUED,
    EVENT_ADAPTER_STARTING,
    EVENT_TASK_STARTED,
    EVENT_PROGRESS,
    EVENT_MEANINGFUL_OUTPUT,
    EVENT_WAITING_FOR_USER,
    EVENT_FOLLOWUP_RECEIVED,
    EVENT_CANCELLATION_REQUESTED,
    EVENT_TASK_CANCELLED,
    EVENT_TASK_COMPLETED,
    EVENT_TASK_FAILED,
    EVENT_TASK_INTERRUPTED,
    EVENT_RECOVERY_REQUIRED,
    EVENT_ACTION_REJECTED,
)

#: Event types only **Task Core** may write, because each one is a claim about
#: the lifecycle rather than about the work.
#:
#: An adapter that emitted ``task_completed`` directly would be writing a
#: completion into the history without passing the transition graph — the
#: history would say a task finished while the snapshot said otherwise, which is
#: the one disagreement this design refuses to allow. Adapter events carrying
#: these types are demoted to ordinary output rather than dropped: the adapter
#: had something to say, and it still gets said, just not as a lifecycle claim.
CORE_OWNED_EVENT_TYPES = frozenset(
    {
        EVENT_TASK_CREATED,
        EVENT_TASK_QUEUED,
        EVENT_ADAPTER_STARTING,
        EVENT_TASK_STARTED,
        EVENT_TASK_COMPLETED,
        EVENT_TASK_FAILED,
        EVENT_TASK_CANCELLED,
        EVENT_TASK_INTERRUPTED,
        EVENT_RECOVERY_REQUIRED,
        EVENT_CANCELLATION_REQUESTED,
        EVENT_FOLLOWUP_RECEIVED,
        EVENT_ACTION_REJECTED,
    }
)

#: Events whose text is the task's own *output* — the last substantive thing it
#: produced, as opposed to the last thing that happened to it.
#:
#: Two exclusions, both deliberate. **Progress ticks** are noise on a list, and a
#: list that shows noise teaches people to stop reading it. **Lifecycle
#: notices** — completed, failed, cancelled, interrupted — are Cofferdam's words
#: about the task, not the task's own work: letting them in would mean a
#: restart's "this was not resumed" overwrote the last real thing the task
#: produced, and an interrupted task would lose the evidence of how far it got.
#: They still update ``latest_activity``, which is what the list row shows.
MEANINGFUL_EVENT_TYPES = frozenset(
    {EVENT_MEANINGFUL_OUTPUT, EVENT_WAITING_FOR_USER}
)

# -- who did it --------------------------------------------------------------
#
# `actor` answers "who caused this", `source` answers "where did the claim come
# from". They are separate fields because they disagree in the interesting
# cases: a user pressing cancel is actor=user/source=cofferdam, while the task
# actually stopping is actor=adapter/source=adapter.

ACTOR_USER = "user"
ACTOR_ADAPTER = "adapter"
ACTOR_SYSTEM = "system"

ACTORS: Tuple[str, ...] = (ACTOR_USER, ACTOR_ADAPTER, ACTOR_SYSTEM)

SOURCE_COFFERDAM = "cofferdam"
SOURCE_ADAPTER = "adapter"
SOURCE_RESTART_RECOVERY = "restart_recovery"

SOURCES: Tuple[str, ...] = (SOURCE_COFFERDAM, SOURCE_ADAPTER, SOURCE_RESTART_RECOVERY)

# -- evidence ----------------------------------------------------------------
#
# A narrow, extensible foothold rather than the full resource audit, which is
# its own milestone. The one thing it must get right *now* is the distinction
# below: an adapter *saying* it wrote a file is not the same class of claim as
# Cofferdam having watched it happen. Collapsing those two into one "evidence"
# field would be very hard to un-collapse later, because every stored row would
# have lost the difference.

EVIDENCE_ADAPTER_REPORTED = "adapter_reported"
EVIDENCE_COFFERDAM_ACTION = "cofferdam_action"
EVIDENCE_OS_OBSERVED = "os_observed"
EVIDENCE_GIT_OBSERVED = "git_observed"
EVIDENCE_USER_REPORTED = "user_reported"

EVIDENCE_SOURCES: Tuple[str, ...] = (
    EVIDENCE_ADAPTER_REPORTED,
    EVIDENCE_COFFERDAM_ACTION,
    EVIDENCE_OS_OBSERVED,
    EVIDENCE_GIT_OBSERVED,
    EVIDENCE_USER_REPORTED,
)

#: Sources Cofferdam observed for itself. Everything outside this set is a
#: *claim*, and the PWA labels it as one.
VERIFIED_EVIDENCE_SOURCES = frozenset(
    {EVIDENCE_COFFERDAM_ACTION, EVIDENCE_OS_OBSERVED, EVIDENCE_GIT_OBSERVED}
)

EVIDENCE_PROCESS = "process"
EVIDENCE_FILE = "file"
EVIDENCE_COMMIT = "commit"
EVIDENCE_PULL_REQUEST = "pull_request"
EVIDENCE_TEST_SUMMARY = "test_summary"
EVIDENCE_ARTIFACT = "artifact"
EVIDENCE_ACTION = "action"

EVIDENCE_TYPES: Tuple[str, ...] = (
    EVIDENCE_PROCESS,
    EVIDENCE_FILE,
    EVIDENCE_COMMIT,
    EVIDENCE_PULL_REQUEST,
    EVIDENCE_TEST_SUMMARY,
    EVIDENCE_ARTIFACT,
    EVIDENCE_ACTION,
)

# -- origins -----------------------------------------------------------------
#
# Where a task was asked for. Assigned by the server from the authenticated
# request context and never read from a body: an origin a client could choose
# would be a client choosing how its own request is later attributed, which is
# the opposite of what this field is for. The future entries are listed so the
# vocabulary is stable before those origins exist.

ORIGIN_PWA = "pwa"
ORIGIN_CLI = "cli"
ORIGIN_CHATGPT_APP = "chatgpt_app"
ORIGIN_OPERA_COMPANION = "opera_companion"

ORIGINS: Tuple[str, ...] = (
    ORIGIN_PWA,
    ORIGIN_CLI,
    ORIGIN_CHATGPT_APP,
    ORIGIN_OPERA_COMPANION,
)

# -- bounds ------------------------------------------------------------------
#
# Every one of these is enforced on the way *in* (a request that exceeds it is
# refused, never truncated) and again on the way *out* (an adapter that exceeds
# it is truncated, because refusing an adapter's own report would lose it).
# Those two rules differ on purpose: a person can retype a shorter prompt, and
# nobody can retype an adapter's output.

MAX_PROMPT_CHARS = 8000
MAX_FOLLOWUP_CHARS = 4000
MAX_RESULT_CHARS = 16000
MAX_ACTIVITY_CHARS = 500
MAX_OUTPUT_CHARS = 4000
MAX_FAILURE_CHARS = 500
MAX_LABEL_CHARS = 120
MAX_CLIENT_REQUEST_ID_CHARS = 128
MAX_EVIDENCE_ITEMS = 8
MAX_EVIDENCE_IDENTIFIER_CHARS = 200
MAX_EVENT_PAYLOAD_CHARS = MAX_OUTPUT_CHARS

#: The most events one request may ask for. A client asking for more gets this
#: many; there is no "all", because an unbounded read of a long task is a slow
#: query a phone did not mean to make.
MAX_EVENT_PAGE = 200
DEFAULT_EVENT_PAGE = 50

#: The most tasks one list response carries.
MAX_TASK_PAGE = 100
DEFAULT_TASK_PAGE = 50


# -- text --------------------------------------------------------------------


def _has_forbidden_control_characters(value: str) -> bool:
    """Control characters, except the whitespace a person actually types.

    Tab and newline are kept: a prompt is prose and paragraphs are part of it.
    Everything else in the C0/C1 ranges is refused rather than stripped —
    stripping would silently change what somebody wrote, and a prompt that does
    not say what its author typed is worse than a refused one.
    """
    for character in value:
        code = ord(character)
        if character in ("\t", "\n"):
            continue
        if code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F:
            return True
    return False


def valid_user_text(value: Any, limit: int) -> bool:
    """Whether ``value`` is text a person may submit as task content.

    Unicode is normalized for *length* purposes only; the stored text is what
    was typed. Turkish is not a special case here and must never become one —
    "Türkçe karakterler" is ordinary text, and any check that treated it as
    exotic would be a bug in this function.
    """
    if not isinstance(value, str):
        return False
    if _has_forbidden_control_characters(value):
        return False
    normalized = unicodedata.normalize("NFC", value)
    if not normalized.strip():
        return False
    return len(normalized) <= limit


def bounded_text(value: Any, limit: int) -> Optional[str]:
    """Adapter-supplied display text, made safe and short, or nothing.

    Truncated rather than refused — see the note on the bounds above — and
    stripped of control characters entirely, because this text goes straight
    into a rendered panel and an adapter is not a person typing paragraphs.
    """
    if not isinstance(value, str):
        return None
    cleaned = "".join(
        character
        for character in value
        if not (
            ord(character) < 0x20
            or ord(character) == 0x7F
            or 0x80 <= ord(character) <= 0x9F
        )
        or character in ("\n", "\t")
    )
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned


def bounded_line(value: Any, limit: int) -> Optional[str]:
    """The same, collapsed to a single line. For list rows and badges."""
    text = bounded_text(value, limit)
    if text is None:
        return None
    return " ".join(text.split())


# -- published shapes --------------------------------------------------------


@dataclass(frozen=True)
class EvidenceReference:
    """One bounded pointer at something outside the task record.

    Never dereferenced by Task Core and never trusted as fact. ``source`` is the
    field that carries the honesty: an adapter saying it made a commit is
    ``adapter_reported`` and stays that way unless something actually looked.
    """

    evidence_type: str
    source: str
    identifier: Optional[str] = None
    operation: Optional[str] = None
    result: Optional[str] = None
    observed_at: Optional[str] = None

    @property
    def verified(self) -> bool:
        return self.source in VERIFIED_EVIDENCE_SOURCES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_type": self.evidence_type,
            "source": self.source,
            # Said out loud in the payload rather than left for a client to
            # derive, because deriving it means every client has to know the
            # rule and one of them will get it wrong.
            "verified": self.verified,
            "identifier": self.identifier,
            "operation": self.operation,
            "result": self.result,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class TaskEvent:
    """One append-only entry in a task's history.

    ``sequence`` is monotonic per task and is the only cursor a client is given.
    It is not a timestamp: clocks move backwards and two events can share a
    millisecond, and a cursor that skipped an event because of either would be a
    cursor that quietly loses history.
    """

    task_id: str
    sequence: int
    event_type: str
    created_at: str
    actor: str
    source: str
    lifecycle_revision: int
    correlation_id: Optional[str] = None
    state: Optional[str] = None
    text: Optional[str] = None
    detail: Optional[str] = None
    evidence: Sequence[EvidenceReference] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "created_at": self.created_at,
            "actor": self.actor,
            "source": self.source,
            "lifecycle_revision": self.lifecycle_revision,
            "correlation_id": self.correlation_id,
            "state": self.state,
            "text": self.text,
            "detail": self.detail,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class TaskFailure:
    """Why a task failed, in words a person can act on.

    Bounded and Cofferdam-worded. There is no ``traceback`` field and no
    ``exception`` field: an adapter's internal stack is not something to render
    on a phone, and a milestone that publishes one has to keep publishing it.
    """

    code: str
    message: str
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


@dataclass(frozen=True)
class TaskSnapshot:
    """One consistent, bounded view of a task.

    Assembled from the durable row and the adapter's declared capabilities, and
    never from a request that is in flight. Every text field has passed through
    the bounds above, so a hostile adapter cannot make this response large.
    """

    task_id: str
    correlation_id: str
    origin: str
    adapter_id: str
    project_id: str
    state: str
    lifecycle_revision: int
    created_at: str
    updated_at: str
    parent_task_id: Optional[str] = None
    waiting_reason: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    title: Optional[str] = None
    latest_activity: Optional[str] = None
    latest_meaningful_output: Optional[str] = None
    final_result: Optional[str] = None
    failure: Optional[TaskFailure] = None
    cancellation: Optional[Dict[str, Any]] = None
    adapter_capabilities: Dict[str, bool] = field(default_factory=dict)
    project_display_name: Optional[str] = None
    adapter_display_name: Optional[str] = None
    event_cursor: int = 0
    resource_summary: Dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_dict(self, *, include_content: bool = True) -> Dict[str, Any]:
        """The wire shape.

        ``include_content=False`` drops the three user-content fields — prompt
        text never appears here at all, and the result and output are the other
        two — so the list view can be assembled without carrying every task's
        result to a phone that is only showing rows.
        """
        payload: Dict[str, Any] = {
            "version": TASK_API_VERSION,
            "task_id": self.task_id,
            "correlation_id": self.correlation_id,
            "parent_task_id": self.parent_task_id,
            "origin": self.origin,
            "adapter_id": self.adapter_id,
            "adapter_display_name": self.adapter_display_name,
            "project_id": self.project_id,
            "project_display_name": self.project_display_name,
            "state": self.state,
            "bucket": bucket_of(self.state),
            "terminal": self.terminal,
            "waiting_reason": self.waiting_reason,
            "lifecycle_revision": self.lifecycle_revision,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "title": self.title,
            "latest_activity": self.latest_activity,
            "failure": self.failure.to_dict() if self.failure else None,
            "cancellation": dict(self.cancellation) if self.cancellation else None,
            "adapter_capabilities": dict(self.adapter_capabilities),
            "event_cursor": self.event_cursor,
            "resource_summary": dict(self.resource_summary),
            "limitations": list(LIMITATIONS),
        }
        if include_content:
            payload["latest_meaningful_output"] = self.latest_meaningful_output
            payload["final_result"] = self.final_result
        return payload


#: What this milestone can and cannot do, stated in the payload rather than only
#: in the docs, so a client never has to infer it. These are facts about the
#: foundation, not apologies for unfinished work.
LIMITATIONS: Tuple[str, ...] = (
    "Cofferdam reports what an adapter tells it; adapter-reported evidence is not observation.",
    "An interrupted task is never resumed automatically — restarting the service ends it.",
    "Task Core runs no shell, no process and no model. What a task does is entirely the adapter's.",
    "Follow-up and cancellation are offered only where the adapter declares support for them.",
    "Secrets — passwords, one-time codes, passkeys — are never task content and have no field here.",
)


__all__ = [
    "ACTIVE_STATES",
    "ACTORS",
    "ACTOR_ADAPTER",
    "ACTOR_SYSTEM",
    "ACTOR_USER",
    "BUCKETS",
    "BUCKET_ACTIVE",
    "BUCKET_FINISHED",
    "BUCKET_WAITING",
    "CORE_OWNED_EVENT_TYPES",
    "DEFAULT_EVENT_PAGE",
    "DEFAULT_TASK_PAGE",
    "EVENT_ACTION_REJECTED",
    "EVENT_ADAPTER_STARTING",
    "EVENT_CANCELLATION_REQUESTED",
    "EVENT_FOLLOWUP_RECEIVED",
    "EVENT_MEANINGFUL_OUTPUT",
    "EVENT_PROGRESS",
    "EVENT_RECOVERY_REQUIRED",
    "EVENT_TASK_CANCELLED",
    "EVENT_TASK_COMPLETED",
    "EVENT_TASK_CREATED",
    "EVENT_TASK_FAILED",
    "EVENT_TASK_INTERRUPTED",
    "EVENT_TASK_QUEUED",
    "EVENT_TASK_STARTED",
    "EVENT_TYPES",
    "EVENT_WAITING_FOR_USER",
    "EVIDENCE_ACTION",
    "EVIDENCE_ADAPTER_REPORTED",
    "EVIDENCE_ARTIFACT",
    "EVIDENCE_COFFERDAM_ACTION",
    "EVIDENCE_COMMIT",
    "EVIDENCE_FILE",
    "EVIDENCE_GIT_OBSERVED",
    "EVIDENCE_OS_OBSERVED",
    "EVIDENCE_PROCESS",
    "EVIDENCE_PULL_REQUEST",
    "EVIDENCE_SOURCES",
    "EVIDENCE_TEST_SUMMARY",
    "EVIDENCE_TYPES",
    "EVIDENCE_USER_REPORTED",
    "LIMITATIONS",
    "MAX_ACTIVITY_CHARS",
    "MAX_CLIENT_REQUEST_ID_CHARS",
    "MAX_EVENT_PAGE",
    "MAX_EVIDENCE_ITEMS",
    "MAX_FAILURE_CHARS",
    "MAX_FOLLOWUP_CHARS",
    "MAX_LABEL_CHARS",
    "MAX_OUTPUT_CHARS",
    "MAX_PROMPT_CHARS",
    "MAX_RESULT_CHARS",
    "MAX_TASK_PAGE",
    "MEANINGFUL_EVENT_TYPES",
    "ORIGINS",
    "ORIGIN_CHATGPT_APP",
    "ORIGIN_CLI",
    "ORIGIN_OPERA_COMPANION",
    "ORIGIN_PWA",
    "RECOVERABLE_STATES",
    "SECRET_BEARING_WAITING_REASONS",
    "SOURCES",
    "SOURCE_ADAPTER",
    "SOURCE_COFFERDAM",
    "SOURCE_RESTART_RECOVERY",
    "STATES",
    "STATE_CANCELLED",
    "STATE_CANCELLING",
    "STATE_COMPLETED",
    "STATE_CREATED",
    "STATE_FAILED",
    "STATE_INTERRUPTED",
    "STATE_QUEUED",
    "STATE_RECOVERY_REQUIRED",
    "STATE_RUNNING",
    "STATE_STARTING",
    "STATE_WAITING_FOR_USER",
    "TASK_API_VERSION",
    "TERMINAL_STATES",
    "VERIFIED_EVIDENCE_SOURCES",
    "WAITING_ADAPTER_INPUT",
    "WAITING_APPROVAL",
    "WAITING_AUTHENTICATION",
    "WAITING_CLARIFICATION",
    "WAITING_PRIVILEGED_ACTION",
    "WAITING_REASONS",
    "WAITING_UNKNOWN",
    "EvidenceReference",
    "TaskEvent",
    "TaskFailure",
    "TaskSnapshot",
    "bounded_line",
    "bounded_text",
    "bucket_of",
    "valid_user_text",
]
