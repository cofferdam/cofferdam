"""What a person decided, kept as its own fact beside what the model said.

The rule this module is shaped around
-------------------------------------

**A model result is never rewritten into human authority.** A ``PlannerResult``
is model-authored data: it says what the planner decided. An answer, an approval
or a rejection is a *different* authority, authored by a person, and it lives in
a different record with its own identity, its own provenance and its own
timestamp.

The tempting shortcut is to move the planner row along — set ``action`` to
``APPROVED``, blank the ``worker_prompt`` on a rejection, overwrite
``user_question`` with the answer. Every one of those destroys the only durable
evidence of what the model actually produced, and the question a person asks six
weeks later is almost always *what did it propose, and what did I agree to* —
which is two facts, and unanswerable from one row.

So this module defines the second fact, and :mod:`.store` gives it a table of its
own. The planner row is never updated by anything here.

Three things that are not each other
------------------------------------

======================  =========================================================
invocation lifecycle    ``pending`` ``running`` ``succeeded`` ``failed``
                        ``interrupted`` — what the *call* did
planner action          ``ASK_USER`` ``PREPARE_WORKER_PROMPT`` ``STOP`` — what the
                        *model* decided
human gate              ``awaiting_answer`` ``answered`` ``awaiting_confirmation``
                        ``approved`` ``rejected`` — what the *person* decided
======================  =========================================================

A ``STOP`` is not a rejection and neither is a failure. A model declining to
plan, a provider breaking, and a person refusing a prepared prompt are three
different sentences, and this milestone keeps three different words for them.

The gate is derived, never chosen
---------------------------------

Which decision a planner request is waiting for follows deterministically from
the persisted, already-validated result. There is no parameter anywhere that
lets a caller treat a ``PREPARE_WORKER_PROMPT`` as a question, or a question as
something approvable. :func:`derive_gate` is the only place that mapping exists.

Nothing here executes
---------------------

An answer may contain prose, a code snippet, a URL, or text that looks exactly
like a shell command. It is recorded as text and read back as text. Approving a
prepared prompt records that a person approved it — it starts nothing, and there
is no function in this package that could.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .errors import PlannerAuthorityInvalid
from .hashing import authority_subject_fingerprint, valid_fingerprint
from .models import (
    ACTION_ASK_USER,
    ACTION_PREPARE_WORKER_PROMPT,
    ACTION_STOP,
    PLANNER_RESULT_SCHEMA_VERSION,
)
from .store import STATUS_SUCCEEDED, AuthorityEvent, PlannerRecord

# -- gate kinds ---------------------------------------------------------------
#
# What kind of human decision a planner result is waiting for. Not a status —
# the kind is a property of the *result*, and it never changes once the result
# is persisted. The state below is what moves.

#: A question is waiting for an answer.
GATE_ANSWER = "answer"
#: A prepared worker prompt is waiting to be approved or refused.
GATE_CONFIRMATION = "confirmation"
#: Nothing is waiting for a person. A ``STOP``, a failed invocation, a result
#: this build cannot read. :attr:`HumanGate.no_gate_reason` says which.
GATE_NONE = "none"

GATE_KINDS: Tuple[str, ...] = (GATE_ANSWER, GATE_CONFIRMATION, GATE_NONE)

# -- gate states --------------------------------------------------------------

GATE_STATE_NOT_REQUIRED = "not_required"
GATE_STATE_AWAITING_ANSWER = "awaiting_answer"
GATE_STATE_ANSWERED = "answered"
GATE_STATE_AWAITING_CONFIRMATION = "awaiting_confirmation"
GATE_STATE_APPROVED = "approved"
GATE_STATE_REJECTED = "rejected"

GATE_STATES: Tuple[str, ...] = (
    GATE_STATE_NOT_REQUIRED,
    GATE_STATE_AWAITING_ANSWER,
    GATE_STATE_ANSWERED,
    GATE_STATE_AWAITING_CONFIRMATION,
    GATE_STATE_APPROVED,
    GATE_STATE_REJECTED,
)

# -- why no gate --------------------------------------------------------------
#
# A closed vocabulary, because "why is nothing waiting on this" is a question
# somebody asks much later and free-form text would answer it differently every
# time. `planner_stopped` and `invocation_did_not_succeed` are deliberately
# distinct: the model declining to plan is not the provider breaking.

NO_GATE_PLANNER_STOPPED = "planner_stopped"
NO_GATE_INVOCATION_DID_NOT_SUCCEED = "invocation_did_not_succeed"
NO_GATE_RESULT_INCOMPLETE = "result_incomplete"
NO_GATE_RESULT_SCHEMA_UNSUPPORTED = "result_schema_unsupported"

NO_GATE_REASONS: Tuple[str, ...] = (
    NO_GATE_PLANNER_STOPPED,
    NO_GATE_INVOCATION_DID_NOT_SUCCEED,
    NO_GATE_RESULT_INCOMPLETE,
    NO_GATE_RESULT_SCHEMA_UNSUPPORTED,
)

# -- authority actions --------------------------------------------------------
#
# **The list is the assertion.** There is no `edit_and_approve`, no
# `approve_and_dispatch`, no `run`, no `override` and no `replan`, and the
# honest form of "PR1d does not dispatch" is not a check that refuses those
# words — it is a vocabulary that does not contain them.
#
# `edit_and_approve` is the one worth naming, because it is the one somebody
# will want. Editing model output inside an approval primitive would produce a
# record saying a person approved a prompt that no planner ever wrote, with the
# planner's provenance attached to it. The safe shape is to reject and take
# another planner step, and that shape belongs to a later PR.

AUTHORITY_ANSWER = "answer"
AUTHORITY_APPROVE = "approve"
AUTHORITY_REJECT = "reject"

AUTHORITY_ACTIONS: Tuple[str, ...] = (
    AUTHORITY_ANSWER,
    AUTHORITY_APPROVE,
    AUTHORITY_REJECT,
)

#: Which actions each gate kind permits. The mapping is the refusal: an action
#: that is not in a gate's tuple is not something a caller can argue about.
PERMITTED_ACTIONS: Dict[str, Tuple[str, ...]] = {
    GATE_ANSWER: (AUTHORITY_ANSWER,),
    GATE_CONFIRMATION: (AUTHORITY_APPROVE, AUTHORITY_REJECT),
    GATE_NONE: (),
}

#: The state each action leaves the gate in. Every one is terminal in this
#: milestone — see :mod:`.authority_service` for why a second, contradictory
#: decision is a refusal rather than an update.
TERMINAL_STATE_FOR_ACTION: Dict[str, str] = {
    AUTHORITY_ANSWER: GATE_STATE_ANSWERED,
    AUTHORITY_APPROVE: GATE_STATE_APPROVED,
    AUTHORITY_REJECT: GATE_STATE_REJECTED,
}

# -- who decided --------------------------------------------------------------
#
# `actor` is a one-value vocabulary and that is the point. A human authority
# record cannot be attributed to `system`, `planner` or `adapter`, because there
# is no such value to write — which is a stronger statement than a check that
# would refuse them.

ACTOR_USER = "user"
AUTHORITY_ACTORS: Tuple[str, ...] = (ACTOR_USER,)

# `source` is which trusted surface asserted it. Code-owned and closed, assigned
# by the surface from its own authenticated context and **never read from a
# request body** — the rule Task Core's clarification provenance already holds.

#: An in-process caller on this workstation. The honest description of what this
#: build actually knows: something inside the trusted local process asked to
#: record a decision. It is not a claim about *which person*, and this module
#: does not pretend otherwise — there is no cryptographic user identity here to
#: report, so none is reported.
SOURCE_LOCAL_CALL = "local_call"

#: A test or internal tool inside this process. Present so a test does not have
#: to claim to be a real surface, which is what makes the field useful for
#: telling real decisions from synthetic ones in a stored history.
SOURCE_INTERNAL_TEST = "internal_test"

#: The private PWA on this workstation's own network. **Reserved and unwritable
#: in this build**: no route exists, so nothing can produce it — the same
#: doctrine Mind's `SOURCE_PLANNER` follows. A vocabulary entry is not an
#: enabled surface, and the day a route arrives it should be an explicit change
#: here rather than a value that was already accepted.
SOURCE_WORKSTATION_PWA = "workstation_pwa"

AUTHORITY_SOURCES: Tuple[str, ...] = (
    SOURCE_LOCAL_CALL,
    SOURCE_INTERNAL_TEST,
    SOURCE_WORKSTATION_PWA,
)

#: The sources a caller in **this** build may attribute a decision to.
ACCEPTED_AUTHORITY_SOURCES = frozenset({SOURCE_LOCAL_CALL, SOURCE_INTERNAL_TEST})

# -- bounds -------------------------------------------------------------------

#: How long an answer to a planner question may be.
#:
#: Larger than Task Core's 2000-character clarification answer and far smaller
#: than the 20000-character worker prompt, and both comparisons are deliberate.
#: A planner ``ASK_USER`` is usually an architecture decision, so an answer may
#: reasonably carry a short snippet — but a field sized like a prompt invites
#: somebody to restate the whole task into it, which is a planning turn, not an
#: answer.
MAX_ANSWER_CHARS = 4000

#: An optional note on a refusal. Bounded to a paragraph: a rejection reason is
#: for the person reading the history later, not a place to write the next plan.
MAX_REJECTION_REASON_CHARS = 500

AUTHORITY_EVENT_ID_PREFIX = "auth_"
_AUTHORITY_EVENT_ID_BYTES = 13

_FORBIDDEN_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def new_authority_event_id() -> str:
    """An identifier for one human decision. Opaque, and content-free.

    Takes no argument, so there is no parameter the answer text could arrive in
    — the property ``new_planner_request_id`` and Task Core's ``new_task_id``
    are both built around. An id derived from what somebody wrote would put a
    fingerprint of their answer into every log line that carries it.
    """
    return AUTHORITY_EVENT_ID_PREFIX + secrets.token_hex(_AUTHORITY_EVENT_ID_BYTES)


def _clean_text(value: Any, *, limit: int, label: str) -> str:
    """Bounded human text, refused rather than repaired.

    Truncating would store a different sentence from the one somebody wrote and
    then attribute it to them, which is worse than refusing and letting them
    write a shorter one.
    """
    if not isinstance(value, str):
        raise PlannerAuthorityInvalid(f"{label} must be text")
    if _FORBIDDEN_CONTROL.search(value):
        raise PlannerAuthorityInvalid(f"{label} contains control characters")
    normalized = unicodedata.normalize("NFC", value)
    if not normalized.strip():
        raise PlannerAuthorityInvalid(f"{label} must not be empty")
    if len(normalized) > limit:
        raise PlannerAuthorityInvalid(
            f"{label} exceeds {limit} characters", detail=str(len(normalized))
        )
    # The stored value is what was typed. Normalization is for the length check
    # only, exactly as Task Core's `valid_user_text` does it.
    return value


def clean_answer(value: Any) -> str:
    """One person's answer, bounded. **Never interpreted.**

    Whatever comes back from here is a string that gets written to a column and
    read out of it. It is not parsed as shell, argv, a provider flag, an MCP
    method, a path or an instruction to anything. Text that looks like a command
    is text that looks like a command.
    """
    return _clean_text(value, limit=MAX_ANSWER_CHARS, label="an answer")


def clean_rejection_reason(value: Any) -> Optional[str]:
    """An optional note on a refusal, or nothing."""
    if value is None:
        return None
    return _clean_text(
        value, limit=MAX_REJECTION_REASON_CHARS, label="a rejection reason"
    )


@dataclass(frozen=True)
class AuthorityProvenance:
    """Where one decision came from, in bounded, code-owned words.

    Every field is a value from a closed vocabulary. There is no display name,
    no address, no header, no user agent, no token — and no field one could be
    put into.

    What this honestly claims, and what it does not
    -----------------------------------------------

    It claims: *a decision of this category arrived through this surface at this
    time*. It does not claim that a particular person authenticated, because on
    this host nothing proves that yet. Recording a name or an account here would
    be inventing evidence, and an authority record that overstates what it knows
    is worse than one that is narrow and true.
    """

    actor: str
    source: str

    def __post_init__(self) -> None:
        if self.actor not in AUTHORITY_ACTORS:
            raise PlannerAuthorityInvalid(
                "a human authority record has no actor but the person",
                detail=str(self.actor),
            )
        if self.source not in AUTHORITY_SOURCES:
            raise PlannerAuthorityInvalid(
                "unknown authority source", detail=str(self.source)
            )
        if self.source not in ACCEPTED_AUTHORITY_SOURCES:
            # In the vocabulary, not enabled. The PWA value exists so that the
            # day a route arrives it is a visible decision here.
            raise PlannerAuthorityInvalid(
                "no surface in this build may attribute a decision to that source",
                detail=str(self.source),
            )

    @classmethod
    def local_call(cls) -> "AuthorityProvenance":
        return cls(actor=ACTOR_USER, source=SOURCE_LOCAL_CALL)

    @classmethod
    def internal_test(cls) -> "AuthorityProvenance":
        return cls(actor=ACTOR_USER, source=SOURCE_INTERNAL_TEST)

    def to_dict(self) -> Dict[str, Any]:
        return {"actor": self.actor, "source": self.source}


def resulting_state(event: AuthorityEvent) -> str:
    """The gate state one recorded decision produces. Every one is terminal."""
    return TERMINAL_STATE_FOR_ACTION[event.authority_action]


@dataclass(frozen=True)
class HumanGate:
    """What a planner request is waiting for, and what it got.

    Derived on every read rather than stored as a column. A stored gate state
    would be a second authority for the same fact, and the day it disagreed with
    the events table there would be no way to tell which one was lying.
    """

    planner_request_id: str
    kind: str
    state: str
    subject_fingerprint: Optional[str] = None
    no_gate_reason: Optional[str] = None
    event: Optional[AuthorityEvent] = None

    @property
    def required(self) -> bool:
        """Whether this planner result calls for a human decision at all."""
        return self.kind != GATE_NONE

    @property
    def awaiting_human(self) -> bool:
        return self.state in (
            GATE_STATE_AWAITING_ANSWER,
            GATE_STATE_AWAITING_CONFIRMATION,
        )

    @property
    def decided(self) -> bool:
        return self.event is not None

    @property
    def permitted_actions(self) -> Tuple[str, ...]:
        """What a person may still do. Empty once a decision exists."""
        if self.decided:
            return ()
        return PERMITTED_ACTIONS[self.kind]

    @property
    def binds_current_subject(self) -> Optional[bool]:
        """Whether the decision still authorizes what is persisted *now*.

        The property a future dispatcher needs and the reason the fingerprint is
        stored at all: it can hold the persisted worker prompt, recompute, and
        prove the approval commits to exactly those bytes. ``None`` when there is
        no decision to check.

        A ``False`` here is not a corrupted record — it is the record working.
        The approval still says truthfully what was approved; what changed is the
        subject, and an approval of one thing does not carry over to another.
        """
        if self.event is None or self.subject_fingerprint is None:
            return None
        return self.event.subject_fingerprint == self.subject_fingerprint

    def to_dict(self) -> Dict[str, Any]:
        """The safe read shape.

        The answer *is* here once one exists: it is the person's own text, this
        is an internal read model with no route in front of it, and the layer
        that will eventually consume an answer needs it. What is not here is
        anything about the invocation's context packet, provider session or
        environment — none of which this type has ever held.
        """
        payload: Dict[str, Any] = {
            "gate_kind": self.kind,
            "gate_state": self.state,
            "required": self.required,
            "awaiting_human": self.awaiting_human,
            "permitted_actions": list(self.permitted_actions),
            "subject_fingerprint": self.subject_fingerprint,
            "no_gate_reason": self.no_gate_reason,
            "decision": None,
        }
        if self.event is not None:
            decision = self.event.to_dict(include_answer=self.kind == GATE_ANSWER)
            decision["binds_current_subject"] = self.binds_current_subject
            payload["decision"] = decision
        return payload


def gate_subject(record: PlannerRecord) -> Optional[str]:
    """The exact model-authored text a decision on this record would bind to.

    One artefact per action, which the result validator already guarantees: an
    ``ASK_USER`` carries a question and no prompt, a ``PREPARE_WORKER_PROMPT``
    carries a prompt and no question.
    """
    if record.action == ACTION_ASK_USER:
        return record.user_question
    if record.action == ACTION_PREPARE_WORKER_PROMPT:
        return record.worker_prompt
    return None


def derive_gate(
    record: PlannerRecord, *, event: Optional[AuthorityEvent] = None
) -> HumanGate:
    """The gate a persisted planner result requires. **The only such mapping.**

    Deterministic, and derived from the row rather than from anything a caller
    said. There is no argument here that could make a prepared prompt answerable
    or a question approvable, which is what stops "treat this
    ``PREPARE_WORKER_PROMPT`` as an ``ASK_USER``" from being expressible at all.

    Four ways a row yields no gate, kept apart because they are four different
    sentences:

    * the invocation never succeeded — there is no model result to authorize;
    * the model said ``STOP`` — there is a result, and it asks for nothing;
    * the result speaks a schema version this build does not;
    * the result is missing the artefact its action requires, which should be
      impossible after validation and is therefore refused rather than guessed
      at.
    """

    def no_gate(reason: str) -> HumanGate:
        return HumanGate(
            planner_request_id=record.planner_request_id,
            kind=GATE_NONE,
            state=GATE_STATE_NOT_REQUIRED,
            no_gate_reason=reason,
        )

    if record.status != STATUS_SUCCEEDED:
        return no_gate(NO_GATE_INVOCATION_DID_NOT_SUCCEED)
    if record.action == ACTION_STOP:
        return no_gate(NO_GATE_PLANNER_STOPPED)
    if record.action not in (ACTION_ASK_USER, ACTION_PREPARE_WORKER_PROMPT):
        # No action on a succeeded row, or one this build does not know.
        return no_gate(NO_GATE_RESULT_INCOMPLETE)
    if record.result_schema_version != PLANNER_RESULT_SCHEMA_VERSION:
        return no_gate(NO_GATE_RESULT_SCHEMA_UNSUPPORTED)

    subject = gate_subject(record)
    if not (subject or "").strip():
        return no_gate(NO_GATE_RESULT_INCOMPLETE)

    kind = GATE_ANSWER if record.action == ACTION_ASK_USER else GATE_CONFIRMATION
    fingerprint = authority_subject_fingerprint(
        planner_request_id=record.planner_request_id,
        result_schema_version=record.result_schema_version,
        action=record.action,
        subject=subject,
    )
    waiting = (
        GATE_STATE_AWAITING_ANSWER
        if kind == GATE_ANSWER
        else GATE_STATE_AWAITING_CONFIRMATION
    )
    return HumanGate(
        planner_request_id=record.planner_request_id,
        kind=kind,
        state=resulting_state(event) if event is not None else waiting,
        subject_fingerprint=fingerprint,
        event=event,
    )


__all__ = [
    "ACCEPTED_AUTHORITY_SOURCES",
    "ACTOR_USER",
    "AUTHORITY_ACTIONS",
    "AUTHORITY_ACTORS",
    "AUTHORITY_ANSWER",
    "AUTHORITY_APPROVE",
    "AUTHORITY_EVENT_ID_PREFIX",
    "AUTHORITY_REJECT",
    "AUTHORITY_SOURCES",
    "GATE_ANSWER",
    "GATE_CONFIRMATION",
    "GATE_KINDS",
    "GATE_NONE",
    "GATE_STATES",
    "GATE_STATE_ANSWERED",
    "GATE_STATE_APPROVED",
    "GATE_STATE_AWAITING_ANSWER",
    "GATE_STATE_AWAITING_CONFIRMATION",
    "GATE_STATE_NOT_REQUIRED",
    "GATE_STATE_REJECTED",
    "MAX_ANSWER_CHARS",
    "MAX_REJECTION_REASON_CHARS",
    "NO_GATE_INVOCATION_DID_NOT_SUCCEED",
    "NO_GATE_PLANNER_STOPPED",
    "NO_GATE_REASONS",
    "NO_GATE_RESULT_INCOMPLETE",
    "NO_GATE_RESULT_SCHEMA_UNSUPPORTED",
    "PERMITTED_ACTIONS",
    "SOURCE_INTERNAL_TEST",
    "SOURCE_LOCAL_CALL",
    "SOURCE_WORKSTATION_PWA",
    "TERMINAL_STATE_FOR_ACTION",
    "AuthorityEvent",
    "AuthorityProvenance",
    "HumanGate",
    "clean_answer",
    "clean_rejection_reason",
    "derive_gate",
    "gate_subject",
    "new_authority_event_id",
    "resulting_state",
    "valid_fingerprint",
]
