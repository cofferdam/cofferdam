"""Pending clarifications: what was asked, who answered it, and what was sent.

A clarification is the one thing an agent can ask a person that a person may
answer **from somewhere other than the workstation**. That single sentence is why
this module exists as its own file rather than as three columns on the task row,
and it is why every type in here is defined without a single field that could
express a tool, a command, a path or a permission.

The distinction this file must never lose
-----------------------------------------

:mod:`.delegated` establishes that a clarification and a tool approval are two
disjoint types. This module carries that distinction into *durable storage and
the answer surface*, which is where it would otherwise be re-derived and
eventually re-derived wrongly:

* a :class:`PendingClarification` has a question and no tool field;
* :class:`ClarificationAnswer` accepts free text and Cofferdam-generated option
  identifiers, and :meth:`ClarificationAnswer.from_request` **refuses** a payload
  carrying any approval-shaped key, by name;
* there is no shared "answer a request" type, and no route in this repository
  serves both categories.

A tool approval has no record here at all. Not an empty one, not a disabled one —
none. Cofferdam's rule is that risky tool and operating-system approvals are
decided on a trusted surface at the workstation, and the way to hold that rule
under later refactoring is to give the remotely-answerable path no row to write
into.

What an answer becomes
----------------------

:func:`encode_answer` is the only function that turns an accepted answer into
text a provider will see, and it is deliberately small and code-owned. There is
no branch anywhere that concatenates a client-supplied string into a prompt
template, no formatting directive read from a payload, and no path by which an
option's provider-supplied *label* is sent back — the answer is composed from
Cofferdam's own words plus the person's own text, and nothing else.

Provenance, and why it is not a display name
--------------------------------------------

:class:`AnswerProvenance` records an actor category and a **source category from
a closed, code-owned vocabulary**, assigned by the route from the authenticated
request context. It is never read from a body. A caller-supplied "answered by"
string would be a caller deciding how its own answer is later attributed, which
is the opposite of what provenance is for, and it would be the first field a
future bridge learned to send.

No secret, no header, no token and no raw provider payload has a field here.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .delegated import (
    ANSWER_MODE_FREE_TEXT,
    ANSWER_MODE_SINGLE_CHOICE,
    ANSWER_MODE_UNKNOWN,
    ANSWER_MODES,
    CATEGORY_CLARIFICATION,
    CHOICE_ANSWER_MODES,
    MAX_OPTIONS,
    MAX_PROVIDER_CHARS,
    MAX_PROVIDER_EVENT_ID_CHARS,
    MAX_PROVIDER_SESSION_ID_CHARS,
    MAX_QUESTION_CHARS,
    ClarificationOption,
    ClarificationRequest,
    safe_line,
    safe_text,
)
from .models import ACTOR_USER, valid_user_text

#: Bumped when the stored clarification shape changes in a way a reader could
#: notice. Separate from ``TASK_API_VERSION`` because a client that never opens a
#: question does not need to reload when this changes.
CLARIFICATION_VERSION = 1

# -- status ------------------------------------------------------------------
#
# Four, and each one is a different sentence about why a question is no longer
# waiting for somebody. Collapsing ``cancelled`` and ``superseded`` into "closed"
# would be the first thing to go, and it is exactly the pair worth keeping: one
# means a person stopped the task, the other means the provider moved on. A
# person looking at a history deserves to know which.

STATUS_PENDING = "pending"
STATUS_ANSWERED = "answered"
STATUS_CANCELLED = "cancelled"
STATUS_SUPERSEDED = "superseded"

CLARIFICATION_STATUSES: Tuple[str, ...] = (
    STATUS_PENDING,
    STATUS_ANSWERED,
    STATUS_CANCELLED,
    STATUS_SUPERSEDED,
)

#: The statuses in which a question is no longer answerable. Read by the service
#: before it accepts anything, so "already answered" and "the task was cancelled"
#: are refusals rather than a second delivery.
CLOSED_STATUSES = frozenset({STATUS_ANSWERED, STATUS_CANCELLED, STATUS_SUPERSEDED})

# -- who answered ------------------------------------------------------------
#
# Code-owned and closed. Assigned by a route from the authenticated request
# context, never read from a body.

#: The private PWA on this workstation's own network. The only source that exists
#: today.
SOURCE_WORKSTATION_PWA = "workstation_pwa"
#: A test or an internal tool inside this process. Present so a test does not
#: have to claim to be the PWA, which would make the field useless for telling
#: real answers from synthetic ones in a stored history.
SOURCE_INTERNAL_TEST = "internal_test"
#: Reserved for M2I.5's Custom GPT Actions bridge. **Nothing produces it in this
#: build and no route accepts it.** The word is reserved now so that the day the
#: bridge exists there is already a truthful value for it, and so nobody is
#: tempted to record a bridge answer as though it came from the PWA.
SOURCE_FUTURE_GPT_BRIDGE = "future_gpt_bridge"

ANSWER_SOURCES: Tuple[str, ...] = (
    SOURCE_WORKSTATION_PWA,
    SOURCE_INTERNAL_TEST,
    SOURCE_FUTURE_GPT_BRIDGE,
)

#: The sources a route in **this** build may attribute an answer to. The bridge
#: is deliberately absent: a vocabulary entry is not an enabled surface, and this
#: frozenset is what makes that difference enforceable rather than documented.
ACCEPTED_ANSWER_SOURCES = frozenset({SOURCE_WORKSTATION_PWA, SOURCE_INTERNAL_TEST})

OUTCOME_ACCEPTED = "accepted"
OUTCOME_REJECTED = "rejected"

# -- bounds ------------------------------------------------------------------

#: How long an answer a person typed may be. Smaller than a prompt on purpose: an
#: answer to a question is a sentence or a paragraph, and a field sized like a
#: prompt invites somebody to restate the whole task into it.
MAX_ANSWER_CHARS = 2000

#: The most options one answer may select. Bounded by the option count itself in
#: practice; named here so a malformed request with four thousand identifiers is
#: refused before anything iterates it.
MAX_SELECTED_OPTIONS = MAX_OPTIONS

#: The most pending clarifications one task may accumulate. One is the working
#: number — a provider turn asks one question and waits — and the bound exists so
#: a provider that ignored that cannot grow a task's storage without limit.
MAX_PENDING_PER_TASK = 8

#: A bounded reason code recorded when an answer is refused.
MAX_REJECTION_REASON_CHARS = 120

QUESTION_ID_PREFIX = "q_"
_QUESTION_ID_BYTES = 12
QUESTION_ID_CHARS = len(QUESTION_ID_PREFIX) + _QUESTION_ID_BYTES * 2

_QUESTION_ID = re.compile(
    r"\A" + QUESTION_ID_PREFIX + r"[0-9a-f]{" + str(_QUESTION_ID_BYTES * 2) + r"}\Z"
)
_OPTION_ID = re.compile(r"\A[a-z][a-z0-9_]{0,31}\Z")
_IDENTIFIER = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")

#: Keys that make a payload a **tool approval**, refused by name wherever an
#: answer is read. Listed rather than derived so that adding a field to the
#: approval type is a visible decision about whether this surface must now refuse
#: it — the same rule ``delegated.py`` uses in the other direction.
#:
#: The last five are not fields of any Cofferdam type at all. They are the shapes
#: somebody would reach for if they were trying to make this endpoint approve
#: something, and refusing them by name turns "that is not what this route is
#: for" from a comment into a test.
_APPROVAL_SHAPED_KEYS = frozenset(
    {
        "approval_id",
        "tool_name",
        "tool_category",
        "tool_input",
        "behavior",
        "decision",
        "allow",
        "deny",
        "permission_mode",
        "command",
        "path",
        "cwd",
        "argv",
        "env",
    }
)


class ClarificationInvalid(ValueError):
    """A clarification or an answer that could not be built truthfully.

    Raised rather than repaired. Every case that reaches it is one where guessing
    would produce a record that reads as fact and is not one — a question with no
    question in it, an answer selecting an option that is not on the list, a
    payload carrying a field that belongs to a permission decision. The task is
    unaffected: a refused answer changes nothing.
    """


def new_question_id() -> str:
    """A question identifier Cofferdam mints, and can therefore verify.

    Random and opaque. **Not** derived from the question text, the provider's own
    event id, or a counter. A text-derived id would put a fingerprint of what the
    agent asked into every URL and audit line that carries it; a provider-derived
    one would let a provider choose the primary key of the surface a person
    answers through.
    """
    return QUESTION_ID_PREFIX + secrets.token_hex(_QUESTION_ID_BYTES)


def valid_question_id(value: object) -> bool:
    """Shape check for an id arriving in a URL path or a body.

    Not a security boundary — the store's lookup, scoped to the task, is — but it
    stops a malformed value from reaching a query, and it makes "a client cannot
    invent an id shape" testable.
    """
    return isinstance(value, str) and bool(_QUESTION_ID.match(value))


@dataclass(frozen=True)
class AnswerProvenance:
    """Where one answer came from, in bounded, code-owned words.

    Every field is either a value from a closed vocabulary or a timestamp this
    process generated. There is no display name, no header, no address, no user
    agent and no token — and no field one could be put in.
    """

    actor: str
    source: str
    received_at: str
    outcome: str
    rejection_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "actor": self.actor,
            "source": self.source,
            "received_at": self.received_at,
            "outcome": self.outcome,
            "rejection_reason": self.rejection_reason,
        }

    @classmethod
    def build(
        cls,
        *,
        source: str,
        received_at: str,
        outcome: str = OUTCOME_ACCEPTED,
        actor: str = ACTOR_USER,
        rejection_reason: Optional[str] = None,
    ) -> "AnswerProvenance":
        if source not in ANSWER_SOURCES:
            raise ClarificationInvalid("unknown answer source")
        if outcome not in (OUTCOME_ACCEPTED, OUTCOME_REJECTED):
            raise ClarificationInvalid("unknown answer outcome")
        return cls(
            actor=actor if actor in ("user", "system", "adapter") else ACTOR_USER,
            source=source,
            received_at=safe_line(received_at, 40) or "",
            outcome=outcome,
            rejection_reason=safe_line(rejection_reason, MAX_REJECTION_REASON_CHARS),
        )


@dataclass(frozen=True)
class ClarificationAnswer:
    """One person's answer to one question, bounded and provider-neutral.

    Two fields carry the answer and neither can carry anything else. ``text`` is
    what somebody typed, validated as user content by the same rules a prompt is.
    ``option_ids`` are **Cofferdam's own** identifiers — ``opt1``, ``opt2`` —
    checked against the question they claim to answer, so a client cannot send a
    choice that is not on the list and cannot send provider text back at all.
    """

    option_ids: Tuple[str, ...] = ()
    text: Optional[str] = None
    provenance: Optional[AnswerProvenance] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "option_ids": list(self.option_ids),
            "text": self.text,
            "provenance": self.provenance.to_dict() if self.provenance else None,
        }

    @classmethod
    def from_request(
        cls,
        payload: Any,
        *,
        clarification: "PendingClarification",
        provenance: AnswerProvenance,
    ) -> "ClarificationAnswer":
        """Build an answer from a request body, refusing anything that is not one.

        Six refusals, and the first is the one this whole file exists for: a body
        carrying **any** approval-shaped key is refused by name, even when the
        rest of it looks like a perfectly good answer. A client that sends
        ``{"answer": "yes", "tool_name": "Bash"}`` does not get an answer with an
        ignored extra field — it gets a refusal, because the alternative is a
        surface that quietly accepts half of a permission grant.
        """
        if not isinstance(payload, dict):
            raise ClarificationInvalid("an answer must be an object")
        intruders = sorted(set(payload) & _APPROVAL_SHAPED_KEYS)
        if intruders:
            raise ClarificationInvalid(
                "this is not where tool permissions are decided: " + intruders[0]
            )

        selected = _read_option_ids(payload.get("option_ids"), clarification)
        text = payload.get("answer")
        if text is not None:
            if not isinstance(text, str):
                raise ClarificationInvalid("an answer must be text")
            if not valid_user_text(text, MAX_ANSWER_CHARS):
                # Refused, never truncated. A person can retype a shorter answer;
                # silently sending a truncated one to a model is a different
                # answer than the one they gave.
                raise ClarificationInvalid(
                    "an answer must be under "
                    + str(MAX_ANSWER_CHARS)
                    + " characters and contain no control characters"
                )

        if not selected and not text:
            raise ClarificationInvalid("an answer cannot be empty")
        if clarification.answer_mode in CHOICE_ANSWER_MODES and not selected:
            # A question that offered choices and got prose instead. Refused
            # rather than forwarded: the provider asked for a selection, and
            # inventing one from free text is exactly the guess this module
            # refuses to make.
            raise ClarificationInvalid("that question is answered by choosing an option")
        if clarification.answer_mode == ANSWER_MODE_SINGLE_CHOICE and len(selected) > 1:
            raise ClarificationInvalid("that question takes one option")
        if clarification.answer_mode == ANSWER_MODE_FREE_TEXT and selected:
            raise ClarificationInvalid("that question has no options to choose from")

        return cls(option_ids=selected, text=text, provenance=provenance)


def _read_option_ids(
    value: Any, clarification: "PendingClarification"
) -> Tuple[str, ...]:
    """Selected option identifiers, checked against the question's own list.

    Every identifier must be one this question actually offers. That check is
    what makes the option channel safe to expose: the set of things a client can
    say is the set Cofferdam wrote down a moment ago, so there is no string a
    client can send that reaches a provider unrecognised.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ClarificationInvalid("option ids must be a list")
    if len(value) > MAX_SELECTED_OPTIONS:
        raise ClarificationInvalid("too many options were selected")

    offered = {option.option_id for option in clarification.options if option.option_id}
    selected: List[str] = []
    for entry in value:
        if not isinstance(entry, str) or not _OPTION_ID.match(entry):
            raise ClarificationInvalid("that is not an option identifier")
        if entry not in offered:
            raise ClarificationInvalid("that option is not offered by this question")
        if entry in selected:
            # Refused rather than de-duplicated. "A, A, B" is not a request
            # anybody meant to make, and answering it as "A, B" would be
            # answering something slightly different from what arrived.
            raise ClarificationInvalid("that option was selected twice")
        selected.append(entry)
    return tuple(selected)


@dataclass(frozen=True)
class PendingClarification:
    """One question a delegated session asked, as Cofferdam durably records it.

    Everything here is either an identifier Cofferdam minted, a value from a
    closed vocabulary, or bounded sanitized text. There is no ``raw``, no
    ``payload``, no ``tool_input`` and no field an SDK object could occupy — the
    same absence :mod:`.delegated` relies on, restated at the storage layer
    because this is the shape that outlives the process.
    """

    question_id: str
    task_id: str
    provider: str
    question: str
    requested_at: str
    answer_mode: str = ANSWER_MODE_UNKNOWN
    options: Tuple[ClarificationOption, ...] = ()
    provider_session_id: Optional[str] = None
    provider_event_id: Optional[str] = None
    provider_sequence: int = 0
    schema_verified: bool = False
    status: str = STATUS_PENDING
    answered_at: Optional[str] = None
    answer: Optional[ClarificationAnswer] = None

    @property
    def category(self) -> str:
        """Always ``clarification``. There is no other value this can take.

        A property rather than a stored column: a discriminator that could be
        *written* is a discriminator somebody can write the wrong value into, and
        the whole point of this type is that it has exactly one category.
        """
        return CATEGORY_CLARIFICATION

    @property
    def pending(self) -> bool:
        return self.status == STATUS_PENDING

    @property
    def allows_free_text(self) -> bool:
        return self.answer_mode in (ANSWER_MODE_FREE_TEXT, ANSWER_MODE_UNKNOWN)

    def to_dict(self, *, include_answer: bool = True) -> Dict[str, Any]:
        """The bounded wire shape. Never contains a provider payload.

        The provider *session* id is deliberately absent from what a client is
        given: a client never needs it, an answer is addressed by task and
        question id alone, and publishing it would put a handle to a live agent
        conversation in a response that a future bridge might one day relay.
        """
        payload: Dict[str, Any] = {
            "version": CLARIFICATION_VERSION,
            "category": CATEGORY_CLARIFICATION,
            "question_id": self.question_id,
            "task_id": self.task_id,
            "provider": self.provider,
            "question": self.question,
            "answer_mode": self.answer_mode,
            "allows_free_text": self.allows_free_text,
            "schema_verified": self.schema_verified,
            "options": [option.to_dict() for option in self.options],
            "requested_at": self.requested_at,
            "status": self.status,
            "answered_at": self.answered_at,
        }
        if include_answer and self.answer is not None:
            payload["answer"] = self.answer.to_dict()
        return payload

    def as_request(self) -> ClarificationRequest:
        """The same question as a delegated-event request. Round-trips cleanly."""
        return ClarificationRequest.from_dict(
            {
                "category": CATEGORY_CLARIFICATION,
                "question": self.question,
                "options": [option.to_dict() for option in self.options],
                "allows_free_text": self.allows_free_text,
                "answer_mode": self.answer_mode,
                "schema_verified": self.schema_verified,
            }
        )


def build_pending(
    *,
    task_id: str,
    provider: str,
    request: ClarificationRequest,
    requested_at: str,
    provider_session_id: Optional[str] = None,
    provider_event_id: Optional[str] = None,
    provider_sequence: int = 0,
    question_id: Optional[str] = None,
) -> PendingClarification:
    """One pending clarification from a normalized delegated request.

    The only supported constructor. Every string is bounded here, so a caller
    cannot decide how much of the database one question uses by passing a longer
    one, and identifiers that are not identifiers are dropped to ``None`` rather
    than stored — an event whose provider event id was malformed is still an
    event that happened, it simply loses its duplicate suppression, which is the
    safe direction for that failure to fall.
    """
    if not isinstance(request, ClarificationRequest):
        raise ClarificationInvalid("that is not a clarification request")
    question = safe_text(request.question, MAX_QUESTION_CHARS)
    if question is None:
        raise ClarificationInvalid("a clarification must ask something")
    mode = request.answer_mode if request.answer_mode in ANSWER_MODES else ANSWER_MODE_UNKNOWN
    return PendingClarification(
        question_id=question_id if valid_question_id(question_id) else new_question_id(),
        task_id=task_id,
        provider=safe_line(provider, MAX_PROVIDER_CHARS) or "unknown",
        question=question,
        answer_mode=mode,
        options=tuple(request.options[:MAX_OPTIONS]),
        provider_session_id=_identifier(
            provider_session_id, MAX_PROVIDER_SESSION_ID_CHARS
        ),
        provider_event_id=_identifier(provider_event_id, MAX_PROVIDER_EVENT_ID_CHARS),
        provider_sequence=max(0, int(provider_sequence)),
        schema_verified=bool(request.schema_verified),
        requested_at=safe_line(requested_at, 40) or "",
        status=STATUS_PENDING,
    )


def _identifier(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str) or not _IDENTIFIER.match(value):
        return None
    return value[:limit]


def encode_answer(
    clarification: PendingClarification, answer: ClarificationAnswer
) -> str:
    """The exact text an accepted answer becomes, composed entirely in this file.

    The one function that turns a person's answer into something a provider will
    read, and it is deliberately dull. Cofferdam's own connecting words, the
    labels of the options *Cofferdam itself stored* — reached through the
    identifiers the client sent, never through a string the client sent — and the
    person's own text, unaltered.

    There is no template read from a payload, no formatting directive, no
    instruction sentence, and nothing a client can put into the structure of the
    message rather than its content. That matters more here than anywhere else in
    the codebase: this string is the one place where text that arrived over the
    network becomes text a language model acts on.
    """
    parts: List[str] = []
    if answer.option_ids:
        chosen = [
            option.label
            for option in clarification.options
            if option.option_id in answer.option_ids
        ]
        if chosen:
            parts.append("Selected: " + ", ".join(chosen))
    if answer.text:
        parts.append(answer.text)
    if not parts:  # pragma: no cover - an empty answer is refused before this
        raise ClarificationInvalid("an answer cannot be empty")
    return "\n".join(parts)


def supersede(
    clarification: PendingClarification, *, status: str, at: str
) -> PendingClarification:
    """Close a pending question without answering it.

    Used when a task is cancelled and when a provider asks something new before
    the previous question was answered. The status is what distinguishes those
    two, and keeping them distinct is the reason this takes a status rather than
    having two nearly identical functions that would drift.

    A question that is already closed is returned untouched: closing it twice
    would move an answered question's status to ``superseded`` and lose the fact
    that somebody answered it.
    """
    if status not in (STATUS_CANCELLED, STATUS_SUPERSEDED):
        raise ClarificationInvalid("a question is closed as cancelled or superseded")
    if clarification.status in CLOSED_STATUSES:
        return clarification
    return PendingClarification(
        question_id=clarification.question_id,
        task_id=clarification.task_id,
        provider=clarification.provider,
        question=clarification.question,
        answer_mode=clarification.answer_mode,
        options=clarification.options,
        provider_session_id=clarification.provider_session_id,
        provider_event_id=clarification.provider_event_id,
        provider_sequence=clarification.provider_sequence,
        schema_verified=clarification.schema_verified,
        requested_at=clarification.requested_at,
        status=status,
        answered_at=safe_line(at, 40),
        answer=None,
    )


def answered(
    clarification: PendingClarification, answer: ClarificationAnswer, *, at: str
) -> PendingClarification:
    """The same question, recorded as answered. Refuses a second answer."""
    if clarification.status != STATUS_PENDING:
        raise ClarificationInvalid("that question is already " + clarification.status)
    return PendingClarification(
        question_id=clarification.question_id,
        task_id=clarification.task_id,
        provider=clarification.provider,
        question=clarification.question,
        answer_mode=clarification.answer_mode,
        options=clarification.options,
        provider_session_id=clarification.provider_session_id,
        provider_event_id=clarification.provider_event_id,
        provider_sequence=clarification.provider_sequence,
        schema_verified=clarification.schema_verified,
        requested_at=clarification.requested_at,
        status=STATUS_ANSWERED,
        answered_at=safe_line(at, 40),
        answer=answer,
    )


def answer_summary(answer: ClarificationAnswer) -> str:
    """One bounded line for a task's history: shape, never content.

    An answer is somebody's private text and belongs on the task, not repeated
    through the event stream — the same rule Task Core already applies to a
    follow-up, which records "Follow-up received (N characters)" and not the
    follow-up.
    """
    parts: List[str] = []
    if answer.option_ids:
        parts.append(str(len(answer.option_ids)) + " option(s) chosen")
    if answer.text:
        parts.append(str(len(answer.text)) + " characters")
    return "Answer received (" + ", ".join(parts) + ")." if parts else "Answer received."


def options_from_observed(entries: Sequence[Any]) -> Tuple[ClarificationOption, ...]:
    """Adapter-side helper: observed options as delegated options.

    Lives here rather than in the adapter so that both ends of the round trip —
    the question that was stored and the answer that is checked against it —
    agree on how an option becomes a record, by construction rather than by two
    similar functions in different packages.
    """
    built: List[ClarificationOption] = []
    for entry in list(entries)[:MAX_OPTIONS]:
        label = safe_line(getattr(entry, "label", None), 120)
        if label is None:
            continue
        identifier = getattr(entry, "option_id", None)
        built.append(
            ClarificationOption(
                label=label,
                value=label,
                option_id=identifier if isinstance(identifier, str) else None,
                description=safe_line(getattr(entry, "description", None), 240),
            )
        )
    return tuple(built)


__all__ = [
    "ACCEPTED_ANSWER_SOURCES",
    "ANSWER_SOURCES",
    "CLARIFICATION_STATUSES",
    "CLARIFICATION_VERSION",
    "CLOSED_STATUSES",
    "MAX_ANSWER_CHARS",
    "MAX_PENDING_PER_TASK",
    "MAX_SELECTED_OPTIONS",
    "OUTCOME_ACCEPTED",
    "OUTCOME_REJECTED",
    "QUESTION_ID_CHARS",
    "QUESTION_ID_PREFIX",
    "SOURCE_FUTURE_GPT_BRIDGE",
    "SOURCE_INTERNAL_TEST",
    "SOURCE_WORKSTATION_PWA",
    "STATUS_ANSWERED",
    "STATUS_CANCELLED",
    "STATUS_PENDING",
    "STATUS_SUPERSEDED",
    "AnswerProvenance",
    "ClarificationAnswer",
    "ClarificationInvalid",
    "PendingClarification",
    "answer_summary",
    "answered",
    "build_pending",
    "encode_answer",
    "new_question_id",
    "options_from_observed",
    "supersede",
    "valid_question_id",
]
