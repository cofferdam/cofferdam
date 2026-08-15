"""Deterministic per-criterion evaluation. A pure function over frozen facts.

What this answers, and what it refuses to answer
------------------------------------------------

M2K PR6 froze **what was required** before the worker was dispatched. PR2 to PR5
froze **what machine evidence exists** for the turn. This module answers, for
each supported criterion, one question:

    does the stored machine evidence for this exact turn satisfy this exact
    criterion?

Three answers, and there is no fourth: ``met``, ``not_met``, ``unverified``.

It is emphatically **not** a task verdict, and the following equivalences are
forbidden everywhere in this build:

===========================  ===========================
this                         is **not** this
===========================  ===========================
criterion ``not_met``        the task failed
criterion ``met``            the task passed
``claim_conflict``           criterion ``not_met``
``claim_conflict``           task failure
no criteria                  success
incomplete evidence          ``not_met``
===========================  ===========================

There is no aggregate in this module. No pass, no fail, no score, no confidence,
no risk, and no code that could produce one — a task-level judgement needs an
independently reviewed doctrine about what a mixture of results *means*, and
inventing one as a side effect of writing the per-criterion evaluator is exactly
how a system starts asserting more than it knows.

Purity
------

:func:`evaluate` takes a :class:`~.criteria.CriteriaSnapshot` and an
:class:`~.evidence.EvidenceBundle` and returns results. It reads no database,
runs no process, opens no file, touches no socket, consults no provider or model,
and reads no clock. Delete the repository after the evidence was stored and this
returns exactly what it returned before, because the repository was never an
input — the stored observation was. ``tests/test_evaluation_purity.py`` asserts
that from the syntax tree and again at runtime, the same two layers
``tests/test_evidence_purity.py`` uses for assembly.

Persistence and orchestration are deliberately elsewhere (``store.py`` and
``service.py``). A pure function that could write would be a pure function only
by convention.

Machine evidence is the only authority
--------------------------------------

**A worker's claim never satisfies a criterion, and a worker's silence never
fails one.** This module does not read ``bundle.claims``, ``bundle.ingestion`` or
``bundle.relationships`` at all — not to confirm, not to deny, not as a
tie-break. That is stronger than a rule about how they are weighed: there is no
code path in which an ``adapter_reported`` statement can influence a result.

A consequence worth stating, because the PR6 readiness audit floated the
opposite: **incomplete claim ingestion does not downgrade anything here.** Claims
are not the truth source for these predicates, so their completeness is not a
gate on them. Only the evidence dimensions a predicate actually needs may gate
it — see :func:`_closure`.

``claim_conflict`` likewise drives nothing. It means an adapter's record and the
machine's record disagree, which is a fact about the *records* and not a proof
that the requirement went unmet.

What ``path_changed`` actually means
------------------------------------

This wording is load-bearing and is repeated in the docs and pinned by tests:

    the turn produced a machine-observed **resulting repository change** for
    this semantic path, as seen at the post-worker observation boundary.

It is **not** "the file was touched at some instant during execution". Cofferdam
observes a boundary, not a process: a worker that edits a file and then reverts
it leaves no resulting change, and this build has no way to see that it ever
happened. Nothing here may be read as complete process tracing, and no result
should be described in words that claim more temporal knowledge than a boundary
observation carries.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from .criteria import (
    CRITERIA_LEGACY_UNKNOWN,
    CRITERIA_NOT_PROVIDED,
    CRITERIA_PRESENT,
    KIND_EVIDENCE,
    KIND_MANUAL,
    PREDICATE_PATH_CHANGED,
    PREDICATE_PATH_OPERATION,
    PREDICATE_RENAME,
    AcceptanceCriterion,
    CriteriaSnapshot,
)
from .evidence import (
    ATTRIBUTION_EXACT,
    LIMIT_UNSUPPORTED_OBSERVATION,
    OBSERVATION_DOMAIN_COMMITTED_RANGE,
    OBSERVATION_DOMAIN_WORKTREE,
    RANGE_ANCESTRY_DIVERGED,
    RANGE_BOUNDARY_CLEAN,
    RANGE_COVERAGE_COMPLETE,
    EvidenceBundle,
)
from .models import CHANGE_RENAMED, CHANGE_UNKNOWN

#: Bumped when the **semantics** of evaluation change: a predicate decided
#: differently, a closure rule relaxed or tightened, a reason given a new
#: meaning. Deliberately separate from :data:`~.store.SCHEMA_VERSION` (the shape
#: of the tables), :data:`~.evidence.ASSEMBLER_VERSION` (how a bundle is built)
#: and :data:`~.criteria.CRITERIA_MODEL_VERSION` (what a criterion may say),
#: because those four things move for four different reasons.
#:
#: It is part of the evaluation's stored identity and of its fingerprint, so a
#: future version-2 evaluator produces a **distinguishable** record rather than
#: silently disagreeing with a stored version-1 one. The uniqueness constraint on
#: ``task_turn_evaluations`` includes it, so version 2 can record its own answer
#: for a turn without rewriting version 1's.
EVALUATOR_VERSION = 1


# -- the result vocabulary ----------------------------------------------------
#
# Three values, closed, and the third is the point of the exercise. A two-valued
# evaluator has to answer *something* when the evidence cannot decide, and every
# such system eventually answers "failed" — which converts a limitation of the
# observer into an accusation about the worker.

#: The stored machine evidence satisfies the criterion.
RESULT_MET = "met"

#: The stored machine evidence is complete enough to rule the criterion out, and
#: it rules it out. **Not** "the task failed" — see this module's docstring.
RESULT_NOT_MET = "not_met"

#: The stored machine evidence cannot decide. This is the honest answer for a
#: manual criterion, for a capability this build cannot evaluate, and for every
#: case where absence might be a gap in observation rather than a gap in the
#: work. Never a synonym for failure.
RESULT_UNVERIFIED = "unverified"

RESULTS: Tuple[str, ...] = (RESULT_MET, RESULT_NOT_MET, RESULT_UNVERIFIED)

#: There is deliberately no fourth value, and in particular no ``failed``. A
#: name is a thing people reach for; the way to keep "not_met" from becoming a
#: verdict is to give a verdict nowhere to live.
EXCLUDED_RESULTS: Tuple[str, ...] = ("failed", "passed", "error", "skipped")


# -- the reason vocabulary ----------------------------------------------------
#
# Closed, code-owned, and one per outcome. There is **no free-form explanation
# column** anywhere in this PR: prose that a reader might treat as authority is
# how a deterministic record starts being argued with, and a sentence generated
# per evaluation would be the obvious place for a model to be added later.

# ...why a criterion is met
REASON_MACHINE_CHANGE_OBSERVED = "machine_change_observed"
REASON_MACHINE_OPERATION_OBSERVED = "machine_operation_observed"
REASON_MACHINE_RENAME_OBSERVED = "machine_rename_observed"

# ...why a criterion is not met. Every one of these says *complete*, because
# that is the only condition under which absence is a finding rather than a gap.
REASON_COMPLETE_CHANGE_ABSENT = "complete_resulting_change_absent"
REASON_COMPLETE_OPERATION_INCOMPATIBLE = "complete_incompatible_operation"
REASON_COMPLETE_RENAME_ABSENT = "complete_rename_not_observed"

# ...why a criterion could not be decided
REASON_MANUAL = "manual_criterion"
REASON_UNSUPPORTED_CAPABILITY = "unsupported_capability"
REASON_ATTRIBUTION_UNKNOWN = "evidence_not_attributable"
REASON_OBSERVATIONS_INCOMPLETE = "machine_observations_incomplete"
REASON_UNSUPPORTED_OBSERVATION = "unsupported_observation_shape"
REASON_RANGE_NOT_RECORDED = "committed_range_not_recorded"
REASON_RANGE_INCOMPLETE = "committed_range_incomplete"
REASON_HISTORY_DIVERGED = "committed_range_history_diverged"
REASON_BOUNDARY_NOT_CLEAN = "pre_work_boundary_not_clean"
REASON_OPERATION_NOT_OBSERVED = "resulting_operation_not_observed"
REASON_WORKTREE_NOT_OBSERVED = "worktree_not_observed"

REASONS: Tuple[str, ...] = (
    REASON_MACHINE_CHANGE_OBSERVED,
    REASON_MACHINE_OPERATION_OBSERVED,
    REASON_MACHINE_RENAME_OBSERVED,
    REASON_COMPLETE_CHANGE_ABSENT,
    REASON_COMPLETE_OPERATION_INCOMPATIBLE,
    REASON_COMPLETE_RENAME_ABSENT,
    REASON_MANUAL,
    REASON_UNSUPPORTED_CAPABILITY,
    REASON_ATTRIBUTION_UNKNOWN,
    REASON_OBSERVATIONS_INCOMPLETE,
    REASON_UNSUPPORTED_OBSERVATION,
    REASON_RANGE_NOT_RECORDED,
    REASON_RANGE_INCOMPLETE,
    REASON_HISTORY_DIVERGED,
    REASON_BOUNDARY_NOT_CLEAN,
    REASON_OPERATION_NOT_OBSERVED,
    REASON_WORKTREE_NOT_OBSERVED,
)

#: Which reasons may accompany which result. Enforced by :func:`evaluate` and
#: asserted by the tests, so a future edit cannot quietly produce a ``met`` whose
#: reason says the evidence was incomplete.
REASONS_FOR_RESULT: Dict[str, Tuple[str, ...]] = {
    RESULT_MET: (
        REASON_MACHINE_CHANGE_OBSERVED,
        REASON_MACHINE_OPERATION_OBSERVED,
        REASON_MACHINE_RENAME_OBSERVED,
    ),
    RESULT_NOT_MET: (
        REASON_COMPLETE_CHANGE_ABSENT,
        REASON_COMPLETE_OPERATION_INCOMPATIBLE,
        REASON_COMPLETE_RENAME_ABSENT,
    ),
    RESULT_UNVERIFIED: (
        REASON_MANUAL,
        REASON_UNSUPPORTED_CAPABILITY,
        REASON_ATTRIBUTION_UNKNOWN,
        REASON_OBSERVATIONS_INCOMPLETE,
        REASON_UNSUPPORTED_OBSERVATION,
        REASON_RANGE_NOT_RECORDED,
        REASON_RANGE_INCOMPLETE,
        REASON_HISTORY_DIVERGED,
        REASON_BOUNDARY_NOT_CLEAN,
        REASON_OPERATION_NOT_OBSERVED,
        REASON_WORKTREE_NOT_OBSERVED,
    ),
}


#: Re-exported so callers reach one module for the evaluation vocabulary, while
#: the minting itself stays outside this module's purity boundary. See
#: :func:`~.identity.new_evaluation_id` for why it lives there.
from .identity import new_evaluation_id, valid_evaluation_id  # noqa: E402,F401


@dataclass(frozen=True)
class CriterionResult:
    """One criterion's deterministic answer.

    ``criterion_id`` and ``ordinal`` come from the frozen snapshot, so a result
    names the exact durable criterion row it answered rather than a position in
    a list somebody might reorder. There is no explanation field, deliberately:
    ``reason`` is a closed code and prose has no seat at this table.
    """

    criterion_id: str
    ordinal: int
    result: str
    reason: str


@dataclass(frozen=True)
class EvaluationRecord:
    """One stored evaluation, as persisted. Immutable once written.

    Carries **both** identities in full — the criteria snapshot id and its
    fingerprint, the assembler version and the evidence input fingerprint —
    because an audit needs to know which durable rows were read *and* what
    immutable content they represented.

    It deliberately does not carry the evidence itself. The bundle is derived
    from immutable rows and identified here by fingerprint; copying it in would
    make a second durable shape for the same facts.

    There is no aggregate field, no pass, no fail and no score. ``results`` is a
    list of per-criterion answers and nothing in this build sums them.
    """

    evaluation_id: str
    task_id: str
    turn_number: int
    evaluator_version: int
    criteria_state: str
    criteria_snapshot_id: str
    criteria_fingerprint: str
    assembler_version: int
    evidence_input_fingerprint: str
    result_count: int
    evaluation_fingerprint: str
    recorded_at: str
    results: Tuple[CriterionResult, ...] = ()

    @property
    def decided(self) -> bool:
        """Whether any criterion got a machine-decided answer at all.

        A convenience for a reader, and pointedly **not** a verdict: it says
        something about how far Cofferdam's evidence reached, never about whether
        the work was acceptable. A record whose results are all ``unverified`` is
        a record about Cofferdam.
        """
        return any(item.result != RESULT_UNVERIFIED for item in self.results)


# -- machine evidence closure -------------------------------------------------
#
# Positive and negative evidence are **asymmetric**, and this is the section
# where that asymmetry lives.
#
# One attributable observation can establish that a change happened. Establishing
# that a change did *not* happen requires that every place the change could have
# shown up was looked at completely — otherwise "absent" means "we did not see
# it", which is a statement about Cofferdam rather than about the work.
#
# There are two result domains and they hide different things from each other:
#
# * ``worktree`` is the index and working tree against the *current* HEAD. A
#   change the worker committed is invisible here, because after the commit the
#   tree is clean.
# * ``committed_range`` is the revision range from the pre-work baseline to the
#   post-work target. A change the worker left uncommitted is invisible here.
#
# So a negative conclusion needs **both** domains closed. Neither alone is a
# complete view of "what this turn resulted in", and a rule that accepted one
# would produce confident false negatives in exactly the ordinary cases — a
# worker that commits, or a worker that does not.


@dataclass(frozen=True)
class _Closure:
    """Whether each domain was observed completely enough to prove an absence.

    ``reason`` names the first thing that was missing, so an ``unverified``
    result can say which gap produced it rather than a generic shrug.
    """

    worktree: bool
    committed: bool
    reason: Optional[str]

    @property
    def complete(self) -> bool:
        return self.worktree and self.committed


def _closure(bundle: EvidenceBundle) -> _Closure:
    """How complete this turn's machine observation is, per domain.

    **Deliberately not "the bundle has a limitation, therefore unverified".**
    Limitations are a mixed set: a truncated *claim* list says nothing about
    machine observation, and gating every predicate on every limitation would
    make claim completeness govern results that claims are not allowed to
    influence at all. Only the dimensions a negative conclusion actually rests on
    are read here.

    **The pre-work boundary is one of those dimensions**, and it gates the
    negative exactly as hard as it gates the positive. That is not obvious and it
    is worth the paragraph, because the intuitive rule — "a dirty tree gives a
    path nowhere to hide" — is wrong, and wrong in the direction that
    manufactures false negatives.

    PR4 persists a **coarse** boundary: one word for the whole working tree
    (``clean_complete``, ``dirty``, ``incomplete``, ``unavailable``) and no record
    of *which paths* were dirty. So consider ``foo.py`` at HEAD revision ``A``,
    dirty at ``B`` when the turn began, and restored by the worker to ``A``:

    * the committed range contains no ``foo.py`` — nothing was committed;
    * the working tree contains no ``foo.py`` — it now matches HEAD.

    The worker plainly produced a resulting effect on ``foo.py`` relative to the
    tree it was handed, and Cofferdam's stored evidence cannot see it. Absence
    after a dirty boundary therefore cannot distinguish "the worker never touched
    this path" from "the path was dirty and the worker put it back", and a
    ``not_met`` in that state would be an accusation built on a gap in evidence
    resolution.

    So for the v1 path predicates, a boundary that is not ``clean_complete``
    blocks **both** conclusions. This is a limitation of what PR4 records, not a
    statement about the work, and closing it would need path-level pre-work state
    — new evidence architecture, deliberately not attempted here.
    """
    if bundle.turn_attribution != ATTRIBUTION_EXACT:
        # Without an exact event window there is no defensible set of
        # observations for this turn — a legacy turn's evidence may belong to a
        # neighbouring one. Nothing can be ruled out.
        return _Closure(False, False, REASON_ATTRIBUTION_UNKNOWN)

    if not _pre_work_state_known(bundle):
        # See the docstring above: without a clean, completely-read pre-work
        # tree, an absent path may have been reverted rather than untouched.
        return _Closure(False, False, _unattributable_reason(bundle))

    worktree_reason: Optional[str] = None
    worktree = True
    if LIMIT_UNSUPPORTED_OBSERVATION in bundle.limitations:
        # Git emitted a shape this build did not understand. Something was
        # observed and not recorded, so the recorded set is not the whole set.
        worktree, worktree_reason = False, REASON_UNSUPPORTED_OBSERVATION
    elif not bundle.machine_observations_complete:
        worktree, worktree_reason = False, REASON_OBSERVATIONS_INCOMPLETE
    elif not _worktree_was_observed(bundle):
        # The subtle one, and the reason it is spelled out rather than folded
        # into the flag above: ``machine_observations_complete`` is **True when
        # nobody looked**. It means "no emitter said its set was partial", not
        # "the working tree was read and found empty" — a turn whose adapter
        # reports no worktree evidence at all satisfies it vacuously.
        #
        # Reading that as closure would let a path absent from the committed
        # range be declared `not_met` while it sat modified and uncommitted in a
        # tree nobody examined. So absence in this domain counts only when there
        # is positive evidence the domain was examined: either an observation in
        # it, or an explicit clean-tree statement, which is `git status` saying
        # it ran and found nothing.
        worktree, worktree_reason = False, REASON_WORKTREE_NOT_OBSERVED

    committed_reason: Optional[str] = None
    committed = True
    span = bundle.committed_range
    if not span.recorded:
        committed, committed_reason = False, REASON_RANGE_NOT_RECORDED
    elif span.ancestry == RANGE_ANCESTRY_DIVERGED or not span.history_valid:
        # A tree comparison across a branch switch or reset reports the other
        # history's files as changed. PR5 records the divergence and runs no
        # diff, so there is no committed view of this turn at all.
        committed, committed_reason = False, REASON_HISTORY_DIVERGED
    elif span.coverage != RANGE_COVERAGE_COMPLETE:
        committed, committed_reason = False, REASON_RANGE_INCOMPLETE

    return _Closure(worktree, committed, worktree_reason or committed_reason)


def _pre_work_state_known(bundle: EvidenceBundle) -> bool:
    """Whether the repository state the worker *received* is known well enough.

    The single fact both a positive and a negative causal conclusion rest on, and
    it is coarse by construction: PR4 records one word for the whole tree, so the
    only value that supports attributing a transition to this turn — in either
    direction — is a boundary that was clean and completely read.

    A turn with no committed-range summary has no boundary statement at all and
    is not known; ``recorded=False`` means nobody looked, never "the tree was
    clean".
    """
    span = bundle.committed_range
    return span.recorded and span.boundary_quality == RANGE_BOUNDARY_CLEAN


def _worktree_was_observed(bundle: EvidenceBundle) -> bool:
    """Whether anything actually looked at the working tree for this turn.

    An explicit clean-tree statement counts — that is ``git status`` reporting
    that it ran and found nothing — and so does any worktree observation, which
    could only exist if the domain was read. Nothing else does.
    """
    if bundle.repository_reported_clean:
        return True
    return any(
        item.domain == OBSERVATION_DOMAIN_WORKTREE for item in bundle.observations
    )


def _worktree_attributable(bundle: EvidenceBundle) -> bool:
    """Whether a worktree observation can be attributed to *this turn*.

    The load-bearing half of PR4 and PR5's doctrine, applied to results rather
    than to comparisons. A change present in the working tree after the worker
    ran is only this turn's change if the tree was **clean and completely read
    before dispatch**. If it was already dirty, the same path may have been
    modified an hour earlier by a person, an editor autosave or another tool, and
    the post-worker reading cannot tell those apart.

    The pre-work boundary quality reaches a bundle through the committed-range
    summary — that is where PR5 publishes it — so a turn with no range recorded
    has no boundary statement at all and is not attributable here. That is
    correct rather than strict: for those turns Cofferdam genuinely does not know
    what the tree looked like before.
    """
    return _pre_work_state_known(bundle)


def _committed_attributable(bundle: EvidenceBundle) -> bool:
    """Whether a committed-range observation can be attributed to *this turn*.

    Everything :func:`_worktree_attributable` requires, plus a history in which
    "since the baseline" means anything — which is exactly
    :attr:`~.evidence.CommittedRangeSummary.comparison_grade`, reused rather than
    restated so the two cannot drift apart.
    """
    return bundle.committed_range.comparison_grade


def _attributable_observations(bundle: EvidenceBundle, path: str):
    """Observations naming ``path`` that this turn may be credited with.

    Each domain is filtered by its own attribution rule, and the domains are
    **not merged into one final operation**. A path can legitimately be
    ``created`` in the committed range and ``modified`` in the working tree —
    committed, then edited again — and both are true statements about two
    different moments. Collapsing them would destroy one of the two facts and
    make a perfectly satisfiable criterion look unsatisfied.
    """
    worktree_ok = _worktree_attributable(bundle)
    committed_ok = _committed_attributable(bundle)
    for observation in bundle.observations:
        if observation.path != path:
            continue
        if observation.domain == OBSERVATION_DOMAIN_COMMITTED_RANGE:
            if committed_ok:
                yield observation
        elif observation.domain == OBSERVATION_DOMAIN_WORKTREE:
            if worktree_ok:
                yield observation


def _observations_at(bundle: EvidenceBundle, path: str):
    """Every observation naming ``path``, attributable or not."""
    return [item for item in bundle.observations if item.path == path]


def _unattributable_reason(bundle: EvidenceBundle) -> str:
    """Why an observation exists but cannot be credited to this turn."""
    span = bundle.committed_range
    if span.recorded and not span.history_valid:
        return REASON_HISTORY_DIVERGED
    if not span.recorded:
        return REASON_RANGE_NOT_RECORDED
    return REASON_BOUNDARY_NOT_CLEAN


# -- the predicates -----------------------------------------------------------


def _evaluate_path_changed(
    criterion: AcceptanceCriterion, bundle: EvidenceBundle
) -> Tuple[str, str]:
    """``path_changed(P)`` — a resulting repository change for P.

    Read this module's docstring for what the words mean. This is a statement
    about the observed **result** at the post-worker boundary, never about
    whether the file was touched at some instant in between.
    """
    for _ in _attributable_observations(bundle, criterion.path):
        return RESULT_MET, REASON_MACHINE_CHANGE_OBSERVED

    if _observations_at(bundle, criterion.path):
        # Something was observed at this path and it cannot be credited to this
        # turn. Neither met nor not_met: the fact is real and the causation is
        # not established.
        return RESULT_UNVERIFIED, _unattributable_reason(bundle)

    closure = _closure(bundle)
    if closure.complete:
        return RESULT_NOT_MET, REASON_COMPLETE_CHANGE_ABSENT
    return RESULT_UNVERIFIED, closure.reason or REASON_OBSERVATIONS_INCOMPLETE


def _evaluate_path_operation(
    criterion: AcceptanceCriterion, bundle: EvidenceBundle
) -> Tuple[str, str]:
    """``path_operation(P, OP)`` — a resulting machine operation at P.

    A match in **either** domain is enough, and a different operation in the
    other domain does not cancel it. ``created`` in the committed range and
    ``modified`` in the working tree are both true, so both
    ``path_operation(P, created)`` and ``path_operation(P, modified)`` are met.
    """
    seen = list(_attributable_observations(bundle, criterion.path))
    for observation in seen:
        if observation.change_kind == criterion.operation:
            return RESULT_MET, REASON_MACHINE_OPERATION_OBSERVED

    if _observations_at(bundle, criterion.path) and not seen:
        return RESULT_UNVERIFIED, _unattributable_reason(bundle)

    if any(
        observation.change_kind in (None, CHANGE_UNKNOWN) for observation in seen
    ):
        # A legacy observation proves the path changed and says nothing about
        # how. It can neither satisfy this criterion nor rule it out.
        return RESULT_UNVERIFIED, REASON_OPERATION_NOT_OBSERVED

    closure = _closure(bundle)
    if not closure.complete:
        return RESULT_UNVERIFIED, closure.reason or REASON_OBSERVATIONS_INCOMPLETE
    if seen:
        # Complete observation, the path was seen, and every operation recorded
        # for it is a definite one that is not the required operation.
        return RESULT_NOT_MET, REASON_COMPLETE_OPERATION_INCOMPATIBLE
    return RESULT_NOT_MET, REASON_COMPLETE_CHANGE_ABSENT


def _evaluate_rename(
    criterion: AcceptanceCriterion, bundle: EvidenceBundle
) -> Tuple[str, str]:
    """``rename(SOURCE, DESTINATION)`` — an explicit machine rename record.

    **Never inferred.** A ``created`` at the destination plus a ``deleted`` at
    the source is what a rename looks like to a tool that was not tracking one,
    and it is also what an unrelated create and an unrelated delete look like.
    Only an observation that says ``renamed`` and carries both endpoints counts,
    which is why PR5 pins ``--find-renames`` on the argv rather than leaving it
    to repository configuration.
    """
    for observation in _attributable_observations(bundle, criterion.to_path):
        if (
            observation.change_kind == CHANGE_RENAMED
            and observation.previous_path == criterion.path
        ):
            return RESULT_MET, REASON_MACHINE_RENAME_OBSERVED

    endpoints = _observations_at(bundle, criterion.path) + _observations_at(
        bundle, criterion.to_path
    )
    attributable = list(_attributable_observations(bundle, criterion.path)) + list(
        _attributable_observations(bundle, criterion.to_path)
    )
    if endpoints and not attributable:
        return RESULT_UNVERIFIED, _unattributable_reason(bundle)

    closure = _closure(bundle)
    if closure.complete:
        # The machine looked at both domains completely, with rename detection
        # on, and recorded no rename with these endpoints.
        return RESULT_NOT_MET, REASON_COMPLETE_RENAME_ABSENT
    return RESULT_UNVERIFIED, closure.reason or REASON_OBSERVATIONS_INCOMPLETE


_PREDICATES = {
    PREDICATE_PATH_CHANGED: _evaluate_path_changed,
    PREDICATE_PATH_OPERATION: _evaluate_path_operation,
    PREDICATE_RENAME: _evaluate_rename,
}


def evaluate_criterion(
    criterion: AcceptanceCriterion, bundle: EvidenceBundle
) -> CriterionResult:
    """One criterion against one bundle. Pure, total, and deterministic.

    Total on purpose: every criterion gets an answer, and a kind or predicate
    this build cannot decide gets ``unverified`` with
    :data:`REASON_UNSUPPORTED_CAPABILITY` rather than an exception. That is the
    seat a future capability — "the tests pass", "the build succeeds", "the file
    contains this text" — will occupy, and it answers honestly today instead of
    crashing when one appears.
    """
    if criterion.kind == KIND_MANUAL:
        # Always. The description is not inspected, not parsed, not matched
        # against anything and never shown to a model.
        return CriterionResult(
            criterion.criterion_id, criterion.ordinal, RESULT_UNVERIFIED, REASON_MANUAL
        )
    handler = (
        _PREDICATES.get(criterion.predicate) if criterion.kind == KIND_EVIDENCE else None
    )
    if handler is None:
        return CriterionResult(
            criterion.criterion_id,
            criterion.ordinal,
            RESULT_UNVERIFIED,
            REASON_UNSUPPORTED_CAPABILITY,
        )
    result, reason = handler(criterion, bundle)
    if reason not in REASONS_FOR_RESULT[result]:  # pragma: no cover - defensive
        raise AssertionError("reason %r is not valid for result %r" % (reason, result))
    return CriterionResult(
        criterion.criterion_id, criterion.ordinal, result, reason
    )


def evaluate(
    snapshot: CriteriaSnapshot, bundle: EvidenceBundle
) -> Tuple[CriterionResult, ...]:
    """Every criterion in the snapshot, in stored ordinal order.

    **The pure core of PR7.** No database, no filesystem, no process, no socket,
    no provider, no clock. Called twice with the same two values it returns the
    same tuple, in this process and in any other, before and after the repository
    it describes has been deleted.

    A ``not_provided`` snapshot yields an empty tuple, which is *not* a pass —
    see :func:`evaluation_fingerprint` and the store's table comment for how that
    is kept from becoming one.
    """
    if snapshot.state != CRITERIA_PRESENT:
        return ()
    return tuple(
        evaluate_criterion(criterion, bundle)
        for criterion in sorted(snapshot.criteria, key=lambda item: item.ordinal)
    )


def evaluable(snapshot: CriteriaSnapshot) -> bool:
    """Whether this snapshot is something an evaluation may be recorded for.

    ``legacy_unknown`` is not. A turn that predates criteria persistence was
    never given a question, and manufacturing a zero-result record for it would
    put a row in the database asserting that Cofferdam checked something it never
    had. The honest answer is that no evaluation exists.
    """
    return snapshot.state in (CRITERIA_PRESENT, CRITERIA_NOT_PROVIDED)


# -- the fingerprint ----------------------------------------------------------
#
# The same discipline `mind/hashing.py`, `claims.artifact_digest`,
# `evidence.input_fingerprint` and `criteria.criteria_fingerprint` record, with a
# tag of this module's own:
#
#     SHA256( tag || length-prefixed field || length-prefixed field || ... )

TAG_FINGERPRINT = b"cofferdam.evaluation.record.v1"
LENGTH_PREFIX_WIDTH = 8
FINGERPRINT_CHARS = 64


class _Fingerprint:
    """A domain-tagged, length-prefixed SHA-256 over an ordered field list."""

    __slots__ = ("_hasher",)

    def __init__(self) -> None:
        self._hasher = hashlib.sha256()
        self._hasher.update(TAG_FINGERPRINT)

    def field(self, value: object) -> "_Fingerprint":
        if value is None:
            data = b"\x00none"
        elif isinstance(value, bool):
            data = b"\x00bool:" + (b"1" if value else b"0")
        elif isinstance(value, int):
            data = b"\x00int:" + str(value).encode("utf-8")
        else:
            data = b"\x00str:" + str(value).encode("utf-8")
        self._hasher.update(len(data).to_bytes(LENGTH_PREFIX_WIDTH, "big"))
        self._hasher.update(data)
        return self

    def hexdigest(self) -> str:
        return self._hasher.hexdigest()


def evaluation_fingerprint(
    *,
    snapshot: CriteriaSnapshot,
    bundle: EvidenceBundle,
    results: Sequence[CriterionResult],
    evaluator_version: int = EVALUATOR_VERSION,
) -> str:
    """A stable hash of exactly what this judgement was made of, and what it said.

    What is in it
    -------------

    The **evaluator version**, so a future semantic change is visibly a different
    judgement rather than a silent disagreement. The **turn identity**. The
    **criteria identity** — both ``snapshot_id`` and ``criteria_fingerprint``,
    which answer different questions: which durable snapshot row was read, and
    what immutable content that row represented. The **evidence identity** —
    ``assembler_version`` and ``input_fingerprint``, for the same pair of
    reasons. And every **result**, by criterion id, ordinal, result and reason.

    The criteria state is bound explicitly, so a ``not_provided`` evaluation and
    a hypothetical empty ``present`` one could never hash alike.

    What is deliberately **not** in it
    ----------------------------------

    * **The evaluation id.** It is server-minted with a clock and randomness, so
      hashing it would make the fingerprint identify the *row* rather than the
      judgement, and re-deriving the same evaluation would produce a different
      value every time.
    * **``recorded_at``, or any clock reading.** A judgement made twice from the
      same frozen inputs is the same judgement, whenever it was made.
    * **Database row ids and insertion order.** Results are walked by stored
      ordinal.
    * **Absolute host paths, project roots, deployment slots.** Every path that
      reaches this function is project-relative by construction.
    * **Provider or session identifiers.**
    * **The evidence bundle's contents.** The bundle is identified by its
      ``input_fingerprint``, not copied: an evaluation that embedded the evidence
      would be a second durable shape for facts the bundle already owns, and the
      two could drift.
    """
    digest = _Fingerprint()
    digest.field("cofferdam.evaluation.record")
    digest.field(evaluator_version)
    digest.field(bundle.task_id)
    digest.field(bundle.turn_number)
    digest.field(snapshot.state)
    digest.field(snapshot.snapshot_id)
    digest.field(snapshot.fingerprint)
    digest.field(bundle.assembler_version)
    digest.field(bundle.input_fingerprint)
    digest.field(len(results))
    for item in sorted(results, key=lambda entry: entry.ordinal):
        digest.field(item.criterion_id)
        digest.field(item.ordinal)
        digest.field(item.result)
        digest.field(item.reason)
    return digest.hexdigest()


__all__ = [
    "EVALUATOR_VERSION",
    "EXCLUDED_RESULTS",
    "FINGERPRINT_CHARS",
    "REASONS",
    "REASONS_FOR_RESULT",
    "REASON_ATTRIBUTION_UNKNOWN",
    "REASON_BOUNDARY_NOT_CLEAN",
    "REASON_COMPLETE_CHANGE_ABSENT",
    "REASON_COMPLETE_OPERATION_INCOMPATIBLE",
    "REASON_COMPLETE_RENAME_ABSENT",
    "REASON_HISTORY_DIVERGED",
    "REASON_MACHINE_CHANGE_OBSERVED",
    "REASON_MACHINE_OPERATION_OBSERVED",
    "REASON_MACHINE_RENAME_OBSERVED",
    "REASON_MANUAL",
    "REASON_OBSERVATIONS_INCOMPLETE",
    "REASON_OPERATION_NOT_OBSERVED",
    "REASON_RANGE_INCOMPLETE",
    "REASON_RANGE_NOT_RECORDED",
    "REASON_UNSUPPORTED_CAPABILITY",
    "REASON_UNSUPPORTED_OBSERVATION",
    "REASON_WORKTREE_NOT_OBSERVED",
    "RESULTS",
    "RESULT_MET",
    "RESULT_NOT_MET",
    "RESULT_UNVERIFIED",
    "TAG_FINGERPRINT",
    "CriterionResult",
    "EvaluationRecord",
    "evaluable",
    "evaluate",
    "evaluate_criterion",
    "evaluation_fingerprint",
    "new_evaluation_id",
    "valid_evaluation_id",
]
