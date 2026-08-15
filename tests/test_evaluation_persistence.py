"""M2K PR7 — what a stored evaluation binds, and what it refuses to become.

An evaluation is a judgement made by one evaluator version against two exact
frozen identities. Everything here is about keeping it that way: the identities
are stored in full, the fingerprint is derived from them and not from a clock,
the parent and its results are one write, and nothing overwrites anything.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.criteria import (
    CRITERIA_NOT_PROVIDED,
    CRITERIA_PRESENT,
    validate_criteria,
)
from cofferdam.workstation.tasks.evaluation import (
    EVALUATOR_VERSION,
    FINGERPRINT_CHARS,
    REASON_MACHINE_CHANGE_OBSERVED,
    REASON_MANUAL,
    RESULT_MET,
    RESULT_UNVERIFIED,
    CriterionResult,
    evaluate,
    evaluation_fingerprint,
    valid_evaluation_id,
)
from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION
from cofferdam.workstation.tasks.store import TaskStore

from .test_evaluation_predicates import (
    CLEAN_RANGE,
    bundle,
    observation,
)
from cofferdam.workstation.tasks.evidence import OBSERVATION_DOMAIN_WORKTREE

CRITERIA = [
    {"kind": "evidence", "predicate": "path_changed", "path": "src/a.py"},
    {"kind": "manual", "description": "a person confirms the page renders"},
]


class EvaluationStoreCase(unittest.TestCase):
    """A real store, with a closed bounded turn and a criteria snapshot."""

    def setUp(self):
        from cofferdam.workstation.config import load_config

        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr7-")
        self.addCleanup(self._temp.cleanup)
        self.home = Path(self._temp.name)
        config = load_config(self.home)
        config.ensure_dirs()
        self.config = config
        self.store = TaskStore(config)
        self.addCleanup(self.store.close)
        self.store.storage_health()
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"
        self._seed()

    def _seed(self, task_id="task_x", turn_number=1, criteria=None, closed=True):
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "INSERT OR IGNORE INTO tasks (task_id,correlation_id,origin,adapter_id,"
                "project_id,state,created_at,updated_at,prompt,event_cursor) VALUES"
                " (?,'c','pwa','validation','p','completed','x','x','p',20)",
                (task_id,),
            )
        self.store.reserve_turn_criteria(
            task_id,
            validate_criteria(CRITERIA if criteria is None else criteria),
            recorded_at="2026-08-15T00:00:00Z",
        )
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "INSERT INTO task_turns (task_id,turn_number,provider,source,started_at,"
                "completed_at,outcome) VALUES (?,?,'validation','pwa','x',?,?)",
                (task_id, turn_number, "y" if closed else None, "completed" if closed else None),
            )
            db.execute(
                "INSERT INTO task_turn_bounds (task_id,turn_number,"
                "opened_after_event_sequence,closed_through_event_sequence)"
                " VALUES (?,?,0,?)",
                (task_id, turn_number, 20 if closed else None),
            )
        return task_id, turn_number

    def snapshot(self, task_id="task_x", turn_number=1):
        return self.store.turn_criteria(task_id, turn_number)

    def record(self, task_id="task_x", turn_number=1, found=None):
        snap = self.snapshot(task_id, turn_number)
        found = found or bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        results = evaluate(snap, found)
        return self.store.record_evaluation(
            snapshot=snap, bundle=found, results=results, recorded_at="2026-08-15T01:00:00Z"
        )


class IdentityBinding(EvaluationStoreCase):
    def test_the_record_binds_every_required_identity(self):
        stored = self.record()
        snap = self.snapshot()
        self.assertEqual(stored.task_id, "task_x")
        self.assertEqual(stored.turn_number, 1)
        self.assertEqual(stored.evaluator_version, EVALUATOR_VERSION)
        self.assertEqual(stored.criteria_state, CRITERIA_PRESENT)
        self.assertEqual(stored.criteria_snapshot_id, snap.snapshot_id)
        self.assertEqual(stored.criteria_fingerprint, snap.fingerprint)
        self.assertEqual(stored.assembler_version, ASSEMBLER_VERSION)
        self.assertEqual(len(stored.evidence_input_fingerprint), 64)

    def test_it_binds_both_the_snapshot_id_and_the_criteria_fingerprint(self):
        """Different audit questions: which row was read, and what it contained."""
        stored = self.record()
        self.assertTrue(stored.criteria_snapshot_id.startswith("acs_"))
        self.assertNotEqual(stored.criteria_snapshot_id, stored.criteria_fingerprint)

    def test_the_evaluation_id_is_server_minted(self):
        stored = self.record()
        self.assertTrue(valid_evaluation_id(stored.evaluation_id))
        self.assertTrue(stored.evaluation_id.startswith("evl_"))

    def test_result_count_matches_the_children(self):
        stored = self.record()
        self.assertEqual(stored.result_count, 2)
        self.assertEqual(len(stored.results), 2)
        with sqlite3.connect(str(self.path)) as db:
            rows = db.execute(
                "SELECT COUNT(*) FROM task_turn_criterion_results"
            ).fetchone()[0]
        self.assertEqual(rows, 2)

    def test_each_result_names_its_exact_criterion_row(self):
        stored = self.record()
        criteria = {c.criterion_id for c in self.snapshot().criteria}
        self.assertEqual({r.criterion_id for r in stored.results}, criteria)

    def test_results_are_ordered_by_stored_ordinal(self):
        stored = self.record()
        self.assertEqual([r.ordinal for r in stored.results], [1, 2])
        self.assertEqual(
            [r.result for r in stored.results], [RESULT_MET, RESULT_UNVERIFIED]
        )
        self.assertEqual(
            [r.reason for r in stored.results],
            [REASON_MACHINE_CHANGE_OBSERVED, REASON_MANUAL],
        )

    def test_there_is_no_aggregate_column(self):
        with sqlite3.connect(str(self.path)) as db:
            columns = {
                r[1].lower() for r in db.execute("PRAGMA table_info(task_turn_evaluations)")
            }
        for forbidden in (
            "passed", "failed", "success", "verdict", "outcome", "score",
            "confidence", "risk", "risk_level", "aggregate", "met_count",
        ):
            self.assertNotIn(forbidden, columns)

    def test_there_is_no_explanation_column(self):
        with sqlite3.connect(str(self.path)) as db:
            columns = {
                r[1].lower()
                for r in db.execute("PRAGMA table_info(task_turn_criterion_results)")
            }
        for forbidden in ("explanation", "narrative", "summary", "detail", "message", "text"):
            self.assertNotIn(forbidden, columns)

    def test_the_evidence_bundle_is_not_copied_in(self):
        """Identified by fingerprint, never duplicated."""
        with sqlite3.connect(str(self.path)) as db:
            columns = {
                r[1].lower() for r in db.execute("PRAGMA table_info(task_turn_evaluations)")
            }
        for forbidden in ("observations", "claims", "bundle", "evidence_json", "relationships"):
            self.assertNotIn(forbidden, columns)


class Fingerprint(EvaluationStoreCase):
    def test_it_is_a_sha256_hex_digest(self):
        stored = self.record()
        self.assertEqual(len(stored.evaluation_fingerprint), FINGERPRINT_CHARS)
        int(stored.evaluation_fingerprint, 16)

    def test_it_is_reproducible_from_the_same_inputs(self):
        stored = self.record()
        snap = self.snapshot()
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        again = evaluation_fingerprint(
            snapshot=snap, bundle=found, results=evaluate(snap, found)
        )
        self.assertEqual(stored.evaluation_fingerprint, again)

    def test_it_survives_a_store_reopen(self):
        stored = self.record()
        self.store.close()
        self.store = TaskStore(self.config)
        reread = self.store.evaluation("task_x", 1)
        self.assertEqual(reread.evaluation_fingerprint, stored.evaluation_fingerprint)
        self.assertEqual(reread.evaluation_id, stored.evaluation_id)

    def test_it_ignores_recorded_at(self):
        snap = self.snapshot()
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        results = evaluate(snap, found)
        first = evaluation_fingerprint(snapshot=snap, bundle=found, results=results)
        self.store.record_evaluation(
            snapshot=snap, bundle=found, results=results, recorded_at="2999-01-01T00:00:00Z"
        )
        self.assertEqual(self.store.evaluation("task_x", 1).evaluation_fingerprint, first)

    def test_it_ignores_the_evaluation_id(self):
        import inspect

        parameters = set(inspect.signature(evaluation_fingerprint).parameters)
        self.assertEqual(parameters, {"snapshot", "bundle", "results", "evaluator_version"})

    def test_it_changes_with_the_evaluator_version(self):
        snap = self.snapshot()
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        results = evaluate(snap, found)
        one = evaluation_fingerprint(snapshot=snap, bundle=found, results=results)
        two = evaluation_fingerprint(
            snapshot=snap, bundle=found, results=results, evaluator_version=2
        )
        self.assertNotEqual(one, two)

    def test_it_changes_with_the_evidence_identity(self):
        snap = self.snapshot()
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        results = evaluate(snap, found)
        base = evaluation_fingerprint(snapshot=snap, bundle=found, results=results)
        moved = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),),
            input_fingerprint="e" * 64,
        )
        self.assertNotEqual(
            base, evaluation_fingerprint(snapshot=snap, bundle=moved, results=results)
        )

    def test_it_changes_with_a_result_or_a_reason(self):
        snap = self.snapshot()
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        results = list(evaluate(snap, found))
        base = evaluation_fingerprint(snapshot=snap, bundle=found, results=results)
        flipped = list(results)
        flipped[0] = CriterionResult(
            results[0].criterion_id, 1, RESULT_UNVERIFIED, REASON_MANUAL
        )
        self.assertNotEqual(
            base, evaluation_fingerprint(snapshot=snap, bundle=found, results=flipped)
        )
        reworded = list(results)
        reworded[1] = CriterionResult(
            results[1].criterion_id, 2, RESULT_UNVERIFIED, "unsupported_capability"
        )
        self.assertNotEqual(
            base, evaluation_fingerprint(snapshot=snap, bundle=found, results=reworded)
        )

    def test_it_distinguishes_present_from_not_provided(self):
        self._seed(task_id="task_np", criteria=[], closed=True)
        empty = self.snapshot("task_np")
        self.assertEqual(empty.state, CRITERIA_NOT_PROVIDED)
        found = bundle()
        self.assertNotEqual(
            evaluation_fingerprint(snapshot=empty, bundle=found, results=()),
            evaluation_fingerprint(
                snapshot=self.snapshot(), bundle=found, results=()
            ),
        )


class Atomicity(EvaluationStoreCase):
    def test_a_failing_child_insert_leaves_no_parent(self):
        snap = self.snapshot()
        found = bundle()
        broken = (
            CriterionResult(snap.criteria[0].criterion_id, 1, RESULT_MET, REASON_MACHINE_CHANGE_OBSERVED),
            # A criterion id that does not exist: the child FK refuses it.
            CriterionResult("acr_nonexistent", 2, RESULT_UNVERIFIED, REASON_MANUAL),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.record_evaluation(
                snapshot=snap, bundle=found, results=broken, recorded_at="x"
            )
        with sqlite3.connect(str(self.path)) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM task_turn_evaluations").fetchone()[0], 0
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM task_turn_criterion_results").fetchone()[0],
                0,
            )
        self.assertIsNone(self.store.evaluation("task_x", 1))

    def test_an_invalid_result_is_refused_before_anything_is_written(self):
        from cofferdam.workstation.tasks.errors import TaskError

        snap = self.snapshot()
        bad = (
            CriterionResult(snap.criteria[0].criterion_id, 1, "failed", REASON_MANUAL),
            CriterionResult(snap.criteria[1].criterion_id, 2, RESULT_UNVERIFIED, REASON_MANUAL),
        )
        with self.assertRaises(TaskError):
            self.store.record_evaluation(
                snapshot=snap, bundle=bundle(), results=bad, recorded_at="x"
            )
        self.assertIsNone(self.store.evaluation("task_x", 1))

    def test_a_reason_that_does_not_match_its_result_is_refused(self):
        from cofferdam.workstation.tasks.errors import TaskError

        snap = self.snapshot()
        mismatched = (
            # `met` with an unverified-only reason.
            CriterionResult(snap.criteria[0].criterion_id, 1, RESULT_MET, REASON_MANUAL),
            CriterionResult(snap.criteria[1].criterion_id, 2, RESULT_UNVERIFIED, REASON_MANUAL),
        )
        with self.assertRaises(TaskError):
            self.store.record_evaluation(
                snapshot=snap, bundle=bundle(), results=mismatched, recorded_at="x"
            )
        self.assertIsNone(self.store.evaluation("task_x", 1))

    def test_a_result_set_that_does_not_answer_every_criterion_is_refused(self):
        from cofferdam.workstation.tasks.errors import TaskError

        snap = self.snapshot()
        short = (
            CriterionResult(snap.criteria[0].criterion_id, 1, RESULT_MET, REASON_MACHINE_CHANGE_OBSERVED),
        )
        with self.assertRaises(TaskError):
            self.store.record_evaluation(
                snapshot=snap, bundle=bundle(), results=short, recorded_at="x"
            )
        self.assertIsNone(self.store.evaluation("task_x", 1))


class Immutability(EvaluationStoreCase):
    def test_an_exact_retry_is_idempotent_and_writes_nothing(self):
        first = self.record()
        again = self.record()
        self.assertIsNone(again, "a retry writes nothing")
        stored = self.store.evaluation("task_x", 1)
        self.assertEqual(stored.evaluation_id, first.evaluation_id)
        self.assertEqual(stored.evaluation_fingerprint, first.evaluation_fingerprint)
        self.assertEqual(stored.evidence_input_fingerprint, first.evidence_input_fingerprint)
        self.assertEqual(
            [(r.ordinal, r.result, r.reason) for r in stored.results],
            [(r.ordinal, r.result, r.reason) for r in first.results],
        )

    def test_only_one_row_exists_per_turn_and_evaluator_version(self):
        self.record()
        self.record()
        self.record()
        with sqlite3.connect(str(self.path)) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM task_turn_evaluations").fetchone()[0], 1
            )

    def test_the_uniqueness_constraint_is_per_evaluator_version(self):
        """A future version 2 records its own answer without rewriting version 1."""
        self.record()
        stored = self.store.evaluation("task_x", 1)
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute(
                "INSERT INTO task_turn_evaluations (evaluation_id,task_id,turn_number,"
                "evaluator_version,criteria_state,criteria_snapshot_id,criteria_fingerprint,"
                "assembler_version,evidence_input_fingerprint,result_count,"
                "evaluation_fingerprint,recorded_at) VALUES"
                " ('evl_v2','task_x',1,2,'present',?,?,3,?,1,?, 'x')",
                (stored.criteria_snapshot_id, stored.criteria_fingerprint, "f" * 64, "d" * 64),
            )
            count = db.execute("SELECT COUNT(*) FROM task_turn_evaluations").fetchone()[0]
        self.assertEqual(count, 2)
        # And version 1's record is untouched and still the one this build reads.
        self.assertEqual(self.store.evaluation("task_x", 1).evaluator_version, 1)
        self.assertEqual(
            self.store.evaluation("task_x", 1, evaluator_version=2).evaluation_id, "evl_v2"
        )

    def test_a_duplicate_row_for_the_same_version_is_refused_by_the_database(self):
        self.record()
        stored = self.store.evaluation("task_x", 1)
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO task_turn_evaluations (evaluation_id,task_id,turn_number,"
                    "evaluator_version,criteria_state,criteria_snapshot_id,"
                    "criteria_fingerprint,assembler_version,evidence_input_fingerprint,"
                    "result_count,evaluation_fingerprint,recorded_at) VALUES"
                    " ('evl_dup','task_x',1,1,'present',?,?,3,?,1,?, 'x')",
                    (stored.criteria_snapshot_id, stored.criteria_fingerprint, "f" * 64, "d" * 64),
                )


class ConflictingRetries(EvaluationStoreCase):
    """An identical retry and a conflicting one are different events.

    The inputs to an evaluation are immutable by construction, so a second
    derivation of the same turn *is* the same judgement. If it is not, one of the
    things this milestone spent five PRs making impossible has happened — and
    both of the easy answers are wrong. Returning the stored record would report
    a success that did not occur; overwriting it would destroy the evidence that
    anything is wrong. So it fails closed and changes nothing.
    """

    def setUp(self):
        super().setUp()
        self.first = self.record()
        self.before = self._raw()

    def _raw(self):
        with sqlite3.connect(str(self.path)) as db:
            db.row_factory = sqlite3.Row
            return (
                [dict(r) for r in db.execute("SELECT * FROM task_turn_evaluations")],
                [dict(r) for r in db.execute(
                    "SELECT * FROM task_turn_criterion_results ORDER BY ordinal")],
            )

    def _conflict(self, **kwargs):
        from cofferdam.workstation.tasks.errors import EvaluationConflict

        with self.assertRaises(EvaluationConflict) as caught:
            self.store.record_evaluation(recorded_at="2027-01-01T00:00:00Z", **kwargs)
        # And nothing moved.
        self.assertEqual(self._raw(), self.before)
        return caught.exception

    def _snapshot_with(self, **overrides):
        snap = self.snapshot()
        fields = {f: getattr(snap, f) for f in snap.__dataclass_fields__}
        fields.update(overrides)
        return type(snap)(**fields)

    def test_a_different_evidence_input_fingerprint_is_refused(self):
        snap = self.snapshot()
        moved = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),),
            input_fingerprint="e" * 64,
        )
        error = self._conflict(
            snapshot=snap, bundle=moved, results=evaluate(snap, moved)
        )
        self.assertIn("evidence_input_fingerprint", error.detail)

    def test_a_different_criteria_fingerprint_is_refused(self):
        snap = self._snapshot_with(fingerprint="a" * 64)
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        error = self._conflict(
            snapshot=snap, bundle=found, results=evaluate(snap, found)
        )
        self.assertIn("criteria_fingerprint", error.detail)

    def test_a_different_snapshot_id_is_refused(self):
        snap = self._snapshot_with(snapshot_id="acs_" + "z" * 26)
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        error = self._conflict(
            snapshot=snap, bundle=found, results=evaluate(snap, found)
        )
        self.assertIn("criteria_snapshot_id", error.detail)

    def test_a_different_assembler_version_is_refused(self):
        snap = self.snapshot()
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        moved = type(found)(
            **{**{f: getattr(found, f) for f in found.__dataclass_fields__},
               "assembler_version": 4}
        )
        error = self._conflict(
            snapshot=snap, bundle=moved, results=evaluate(snap, moved)
        )
        self.assertIn("assembler_version", error.detail)

    def test_a_changed_result_is_refused(self):
        snap = self.snapshot()
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        flipped = list(evaluate(snap, found))
        flipped[0] = CriterionResult(
            flipped[0].criterion_id, 1, RESULT_UNVERIFIED, REASON_MANUAL
        )
        error = self._conflict(snapshot=snap, bundle=found, results=flipped)
        # The fingerprint covers the results, so it is the first field to move.
        self.assertIn("evaluation_fingerprint", error.detail)

    def test_a_changed_reason_is_refused(self):
        snap = self.snapshot()
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        reworded = list(evaluate(snap, found))
        reworded[1] = CriterionResult(
            reworded[1].criterion_id, 2, RESULT_UNVERIFIED, "unsupported_capability"
        )
        error = self._conflict(snapshot=snap, bundle=found, results=reworded)
        self.assertIn("evaluation_fingerprint", error.detail)

    def test_a_changed_result_row_alone_is_refused(self):
        """Results differing while the fingerprint is held fixed.

        Reached by monkeypatching the fingerprint so the row comparison is the
        thing under test rather than the hash. It proves the store compares the
        rows themselves and does not rely on the digest to notice.
        """
        from cofferdam.workstation.tasks import store as store_module
        from cofferdam.workstation.tasks.errors import EvaluationConflict

        snap = self.snapshot()
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        frozen = self.first.evaluation_fingerprint
        flipped = list(evaluate(snap, found))
        flipped[0] = CriterionResult(
            flipped[0].criterion_id, 1, RESULT_UNVERIFIED, REASON_MANUAL
        )
        import cofferdam.workstation.tasks.evaluation as evaluation_module

        original = evaluation_module.evaluation_fingerprint
        evaluation_module.evaluation_fingerprint = lambda **kwargs: frozen
        try:
            with self.assertRaises(EvaluationConflict) as caught:
                self.store.record_evaluation(
                    snapshot=snap, bundle=found, results=flipped, recorded_at="x"
                )
        finally:
            evaluation_module.evaluation_fingerprint = original
        self.assertIn("criterion_results", caught.exception.detail)
        self.assertEqual(self._raw(), self.before)

    def test_the_refusal_names_the_dimension_and_never_a_value(self):
        snap = self._snapshot_with(fingerprint="a" * 64)
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        error = self._conflict(snapshot=snap, bundle=found, results=evaluate(snap, found))
        self.assertNotIn("a" * 64, error.detail)
        self.assertNotIn(self.first.criteria_fingerprint, error.detail)

    def test_an_exact_retry_after_a_restart_is_still_idempotent(self):
        self.store.close()
        self.store = TaskStore(self.config)
        again = self.record()
        self.assertIsNone(again)
        stored = self.store.evaluation("task_x", 1)
        self.assertEqual(stored.evaluation_id, self.first.evaluation_id)
        self.assertEqual(self._raw(), self.before)

    def test_a_conflicting_retry_after_a_restart_is_still_refused(self):
        self.store.close()
        self.store = TaskStore(self.config)
        snap = self.snapshot()
        moved = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),),
            input_fingerprint="e" * 64,
        )
        self._conflict(snapshot=snap, bundle=moved, results=evaluate(snap, moved))

    def test_the_original_record_is_untouched_after_every_refusal(self):
        snap = self.snapshot()
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        for kwargs in (
            {"snapshot": self._snapshot_with(snapshot_id="acs_" + "z" * 26)},
            {"snapshot": self._snapshot_with(fingerprint="a" * 64)},
            {"bundle": bundle(input_fingerprint="e" * 64)},
        ):
            payload = {"snapshot": snap, "bundle": found}
            payload.update(kwargs)
            payload["results"] = evaluate(payload["snapshot"], payload["bundle"])
            self._conflict(**payload)
        stored = self.store.evaluation("task_x", 1)
        self.assertEqual(stored.evaluation_id, self.first.evaluation_id)
        self.assertEqual(stored.evaluation_fingerprint, self.first.evaluation_fingerprint)
        self.assertEqual(self._raw(), self.before)

    def test_exactly_one_row_survives_all_of_it(self):
        with sqlite3.connect(str(self.path)) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM task_turn_evaluations").fetchone()[0], 1
            )


class Refusals(EvaluationStoreCase):
    def test_an_open_turn_is_never_evaluated(self):
        from cofferdam.workstation.tasks.errors import TaskError

        self._seed(task_id="task_open", closed=False)
        snap = self.snapshot("task_open")
        with self.assertRaises(TaskError):
            self.store.record_evaluation(
                snapshot=snap,
                bundle=bundle(),
                results=evaluate(snap, bundle()),
                recorded_at="x",
            )
        self.assertIsNone(self.store.evaluation("task_open", 1))

    def test_a_legacy_unknown_snapshot_produces_no_record(self):
        from cofferdam.workstation.tasks.criteria import CriteriaSnapshot

        legacy = CriteriaSnapshot(task_id="task_x", turn_number=1, state="legacy_unknown")
        self.assertIsNone(
            self.store.record_evaluation(
                snapshot=legacy, bundle=bundle(), results=(), recorded_at="x"
            )
        )
        self.assertIsNone(self.store.evaluation("task_x", 1))

    def test_a_not_provided_snapshot_records_zero_results_and_no_aggregate(self):
        self._seed(task_id="task_np", criteria=[], closed=True)
        snap = self.snapshot("task_np")
        stored = self.store.record_evaluation(
            snapshot=snap, bundle=bundle(), results=(), recorded_at="x"
        )
        self.assertEqual(stored.criteria_state, CRITERIA_NOT_PROVIDED)
        self.assertEqual(stored.result_count, 0)
        self.assertEqual(stored.results, ())
        self.assertFalse(stored.decided)
        # And nothing in the row can be read as a pass.
        blob = repr(stored).lower()
        for forbidden in ("passed", "success", "verdict"):
            self.assertNotIn(forbidden, blob)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
