"""M2K PR16 — the pure binder, on constructed inputs.

Everything here builds PR11 and PR7 shapes directly rather than through a
database, because the binder's whole claim is that it is a deterministic function
of immutable values. If a test needed a store to exercise it, that claim would
already be false.

The load-bearing assertion in this file is `HistoricalResultNeverCarries`: an
inherited change criterion is `unverified` **whatever** its origin turn decided,
and the origin's `met` is not privileged over its `not_met`.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.binding import (
    ASSESSMENT_RESOLVED,
    ASSESSMENT_UNAVAILABLE,
    CURRENT_ASSESSMENT_VERSION,
    DOMAIN_FINAL_STATE,
    DOMAIN_NOT_APPLICABLE,
    DOMAIN_TURN_CHANGE,
    EVIDENCE_DOMAINS,
    REASONS_FOR_RESULT,
    REASON_EVALUATION_INCONSISTENT,
    REASON_EVALUATION_NOT_RECORDED,
    REASON_FINAL_STATE_NOT_RECORDED,
    REASON_FINAL_STATE_OBSERVED,
    REASON_FINAL_STATE_PATH_UNAVAILABLE,
    REASON_FINAL_STATE_UNAVAILABLE,
    REASON_INHERITED_CHANGE_NOT_CURRENT,
    REASON_LINEAGE_UNAVAILABLE,
    REASON_MANUAL_AUTHORITY,
    REASON_TURN_CHANGE_EVALUATED,
    REASON_TURN_NOT_CLOSED,
    REASON_UNSUPPORTED_EVALUATOR,
    REASON_UNSUPPORTED_PREDICATE,
    bind,
    criterion_assessment_fingerprint,
)
from cofferdam.workstation.tasks.criteria import AcceptanceCriterion
from cofferdam.workstation.tasks.evaluation import (
    EVALUATOR_VERSION,
    RESULT_MET,
    RESULT_NOT_MET,
    RESULT_UNVERIFIED,
    CriterionResult,
    EvaluationRecord,
)
from cofferdam.workstation.tasks.lineage import (
    RESOLVER_VERSION,
    ActiveCriterion,
    LineageUnavailable,
    ResolvedActiveCriteria,
)

TASK = "task_01aaaaaaaaaaaaaaaaaaaaaaaa"
REPO_ROOT = Path(__file__).resolve().parents[1]


def criterion(ordinal, *, kind="evidence", predicate="path_changed", path="a.py"):
    return AcceptanceCriterion(
        ordinal=ordinal,
        kind=kind,
        predicate=predicate if kind == "evidence" else None,
        path=path if kind == "evidence" else None,
        description="check it" if kind == "manual" else None,
    )


def active(criterion_id, source_turn, item, *, snapshot=None):
    return ActiveCriterion(
        criterion_id=criterion_id,
        source_snapshot_id=snapshot or ("snapshot_000%d" % source_turn),
        source_turn_number=source_turn,
        source_ordinal=item.ordinal,
        criterion=item,
    )


def resolved(target, entries, *, fingerprint="lineage" + "f" * 58, snapshot=None):
    return ResolvedActiveCriteria(
        task_id=TASK,
        target_turn_number=target,
        target_snapshot_id=snapshot or ("snapshot_000%d" % target),
        resolver_version=RESOLVER_VERSION,
        active=tuple(entries),
        lineage=(),
        fingerprint=fingerprint,
        state="resolved",
    )


def evaluation(target, results, *, snapshot=None, version=EVALUATOR_VERSION, count=None):
    return EvaluationRecord(
        evaluation_id="eval_000000000001",
        task_id=TASK,
        turn_number=target,
        evaluator_version=version,
        criteria_state="present",
        criteria_snapshot_id=snapshot or ("snapshot_000%d" % target),
        criteria_fingerprint="c" * 64,
        assembler_version=3,
        evidence_input_fingerprint="e" * 64,
        result_count=len(results) if count is None else count,
        evaluation_fingerprint="d" * 64,
        recorded_at="2026-01-01T00:00:00Z",
        results=tuple(results),
    )


def result(criterion_id, ordinal, value, reason="machine_change_observed"):
    return CriterionResult(
        criterion_id=criterion_id, ordinal=ordinal, result=value, reason=reason
    )


class SameTurnChangeTests(unittest.TestCase):
    """A criterion that originated here binds to this turn's stored judgement."""

    def bound(self, stored_result):
        item = criterion(1)
        answer = bind(
            resolved(1, [active("criterion_0001", 1, item)]),
            evaluation(1, [result("criterion_0001", 1, stored_result)]),
            turn_closed=True,
        )
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        return answer.assessments[0]

    def test_a_met_result_is_bound_unchanged(self):
        self.assertEqual(RESULT_MET, self.bound(RESULT_MET).result)

    def test_a_not_met_result_is_bound_unchanged(self):
        self.assertEqual(RESULT_NOT_MET, self.bound(RESULT_NOT_MET).result)

    def test_an_unverified_result_is_bound_unchanged(self):
        self.assertEqual(RESULT_UNVERIFIED, self.bound(RESULT_UNVERIFIED).result)

    def test_the_domain_is_turn_change(self):
        self.assertEqual(DOMAIN_TURN_CHANGE, self.bound(RESULT_MET).domain)

    def test_the_reason_says_it_was_evaluated(self):
        self.assertEqual(REASON_TURN_CHANGE_EVALUATED, self.bound(RESULT_MET).reason)

    def test_it_carries_the_exact_pr7_judgement_identity(self):
        """Provenance is the evaluation fingerprint, not the minted row id."""
        assessment = self.bound(RESULT_MET)
        self.assertEqual("d" * 64, assessment.evidence_fingerprint)

    def test_origin_and_target_agree_for_a_same_turn_criterion(self):
        assessment = self.bound(RESULT_MET)
        self.assertEqual(1, assessment.source_turn_number)
        self.assertEqual(1, assessment.target_turn_number)
        self.assertFalse(assessment.inherited)


class HistoricalResultNeverCarries(unittest.TestCase):
    """The load-bearing rule: an old answer is never a current one."""

    def inherited(self, origin_result):
        """Criterion A from turn 1, still active at turn 2, decided at its origin."""
        first = criterion(1)
        second = criterion(1, path="b.py")
        return bind(
            resolved(
                2,
                [
                    active("criterion_0001", 1, first),
                    active("criterion_0002", 2, second),
                ],
            ),
            # Turn 2's own evaluation answers turn 2's own criterion. The origin
            # turn's record is not even offered to the binder — nothing about it
            # is consulted, which is the point.
            evaluation(2, [result("criterion_0002", 1, origin_result)]),
            turn_closed=True,
        ).assessments[0]

    def test_an_origin_met_does_not_become_a_current_met(self):
        assessment = self.inherited(RESULT_MET)
        self.assertEqual(RESULT_UNVERIFIED, assessment.result)
        self.assertEqual(REASON_INHERITED_CHANGE_NOT_CURRENT, assessment.reason)

    def test_an_origin_not_met_does_not_become_a_current_not_met(self):
        assessment = self.inherited(RESULT_NOT_MET)
        self.assertEqual(RESULT_UNVERIFIED, assessment.result)
        self.assertEqual(REASON_INHERITED_CHANGE_NOT_CURRENT, assessment.reason)

    def test_an_origin_unverified_stays_unverified_for_its_own_reason(self):
        assessment = self.inherited(RESULT_UNVERIFIED)
        self.assertEqual(RESULT_UNVERIFIED, assessment.result)
        self.assertEqual(REASON_INHERITED_CHANGE_NOT_CURRENT, assessment.reason)

    def test_all_three_origins_produce_an_identical_assessment(self):
        """The origin result cannot be recovered from the current answer.

        Not merely equal results — equal **fingerprints**. If the origin leaked
        into the derived identity in any form, these would differ.
        """
        prints = {
            self.inherited(value).fingerprint
            for value in (RESULT_MET, RESULT_NOT_MET, RESULT_UNVERIFIED)
        }
        self.assertEqual(1, len(prints))

    def test_no_machine_evidence_identity_is_fabricated(self):
        self.assertIsNone(self.inherited(RESULT_MET).evidence_fingerprint)
        self.assertEqual(DOMAIN_NOT_APPLICABLE, self.inherited(RESULT_MET).domain)

    def test_the_inherited_flag_is_set(self):
        assessment = self.inherited(RESULT_MET)
        self.assertTrue(assessment.inherited)
        self.assertEqual(1, assessment.source_turn_number)
        self.assertEqual(2, assessment.target_turn_number)

    def test_the_origin_snapshot_is_kept_for_audit(self):
        self.assertEqual("snapshot_0001", self.inherited(RESULT_MET).source_snapshot_id)


class ManualTests(unittest.TestCase):
    def test_a_same_turn_manual_criterion_is_unverified(self):
        item = criterion(1, kind="manual")
        answer = bind(
            resolved(1, [active("criterion_0001", 1, item)]),
            None,
            turn_closed=True,
        )
        assessment = answer.assessments[0]
        self.assertEqual(RESULT_UNVERIFIED, assessment.result)
        self.assertEqual(REASON_MANUAL_AUTHORITY, assessment.reason)
        self.assertEqual(DOMAIN_NOT_APPLICABLE, assessment.domain)
        self.assertIsNone(assessment.evidence_fingerprint)

    def test_an_inherited_manual_criterion_is_unverified_the_same_way(self):
        item = criterion(1, kind="manual")
        answer = bind(
            resolved(3, [active("criterion_0001", 1, item)]), None, turn_closed=True
        )
        assessment = answer.assessments[0]
        self.assertEqual(RESULT_UNVERIFIED, assessment.result)
        self.assertEqual(REASON_MANUAL_AUTHORITY, assessment.reason)

    def test_a_manual_only_turn_needs_no_evaluation_at_all(self):
        """Nothing here could be answered by PR7, so none is demanded."""
        item = criterion(1, kind="manual")
        answer = bind(
            resolved(1, [active("criterion_0001", 1, item)]), None, turn_closed=True
        )
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)


class UnsupportedPredicateTests(unittest.TestCase):
    """A predicate no version of this binder knows.

    `path_exists` used to stand in here and cannot any more — M2K PR18 decides
    it. The stand-in is a name this build genuinely does not have, which is the
    case the reason exists for: a criterion authored by a newer Cofferdam must
    get an honest answer from an older one rather than a crash or a guess.
    """

    def test_an_unknown_predicate_is_unverified_rather_than_an_exception(self):
        """Total, like PR7's evaluator, so a newer build's criterion is answerable."""
        item = AcceptanceCriterion(
            ordinal=1, kind="evidence", predicate="path_is_executable", path="a.py"
        )
        assessment = bind(
            resolved(1, [active("criterion_0001", 1, item)]), None, turn_closed=True
        ).assessments[0]
        self.assertEqual(RESULT_UNVERIFIED, assessment.result)
        self.assertEqual(REASON_UNSUPPORTED_PREDICATE, assessment.reason)
        self.assertEqual(DOMAIN_NOT_APPLICABLE, assessment.domain)

    def test_an_unknown_predicate_needs_no_evaluation(self):
        item = AcceptanceCriterion(
            ordinal=1, kind="evidence", predicate="path_is_executable", path="a.py"
        )
        answer = bind(
            resolved(1, [active("criterion_0001", 1, item)]), None, turn_closed=True
        )
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)


class LineageShapeTests(unittest.TestCase):
    """root / extend / revise / replace, as the resolver hands them over."""

    def test_root_binds_every_criterion_to_this_turn(self):
        items = [criterion(1), criterion(2, path="b.py")]
        answer = bind(
            resolved(
                1,
                [
                    active("criterion_0001", 1, items[0]),
                    active("criterion_0002", 1, items[1]),
                ],
            ),
            evaluation(
                1,
                [
                    result("criterion_0001", 1, RESULT_MET),
                    result("criterion_0002", 2, RESULT_NOT_MET),
                ],
            ),
            turn_closed=True,
        )
        self.assertEqual(
            [RESULT_MET, RESULT_NOT_MET], [a.result for a in answer.assessments]
        )
        self.assertEqual(
            [DOMAIN_TURN_CHANGE, DOMAIN_TURN_CHANGE],
            [a.domain for a in answer.assessments],
        )

    def test_extend_splits_inherited_from_current(self):
        answer = bind(
            resolved(
                2,
                [
                    active("criterion_0001", 1, criterion(1)),
                    active("criterion_0002", 2, criterion(1, path="b.py")),
                ],
            ),
            evaluation(2, [result("criterion_0002", 1, RESULT_MET)]),
            turn_closed=True,
        )
        first, second = answer.assessments
        self.assertEqual(RESULT_UNVERIFIED, first.result)
        self.assertEqual(REASON_INHERITED_CHANGE_NOT_CURRENT, first.reason)
        self.assertEqual(RESULT_MET, second.result)
        self.assertEqual(REASON_TURN_CHANGE_EVALUATED, second.reason)

    def test_revise_drops_the_superseded_criterion_entirely(self):
        """A is superseded, so the resolver never offers it and nothing mentions it."""
        answer = bind(
            resolved(
                2,
                [
                    active("criterion_0002", 1, criterion(2, path="b.py")),
                    active("criterion_0003", 2, criterion(1, path="c.py")),
                ],
            ),
            evaluation(2, [result("criterion_0003", 1, RESULT_MET)]),
            turn_closed=True,
        )
        identifiers = [a.criterion_id for a in answer.assessments]
        self.assertNotIn("criterion_0001", identifiers)
        self.assertEqual(["criterion_0002", "criterion_0003"], identifiers)
        self.assertEqual(RESULT_UNVERIFIED, answer.assessments[0].result)
        self.assertEqual(RESULT_MET, answer.assessments[1].result)

    def test_replace_cuts_every_older_criterion(self):
        answer = bind(
            resolved(
                2,
                [
                    active("criterion_0004", 2, criterion(1, path="d.py")),
                    active("criterion_0005", 2, criterion(2, path="e.py")),
                ],
            ),
            evaluation(
                2,
                [
                    result("criterion_0004", 1, RESULT_MET),
                    result("criterion_0005", 2, RESULT_MET),
                ],
            ),
            turn_closed=True,
        )
        self.assertEqual(
            ["criterion_0004", "criterion_0005"],
            [a.criterion_id for a in answer.assessments],
        )
        self.assertTrue(all(not a.inherited for a in answer.assessments))
        self.assertTrue(all(a.domain == DOMAIN_TURN_CHANGE for a in answer.assessments))

    def test_the_order_is_the_resolvers_and_is_not_re_sorted(self):
        """Not by id, path, predicate, source turn or fingerprint."""
        answer = bind(
            resolved(
                2,
                [
                    active("criterion_zzzz", 2, criterion(1, path="z.py")),
                    active("criterion_aaaa", 1, criterion(1, path="a.py")),
                ],
            ),
            evaluation(2, [result("criterion_zzzz", 1, RESULT_MET)]),
            turn_closed=True,
        )
        self.assertEqual(
            ["criterion_zzzz", "criterion_aaaa"],
            [a.criterion_id for a in answer.assessments],
        )


class EmptyAndUnavailableTests(unittest.TestCase):
    def test_a_resolved_empty_active_set_is_a_legitimate_answer(self):
        answer = bind(resolved(1, []), None, turn_closed=True)
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(0, answer.criterion_count)
        self.assertTrue(answer.fingerprint)
        # And it says nothing about acceptance.
        self.assertFalse(hasattr(answer, "met"))
        self.assertFalse(hasattr(answer, "passed"))
        self.assertFalse(hasattr(answer, "outcome"))

    def test_an_unresolvable_lineage_is_set_level_unavailable(self):
        answer = bind(
            LineageUnavailable(
                task_id=TASK,
                target_turn_number=2,
                resolver_version=RESOLVER_VERSION,
                reason="not_declared",
            ),
            None,
            turn_closed=True,
        )
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(REASON_LINEAGE_UNAVAILABLE, answer.unavailable_reason)
        self.assertEqual((), answer.assessments)
        self.assertIsNone(answer.lineage_fingerprint)

    def test_an_open_turn_is_refused_before_anything_else(self):
        answer = bind(
            resolved(1, [active("criterion_0001", 1, criterion(1))]),
            evaluation(1, [result("criterion_0001", 1, RESULT_MET)]),
            turn_closed=False,
        )
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(REASON_TURN_NOT_CLOSED, answer.unavailable_reason)
        self.assertEqual((), answer.assessments)

    def test_a_missing_evaluation_is_operational_not_semantic(self):
        """Never a set of `unverified` criteria, and never `not_met`."""
        answer = bind(
            resolved(1, [active("criterion_0001", 1, criterion(1))]),
            None,
            turn_closed=True,
        )
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(REASON_EVALUATION_NOT_RECORDED, answer.unavailable_reason)
        self.assertEqual((), answer.assessments)

    def test_an_unavailable_set_never_reports_not_met(self):
        for answer in (
            bind(resolved(1, [active("c", 1, criterion(1))]), None, turn_closed=True),
            bind(resolved(1, []), None, turn_closed=False),
        ):
            self.assertNotIn(
                RESULT_NOT_MET, [a.result for a in answer.assessments]
            )


class MalformedEvaluationTests(unittest.TestCase):
    """PR15 proved the DDL permits these. The binder refuses them."""

    def entries(self):
        return [active("criterion_0001", 1, criterion(1))]

    def test_an_evaluation_for_another_turn_is_refused(self):
        answer = bind(
            resolved(1, self.entries()),
            evaluation(2, [result("criterion_0001", 1, RESULT_MET)], snapshot="snapshot_0001"),
            turn_closed=True,
        )
        self.assertEqual(REASON_EVALUATION_INCONSISTENT, answer.unavailable_reason)

    def test_an_evaluation_naming_another_snapshot_is_refused(self):
        answer = bind(
            resolved(1, self.entries()),
            evaluation(
                1, [result("criterion_0001", 1, RESULT_MET)], snapshot="snapshot_0009"
            ),
            turn_closed=True,
        )
        self.assertEqual(REASON_EVALUATION_INCONSISTENT, answer.unavailable_reason)

    def test_an_evaluation_for_another_task_is_refused(self):
        record = evaluation(1, [result("criterion_0001", 1, RESULT_MET)])
        other = EvaluationRecord(**{**record.__dict__, "task_id": "task_other"})
        answer = bind(resolved(1, self.entries()), other, turn_closed=True)
        self.assertEqual(REASON_EVALUATION_INCONSISTENT, answer.unavailable_reason)

    def test_a_count_that_disagrees_with_the_results_is_refused(self):
        answer = bind(
            resolved(1, self.entries()),
            evaluation(1, [result("criterion_0001", 1, RESULT_MET)], count=7),
            turn_closed=True,
        )
        self.assertEqual(REASON_EVALUATION_INCONSISTENT, answer.unavailable_reason)

    def test_a_duplicated_criterion_answer_is_refused(self):
        answer = bind(
            resolved(1, self.entries()),
            evaluation(
                1,
                [
                    result("criterion_0001", 1, RESULT_MET),
                    result("criterion_0001", 2, RESULT_NOT_MET),
                ],
            ),
            turn_closed=True,
        )
        self.assertEqual(REASON_EVALUATION_INCONSISTENT, answer.unavailable_reason)

    def test_a_missing_answer_for_a_same_turn_criterion_is_refused(self):
        answer = bind(
            resolved(
                1,
                [
                    active("criterion_0001", 1, criterion(1)),
                    active("criterion_0002", 1, criterion(2, path="b.py")),
                ],
            ),
            evaluation(1, [result("criterion_0001", 1, RESULT_MET)]),
            turn_closed=True,
        )
        self.assertEqual(REASON_EVALUATION_INCONSISTENT, answer.unavailable_reason)

    def test_an_unsupported_evaluator_version_is_refused_distinctly(self):
        """Not the same as absent: waiting will not turn version 2 into version 1."""
        answer = bind(
            resolved(1, self.entries()),
            evaluation(1, [result("criterion_0001", 1, RESULT_MET)], version=99),
            turn_closed=True,
        )
        self.assertEqual(REASON_UNSUPPORTED_EVALUATOR, answer.unavailable_reason)

    def test_a_refusal_carries_no_partial_assessment(self):
        answer = bind(
            resolved(1, self.entries()),
            evaluation(2, [result("criterion_0001", 1, RESULT_MET)]),
            turn_closed=True,
        )
        self.assertEqual((), answer.assessments)
        self.assertIsNone(answer.lineage_fingerprint)


class FingerprintTests(unittest.TestCase):
    def answer(self, **overrides):
        base = dict(
            target=1,
            entries=[active("criterion_0001", 1, criterion(1))],
            results=[result("criterion_0001", 1, RESULT_MET)],
            fingerprint="lineage" + "f" * 58,
        )
        base.update(overrides)
        return bind(
            resolved(base["target"], base["entries"], fingerprint=base["fingerprint"]),
            evaluation(base["target"], base["results"]),
            turn_closed=True,
        )

    def test_it_is_a_sha256_hex_digest(self):
        value = self.answer().fingerprint
        self.assertEqual(64, len(value))
        int(value, 16)

    def test_it_is_deterministic(self):
        self.assertEqual(self.answer().fingerprint, self.answer().fingerprint)

    def test_the_assessment_version_is_bound(self):
        one = criterion_assessment_fingerprint(
            criterion_id="c",
            source_snapshot_id="s",
            source_turn_number=1,
            target_turn_number=1,
            kind="evidence",
            predicate="path_changed",
            domain=DOMAIN_TURN_CHANGE,
            result=RESULT_MET,
            reason=REASON_TURN_CHANGE_EVALUATED,
            evidence_fingerprint="d" * 64,
        )
        self.assertNotEqual(one, "0" * 64)
        # M2K PR18 took this to 2 (a second evidence domain); M2K PR20 to 3 (an
        # unavailable envelope that preserves which lineage failure occurred).
        self.assertEqual(3, CURRENT_ASSESSMENT_VERSION)

    def test_the_lineage_fingerprint_moves_the_set_fingerprint(self):
        self.assertNotEqual(
            self.answer().fingerprint,
            self.answer(fingerprint="other" + "f" * 59).fingerprint,
        )

    def test_the_target_turn_moves_it(self):
        other = bind(
            resolved(4, [active("criterion_0001", 4, criterion(1))]),
            evaluation(4, [result("criterion_0001", 1, RESULT_MET)]),
            turn_closed=True,
        )
        self.assertNotEqual(self.answer().fingerprint, other.fingerprint)

    def test_the_result_moves_it(self):
        self.assertNotEqual(
            self.answer().fingerprint,
            self.answer(results=[result("criterion_0001", 1, RESULT_NOT_MET)]).fingerprint,
        )

    def test_the_criterion_origin_moves_it(self):
        """Same criterion, same result — a different origin is a different fact."""
        one = criterion_assessment_fingerprint(
            criterion_id="c", source_snapshot_id="s", source_turn_number=1,
            target_turn_number=4, kind="evidence", predicate="path_changed",
            domain=DOMAIN_NOT_APPLICABLE, result=RESULT_UNVERIFIED,
            reason=REASON_INHERITED_CHANGE_NOT_CURRENT, evidence_fingerprint=None,
        )
        two = criterion_assessment_fingerprint(
            criterion_id="c", source_snapshot_id="s", source_turn_number=2,
            target_turn_number=4, kind="evidence", predicate="path_changed",
            domain=DOMAIN_NOT_APPLICABLE, result=RESULT_UNVERIFIED,
            reason=REASON_INHERITED_CHANGE_NOT_CURRENT, evidence_fingerprint=None,
        )
        self.assertNotEqual(one, two)

    def test_the_domain_moves_it(self):
        """A future final_state `met` must never hash equal to a turn_change one."""
        shared = dict(
            criterion_id="c", source_snapshot_id="s", source_turn_number=1,
            target_turn_number=1, kind="evidence", predicate="path_changed",
            result=RESULT_MET, reason=REASON_TURN_CHANGE_EVALUATED,
            evidence_fingerprint="d" * 64,
        )
        self.assertNotEqual(
            criterion_assessment_fingerprint(domain=DOMAIN_TURN_CHANGE, **shared),
            criterion_assessment_fingerprint(domain="final_state", **shared),
        )

    def test_the_evidence_identity_moves_it(self):
        shared = dict(
            criterion_id="c", source_snapshot_id="s", source_turn_number=1,
            target_turn_number=1, kind="evidence", predicate="path_changed",
            domain=DOMAIN_TURN_CHANGE, result=RESULT_MET,
            reason=REASON_TURN_CHANGE_EVALUATED,
        )
        self.assertNotEqual(
            criterion_assessment_fingerprint(evidence_fingerprint="a" * 64, **shared),
            criterion_assessment_fingerprint(evidence_fingerprint="b" * 64, **shared),
        )

    def test_the_ordering_moves_the_set_fingerprint(self):
        forward = [
            active("criterion_0001", 1, criterion(1)),
            active("criterion_0002", 1, criterion(2, path="b.py")),
        ]
        results = [
            result("criterion_0001", 1, RESULT_MET),
            result("criterion_0002", 2, RESULT_MET),
        ]
        self.assertNotEqual(
            self.answer(entries=forward, results=results).fingerprint,
            self.answer(entries=list(reversed(forward)), results=results).fingerprint,
        )

    def test_the_unavailable_reason_moves_it(self):
        one = bind(resolved(1, [active("c", 1, criterion(1))]), None, turn_closed=True)
        two = bind(resolved(1, [active("c", 1, criterion(1))]), None, turn_closed=False)
        self.assertNotEqual(one.fingerprint, two.fingerprint)

    def test_no_clock_or_row_handle_reaches_the_material(self):
        """`recorded_at` and the minted evaluation id must not move the answer."""
        record = evaluation(1, [result("criterion_0001", 1, RESULT_MET)])
        other = EvaluationRecord(
            **{
                **record.__dict__,
                "recorded_at": "2099-12-31T23:59:59Z",
                "evaluation_id": "eval_999999999999",
            }
        )
        entries = [active("criterion_0001", 1, criterion(1))]
        self.assertEqual(
            bind(resolved(1, entries), record, turn_closed=True).fingerprint,
            bind(resolved(1, entries), other, turn_closed=True).fingerprint,
        )


class VocabularyTests(unittest.TestCase):
    def test_only_a_bound_judgement_may_be_met_or_not_met(self):
        """Two machine domains now; every other reason is `unverified`-only.

        The list is asserted exactly rather than by membership. A limitation
        reason quietly gaining permission to be `met` is the shape of mistake
        that turns "we could not look" into "it is there".
        """
        for outcome in (RESULT_MET, RESULT_NOT_MET):
            self.assertEqual(
                (REASON_TURN_CHANGE_EVALUATED, REASON_FINAL_STATE_OBSERVED),
                REASONS_FOR_RESULT[outcome],
            )
        for limitation in (
            REASON_FINAL_STATE_UNAVAILABLE,
            REASON_FINAL_STATE_NOT_RECORDED,
            REASON_FINAL_STATE_PATH_UNAVAILABLE,
        ):
            self.assertNotIn(limitation, REASONS_FOR_RESULT[RESULT_MET])
            self.assertNotIn(limitation, REASONS_FOR_RESULT[RESULT_NOT_MET])
            self.assertIn(limitation, REASONS_FOR_RESULT[RESULT_UNVERIFIED])

    def test_every_produced_reason_is_valid_for_its_result(self):
        answers = [
            bind(
                resolved(
                    2,
                    [
                        active("criterion_0001", 1, criterion(1)),
                        active("criterion_0002", 2, criterion(1, path="b.py")),
                        active("criterion_0003", 2, criterion(2, kind="manual")),
                    ],
                ),
                evaluation(
                    2,
                    [
                        result("criterion_0002", 1, RESULT_MET),
                        result("criterion_0003", 2, RESULT_UNVERIFIED),
                    ],
                    count=2,
                ),
                turn_closed=True,
            )
        ]
        for answer in answers:
            for assessment in answer.assessments:
                self.assertIn(
                    assessment.reason, REASONS_FOR_RESULT[assessment.result]
                )

    def test_the_domain_vocabulary_is_closed_and_v2_sized(self):
        """M2K PR18 added exactly one domain. `named_check` is still not here."""
        self.assertEqual(
            (DOMAIN_TURN_CHANGE, DOMAIN_FINAL_STATE, DOMAIN_NOT_APPLICABLE),
            EVIDENCE_DOMAINS,
        )
        self.assertNotIn("named_check", EVIDENCE_DOMAINS)

    def test_no_confidence_or_score_field_exists(self):
        assessment = bind(
            resolved(1, [active("criterion_0001", 1, criterion(1))]),
            evaluation(1, [result("criterion_0001", 1, RESULT_MET)]),
            turn_closed=True,
        ).assessments[0]
        for forbidden in ("confidence", "score", "risk", "probability", "verdict"):
            self.assertFalse(hasattr(assessment, forbidden))


class PurityTests(unittest.TestCase):
    """The binder is a function of values, proven from its own source."""

    def source(self):
        path = REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "binding.py"
        return ast.parse(path.read_text(encoding="utf-8"))

    def imported(self):
        names = set()
        for node in ast.walk(self.source()):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module.split(".")[0])
                names.update(alias.name for alias in node.names)
        return names

    def test_it_imports_nothing_that_touches_the_world(self):
        forbidden = {
            "sqlite3", "os", "subprocess", "shutil", "socket", "urllib",
            "requests", "httpx", "time", "datetime", "random", "pathlib",
            "tempfile", "threading",
        }
        self.assertEqual(set(), self.imported() & forbidden)

    def test_it_does_not_import_the_store_the_service_or_any_observer(self):
        forbidden = {
            "store", "service", "TaskStore", "TaskService",
            "observe", "gitbaseline", "gitrange", "evidence", "claims",
        }
        self.assertEqual(set(), self.imported() & forbidden)

    def test_it_takes_only_values_and_a_pure_verifier_from_final_state(self):
        """M2K PR18: the vocabulary and the hash check, and nothing that looks.

        PR16 forbade the `finalstate` import outright because it had no business
        reading an observation. PR18 does, so the rule moves down a level rather
        than away: the binder may name PR14's *constants and pure functions* and
        may not name anything that touches a filesystem. Asserted as an exact
        allowlist, because a permissive check here would let `observe_path`
        arrive one day with no test noticing.
        """
        allowed = {
            "FINAL_STATE_OBSERVER_VERSION", "OBSERVATION_COMPLETE",
            "OBSERVATION_INCOMPLETE", "OBSERVATION_UNAVAILABLE", "PATH_ABSENT",
            "PATH_PRESENT", "PATH_UNAVAILABLE", "STORED_OBSERVATION_STATES",
            "verify_final_state_fingerprint", "finalstate",
        }
        taken = set()
        for node in ast.walk(self.source()):
            if isinstance(node, ast.ImportFrom) and node.module == ".finalstate".lstrip("."):
                taken.update(alias.name for alias in node.names)
        self.assertTrue(taken)
        self.assertEqual(set(), taken - allowed)
        for observer in (
            "observe_path", "observe_paths", "target_paths",
            "FinalStateObservation", "PathObservation", "final_state_fingerprint",
        ):
            self.assertNotIn(observer, taken)

    def test_it_never_calls_the_evaluator(self):
        """It reads stored results; it does not decide criteria."""
        called = set()
        for node in ast.walk(self.source()):
            if isinstance(node, ast.Call):
                target = node.func
                name = getattr(target, "id", None) or getattr(target, "attr", None)
                if name:
                    called.add(name)
        for forbidden in ("evaluate", "evaluate_criterion", "observe_path", "resolve"):
            self.assertNotIn(forbidden, called)

    def test_it_reads_stored_observations_and_never_takes_one(self):
        """"Use FinalStateObservation" means the stored row, never a fresh look.

        The distinction PR18 rests on. Consuming an immutable observation as data
        is the whole feature; going and asking the filesystem what is there now
        would make historical answers drift with the repository and turn a read
        into a probe of somebody's disk.
        """
        tree = self.source()
        referenced = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        for forbidden in (
            "turn_final_state",
            "record_final_state",
            "observe_path",
            "observe_paths",
            "target_paths",
            "lstat",
            "stat",
        ):
            self.assertNotIn(forbidden, referenced)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
