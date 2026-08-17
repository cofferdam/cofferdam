"""What can honestly be said about each active criterion, at one target turn.

M2K PR16, and it is the layer PR13 said was missing and PR15 designed. PR11
answers *which criteria are in force at turn N*. PR7 answers *what did the worker
do during turn N*. Neither answers the question an aggregate would have to ask:

    for every criterion active at turn N, what current result can Cofferdam
    legitimately establish **at N**?

This module answers exactly that, for the predicates that exist today, and
refuses to answer for the ones that do not.

M2K PR18 is the second version. PR17 made ``path_exists`` and ``path_absent``
representable and left both answering ``unverified`` / ``unsupported_predicate``
here, on purpose, because nothing had reviewed what they should mean. This
version gives them the one evidence domain that can honestly decide them —
PR14's immutable :class:`~.finalstate.FinalStateObservation` — and nothing else
about V1 moves.

The rule everything else follows
--------------------------------

**Evidence must match the criterion's semantics.** A machine predicate in this
build is one of two kinds, and the kind decides which evidence may answer it:

* ``path_changed``, ``path_operation``, ``rename`` are *turn-change* observations.
  Each asks what a worker did during one particular turn, which makes it
  answerable at its own turn and nowhere else;
* ``path_exists``, ``path_absent`` are *state* observations. Each asks what the
  project **is** at a boundary, which makes it re-askable at every turn — and
  answerable at the target turn from the target turn's own final state.

So an active criterion gets a current result from exactly one of four places,
decided by where it came from and what kind of thing it is:

* **it originated at the target turn, and it is a change predicate** — its answer
  is PR7's stored judgement for that turn. Read, never recomputed;
* **it was inherited from an earlier turn, and it is a change predicate** —
  ``unverified``. Not the old result, and not a fresh evaluation;
* **it is a state predicate**, inherited or not — the **target turn's** stored
  final-state observation decides it. Never an earlier turn's answer;
* **it is manual** — ``unverified``, wherever it came from.

Why a state criterion ignores its own PR7 row
---------------------------------------------

PR7 records ``path_exists`` as ``unverified`` / ``unsupported_capability``, and
that record is correct and stays: PR7's evaluator decides turn-change questions
and this is not one. It is a permanent historical statement about *what the
turn-change evaluator could establish*, not about the path — which is exactly why
the stored evaluation and the current assessment are two layers rather than one.
This binder does not read that result for a state criterion, does not rewrite it,
and does not delete it.

Why an inherited state criterion is **not** ``unverified``
-----------------------------------------------------------

An inherited *change* criterion is unanswerable at a later turn because its
question is about a turn that already ended. An inherited *state* criterion is
the opposite: its question — *is ``foo.py`` there?* — is exactly as meaningful at
turn 4 as at turn 1, and the target turn's own observation answers it. So it is
re-assessed at every target, and a file that was present at turn 1, deleted at
turn 2 and restored at turn 3 produces ``met``, ``not_met``, ``met``. Three
different derived facts about three different boundaries, none of them carried
forward from another.

What still does not happen
--------------------------

**No change-to-state conversion, in either direction.** ``path_operation(foo.py,
created)`` is decided by PR7 and stays decided by PR7 even when final state says
``foo.py`` is gone — the criterion asks what the worker did, and it did. And
``foo.py`` being present does not satisfy it either. Only an **explicitly
authored** ``path_exists`` or ``path_absent`` consults final state. See
D-2026-08-17-4.

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
did. PR18 reads final state and still refuses this: the observation decides
``path_exists(foo.py)`` and touches no change criterion.

``unverified`` is therefore not a placeholder or an admission of laziness. It is
the accurate answer: Cofferdam has no evidence of the right kind, and says so.

Evidence is fetched because something needs it
-----------------------------------------------

Neither input is unconditional, and that is load-bearing rather than an
optimisation. A PR7 evaluation is required **only** when some active criterion
originated at the target turn *and* is a change predicate; a final-state
observation is required **only** when some active criterion is a state predicate.
A target whose active set is one ``path_exists`` resolves with no PR7 record at
all, because nothing in it consumes a turn-change judgement — and demanding one
would make a legitimate answer wait on a row nobody was going to read. The
converse holds exactly as strictly: a change-and-manual target never depends on
final state existing.

What this is not
----------------

**No aggregate.** No ``met`` count, no verdict, no pass, no fail, no
``AGGREGATOR_VERSION``. This produces the legitimate per-criterion inputs a future
aggregate may consume, and stops.

**Nothing persisted.** Every input is immutable and versioned, so the answer is a
pure function of stored facts and re-derives identically forever. Schema stays at
v11 and no table is added.

**No new predicate**, no change to ``EVALUATOR_VERSION`` or
``FINAL_STATE_OBSERVER_VERSION``, and no reinterpretation of a single stored PR7
or PR14 row.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from .criteria import (
    KIND_EVIDENCE,
    KIND_MANUAL,
    PREDICATE_PATH_ABSENT,
    PREDICATE_PATH_CHANGED,
    PREDICATE_PATH_EXISTS,
    PREDICATE_PATH_OPERATION,
    PREDICATE_RENAME,
)
from .evaluation import (
    EVALUATOR_VERSION,
    RESULT_MET,
    RESULT_NOT_MET,
    RESULT_UNVERIFIED,
)

# PR14's vocabulary and its pure fingerprint verifier, and **nothing else from
# that module**. No observer, no path lookup, no target selection: this binder
# consumes a stored observation as a value and would produce the same answer if
# the repository it describes had been deleted years ago. The purity tests pin
# this import list exactly, because "use FinalStateObservation" means *read the
# stored row* and never *go and look now*.
from .finalstate import (
    FINAL_STATE_OBSERVER_VERSION,
    OBSERVATION_COMPLETE,
    OBSERVATION_INCOMPLETE,
    OBSERVATION_UNAVAILABLE,
    PATH_ABSENT,
    PATH_PRESENT,
    PATH_UNAVAILABLE,
    STORED_OBSERVATION_STATES,
    verify_final_state_fingerprint,
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
#:
#: **2 (M2K PR18).** The binding semantics now understand a second legitimate
#: evidence domain, :data:`DOMAIN_FINAL_STATE`, and a criterion that V1 answered
#: ``unverified`` / ``unsupported_predicate`` can now be ``met`` or ``not_met``.
#: That is a change in *meaning*, not in shape, which is precisely what this
#: number is for: a V1 fingerprint and a V2 fingerprint of the same criterion
#: must not collide, because they are answers to the question as two different
#: builds understood it. Nothing else moves — the schema, the evaluator, the
#: observer, the resolver and the criteria model are all untouched.
CURRENT_ASSESSMENT_VERSION = 2


# -- evidence domains ---------------------------------------------------------

#: The answer came from a PR7 turn-change evaluation of the target turn's own
#: criteria snapshot.
DOMAIN_TURN_CHANGE = "turn_change"

#: The answer came from the **target turn's** immutable PR14 final-state
#: observation: a state predicate, decided by what was actually there at that
#: turn's post-worker boundary.
#:
#: Separate from :data:`DOMAIN_TURN_CHANGE` and never interchangeable with it. A
#: turn-change judgement says what a worker did and a final-state observation
#: says what is there, and the two disagree routinely without either being
#: wrong: a turn that provably created ``foo.py`` and a later boundary at which
#: ``foo.py`` is gone are both true. Binding the domain into every fingerprint is
#: what stops one from ever being read as the other.
DOMAIN_FINAL_STATE = "final_state"

#: No machine domain applies. A manual criterion, or a change criterion whose
#: semantics cannot reach the target turn.
#:
#: Deliberately **not** called ``none``: a domain is a statement about what kind
#: of evidence *could* answer this criterion here, and "none of the machine ones"
#: is a real answer rather than a missing field.
DOMAIN_NOT_APPLICABLE = "not_applicable"

#: Closed. ``named_check`` is the one that will join and it is not implemented —
#: adding a domain must not reinterpret an older answer, so every assessment
#: binds the domain it used into its fingerprint and the version above moves when
#: the vocabulary does.
EVIDENCE_DOMAINS: Tuple[str, ...] = (
    DOMAIN_TURN_CHANGE,
    DOMAIN_FINAL_STATE,
    DOMAIN_NOT_APPLICABLE,
)


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
#:
#: No predicate in this build reaches it any more — PR17's two state predicates
#: were the last to, and PR18 decides them. It stays because the next predicate
#: to be authored before it is bound will need it.
REASON_UNSUPPORTED_PREDICATE = "unsupported_predicate"

#: **M2K PR18.** A state predicate, decided by the target turn's stored
#: final-state observation. The result is what was observed at that boundary,
#: never a live look and never an earlier turn's answer.
REASON_FINAL_STATE_OBSERVED = "final_state_observed"

#: A state predicate whose exact path was recorded as
#: :data:`~.finalstate.PATH_UNAVAILABLE` — a refused symlink traversal, a
#: permission wall, an IO error. Cofferdam could not look safely, which is a
#: different fact from having looked and found nothing, and reporting it as
#: ``not_met`` would convert a limitation of the observer into a failure of the
#: user's work.
REASON_FINAL_STATE_PATH_UNAVAILABLE = "final_state_path_unavailable"

#: A state predicate at a turn whose whole final-state observation is
#: :data:`~.finalstate.OBSERVATION_UNAVAILABLE`. No trustworthy path results
#: exist at all, so there is nothing to read and nothing may be inferred.
REASON_FINAL_STATE_UNAVAILABLE = "final_state_unavailable"

#: A state predicate at a turn with **no** final-state row: one that ran before
#: PR14, or one whose process died before the boundary could be recorded.
#: Semantically identical to the caller supplying no observation at all.
#:
#: Emphatically not ``not_met``, and emphatically not a reason to go and look at
#: the repository now — a filesystem read taken today is not a statement about a
#: boundary that passed months ago.
REASON_FINAL_STATE_NOT_RECORDED = "final_state_not_recorded"

CRITERION_REASONS: Tuple[str, ...] = (
    REASON_TURN_CHANGE_EVALUATED,
    REASON_INHERITED_CHANGE_NOT_CURRENT,
    REASON_MANUAL_AUTHORITY,
    REASON_UNSUPPORTED_PREDICATE,
    REASON_FINAL_STATE_OBSERVED,
    REASON_FINAL_STATE_PATH_UNAVAILABLE,
    REASON_FINAL_STATE_UNAVAILABLE,
    REASON_FINAL_STATE_NOT_RECORDED,
)

#: Which reasons may accompany which result. ``met`` and ``not_met`` are only
#: ever a bound machine judgement — PR7's for a turn-change criterion, PR14's
#: observation for a state one. Nothing else in this version can decide a
#: criterion either way, and the three final-state limitation reasons are
#: ``unverified``-only by construction.
REASONS_FOR_RESULT: Dict[str, Tuple[str, ...]] = {
    RESULT_MET: (REASON_TURN_CHANGE_EVALUATED, REASON_FINAL_STATE_OBSERVED),
    RESULT_NOT_MET: (REASON_TURN_CHANGE_EVALUATED, REASON_FINAL_STATE_OBSERVED),
    RESULT_UNVERIFIED: (
        REASON_TURN_CHANGE_EVALUATED,
        REASON_INHERITED_CHANGE_NOT_CURRENT,
        REASON_MANUAL_AUTHORITY,
        REASON_UNSUPPORTED_PREDICATE,
        REASON_FINAL_STATE_PATH_UNAVAILABLE,
        REASON_FINAL_STATE_UNAVAILABLE,
        REASON_FINAL_STATE_NOT_RECORDED,
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

#: **M2K PR18.** A stored final-state row disagrees with the invariants that make
#: it meaningful, and the disagreement is *structural* rather than observational:
#: it names another task or turn, its ``path_count`` does not match its children,
#: it answers a path twice, an ``unavailable`` observation carries paths, or it
#: does not hash to the fingerprint it carries.
#:
#: The distinction from :data:`REASON_FINAL_STATE_UNAVAILABLE` is the whole
#: point. *We could not look safely* is an observation Cofferdam made and stands
#: behind, and it maps a criterion to ``unverified``. *This row is not what the
#: service writes* means the evidence has been edited outside the service or is
#: corrupt, and laundering that into an ordinary ``unverified`` would file
#: tampering as a routine limitation. Fails closed at the set, and repairs
#: nothing — a read that fixed a row would destroy the proof something wrote it.
REASON_FINAL_STATE_INCONSISTENT = "final_state_inconsistent"

#: The stored observation was produced by an observer version whose semantics
#: this binder does not know. A future ``FINAL_STATE_OBSERVER_VERSION`` 2 may
#: observe the index rather than the worktree, follow symlinks, or use a
#: different kind vocabulary, and reading its rows as though they meant version
#: 1's thing because the columns look familiar is exactly the silent
#: reinterpretation this layer exists to prevent.
REASON_UNSUPPORTED_OBSERVER = "unsupported_final_state_observer_version"

#: The stored observation's :attr:`lineage_fingerprint` — PR11's resolved-active
#: identity **at capture time** — disagrees with the active set resolved now.
#:
#: PR14 chose which paths to look at from the active criteria, so a mismatch
#: means the observation's declared target scope belongs to a different
#: requirement set than the one being assessed. Its path rows may still *look*
#: usable and some may even name the right paths; consuming them would be reading
#: an answer to another question. Fails closed at the set rather than downgrading
#: one criterion to ``unverified``, because a scope-identity disagreement is not
#: a fact about any single criterion.
REASON_FINAL_STATE_LINEAGE_MISMATCH = "final_state_lineage_mismatch"

#: An active state criterion's exact path is **missing** from an observation that
#: claims a recorded target scope.
#:
#: PR14 derives its targets from the same active set and gives every target an
#: explicit child row — an unobservable path is stored as ``unavailable`` with a
#: reason, never omitted. So absence of a row is not absence of the file; it is
#: an observation that does not describe the scope it claims. The one reading it
#: must never be ``absent``, and it is too structural to be per-path
#: ``unverified``: the row's own account of itself is wrong.
REASON_FINAL_STATE_PATH_MISSING = "final_state_path_missing"

SET_REASONS: Tuple[str, ...] = (
    REASON_LINEAGE_UNAVAILABLE,
    REASON_TURN_NOT_CLOSED,
    REASON_EVALUATION_NOT_RECORDED,
    REASON_EVALUATION_INCONSISTENT,
    REASON_UNSUPPORTED_EVALUATOR,
    REASON_FINAL_STATE_INCONSISTENT,
    REASON_UNSUPPORTED_OBSERVER,
    REASON_FINAL_STATE_LINEAGE_MISMATCH,
    REASON_FINAL_STATE_PATH_MISSING,
)

#: Evaluator semantics this version knows how to bind. Explicitly a set rather
#: than ``<= EVALUATOR_VERSION``: an old binder must not assume it understands a
#: newer evaluator, and this is the seat where that judgement is recorded.
SUPPORTED_EVALUATOR_VERSIONS: Tuple[int, ...] = (EVALUATOR_VERSION,)

#: Final-state observation semantics this version knows how to bind. Enumerated
#: for the same reason and with the same strictness as the evaluator set above:
#: not every future observer version means what version 1 means, and an unknown
#: one is refused rather than assumed compatible because its columns parse.
SUPPORTED_OBSERVER_VERSIONS: Tuple[int, ...] = (FINAL_STATE_OBSERVER_VERSION,)

#: Change predicates. Each asks what happened during one turn, which is why each
#: is answerable only at its own turn.
CHANGE_PREDICATES: Tuple[str, ...] = (
    PREDICATE_PATH_CHANGED,
    PREDICATE_PATH_OPERATION,
    PREDICATE_RENAME,
)

#: State predicates. Each asks what the project **is** at a boundary, which is
#: why each is answerable at every target turn from that turn's own observation.
STATE_PREDICATES: Tuple[str, ...] = (
    PREDICATE_PATH_EXISTS,
    PREDICATE_PATH_ABSENT,
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

    Which fingerprint it is follows the domain, and there is exactly one of each:
    PR7's ``evaluation_fingerprint`` for :data:`DOMAIN_TURN_CHANGE`, and PR14's
    ``observation_fingerprint`` — **the stored one, verified, never a second copy
    minted here** — for :data:`DOMAIN_FINAL_STATE`.

    ``path_state`` and ``path_kind`` are set exactly for a state criterion whose
    path was found in the observation, and record what that observation said
    about this path. Audit detail on the input, not a second evidence record:
    nothing is persisted, and PR14's row remains the only place a path fact
    lives. ``path_kind`` never changes a result — ``path_exists`` is satisfied by
    a file, a directory, a symlink (broken or not) or anything else, because the
    question is whether an object is there.
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
    #: The PR7 ``evaluation_fingerprint`` this result was bound from when the
    #: domain is :data:`DOMAIN_TURN_CHANGE`, and PR14's stored
    #: ``observation_fingerprint`` when it is :data:`DOMAIN_FINAL_STATE`.
    evidence_fingerprint: Optional[str] = None
    #: What the observation said about this criterion's path, for a state
    #: criterion that found one. Never authority on its own; the result above is.
    path_state: Optional[str] = None
    path_kind: Optional[str] = None
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

    ``graph`` is PR11's immutable input graph and ``resolved`` is what PR11's own
    resolver — the same one, never re-implemented — made of it inside the same
    snapshot. Both are here because the store must resolve to know *which
    evidence the active set actually needs*, and resolving a second time outside
    the snapshot would reintroduce the split read this shape exists to prevent.

    ``evaluation`` is the target turn's stored PR7 record or ``None``.
    ``final_state`` is the target turn's stored PR14 observation, or ``None``
    when no active criterion asked for one — which is a different thing from
    :data:`~.finalstate.OBSERVATION_LEGACY_UNKNOWN`, and the binder treats them
    identically anyway because both mean *nothing may be assumed*.
    ``turn_closed`` is whether the target turn is a completed boundary.
    """

    graph: object
    evaluation: object = None
    turn_closed: bool = False
    resolved: object = None
    final_state: object = None


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
    turn is a different fact*, and the domain is in because a ``final_state``
    answer of ``met`` must never hash equal to a ``turn_change`` one. The
    evidence fingerprint is in because a result that cannot say which judgement
    it came from is not auditable — for a state criterion that is PR14's stored
    ``observation_fingerprint``, which is what makes the exact immutable
    observation that produced the answer recoverable from the answer.

    ``CURRENT_ASSESSMENT_VERSION`` is first among the bound fields and it moved to
    2 here, so no V2 answer can collide with the V1 answer to the same question.

    Deliberately **not** in it: any clock, the PR7 ``evaluation_id`` or PR14's
    ``observation_id`` (minted row handles carrying randomness — the *fingerprint*
    is a judgement's identity), ``recorded_at``, database rowids, absolute host
    paths, and provider or session identifiers.

    ``path_state`` and ``path_kind`` are **not** bound either, and that is
    deliberate rather than an oversight: they are already inside the observation
    fingerprint that *is* bound, and a state criterion's result plus that
    fingerprint pin the path fact exactly. Hashing them again would add nothing
    and would make the answer's identity depend on how much audit detail this
    build happened to carry alongside it.
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
    """Active criteria that must have a PR7 answer at the target turn.

    The **only** thing that makes an evaluation required. Not "the target turn
    exists", not "the task has criteria", not "some criterion is an evidence
    one": a criterion needs a turn-change judgement exactly when it *is* a
    turn-change question asked of this turn. An inherited change criterion is not
    one — it is answered ``unverified`` from its origin turn alone — and a state
    criterion is not one either, whatever turn it came from.
    """
    return tuple(
        entry.criterion_id
        for entry in resolved.active
        if entry.source_turn_number == resolved.target_turn_number
        and entry.criterion.kind == KIND_EVIDENCE
        and entry.criterion.predicate in CHANGE_PREDICATES
    )


def _state_paths(resolved) -> Tuple[str, ...]:
    """Paths the active state criteria need a final-state answer for.

    The mirror of :func:`_same_turn_change_ids`, and required for the same shape
    of reason: a final-state observation is consulted exactly when some active
    criterion asks a state question, at any source turn, and never merely because
    PR14 recorded one. Empty means the whole domain is irrelevant to this target
    and no observation is read at all.
    """
    ordered = []
    seen = set()
    for entry in resolved.active:
        criterion = entry.criterion
        if criterion.kind != KIND_EVIDENCE or criterion.predicate not in STATE_PREDICATES:
            continue
        if criterion.path and criterion.path not in seen:
            seen.add(criterion.path)
            ordered.append(criterion.path)
    return tuple(ordered)


def _final_state_defect(observation, resolved, required_paths) -> Optional[str]:
    """Why a stored final-state observation may not be used at all, or ``None``.

    **Structural corruption only.** Every check here asks *is this row what the
    service writes* and none asks *what did it find*. An observation that
    honestly reports it could not look is not a defect and does not appear here;
    it is a semantic limitation and each affected criterion carries it as
    ``unverified``. Keeping the two apart is what stops tampered evidence from
    arriving dressed as an ordinary observational gap.

    Checked, in order, cheapest identity first:

    #. **observer version** — refused before anything else is read, because every
       later check interprets the fields under version 1's semantics and doing
       that to a version 2 row is the reinterpretation being guarded against;
    #. **task and turn identity** — the row must describe this target;
    #. **shape** — ``path_count`` matches the children, no path is answered
       twice, no ordinal is reused, and an ``unavailable`` observation carries no
       paths. PR15's doctrine applies: the DDL forbids some of these and not all,
       so they are checked rather than assumed;
    #. **lineage agreement** — the scope this observation says it was taken for
       must be the active set being assessed;
    #. **the fingerprint** — recomputed from the stored fields with PR14's own
       function. A stored hash nobody recomputes is not authority, it is a string;
       this is what makes raw-SQL edits to a path state, a kind or a reason
       detectable at all;
    #. **expected paths** — every active state criterion's path must have a row
       in an observation claiming a recorded scope.

    Pure, total, and repairs nothing.
    """
    if int(observation.observer_version or 0) not in SUPPORTED_OBSERVER_VERSIONS:
        return REASON_UNSUPPORTED_OBSERVER
    if observation.task_id != resolved.task_id:
        return REASON_FINAL_STATE_INCONSISTENT
    if int(observation.turn_number) != int(resolved.target_turn_number):
        return REASON_FINAL_STATE_INCONSISTENT

    paths = observation.paths
    if int(observation.path_count) != len(paths):
        return REASON_FINAL_STATE_INCONSISTENT
    if len({item.path for item in paths}) != len(paths):
        return REASON_FINAL_STATE_INCONSISTENT
    if len({item.ordinal for item in paths}) != len(paths):
        return REASON_FINAL_STATE_INCONSISTENT
    if observation.state == OBSERVATION_UNAVAILABLE and paths:
        # No defensible target list was known, so children imply a scope that
        # never existed.
        return REASON_FINAL_STATE_INCONSISTENT

    # Scope before integrity, deliberately. A row whose declared scope is not
    # this active set is refused whether the disagreement came from a raw edit
    # or from an honest observation of a different requirement set, and naming
    # *that* is a more useful diagnosis than the generic corruption reason the
    # fingerprint check below would also produce for the edited case. Both fail
    # the set closed, so the ordering changes what is reported and never what is
    # consumed.
    recorded_scope = observation.state in (OBSERVATION_COMPLETE, OBSERVATION_INCOMPLETE)
    if recorded_scope:
        # PR14 sets this from the resolved active set whenever it had one, so a
        # complete or incomplete observation without it is as malformed as one
        # that disagrees.
        if observation.lineage_fingerprint != resolved.fingerprint:
            return REASON_FINAL_STATE_LINEAGE_MISMATCH
    elif (
        observation.lineage_fingerprint is not None
        and observation.lineage_fingerprint != resolved.fingerprint
    ):
        return REASON_FINAL_STATE_LINEAGE_MISMATCH

    if not verify_final_state_fingerprint(observation):
        return REASON_FINAL_STATE_INCONSISTENT

    if recorded_scope:
        found = {item.path for item in paths}
        if any(path not in found for path in required_paths):
            return REASON_FINAL_STATE_PATH_MISSING
    return None


def _state_answer(criterion, observation) -> Tuple[str, str, str, Optional[str], Optional[str]]:
    """One state criterion's ``(domain, result, reason, path_state, path_kind)``.

    The whole of ``path_exists`` and ``path_absent``, and it is this short because
    the semantics are:

    * ``present`` — an object is there. ``path_exists`` is ``met``,
      ``path_absent`` is ``not_met``. **Any** kind counts: a file, a directory, a
      symlink, a symlink whose target does not exist, a socket. A broken symlink
      is a present symlink object and PR14 recorded it without following it, so
      nothing here follows it either;
    * ``absent`` — the safe lookup completed and found nothing. ``path_exists``
      is ``not_met``, ``path_absent`` is ``met``;
    * ``unavailable``, or no row at all — ``unverified``. Never ``not_met``: *we
      could not look* is not *it is not there*.

    ``observation`` is ``None`` for a turn with nothing recorded and for a caller
    that supplied nothing. Both mean the same and both refuse to guess.
    """
    if observation is None or observation.state not in STORED_OBSERVATION_STATES:
        return (
            DOMAIN_NOT_APPLICABLE,
            RESULT_UNVERIFIED,
            REASON_FINAL_STATE_NOT_RECORDED,
            None,
            None,
        )
    if observation.state == OBSERVATION_UNAVAILABLE:
        return (
            DOMAIN_NOT_APPLICABLE,
            RESULT_UNVERIFIED,
            REASON_FINAL_STATE_UNAVAILABLE,
            None,
            None,
        )
    # `incomplete` is not a blanket refusal. PR15 decided per-path authority
    # survives a partial observation: a path safely observed as present is a
    # fact whatever happened to some other path, and discarding it would throw
    # away real evidence over an unrelated wall.
    found = None
    for item in observation.paths:
        if item.path == criterion.path:
            found = item
            break
    if found is None:  # pragma: no cover - _final_state_defect refuses these first
        return (
            DOMAIN_NOT_APPLICABLE,
            RESULT_UNVERIFIED,
            REASON_FINAL_STATE_NOT_RECORDED,
            None,
            None,
        )
    if found.state == PATH_UNAVAILABLE:
        return (
            DOMAIN_NOT_APPLICABLE,
            RESULT_UNVERIFIED,
            REASON_FINAL_STATE_PATH_UNAVAILABLE,
            found.state,
            found.kind,
        )
    if criterion.predicate == PREDICATE_PATH_EXISTS:
        result = RESULT_MET if found.state == PATH_PRESENT else RESULT_NOT_MET
    else:
        result = RESULT_MET if found.state == PATH_ABSENT else RESULT_NOT_MET
    return (
        DOMAIN_FINAL_STATE,
        result,
        REASON_FINAL_STATE_OBSERVED,
        found.state,
        found.kind,
    )


def bind(
    resolved, evaluation, *, turn_closed: bool, final_state=None
) -> CurrentAssessment:
    """Current status for every active criterion at the target turn.

    ``resolved`` is PR11's :class:`~.lineage.ResolvedActiveCriteria` or a
    :class:`~.lineage.LineageUnavailable`; ``evaluation`` is the target turn's
    stored :class:`~.evaluation.EvaluationRecord` or ``None``; ``final_state`` is
    the target turn's stored :class:`~.finalstate.FinalStateObservation` or
    ``None``; ``turn_closed`` says whether the target turn is a completed
    boundary.

    **Each input is required only by the criteria that consume it**, which is the
    rule PR18 had to get right for the two domains to stay independent. A target
    whose active set is one ``path_exists`` needs no PR7 record. A target of
    change and manual criteria needs no observation. Neither missing input is
    allowed to fail criteria of the other domain, and the tests pin all four
    combinations because a coupling here would be invisible until the day one
    pipeline stage lagged.

    The order of refusals is the doctrine, and each is a different kind of
    silence that must not be confused with the others:

    #. **the turn is not a completed boundary** — a current assessment of a turn
       still running describes a moment that has not happened;
    #. **the lineage is unavailable** — there is no defensible requirement set,
       and the latest snapshot is not a substitute for one;
    #. **an evaluation is required and missing** — operational, and reported as
       such rather than as a set of ``unverified`` criteria;
    #. **an evaluation is present but inconsistent, or from an evaluator this
       version does not understand** — fails closed;
    #. **a required observation is structurally corrupt** — wrong target, wrong
       observer version, a scope that disagrees with the active set, a missing
       expected path, or fields that do not hash to the fingerprint they carry.
       Fails closed, and never as a per-criterion ``unverified``.

    A *missing* or *legitimately unavailable* observation is deliberately absent
    from that list. It is not a set-level refusal, because it is a real limit on
    what one domain could see rather than a reason to distrust the record: the
    state criteria say ``unverified`` and every change and manual criterion in
    the same set is answered exactly as it would have been.

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

    # The same conditionality, for the other domain. No observation is looked at
    # — not even to validate it — unless some active criterion asks a state
    # question, so an unrelated corrupt row cannot become an authority dependency
    # of a set that would never have read it.
    state_paths = _state_paths(resolved)
    observation = final_state if state_paths else None
    if state_paths and observation is not None and observation.recorded:
        defect = _final_state_defect(observation, resolved, state_paths)
        if defect is not None:
            return _unavailable(
                resolved.task_id, resolved.target_turn_number, defect
            )

    assessments = []
    for entry in resolved.active:
        criterion = entry.criterion
        target = int(resolved.target_turn_number)
        path_state = path_kind = None
        if criterion.kind == KIND_MANUAL:
            domain, result, reason = (
                DOMAIN_NOT_APPLICABLE,
                RESULT_UNVERIFIED,
                REASON_MANUAL_AUTHORITY,
            )
            evidence = None
        elif criterion.predicate in STATE_PREDICATES:
            # The target turn's observation, whether this criterion originated
            # here or five turns ago. A state question is re-askable at every
            # boundary, so there is nothing to carry forward and nothing is.
            domain, result, reason, path_state, path_kind = _state_answer(
                criterion, observation
            )
            evidence = (
                observation.fingerprint if domain == DOMAIN_FINAL_STATE else None
            )
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
            # Exactly PR7's stored judgement, and final state does not enter
            # here even when it contradicts: "did this turn create foo.py" stays
            # decided by what the turn did, whatever became of the file after.
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
                path_state=path_state,
                path_kind=path_kind,
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
    "DOMAIN_FINAL_STATE",
    "DOMAIN_NOT_APPLICABLE",
    "DOMAIN_TURN_CHANGE",
    "EVIDENCE_DOMAINS",
    "FINGERPRINT_CHARS",
    "REASONS_FOR_RESULT",
    "REASON_EVALUATION_INCONSISTENT",
    "REASON_EVALUATION_NOT_RECORDED",
    "REASON_FINAL_STATE_INCONSISTENT",
    "REASON_FINAL_STATE_LINEAGE_MISMATCH",
    "REASON_FINAL_STATE_NOT_RECORDED",
    "REASON_FINAL_STATE_OBSERVED",
    "REASON_FINAL_STATE_PATH_MISSING",
    "REASON_FINAL_STATE_PATH_UNAVAILABLE",
    "REASON_FINAL_STATE_UNAVAILABLE",
    "REASON_INHERITED_CHANGE_NOT_CURRENT",
    "REASON_LINEAGE_UNAVAILABLE",
    "REASON_MANUAL_AUTHORITY",
    "REASON_TURN_CHANGE_EVALUATED",
    "REASON_TURN_NOT_CLOSED",
    "REASON_UNSUPPORTED_EVALUATOR",
    "REASON_UNSUPPORTED_OBSERVER",
    "REASON_UNSUPPORTED_PREDICATE",
    "SET_REASONS",
    "STATE_PREDICATES",
    "SUPPORTED_EVALUATOR_VERSIONS",
    "SUPPORTED_OBSERVER_VERSIONS",
    "TAG_FINGERPRINT",
    "AssessmentInputs",
    "CriterionAssessment",
    "CurrentAssessment",
    "bind",
    "criterion_assessment_fingerprint",
    "current_assessment_fingerprint",
]
