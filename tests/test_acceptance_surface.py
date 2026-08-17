"""M2K PR22 — the acceptance serializer, and the one-response consistency rule.

Two things are proven here and neither is about HTTP.

`SerializerShape` pins that the published contract is an explicit whitelist whose
tri-states survive JSON. `counts: null` and `counts: {0,0,0,0}` are different
answers, and `requires_human: null` is not `false` — collapsing either in
transport would undo, at the last possible moment, the distinction PR21 made
unrepresentable internally.

`OneResponseSnapshot` is the load-bearing one. PR8's criteria/evaluation read and
PR16's current-assessment read are each internally consistent, and calling both
would still be wrong: the PR7 evaluation row is written *after* dispatch by a
bounded recovery pass, so one landing between two reads would produce a single
envelope saying `evaluation: not_recorded` beside `acceptance: assessable`. Both
reads being individually correct is not the property that matters — the response
is the unit of consistency.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

from cofferdam.workstation.config import load_config
from cofferdam.workstation.tasks.acceptance import (
    AGGREGATOR_VERSION,
    AVAILABILITY_ASSESSABLE,
    AVAILABILITY_NOT_ASSESSABLE,
    OUTCOME_INCOMPLETE,
    OUTCOME_MET,
    OUTCOME_NOT_MET,
    REASON_NO_STRUCTURED_CRITERIA,
    AcceptanceAggregate,
    CriterionCounts,
    aggregate,
)
from cofferdam.workstation.tasks.assessment import (
    MAX_SERIALIZED_ASSESSMENT_BYTES,
    AssessmentTooLarge,
    acceptance_view,
    assessment_view,
    serialized_size,
)
from cofferdam.workstation.tasks.binding import (
    ASSESSMENT_RESOLVED,
    ASSESSMENT_UNAVAILABLE,
    CURRENT_ASSESSMENT_VERSION,
    SET_REASONS,
    CriterionAssessment,
    CurrentAssessment,
)
from cofferdam.workstation.tasks.continuity import validate_declaration
from cofferdam.workstation.tasks.criteria import CriteriaSnapshot, validate_criteria
from cofferdam.workstation.tasks.evaluation import (
    RESULT_MET,
    RESULT_NOT_MET,
    RESULT_UNVERIFIED,
)
from cofferdam.workstation.tasks.identity import new_task_id
from cofferdam.workstation.tasks.lineage import (
    REASON_LEGACY_UNKNOWN,
    REASON_NOT_DECLARED,
    REASON_PREDECESSOR_UNAVAILABLE,
)
from cofferdam.workstation.tasks.projects import load_projects
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

TASK = "task_01aaaaaaaaaaaaaaaaaaaaaaaa"
PROJECT_ID = "demo"
REPO_ROOT = Path(__file__).resolve().parents[1]

ACCEPTANCE_KEYS = {
    "aggregator_version",
    "availability",
    "availability_reason",
    "unavailable_cause",
    "unavailable_at_turn_number",
    "outcome",
    "counts",
    "requires_human",
    "assessment_fingerprint",
    "acceptance_fingerprint",
}


# -- builders -----------------------------------------------------------------


def criterion(identity, result, *, kind="evidence"):
    return CriterionAssessment(
        criterion_id=identity,
        source_snapshot_id="snapshot_0001",
        source_turn_number=1,
        target_turn_number=1,
        kind=kind,
        predicate=None if kind == "manual" else "path_changed",
        domain="turn_change" if kind == "evidence" else "not_applicable",
        result=result,
        reason="turn_change_evaluated",
        fingerprint="c" * 64,
    )


def resolved(items, *, fingerprint="e" * 64):
    return CurrentAssessment(
        task_id=TASK,
        target_turn_number=1,
        assessment_version=CURRENT_ASSESSMENT_VERSION,
        state=ASSESSMENT_RESOLVED,
        lineage_fingerprint="l" * 64,
        assessments=tuple(items),
        fingerprint=fingerprint,
    )


def unavailable(reason, *, cause=None, at_turn=None, fingerprint="u" * 64):
    return CurrentAssessment(
        task_id=TASK,
        target_turn_number=1,
        assessment_version=CURRENT_ASSESSMENT_VERSION,
        state=ASSESSMENT_UNAVAILABLE,
        unavailable_reason=reason,
        unavailable_cause=cause,
        unavailable_at_turn_number=at_turn,
        fingerprint=fingerprint,
    )


def published(envelope):
    """Through real JSON, because that is where a tri-state gets lost."""
    return json.loads(json.dumps(acceptance_view(aggregate(envelope))))


# -- the serializer -----------------------------------------------------------


class SerializerShape(unittest.TestCase):
    def test_the_published_keys_are_an_exact_whitelist(self):
        self.assertEqual(ACCEPTANCE_KEYS, set(published(resolved([criterion("c1", RESULT_MET)]))))

    def test_it_refuses_an_object_of_the_wrong_type(self):
        """A dict must not arrive dressed as an aggregate."""
        for wrong in ({}, None, "met", 1, resolved([])):
            with self.subTest(wrong=type(wrong).__name__):
                with self.assertRaises(TypeError):
                    acceptance_view(wrong)

    def test_the_module_publishes_by_whitelist_and_never_by_reflection(self):
        """From the AST — the module explains in prose that it does not do this."""
        tree = ast.parse(
            (REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "assessment.py")
            .read_text(encoding="utf-8")
        )
        called = {
            getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            for node in ast.walk(tree) if isinstance(node, ast.Call)
        }
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        for forbidden in ("asdict", "vars"):
            self.assertNotIn(forbidden, called)
        for forbidden in ("__dict__", "__dataclass_fields__"):
            self.assertNotIn(forbidden, attributes)

    def test_a_new_internal_field_would_not_appear_by_itself(self):
        """The whitelist is the contract; the dataclass is not."""
        tree = ast.parse(
            (REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "assessment.py")
            .read_text(encoding="utf-8")
        )
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "acceptance_view"
        )
        literals = {
            node.value
            for node in ast.walk(function)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertTrue(ACCEPTANCE_KEYS <= literals)


class AssessableOutcomes(unittest.TestCase):
    def test_all_met(self):
        body = published(resolved([criterion("c1", RESULT_MET), criterion("c2", RESULT_MET)]))
        self.assertEqual(AVAILABILITY_ASSESSABLE, body["availability"])
        self.assertIsNone(body["availability_reason"])
        self.assertEqual(OUTCOME_MET, body["outcome"])
        self.assertEqual({"total": 2, "met": 2, "not_met": 0, "unverified": 0}, body["counts"])
        self.assertIs(False, body["requires_human"])

    def test_not_met(self):
        body = published(resolved([criterion("c1", RESULT_NOT_MET), criterion("c2", RESULT_MET)]))
        self.assertEqual(OUTCOME_NOT_MET, body["outcome"])
        self.assertEqual(1, body["counts"]["not_met"])

    def test_incomplete_with_a_manual_criterion(self):
        body = published(resolved([criterion("c1", RESULT_MET),
                                   criterion("m1", RESULT_UNVERIFIED, kind="manual")]))
        self.assertEqual(OUTCOME_INCOMPLETE, body["outcome"])
        self.assertIs(True, body["requires_human"])

    def test_no_lifecycle_or_score_vocabulary_is_published(self):
        body = published(resolved([criterion("c1", RESULT_MET)]))
        for forbidden in ("passed", "failed", "success", "score", "confidence",
                          "risk", "percentage", "verdict"):
            self.assertNotIn(forbidden, body)
        self.assertNotIn(body["outcome"], ("pass", "fail", "success", "failure"))

    def test_the_fingerprints_are_published(self):
        body = published(resolved([criterion("c1", RESULT_MET)], fingerprint="a" * 64))
        self.assertEqual("a" * 64, body["assessment_fingerprint"])
        self.assertEqual(64, len(body["acceptance_fingerprint"]))
        self.assertEqual(AGGREGATOR_VERSION, body["aggregator_version"])

    def test_no_low_level_evidence_fingerprint_is_republished(self):
        body = published(resolved([criterion("c1", RESULT_MET)]))
        for forbidden in ("evaluation_fingerprint", "observation_fingerprint",
                          "lineage_fingerprint", "criteria_fingerprint",
                          "evidence_input_fingerprint"):
            self.assertNotIn(forbidden, body)


class KnownZeroVersusUnknownThroughJson(unittest.TestCase):
    """The red line, at the last place it could be lost."""

    def zero(self):
        return published(resolved([]))

    def unknown(self):
        return published(unavailable(REASON_NOT_DECLARED, at_turn=1))

    def test_known_zero_publishes_a_real_counts_object(self):
        body = self.zero()
        self.assertEqual(AVAILABILITY_NOT_ASSESSABLE, body["availability"])
        self.assertEqual(REASON_NO_STRUCTURED_CRITERIA, body["availability_reason"])
        self.assertIsNone(body["outcome"])
        self.assertEqual({"total": 0, "met": 0, "not_met": 0, "unverified": 0}, body["counts"])
        self.assertIs(False, body["requires_human"])

    def test_an_unknown_population_publishes_null_counts(self):
        body = self.unknown()
        self.assertIsNone(body["counts"])
        self.assertIsNone(body["requires_human"])

    def test_null_is_never_serialised_as_zero_or_false(self):
        body = self.unknown()
        self.assertNotEqual({"total": 0, "met": 0, "not_met": 0, "unverified": 0},
                            body["counts"])
        self.assertIsNot(False, body["requires_human"])

    def test_the_two_are_distinguishable_from_the_json_alone(self):
        """A client with only the body must be able to tell them apart."""
        zero, unknown = json.dumps(self.zero()), json.dumps(self.unknown())
        self.assertNotEqual(zero, unknown)
        self.assertIn('"counts": null', unknown)
        self.assertIn('"requires_human": null', unknown)
        self.assertNotIn('"counts": null', zero)

    def test_neither_is_ever_incomplete(self):
        for body in (self.zero(), self.unknown()):
            self.assertIsNone(body["outcome"])
            self.assertNotEqual(OUTCOME_INCOMPLETE, body["outcome"])


class ReasonFidelity(unittest.TestCase):
    def test_every_envelope_reason_survives_verbatim(self):
        for reason in SET_REASONS:
            with self.subTest(reason=reason):
                self.assertEqual(reason, published(unavailable(reason))["availability_reason"])

    def test_the_nested_cause_and_turn_survive(self):
        body = published(unavailable(REASON_PREDECESSOR_UNAVAILABLE,
                                     cause=REASON_NOT_DECLARED, at_turn=2))
        self.assertEqual(REASON_PREDECESSOR_UNAVAILABLE, body["availability_reason"])
        self.assertEqual(REASON_NOT_DECLARED, body["unavailable_cause"])
        self.assertEqual(2, body["unavailable_at_turn_number"])

    def test_the_two_nested_causes_are_different_bodies(self):
        undeclared = published(unavailable(REASON_PREDECESSOR_UNAVAILABLE,
                                           cause=REASON_NOT_DECLARED, at_turn=2))
        legacy = published(unavailable(REASON_PREDECESSOR_UNAVAILABLE,
                                       cause=REASON_LEGACY_UNKNOWN, at_turn=2))
        self.assertNotEqual(undeclared["unavailable_cause"], legacy["unavailable_cause"])
        self.assertNotEqual(undeclared["acceptance_fingerprint"],
                            legacy["acceptance_fingerprint"])

    def test_operational_reasons_are_not_assessable_rather_than_incomplete(self):
        for reason in ("turn_not_closed", "evaluation_not_recorded"):
            with self.subTest(reason=reason):
                body = published(unavailable(reason))
                self.assertEqual(AVAILABILITY_NOT_ASSESSABLE, body["availability"])
                self.assertEqual(reason, body["availability_reason"])
                self.assertIsNone(body["outcome"])

    def test_structural_reasons_keep_their_exact_code(self):
        for reason in ("final_state_inconsistent", "evaluation_inconsistent",
                       "unsupported_final_state_observer_version", "cycle_detected"):
            with self.subTest(reason=reason):
                self.assertEqual(reason, published(unavailable(reason))["availability_reason"])

    def test_no_reason_is_prettified_in_the_contract(self):
        """Human wording belongs to the client; the API carries the code."""
        body = published(unavailable(REASON_NOT_DECLARED))
        self.assertEqual(REASON_NOT_DECLARED, body["availability_reason"])
        self.assertNotIn(" ", body["availability_reason"])


# -- the envelope -------------------------------------------------------------


class AdditiveExtension(unittest.TestCase):
    def snapshot(self):
        return CriteriaSnapshot(task_id=TASK, turn_number=1, state="not_provided")

    def test_acceptance_is_optional_so_the_old_shape_remains_constructible(self):
        body = assessment_view(task_id=TASK, turn_number=1, snapshot=self.snapshot(),
                               record=None, turn_open=False)
        self.assertNotIn("acceptance", body)

    def test_adding_it_changes_nothing_else(self):
        without = assessment_view(task_id=TASK, turn_number=1, snapshot=self.snapshot(),
                                  record=None, turn_open=False)
        with_it = assessment_view(task_id=TASK, turn_number=1, snapshot=self.snapshot(),
                                  record=None, turn_open=False,
                                  acceptance=aggregate(resolved([])))
        self.assertEqual(set(without) | {"acceptance"}, set(with_it))
        for key in without:
            self.assertEqual(without[key], with_it[key])

    def test_the_api_version_did_not_move(self):
        """Additive: an existing client reading two sections is unaffected."""
        from cofferdam.workstation.tasks.assessment import ASSESSMENT_API_VERSION

        self.assertEqual(1, ASSESSMENT_API_VERSION)


class ResponseCeiling(unittest.TestCase):
    """The bound covers acceptance, because acceptance is inside it."""

    def worst_case_snapshot(self):
        from cofferdam.workstation.tasks.criteria import (
            MAX_CRITERIA_PER_TURN,
            MAX_CRITERION_PATH_CHARS,
        )

        # The real worst case the bounds allow: the maximum number of criteria,
        # each a rename carrying two paths at the maximum length. Paths must be
        # distinct — duplicates are refused — and the 255-per-segment limit binds
        # before the 512 total, so the length is made up of several segments.
        def longest(index, tail):
            segment = "%03d%s" % (index, "d" * 250)
            path = "/".join([segment, segment, tail])
            return path[:MAX_CRITERION_PATH_CHARS]

        specs = [
            {
                "kind": "evidence",
                "predicate": "rename",
                "path": longest(index, "a.py"),
                "to_path": longest(index, "b.py"),
            }
            for index in range(MAX_CRITERIA_PER_TURN)
        ]
        return validate_criteria(specs)

    def worst_case_acceptance(self):
        """The largest legitimate acceptance object: every optional field set."""
        return AcceptanceAggregate(
            task_id=TASK,
            target_turn_number=999999,
            aggregator_version=AGGREGATOR_VERSION,
            assessment_fingerprint="f" * 64,
            availability=AVAILABILITY_NOT_ASSESSABLE,
            availability_reason="unsupported_final_state_observer_version",
            unavailable_cause="supersession_predecessor_unknown",
            unavailable_at_turn_number=999999,
            outcome=OUTCOME_INCOMPLETE,
            counts=CriterionCounts(999999, 999999, 999999, 999999),
            requires_human=True,
            fingerprint="a" * 64,
        )

    def test_the_acceptance_object_is_small(self):
        self.assertLess(serialized_size(acceptance_view(self.worst_case_acceptance())), 1024)

    def test_the_worst_case_response_stays_under_the_ceiling(self):
        criteria = self.worst_case_snapshot()
        snapshot = CriteriaSnapshot(
            task_id=TASK, turn_number=1, state="present",
            snapshot_id="snapshot_" + "0" * 17,
            fingerprint="c" * 64,
            criterion_count=len(criteria),
            criteria=criteria,
        )
        body = assessment_view(
            task_id=TASK, turn_number=1, snapshot=snapshot, record=None,
            turn_open=False, acceptance=self.worst_case_acceptance(),
        )
        self.assertLess(serialized_size(body), MAX_SERIALIZED_ASSESSMENT_BYTES)

    def test_the_ceiling_constant_was_not_raised(self):
        self.assertEqual(128 * 1024, MAX_SERIALIZED_ASSESSMENT_BYTES)

    def test_the_bound_is_applied_after_acceptance_is_added(self):
        """Attaching a field after the check would make the bound describe less."""
        tree = ast.parse(
            (REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "assessment.py")
            .read_text(encoding="utf-8")
        )
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "assessment_view"
        )
        lines = [node.lineno for node in ast.walk(function)
                 if isinstance(node, ast.Constant) and node.value == "acceptance"]
        checks = [node.lineno for node in ast.walk(function)
                  if isinstance(node, ast.Call)
                  and getattr(node.func, "id", None) == "serialized_size"]
        self.assertTrue(lines and checks)
        self.assertLess(max(lines), min(checks))

    def test_an_oversized_response_fails_closed_rather_than_trimming(self):
        huge = CriteriaSnapshot(
            task_id=TASK, turn_number=1, state="present",
            snapshot_id="snapshot_" + "0" * 17,
            fingerprint="c" * 64, criterion_count=1,
            criteria=self.worst_case_snapshot(),
        )
        oversized = dict(task_id="t" * (MAX_SERIALIZED_ASSESSMENT_BYTES),
                         turn_number=1, snapshot=huge, record=None, turn_open=False,
                         acceptance=self.worst_case_acceptance())
        with self.assertRaises(AssessmentTooLarge):
            assessment_view(**oversized)


# -- one response, one snapshot -----------------------------------------------


class StoreHarness(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.root = self.home / "projects" / PROJECT_ID
        self.root.mkdir(parents=True)
        (self.root / "README.md").write_text("r\n", encoding="utf-8")

        config = load_config(self.home)
        config = type(config)(
            **{**config.__dict__, "enable_validation_task_adapter": True}
        )
        config.ensure_dirs()
        (config.config_dir / "task-projects.json").write_text(
            json.dumps({"projects": [{
                "project_id": PROJECT_ID, "display_name": "Demo",
                "root": str(self.root), "adapters": ["validation"], "enabled": True,
            }]}),
            encoding="utf-8",
        )
        self.config = config
        self.store = TaskStore(config)
        self.addCleanup(self.store.close)
        self.store.storage_health()
        self.database = self.store.path

        from cofferdam.workstation.tasks import build_registry

        adapters = build_registry(enable_validation_adapter=True)
        self.service = TaskService(
            config, self.store, adapters, projects=load_projects(config, adapters.ids())
        )
        self.task_id = new_task_id()
        with self.sql() as connection:
            connection.execute(
                "INSERT INTO tasks (task_id, correlation_id, origin, adapter_id,"
                " project_id, state, created_at, updated_at, title, prompt)"
                " VALUES (?,'cor','pwa','validation',?,'running','x','x','t','p')",
                (self.task_id, PROJECT_ID),
            )

    @contextmanager
    def sql(self):
        connection = sqlite3.connect(str(self.database))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def turn(self, specs, declaration):
        self.store.reserve_turn_criteria(
            self.task_id, validate_criteria(specs), recorded_at="x"
        )
        number = self.store.reserve_turn_continuity(
            self.task_id, validate_declaration(declaration), recorded_at="x"
        )
        self.store.mark_criteria_dispatch_started(self.task_id, number)
        self.store.mark_continuity_dispatch_started(self.task_id, number)
        with self.sql() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO task_turns (task_id, turn_number, provider,"
                " source, started_at, completed_at, outcome)"
                " VALUES (?,?,'validation','pwa','x','y','completed')",
                (self.task_id, number),
            )
            connection.execute(
                "INSERT OR IGNORE INTO task_turn_bounds (task_id, turn_number,"
                " opened_after_event_sequence, closed_through_event_sequence)"
                " VALUES (?,?,0,1)",
                (self.task_id, number),
            )
        return number


class OneResponseSnapshot(StoreHarness):
    """The race the response architecture had to close.

    The PR7 evaluation row is written after dispatch by a bounded recovery pass.
    Two separate reads could therefore straddle it, and produce one envelope
    describing a database state that never existed.
    """

    CHANGE = {"kind": "evidence", "predicate": "path_changed", "path": "a.py"}

    def test_the_store_returns_both_input_sets_from_one_call(self):
        number = self.turn([self.CHANGE], {"mode": "root"})
        result = self.store.turn_audit_inputs(self.task_id, number)
        self.assertIsNotNone(result)
        turn_open, snapshot, record, inputs = result
        self.assertFalse(turn_open)
        self.assertIsNotNone(snapshot)
        self.assertIsNotNone(inputs.resolved)

    def test_a_missing_turn_is_still_none(self):
        self.assertIsNone(self.store.turn_audit_inputs(self.task_id, 9))

    def test_the_evaluation_seen_by_both_sections_is_the_same_row(self):
        """The discriminating case, with a recovery forced mid-read.

        A writer commits the evaluation while the audit read is in flight. The
        store's lock serialises them, so the response either sees the evaluation
        in *both* sections or in neither — never one of each.
        """
        number = self.turn([self.CHANGE], {"mode": "root"})
        self.assertIsNone(self.store.evaluation(self.task_id, number))

        started = threading.Event()
        release = threading.Event()
        seen = {}

        original = self.store._current_assessment_inputs_locked

        def slow(connection, task_id, turn_number):
            # Inside the audit snapshot, after criteria and evaluation were read.
            started.set()
            release.wait(5)
            return original(connection, task_id, turn_number)

        self.store._current_assessment_inputs_locked = slow

        def read():
            seen["result"] = self.service.turn_assessment(self.task_id, number)

        reader = threading.Thread(target=read)
        reader.start()
        self.assertTrue(started.wait(5))

        def recover():
            self.service.evaluate_closed_turns(self.task_id)

        writer = threading.Thread(target=recover)
        writer.start()
        release.set()
        reader.join(10)
        writer.join(10)
        self.store._current_assessment_inputs_locked = original

        body = seen["result"]
        self.assertIsNotNone(body)
        # The one thing that must never happen: an evaluation section saying
        # nothing was recorded beside an acceptance that folded one.
        if not body["evaluation"]["recorded"]:
            self.assertEqual(
                AVAILABILITY_NOT_ASSESSABLE, body["acceptance"]["availability"]
            )
            self.assertEqual(
                "evaluation_not_recorded", body["acceptance"]["availability_reason"]
            )
        else:
            self.assertEqual(
                AVAILABILITY_ASSESSABLE, body["acceptance"]["availability"]
            )

    def test_the_sections_agree_once_the_evaluation_exists(self):
        number = self.turn([self.CHANGE], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        body = self.service.turn_assessment(self.task_id, number)
        self.assertTrue(body["evaluation"]["recorded"])
        self.assertEqual(AVAILABILITY_ASSESSABLE, body["acceptance"]["availability"])

    def test_the_sections_agree_while_it_does_not(self):
        number = self.turn([self.CHANGE], {"mode": "root"})
        body = self.service.turn_assessment(self.task_id, number)
        self.assertFalse(body["evaluation"]["recorded"])
        self.assertEqual("not_recorded", body["evaluation"]["state"])
        self.assertEqual(AVAILABILITY_NOT_ASSESSABLE, body["acceptance"]["availability"])
        self.assertEqual("evaluation_not_recorded",
                         body["acceptance"]["availability_reason"])
        self.assertIsNone(body["acceptance"]["counts"])

    def test_the_service_takes_exactly_one_store_read(self):
        """No second call to the older two-section read."""
        number = self.turn([self.CHANGE], {"mode": "root"})
        calls = []
        for name in ("turn_audit_inputs", "turn_assessment_inputs",
                     "current_assessment_inputs"):
            original = getattr(self.store, name)

            def wrap(*args, _name=name, _original=original, **kwargs):
                calls.append(_name)
                return _original(*args, **kwargs)

            setattr(self.store, name, wrap)
        self.service.turn_assessment(self.task_id, number)
        self.assertEqual(["turn_audit_inputs"], calls)


class ServiceReadIsInert(StoreHarness):
    CHANGE = {"kind": "evidence", "predicate": "path_changed", "path": "a.py"}

    def test_repeated_reads_leave_the_database_byte_identical(self):
        number = self.turn([self.CHANGE], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        self.service.turn_assessment(self.task_id, number)
        self.store.close()
        before = self.database.read_bytes()

        store = TaskStore(self.config)
        self.addCleanup(store.close)
        from cofferdam.workstation.tasks import build_registry

        adapters = build_registry(enable_validation_adapter=True)
        service = TaskService(
            self.config, store, adapters,
            projects=load_projects(self.config, adapters.ids()),
        )
        for _ in range(20):
            service.turn_assessment(self.task_id, number)
        store.close()
        self.assertEqual(before, self.database.read_bytes())

    def test_a_read_never_creates_the_missing_evaluation(self):
        """GET must not 'fix' a turn awaiting recovery."""
        number = self.turn([self.CHANGE], {"mode": "root"})
        for _ in range(5):
            self.service.turn_assessment(self.task_id, number)
        self.assertIsNone(self.store.evaluation(self.task_id, number))

    def test_no_schema_change_and_no_new_table(self):
        with self.sql() as connection:
            names = {
                row["name"] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()["value"]
        self.assertEqual(11, int(version))
        self.assertEqual(11, SCHEMA_VERSION)
        for forbidden in ("task_turn_acceptance", "task_acceptance", "acceptance"):
            self.assertNotIn(forbidden, names)


class NegativeSpaceTests(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(11, SCHEMA_VERSION)
        self.assertEqual(3, CURRENT_ASSESSMENT_VERSION)
        self.assertEqual(1, AGGREGATOR_VERSION)

    def test_no_global_task_verdict_is_published(self):
        body = published(resolved([criterion("c1", RESULT_MET)]))
        for forbidden in ("task_outcome", "task_verdict", "task_passed", "overall",
                          "latest", "merge_ready", "deploy_ready", "project"):
            self.assertNotIn(forbidden, body)

    def test_the_serializer_computes_no_acceptance_semantics(self):
        """It is a whitelist. The fold lives one layer down."""
        tree = ast.parse(
            (REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "assessment.py")
            .read_text(encoding="utf-8")
        )
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "acceptance_view"
        )
        called = {
            getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            for node in ast.walk(function) if isinstance(node, ast.Call)
        }
        for forbidden in ("aggregate", "bind", "resolve", "evaluate", "count"):
            self.assertNotIn(forbidden, called)

    def test_no_write_route_or_control_was_added(self):
        service = (
            REPO_ROOT / "cofferdam" / "workstation" / "service.py"
        ).read_text(encoding="utf-8")
        for forbidden in ("acceptance_override", "mark_met", "approve_acceptance",
                          "rerun_assessment", "dismiss_acceptance"):
            self.assertNotIn(forbidden, service)

    def test_no_named_check_or_runner_appeared(self):
        from cofferdam.workstation.tasks import acceptance as module

        for forbidden in ("run_check", "named_check", "CheckRunner", "check_id"):
            self.assertFalse(hasattr(module, forbidden))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
