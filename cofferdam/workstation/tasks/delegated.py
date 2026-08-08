"""What a delegated agent session may say, in Cofferdam's words rather than a provider's.

Task Core already has a vocabulary for what happens *to* a task — eleven states
and sixteen event types, in :mod:`.models`. This module adds the smaller,
narrower vocabulary for what happens *inside* one delegated agent session, and it
exists for one reason: a real agent transport reports far more than "something
happened", and the difference between two of those reports is a safety boundary.

The boundary is here
--------------------

An agent can ask a person for **information** — which of these two approaches,
what should the flag be called, is this the branch you meant. It can also ask for
**permission** — may I run this command, may I write outside this folder. Both
arrive as "the agent is waiting", and a design that stored them as one kind of
event would have to re-derive the difference at every later layer: the phone, the
Custom GPT bridge, the answer route, the audit.

So they are two types here, with **disjoint required fields**, disjoint
serialized shapes, and constructors that refuse each other's payloads. A
clarification has a question and cannot have a tool; an approval has a tool and
cannot have a question. There is no field either could be smuggled through and no
string that turns one into the other. See :class:`ClarificationRequest` and
:class:`ToolApprovalRequest`, and the round-trip refusals in
:meth:`ClarificationRequest.from_dict`.

Why it matters concretely: a clarification is answerable from a phone, and one
day from a Custom GPT. A tool approval is not, and must not become so by
accident. Cofferdam's rule is that risky tool and OS approvals stay on a trusted
surface at the workstation; the way to keep that rule is to make the two things
impossible to confuse in storage, not to remember to check.

What is not here
----------------

No provider payload. There is no ``raw``, ``data``, ``payload`` or ``message``
field on any class in this file, so a normalized event *cannot* carry an SDK
object even by mistake. Every string that reaches one of these dataclasses has
passed :func:`safe_text` — ANSI removed, control characters removed,
bidirectional overrides removed, NFC-normalized, truncated to a bound named in
this module.

No credentials, no environment, no reasoning, no tool input. A tool approval
records the tool's *name* and a coarse category; what the tool was going to do
with which arguments is exactly the material that must not travel to a phone
unexamined, and there is no field for it.

No storage. These events project onto Task Core's existing generic event storage
(see :func:`projection`) rather than adding a table. That is deliberate: the
store already gives transactional append, monotonic per-task sequencing and
duplicate suppression, and a second event schema would be a second history for
the same task.
"""

from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    EVENT_MEANINGFUL_OUTPUT,
    EVENT_PROGRESS,
    EVENT_WAITING_FOR_USER,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_INTERRUPTED,
    WAITING_APPROVAL,
    WAITING_CLARIFICATION,
)

#: Bumped when the normalized delegated-session shape changes in a way something
#: reading it could notice. Separate from ``TASK_API_VERSION`` because these
#: events are not published on their own yet — they project onto task events —
#: and versioning them together would mean a change here forced a client reload.
DELEGATED_EVENT_VERSION = 1

# -- the closed kind vocabulary ----------------------------------------------
#
# Twelve kinds. Every one of them is a different sentence, and the two that would
# be collapsed first — clarification and approval — are the two that must never
# be. The rest are here so that "the agent is doing something" does not have to
# mean five different things wearing one label.

KIND_SESSION_STARTED = "session_started"
KIND_ACTIVITY = "activity"
KIND_OUTPUT = "output"
KIND_CLARIFICATION_REQUESTED = "clarification_requested"
KIND_TOOL_APPROVAL_REQUESTED = "tool_approval_requested"
KIND_TOOL_STARTED = "tool_started"
KIND_TOOL_FINISHED = "tool_finished"
KIND_SUCCEEDED = "succeeded"
KIND_PROVIDER_FAILED = "provider_failed"
KIND_CANCELLATION_REQUESTED = "cancellation_requested"
KIND_CANCELLED = "cancelled"
KIND_INTERRUPTED = "interrupted"

DELEGATED_KINDS: Tuple[str, ...] = (
    KIND_SESSION_STARTED,
    KIND_ACTIVITY,
    KIND_OUTPUT,
    KIND_CLARIFICATION_REQUESTED,
    KIND_TOOL_APPROVAL_REQUESTED,
    KIND_TOOL_STARTED,
    KIND_TOOL_FINISHED,
    KIND_SUCCEEDED,
    KIND_PROVIDER_FAILED,
    KIND_CANCELLATION_REQUESTED,
    KIND_CANCELLED,
    KIND_INTERRUPTED,
)

#: Kinds after which a session says nothing else. Enforced by
#: :class:`DelegatedEventLog`, which drops everything that arrives afterwards —
#: the mechanism that stops a late provider result from resurrecting a task
#: somebody already cancelled.
TERMINAL_KINDS = frozenset(
    {KIND_SUCCEEDED, KIND_PROVIDER_FAILED, KIND_CANCELLED, KIND_INTERRUPTED}
)

#: Kinds that mean a person is being asked for something. Two, and they stay two.
WAITING_KINDS = frozenset(
    {KIND_CLARIFICATION_REQUESTED, KIND_TOOL_APPROVAL_REQUESTED}
)

#: The Task Core terminal state each terminal kind maps to. A table rather than
#: a chain of ``if``s so the mapping can be read, and asserted, in one place.
#: Nothing here *performs* a transition — Task Core's graph still decides whether
#: the move is legal.
TERMINAL_STATE_FOR_KIND: Dict[str, str] = {
    KIND_SUCCEEDED: STATE_COMPLETED,
    KIND_PROVIDER_FAILED: STATE_FAILED,
    KIND_CANCELLED: STATE_CANCELLED,
    KIND_INTERRUPTED: STATE_INTERRUPTED,
}

# -- how a request to a person is categorised --------------------------------

#: The discriminator written into every serialized request. Not free text and
#: not derived from a kind string at read time: it is stored, and a payload
#: carrying the wrong one is refused rather than reinterpreted.
CATEGORY_CLARIFICATION = "clarification"
CATEGORY_TOOL_APPROVAL = "tool_approval"

#: Coarse buckets for what a tool would do. Deliberately five words rather than a
#: tool taxonomy: this is what a person needs to judge an approval at a glance,
#: and a richer vocabulary would tempt somebody into deriving policy from it.
TOOL_CATEGORY_READ = "read"
TOOL_CATEGORY_WRITE = "write"
TOOL_CATEGORY_EXECUTE = "execute"
TOOL_CATEGORY_NETWORK = "network"
TOOL_CATEGORY_OTHER = "other"

TOOL_CATEGORIES: Tuple[str, ...] = (
    TOOL_CATEGORY_READ,
    TOOL_CATEGORY_WRITE,
    TOOL_CATEGORY_EXECUTE,
    TOOL_CATEGORY_NETWORK,
    TOOL_CATEGORY_OTHER,
)

# -- bounds ------------------------------------------------------------------
#
# Every string on every class below is truncated to one of these. They are
# smaller than Task Core's own bounds on purpose: a task result is something a
# person asked for and will read, while a delegated-session event is something a
# provider volunteered, and the two do not deserve the same room.

MAX_PROVIDER_CHARS = 40
MAX_PROVIDER_SESSION_ID_CHARS = 64
MAX_PROVIDER_EVENT_ID_CHARS = 128
MAX_QUESTION_CHARS = 1000
MAX_OPTION_LABEL_CHARS = 120
MAX_OPTION_VALUE_CHARS = 120
MAX_OPTIONS = 8
MAX_TOOL_NAME_CHARS = 60
MAX_APPROVAL_REASON_CHARS = 300
MAX_ACTIVITY_SUMMARY_CHARS = 300
MAX_OUTPUT_TEXT_CHARS = 4000
MAX_RESULT_TEXT_CHARS = 16000
MAX_FAILURE_SUMMARY_CHARS = 500
MAX_FAILURE_CODE_CHARS = 60
MAX_DETAIL_CHARS = 200

#: How many events one session's log holds before the oldest are dropped, and how
#: many event ids are remembered for duplicate suppression. Both bounded because
#: a session that ran for an hour must not be able to grow either without limit.
MAX_LOG_EVENTS = 200
MAX_REMEMBERED_EVENT_IDS = 512

#: Identifier shapes. A provider session id ends up in a durable record and in
#: an audit line, so it is checked against a shape rather than passed through: an
#: "id" containing a newline or a quote is not an id.
_IDENTIFIER = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")

#: Tool names are shown to a person next to the word "allow". A name that is not
#: a name is dropped rather than sanitised into something that looks legitimate.
_TOOL_NAME = re.compile(r"\A[A-Za-z][A-Za-z0-9_.-]{0,59}\Z")

#: Failure codes appear in a stable machine-readable field, so they are lowercase
#: words and nothing else.
_FAILURE_CODE = re.compile(r"\A[a-z][a-z0-9_]{0,59}\Z")

#: ANSI/VT escape sequences: OSC first, then CSI, then the single-character
#: forms. The order matters and the reason is recorded in the CLI adapter's own
#: parser, which learned it the hard way: Python alternation is first-match, and
#: the single-character class contains ``]``, so putting it first leaves
#: ``0;title`` sitting in the text looking like something the agent said.
_ANSI = re.compile(
    r"\x1B(?:\][^\x07\x1B]*(?:\x07|\x1B\\)?|\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])"
)


class DelegatedEventInvalid(ValueError):
    """A normalized event that could not be built truthfully.

    Raised rather than repaired. The two cases that reach it are a request to a
    person with nothing in it — a clarification with no question, an approval
    with no tool — and a kind carrying a field that belongs to a different kind.
    Both are conditions where guessing produces a record that reads as fact and
    is not one, so the provider event is refused and reported as refused. The
    task is unaffected: a malformed event is dropped, never applied.
    """


def safe_text(value: Any, limit: int) -> Optional[str]:
    """One provider string, made safe to store and to show, or ``None``.

    Escapes are removed before control characters, because an ANSI sequence is
    partly printable and stripping the escape byte first leaves ``[31m`` behind
    as visible garbage. Bidirectional overrides go too: they are invisible and
    can make stored text display in an order it was not written in, which in a
    field a person reads before approving something is not a cosmetic problem.

    Newlines and tabs survive. A clarification question can have paragraphs, and
    flattening them would cost readability to save nothing.
    """
    if not isinstance(value, str) or not value:
        return None
    text = _ANSI.sub("", value)
    text = "".join(
        character
        for character in text
        if character in "\n\t"
        or not (
            ord(character) < 0x20
            or ord(character) == 0x7F
            or 0x80 <= ord(character) <= 0x9F
            or 0x202A <= ord(character) <= 0x202E
            or 0x2066 <= ord(character) <= 0x2069
        )
    )
    text = unicodedata.normalize("NFC", text).strip()
    if not text:
        return None
    if len(text) > limit:
        # Truncated, not refused. A provider's own output cannot be retyped by
        # anybody, so losing the tail is better than losing the message — the
        # opposite of the rule for text a person submitted, which is refused so
        # they can shorten it themselves. Task Core makes the same distinction.
        text = text[: max(1, limit - 1)].rstrip() + "…"
    return text


def safe_line(value: Any, limit: int) -> Optional[str]:
    """The same, collapsed to one line. For badges, details and list rows."""
    text = safe_text(value, limit)
    if text is None:
        return None
    return " ".join(text.split())


def valid_tool_name(value: Any) -> bool:
    """Whether a string is a tool name this module will store.

    Public because a normalizer needs the same answer *before* it writes a
    sentence containing the name: dropping the field while leaving "the agent
    used <img onerror=…>." in the text would defeat the check by half.
    """
    return isinstance(value, str) and bool(_TOOL_NAME.match(value))


def _identifier(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str) or not _IDENTIFIER.match(value):
        return None
    return value[:limit]


@dataclass(frozen=True)
class ClarificationOption:
    """One choice a person may pick when answering a clarification.

    ``value`` is what would be sent back if this option were chosen, and it is
    bounded and sanitized exactly like the label. It is **not** an instruction,
    a command or a path: nothing in Cofferdam executes an option value, and the
    answer route that will one day consume it is PR2's, where the provenance of
    an answer is the whole subject.
    """

    label: str
    value: str

    def to_dict(self) -> Dict[str, str]:
        return {"label": self.label, "value": self.value}


@dataclass(frozen=True)
class ClarificationRequest:
    """The agent needs information or a choice in order to continue.

    **This is not a permission request**, and the class carries no field that
    could express one: there is no tool name, no command, no path, no category.
    An answer to this is a sentence or a choice from a list, and answering it
    grants nothing — which is precisely why it is the one of the two that may
    eventually be answered from a phone or a Custom GPT.
    """

    question: str
    options: Tuple[ClarificationOption, ...] = ()
    allows_free_text: bool = True

    @property
    def category(self) -> str:
        return CATEGORY_CLARIFICATION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": CATEGORY_CLARIFICATION,
            "question": self.question,
            "options": [option.to_dict() for option in self.options],
            "allows_free_text": self.allows_free_text,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "ClarificationRequest":
        """Rebuild one, refusing anything that is not one.

        Three refusals, and the second and third are the point of this method.
        A payload whose ``category`` is not ``clarification`` is refused; a
        payload carrying **any** field that belongs to a tool approval is
        refused even if its category says otherwise; and a payload with no
        usable question is refused because a question with no question is not a
        question.

        Together those mean a stored tool approval can never be read back as a
        clarification — not by a bug, not by a field-name collision, and not by
        a future writer who copies a dictionary from the wrong branch.
        """
        if not isinstance(payload, dict):
            raise DelegatedEventInvalid("a clarification must be an object")
        if payload.get("category") != CATEGORY_CLARIFICATION:
            raise DelegatedEventInvalid(
                "that is not a clarification request: category is "
                + str(payload.get("category"))
            )
        intruders = sorted(set(payload) & _TOOL_APPROVAL_FIELDS)
        if intruders:
            raise DelegatedEventInvalid(
                "a clarification cannot carry a tool approval field: " + intruders[0]
            )
        question = safe_text(payload.get("question"), MAX_QUESTION_CHARS)
        if question is None:
            raise DelegatedEventInvalid("a clarification must ask something")
        return cls(
            question=question,
            options=_read_options(payload.get("options")),
            allows_free_text=payload.get("allows_free_text") is not False,
        )


@dataclass(frozen=True)
class ToolApprovalRequest:
    """The agent is asking permission to use a tool, command or risky action.

    **This is not a clarification**, and it carries no question and no options —
    there is nothing here for an answer route to fill in. That is the design:
    Cofferdam's rule is that risky tool and operating-system approvals are
    decided on a trusted surface at the workstation, and the way to hold that
    rule under later refactoring is to give the remote-answerable path no field
    to write into.

    In this foundation nothing grants one. The Agent SDK's permission callback
    is answered by a code-owned handler that denies and reports, so an approval
    request becomes a task waiting for a person and never an automatic yes.
    """

    tool_name: str
    tool_category: str = TOOL_CATEGORY_OTHER
    reason: Optional[str] = None

    @property
    def category(self) -> str:
        return CATEGORY_TOOL_APPROVAL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": CATEGORY_TOOL_APPROVAL,
            "tool_name": self.tool_name,
            "tool_category": self.tool_category,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "ToolApprovalRequest":
        """Rebuild one, refusing anything that is not one.

        The mirror of :meth:`ClarificationRequest.from_dict`, and refusing in
        the same three ways: wrong category, a clarification field present, or
        no usable tool name. An approval that could not name its tool is not an
        approval anybody could decide.
        """
        if not isinstance(payload, dict):
            raise DelegatedEventInvalid("a tool approval must be an object")
        if payload.get("category") != CATEGORY_TOOL_APPROVAL:
            raise DelegatedEventInvalid(
                "that is not a tool approval request: category is "
                + str(payload.get("category"))
            )
        intruders = sorted(set(payload) & _CLARIFICATION_FIELDS)
        if intruders:
            raise DelegatedEventInvalid(
                "a tool approval cannot carry a clarification field: " + intruders[0]
            )
        name = payload.get("tool_name")
        if not isinstance(name, str) or not _TOOL_NAME.match(name):
            raise DelegatedEventInvalid("a tool approval must name a tool")
        category = payload.get("tool_category")
        return cls(
            tool_name=name,
            tool_category=category if category in TOOL_CATEGORIES else TOOL_CATEGORY_OTHER,
            reason=safe_line(payload.get("reason"), MAX_APPROVAL_REASON_CHARS),
        )


#: The field names that make each request what it is. Used by both ``from_dict``
#: methods to refuse the other's payload, and listed by name rather than derived
#: so that adding a field to one class is a visible decision about whether the
#: other must now refuse it.
_CLARIFICATION_FIELDS = frozenset({"question", "options", "allows_free_text"})
_TOOL_APPROVAL_FIELDS = frozenset({"tool_name", "tool_category"})


def _read_options(value: Any) -> Tuple[ClarificationOption, ...]:
    """Options, bounded in count and in length, dropping what is not usable.

    Dropped rather than refused: a question with one unreadable option is still
    a question worth asking, and losing the whole thing would lose the part a
    person could have answered.
    """
    if not isinstance(value, (list, tuple)):
        return ()
    options: List[ClarificationOption] = []
    for entry in list(value)[:MAX_OPTIONS]:
        if isinstance(entry, dict):
            label = safe_line(entry.get("label"), MAX_OPTION_LABEL_CHARS)
            raw_value = entry.get("value", entry.get("label"))
        elif isinstance(entry, str):
            label = safe_line(entry, MAX_OPTION_LABEL_CHARS)
            raw_value = entry
        else:
            continue
        if label is None:
            continue
        option_value = safe_line(raw_value, MAX_OPTION_VALUE_CHARS) or label
        options.append(ClarificationOption(label=label, value=option_value))
    return tuple(options)


@dataclass(frozen=True)
class DelegatedEvent:
    """One normalized thing a delegated session said.

    Built field by field from values that were individually checked. There is no
    attribute on this class that can hold a provider object, and that absence is
    the implementation of "raw SDK payloads are never persisted" — not a rule
    somebody has to remember at each call site.

    ``provider_sequence`` is the provider's own ordering, kept so a reader can
    tell whether two events arrived in the order they happened. It is *not* the
    durable cursor: Task Core allocates that when the projected event is
    appended, inside the transaction, which is the only place a monotonic
    per-task sequence can be allocated correctly.
    """

    kind: str
    provider: str
    provider_sequence: int
    observed_at: str
    provider_session_id: Optional[str] = None
    provider_event_id: Optional[str] = None
    text: Optional[str] = None
    detail: Optional[str] = None
    tool_name: Optional[str] = None
    clarification: Optional[ClarificationRequest] = None
    approval: Optional[ToolApprovalRequest] = None
    failure_code: Optional[str] = None
    result: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind not in DELEGATED_KINDS:
            raise DelegatedEventInvalid("unknown delegated event kind: " + str(self.kind))
        # The exclusivity is checked on construction rather than trusted to
        # callers. An event holding both a question and a tool would be the one
        # record from which the difference this module exists to keep could not
        # be recovered.
        if self.clarification is not None and self.approval is not None:
            raise DelegatedEventInvalid(
                "an event cannot be both a clarification and a tool approval"
            )
        if (self.clarification is not None) != (
            self.kind == KIND_CLARIFICATION_REQUESTED
        ):
            raise DelegatedEventInvalid(
                "a clarification belongs to " + KIND_CLARIFICATION_REQUESTED + " only"
            )
        if (self.approval is not None) != (self.kind == KIND_TOOL_APPROVAL_REQUESTED):
            raise DelegatedEventInvalid(
                "a tool approval belongs to " + KIND_TOOL_APPROVAL_REQUESTED + " only"
            )
        if self.result is not None and self.kind != KIND_SUCCEEDED:
            raise DelegatedEventInvalid("only a success carries a result")
        if self.failure_code is not None and self.kind != KIND_PROVIDER_FAILED:
            raise DelegatedEventInvalid("only a provider failure carries a failure code")

    @property
    def terminal(self) -> bool:
        return self.kind in TERMINAL_KINDS

    @property
    def waiting_reason(self) -> Optional[str]:
        """Which Task Core waiting reason this event means, if any.

        The mapping is one line and it is the whole safety property: a
        clarification is ``clarification`` and an approval is ``approval``, and
        there is no branch here that could return the other.
        """
        if self.kind == KIND_CLARIFICATION_REQUESTED:
            return WAITING_CLARIFICATION
        if self.kind == KIND_TOOL_APPROVAL_REQUESTED:
            return WAITING_APPROVAL
        return None

    def to_dict(self) -> Dict[str, Any]:
        """The bounded, provider-neutral shape. Never contains a provider object."""
        payload: Dict[str, Any] = {
            "version": DELEGATED_EVENT_VERSION,
            "kind": self.kind,
            "provider": self.provider,
            "provider_session_id": self.provider_session_id,
            "provider_sequence": self.provider_sequence,
            "provider_event_id": self.provider_event_id,
            "observed_at": self.observed_at,
            "terminal": self.terminal,
            "text": self.text,
            "detail": self.detail,
            "tool_name": self.tool_name,
            "failure_code": self.failure_code,
            "result": self.result,
        }
        # One key, holding one of two disjoint shapes, each of which names its
        # own category. A reader branches on `request["category"]` and cannot
        # reach the wrong constructor by reading the wrong key.
        if self.clarification is not None:
            payload["request"] = self.clarification.to_dict()
        elif self.approval is not None:
            payload["request"] = self.approval.to_dict()
        else:
            payload["request"] = None
        return payload


def build_event(
    *,
    kind: str,
    provider: str,
    provider_sequence: int,
    observed_at: str,
    provider_session_id: Any = None,
    provider_event_id: Any = None,
    text: Any = None,
    detail: Any = None,
    tool_name: Any = None,
    clarification: Optional[ClarificationRequest] = None,
    approval: Optional[ToolApprovalRequest] = None,
    failure_code: Any = None,
    result: Any = None,
) -> DelegatedEvent:
    """The only supported way to make a :class:`DelegatedEvent`.

    Every string is bounded here, so a caller cannot decide how much of the
    database an event uses by passing a longer one. The per-kind limits differ
    on purpose: a final result is something a person asked for and will read, an
    activity line is a status badge, and giving both sixteen thousand characters
    would make the badge a wall of text.

    Unbounded or unusable identifiers are dropped to ``None`` rather than
    rejected: an event whose provider event id was malformed is still an event
    that happened, it simply loses its duplicate suppression, which is the safe
    direction for that failure to fall.
    """
    if kind not in DELEGATED_KINDS:
        raise DelegatedEventInvalid("unknown delegated event kind: " + str(kind))

    limit = MAX_ACTIVITY_SUMMARY_CHARS
    if kind == KIND_OUTPUT:
        limit = MAX_OUTPUT_TEXT_CHARS
    elif kind == KIND_SUCCEEDED:
        limit = MAX_RESULT_TEXT_CHARS
    elif kind == KIND_PROVIDER_FAILED:
        limit = MAX_FAILURE_SUMMARY_CHARS
    elif kind == KIND_CLARIFICATION_REQUESTED:
        limit = MAX_QUESTION_CHARS

    code = None
    if kind == KIND_PROVIDER_FAILED:
        candidate = failure_code if isinstance(failure_code, str) else None
        # A code that is not a code becomes the generic one rather than being
        # passed through: this field is machine-readable, and something branches
        # on it later.
        code = (
            candidate
            if candidate and _FAILURE_CODE.match(candidate)
            else "provider_error"
        )[:MAX_FAILURE_CODE_CHARS]

    name = tool_name[:MAX_TOOL_NAME_CHARS] if valid_tool_name(tool_name) else None

    return DelegatedEvent(
        kind=kind,
        provider=safe_line(provider, MAX_PROVIDER_CHARS) or "unknown",
        provider_sequence=max(0, int(provider_sequence)),
        observed_at=safe_line(observed_at, 40) or "",
        provider_session_id=_identifier(
            provider_session_id, MAX_PROVIDER_SESSION_ID_CHARS
        ),
        provider_event_id=_identifier(provider_event_id, MAX_PROVIDER_EVENT_ID_CHARS),
        text=safe_text(text, limit),
        detail=safe_line(detail, MAX_DETAIL_CHARS),
        tool_name=name,
        clarification=clarification,
        approval=approval,
        failure_code=code,
        result=safe_text(result, MAX_RESULT_TEXT_CHARS) if kind == KIND_SUCCEEDED else None,
    )


class DelegatedEventLog:
    """Order, duplication and finality for one delegated session.

    Three jobs, and each one exists because of a way an event stream lies.

    **Duplicates.** A provider that retries, or a reader that is polled twice
    before it advances, will offer the same event again. One with a provider
    event id it has already seen is dropped, so a retry does not become two
    durable events. Ids are remembered in a bounded window; past it the oldest
    are forgotten, which can only cause an old duplicate to be recorded again —
    never a real event to be lost.

    **Order.** Events are kept as they arrived and can be read back sorted by
    the provider's own sequence, so "arrived late" and "happened late" stay
    distinguishable. Neither is corrected silently: an out-of-order arrival is
    counted, and the count is a fact about the stream rather than a repair of it.

    **Finality.** Once a terminal kind is accepted, nothing else is. This is the
    rule that makes a cancelled task stay cancelled when the provider's result
    lands a moment later, and it is enforced here rather than at the caller
    because there is exactly one place it can be enforced completely.
    """

    def __init__(self, *, max_events: int = MAX_LOG_EVENTS) -> None:
        self._events: List[DelegatedEvent] = []
        self._seen: "OrderedDict[str, bool]" = OrderedDict()
        self._max_events = max(1, int(max_events))
        self._terminal: Optional[DelegatedEvent] = None
        self._highest_sequence = -1
        self.duplicates = 0
        self.out_of_order = 0
        self.after_terminal = 0
        self.refused = 0

    # -- reading -------------------------------------------------------------

    @property
    def terminal_event(self) -> Optional[DelegatedEvent]:
        return self._terminal

    @property
    def terminal(self) -> bool:
        return self._terminal is not None

    def events(self) -> Tuple[DelegatedEvent, ...]:
        """As they arrived."""
        return tuple(self._events)

    def ordered(self) -> Tuple[DelegatedEvent, ...]:
        """By the provider's own sequence, arrival order breaking ties.

        Sorted on read rather than on insert so that arrival order stays
        recoverable. A stream that disagrees with itself about order is
        information, and sorting it away at write time would destroy it.
        """
        return tuple(
            event
            for _, event in sorted(
                enumerate(self._events), key=lambda pair: (pair[1].provider_sequence, pair[0])
            )
        )

    # -- writing -------------------------------------------------------------

    def record(self, event: DelegatedEvent) -> Optional[DelegatedEvent]:
        """Accept one event, or refuse it and say nothing happened.

        Returns the event when it was accepted and ``None`` when it was not, so
        a caller that projects the return value into Task Core cannot
        accidentally write a duplicate or a post-terminal event: there is
        nothing to write.
        """
        if not isinstance(event, DelegatedEvent):
            self.refused += 1
            return None
        if self._terminal is not None:
            # The session is over. A result that arrives after a cancellation
            # does not un-cancel it, and this is the line that says so.
            self.after_terminal += 1
            return None
        if event.provider_event_id is not None:
            if event.provider_event_id in self._seen:
                self.duplicates += 1
                return None
            self._seen[event.provider_event_id] = True
            while len(self._seen) > MAX_REMEMBERED_EVENT_IDS:
                self._seen.popitem(last=False)

        if event.provider_sequence < self._highest_sequence:
            self.out_of_order += 1
        else:
            self._highest_sequence = event.provider_sequence

        self._events.append(event)
        if len(self._events) > self._max_events:
            del self._events[: len(self._events) - self._max_events]
        if event.terminal:
            self._terminal = event
        return event

    def health(self) -> Dict[str, Any]:
        """Counts only. Useful in a test and in a bounded diagnostic; no content."""
        return {
            "events": len(self._events),
            "duplicates": self.duplicates,
            "out_of_order": self.out_of_order,
            "after_terminal": self.after_terminal,
            "refused": self.refused,
            "terminal": self.terminal,
        }


# -- projection onto Task Core -----------------------------------------------


def projection(event: DelegatedEvent) -> Tuple[str, Optional[str], Optional[str]]:
    """One delegated event as ``(task event type, text, detail)``.

    The bridge to the storage Task Core already has. Three event types come out
    of twelve kinds, and the compression is deliberate rather than lossy in the
    way it looks: ``progress`` and ``meaningful_output`` are the two things the
    store treats differently, ``waiting_for_user`` is the third, and the kind
    itself survives in the detail so a reader can still tell a tool approval
    from a clarification in the history.

    Nothing here returns a **lifecycle** event type. Those belong to Task Core
    and an adapter that emitted one would be writing a completion into the
    history without passing the transition graph — the core demotes them anyway,
    and this function never produces one to be demoted.
    """
    detail = event.detail
    if event.kind == KIND_CLARIFICATION_REQUESTED and event.clarification is not None:
        text = event.clarification.question
        if event.clarification.options:
            # Rendered into the text rather than left as structure, because this
            # projection targets a store whose event row has no structured
            # field. The structured form is what PR2's answer route will read;
            # this is what a person sees in the history today.
            text = text + "\n" + "\n".join(
                "• " + option.label for option in event.clarification.options
            )
        return EVENT_WAITING_FOR_USER, safe_text(text, MAX_OUTPUT_TEXT_CHARS), "clarification"
    if event.kind == KIND_TOOL_APPROVAL_REQUESTED and event.approval is not None:
        # No vendor named, and the omission is enforced by a Task Core guard
        # rather than remembered: this package must read the same whichever
        # provider produced the event. The adapter's own limitation sentences
        # are where a product name belongs.
        return (
            EVENT_WAITING_FOR_USER,
            "The agent asked for permission to use "
            + event.approval.tool_name
            + ". Cofferdam cannot grant that from a phone — decide it at the workstation.",
            "tool approval",
        )
    if event.kind in (KIND_OUTPUT, KIND_SUCCEEDED):
        return EVENT_MEANINGFUL_OUTPUT, event.result or event.text, detail
    return EVENT_PROGRESS, event.text, detail or event.kind


def waiting_reason_for(event: DelegatedEvent) -> Optional[str]:
    """The Task Core waiting reason for an event, or ``None``."""
    return event.waiting_reason


# -- the terminal result boundary --------------------------------------------


@dataclass(frozen=True)
class DelegatedResult:
    """What a finished delegated task can honestly be asked for later.

    The provider-neutral shape behind the ``get_result`` action M2I.5 will
    expose. It is defined now, and produced now, so that the later route is a
    serialization of something that already exists rather than a new claim
    invented at the boundary — but **no route serves it in this build**, and
    nothing in this repository should say otherwise.

    A success carries bounded result text. A failure carries a category and a
    bounded, Cofferdam-worded summary; there is no ``traceback`` field and no
    ``exception`` field, because a provider's internal stack is not something to
    render on a phone and a milestone that publishes one has to keep publishing
    it.
    """

    task_id: str
    provider: str
    terminal_state: str
    completed_at: str
    provider_session_id: Optional[str] = None
    result: Optional[str] = None
    failure_code: Optional[str] = None
    failure_summary: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.terminal_state == STATE_COMPLETED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": DELEGATED_EVENT_VERSION,
            "task_id": self.task_id,
            "provider": self.provider,
            "provider_session_id": self.provider_session_id,
            "terminal_state": self.terminal_state,
            "succeeded": self.succeeded,
            "completed_at": self.completed_at,
            "result": self.result,
            "failure_code": self.failure_code,
            "failure_summary": self.failure_summary,
        }


def result_from_event(
    *, task_id: str, event: DelegatedEvent, completed_at: str
) -> DelegatedResult:
    """The terminal result implied by a terminal event.

    Refuses a non-terminal one rather than inventing an outcome: "the task is
    still running" has no result, and a function that returned an empty one for
    it would be the first step towards a task that reports finished because
    somebody asked whether it had.
    """
    if not event.terminal:
        raise DelegatedEventInvalid("that event did not end the session")
    state = TERMINAL_STATE_FOR_KIND[event.kind]
    return DelegatedResult(
        task_id=task_id,
        provider=event.provider,
        provider_session_id=event.provider_session_id,
        terminal_state=state,
        completed_at=safe_line(completed_at, 40) or "",
        result=event.result if event.kind == KIND_SUCCEEDED else None,
        failure_code=event.failure_code if event.kind == KIND_PROVIDER_FAILED else None,
        failure_summary=(
            safe_text(event.text, MAX_FAILURE_SUMMARY_CHARS)
            if event.kind == KIND_PROVIDER_FAILED
            else None
        ),
    )


__all__ = [
    "CATEGORY_CLARIFICATION",
    "CATEGORY_TOOL_APPROVAL",
    "DELEGATED_EVENT_VERSION",
    "DELEGATED_KINDS",
    "KIND_ACTIVITY",
    "KIND_CANCELLATION_REQUESTED",
    "KIND_CANCELLED",
    "KIND_CLARIFICATION_REQUESTED",
    "KIND_INTERRUPTED",
    "KIND_OUTPUT",
    "KIND_PROVIDER_FAILED",
    "KIND_SESSION_STARTED",
    "KIND_SUCCEEDED",
    "KIND_TOOL_APPROVAL_REQUESTED",
    "KIND_TOOL_FINISHED",
    "KIND_TOOL_STARTED",
    "MAX_ACTIVITY_SUMMARY_CHARS",
    "MAX_APPROVAL_REASON_CHARS",
    "MAX_FAILURE_SUMMARY_CHARS",
    "MAX_LOG_EVENTS",
    "MAX_OPTIONS",
    "MAX_OPTION_LABEL_CHARS",
    "MAX_OPTION_VALUE_CHARS",
    "MAX_OUTPUT_TEXT_CHARS",
    "MAX_PROVIDER_SESSION_ID_CHARS",
    "MAX_QUESTION_CHARS",
    "MAX_RESULT_TEXT_CHARS",
    "MAX_TOOL_NAME_CHARS",
    "TERMINAL_KINDS",
    "TERMINAL_STATE_FOR_KIND",
    "TOOL_CATEGORIES",
    "TOOL_CATEGORY_EXECUTE",
    "TOOL_CATEGORY_NETWORK",
    "TOOL_CATEGORY_OTHER",
    "TOOL_CATEGORY_READ",
    "TOOL_CATEGORY_WRITE",
    "WAITING_KINDS",
    "ClarificationOption",
    "ClarificationRequest",
    "DelegatedEvent",
    "DelegatedEventInvalid",
    "DelegatedEventLog",
    "DelegatedResult",
    "ToolApprovalRequest",
    "build_event",
    "projection",
    "result_from_event",
    "safe_line",
    "safe_text",
    "valid_tool_name",
    "waiting_reason_for",
]
