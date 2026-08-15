"""M2K PR8 — the published assessment shape, field by field.

The serializer is a **whitelist**, and these tests are what keeps it one. Every
published key is asserted by name, so a field added to a dataclass for an
internal reason cannot arrive in a client response by accident — the test that
enumerates the keys fails first.

The other thing pinned here is the absence of an aggregate. There is no overall
result, no pass, no fail, no score, no percentage and no count of how many
criteria were met, in the response or anywhere that could compute one from it.
A list of per-criterion results is not a verdict on a task.
"""

from __future__ import annotations

import json
import unittest

from cofferdam.workstation.tasks.assessment import (
    ASSESSMENT_API_VERSION,
    EVALUATION_CRITERIA_LEGACY_UNKNOWN,
    EVALUATION_NOT_RECORDED,
    EVALUATION_RECORDED,
    EVALUATION_STATES,
    EVALUATION_TURN_NOT_CLOSED,
    MAX_SERIALIZED_ASSESSMENT_BYTES,
    AssessmentTooLarge,
    assessment_view,
    criteria_view,
    criterion_view,
    evaluation_view,
    result_view,
    serialized_size,
)
from cofferdam.workstation.tasks.criteria import (
    CRITERIA_LEGACY_UNKNOWN,
    CRITERIA_NOT_PROVIDED,
    CRITERIA_PRESENT,
    AcceptanceCriterion,
    CriteriaSnapshot,
    validate_criteria,
)
from cofferdam.workstation.tasks.evaluation import (
    EVALUATOR_VERSION,
    REASON_MACHINE_CHANGE_OBSERVED,
    REASON_MANUAL,
    RESULT_MET,
    RESULT_NOT_MET,
    RESULT_UNVERIFIED,
    CriterionResult,
    EvaluationRecord,
)

CRITERIA = [
    {"kind": "evidence", "predicate": "path_changed", "path": "src/a.py"},
    {
        "kind": "evidence",
        "predicate": "path_operation",
        "path": "src/b.py",
        "operation": "created",
        "description": "the new module has to exist",
    },
    {
        "kind": "evidence",
        "predicate": "rename",
        "path": "src/old.py",
        "to_path": "src/new.py",
    },
    {"kind": "manual", "description": "a person confirms the page renders"},
]


def snapshot(state=CRITERIA_PRESENT, items=None, task_id="task_x", turn_number=1):
    criteria = ()
    if state == CRITERIA_PRESENT:
        criteria = tuple(
            c.with_id("acr_%d" % c.ordinal)
            for c in validate_criteria(CRITERIA if items is None else items)
        )
    if state == CRITERIA_LEGACY_UNKNOWN:
        return CriteriaSnapshot(task_id=task_id, turn_number=turn_number, state=state)
    return CriteriaSnapshot(
        task_id=task_id,
        turn_number=turn_number,
        state=state,
        snapshot_id="acs_" + "a" * 26,
        fingerprint="c" * 64,
        criterion_count=len(criteria),
        dispatch_state="turn_opened",
        recorded_at="2026-08-16T00:00:00Z",
        criteria=criteria,
    )


def record(results=None, task_id="task_x", turn_number=1, state=CRITERIA_PRESENT):
    if results is None:
        results = (
            CriterionResult("acr_1", 1, RESULT_MET, REASON_MACHINE_CHANGE_OBSERVED),
            CriterionResult("acr_2", 2, RESULT_NOT_MET, "complete_resulting_change_absent"),
            CriterionResult("acr_3", 3, RESULT_UNVERIFIED, "pre_work_boundary_not_clean"),
            CriterionResult("acr_4", 4, RESULT_UNVERIFIED, REASON_MANUAL),
        )
    return EvaluationRecord(
        evaluation_id="evl_" + "b" * 26,
        task_id=task_id,
        turn_number=turn_number,
        evaluator_version=EVALUATOR_VERSION,
        criteria_state=state,
        criteria_snapshot_id="acs_" + "a" * 26,
        criteria_fingerprint="c" * 64,
        assembler_version=3,
        evidence_input_fingerprint="f" * 64,
        result_count=len(results),
        evaluation_fingerprint="d" * 64,
        recorded_at="2026-08-16T01:00:00Z",
        results=tuple(results),
    )


class TheVocabulary(unittest.TestCase):
    def test_four_evaluation_states_and_none_of_them_is_a_pass(self):
        self.assertEqual(
            EVALUATION_STATES,
            (
                EVALUATION_RECORDED,
                EVALUATION_CRITERIA_LEGACY_UNKNOWN,
                EVALUATION_TURN_NOT_CLOSED,
                EVALUATION_NOT_RECORDED,
            ),
        )
        for state in EVALUATION_STATES:
            for forbidden in ("pass", "success", "ok", "complete", "fail"):
                self.assertNotIn(forbidden, state, state)

    def test_pending_is_not_a_word_this_module_uses(self):
        """It invites polling and implies a record is owed."""
        for state in EVALUATION_STATES:
            self.assertNotIn("pending", state)


class TopLevelShape(unittest.TestCase):
    def test_the_response_has_exactly_these_keys(self):
        view = assessment_view(
            task_id="task_x", turn_number=1, snapshot=snapshot(),
            record=record(), turn_open=False,
        )
        self.assertEqual(
            sorted(view), ["criteria", "evaluation", "task_id", "turn_number", "version"]
        )
        self.assertEqual(view["version"], ASSESSMENT_API_VERSION)
        self.assertEqual(view["task_id"], "task_x")
        self.assertEqual(view["turn_number"], 1)

    def test_there_is_no_aggregate_anywhere(self):
        view = assessment_view(
            task_id="task_x", turn_number=1, snapshot=snapshot(),
            record=record(), turn_open=False,
        )
        blob = json.dumps(view)
        for forbidden in (
            "overall", "pass", "passed", "failed", "success", "score",
            "percent", "all_met", "met_count", "confidence", "risk", "verdict",
            "aggregate", "summary",
        ):
            self.assertNotIn('"%s"' % forbidden, blob, forbidden)

    def test_no_host_or_provider_detail_leaks(self):
        view = assessment_view(
            task_id="task_x", turn_number=1, snapshot=snapshot(),
            record=record(), turn_open=False,
        )
        blob = json.dumps(view)
        for forbidden in (
            "/home/", "sqlite", "rowid", "session", "provider", "project_root",
            "recorded_at", "dispatch_state",
        ):
            self.assertNotIn(forbidden, blob, forbidden)

    def test_the_evidence_bundle_is_named_not_embedded(self):
        view = assessment_view(
            task_id="task_x", turn_number=1, snapshot=snapshot(),
            record=record(), turn_open=False,
        )
        evaluation = view["evaluation"]
        self.assertEqual(evaluation["assembler_version"], 3)
        self.assertEqual(len(evaluation["evidence_input_fingerprint"]), 64)
        blob = json.dumps(view)
        for forbidden in ("observations", "claims", "relationships", "ingestion", "limitations"):
            self.assertNotIn(forbidden, blob, forbidden)

    def test_claim_conflict_is_absent_entirely(self):
        view = assessment_view(
            task_id="task_x", turn_number=1, snapshot=snapshot(),
            record=record(), turn_open=False,
        )
        self.assertNotIn("claim_conflict", json.dumps(view))


class CriteriaSerialization(unittest.TestCase):
    def test_present_publishes_exactly_these_keys(self):
        view = criteria_view(snapshot())
        self.assertEqual(
            sorted(view),
            ["criteria_fingerprint", "criterion_count", "items", "recorded", "snapshot_id", "state"],
        )
        self.assertEqual(view["state"], CRITERIA_PRESENT)
        self.assertTrue(view["recorded"])
        self.assertEqual(view["criterion_count"], 4)
        self.assertEqual(len(view["items"]), 4)

    def test_a_criterion_publishes_exactly_these_keys(self):
        item = criterion_view(snapshot().criteria[0])
        self.assertEqual(
            sorted(item),
            ["criterion_id", "description", "kind", "operation", "ordinal", "path",
             "predicate", "to_path"],
        )

    def test_items_are_in_deterministic_ordinal_order(self):
        view = criteria_view(snapshot())
        self.assertEqual([i["ordinal"] for i in view["items"]], [1, 2, 3, 4])
        # Even if the stored tuple arrives reordered.
        snap = snapshot()
        shuffled = CriteriaSnapshot(
            **{**{f: getattr(snap, f) for f in snap.__dataclass_fields__},
               "criteria": tuple(reversed(snap.criteria))}
        )
        self.assertEqual([i["ordinal"] for i in criteria_view(shuffled)["items"]], [1, 2, 3, 4])

    def test_not_provided_is_recorded_with_no_items(self):
        view = criteria_view(snapshot(state=CRITERIA_NOT_PROVIDED, items=[]))
        self.assertEqual(view["state"], CRITERIA_NOT_PROVIDED)
        self.assertTrue(view["recorded"])
        self.assertEqual(view["criterion_count"], 0)
        self.assertEqual(view["items"], [])
        self.assertIsNotNone(view["snapshot_id"])

    def test_legacy_unknown_publishes_no_fabricated_identity(self):
        view = criteria_view(snapshot(state=CRITERIA_LEGACY_UNKNOWN))
        self.assertEqual(view["state"], CRITERIA_LEGACY_UNKNOWN)
        self.assertFalse(view["recorded"])
        self.assertIsNone(view["snapshot_id"])
        self.assertIsNone(view["criteria_fingerprint"])
        self.assertEqual(view["items"], [])

    def test_legacy_unknown_is_not_not_provided(self):
        legacy = criteria_view(snapshot(state=CRITERIA_LEGACY_UNKNOWN))
        empty = criteria_view(snapshot(state=CRITERIA_NOT_PROVIDED, items=[]))
        self.assertNotEqual(legacy["state"], empty["state"])
        self.assertNotEqual(legacy["recorded"], empty["recorded"])


class EvaluationSerialization(unittest.TestCase):
    def test_recorded_publishes_exactly_these_keys(self):
        view = evaluation_view(record(), criteria_state=CRITERIA_PRESENT, turn_open=False)
        self.assertEqual(
            sorted(view),
            ["assembler_version", "criteria_fingerprint", "criteria_snapshot_id",
             "criteria_state", "evaluation_fingerprint", "evaluation_id",
             "evaluator_version", "evidence_input_fingerprint", "recorded",
             "result_count", "results", "state"],
        )
        self.assertEqual(view["state"], EVALUATION_RECORDED)
        self.assertTrue(view["recorded"])
        self.assertEqual(view["evaluator_version"], EVALUATOR_VERSION)

    def test_a_result_publishes_exactly_these_keys(self):
        row = result_view(record().results[0])
        self.assertEqual(sorted(row), ["criterion_id", "ordinal", "reason", "result"])

    def test_results_are_in_deterministic_ordinal_order(self):
        rows = record().results
        view = evaluation_view(
            record(results=tuple(reversed(rows))), criteria_state=CRITERIA_PRESENT,
            turn_open=False,
        )
        self.assertEqual([r["ordinal"] for r in view["results"]], [1, 2, 3, 4])

    def test_every_result_word_is_from_the_closed_vocabulary(self):
        view = evaluation_view(record(), criteria_state=CRITERIA_PRESENT, turn_open=False)
        for row in view["results"]:
            self.assertIn(row["result"], (RESULT_MET, RESULT_NOT_MET, RESULT_UNVERIFIED))

    def test_criterion_ids_line_up_with_the_criteria(self):
        view = assessment_view(
            task_id="task_x", turn_number=1, snapshot=snapshot(),
            record=record(), turn_open=False,
        )
        self.assertEqual(
            [i["criterion_id"] for i in view["criteria"]["items"]],
            [r["criterion_id"] for r in view["evaluation"]["results"]],
        )

    def test_absent_shapes_carry_the_same_keys_as_the_present_one(self):
        """So a client branches on `state`, never on whether a key exists."""
        present = evaluation_view(record(), criteria_state=CRITERIA_PRESENT, turn_open=False)
        for kwargs in (
            {"criteria_state": CRITERIA_LEGACY_UNKNOWN, "turn_open": False},
            {"criteria_state": CRITERIA_PRESENT, "turn_open": True},
            {"criteria_state": CRITERIA_PRESENT, "turn_open": False},
        ):
            absent = evaluation_view(None, **kwargs)
            self.assertEqual(sorted(absent), sorted(present))
            self.assertFalse(absent["recorded"])
            self.assertEqual(absent["results"], [])
            self.assertEqual(absent["result_count"], 0)

    def test_the_three_absent_states_are_distinguished(self):
        self.assertEqual(
            evaluation_view(None, criteria_state=CRITERIA_LEGACY_UNKNOWN, turn_open=False)["state"],
            EVALUATION_CRITERIA_LEGACY_UNKNOWN,
        )
        self.assertEqual(
            evaluation_view(None, criteria_state=CRITERIA_PRESENT, turn_open=True)["state"],
            EVALUATION_TURN_NOT_CLOSED,
        )
        self.assertEqual(
            evaluation_view(None, criteria_state=CRITERIA_PRESENT, turn_open=False)["state"],
            EVALUATION_NOT_RECORDED,
        )

    def test_a_not_provided_evaluation_is_recorded_with_zero_results(self):
        view = evaluation_view(
            record(results=(), state=CRITERIA_NOT_PROVIDED),
            criteria_state=CRITERIA_NOT_PROVIDED, turn_open=False,
        )
        self.assertEqual(view["state"], EVALUATION_RECORDED)
        self.assertTrue(view["recorded"])
        self.assertEqual(view["result_count"], 0)
        self.assertEqual(view["results"], [])
        self.assertNotIn("passed", json.dumps(view))


class TypeSafety(unittest.TestCase):
    """A dict cannot masquerade as a stored record."""

    def test_criteria_view_refuses_anything_else(self):
        for bad in ({"state": "present"}, None, "present", 7, object()):
            with self.assertRaises(TypeError):
                criteria_view(bad)

    def test_criterion_view_refuses_anything_else(self):
        for bad in ({"ordinal": 1}, None, "x", object()):
            with self.assertRaises(TypeError):
                criterion_view(bad)

    def test_evaluation_view_refuses_a_non_record(self):
        for bad in ({"evaluation_id": "x"}, "x", 7, object()):
            with self.assertRaises(TypeError):
                evaluation_view(bad, criteria_state=CRITERIA_PRESENT, turn_open=False)

    def test_result_view_refuses_anything_else(self):
        for bad in ({"result": "met"}, None, "met", object()):
            with self.assertRaises(TypeError):
                result_view(bad)

    def test_assessment_view_refuses_a_non_snapshot(self):
        with self.assertRaises(TypeError):
            assessment_view(
                task_id="t", turn_number=1, snapshot={"state": "present"},
                record=None, turn_open=False,
            )

    def test_the_module_uses_no_reflective_serialization(self):
        """asdict/vars/__dict__ would publish whatever a dataclass gains next."""
        import ast
        from pathlib import Path

        from cofferdam.workstation.tasks import assessment as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for forbidden in ("asdict", "vars", "dict"):
            self.assertNotIn(forbidden, names, forbidden)
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        for forbidden in ("__dict__", "__dataclass_fields__"):
            self.assertNotIn(forbidden, attributes, forbidden)


class TransportCeiling(unittest.TestCase):
    def test_a_maximum_criteria_assessment_fits(self):
        """The real worst case: 32 criteria, each at every bound PR6 permits.

        Distinct by construction — PR6 refuses a duplicate criterion, so an
        honest worst case cannot be 32 copies of one. Each carries a maximal
        rename (two 512-character paths) plus a maximal 500-character
        description, which is the largest assessment the criteria bounds can
        produce.
        """
        def wide(prefix, n):
            # Two 250-character segments: PR6 bounds a path at 512 characters
            # *and* each segment at 255, so a maximal path is several segments.
            return "%s%03d/%s/%s" % (prefix, n, "d" * 250, "e" * 250)

        items = [
            {
                "kind": "evidence",
                "predicate": "rename",
                "path": wide("src", n),
                "to_path": wide("dst", n),
                "description": ("x" * 497) + ("%03d" % n),
            }
            for n in range(32)
        ]
        snap = snapshot(items=items)
        results = tuple(
            CriterionResult("acr_%d" % n, n, RESULT_UNVERIFIED, REASON_MANUAL)
            for n in range(1, 33)
        )
        view = assessment_view(
            task_id="task_x", turn_number=1, snapshot=snap,
            record=record(results=results), turn_open=False,
        )
        size = serialized_size(view)
        self.assertLess(size, MAX_SERIALIZED_ASSESSMENT_BYTES, size)

    def test_an_oversize_assessment_fails_closed(self):
        """Refused, never trimmed. Half an audit view is worse than an error."""
        import cofferdam.workstation.tasks.assessment as module

        original = module.MAX_SERIALIZED_ASSESSMENT_BYTES
        module.MAX_SERIALIZED_ASSESSMENT_BYTES = 64
        try:
            with self.assertRaises(AssessmentTooLarge):
                assessment_view(
                    task_id="task_x", turn_number=1, snapshot=snapshot(),
                    record=record(), turn_open=False,
                )
        finally:
            module.MAX_SERIALIZED_ASSESSMENT_BYTES = original

    def test_nothing_is_silently_truncated(self):
        snap = snapshot()
        view = assessment_view(
            task_id="task_x", turn_number=1, snapshot=snap,
            record=record(), turn_open=False,
        )
        self.assertEqual(len(view["criteria"]["items"]), len(snap.criteria))
        self.assertEqual(len(view["evaluation"]["results"]), 4)
        self.assertNotIn("truncated", json.dumps(view))


class Determinism(unittest.TestCase):
    def test_the_same_inputs_give_the_same_bytes(self):
        views = {
            json.dumps(
                assessment_view(
                    task_id="task_x", turn_number=1, snapshot=snapshot(),
                    record=record(), turn_open=False,
                ),
                sort_keys=True,
            )
            for _ in range(20)
        }
        self.assertEqual(len(views), 1)

    def test_no_clock_reading_appears_in_the_view(self):
        view = assessment_view(
            task_id="task_x", turn_number=1, snapshot=snapshot(),
            record=record(), turn_open=False,
        )
        blob = json.dumps(view)
        self.assertNotIn("generated_at", blob)
        self.assertNotIn("2026-08-16T01:00:00Z", blob)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
