"""The one sentence a person actually wants, derived from facts nobody invented.

A projection, not a lifecycle
------------------------------

This module defines no state machine. There are no transitions here, nothing
persists a phase, and no code anywhere may set one — a phase is **computed** from
whatever the planner, the authority gate, Task Core, worker recovery and the
publisher currently say, every time it is asked.

That distinction is the whole safety property. A stored operational status is a
second opinion about facts that already have owners, and the moment it exists it
can be stale, can be written by the wrong thing, and can disagree with the
systems it claims to summarise. A derived phase cannot: if Task Core says
``running``, this says the worker is running, because that is the only place the
answer comes from.

So when the vocabularies below look like they duplicate something — they do not.
:data:`PHASE_WORKER_RUNNING` is not a copy of Task Core's ``running``; it is the
*rendering* of it, in a vocabulary that also has room for "waiting for you to
approve a prompt", which Task Core has no concept of because no task exists yet.

Ordering is by attention, not by time
--------------------------------------

:func:`rank` orders phases by *how much a person is needed*, not by how far along
the work is. A dispatch waiting for an answer outranks one that is running, and a
failure outranks both. That is the order a status screen should list projects in,
and putting it here rather than in a future UI keeps it one decision instead of
one per client.

Machine facts and worker claims stay apart
--------------------------------------------

Nothing in this module reads a worker's prose. Every input is either a durable
row written by Cofferdam or a state owned by one of the five components. What a
model *said* about its own work travels separately, clearly labelled, and never
decides a phase — see :mod:`.view`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

#: Nothing is happening for this project. Not an error, and the most common
#: honest answer.
PHASE_IDLE = "idle"

#: The planner is deciding what to do. Owned by the planner request's status.
PHASE_PLANNER_PREPARING = "planner_preparing"

#: The model asked a question and nobody has answered it.
PHASE_AWAITING_USER_ANSWER = "awaiting_user_answer"

#: A worker prompt is prepared and waiting for a person to approve or reject it.
PHASE_AWAITING_APPROVAL = "awaiting_approval"

#: A person rejected the prepared prompt. Terminal, and their decision.
PHASE_REJECTED = "rejected"

#: The planner deliberately declined to plan. A successful invocation whose
#: result was "do not do this", which is not a failure.
PHASE_STOPPED = "stopped"

#: Approved and dispatched; the worker has not started yet.
PHASE_WORKER_QUEUED = "worker_queued"

#: A contained worker is editing.
PHASE_WORKER_RUNNING = "worker_running"

#: The daemon stopped underneath a worker and nothing has reconciled it yet.
PHASE_RECOVERY_REQUIRED = "recovery_required"

#: A restart reconciled an interrupted worker and preserved what it found.
PHASE_RECOVERY_RECONCILED = "recovery_reconciled"

#: The worker stopped without finishing, and its partial work is preserved.
PHASE_WORKER_INTERRUPTED = "worker_interrupted"

#: A host-owned commit exists on the worker branch and has not been published.
PHASE_COMMIT_READY = "commit_ready"

#: The publisher is pushing, or has pushed and not yet opened a pull request.
PHASE_PUBLISHING = "publishing"

#: A pull request is open and waiting for review.
PHASE_PR_READY = "pr_ready"

#: Something went wrong: the planner, the worker, or the publisher.
PHASE_FAILED = "failed"

#: A person cancelled the work.
PHASE_CANCELLED = "cancelled"

#: Cofferdam holds a credential that will not authenticate.
PHASE_AUTH_REQUIRED = "auth_required"

#: The records disagree with the machine and a person is needed.
PHASE_NEEDS_ATTENTION = "needs_attention"

PHASES: Tuple[str, ...] = (
    PHASE_IDLE,
    PHASE_PLANNER_PREPARING,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_AWAITING_APPROVAL,
    PHASE_REJECTED,
    PHASE_STOPPED,
    PHASE_WORKER_QUEUED,
    PHASE_WORKER_RUNNING,
    PHASE_RECOVERY_REQUIRED,
    PHASE_RECOVERY_RECONCILED,
    PHASE_WORKER_INTERRUPTED,
    PHASE_COMMIT_READY,
    PHASE_PUBLISHING,
    PHASE_PR_READY,
    PHASE_FAILED,
    PHASE_CANCELLED,
    PHASE_AUTH_REQUIRED,
    PHASE_NEEDS_ATTENTION,
)

#: Phases where Cofferdam is doing something by itself right now.
BUSY: frozenset = frozenset(
    {PHASE_PLANNER_PREPARING, PHASE_WORKER_QUEUED, PHASE_WORKER_RUNNING, PHASE_PUBLISHING}
)

#: Phases that cannot advance without a person. What a notification is for.
NEEDS_PERSON: frozenset = frozenset(
    {
        PHASE_AWAITING_USER_ANSWER,
        PHASE_AWAITING_APPROVAL,
        PHASE_RECOVERY_REQUIRED,
        PHASE_WORKER_INTERRUPTED,
        PHASE_COMMIT_READY,
        PHASE_PR_READY,
        PHASE_FAILED,
        PHASE_AUTH_REQUIRED,
        PHASE_NEEDS_ATTENTION,
    }
)

#: Nothing further will happen without new instruction.
SETTLED: frozenset = frozenset(
    {PHASE_IDLE, PHASE_REJECTED, PHASE_STOPPED, PHASE_CANCELLED, PHASE_PR_READY}
)

#: How urgently a person is needed. Lower sorts first.
#:
#: Deliberately *not* chronological. A screen listing five projects should lead
#: with the one that cannot move without you, not the one that started most
#: recently — and that judgement belongs here, once, rather than in every client
#: that ever renders this.
_RANK: Dict[str, int] = {
    PHASE_AUTH_REQUIRED: 0,
    PHASE_NEEDS_ATTENTION: 1,
    PHASE_FAILED: 2,
    PHASE_AWAITING_USER_ANSWER: 3,
    PHASE_AWAITING_APPROVAL: 4,
    PHASE_RECOVERY_REQUIRED: 5,
    PHASE_WORKER_INTERRUPTED: 6,
    PHASE_COMMIT_READY: 7,
    PHASE_PR_READY: 8,
    PHASE_WORKER_RUNNING: 9,
    PHASE_PUBLISHING: 10,
    PHASE_WORKER_QUEUED: 11,
    PHASE_PLANNER_PREPARING: 12,
    PHASE_RECOVERY_RECONCILED: 13,
    PHASE_REJECTED: 14,
    PHASE_STOPPED: 15,
    PHASE_CANCELLED: 16,
    PHASE_IDLE: 17,
}


def rank(phase: str) -> int:
    return _RANK.get(phase, len(_RANK))


#: One plain sentence per phase, written for a person on a phone.
#:
#: Kept as data beside the phases so a new phase cannot be added without one, and
#: so the wording is reviewable in one place rather than scattered across a
#: template. These are the sentences a cockpit shows instead of "failed".
SENTENCES: Dict[str, str] = {
    PHASE_IDLE: "Nothing in progress.",
    PHASE_PLANNER_PREPARING: "Working out what to do.",
    PHASE_AWAITING_USER_ANSWER: "Waiting for your answer to a question.",
    PHASE_AWAITING_APPROVAL: "A development step is prepared and waiting for your approval.",
    PHASE_REJECTED: "You rejected the prepared step. Nothing ran.",
    PHASE_STOPPED: "The planner decided not to proceed, and explained why.",
    PHASE_WORKER_QUEUED: "The approved step is dispatched and about to start.",
    PHASE_WORKER_RUNNING: "A development worker is editing in an isolated worktree.",
    PHASE_RECOVERY_REQUIRED: (
        "Cofferdam restarted while a worker was running. It has not been "
        "reconciled yet."
    ),
    PHASE_RECOVERY_RECONCILED: (
        "Cofferdam restarted during this step and reconciled what it found. "
        "Nothing was rerun."
    ),
    PHASE_WORKER_INTERRUPTED: (
        "The worker was interrupted. Its unfinished work is preserved and "
        "nothing was discarded."
    ),
    PHASE_COMMIT_READY: (
        "The work is committed locally and has not been published yet."
    ),
    PHASE_PUBLISHING: "Publishing the branch.",
    PHASE_PR_READY: "A pull request is open and ready for your review.",
    PHASE_FAILED: "This step did not succeed.",
    PHASE_CANCELLED: "You stopped this step.",
    PHASE_AUTH_REQUIRED: "Cofferdam needs you to sign something in before it can continue.",
    PHASE_NEEDS_ATTENTION: "Something does not add up and needs a look.",
}


@dataclass(frozen=True)
class Phase:
    """One projected phase, with everything a caller needs to render it."""

    phase: str
    sentence: str
    needs_person: bool
    busy: bool
    settled: bool
    rank: int
    #: Which component's fact decided this, so a reader can go and check.
    because: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "sentence": self.sentence,
            "needs_person": self.needs_person,
            "busy": self.busy,
            "settled": self.settled,
            "rank": self.rank,
            "because": self.because,
        }


def build(phase: str, because: str) -> Phase:
    return Phase(
        phase=phase,
        sentence=SENTENCES.get(phase, "Cofferdam's state could not be summarised."),
        needs_person=phase in NEEDS_PERSON,
        busy=phase in BUSY,
        settled=phase in SETTLED,
        rank=rank(phase),
        because=because,
    )


def derive(
    *,
    planner_status: Optional[str],
    planner_action: Optional[str],
    authority_action: Optional[str],
    task_state: Optional[str],
    reconciliation_outcome: Optional[str],
    reconciliation_needs_attention: bool,
    publication_state: Optional[str],
    publication_failure: Optional[str],
    has_commit: bool,
) -> Phase:
    """The projection. Reads facts in the order the work actually flows.

    **Latest-first.** A dispatch that has been published is not also "awaiting
    approval" merely because an approval row still exists — the approval is
    history. So this checks the furthest-along fact first and walks backwards,
    and the first one that speaks wins.

    Every branch names its source in ``because``, so any phase this returns can
    be traced to the row that produced it without re-deriving anything.
    """
    # -- publication, the furthest along ---------------------------------
    if publication_state == "published":
        return build(PHASE_PR_READY, "publication.state=published")
    if publication_failure == "publisher_auth_required":
        return build(PHASE_AUTH_REQUIRED, "publication.failure=publisher_auth_required")
    if publication_state in ("pending", "branch_published"):
        return build(PHASE_PUBLISHING, f"publication.state={publication_state}")
    if publication_state == "interrupted":
        return build(PHASE_NEEDS_ATTENTION, "publication.state=interrupted")
    if publication_state == "refused":
        return build(PHASE_FAILED, "publication.state=refused")

    # -- recovery, which reinterprets a task's terminal state -------------
    if task_state == "recovery_required":
        return build(PHASE_RECOVERY_REQUIRED, "task.state=recovery_required")
    if reconciliation_outcome is not None:
        if reconciliation_needs_attention:
            if reconciliation_outcome == "partial_work_preserved":
                return build(
                    PHASE_WORKER_INTERRUPTED,
                    f"reconciliation.outcome={reconciliation_outcome}",
                )
            return build(
                PHASE_NEEDS_ATTENTION,
                f"reconciliation.outcome={reconciliation_outcome}",
            )
        if reconciliation_outcome == "commit_recovered":
            # A commit exists and was recovered. That is publishable work, and
            # saying "interrupted" would bury the thing a person can act on.
            return build(PHASE_COMMIT_READY, "reconciliation.outcome=commit_recovered")
        return build(
            PHASE_RECOVERY_RECONCILED, f"reconciliation.outcome={reconciliation_outcome}"
        )

    # -- the worker ------------------------------------------------------
    if task_state == "cancelled":
        return build(PHASE_CANCELLED, "task.state=cancelled")
    if task_state == "failed":
        return build(PHASE_FAILED, "task.state=failed")
    if task_state == "interrupted":
        return build(PHASE_WORKER_INTERRUPTED, "task.state=interrupted")
    if task_state == "completed":
        if has_commit:
            return build(PHASE_COMMIT_READY, "task.state=completed with a commit")
        # Finished and produced nothing to publish. Truthful, and not a failure:
        # a step can legitimately conclude that no change was needed.
        return build(PHASE_RECOVERY_RECONCILED, "task.state=completed with no commit")
    if task_state in ("running", "starting", "ready_for_followup", "cancelling"):
        return build(PHASE_WORKER_RUNNING, f"task.state={task_state}")
    if task_state in ("created", "queued"):
        return build(PHASE_WORKER_QUEUED, f"task.state={task_state}")
    if task_state == "waiting_for_user":
        return build(PHASE_AWAITING_USER_ANSWER, "task.state=waiting_for_user")

    # -- the human gate --------------------------------------------------
    if authority_action == "reject":
        return build(PHASE_REJECTED, "authority.action=reject")
    if authority_action == "approve":
        # Approved, and no task exists yet. The dispatch has not happened.
        return build(PHASE_WORKER_QUEUED, "authority.action=approve, no task yet")

    # -- the planner -----------------------------------------------------
    if planner_status in ("pending", "running"):
        return build(PHASE_PLANNER_PREPARING, f"planner.status={planner_status}")
    if planner_status == "failed":
        return build(PHASE_FAILED, "planner.status=failed")
    if planner_status == "interrupted":
        return build(PHASE_NEEDS_ATTENTION, "planner.status=interrupted")
    if planner_status == "succeeded":
        if planner_action == "ASK_USER":
            return build(PHASE_AWAITING_USER_ANSWER, "planner.action=ASK_USER")
        if planner_action == "PREPARE_WORKER_PROMPT":
            return build(PHASE_AWAITING_APPROVAL, "planner.action=PREPARE_WORKER_PROMPT")
        if planner_action == "STOP":
            return build(PHASE_STOPPED, "planner.action=STOP")

    return build(PHASE_IDLE, "no planner request")


__all__ = [
    "BUSY",
    "NEEDS_PERSON",
    "PHASES",
    "PHASE_AUTH_REQUIRED",
    "PHASE_AWAITING_APPROVAL",
    "PHASE_AWAITING_USER_ANSWER",
    "PHASE_CANCELLED",
    "PHASE_COMMIT_READY",
    "PHASE_FAILED",
    "PHASE_IDLE",
    "PHASE_NEEDS_ATTENTION",
    "PHASE_PLANNER_PREPARING",
    "PHASE_PR_READY",
    "PHASE_PUBLISHING",
    "PHASE_RECOVERY_RECONCILED",
    "PHASE_RECOVERY_REQUIRED",
    "PHASE_REJECTED",
    "PHASE_STOPPED",
    "PHASE_WORKER_INTERRUPTED",
    "PHASE_WORKER_QUEUED",
    "PHASE_WORKER_RUNNING",
    "SENTENCES",
    "SETTLED",
    "Phase",
    "build",
    "derive",
    "rank",
]
