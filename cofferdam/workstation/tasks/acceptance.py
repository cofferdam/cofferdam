"""Whether one turn's requirements were established. Two dimensions, never one.

M2K PR21, and the first aggregate this milestone has allowed itself to build.
PR9 settled what an acceptance answer may mean before one could be produced; PR19
reconciled that doctrine with the concrete model; PR20 stabilised the envelope it
consumes. This folds the per-criterion answers into a target-turn result and
stops there.

Two dimensions, and collapsing them is the failure
--------------------------------------------------

**Availability** asks whether an acceptance question can be answered at all, and
**outcome** exists only when it can. A single enum cannot carry both: the moment
*we could not determine your requirements* has to share a field with *one of your
requirements was not met*, one of them starts being read as the other. So an
unanswerable turn has no outcome — not a null one, not a neutral one, and
emphatically not ``incomplete``.

``incomplete`` is a statement about a **known** requirement set containing at
least one criterion nothing could establish. Applying it to a set that was never
determined would report evidence uncertainty where the real problem is that
Cofferdam does not know what was required.

The fold, and why the order is the point
----------------------------------------

For a resolved, non-empty active set:

#. any ``not_met`` → ``not_met``;
#. otherwise any ``unverified`` → ``incomplete``;
#. otherwise → ``met``.

Known failure dominates uncertainty: one criterion demonstrably unmet already
settles that the turn's recorded requirements were not all established, however
many others could not be checked. Uncertainty blocks ``met``, and nothing else
reaches it. Monotonic and conservative in the only direction that matters — it
cannot manufacture good news.

**Domain-agnostic by construction.** The fold reads ``result`` and nothing else.
It does not know that PR7 decided one criterion and PR14's observation decided
another, does not weight them, and does not rank a final-state answer above a
turn-change one. All of that was resolved below and is already committed to by
the envelope's fingerprint. It reads ``kind`` for exactly one purpose — deciding
whether a person is needed — and reads criterion *reasons* for none at all: those
are audit provenance, and letting them steer acceptance would put a second,
unreviewed rule set underneath this one.

Known zero and unknown population are different facts
-----------------------------------------------------

A resolved set of size zero means *no structured criteria were declared* — one
meaning, established by PR19 against the resolver and the write path. Its counts
are genuinely zero and it genuinely needs no human. An **unavailable** envelope
means the population could not be determined at all, so its counts are not zero,
they are *unknown*, and whether a person is needed is unknown with them.

Reporting the second as four zeros would state an observation nobody made, and it
is the mistake this module is most able to make silently. Hence
:class:`CriterionCounts` is optional and :attr:`AcceptanceAggregate.requires_human`
is a tri-state, and both are bound into the fingerprint so the two cases cannot
share an identity.

What this is not
----------------

**No task verdict.** This answers *acceptance at target turn N, over the criteria
active at N, using their current status at N*. There is no overall task result,
no merge readiness, no deployment readiness, no project quality, and no alias for
"the latest turn". Composing several target-turn answers into one is a separate
undecided question about which turn's requirements a task is judged against.

**Not lifecycle.** A completed turn may be ``not_met``; a failed one may have no
assessable acceptance. Acceptance never uses ``success``, ``failed`` or
``passed`` — those words belong to lifecycle, and a reader who saw one could not
tell which domain it came from.

**Nothing persisted**, no schema change, no cache and no recovery path. Every
input is immutable or deterministically derived from immutable rows, so the
answer re-derives identically forever and the fingerprint gives it audit identity
without a table.

**No named checks**, no runner, no command execution, and no public surface.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional, Tuple

from .binding import (
    ASSESSMENT_RESOLVED,
    ASSESSMENT_UNAVAILABLE,
    CURRENT_ASSESSMENT_VERSION,
    LINEAGE_REASONS,
    SET_REASONS,
)
from .criteria import CRITERION_KINDS, KIND_MANUAL
from .evaluation import RESULTS, RESULT_MET, RESULT_NOT_MET, RESULT_UNVERIFIED

# PR11 owns this name; PR20 is the reason it matters here. `cause` is populated
# for exactly this reason and no other, so validating that invariant means
# naming it — from its author, never from a copy.
from .lineage import REASON_PREDECESSOR_UNAVAILABLE

#: Bumped when the **meaning** of an acceptance aggregate changes: a different
#: fold, a different availability rule, a different notion of what a count or a
#: missing outcome asserts.
#:
#: Distinct from :data:`~.store.SCHEMA_VERSION` (table shape),
#: :data:`~.evidence.ASSEMBLER_VERSION`, :data:`~.evaluation.EVALUATOR_VERSION`,
#: :data:`~.criteria.CRITERIA_MODEL_VERSION`,
#: :data:`~.continuity.CONTINUITY_MODEL_VERSION`,
#: :data:`~.lineage.RESOLVER_VERSION`,
#: :data:`~.finalstate.FINAL_STATE_OBSERVER_VERSION` and
#: :data:`~.binding.CURRENT_ASSESSMENT_VERSION`. Eight things that move for eight
#: reasons; a reader must be able to tell which one did.
#:
#: This one owns exactly the mapping *CurrentAssessment V3 → target-turn
#: availability and outcome*. It does not own how any criterion was decided, and
#: it does **not** move when a new evidence domain or criterion family appears —
#: a future domain that produces the same criterion-level ``met`` / ``not_met`` /
#: ``unverified`` folds identically here, because the fold never reads domains.
AGGREGATOR_VERSION = 1

#: Envelope semantics this aggregator knows how to fold. Enumerated rather than
#: ``<= CURRENT_ASSESSMENT_VERSION``: a future V4 may mean something different by
#: the same field names, and folding it as though it meant V3's thing is the
#: silent reinterpretation every layer in this milestone refuses. A dataclass
#: whose shape happens to still fit is not evidence of compatible semantics.
SUPPORTED_ASSESSMENT_VERSIONS: Tuple[int, ...] = (CURRENT_ASSESSMENT_VERSION,)


# -- availability -------------------------------------------------------------

#: An acceptance question can be answered here: the active requirement set is
#: known and non-empty. An outcome accompanies this and only this.
AVAILABILITY_ASSESSABLE = "assessable"

#: No acceptance question can be answered. A closed reason says why, and there is
#: **no outcome** — not a null one and not a neutral one.
AVAILABILITY_NOT_ASSESSABLE = "not_assessable"

AVAILABILITIES: Tuple[str, ...] = (
    AVAILABILITY_ASSESSABLE,
    AVAILABILITY_NOT_ASSESSABLE,
)


# -- availability reasons -----------------------------------------------------

#: The active set resolved and is **empty**: no structured criteria were declared
#: anywhere in the resolved chain. PR19 established this has exactly one meaning,
#: because ``revise`` cannot reduce a set to zero and ``extend`` never removes.
#:
#: Never ``met``. Vacuous truth is not acceptance: "no requirements were stated"
#: and "every requirement was satisfied" are different sentences, and only the
#: second is a claim about somebody's work.
REASON_NO_STRUCTURED_CRITERIA = "no_structured_criteria"

#: The envelope handed in does not satisfy the V3 contract — a resolved set
#: carrying an unavailable reason, an unavailable one carrying criteria, a result
#: outside the closed vocabulary, a criterion answered twice.
#:
#: **This layer's own failure, not a translation of a lower one.** The service
#: cannot produce such an envelope; one that exists was hand-built or corrupted.
#: Refused rather than normalised, because quietly repairing an input would make
#: the aggregate agree with a record nothing else agrees with.
REASON_ASSESSMENT_INPUT_INVALID = "assessment_input_invalid"

#: The envelope was produced under assessment semantics this aggregator does not
#: know. See :data:`SUPPORTED_ASSESSMENT_VERSIONS`.
REASON_UNSUPPORTED_ASSESSMENT_VERSION = "unsupported_assessment_version"

#: The three this layer owns, for failures of **its own** input contract. Every
#: other reason it can report is passed through untouched from the envelope.
AGGREGATE_REASONS: Tuple[str, ...] = (
    REASON_NO_STRUCTURED_CRITERIA,
    REASON_ASSESSMENT_INPUT_INVALID,
    REASON_UNSUPPORTED_ASSESSMENT_VERSION,
)

#: Closed, and deliberately built as *this layer's three* plus **the envelope's
#: twenty-seven verbatim**.
#:
#: There is no translation table and there will not be one. A parallel vocabulary
#: would be a second closed set to keep in step with the first, and this
#: repository already carries the untranslated ``ContinuityInvalid`` →
#: ``ContinuityUnrecorded`` debt as the standing example of what that costs. PR20
#: went to some trouble to stop `continuity_not_declared` being flattened into
#: `lineage_unavailable`; re-flattening it one layer up would undo exactly that.
AVAILABILITY_REASONS: Tuple[str, ...] = AGGREGATE_REASONS + SET_REASONS


# -- outcome ------------------------------------------------------------------

#: Every active criterion is established as met **by the current assessment
#: model**. Not "the task succeeded", not "the worker succeeded", not "the user's
#: intent was captured", and not a promise a later turn cannot regress it.
OUTCOME_MET = RESULT_MET

#: At least one active criterion is demonstrably unmet. An **acceptance** result
#: and explicitly not a lifecycle failure.
OUTCOME_NOT_MET = RESULT_NOT_MET

#: The set is known, nothing is demonstrably unmet, and at least one criterion
#: could not be established. An evidence limitation, never a finding about the
#: work.
OUTCOME_INCOMPLETE = "incomplete"

OUTCOMES: Tuple[str, ...] = (OUTCOME_MET, OUTCOME_NOT_MET, OUTCOME_INCOMPLETE)


# -- shapes -------------------------------------------------------------------


@dataclass(frozen=True)
class CriterionCounts:
    """How many active criteria landed where. Present only when actually counted.

    The type exists so that *counted zero* and *not counted* cannot be written
    the same way. An unavailable envelope has no population to count, and
    reporting four zeros for it would assert an observation nobody made —
    indistinguishable, at a glance, from a genuinely empty requirement set.
    """

    total: int
    met: int
    not_met: int
    unverified: int


@dataclass(frozen=True)
class AcceptanceAggregate:
    """Acceptance at one target turn, or why there is none.

    One shape for both, so a reader never branches on ``None`` before it can ask
    what happened. Three fields are deliberately optional and each ``None`` means
    *unknown*, never *zero* and never *no*:

    * ``outcome`` — absent exactly when not assessable;
    * ``counts`` — absent when the population could not be determined;
    * ``requires_human`` — a **tri-state**, absent for the same reason.

    Deliberately **not** published. No ``to_dict``, no route, no bridge Action,
    no PWA control, and PR8's assessment response is unchanged — a read surface
    is its own review, and a serializer written before anything needs one is how
    an internal shape becomes a contract by accident.
    """

    task_id: str
    target_turn_number: int
    aggregator_version: int
    #: The exact envelope this was folded from. Composition rather than
    #: re-derivation: it already commits to the lineage identity and to every
    #: criterion-level answer.
    assessment_fingerprint: str
    availability: str
    availability_reason: Optional[str] = None
    #: Passed through from the envelope, never translated. Present only for a
    #: lineage failure inherited from a predecessor.
    unavailable_cause: Optional[str] = None
    unavailable_at_turn_number: Optional[int] = None
    outcome: Optional[str] = None
    counts: Optional[CriterionCounts] = None
    requires_human: Optional[bool] = None
    fingerprint: str = ""

    @property
    def assessable(self) -> bool:
        return self.availability == AVAILABILITY_ASSESSABLE

    @property
    def population_known(self) -> bool:
        """Whether the active requirement set was determined at all.

        True for a resolved set of any size, **including an empty one**, and
        false whenever the envelope was unavailable. Distinct from
        :attr:`assessable`: a known-empty set is a population Cofferdam knows and
        an acceptance question it still cannot answer.
        """
        return self.counts is not None


# -- the fingerprint ----------------------------------------------------------

TAG_FINGERPRINT = b"cofferdam.acceptance.aggregate.v1"
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


def acceptance_fingerprint(
    *,
    task_id: str,
    target_turn_number: int,
    assessment_fingerprint: str,
    availability: str,
    availability_reason: Optional[str],
    unavailable_cause: Optional[str],
    unavailable_at_turn_number: Optional[int],
    outcome: Optional[str],
    counts: Optional[CriterionCounts],
    requires_human: Optional[bool],
) -> str:
    """A stable hash of one acceptance answer and of exactly what it was folded from.

    **Compositional, not re-derived.** The single most important field is the
    consumed :attr:`~.binding.CurrentAssessment.fingerprint`, which already
    commits to the assessment version, the lineage identity and every
    criterion-level answer. Re-binding evidence bundles, evaluation records or
    final-state observations here would duplicate that commitment and couple this
    layer to ones it is not allowed to read — and two envelopes that folded to the
    same counts from different evidence would wrongly share an identity.

    **``counts`` binds a presence flag before its numbers**, so *counted zero* and
    *not counted* can never collide. The same care applies to ``requires_human``,
    where ``None`` and ``False`` are different answers.

    Deliberately **not** in it: any clock, database rowids, absolute host paths,
    provider or session identifiers, and every low-level observation.
    """
    digest = _Fingerprint()
    digest.field("cofferdam.acceptance.aggregate")
    digest.field(AGGREGATOR_VERSION)
    digest.field(task_id)
    digest.field(target_turn_number)
    digest.field(assessment_fingerprint)
    digest.field(availability)
    digest.field(availability_reason)
    digest.field(unavailable_cause)
    digest.field(unavailable_at_turn_number)
    digest.field(outcome)
    # The flag first, and then the numbers or four explicit absences. Belt and
    # braces on the distinction this module exists to keep: an unknown population
    # must not hash like a counted-empty one.
    digest.field(counts is not None)
    digest.field(None if counts is None else counts.total)
    digest.field(None if counts is None else counts.met)
    digest.field(None if counts is None else counts.not_met)
    digest.field(None if counts is None else counts.unverified)
    digest.field(requires_human)
    return digest.hexdigest()


# -- the aggregate ------------------------------------------------------------
#
# Pure. No SQLite, no store, no service, no filesystem, no Git, no subprocess, no
# network, no provider, no evaluator, no observer, no resolver, no clock and no
# mutation. Called twice with the same envelope it returns the same answer, in
# this process and any other, before and after the repository it describes has
# been deleted.


def _build(
    assessment,
    availability: str,
    *,
    reason: Optional[str] = None,
    cause: Optional[str] = None,
    at_turn: Optional[int] = None,
    outcome: Optional[str] = None,
    counts: Optional[CriterionCounts] = None,
    requires_human: Optional[bool] = None,
) -> AcceptanceAggregate:
    task_id = getattr(assessment, "task_id", "")
    target = int(getattr(assessment, "target_turn_number", 0) or 0)
    consumed = getattr(assessment, "fingerprint", "") or ""
    return AcceptanceAggregate(
        task_id=task_id,
        target_turn_number=target,
        aggregator_version=AGGREGATOR_VERSION,
        assessment_fingerprint=consumed,
        availability=availability,
        availability_reason=reason,
        unavailable_cause=cause,
        unavailable_at_turn_number=at_turn,
        outcome=outcome,
        counts=counts,
        requires_human=requires_human,
        fingerprint=acceptance_fingerprint(
            task_id=task_id,
            target_turn_number=target,
            assessment_fingerprint=consumed,
            availability=availability,
            availability_reason=reason,
            unavailable_cause=cause,
            unavailable_at_turn_number=at_turn,
            outcome=outcome,
            counts=counts,
            requires_human=requires_human,
        ),
    )


def _contract_violation(assessment) -> bool:
    """Whether the envelope breaks the V3 contract it claims to satisfy.

    The service cannot produce any of these, so an envelope that does was
    hand-built or corrupted. Checked rather than trusted for the reason PR15
    established one layer down: a shape that only a convention prevents is a
    shape something will eventually produce.

    Checked: the state is one of the two; a resolved set carries no unavailable
    fields; an unavailable set carries a reason from the closed vocabulary and no
    criterion assessments; a cause appears only where PR20 puts one, and only
    from the lineage vocabulary; every criterion has a known kind and a known
    result; and no criterion is answered twice.

    Nothing is repaired. Normalising an input here would make this layer agree
    with a record no other layer agrees with.
    """
    state = getattr(assessment, "state", None)
    if state not in (ASSESSMENT_RESOLVED, ASSESSMENT_UNAVAILABLE):
        return True

    reason = getattr(assessment, "unavailable_reason", None)
    cause = getattr(assessment, "unavailable_cause", None)
    at_turn = getattr(assessment, "unavailable_at_turn_number", None)
    items = tuple(getattr(assessment, "assessments", ()) or ())

    if state == ASSESSMENT_RESOLVED:
        if reason is not None or cause is not None or at_turn is not None:
            return True
    else:
        if reason not in SET_REASONS:
            # Covers a missing reason and one outside the closed set alike.
            return True
        if items:
            # An unavailable envelope carries no criterion assessments: a partial
            # set is one a caller would use.
            return True
        if cause is not None and (
            reason != REASON_PREDECESSOR_UNAVAILABLE or cause not in LINEAGE_REASONS
        ):
            return True
        if at_turn is not None and reason not in LINEAGE_REASONS:
            return True

    seen = set()
    for item in items:
        if getattr(item, "kind", None) not in CRITERION_KINDS:
            return True
        if getattr(item, "result", None) not in RESULTS:
            return True
        identity = getattr(item, "criterion_id", None)
        if not identity or identity in seen:
            return True
        seen.add(identity)
    return False


def aggregate(assessment) -> AcceptanceAggregate:
    """Acceptance at one target turn, folded from one current assessment.

    ``assessment`` is a :class:`~.binding.CurrentAssessment`. It is the **only**
    semantic input: this function does not reach back to PR7's evaluation, PR14's
    observations, the continuity rows, the resolver, Git or the filesystem, and
    could not — none of them is in scope here, which is what makes the purity
    claim checkable rather than promised.

    The order of answers is the doctrine:

    #. **the envelope's semantics are unknown to this version** — refused, rather
       than folded as though a V4 meant what V3 means;
    #. **the envelope breaks its own contract** — refused, and nothing repaired;
    #. **the envelope is unavailable** — not assessable, its reason, cause and
       turn preserved verbatim, and counts and ``requires_human`` **unknown**
       because the population was never determined;
    #. **the set resolved and is empty** — not assessable for
       ``no_structured_criteria``, with genuinely zero counts and no human
       needed, because here the population *is* known;
    #. **otherwise** — assessable, counted, and folded.

    Never raises for an envelope it dislikes, and never returns ``None``.
    """
    version = getattr(assessment, "assessment_version", None)
    if version not in SUPPORTED_ASSESSMENT_VERSIONS:
        return _build(
            assessment,
            AVAILABILITY_NOT_ASSESSABLE,
            reason=REASON_UNSUPPORTED_ASSESSMENT_VERSION,
        )
    if _contract_violation(assessment):
        return _build(
            assessment,
            AVAILABILITY_NOT_ASSESSABLE,
            reason=REASON_ASSESSMENT_INPUT_INVALID,
        )

    if assessment.state == ASSESSMENT_UNAVAILABLE:
        # The population is unknown, so counts and `requires_human` stay None.
        # Four zeros here would be an observation nobody made.
        return _build(
            assessment,
            AVAILABILITY_NOT_ASSESSABLE,
            reason=assessment.unavailable_reason,
            cause=assessment.unavailable_cause,
            at_turn=assessment.unavailable_at_turn_number,
        )

    items = tuple(assessment.assessments)
    results = [item.result for item in items]
    counts = CriterionCounts(
        total=len(results),
        met=results.count(RESULT_MET),
        not_met=results.count(RESULT_NOT_MET),
        unverified=results.count(RESULT_UNVERIFIED),
    )
    # Derived from criterion **kind**, never from uncertainty. An inherited change
    # criterion is `unverified` and no person can resolve it; saying otherwise
    # would send somebody to look at something they cannot answer.
    requires_human = any(item.kind == KIND_MANUAL for item in items)

    if not items:
        # Known zero: a real population of size zero, not an unknown one.
        return _build(
            assessment,
            AVAILABILITY_NOT_ASSESSABLE,
            reason=REASON_NO_STRUCTURED_CRITERIA,
            counts=counts,
            requires_human=requires_human,
        )

    if counts.not_met:
        outcome = OUTCOME_NOT_MET
    elif counts.unverified:
        outcome = OUTCOME_INCOMPLETE
    else:
        outcome = OUTCOME_MET
    return _build(
        assessment,
        AVAILABILITY_ASSESSABLE,
        outcome=outcome,
        counts=counts,
        requires_human=requires_human,
    )


__all__ = [
    "AGGREGATE_REASONS",
    "AGGREGATOR_VERSION",
    "AVAILABILITIES",
    "AVAILABILITY_ASSESSABLE",
    "AVAILABILITY_NOT_ASSESSABLE",
    "AVAILABILITY_REASONS",
    "FINGERPRINT_CHARS",
    "OUTCOMES",
    "OUTCOME_INCOMPLETE",
    "OUTCOME_MET",
    "OUTCOME_NOT_MET",
    "REASON_ASSESSMENT_INPUT_INVALID",
    "REASON_NO_STRUCTURED_CRITERIA",
    "REASON_UNSUPPORTED_ASSESSMENT_VERSION",
    "SUPPORTED_ASSESSMENT_VERSIONS",
    "TAG_FINGERPRINT",
    "AcceptanceAggregate",
    "CriterionCounts",
    "acceptance_fingerprint",
    "aggregate",
]
