"""M2K PR7 — when an evaluation happens, and what a crash between the two leaves.

The rule this module exists to hold:

    never attach a judgement to evidence whose exact turn window can still move.

A turn's window becomes final when ``task_turn_bounds.closed_through_event_sequence``
is written, which happens inside the same transaction as the turn's
``completed_at``. Everything before that is a moving target; everything after it
is immutable. So evaluation is strictly post-close, and because it commits in a
*separate* transaction from the close, the interesting failure is the gap between
them — a turn that is durably closed with no evaluation yet.

That gap is not a special case with its own repair logic. It is the ordinary case
arriving late, and one function serves both: after a close it runs for one task,
at start-up it runs for all of them, and its query excludes anything already
evaluated so running it ten times produces one record.
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from typing import Any, Dict, List, Sequence

from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    AdapterRefusal,
    TaskAdapter,
    TaskContext,
)
from cofferdam.workstation.tasks.criteria import (
    CRITERIA_NOT_PROVIDED,
    CRITERIA_PRESENT,
)
from cofferdam.workstation.tasks.evaluation import (
    EVALUATOR_VERSION,
    RESULT_UNVERIFIED,
)

from ._task_doubles import PROJECT_ID, TaskTestCase

CRITERIA = [
    {"kind": "evidence", "predicate": "path_changed", "path": "src/a.py"},
    {"kind": "manual", "description": "a person confirms the page renders"},
]


class WatchingAdapter(TaskAdapter):
    """Records, at its own first instruction, whether an evaluation exists yet."""

    adapter_id = "watcher"
    display_name = "Watching adapter"
    description = "A test double."

    def __init__(self, db_path) -> None:
        self._db = db_path
        self.refuse = False
        self.seen: List[Dict[str, Any]] = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(start=True, followup=True, cancel=True, final_result=True)

    def available(self) -> bool:
        return True

    def session_available(self, task_id: str) -> bool:
        return True

    def _look(self, context: TaskContext, call: str) -> None:
        connection = sqlite3.connect("file:%s?mode=ro" % self._db, uri=True)
        try:
            evaluations = connection.execute(
                "SELECT COUNT(*) FROM task_turn_evaluations WHERE task_id = ?",
                (context.task_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.seen.append({"call": call, "evaluations": evaluations})

    def start(self, context: TaskContext) -> AdapterOutcome:
        self._look(context, "start")
        if self.refuse:
            raise AdapterRefusal("refused")
        return AdapterOutcome(
            events=(AdapterEvent(text="work"),), requested_state="ready_for_followup"
        )

    def send_followup(self, context: TaskContext, followup: str) -> AdapterOutcome:
        self._look(context, "send_followup")
        if self.refuse:
            raise AdapterRefusal("refused")
        return AdapterOutcome(
            events=(AdapterEvent(text="work"),), requested_state="ready_for_followup"
        )

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        return AdapterOutcome(events=(), requested_state="cancelled")


class EvaluationLifecycleCase(TaskTestCase):
    project_adapters = ("watcher", "validation")

    def extra_adapters(self) -> Sequence[TaskAdapter]:
        self.watcher = WatchingAdapter(self.home / "state" / "tasks" / "tasks.sqlite3")
        return (self.watcher,)

    def start(self, criteria=CRITERIA):
        row, _ = self.service.create_task(
            prompt="do the work",
            project_id=PROJECT_ID,
            adapter_id="watcher",
            origin="pwa",
            criteria=criteria,
        )
        return row

    def rows(self, table, task_id=None):
        path = self.home / "state" / "tasks" / "tasks.sqlite3"
        connection = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            if task_id is None:
                return [dict(r) for r in connection.execute("SELECT * FROM %s" % table)]
            return [
                dict(r)
                for r in connection.execute(
                    "SELECT * FROM %s WHERE task_id = ?" % table, (task_id,)
                )
            ]
        finally:
            connection.close()


class PostCloseTiming(EvaluationLifecycleCase):
    def test_no_evaluation_exists_while_the_worker_is_running(self):
        self.start()
        self.assertEqual(self.watcher.seen[0], {"call": "start", "evaluations": 0})

    def test_the_first_turn_is_evaluated_once_it_closes(self):
        row = self.start()
        stored = self.service.turn_evaluation(row.task_id, 1)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.turn_number, 1)
        self.assertEqual(stored.criteria_state, CRITERIA_PRESENT)
        self.assertEqual(stored.result_count, 2)

    def test_the_evaluation_binds_the_closed_window(self):
        row = self.start()
        stored = self.service.turn_evaluation(row.task_id, 1)
        bundle = self.service.evidence_bundle(row.task_id, 1)
        self.assertFalse(bundle.turn_open)
        self.assertIsNotNone(bundle.closed_through_event_sequence)
        self.assertEqual(stored.evidence_input_fingerprint, bundle.input_fingerprint)
        self.assertEqual(stored.assembler_version, bundle.assembler_version)

    def test_an_open_turn_has_no_evaluation(self):
        """`waiting_for_user` leaves the turn running; nothing may judge it yet."""
        row = self.start()
        self.service.store.transition(
            row.task_id,
            "waiting_for_user",
            event_type="waiting_for_user",
            actor="system",
            source="cofferdam",
            expected_state=self.service.get_task(row.task_id).state,
            waiting_reason="clarification",
        )
        self.service.send_followup(row.task_id, "an answer")
        turns = self.service.store.turns(row.task_id)
        open_turns = [t.turn_number for t in turns if t.completed_at is None]
        for number in open_turns:
            self.assertIsNone(self.service.turn_evaluation(row.task_id, number))

    def test_a_follow_up_turn_is_evaluated_after_its_own_close(self):
        row = self.start()
        self.service.send_followup(row.task_id, "more", criteria=CRITERIA)
        first = self.service.turn_evaluation(row.task_id, 1)
        second = self.service.turn_evaluation(row.task_id, 2)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.evaluation_id, second.evaluation_id)
        self.assertEqual(second.turn_number, 2)

    def test_each_evaluation_binds_its_own_turns_evidence(self):
        row = self.start()
        self.service.send_followup(row.task_id, "more", criteria=CRITERIA)
        first = self.service.turn_evaluation(row.task_id, 1)
        second = self.service.turn_evaluation(row.task_id, 2)
        self.assertNotEqual(
            first.evidence_input_fingerprint, second.evidence_input_fingerprint
        )

    def test_a_refused_dispatch_opens_no_turn_and_records_no_evaluation(self):
        self.watcher.refuse = True
        row = self.start()
        self.assertEqual(self.service.store.turns(row.task_id), [])
        self.assertEqual(self.rows("task_turn_evaluations", row.task_id), [])


class NoCriteriaCases(EvaluationLifecycleCase):
    def test_a_not_provided_turn_gets_a_zero_result_record(self):
        row = self.start(criteria=None)
        stored = self.service.turn_evaluation(row.task_id, 1)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.criteria_state, CRITERIA_NOT_PROVIDED)
        self.assertEqual(stored.result_count, 0)
        self.assertEqual(stored.results, ())

    def test_a_zero_result_record_is_not_a_pass(self):
        row = self.start(criteria=None)
        stored = self.service.turn_evaluation(row.task_id, 1)
        self.assertFalse(stored.decided)
        self.assertFalse(hasattr(stored, "passed"))
        self.assertFalse(hasattr(stored, "succeeded"))
        self.assertFalse(hasattr(stored, "verdict"))

    def test_a_legacy_unknown_turn_gets_no_record_at_all(self):
        row = self.start()
        # Delete the criteria snapshot, as a pre-v7 turn has none, then re-run.
        path = self.home / "state" / "tasks" / "tasks.sqlite3"
        with sqlite3.connect(str(path)) as db:
            db.execute("DELETE FROM task_turn_evaluations WHERE task_id = ?", (row.task_id,))
            db.execute("DELETE FROM task_turn_criteria WHERE task_id = ?", (row.task_id,))
        self.restart()
        self.assertEqual(
            self.service.store.turn_criteria(row.task_id, 1).state, "legacy_unknown"
        )
        self.assertEqual(self.service.evaluate_closed_turns(row.task_id), 0)
        self.assertIsNone(self.service.turn_evaluation(row.task_id, 1))
        self.assertEqual(self.rows("task_turn_evaluations", row.task_id), [])


class CrashRecovery(EvaluationLifecycleCase):
    """The gap between a durable close and a durable judgement."""

    def _drop_evaluations(self, task_id):
        path = self.home / "state" / "tasks" / "tasks.sqlite3"
        with sqlite3.connect(str(path)) as db:
            db.execute("DELETE FROM task_turn_evaluations WHERE task_id = ?", (task_id,))

    def test_a_crash_after_close_before_evaluation_recovers_on_restart(self):
        row = self.start()
        self._drop_evaluations(row.task_id)          # as a crash would leave it
        self.assertIsNone(self.service.turn_evaluation(row.task_id, 1))
        self.restart()
        self.service.recover_after_restart()
        stored = self.service.turn_evaluation(row.task_id, 1)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.result_count, 2)

    def test_recovery_creates_exactly_one_record(self):
        row = self.start()
        self._drop_evaluations(row.task_id)
        self.restart()
        self.service.recover_after_restart()
        self.assertEqual(len(self.rows("task_turn_evaluations", row.task_id)), 1)

    def test_repeated_restarts_produce_no_duplicate(self):
        row = self.start()
        self._drop_evaluations(row.task_id)
        for _ in range(4):
            self.restart()
            self.service.recover_after_restart()
        self.assertEqual(len(self.rows("task_turn_evaluations", row.task_id)), 1)

    def test_the_recovered_record_is_identical_to_the_one_lost(self):
        row = self.start()
        before = self.service.turn_evaluation(row.task_id, 1)
        self._drop_evaluations(row.task_id)
        self.restart()
        self.service.recover_after_restart()
        after = self.service.turn_evaluation(row.task_id, 1)
        self.assertEqual(after.evaluation_fingerprint, before.evaluation_fingerprint)
        self.assertEqual(after.evidence_input_fingerprint, before.evidence_input_fingerprint)
        self.assertEqual(after.criteria_snapshot_id, before.criteria_snapshot_id)
        self.assertEqual(
            [(r.ordinal, r.result, r.reason) for r in after.results],
            [(r.ordinal, r.result, r.reason) for r in before.results],
        )
        # A new row, so a new server-minted id — the identity that matters is the
        # fingerprint, and it did not move.
        self.assertNotEqual(after.evaluation_id, before.evaluation_id)

    def test_the_evaluation_pass_touches_no_task_history(self):
        """Scoped to the pass itself, deliberately.

        ``recover_after_restart`` legitimately writes a ``task_interrupted``
        event for a task that was still open when the daemon stopped — that is
        pre-existing restart behaviour and has nothing to do with evaluation.
        What must be true of *this* PR is that the evaluation pass adds no event,
        moves no turn, and rewrites no bound or criteria row, so it is called on
        its own here.
        """
        row = self.start()
        self._drop_evaluations(row.task_id)
        self.restart()
        events_before = self.rows("task_events", row.task_id)
        turns_before = self.rows("task_turns", row.task_id)
        bounds_before = self.rows("task_turn_bounds", row.task_id)
        criteria_before = self.rows("task_turn_criteria", row.task_id)
        tasks_before = self.rows("tasks", row.task_id)

        self.assertEqual(self.service.evaluate_closed_turns(), 1)

        self.assertEqual(self.rows("task_events", row.task_id), events_before)
        self.assertEqual(self.rows("task_turns", row.task_id), turns_before)
        self.assertEqual(self.rows("task_turn_bounds", row.task_id), bounds_before)
        self.assertEqual(self.rows("task_turn_criteria", row.task_id), criteria_before)
        self.assertEqual(self.rows("tasks", row.task_id), tasks_before)

    def test_restart_recovery_behaviour_is_otherwise_unchanged(self):
        """The interrupted-task settling PR7 inherited still happens exactly once."""
        row = self.start()
        self.restart()
        self.service.recover_after_restart()
        events = [e["event_type"] for e in self.rows("task_events", row.task_id)]
        self.assertEqual(events.count("task_interrupted"), 1)
        self.assertEqual(self.service.get_task(row.task_id).state, "interrupted")

    def test_a_second_pass_over_an_evaluated_turn_writes_nothing(self):
        row = self.start()
        self.assertEqual(self.service.evaluate_closed_turns(row.task_id), 0)
        self.assertEqual(self.service.evaluate_closed_turns(), 0)


class PersistenceFailure(EvaluationLifecycleCase):
    def test_a_failing_evaluation_does_not_disturb_the_task(self):
        """The task completes normally; the judgement is simply not there yet."""
        original = self.service.store.record_evaluation

        def refuse(**kwargs):
            from cofferdam.workstation.tasks.errors import StoreUnavailable

            raise StoreUnavailable("the evaluation could not be written")

        self.service.store.record_evaluation = refuse
        row = self.start()
        self.service.store.record_evaluation = original
        settled = self.service.get_task(row.task_id)
        self.assertEqual(settled.state, "ready_for_followup")
        self.assertEqual(len(self.service.store.turns(row.task_id)), 1)
        self.assertIsNone(self.service.turn_evaluation(row.task_id, 1))

    def test_and_the_evaluation_remains_recoverable_afterwards(self):
        original = self.service.store.record_evaluation

        def refuse(**kwargs):
            from cofferdam.workstation.tasks.errors import StoreUnavailable

            raise StoreUnavailable("nope")

        self.service.store.record_evaluation = refuse
        row = self.start()
        self.service.store.record_evaluation = original
        self.assertEqual(self.service.evaluate_closed_turns(row.task_id), 1)
        self.assertIsNotNone(self.service.turn_evaluation(row.task_id, 1))


class ReadsDoNotMutate(EvaluationLifecycleCase):
    def test_reading_evidence_and_evaluations_creates_nothing(self):
        row = self.start()
        before = self.rows("task_turn_evaluations")
        results_before = self.rows("task_turn_criterion_results")
        for _ in range(10):
            self.service.evidence_bundle(row.task_id, 1)
            self.service.turn_evaluation(row.task_id, 1)
            self.service.store.turn_criteria(row.task_id, 1)
        self.assertEqual(self.rows("task_turn_evaluations"), before)
        self.assertEqual(self.rows("task_turn_criterion_results"), results_before)

    def test_reading_an_unevaluated_turn_does_not_evaluate_it(self):
        row = self.start()
        path = self.home / "state" / "tasks" / "tasks.sqlite3"
        with sqlite3.connect(str(path)) as db:
            db.execute("DELETE FROM task_turn_evaluations WHERE task_id = ?", (row.task_id,))
        for _ in range(5):
            self.assertIsNone(self.service.turn_evaluation(row.task_id, 1))
            self.service.evidence_bundle(row.task_id, 1)
        self.assertEqual(self.rows("task_turn_evaluations", row.task_id), [])


class NoAggregateAnywhere(EvaluationLifecycleCase):
    def test_the_service_exposes_no_task_verdict(self):
        row = self.start()
        for name in ("task_verdict", "task_passed", "evaluate_task", "task_result_verdict"):
            self.assertFalse(hasattr(self.service, name), name)
        stored = self.service.turn_evaluation(row.task_id, 1)
        self.assertFalse(hasattr(stored, "aggregate"))

    def test_the_task_snapshot_carries_no_evaluation_vocabulary(self):
        row = self.start()
        payload = json.dumps(self.service.snapshot(self.service.get_task(row.task_id)).to_dict())
        for forbidden in ("met", "not_met", "unverified", "verdict", "confidence", "risk"):
            self.assertNotIn('"%s"' % forbidden, payload)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
