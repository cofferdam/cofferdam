"""What can honestly be said about each active criterion, at one target turn.

M2K PR16, and it is the layer PR13 said was missing and PR15 designed. PR11
answers *which criteria are in force at turn N*. PR7 answers *what did the worker
do during turn N*. Neither answers the question an aggregate would have to ask:

    for every criterion active at turn N, what current result can Cofferdam
    legitimately establish **at N**?

This module answers exactly that, for the predicates that exist today, and
refuses to answer for the ones that do not.

The rule everything else follows
--------------------------------

**Evidence must match the criterion's semantics.** Every machine predicate in
this build — ``path_changed``, ``path_operation``, ``rename`` — is a *turn-change*
observation: it asks what a worker did during one particular turn. That makes it
answerable at its own turn and nowhere else.

So an active criterion gets a current result from exactly one of three places,
decided by where it came from and what kind of thing it is:

* **it originated at the target turn, and it is a change predicate** — its answer
  is PR7's stored judgement for that turn. Read, never recomputed;
* **it was inherited from an earlier turn, and it is a change predicate** —
  ``unverified``. Not the old result, and not a fresh evaluation;
* **it is manual** — ``unverified``, wherever it came from.

Why an inherited change criterion is `unverified`
-------------------------------------------------

Three tempting answers exist and PR13 showed all three are wrong.

**Carrying the old result forward** reuses a statement about turn 1 as a statement
about turn 4. It misses later breakage when it was ``met`` and later repair when
it was ``not_met``, and the direction of the error is unknowable from the record.

**Re-evaluating against the target turn's evidence** asks "did *this* turn create
``foo.py``?" of a requirement that was satisfied three turns ago and correctly
left alone since. The honest answer to that question is *no*, and reporting it as
``not_met`` would fail work precisely when it was right.

**Reading PR14's final state** — ``foo.py`` is present, so ``created`` is met —
is the semantic conversion this milestone refuses everywhere. *Present* answers a
question nobody asked; ``path_operation(foo.py, created)`` asks what the worker
did. PR14's observations exist on main and this module does not look at them:
they become relevant when explicit state predicates exist, and not one moment
earlier.

``unverified`` is therefore not a placeholder or an admission of laziness. It is
the accurate answer: Cofferdam has no evidence of the right kind, and says so.

What this is not
----------------

**No aggregate.** No ``met`` count, no verdict, no pass, no fail, no
``AGGREGATOR_VERSION``. This produces the legitimate per-criterion inputs a future
aggregate may consume, and stops.

**Nothing persisted.** Every input is immutable and versioned, so the answer is a
pure function of stored facts and re-derives identically forever. Schema stays at
v10 and no table is added.

**No new predicate**, no change to ``EVALUATOR_VERSION``, and no reinterpretation
of a single stored PR7 row.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from .criteria import (
    KIND_EVIDENCE,
    KIND_MANUAL,
    PREDICATE_PATH_CHANGED,
    PREDICATE_PATH_OPERATION,
    PREDICATE_RENAME,
)
from .evaluation import (
    EVALUATOR_VERSION,
    RESULT_MET,
    RESULT_NOT_MET,
    RESULT_UNVERIFIED,
)

#: Bumped when the **meaning** of a current assessment changes: a different
#: binding rule, a different domain vocabulary, a different notion of which
#: evidence may answer which criterion.
#:
#: Distinct from :data:`~.store.SCHEMA_VERSION` (table shape),
#: :data:`~.evidence.ASSEMBLER_VERSION` (how a bundle is built),
#: :data:`~.evaluation.EVALUATOR_VERSION` (how a turn-change criterion is
#: decided), :data:`~.criteria.CRITERIA_MODEL_VERSION`,
#: :data:`~.continuity.CONTINUITY_MODEL_VERSION`,
#: :data:`~.lineage.RESOLVER_VERSION`,
#: :data:`~.finalstate.FINAL_STATE_OBSERVER_VERSION`, and the future aggregate
#: version. Eight things that move for eight reasons; a reader must be able to
#: tell which one did.
#:
#: This one owns exactly the mapping *active criterion + evidence domain →
#: current result at a target turn*. It does not own how any underlying
#: judgement was reached.
CURRENT_ASSESSMENT_VERSION = 1


# -- evidence domains ---------------------------------------------------------

#: The answer came from a PR7 turn-change evaluation of the target turn's own
#: criteria snapshot. The only machine domain this version can bind.
DOMAIN_TURN_CHANGE = "turn_change"

#: No machine domain applies. A manual criterion, or a change criterion whose
#: semantics cannot reach the target turn.
#:
#: Deliberately **not** called ``none``: a domain is a statement about what kind
#: of evidence *could* answer this criterion here, and "none of the machine ones"
#: is a real answer rather than a missing field.
DOMAIN_NOT_APPLICABLE = "not_applicable"

#: Closed. ``final_state`` and ``named_check`` are the two that will join, and
#: neither is implemented — adding a domain must not reinterpret a V1 answer, so
#: every assessment binds the domain it used into its fingerprint.
EVIDENCE_DOMAINS: Tuple[str, ...] = (DOMAIN_TURN_CHANGE, DOMAIN_NOT_APPLICABLE)


# -- per-criterion reasons ----------------------------------------------------

#: The criterion originated at the target turn and PR7 decided it there. The
#: result is PR7's, unchanged.
REASON_TURN_CHANGE_EVALUATED = "turn_change_evaluated"

#: **The load-bearing one.** A change predicate inherited from an earlier turn.
#: It asks what a worker did at its origin turn, which is not a question about
#: the target turn, and no evidence in this build answers it there. The old
#: result is not reused and no re-evaluation is attempted.
REASON_INHERITED_CHANGE_NOT_CURRENT = "inherited_change_not_current_state_evaluable"

#: A manual criterion. No machine authority exists for it at any turn, and this
#: build has no human-answer channel — that is a surface decision nobody has
#: made.
REASON_MANUAL_AUTHORITY = "manual_criterion_no_machine_authority"

#: An evidence criterion whose predicate this binder does not know. Total rather
#: than raising, the same discipline PR7's evaluator uses: a criterion written by
#: a newer build must get an honest answer from an older one.
REASON_UNSUPPORTED_PREDICATE = "unsupported_predicate"

CRITERION_REASONS: Tuple[str, ...] = (
    REASON_TURN_CHANGE_EVALUATED,
    REASON_INHERITED_CHANGE_NOT_CURRENT,
    REASON_MANUAL_AUTHORITY,
    REASON_UNSUPPORTED_PREDICATE,
)

#: Which reasons may accompany which result. ``met`` and ``not_met`` are only
#: ever a bound PR7 judgement: this version has exactly one machine domain, and
#: nothing else in it can decide a criterion either way.
REASONS_FOR_RESULT: Dict[str, Tuple[str, ...]] = {
    RESULT_MET: (REASON_TURN_CHANGE_EVALUATED,),
    RESULT_NOT_MET: (REASON_TURN_CHANGE_EVALUATED,),
    RESULT_UNVERIFIED: (
        REASON_TURN_CHANGE_EVALUATED,
        REASON_INHERITED_CHANGE_NOT_CURRENT,
        REASON_MANUAL_AUTHORITY,
        REASON_UNSUPPORTED_PREDICATE,
    ),
}


# -- set-level states ---------------------------------------------------------

#: Every active criterion got an answer. The set may legitimately be **empty**.
ASSESSMENT_RESOLVED = "resolved"

#: No defensible assessment exists, and a closed reason says why. Carries no
#: criterion assessments: a partial set is one a caller would use.
ASSESSMENT_UNAVAILABLE = "unavailable"

ASSESSMENT_STATES: Tuple[str, ...] = (ASSESSMENT_RESOLVED, ASSESSMENT_UNAVAILABLE)


# -- set-level reasons --------------------------------------------------------

#: PR11 could not resolve the active set — ``not_declared``, ``legacy_unknown``,
#: a broken chain. There is no requirement set to assess, and guessing one from
#: the latest snapshot or from all history is the inference this milestone
#: refuses.
REASON_LINEAGE_UNAVAILABLE = "lineage_unavailable"

#: The target turn is not a completed boundary: it does not exist, or it is open.
#: A "current assessment" of a turn still running would describe a moment that
#: has not happened.
REASON_TURN_NOT_CLOSED = "turn_not_closed"

#: The target turn is closed and its criteria are evaluable, but PR7 has not
#: recorded an evaluation yet. **Operational, not semantic.** PR7 evaluation is a
#: bounded recovery pass, so this is very often a matter of timing — and reporting
#: it as a set of ``unverified`` criteria would file a temporary gap in
#: Cofferdam's own pipeline as a statement about the user's work.
REASON_EVALUATION_NOT_RECORDED = "evaluation_not_recorded"

#: A stored PR7 row disagrees with the service-owned invariants that make it
#: meaningful — a snapshot from another turn, a result for a criterion outside
#: its snapshot, a missing or duplicated answer, a count that does not match.
#: PR15 proved the DDL permits several of these, so they are checked here rather
#: than assumed. Fails closed and repairs nothing.
REASON_EVALUATION_INCONSISTENT = "evaluation_inconsistent"

#: The stored evaluation was produced by an evaluator version whose semantics
#: this binder does not know. A future ``EVALUATOR_VERSION`` 2 may decide
#: criteria differently, and binding its results as though they meant version 1's
#: thing is precisely the silent reinterpretation this layer exists to prevent.
REASON_UNSUPPORTED_EVALUATOR = "unsupported_evaluator_version"

SET_REASONS: Tuple[str, ...] = (
    REASON_LINEAGE_UNAVAILABLE,
    REASON_TURN_NOT_CLOSED,
    REASON_EVALUATION_NOT_RECORDED,
    REASON_EVALUATION_INCONSISTENT,
    REASON_UNSUPPORTED_EVALUATOR,
)

#: Evaluator semantics this version knows how to bind. Explicitly a set rather
#: than ``<= EVALUATOR_VERSION``: an old binder must not assume it understands a
#: newer evaluator, and this is the seat where that judgement is recorded.
SUPPORTED_EVALUATOR_VERSIONS: Tuple[int, ...] = (EVALUATOR_VERSION,)

#: Change predicates. Each asks what happened during one turn, which is why each
#: is answerable only at its own turn.
CHANGE_PREDICATES: Tuple[str, ...] = (
    PREDICATE_PATH_CHANGED,
    PREDICATE_PATH_OPERATION,
    PREDICATE_RENAME,
)


# -- shapes -------------------------------------------------------------------


@dataclass(frozen=True)
class CriterionAssessment:
    """One active criterion's current status at the target turn.

    ``source_turn_number`` and ``target_turn_number`` are **both** here and are
    never collapsed. A criterion introduced at turn 1 and assessed at turn 4 has
    two honest identities, and a single turn number could only tell the truth
    about one of them.

    ``evidence_fingerprint`` is the identity of the exact immutable machine
    judgement this result came from, and is ``None`` whenever there is not one.
    No placeholder is invented for a criterion nothing decided: a fingerprint
    that pointed at evidence which did not answer this criterion would be worse
    than an empty field, because it would look like provenance.
    """

    criterion_id: str
    source_snapshot_id: str
    source_turn_number: int
    target_turn_number: int
    kind: str
    predicate: Optional[str]
    domain: str
    result: str
    reason: str
    #: The PR7 ``evaluation_fingerprint`` this result was bound from, when the
    #: domain is :data:`DOMAIN_TURN_CHANGE`.
    evidence_fingerprint: Optional[str] = None
    fingerprint: str = ""

    @property
    def inherited(self) -> bool:
        """Whether this criterion came from a turn earlier than the target."""
        return self.source_turn_number < self.target_turn_number


@dataclass(frozen=True)
class AssessmentInputs:
    """Everything one current assessment may look at, read as one snapshot.

    The store fills this inside a single read transaction and nothing else is
    consulted afterwards, which is what makes the answer a pure function of a
    coherent database state rather than of several states stitched together.

    ``graph`` is PR11's immutable input graph — resolved by PR11's own resolver,
    never re-implemented here. ``evaluation`` is the target turn's stored PR7
    record or ``None``. ``turn_closed`` is whether the target turn is a completed
    boundary.
    """

    graph: object
    evaluation: object = None
    turn_closed: bool = False


@dataclass(frozen=True)
class CurrentAssessment:
    """Every active criterion at one target turn, or why there is no answer.

    One shape for both states, so a reader never branches on ``None`` before it
    can ask what happened.

    Deliberately **not** published. No ``to_dict``, no route, no bridge Action,
    no PWA control, and PR8's assessment response is unchanged — a read surface
    is its own review, and a serializer written before anything needs one is how
    an internal shape becomes a contract by accident.
    """

    task_id: str
    target_turn_number: int
    assessment_version: int
    state: str
    #: PR11's resolved-active fingerprint. Present exactly when resolved: it is
    #: what proves *these* criteria were the ones in force here.
    lineage_fingerprint: Optional[str] = None
    unavailable_reason: Optional[str] = None
    assessments: Tuple[CriterionAssessment, ...] = ()
    fingerprint: str = ""

    @property
    def resolved(self) -> bool:
        return self.state == ASSESSMENT_RESOLVED

    @property
    def criterion_count(self) -> int:
        """How many active criteria were assessed.

        Zero is a legitimate resolved answer — an explicit lineage with an empty
        requirement set. It does **not** mean acceptance was met, that the task
        passed, or that anything succeeded.
        """
        return len(self.assessments)


# -- the fingerprint ----------------------------------------------------------

TAG_FINGERPRINT = b"cofferdam.assessment.current.v1"
LENGTH_PREFIX_WIDTH = 8
FINGERPRINT_CHARS = 64


class _Fingerprint:
    """The domain-tagged, length-prefixed construction the rest of M2K uses."""

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


def criterion_assessment_fingerprint(
    *,
    criterion_id: str,
    source_snapshot_id: str,
    source_turn_number: int,
    target_turn_number: int,
    kind: str,
    predicate: Optional[str],
    domain: str,
    result: str,
    reason: str,
    evidence_fingerprint: Optional[str],
) -> str:
    """A stable hash of one current answer, and of what entitled it.

    Binds the assessment version; the criterion's identity **and its origin**;
    the target turn; the semantic kind and predicate; the evidence domain; the
    result and its reason; and the identity of the exact machine judgement used,
    where there is one.

    The origin fields are in because *the same criterion assessed at a different
    turn is a different fact*, and the domain is in because a future
    ``final_state`` answer of ``met`` must never hash equal to today's
    ``turn_change`` one. The evidence fingerprint is in because a result that
    cannot say which judgement it came from is not auditable.

    Deliberately **not** in it: any clock, the PR7 ``evaluation_id`` (a minted
    row handle carrying randomness — the *fingerprint* is the judgement's
    identity), database rowids, absolute host paths, and provider or session
    identifiers.
    """
    digest = _Fingerprint()
    digest.field("cofferdam.assessment.criterion")
    digest.field(CURRENT_ASSESSMENT_VERSION)
    digest.field(criterion_id)
    digest.field(source_snapshot_id)
    digest.field(source_turn_number)
    digest.field(target_turn_number)
    digest.field(kind)
    digest.field(predicate)
    digest.field(domain)
    digest.field(result)
    digest.field(reason)
    digest.field(evidence_fingerprint)
    return digest.hexdigest()


def current_assessment_fingerprint(
    *,
    task_id: str,
    target_turn_number: int,
    state: str,
    unavailable_reason: Optional[str],
    lineage_fingerprint: Optional[str],
    assessments: Sequence[CriterionAssessment],
) -> str:
    """A stable hash of the whole answer at one target turn.

    Binds the assessment version, the target, the set state and its reason, the
    **lineage fingerprint** that selected the criteria, and every criterion
    fingerprint in resolved-active order.

    The lineage fingerprint is bound because *which criteria were in force* is
    half of what the set asserts: two turns whose criteria all read ``unverified``
    are not the same fact if they were standing on different requirement sets.
    Order is bound by hashing the sequence as given, never a re-sorted copy.
    """
    digest = _Fingerprint()
    digest.field("cofferdam.assessment.set")
    digest.field(CURRENT_ASSESSMENT_VERSION)
    digest.field(task_id)
    digest.field(target_turn_number)
    digest.field(state)
    digest.field(unavailable_reason)
    digest.field(lineage_fingerprint)
    digest.field(len(assessments))
    for item in assessments:
        digest.field(item.fingerprint)
    return digest.hexdigest()


# -- the binder ---------------------------------------------------------------
#
# Pure. No SQLite, no filesystem, no Git, no subprocess, no network, no provider,
# no environment, no clock, no mutation, and no observer of any kind. Called
# twice with the same inputs it returns the same answer, in this process and any
# other, before and after the repository it describes has been deleted.


def _unavailable(
    task_id: str, target_turn_number: int, reason: str
) -> CurrentAssessment:
    return CurrentAssessment(
        task_id=task_id,
        target_turn_number=int(target_turn_number),
        assessment_version=CURRENT_ASSESSMENT_VERSION,
        state=ASSESSMENT_UNAVAILABLE,
        unavailable_reason=reason,
        fingerprint=current_assessment_fingerprint(
            task_id=task_id,
            target_turn_number=int(target_turn_number),
            state=ASSESSMENT_UNAVAILABLE,
            unavailable_reason=reason,
            lineage_fingerprint=None,
            assessments=(),
        ),
    )


def _evaluation_is_consistent(evaluation, resolved) -> bool:
    """Whether a stored PR7 record still satisfies its service-owned invariants.

    PR15 established that the DDL does **not** enforce these: a result may name a
    criterion from any snapshot, and an evaluation's ``turn_number`` and
    ``criteria_snapshot_id`` may disagree. `record_evaluation` refuses such rows,
    so a database containing one has been edited outside the service or is
    corrupt — and this layer must not read it as a judgement.

    Checked, in order: the record belongs to the target task and turn; its
    snapshot is the target turn's own; its declared count matches the results it
    carries; and no criterion is answered twice.

    Nothing is repaired. A read that fixed a row would destroy the evidence that
    something wrote it.
    """
    if evaluation.task_id != resolved.task_id:
        return False
    if int(evaluation.turn_number) != int(resolved.target_turn_number):
        return False
    if evaluation.criteria_snapshot_id != resolved.target_snapshot_id:
        return False
    if int(evaluation.result_count) != len(evaluation.results):
        return False
    seen = {item.criterion_id for item in evaluation.results}
    if len(seen) != len(evaluation.results):
        return False
    return True


def _same_turn_change_ids(resolved) -> Tuple[str, ...]:
    """Active criteria that must have a PR7 answer at the target turn."""
    return tuple(
        entry.criterion_id
        for entry in resolved.active
        if entry.source_turn_number == resolved.target_turn_number
        and entry.criterion.kind == KIND_EVIDENCE
        and entry.criterion.predicate in CHANGE_PREDICATES
    )


def bind(resolved, evaluation, *, turn_closed: bool) -> CurrentAssessment:
    """Current status for every active criterion at the target turn.

    ``resolved`` is PR11's :class:`~.lineage.ResolvedActiveCriteria` or a
    :class:`~.lineage.LineageUnavailable`; ``evaluation`` is the target turn's
    stored :class:`~.evaluation.EvaluationRecord` or ``None``; ``turn_closed``
    says whether the target turn is a completed boundary.

    The order of refusals is the doctrine, and each is a different kind of
    silence that must not be confused with the others:

    #. **the turn is not a completed boundary** — a current assessment of a turn
       still running describes a moment that has not happened;
    #. **the lineage is unavailable** — there is no defensible requirement set,
       and the latest snapshot is not a substitute for one;
    #. **an evaluation is required and missing** — operational, and reported as
       such rather than as a set of ``unverified`` criteria;
    #. **an evaluation is present but inconsistent, or from an evaluator this
       version does not understand** — fails closed.

    Only then is every active criterion answered, in PR11's order, unchanged.
    """
    if not turn_closed:
        return _unavailable(
            resolved.task_id, resolved.target_turn_number, REASON_TURN_NOT_CLOSED
        )
    if not resolved.resolved:
        return _unavailable(
            resolved.task_id, resolved.target_turn_number, REASON_LINEAGE_UNAVAILABLE
        )

    # A PR7 record is required only if some active criterion actually needs one.
    # A turn whose active set is entirely inherited or manual is fully
    # answerable without any evaluation at all, and demanding one would make a
    # legitimate answer wait on a record nothing was going to read.
    required = _same_turn_change_ids(resolved)
    if required:
        if evaluation is None:
            return _unavailable(
                resolved.task_id,
                resolved.target_turn_number,
                REASON_EVALUATION_NOT_RECORDED,
            )
        if int(evaluation.evaluator_version) not in SUPPORTED_EVALUATOR_VERSIONS:
            return _unavailable(
                resolved.task_id,
                resolved.target_turn_number,
                REASON_UNSUPPORTED_EVALUATOR,
            )
        if not _evaluation_is_consistent(evaluation, resolved):
            return _unavailable(
                resolved.task_id,
                resolved.target_turn_number,
                REASON_EVALUATION_INCONSISTENT,
            )
        answered = {item.criterion_id: item for item in evaluation.results}
        if any(criterion_id not in answered for criterion_id in required):
            # PR7 answers every criterion in the snapshot it evaluated, so a
            # same-turn criterion with no answer means the stored record does not
            # describe the snapshot it claims to.
            return _unavailable(
                resolved.task_id,
                resolved.target_turn_number,
                REASON_EVALUATION_INCONSISTENT,
            )
    else:
        answered = {}

    assessments = []
    for entry in resolved.active:
        criterion = entry.criterion
        target = int(resolved.target_turn_number)
        if criterion.kind == KIND_MANUAL:
            domain, result, reason = (
                DOMAIN_NOT_APPLICABLE,
                RESULT_UNVERIFIED,
                REASON_MANUAL_AUTHORITY,
            )
            evidence = None
        elif criterion.predicate not in CHANGE_PREDICATES:
            domain, result, reason = (
                DOMAIN_NOT_APPLICABLE,
                RESULT_UNVERIFIED,
                REASON_UNSUPPORTED_PREDICATE,
            )
            evidence = None
        elif entry.source_turn_number != target:
            # The whole point of the layer. Not the old result, not a fresh
            # evaluation, and emphatically not PR14's final state.
            domain, result, reason = (
                DOMAIN_NOT_APPLICABLE,
                RESULT_UNVERIFIED,
                REASON_INHERITED_CHANGE_NOT_CURRENT,
            )
            evidence = None
        else:
            stored = answered[entry.criterion_id]
            domain, result, reason = (
                DOMAIN_TURN_CHANGE,
                stored.result,
                REASON_TURN_CHANGE_EVALUATED,
            )
            evidence = evaluation.evaluation_fingerprint
        assessments.append(
            CriterionAssessment(
                criterion_id=entry.criterion_id,
                source_snapshot_id=entry.source_snapshot_id,
                source_turn_number=int(entry.source_turn_number),
                target_turn_number=target,
                kind=criterion.kind,
                predicate=criterion.predicate,
                domain=domain,
                result=result,
                reason=reason,
                evidence_fingerprint=evidence,
                fingerprint=criterion_assessment_fingerprint(
                    criterion_id=entry.criterion_id,
                    source_snapshot_id=entry.source_snapshot_id,
                    source_turn_number=int(entry.source_turn_number),
                    target_turn_number=target,
                    kind=criterion.kind,
                    predicate=criterion.predicate,
                    domain=domain,
                    result=result,
                    reason=reason,
                    evidence_fingerprint=evidence,
                ),
            )
        )
    items = tuple(assessments)
    return CurrentAssessment(
        task_id=resolved.task_id,
        target_turn_number=int(resolved.target_turn_number),
        assessment_version=CURRENT_ASSESSMENT_VERSION,
        state=ASSESSMENT_RESOLVED,
        lineage_fingerprint=resolved.fingerprint,
        assessments=items,
        fingerprint=current_assessment_fingerprint(
            task_id=resolved.task_id,
            target_turn_number=int(resolved.target_turn_number),
            state=ASSESSMENT_RESOLVED,
            unavailable_reason=None,
            lineage_fingerprint=resolved.fingerprint,
            assessments=items,
        ),
    )


__all__ = [
    "ASSESSMENT_RESOLVED",
    "ASSESSMENT_STATES",
    "ASSESSMENT_UNAVAILABLE",
    "CHANGE_PREDICATES",
    "CRITERION_REASONS",
    "CURRENT_ASSESSMENT_VERSION",
    "DOMAIN_NOT_APPLICABLE",
    "DOMAIN_TURN_CHANGE",
    "EVIDENCE_DOMAINS",
    "FINGERPRINT_CHARS",
    "REASONS_FOR_RESULT",
    "REASON_EVALUATION_INCONSISTENT",
    "REASON_EVALUATION_NOT_RECORDED",
    "REASON_INHERITED_CHANGE_NOT_CURRENT",
    "REASON_LINEAGE_UNAVAILABLE",
    "REASON_MANUAL_AUTHORITY",
    "REASON_TURN_CHANGE_EVALUATED",
    "REASON_TURN_NOT_CLOSED",
    "REASON_UNSUPPORTED_EVALUATOR",
    "REASON_UNSUPPORTED_PREDICATE",
    "SET_REASONS",
    "SUPPORTED_EVALUATOR_VERSIONS",
    "TAG_FINGERPRINT",
    "AssessmentInputs",
    "CriterionAssessment",
    "CurrentAssessment",
    "bind",
    "criterion_assessment_fingerprint",
    "current_assessment_fingerprint",
]
