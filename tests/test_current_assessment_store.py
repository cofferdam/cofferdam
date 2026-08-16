"""M2K PR16 — the read that feeds the binder, against a real database.

Covers what constructed values cannot: the target turn's lifecycle, the one-
snapshot read, a PR7 evaluation that is genuinely absent, stored rows corrupted
past what the service would ever write, and the promise that asking costs the
database nothing.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from cofferdam.workstation.config import load_config
from cofferdam.workstation.tasks.binding import (
    ASSESSMENT_RESOLVED,
    ASSESSMENT_UNAVAILABLE,
    DOMAIN_TURN_CHANGE,
    REASON_EVALUATION_INCONSISTENT,
    REASON_EVALUATION_NOT_RECORDED,
    REASON_LINEAGE_UNAVAILABLE,
    REASON_TURN_CHANGE_EVALUATED,
    REASON_TURN_NOT_CLOSED,
    REASON_UNSUPPORTED_EVALUATOR,
)
from cofferdam.workstation.tasks.continuity import validate_declaration
from cofferdam.workstation.tasks.criteria import validate_criteria
from cofferdam.workstation.tasks.evaluation import EVALUATOR_VERSION, RESULT_UNVERIFIED
from cofferdam.workstation.tasks.identity import new_task_id
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

PROJECT_ID = "demo"


class AssessmentStoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.root = self.home / "projects" / PROJECT_ID
        self.root.mkdir(parents=True)
        (self.root / "README.md").write_text("a repository\n", encoding="utf-8")

        config = load_config(self.home)
        config = type(config)(
            **{**config.__dict__, "enable_validation_task_adapter": True}
        )
        config.ensure_dirs()
        (config.config_dir / "task-projects.json").write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "project_id": PROJECT_ID,
                            "display_name": "Demo",
                            "root": str(self.root),
                            "adapters": ["validation"],
                            "enabled": True,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.config = config
        self.database = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.store = TaskStore(config)
        self.addCleanup(self.store.close)

        from cofferdam.workstation.tasks import build_registry
        from cofferdam.workstation.tasks.projects import load_projects

        adapters = build_registry(enable_validation_adapter=True)
        self.service = TaskService(
            config,
            self.store,
            adapters,
            projects=load_projects(config, adapters.ids()),
        )
        self.task_id = new_task_id()
        self.store.storage_health()
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

    def change(self, label):
        return {"kind": "evidence", "predicate": "path_changed", "path": "%s.py" % label}

    def turn(self, specs, declaration, *, close=True):
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
                " VALUES (?,?,'validation','pwa','x',?,?)",
                (
                    self.task_id,
                    number,
                    "y" if close else None,
                    "completed" if close else None,
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO task_turn_bounds (task_id, turn_number,"
                " opened_after_event_sequence, closed_through_event_sequence)"
                " VALUES (?,?,0,?)",
                (self.task_id, number, 1 if close else None),
            )
        return number

    def assess(self, turn_number):
        return self.service.current_criterion_assessment(self.task_id, turn_number)

    def digest(self):
        return hashlib.sha256(self.database.read_bytes()).hexdigest()


class TargetTurnLifecycleTests(AssessmentStoreCase):
    def test_a_turn_that_does_not_exist_is_not_closed(self):
        answer = self.assess(9)
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(REASON_TURN_NOT_CLOSED, answer.unavailable_reason)

    def test_a_reserved_but_unopened_turn_has_no_assessment(self):
        """Criteria are frozen, but no turn row exists yet."""
        self.store.reserve_turn_criteria(
            self.task_id, validate_criteria([self.change("a")]), recorded_at="x"
        )
        number = self.store.reserve_turn_continuity(
            self.task_id, validate_declaration({"mode": "root"}), recorded_at="x"
        )
        answer = self.assess(number)
        self.assertEqual(REASON_TURN_NOT_CLOSED, answer.unavailable_reason)

    def test_an_open_turn_has_no_assessment(self):
        number = self.turn([self.change("a")], {"mode": "root"}, close=False)
        answer = self.assess(number)
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(REASON_TURN_NOT_CLOSED, answer.unavailable_reason)

    def test_a_closed_turn_with_its_evaluation_resolves(self):
        number = self.turn([self.change("a")], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        answer = self.assess(number)
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(1, answer.criterion_count)
        self.assertEqual(DOMAIN_TURN_CHANGE, answer.assessments[0].domain)


class EvaluationAbsenceTests(AssessmentStoreCase):
    def test_a_closed_turn_awaiting_evaluation_is_operationally_unavailable(self):
        number = self.turn([self.change("a")], {"mode": "root"})
        self.assertIsNone(self.store.evaluation(self.task_id, number))
        answer = self.assess(number)
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(REASON_EVALUATION_NOT_RECORDED, answer.unavailable_reason)

    def test_it_never_becomes_not_met(self):
        number = self.turn([self.change("a")], {"mode": "root"})
        answer = self.assess(number)
        self.assertEqual((), answer.assessments)

    def test_the_read_does_not_run_the_evaluation_it_is_missing(self):
        number = self.turn([self.change("a")], {"mode": "root"})
        self.assess(number)
        self.assertIsNone(self.store.evaluation(self.task_id, number))

    def test_it_resolves_once_the_recovery_pass_records_one(self):
        number = self.turn([self.change("a")], {"mode": "root"})
        self.assertEqual(
            REASON_EVALUATION_NOT_RECORDED, self.assess(number).unavailable_reason
        )
        self.service.evaluate_closed_turns(self.task_id)
        self.assertEqual(ASSESSMENT_RESOLVED, self.assess(number).state)

    def test_a_manual_only_turn_resolves_with_no_evaluation_at_all(self):
        number = self.turn(
            [{"kind": "manual", "description": "look at it"}], {"mode": "root"}
        )
        answer = self.assess(number)
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(RESULT_UNVERIFIED, answer.assessments[0].result)

    def test_a_turn_whose_active_set_is_entirely_inherited_needs_no_evaluation(self):
        first = self.turn([self.change("a")], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        snapshot = self.store.turn_criteria(self.task_id, first).snapshot_id
        second = self.turn(
            [], {"mode": "extend", "predecessor_snapshot_id": snapshot}
        )
        # Turn 2 adds nothing of its own, so nothing needs a turn-2 judgement.
        answer = self.assess(second)
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(1, answer.criterion_count)
        self.assertTrue(answer.assessments[0].inherited)


class UnknownLineageTests(AssessmentStoreCase):
    def test_a_turn_with_no_continuity_declaration_is_unavailable(self):
        self.store.reserve_turn_criteria(
            self.task_id, validate_criteria([self.change("a")]), recorded_at="x"
        )
        with self.sql() as connection:
            connection.execute(
                "INSERT INTO task_turns (task_id, turn_number, provider, source,"
                " started_at, completed_at, outcome)"
                " VALUES (?,1,'validation','pwa','x','y','completed')",
                (self.task_id,),
            )
        answer = self.assess(1)
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(REASON_LINEAGE_UNAVAILABLE, answer.unavailable_reason)
        self.assertEqual((), answer.assessments)

    def test_a_legacy_turn_with_no_criteria_at_all_is_unavailable(self):
        with self.sql() as connection:
            connection.execute(
                "INSERT INTO task_turns (task_id, turn_number, provider, source,"
                " started_at, completed_at, outcome)"
                " VALUES (?,1,'validation','pwa','x','y','completed')",
                (self.task_id,),
            )
        answer = self.assess(1)
        self.assertEqual(REASON_LINEAGE_UNAVAILABLE, answer.unavailable_reason)


class CorruptedEvaluationTests(AssessmentStoreCase):
    """Rows the service would never write, inserted with raw SQL.

    PR15 established that the DDL permits several of these. Each must fail closed
    and none may be repaired on read.
    """

    def prepared(self):
        number = self.turn([self.change("a")], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        self.assertEqual(ASSESSMENT_RESOLVED, self.assess(number).state)
        return number

    def test_an_evaluation_pointing_at_another_snapshot_is_refused(self):
        """A real snapshot from a real other turn — the FK is satisfied and the
        row is still a lie, which is precisely PR15's finding."""
        number = self.prepared()
        second = self.turn([self.change("b")], {"mode": "replace",
                                                "predecessor_snapshot_id":
                                                self.store.turn_criteria(
                                                    self.task_id, number).snapshot_id})
        other_snapshot = self.store.turn_criteria(self.task_id, second).snapshot_id
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_evaluations SET criteria_snapshot_id = ?"
                " WHERE task_id = ? AND turn_number = ?",
                (other_snapshot, self.task_id, number),
            )
        answer = self.assess(number)
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(REASON_EVALUATION_INCONSISTENT, answer.unavailable_reason)

    def test_a_result_count_that_disagrees_is_refused(self):
        number = self.prepared()
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_evaluations SET result_count = 9 WHERE task_id = ?",
                (self.task_id,),
            )
        self.assertEqual(
            REASON_EVALUATION_INCONSISTENT, self.assess(number).unavailable_reason
        )

    def test_a_missing_result_for_the_target_criterion_is_refused(self):
        """Two criteria, one answer removed, the count corrected to match.

        Every constraint the database can express is satisfied — the count agrees
        with the rows carried — and the record still fails to answer a criterion
        it claims to cover. Only the service-owned invariant catches it.
        """
        number = self.turn(
            [self.change("a"), self.change("b")], {"mode": "root"}
        )
        self.service.evaluate_closed_turns(self.task_id)
        self.assertEqual(ASSESSMENT_RESOLVED, self.assess(number).state)
        target = self.store.turn_criteria(self.task_id, number).criteria[0]
        with self.sql() as connection:
            connection.execute(
                "DELETE FROM task_turn_criterion_results WHERE criterion_id = ?",
                (target.criterion_id,),
            )
            connection.execute(
                "UPDATE task_turn_evaluations SET result_count = 1 WHERE task_id = ?",
                (self.task_id,),
            )
        self.assertEqual(
            REASON_EVALUATION_INCONSISTENT, self.assess(number).unavailable_reason
        )

    def test_an_unsupported_evaluator_version_is_refused_distinctly(self):
        number = self.prepared()
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_evaluations SET evaluator_version = ? WHERE task_id = ?",
                (EVALUATOR_VERSION + 7, self.task_id),
            )
        answer = self.assess(number)
        self.assertEqual(REASON_UNSUPPORTED_EVALUATOR, answer.unavailable_reason)

    def test_a_newer_evaluator_is_distinguished_from_no_evaluation(self):
        """The two must not collapse: only one of them changes by waiting."""
        number = self.prepared()
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_evaluations SET evaluator_version = ? WHERE task_id = ?",
                (EVALUATOR_VERSION + 7, self.task_id),
            )
        self.assertNotEqual(
            REASON_EVALUATION_NOT_RECORDED, self.assess(number).unavailable_reason
        )

    def test_a_corrupted_row_is_never_repaired_on_read(self):
        number = self.prepared()
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_evaluations SET result_count = 9 WHERE task_id = ?",
                (self.task_id,),
            )
        before = self.digest()
        for _ in range(3):
            self.assess(number)
        self.assertEqual(before, self.digest())
        with self.sql() as connection:
            stored = connection.execute(
                "SELECT result_count FROM task_turn_evaluations WHERE task_id = ?",
                (self.task_id,),
            ).fetchone()
        self.assertEqual(9, stored["result_count"])


class ConsistentReadTests(AssessmentStoreCase):
    def test_the_inputs_come_from_one_pinned_snapshot(self):
        """Lineage and evaluation cannot straddle another process's commit.

        A second connection commits an evaluation *while* the read transaction is
        open. The read must not see it — it pinned its view before the commit —
        which is exactly the property that stops an active set from before a
        commit being combined with an evaluation from after it.
        """
        number = self.turn([self.change("a")], {"mode": "root"})
        opened = []
        original = self.store._lineage_graph_locked

        def watched(connection, task_id, turn_number):
            graph = original(connection, task_id, turn_number)
            if not opened:
                opened.append(True)
                # Another process commits an evaluation mid-read.
                second = TaskStore(self.config)
                try:
                    second_service = TaskService(
                        self.config,
                        second,
                        self.service._adapters,
                        projects=self.service._projects,
                    )
                    second_service.evaluate_closed_turns(self.task_id)
                finally:
                    second.close()
            return graph

        self.store._lineage_graph_locked = watched
        try:
            answer = self.assess(number)
        finally:
            del self.store._lineage_graph_locked
        # The read was pinned before the other process committed, so it reports
        # the evaluation as not yet recorded rather than mixing two states.
        self.assertEqual(REASON_EVALUATION_NOT_RECORDED, answer.unavailable_reason)
        # And a fresh read, after the commit, sees it.
        self.assertEqual(ASSESSMENT_RESOLVED, self.assess(number).state)

    def test_the_read_takes_no_write_lock(self):
        """A reader must never block a writer."""
        number = self.turn([self.change("a")], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        with self.sql() as connection:
            connection.execute("BEGIN IMMEDIATE")
            answer = self.assess(number)
            connection.execute("ROLLBACK")
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)


class ZeroMutationTests(AssessmentStoreCase):
    def test_repeated_reads_leave_the_database_byte_identical(self):
        number = self.turn([self.change("a")], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        self.store.close()
        before = self.digest()
        store = TaskStore(self.config)
        self.addCleanup(store.close)
        from cofferdam.workstation.tasks import build_registry
        from cofferdam.workstation.tasks.projects import load_projects

        adapters = build_registry(enable_validation_adapter=True)
        service = TaskService(
            self.config,
            store,
            adapters,
            projects=load_projects(self.config, adapters.ids()),
        )
        for _ in range(5):
            service.current_criterion_assessment(self.task_id, number)
        store.close()
        self.assertEqual(before, self.digest())

    def test_reading_creates_no_table(self):
        number = self.turn([self.change("a")], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)

        def tables():
            with self.sql() as connection:
                return sorted(
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                )

        before = tables()
        self.assess(number)
        self.assertEqual(before, tables())

    def test_no_current_assessment_is_persisted_anywhere(self):
        number = self.turn([self.change("a")], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        answer = self.assess(number)
        with self.sql() as connection:
            script = "\n".join(
                row["sql"] or ""
                for row in connection.execute("SELECT sql FROM sqlite_master")
            )
            for table in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall():
                rows = connection.execute(
                    'SELECT * FROM "%s"' % table["name"]
                ).fetchall()
                for row in rows:
                    self.assertNotIn(
                        answer.fingerprint,
                        [str(value) for value in tuple(row)],
                        "%s stored the derived fingerprint" % table["name"],
                    )
        for forbidden in ("current_assessment", "criterion_assessment"):
            self.assertNotIn(forbidden, script)

    def test_the_schema_version_does_not_move(self):
        self.assertEqual(10, SCHEMA_VERSION)


class WorldIndependenceTests(AssessmentStoreCase):
    def test_deleting_the_repository_does_not_change_the_answer(self):
        import shutil

        number = self.turn([self.change("a")], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        before = self.assess(number)
        shutil.rmtree(self.root)
        after = self.assess(number)
        self.assertEqual(before, after)
        self.assertEqual(before.fingerprint, after.fingerprint)

    def test_the_answer_survives_a_store_reopen(self):
        number = self.turn([self.change("a")], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        before = self.assess(number).fingerprint
        self.store.close()
        store = TaskStore(self.config)
        self.addCleanup(store.close)
        from cofferdam.workstation.tasks import build_registry
        from cofferdam.workstation.tasks.projects import load_projects

        adapters = build_registry(enable_validation_adapter=True)
        service = TaskService(
            self.config,
            store,
            adapters,
            projects=load_projects(self.config, adapters.ids()),
        )
        self.assertEqual(
            before,
            service.current_criterion_assessment(self.task_id, number).fingerprint,
        )

    def test_the_read_never_invokes_an_observer_or_the_evaluator(self):
        number = self.turn([self.change("a")], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)

        import cofferdam.workstation.tasks.evaluation as evaluation_module
        import cofferdam.workstation.tasks.finalstate as finalstate_module

        def poison(*args, **kwargs):
            raise AssertionError("the read path reached the world")

        saved = (
            evaluation_module.evaluate,
            finalstate_module.observe_paths,
            finalstate_module.observe_path,
        )
        evaluation_module.evaluate = poison
        finalstate_module.observe_paths = poison
        finalstate_module.observe_path = poison
        try:
            answer = self.assess(number)
        finally:
            (
                evaluation_module.evaluate,
                finalstate_module.observe_paths,
                finalstate_module.observe_path,
            ) = saved
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(REASON_TURN_CHANGE_EVALUATED, answer.assessments[0].reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
