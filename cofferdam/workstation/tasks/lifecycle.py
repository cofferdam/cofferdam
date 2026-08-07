"""The transition graph, and the refusal that makes it real.

A state machine written as a table is a suggestion. This one is enforced in
exactly one place — :func:`check_transition` — which the store calls inside the
same transaction that writes the row and appends the event. There is no second
path that sets a state, so "can a task go from completed back to running" is
answerable by reading :data:`ALLOWED_TRANSITIONS` rather than by auditing every
caller.

What the graph refuses is more interesting than what it permits:

* **Nothing leaves a terminal state.** Not to another terminal state, and not
  back to itself. A second ``completed`` is not harmless bookkeeping — it would
  mean a task's completion time and result could change after the fact, which is
  precisely the kind of quiet rewrite an append-only history exists to prevent.
* **No transition skips the adapter.** ``created → completed`` is absent, so a
  request cannot finish a task; only an adapter report can. This is the same
  rule as "a tab appearing is not proof a video played", one milestone later.
* **A follow-up does not finish anything.** ``waiting_for_user → completed`` is
  absent on purpose: receiving an answer returns the task to ``running``, and
  what happens after that is the adapter's to report.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Optional

from .models import (
    STATE_CANCELLED,
    STATE_CANCELLING,
    STATE_COMPLETED,
    STATE_CREATED,
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_QUEUED,
    STATE_READY_FOR_FOLLOWUP,
    STATE_RECOVERY_REQUIRED,
    STATE_RUNNING,
    STATE_STARTING,
    STATE_WAITING_FOR_USER,
    STATES,
    TERMINAL_STATES,
)

#: The complete graph. Every arrow a task may take, and no others.
#:
#: Read the failure edges first, because they are the ones that matter under
#: load: every non-terminal state can reach ``failed`` and ``interrupted``.
#: A task can break at any point, and the daemon can be restarted at any point,
#: so a graph that could not express either would have to lie about one of them.
ALLOWED_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    STATE_CREATED: frozenset(
        {STATE_QUEUED, STATE_FAILED, STATE_CANCELLING, STATE_CANCELLED, STATE_INTERRUPTED}
    ),
    STATE_QUEUED: frozenset(
        {STATE_STARTING, STATE_FAILED, STATE_CANCELLING, STATE_CANCELLED, STATE_INTERRUPTED}
    ),
    STATE_STARTING: frozenset(
        {
            STATE_RUNNING,
            STATE_FAILED,
            STATE_CANCELLING,
            STATE_INTERRUPTED,
            STATE_RECOVERY_REQUIRED,
        }
    ),
    STATE_RUNNING: frozenset(
        {
            STATE_WAITING_FOR_USER,
            STATE_READY_FOR_FOLLOWUP,
            STATE_COMPLETED,
            STATE_FAILED,
            STATE_CANCELLING,
            STATE_INTERRUPTED,
            STATE_RECOVERY_REQUIRED,
        }
    ),
    # A finished turn with a live session. It can take another message, be
    # finished on purpose, or end the way anything else ends.
    #
    # `ready_for_followup → completed` is present and `waiting_for_user →
    # completed` is still absent, and the difference is the point: an *answer*
    # to a question must not finish a task, because what happens after the
    # answer is the adapter's to report. Choosing "I am done with this" is not
    # an answer to anything — it is a person closing a session they own.
    STATE_READY_FOR_FOLLOWUP: frozenset(
        {
            STATE_RUNNING,
            STATE_WAITING_FOR_USER,
            STATE_COMPLETED,
            STATE_FAILED,
            STATE_CANCELLING,
            STATE_INTERRUPTED,
            STATE_RECOVERY_REQUIRED,
        }
    ),
    STATE_WAITING_FOR_USER: frozenset(
        {
            STATE_RUNNING,
            STATE_FAILED,
            STATE_CANCELLING,
            STATE_INTERRUPTED,
            STATE_RECOVERY_REQUIRED,
        }
    ),
    # Cancelling may still end in completed or failed, and that is deliberate
    # honesty rather than a loose edge: a cancel that arrives while an adapter
    # is finishing does not un-finish it. Reporting `cancelled` for a task that
    # actually completed would be a false claim in the direction that matters
    # least to a person and most to an audit.
    STATE_CANCELLING: frozenset(
        {
            STATE_CANCELLED,
            STATE_COMPLETED,
            STATE_FAILED,
            STATE_INTERRUPTED,
            STATE_RECOVERY_REQUIRED,
        }
    ),
    # `recovery_required` is the only non-terminal state with no path back into
    # running, because this milestone implements no recovery. Its exits are the
    # honest ones: a person cancels it, or a restart ends it.
    STATE_RECOVERY_REQUIRED: frozenset(
        {STATE_CANCELLING, STATE_CANCELLED, STATE_FAILED, STATE_INTERRUPTED}
    ),
    # Terminal. Empty, and empty is the assertion.
    STATE_COMPLETED: frozenset(),
    STATE_FAILED: frozenset(),
    STATE_CANCELLED: frozenset(),
    STATE_INTERRUPTED: frozenset(),
}


class IllegalTransition(Exception):
    """A transition the graph does not contain.

    Carries both ends so the rejection can be recorded as an event and shown to
    a person without the caller having to reconstruct what it tried to do.
    """

    def __init__(self, current: str, requested: str, reason: Optional[str] = None) -> None:
        self.current = current
        self.requested = requested
        self.reason = reason or _explain(current, requested)
        super().__init__(self.reason)


def _explain(current: str, requested: str) -> str:
    """A sentence about *why*, not just a restatement of the arrow.

    Worth the branches: "completed → running is not allowed" tells a reader
    nothing they did not already see, while "a completed task is finished and
    its history is not rewritten" tells them the rule.
    """
    if requested not in STATES:
        return "unknown task state: " + str(requested)
    if current in TERMINAL_STATES:
        return (
            "this task is already " + current + ", and a finished task's history "
            "is not rewritten"
        )
    if current == requested:
        return "the task is already " + current
    return "a task cannot go from " + current + " to " + requested


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def check_transition(current: str, requested: str) -> None:
    """Raise :class:`IllegalTransition` unless the graph permits this move.

    Called with the lock held and inside the transaction, so the state it checks
    is the state that is about to be written rather than one read a moment ago.
    """
    if requested not in STATES:
        raise IllegalTransition(current, requested)
    if current not in ALLOWED_TRANSITIONS:  # pragma: no cover - storage invariant
        raise IllegalTransition(current, requested, "unknown current state")
    if requested not in ALLOWED_TRANSITIONS[current]:
        raise IllegalTransition(current, requested)


def can_transition(current: str, requested: str) -> bool:
    return requested in ALLOWED_TRANSITIONS.get(current, frozenset())


def transition_graph() -> Dict[str, list]:
    """The graph as plain data, for documentation and for the tests that assert
    it has not drifted from what the guide says."""
    return {state: sorted(targets) for state, targets in ALLOWED_TRANSITIONS.items()}


__all__ = [
    "ALLOWED_TRANSITIONS",
    "IllegalTransition",
    "can_transition",
    "check_transition",
    "is_terminal",
    "transition_graph",
]
