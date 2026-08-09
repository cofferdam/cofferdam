"""Turns, and the durable result a finished turn leaves behind.

One Cofferdam task can be several provider turns. That sentence is the whole
reason this module exists, and everything in it follows from refusing the two
shortcuts that would have avoided writing it.

**The first shortcut: let the task row hold the result.** It already has a
``final_result`` column, and ``store.transition`` writes it with
``COALESCE(?, final_result)`` — so a second turn's result silently replaces the
first one's. For a one-turn task that is correct and has been correct since M2F.
For a conversation it destroys evidence: the answer somebody read, acted on, and
is now asking a follow-up *about* is gone, and nothing in the history says it
was ever there. A turn is therefore its own row, and a turn that has completed
is never written again.

**The second shortcut: let the adapter remember.** M2I PR1 did, honestly and on
purpose — :meth:`ClaudeAgentSdkAdapter.result_for` is a dictionary in memory and
its own docstring says so. It cannot survive a restart, which means it cannot
answer the one question ``get_result`` exists to answer: *what did this task
produce*, asked by something that was not connected when it produced it.

What a turn is, and is not
--------------------------

A turn is **the provider's unit of work and Cofferdam's unit of evidence**: one
user message in, one terminal outcome out. It is not a transcript, not a copy of
the task, and not a second lifecycle — a turn has no state machine, only a
``completed_at`` that is null or is not.

There is deliberately no field here for a message list, an assistant reply
sequence, a tool call, a thinking block or a provider payload. What a turn keeps
is what a person could be shown and an auditor could check: which session
produced it, which turn it was, when it started and ended, how it ended, and the
one bounded piece of text the provider offered as its answer.

The vocabulary is shared, not copied
------------------------------------

``source`` uses the same three words :mod:`.clarifications` uses, imported
rather than re-declared. They answer the same question — *which surface did this
instruction arrive on* — and two lists that must agree are a list that will one
day disagree. ``future_gpt_bridge`` is reserved and, exactly as it is there, not
accepted by anything in this build.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .clarifications import (
    SOURCE_FUTURE_GPT_BRIDGE,
    SOURCE_INTERNAL_TEST,
    SOURCE_WORKSTATION_PWA,
)
from .delegated import (
    KIND_CANCELLED,
    KIND_INTERRUPTED,
    KIND_PROVIDER_FAILED,
    KIND_SUCCEEDED,
    MAX_FAILURE_CODE_CHARS,
    MAX_FAILURE_SUMMARY_CHARS,
    MAX_PROVIDER_CHARS,
    MAX_PROVIDER_SESSION_ID_CHARS,
    MAX_RESULT_TEXT_CHARS,
    safe_line,
    safe_text,
)
from .models import (
    EVIDENCE_ADAPTER_REPORTED,
    ORIGIN_CHATGPT_APP,
    ORIGIN_CLI,
    ORIGIN_OPERA_COMPANION,
    ORIGIN_PWA,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_READY_FOR_FOLLOWUP,
    TERMINAL_STATES,
)

#: Bumped when the published result shape changes in a way a client could
#: notice. Separate from ``TASK_API_VERSION`` because a bridge may one day read
#: results without reading tasks, and a version it cannot interpret should be a
#: refusal rather than a mis-render.
TASK_RESULT_VERSION = 1

# -- where a follow-up came from ---------------------------------------------
#
# One vocabulary, two uses. See the module docstring.

FOLLOWUP_SOURCES: Tuple[str, ...] = (
    SOURCE_WORKSTATION_PWA,
    SOURCE_INTERNAL_TEST,
    SOURCE_FUTURE_GPT_BRIDGE,
)

#: The sources a route in **this** build may attribute a follow-up to. The
#: bridge is deliberately absent, for the same reason it is absent from
#: ``clarifications.ACCEPTED_ANSWER_SOURCES``: a reserved word is not an enabled
#: surface, and a frozenset is what makes that difference enforceable.
ACCEPTED_FOLLOWUP_SOURCES = frozenset({SOURCE_WORKSTATION_PWA, SOURCE_INTERNAL_TEST})

#: Which source opened a task's **first** turn — the one the prompt began rather
#: than a follow-up. Derived from the task's origin, which the server assigned
#: from the authenticated request context and no client can choose.
#:
#: The ``chatgpt_app`` entry is unreachable in this build: no route sets that
#: origin. It is written down anyway, because the alternative is a ``.get``
#: default that would one day quietly file a bridge-created task under
#: ``internal_test`` — a mislabel in exactly the direction provenance exists to
#: prevent.
SOURCE_FOR_ORIGIN: Dict[str, str] = {
    ORIGIN_PWA: SOURCE_WORKSTATION_PWA,
    ORIGIN_CLI: SOURCE_INTERNAL_TEST,
    ORIGIN_OPERA_COMPANION: SOURCE_INTERNAL_TEST,
    ORIGIN_CHATGPT_APP: SOURCE_FUTURE_GPT_BRIDGE,
}


def source_for_origin(origin: Any) -> str:
    """Which source opened a task's first turn, from the task's origin.

    Every member of :data:`~.models.ORIGINS` has an entry, so the fallback is
    reached only by an origin outside the closed vocabulary — which today means
    a test constructing one. ``internal_test`` is the truthful label for that,
    and it is not a default the bridge could ever land on: ``chatgpt_app`` maps
    to its own word.
    """
    return SOURCE_FOR_ORIGIN.get(origin, SOURCE_INTERNAL_TEST)


# -- how a turn ended ---------------------------------------------------------
#
# The four Task Core terminal states, reused rather than paralleled. A fifth
# private word here would be a second vocabulary meaning the same four things,
# and the first time they disagreed nobody would know which was authoritative.

TURN_OUTCOMES: Tuple[str, ...] = (
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_CANCELLED,
    STATE_INTERRUPTED,
)

#: A delegated event kind to the turn outcome it implies. The same mapping
#: :data:`~.delegated.TERMINAL_STATE_FOR_KIND` holds, restated here only so this
#: module can validate a stored value without importing the event layer's table
#: into its own vocabulary.
OUTCOME_FOR_KIND: Dict[str, str] = {
    KIND_SUCCEEDED: STATE_COMPLETED,
    KIND_PROVIDER_FAILED: STATE_FAILED,
    KIND_CANCELLED: STATE_CANCELLED,
    KIND_INTERRUPTED: STATE_INTERRUPTED,
}

# -- bounds -------------------------------------------------------------------

#: The most turns one task may accumulate. A conversation, not a chat log: a
#: task that has taken this many follow-ups is one that should be a new task
#: with a fresh context, and the bound is what stops a retry loop from growing a
#: task's storage without limit.
MAX_TURNS_PER_TASK = 64

#: The longest a stored turn's result text may be. The same bound Task Core
#: already applies to ``final_result``, named here because this table is the
#: durable copy and a reader should not have to go and find the other one.
MAX_TURN_RESULT_CHARS = MAX_RESULT_TEXT_CHARS


class TurnInvalid(ValueError):
    """A turn record that could not be built truthfully.

    Raised rather than repaired, for the same reason
    :class:`~.clarifications.ClarificationInvalid` is: every case that reaches
    it is one where guessing produces a row that reads as fact and is not one.
    """


@dataclass(frozen=True)
class TaskTurn:
    """One provider turn, as stored. Provider-neutral and bounded.

    ``turn_number`` is Cofferdam's own, allocated inside the transaction that
    inserts the row and starting at one. It is not the provider's — that is
    ``provider_turn_sequence``, kept beside it so a reader can tell whether the
    two agree without either being able to stand in for the other.

    A turn with ``completed_at`` set is finished, and finished is final: the
    store's update is guarded on the column being null, so a late report cannot
    rewrite an outcome somebody has already been shown.
    """

    task_id: str
    turn_number: int
    provider: str
    source: str
    started_at: str
    provider_session_id: Optional[str] = None
    provider_turn_sequence: int = 0
    #: The ``client_request_id`` of the follow-up that opened this turn, or
    #: ``None`` for the first turn, which the prompt opened. Stored so a retry
    #: that reaches the adapter twice can be recognised as one turn.
    followup_request_id: Optional[str] = None
    completed_at: Optional[str] = None
    outcome: Optional[str] = None
    result: Optional[str] = None
    failure_code: Optional[str] = None
    failure_summary: Optional[str] = None

    @property
    def completed(self) -> bool:
        return self.completed_at is not None

    @property
    def succeeded(self) -> bool:
        return self.outcome == STATE_COMPLETED

    def to_dict(self) -> Dict[str, Any]:
        """The bounded shape. Never published on its own — see :class:`TaskResult`.

        Used by tests and by the result assembler. There is no route that serves
        a list of turns, and adding one would be a decision about publishing a
        conversation's shape rather than its answer.
        """
        return {
            "task_id": self.task_id,
            "turn_number": self.turn_number,
            "provider": self.provider,
            "provider_session_id": self.provider_session_id,
            "provider_turn_sequence": self.provider_turn_sequence,
            "source": self.source,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "outcome": self.outcome,
            "result": self.result,
            "failure_code": self.failure_code,
            "failure_summary": self.failure_summary,
        }


def open_turn(
    *,
    task_id: str,
    turn_number: int,
    provider: Any,
    source: str,
    started_at: str,
    provider_session_id: Any = None,
    followup_request_id: Any = None,
) -> TaskTurn:
    """Build the record for a turn that is beginning. Every string bounded here."""
    if source not in FOLLOWUP_SOURCES:
        raise TurnInvalid("unknown follow-up source")
    if not isinstance(turn_number, int) or turn_number < 1:
        raise TurnInvalid("a turn number starts at one")
    return TaskTurn(
        task_id=task_id,
        turn_number=turn_number,
        provider=safe_line(provider, MAX_PROVIDER_CHARS) or "unknown",
        source=source,
        started_at=safe_line(started_at, 40) or "",
        provider_session_id=safe_line(
            provider_session_id, MAX_PROVIDER_SESSION_ID_CHARS
        ),
        followup_request_id=safe_line(followup_request_id, 128),
    )


def close_turn(
    turn: TaskTurn,
    *,
    outcome: str,
    completed_at: str,
    provider_session_id: Any = None,
    provider_turn_sequence: Any = 0,
    result: Any = None,
    failure_code: Any = None,
    failure_summary: Any = None,
) -> TaskTurn:
    """The same turn, finished. Refuses an outcome that is not one of the four.

    ``provider_session_id`` is *filled in* rather than overwritten: the id is
    often unknown when a turn opens and known by the time it ends, and a turn
    that learned its session late should record it. A turn that already had one
    keeps it, because a changed id means the stream is no longer the session
    this turn belongs to — which is a mismatch to report, never to adopt.
    """
    if outcome not in TURN_OUTCOMES:
        raise TurnInvalid("unknown turn outcome: " + str(outcome))
    return TaskTurn(
        task_id=turn.task_id,
        turn_number=turn.turn_number,
        provider=turn.provider,
        source=turn.source,
        started_at=turn.started_at,
        provider_session_id=turn.provider_session_id
        or safe_line(provider_session_id, MAX_PROVIDER_SESSION_ID_CHARS),
        provider_turn_sequence=max(0, int(provider_turn_sequence or 0)),
        followup_request_id=turn.followup_request_id,
        completed_at=safe_line(completed_at, 40) or "",
        outcome=outcome,
        result=(
            safe_text(result, MAX_TURN_RESULT_CHARS)
            if outcome == STATE_COMPLETED
            else None
        ),
        failure_code=(
            safe_line(failure_code, MAX_FAILURE_CODE_CHARS)
            if outcome == STATE_FAILED
            else None
        ),
        failure_summary=(
            safe_text(failure_summary, MAX_FAILURE_SUMMARY_CHARS)
            if outcome == STATE_FAILED
            else None
        ),
    )


# -- what a client is given ---------------------------------------------------


@dataclass(frozen=True)
class TaskResult:
    """The provider-neutral answer to "what did this task produce".

    **What ``result`` means, stated once and not left to be inferred: it is the
    latest *completed turn's* result.** For a terminal task that is also the
    final task result, and ``task_terminal`` says which case a reader is
    looking at. Both facts are separate fields rather than one implicit rule,
    because a bridge that guessed would guess wrong exactly when it matters —
    on a task whose first turn answered the question and whose session is still
    open for a second.

    What is absent is the contract. There is no transcript, no message list, no
    tool input, no stack trace, no hidden reasoning, no environment, no
    credential and no raw provider payload — and no field any of them could be
    put in. ``provider_session_id`` is present because provenance is the point
    of this shape, and it is a name for a conversation rather than a capability:
    nothing in Cofferdam accepts one from a caller.
    """

    task_id: str
    task_state: str
    task_terminal: bool
    #: **The task's disposition**, not the turn's — and the distinction is the
    #: one a caller is most likely to get wrong.
    #:
    #: For a terminal task this is how the *task* ended. For a live one it is
    #: the outcome of the latest turn that produced something. The difference
    #: matters in exactly one case, and it is a case that happens: a task whose
    #: first turn answered perfectly and which was then cancelled. ``result``
    #: still carries that answer, because it is real and somebody should be able
    #: to read it — and ``outcome`` says ``cancelled``, because the task was.
    #: A field that said ``completed`` there would be a response whose headline
    #: contradicted its own ``task_state``.
    outcome: str
    completed_at: Optional[str]
    provider: Optional[str] = None
    provider_session_id: Optional[str] = None
    turn_number: Optional[int] = None
    provider_turn_sequence: int = 0
    turn_count: int = 0
    result: Optional[str] = None
    failure_code: Optional[str] = None
    failure_summary: Optional[str] = None
    #: Whether the task can take another message **right now**. False for every
    #: terminal task, and false for a live one whose adapter cannot continue —
    #: see ``TaskService.get_result``. A client renders its follow-up box from
    #: this rather than from the state name, so that "the session is gone" and
    #: "the task is finished" cannot be confused for one another.
    follow_up_available: bool = False
    #: Which class of claim this result is. Always ``adapter_reported`` today:
    #: a provider saying it produced an answer is a claim, and Cofferdam did not
    #: watch it be true. The field exists so that the day something is observed
    #: it does not have to arrive as a new key.
    evidence_source: str = EVIDENCE_ADAPTER_REPORTED

    @property
    def succeeded(self) -> bool:
        return self.outcome == STATE_COMPLETED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": TASK_RESULT_VERSION,
            "task_id": self.task_id,
            "task_state": self.task_state,
            "task_terminal": self.task_terminal,
            "outcome": self.outcome,
            "succeeded": self.succeeded,
            "completed_at": self.completed_at,
            "provider": self.provider,
            "provider_session_id": self.provider_session_id,
            "turn_number": self.turn_number,
            "provider_turn_sequence": self.provider_turn_sequence,
            "turn_count": self.turn_count,
            "result": self.result,
            "failure_code": self.failure_code,
            "failure_summary": self.failure_summary,
            "follow_up_available": self.follow_up_available,
            "evidence_source": self.evidence_source,
            "result_meaning": RESULT_MEANING,
        }


#: Said in the payload rather than only in the docs, exactly as
#: ``TaskSnapshot`` states its limitations. A bridge author reading one response
#: should not have to find this file to learn what they are holding.
RESULT_MEANING = (
    "The latest completed turn's result. When task_terminal is true this is "
    "also the task's final result; when it is false the task may produce more."
)


def result_from_turn(
    turn: TaskTurn,
    *,
    task_state: str,
    turn_count: int,
    follow_up_available: bool,
) -> TaskResult:
    """The published result implied by one completed turn.

    Refuses an unfinished turn rather than publishing an empty one: "still
    working" has no result, and a function that returned a blank one for it
    would be the first step towards a task that reports finished because
    somebody asked whether it had.
    """
    if not turn.completed or turn.outcome is None:
        raise TurnInvalid("that turn has not finished")
    terminal = task_state in TERMINAL_STATES
    return TaskResult(
        task_id=turn.task_id,
        task_state=task_state,
        task_terminal=terminal,
        # The task's word when the task has one. See the field's own comment for
        # the case this exists to get right.
        outcome=task_state if terminal else turn.outcome,
        completed_at=turn.completed_at,
        provider=turn.provider,
        provider_session_id=turn.provider_session_id,
        turn_number=turn.turn_number,
        provider_turn_sequence=turn.provider_turn_sequence,
        turn_count=turn_count,
        result=turn.result,
        failure_code=turn.failure_code,
        failure_summary=turn.failure_summary,
        follow_up_available=follow_up_available,
    )


def result_from_task(
    *,
    task_id: str,
    task_state: str,
    completed_at: Optional[str],
    turn_count: int,
    failure_code: Optional[str] = None,
    failure_summary: Optional[str] = None,
) -> TaskResult:
    """The published result for a terminal task with no completed turn of its own.

    The honest shape for the three endings that are not an answer: a task
    cancelled before it produced one, a task that failed on its way to one, and
    a task the daemon restarted underneath. Each of them has a real terminal
    outcome and a real timestamp, and none of them has text — so none is
    invented.
    """
    if task_state not in TURN_OUTCOMES:
        raise TurnInvalid("that task has not finished")
    return TaskResult(
        task_id=task_id,
        task_state=task_state,
        task_terminal=True,
        outcome=task_state,
        completed_at=completed_at,
        turn_count=turn_count,
        failure_code=safe_line(failure_code, MAX_FAILURE_CODE_CHARS),
        failure_summary=safe_text(failure_summary, MAX_FAILURE_SUMMARY_CHARS),
        follow_up_available=False,
    )


#: The one non-terminal state in which a task may take another message. Named
#: here as well as in the service because it is half of the follow-up contract
#: and the other half — a live session — is the adapter's to answer.
FOLLOWUP_READY_STATE = STATE_READY_FOR_FOLLOWUP


__all__ = [
    "ACCEPTED_FOLLOWUP_SOURCES",
    "FOLLOWUP_READY_STATE",
    "FOLLOWUP_SOURCES",
    "MAX_TURNS_PER_TASK",
    "MAX_TURN_RESULT_CHARS",
    "OUTCOME_FOR_KIND",
    "RESULT_MEANING",
    "SOURCE_FOR_ORIGIN",
    "TASK_RESULT_VERSION",
    "TURN_OUTCOMES",
    "TaskResult",
    "TaskTurn",
    "TurnInvalid",
    "close_turn",
    "open_turn",
    "result_from_task",
    "result_from_turn",
    "source_for_origin",
]
