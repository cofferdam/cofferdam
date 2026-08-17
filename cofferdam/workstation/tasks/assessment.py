"""The published view of what a turn was asked to do and how it was answered.

M2K PR6 froze the criteria. PR7 froze the deterministic evaluation. Both have
been durable and completely invisible: there is no route, no panel, and no way to
see either without opening the database. This module is the published shape of
those two facts, and **nothing else happens here**.

It computes no judgement. It runs no evaluator. It reads no row — the caller
hands it values that were already loaded. Given the same snapshot and record it
returns the same dictionary, in this process and any other, before and after the
repository it describes has been deleted.

Why one view rather than two
----------------------------

Criteria and evaluation are one turn-qualified audit question — *what was
required, and what did the machine make of it* — and a reader needs both or
neither. Two routes would let a client read criteria from one moment and an
evaluation from another and draw a conclusion about the pair, which is precisely
the inconsistency :func:`~.store.TaskStore.turn_assessment_inputs` holds one lock
to prevent. It would also create two HTTP contracts that could drift while
describing one thing.

Serialization is a whitelist, and that is structural
----------------------------------------------------

Every published key is written out literally below. There is no ``asdict``, no
``vars``, no ``__dict__``, no loop over ``__dataclass_fields__`` anywhere in this
module. That is not stylistic: those idioms publish whatever a dataclass gains
next, so a future field added for an internal reason would appear in a client
response the day it was added, without anybody deciding to publish it. The
functions here also **refuse an object of the wrong type** rather than
duck-typing their way through it, so a dict cannot arrive dressed as a
:class:`~.criteria.CriteriaSnapshot`.

What is deliberately not in the response
----------------------------------------

**No aggregate.** No overall result, no pass, no fail, no success, no score, no
percentage, no ``all_met``, no confidence, no risk — and no code that could
compute one. A list of per-criterion results is not an aggregate, and turning it
into one is a doctrine decision nobody has taken.

**No evidence body.** The assessment names the bundle it was evaluated against by
``assembler_version`` and ``evidence_input_fingerprint`` and stops there. The
evidence route is the evidence surface; copying a bundle in here would create a
second published shape for facts that already have one.

**No claim relationships.** ``claim_conflict`` in particular is absent by design.
It is a disagreement between an adapter's record and the machine's, it is not a
reason a criterion went unmet, and putting it beside a result is exactly how a
reader would come to treat it as one. The evidence route publishes relationships;
a client that wants that context navigates there.

**No host detail.** No absolute path, no project root, no database location, no
provider or session identifier, no SQLite rowid.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .criteria import (
    CRITERIA_LEGACY_UNKNOWN,
    CRITERIA_NOT_PROVIDED,
    CRITERIA_PRESENT,
    AcceptanceCriterion,
    CriteriaSnapshot,
)
from .acceptance import AcceptanceAggregate
from .evaluation import CriterionResult, EvaluationRecord

#: Bumped when the published shape changes in a way a client could notice.
#: Separate from every other version in this package: the schema, the assembler,
#: the criteria model and the evaluator all move for their own reasons, and a
#: presentation change must not be mistaken for any of them.
ASSESSMENT_API_VERSION = 1


# -- how an evaluation can be absent, said out loud ---------------------------
#
# `evaluation: null` would collapse four different situations into one, and three
# of them are ordinary while the fourth wants somebody to look. A closed state
# makes a client say which one it is handling.

#: An EvaluationRecord exists for this turn and this evaluator version.
EVALUATION_RECORDED = "recorded"

#: The turn's criteria are ``legacy_unknown``, so there was never a question to
#: answer. Not a gap and not a problem: this turn predates criteria persistence.
EVALUATION_CRITERIA_LEGACY_UNKNOWN = "criteria_legacy_unknown"

#: The turn is still open. Evaluation is strictly post-close by design, so this
#: is the expected state for work in flight and is not an operational concern.
#: Deliberately not called "pending" — that word invites a client to poll and to
#: treat the eventual record as owed, and neither is this module's business.
EVALUATION_TURN_NOT_CLOSED = "turn_not_closed"

#: The turn is closed, has criteria, and has no record for this evaluator
#: version. **This one is worth noticing.** Ordinarily PR7's post-close pass or
#: its start-up recovery has already written one, so this means recovery has not
#: run yet, its persistence failed, or an invariant is broken.
#:
#: What it is emphatically not: a pass, a skip, or "evaluation was unnecessary".
#: It is also not an ``unverified`` criterion result — there is no result record
#: at all, which is a different statement from a result that says the evidence
#: could not decide.
EVALUATION_NOT_RECORDED = "not_recorded"

EVALUATION_STATES = (
    EVALUATION_RECORDED,
    EVALUATION_CRITERIA_LEGACY_UNKNOWN,
    EVALUATION_TURN_NOT_CLOSED,
    EVALUATION_NOT_RECORDED,
)


# -- the transport ceiling ----------------------------------------------------
#
# The same fail-closed discipline `projectcontext` uses on the read surface it
# owns, with a bound of its own rather than a shared constant: these are
# different payloads with different growth, and one number serving both would
# make a change to either a change to the other.
#
# An assessment is bounded before it starts — at most 32 criteria, each with a
# 512-character path, a 512-character destination and a 500-character
# description — so the realistic worst case is comfortably inside this. The check
# exists for the case where that reasoning stops being true, and it **fails
# closed**: a response that does not fit is refused rather than trimmed, because
# half an audit view is worse than an error that says so.

MAX_SERIALIZED_ASSESSMENT_BYTES = 128 * 1024


class AssessmentTooLarge(Exception):
    """A logically valid assessment that does not fit the response contract.

    Raised instead of truncating. Silently dropping criteria or results from an
    audit view would produce a response that looks complete and is not — the same
    failure the criteria bounds refuse at the other end of the pipeline.
    """


def serialized_size(payload: Dict[str, Any]) -> int:
    """The exact byte length this payload will occupy, measured not estimated."""
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))


# -- criteria -----------------------------------------------------------------


def criterion_view(criterion: object) -> Dict[str, Any]:
    """One acceptance criterion, whitelisted field by field.

    Publishes exactly what a reader needs to understand *what was expected* and
    nothing that describes how it is stored. Every key is written out here; a
    field added to :class:`~.criteria.AcceptanceCriterion` later does not appear
    in a response until somebody adds it to this function on purpose.
    """
    if not isinstance(criterion, AcceptanceCriterion):
        raise TypeError("a criterion view needs an AcceptanceCriterion")
    return {
        "criterion_id": criterion.criterion_id,
        "ordinal": criterion.ordinal,
        "kind": criterion.kind,
        "predicate": criterion.predicate,
        "path": criterion.path,
        "to_path": criterion.to_path,
        "operation": criterion.operation,
        "description": criterion.description,
    }


def criteria_view(snapshot: object) -> Dict[str, Any]:
    """One turn's criteria snapshot, in whichever of its three states it is in.

    ``recorded`` is published beside ``state`` rather than left to be derived,
    because the difference between "Cofferdam knew there were none" and "this
    turn predates criteria" is the single most misreadable thing in this
    response, and a client that had to compute it is a client that can get it
    wrong in the direction that reads as success.

    ``not_provided`` publishes a real snapshot identity and an **empty** item
    list. ``legacy_unknown`` publishes no identity at all — there is no snapshot
    to name, and inventing an empty one would assert that Cofferdam checked
    something it never had.
    """
    if not isinstance(snapshot, CriteriaSnapshot):
        raise TypeError("a criteria view needs a CriteriaSnapshot")
    if snapshot.state == CRITERIA_LEGACY_UNKNOWN:
        return {
            "state": CRITERIA_LEGACY_UNKNOWN,
            "recorded": False,
            "snapshot_id": None,
            "criteria_fingerprint": None,
            "criterion_count": 0,
            "items": [],
        }
    items: List[Dict[str, Any]] = [
        criterion_view(criterion)
        for criterion in sorted(snapshot.criteria, key=lambda item: item.ordinal)
    ]
    return {
        # `present` or `not_provided`, and both are recorded facts.
        "state": snapshot.state,
        "recorded": True,
        "snapshot_id": snapshot.snapshot_id,
        "criteria_fingerprint": snapshot.fingerprint,
        "criterion_count": snapshot.criterion_count,
        "items": items,
    }


# -- evaluation ---------------------------------------------------------------


def result_view(result: object) -> Dict[str, Any]:
    """One criterion's deterministic answer.

    Four fields and no fifth. There is no explanation string here because there
    is none in the database: ``reason`` is a closed code, and a client renders it
    through its own vocabulary rather than displaying prose the evaluator never
    wrote.
    """
    if not isinstance(result, CriterionResult):
        raise TypeError("a result view needs a CriterionResult")
    return {
        "criterion_id": result.criterion_id,
        "ordinal": result.ordinal,
        "result": result.result,
        "reason": result.reason,
    }


def evaluation_view(
    record: object, *, criteria_state: str, turn_open: bool
) -> Dict[str, Any]:
    """The evaluation, or a closed statement of why there is not one.

    The absent shapes carry the same keys as the present one, with nulls and an
    empty list, so a client renders one structure and branches on ``state``
    rather than on whether a key exists.

    **No aggregate is computed here or anywhere downstream.** ``result_count`` is
    how many criteria were answered, copied from the stored row; it is not a
    score, and there is deliberately no count of how many were met.
    """
    if record is None:
        if criteria_state == CRITERIA_LEGACY_UNKNOWN:
            state = EVALUATION_CRITERIA_LEGACY_UNKNOWN
        elif turn_open:
            state = EVALUATION_TURN_NOT_CLOSED
        else:
            state = EVALUATION_NOT_RECORDED
        return {
            "state": state,
            "recorded": False,
            "evaluation_id": None,
            "evaluator_version": None,
            "criteria_state": None,
            "criteria_snapshot_id": None,
            "criteria_fingerprint": None,
            "assembler_version": None,
            "evidence_input_fingerprint": None,
            "result_count": 0,
            "evaluation_fingerprint": None,
            "results": [],
        }
    if not isinstance(record, EvaluationRecord):
        raise TypeError("an evaluation view needs an EvaluationRecord")
    return {
        "state": EVALUATION_RECORDED,
        "recorded": True,
        "evaluation_id": record.evaluation_id,
        "evaluator_version": record.evaluator_version,
        # The criteria state the evaluation was made against, kept beside the
        # criteria block's own so a reader can see they agree rather than assume
        # it. They can only disagree if something is very wrong.
        "criteria_state": record.criteria_state,
        "criteria_snapshot_id": record.criteria_snapshot_id,
        "criteria_fingerprint": record.criteria_fingerprint,
        # How the bundle this was judged against was built, and which bundle it
        # was. Enough to correlate with the evidence route; not the bundle.
        "assembler_version": record.assembler_version,
        "evidence_input_fingerprint": record.evidence_input_fingerprint,
        "result_count": record.result_count,
        "evaluation_fingerprint": record.evaluation_fingerprint,
        "results": [
            result_view(result)
            for result in sorted(record.results, key=lambda item: item.ordinal)
        ],
    }


# -- the whole view -----------------------------------------------------------


def acceptance_view(aggregate_result: object) -> Dict[str, Any]:
    """M2K PR21's target-turn acceptance result, as published.

    **A serializer and nothing else.** Every value here was decided by
    :func:`~.acceptance.aggregate`; this function folds nothing, counts nothing,
    and re-derives no availability. It refuses an object of the wrong type rather
    than duck-typing its way through one, exactly as the views above do.

    Three fields are **tri-state and must stay that way through JSON**:

    * ``outcome`` is ``null`` exactly when the turn is not assessable — never a
      neutral word, and never ``incomplete``, which is a statement about a
      *known* requirement set containing something unverified;
    * ``counts`` is ``null`` when the active population could not be determined,
      and a real object of zeros when it was determined and is empty. Serialising
      the first as four zeros would publish an observation nobody made, and a
      client could not tell it from a genuinely empty requirement set;
    * ``requires_human`` is ``true`` / ``false`` / ``null``, and ``null`` never
      becomes ``false``. Whether a person is needed is unknown when the set is.

    **Reason codes are published exactly as the layers below produced them.** No
    translation, no prettier wording, no collapsing of PR20's eighteen lineage
    reasons back into one. Human phrasing belongs to the client; the contract
    carries the code-owned value.

    Deliberately **not** here: any global task verdict, any pass/fail/success
    word, any score, percentage, confidence or risk, and any low-level evidence
    fingerprint. The compositional identity — which envelope was folded, by which
    aggregator version, into which answer — is the audit trail this layer owes.
    """
    if not isinstance(aggregate_result, AcceptanceAggregate):
        raise TypeError("an acceptance view needs an AcceptanceAggregate")
    counts = aggregate_result.counts
    return {
        "aggregator_version": aggregate_result.aggregator_version,
        "availability": aggregate_result.availability,
        "availability_reason": aggregate_result.availability_reason,
        "unavailable_cause": aggregate_result.unavailable_cause,
        "unavailable_at_turn_number": aggregate_result.unavailable_at_turn_number,
        "outcome": aggregate_result.outcome,
        "counts": None
        if counts is None
        else {
            "total": counts.total,
            "met": counts.met,
            "not_met": counts.not_met,
            "unverified": counts.unverified,
        },
        "requires_human": aggregate_result.requires_human,
        "assessment_fingerprint": aggregate_result.assessment_fingerprint,
        "acceptance_fingerprint": aggregate_result.fingerprint,
    }


def assessment_view(
    *,
    task_id: str,
    turn_number: int,
    snapshot: object,
    record: object,
    turn_open: bool,
    acceptance: object = None,
) -> Dict[str, Any]:
    """One turn's criteria, evaluation and acceptance, as published.

    Turn-qualified and nothing else: there is no "latest" here, no task-level
    rollup, and no path by which turn two's evaluation could be returned for
    turn one. The caller has already resolved which turn this is; this function
    cannot look up another.

    ``acceptance`` is **additive** (M2K PR22). Existing clients reading
    ``criteria`` and ``evaluation`` are untouched — no key was renamed, removed
    or reinterpreted — and it is optional so the older two-section view stays
    constructible. It belongs in the same response because all three describe one
    historical target-turn audit boundary, and a reader needs them together or
    not at all; two routes would let a client pair sections read at different
    moments, which is the split
    :meth:`~.store.TaskStore.turn_audit_inputs` holds one snapshot to prevent.

    **The size ceiling covers acceptance too**, because it is placed in the
    payload *before* the check rather than attached to the result afterwards.
    Adding a field after the bound would quietly make the bound describe
    something other than what is sent.

    Raises :class:`AssessmentTooLarge` rather than returning a trimmed response.
    """
    if not isinstance(snapshot, CriteriaSnapshot):
        raise TypeError("an assessment view needs a CriteriaSnapshot")
    payload = {
        "version": ASSESSMENT_API_VERSION,
        "task_id": task_id,
        "turn_number": turn_number,
        "criteria": criteria_view(snapshot),
        "evaluation": evaluation_view(
            record, criteria_state=snapshot.state, turn_open=turn_open
        ),
    }
    if acceptance is not None:
        payload["acceptance"] = acceptance_view(acceptance)
    if serialized_size(payload) > MAX_SERIALIZED_ASSESSMENT_BYTES:
        raise AssessmentTooLarge(
            "the assessment does not fit the response contract"
        )
    return payload


__all__ = [
    "ASSESSMENT_API_VERSION",
    "EVALUATION_CRITERIA_LEGACY_UNKNOWN",
    "EVALUATION_NOT_RECORDED",
    "EVALUATION_RECORDED",
    "EVALUATION_STATES",
    "EVALUATION_TURN_NOT_CLOSED",
    "MAX_SERIALIZED_ASSESSMENT_BYTES",
    "AssessmentTooLarge",
    "acceptance_view",
    "assessment_view",
    "criteria_view",
    "criterion_view",
    "evaluation_view",
    "result_view",
    "serialized_size",
]
