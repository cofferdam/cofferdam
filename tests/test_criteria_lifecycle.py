"""M2K PR6 — the dispatch lifecycle, and the moving target it exists to prevent.

The invariant, stated once:

    A future evaluation must refer to the exact criteria snapshot that was
    already in force **before worker dispatch began**.

Everything in this module is a way of failing to hold it. A snapshot written
after the adapter was called is a target that moved while the worker ran. A
snapshot a retry may replace after a crash is a target that moved after the work
happened. A follow-up that edits turn one's requirements is a target that moved
after the evaluation. Each of those produces a verdict that looks exactly as
authoritative as a correct one.

So the rule is PR4's, applied to a second pre-work fact: ``dispatch_state`` is
durable, it is committed **before** the adapter call, and only ``captured``
permits replacement — which makes ``captured`` mean "the adapter had provably not
been reached" rather than "we did not find a turn row". An ``AdapterRefusal``
does not re-open it, because an adapter's refusal is a statement of intent and
not a proof about side effects.

The load-bearing test in here is :meth:`FirstInstructionDurability` — a separate
**read-only** connection, opened by the adapter at its first instruction, which
can only see what has actually been committed. No uncommitted write satisfies it.
"""

from __future__ import annotations

import sqlite3
import unittest
from typing import Any, Dict, List, Optional, Sequence

from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    AdapterRefusal,
    TaskAdapter,
    TaskContext,
)
from cofferdam.workstation.tasks.criteria import (
    CRITERIA_LEGACY_UNKNOWN,
    CRITERIA_NOT_PROVIDED,
    CRITERIA_PRESENT,
)
from cofferdam.workstation.tasks.errors import CriteriaInvalid
from cofferdam.workstation.tasks.gitbaseline import (
    DISPATCH_CAPTURED,
    DISPATCH_REFUSED,
    DISPATCH_STARTED,
    DISPATCH_TURN_OPENED,
    REPLACEABLE_DISPATCH_STATES,
)

from ._task_doubles import PROJECT_ID, TaskTestCase

SNAPSHOT_TABLE = "task_turn_criteria"
ITEM_TABLE = "task_turn_criterion_items"

CHANGED = {"kind": "evidence", "predicate": "path_changed", "path": "src/a.py"}
CREATED = {
    "kind": "evidence",
    "predicate": "path_operation",
    "path": "src/b.py",
    "operation": "created",
}
MANUAL = {"kind": "manual", "description": "a person confirms the page renders"}
OTHER = {"kind": "evidence", "predicate": "path_changed", "path": "src/z.py"}


class LookingAdapter(TaskAdapter):
    """Reads the database over a separate read-only connection, when called.

    Read-only and separate on purpose. The service holds one write connection
    inside an open transaction while it dispatches; a probe that borrowed it
    would see uncommitted rows and prove nothing about durability. This one can
    only see what has been committed, which is what "durable before the worker
    starts" has to mean.
    """

    adapter_id = "looker"
    display_name = "Looking adapter"
    description = "A test double."

    def __init__(self, db_path, *, refuse: bool = False) -> None:
        self._db_path = db_path
        self.refuse = refuse
        self.seen: List[Dict[str, Any]] = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            start=True, followup=True, cancel=True, final_result=True
        )

    def available(self) -> bool:
        return True

    def session_available(self, task_id: str) -> bool:
        return True

    def _look(self, context: TaskContext, call: str) -> None:
        connection = sqlite3.connect("file:%s?mode=ro" % self._db_path, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            snapshots = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM %s WHERE task_id = ? ORDER BY turn_number"
                    % SNAPSHOT_TABLE,
                    (context.task_id,),
                )
            ]
            items = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM %s WHERE task_id = ? ORDER BY turn_number, ordinal"
                    % ITEM_TABLE,
                    (context.task_id,),
                )
            ]
            baselines = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM task_turn_git_baselines WHERE task_id = ?"
                    " ORDER BY turn_number",
                    (context.task_id,),
                )
            ]
            turns = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM task_turns WHERE task_id = ? ORDER BY turn_number",
                    (context.task_id,),
                )
            ]
        finally:
            connection.close()
        self.seen.append(
            {
                "call": call,
                "snapshots": snapshots,
                "items": items,
                "baselines": baselines,
                "turns": turns,
            }
        )

    def start(self, context: TaskContext) -> AdapterOutcome:
        self._look(context, "start")
        if self.refuse:
            raise AdapterRefusal("the session refused")
        return AdapterOutcome(
            events=(AdapterEvent(text="looked"),),
            requested_state="ready_for_followup",
        )

    def send_followup(self, context: TaskContext, followup: str) -> AdapterOutcome:
        self._look(context, "send_followup")
        if self.refuse:
            raise AdapterRefusal("the session refused")
        return AdapterOutcome(
            events=(AdapterEvent(text="looked"),),
            requested_state="ready_for_followup",
        )

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        return AdapterOutcome(events=(), requested_state="cancelled")


class CriteriaTestCase(TaskTestCase):
    project_adapters = ("looker", "validation")

    def extra_adapters(self) -> Sequence[TaskAdapter]:
        self.looker = LookingAdapter(self.home / "state" / "tasks" / "tasks.sqlite3")
        return (self.looker,)

    def start(self, criteria=None, **kwargs):
        row, _ = self.service.create_task(
            prompt="do the work",
            project_id=PROJECT_ID,
            adapter_id="looker",
            origin="pwa",
            criteria=criteria,
            **kwargs,
        )
        return row

    def rows(self, task_id, table=SNAPSHOT_TABLE):
        path = self.home / "state" / "tasks" / "tasks.sqlite3"
        connection = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            return [
                dict(r)
                for r in connection.execute(
                    "SELECT * FROM %s WHERE task_id = ?" % table, (task_id,)
                )
            ]
        finally:
            connection.close()


class FirstInstructionDurability(CriteriaTestCase):
    """What a worker's very first instruction can already see, committed."""

    def test_the_snapshot_is_durable_before_the_first_instruction(self):
        row = self.start([CHANGED, CREATED, MANUAL])
        first = self.looker.seen[0]
        self.assertEqual(first["call"], "start")
        self.assertEqual(len(first["snapshots"]), 1)
        snapshot = first["snapshots"][0]
        self.assertEqual(snapshot["turn_number"], 1)
        self.assertEqual(snapshot["criteria_state"], CRITERIA_PRESENT)
        self.assertEqual(snapshot["criterion_count"], 3)
        self.assertEqual(len(snapshot["criteria_fingerprint"]), 64)

    def test_every_criterion_row_is_durable_before_the_first_instruction(self):
        self.start([CHANGED, CREATED, MANUAL])
        items = self.looker.seen[0]["items"]
        self.assertEqual(len(items), 3)
        self.assertEqual([item["ordinal"] for item in items], [1, 2, 3])
        self.assertEqual([item["path"] for item in items], ["src/a.py", "src/b.py", None])

    def test_the_dispatch_state_is_already_frozen(self):
        self.start([CHANGED])
        self.assertEqual(
            self.looker.seen[0]["snapshots"][0]["dispatch_state"], DISPATCH_STARTED
        )
        self.assertNotIn(DISPATCH_STARTED, REPLACEABLE_DISPATCH_STATES)

    def test_the_pr4_baseline_is_durable_at_the_same_moment(self):
        self.start([CHANGED])
        self.assertEqual(len(self.looker.seen[0]["baselines"]), 1)
        self.assertEqual(
            self.looker.seen[0]["baselines"][0]["dispatch_state"], DISPATCH_STARTED
        )

    def test_the_turn_row_is_still_absent(self):
        """Which is exactly why the snapshot's foreign key names `tasks`."""
        self.start([CHANGED])
        self.assertEqual(self.looker.seen[0]["turns"], [])

    def test_the_snapshot_and_the_baseline_share_the_reserved_turn_number(self):
        self.start([CHANGED])
        first = self.looker.seen[0]
        self.assertEqual(
            first["snapshots"][0]["turn_number"], first["baselines"][0]["turn_number"]
        )

    def test_a_followup_turn_proves_the_same_thing(self):
        row = self.start([CHANGED])
        self.service.send_followup(row.task_id, "more please", criteria=[CREATED])
        second = self.looker.seen[1]
        self.assertEqual(second["call"], "send_followup")
        self.assertEqual(len(second["snapshots"]), 2)
        fresh = [s for s in second["snapshots"] if s["turn_number"] == 2][0]
        self.assertEqual(fresh["criteria_state"], CRITERIA_PRESENT)
        self.assertEqual(fresh["dispatch_state"], DISPATCH_STARTED)
        self.assertEqual(
            [i for i in second["items"] if i["turn_number"] == 2][0]["path"], "src/b.py"
        )
        # And turn two's row does not exist yet either.
        self.assertEqual([t["turn_number"] for t in second["turns"]], [1])

    def test_the_turn_opens_afterwards_and_binds_the_snapshot(self):
        row = self.start([CHANGED])
        stored = self.rows(row.task_id)[0]
        self.assertEqual(stored["dispatch_state"], DISPATCH_TURN_OPENED)
        self.assertEqual(len(self.service.store.turns(row.task_id)), 1)


class NoCriteriaIsRecorded(CriteriaTestCase):
    """`not_provided` is a fact somebody wrote down, before dispatch."""

    def test_a_task_with_no_criteria_records_not_provided_before_dispatch(self):
        self.start(None)
        snapshot = self.looker.seen[0]["snapshots"][0]
        self.assertEqual(snapshot["criteria_state"], CRITERIA_NOT_PROVIDED)
        self.assertEqual(snapshot["criterion_count"], 0)
        self.assertEqual(snapshot["dispatch_state"], DISPATCH_STARTED)
        self.assertEqual(self.looker.seen[0]["items"], [])

    def test_not_provided_is_not_a_missing_row(self):
        row = self.start(None)
        self.assertEqual(len(self.rows(row.task_id)), 1)
        self.assertEqual(
            self.service.turn_criteria(row.task_id, 1).state, CRITERIA_NOT_PROVIDED
        )

    def test_not_provided_is_not_legacy_unknown(self):
        row = self.start(None)
        snapshot = self.service.turn_criteria(row.task_id, 1)
        self.assertNotEqual(snapshot.state, CRITERIA_LEGACY_UNKNOWN)
        self.assertTrue(snapshot.recorded)

    def test_an_empty_list_is_the_same_as_none(self):
        row = self.start([])
        self.assertEqual(
            self.service.turn_criteria(row.task_id, 1).state, CRITERIA_NOT_PROVIDED
        )

    def test_a_turn_that_was_never_reached_reads_legacy_unknown(self):
        row = self.start([CHANGED])
        self.assertEqual(
            self.service.turn_criteria(row.task_id, 5).state, CRITERIA_LEGACY_UNKNOWN
        )


class Immutability(CriteriaTestCase):
    """After `dispatch_started`, nothing about the snapshot may move.

    The adapter refuses here, deliberately. A refusal leaves **no turn row**, so
    ``MAX(turn_number) + 1`` still resolves to the turn that was already
    dispatched — which is the only shape in which a re-reservation can even
    *reach* a frozen snapshot, and therefore the only shape in which "it is
    frozen" is a claim worth testing rather than a claim about arithmetic. The
    complementary case, where the turn did open, is
    :class:`AnOpenTurnIsOutOfReach` below.
    """

    def extra_adapters(self):
        self.looker = LookingAdapter(
            self.home / "state" / "tasks" / "tasks.sqlite3", refuse=True
        )
        return (self.looker,)

    def _frozen(self, task_id):
        return self.rows(task_id)[0]

    def test_a_second_reservation_keeps_the_frozen_snapshot(self):
        row = self.start([CHANGED, CREATED])
        before = self._frozen(row.task_id)
        # A retry against the same reserved turn, with different criteria.
        from cofferdam.workstation.tasks.criteria import validate_criteria

        self.service.store.reserve_turn_criteria(
            row.task_id, validate_criteria([OTHER]), recorded_at="2026-08-15T09:00:00Z"
        )
        after = self._frozen(row.task_id)
        self.assertEqual(before, after)

    def test_the_criterion_rows_are_untouched_by_a_second_reservation(self):
        row = self.start([CHANGED, CREATED])
        before = self.rows(row.task_id, ITEM_TABLE)
        from cofferdam.workstation.tasks.criteria import validate_criteria

        self.service.store.reserve_turn_criteria(
            row.task_id, validate_criteria([OTHER]), recorded_at="2026-08-15T09:00:00Z"
        )
        self.assertEqual(self.rows(row.task_id, ITEM_TABLE), before)
        self.assertEqual([r["path"] for r in before], ["src/a.py", "src/b.py"])

    def test_not_provided_cannot_become_present(self):
        row = self.start(None)
        from cofferdam.workstation.tasks.criteria import validate_criteria

        self.service.store.reserve_turn_criteria(
            row.task_id, validate_criteria([CHANGED]), recorded_at="2026-08-15T09:00:00Z"
        )
        self.assertEqual(
            self.service.turn_criteria(row.task_id, 1).state, CRITERIA_NOT_PROVIDED
        )

    def test_present_cannot_become_not_provided(self):
        row = self.start([CHANGED])
        self.service.store.reserve_turn_criteria(
            row.task_id, (), recorded_at="2026-08-15T09:00:00Z"
        )
        snapshot = self.service.turn_criteria(row.task_id, 1)
        self.assertEqual(snapshot.state, CRITERIA_PRESENT)
        self.assertEqual(len(snapshot.criteria), 1)

    def test_the_snapshot_identity_does_not_change(self):
        row = self.start([CHANGED])
        before = self.service.turn_criteria(row.task_id, 1)
        from cofferdam.workstation.tasks.criteria import validate_criteria

        self.service.store.reserve_turn_criteria(
            row.task_id, validate_criteria([OTHER]), recorded_at="2026-08-15T09:00:00Z"
        )
        after = self.service.turn_criteria(row.task_id, 1)
        self.assertEqual(before.snapshot_id, after.snapshot_id)
        self.assertEqual(before.fingerprint, after.fingerprint)

    def test_the_criterion_ids_do_not_change(self):
        row = self.start([CHANGED, CREATED])
        before = [c.criterion_id for c in self.service.turn_criteria(row.task_id, 1).criteria]
        from cofferdam.workstation.tasks.criteria import validate_criteria

        self.service.store.reserve_turn_criteria(
            row.task_id, validate_criteria([OTHER]), recorded_at="2026-08-15T09:00:00Z"
        )
        after = [c.criterion_id for c in self.service.turn_criteria(row.task_id, 1).criteria]
        self.assertEqual(before, after)

    def test_it_survives_a_restart(self):
        row = self.start([CHANGED, CREATED, MANUAL])
        before = self.service.turn_criteria(row.task_id, 1)
        self.restart()
        after = self.service.turn_criteria(row.task_id, 1)
        self.assertEqual(before.fingerprint, after.fingerprint)
        self.assertEqual(before.snapshot_id, after.snapshot_id)
        self.assertEqual(
            [c.criterion_id for c in before.criteria],
            [c.criterion_id for c in after.criteria],
        )


class AnOpenTurnIsOutOfReach(CriteriaTestCase):
    """Once the turn row exists, a reservation cannot name that turn at all.

    A second layer under the ``dispatch_state`` rule rather than a restatement of
    it: reservation allocates ``MAX(turn_number) + 1``, so a turn that has opened
    is arithmetically unreachable. Both layers are wanted — the state rule covers
    the window before the turn row exists, this covers everything after.
    """

    def test_a_reservation_after_the_turn_opened_lands_on_the_next_turn(self):
        from cofferdam.workstation.tasks.criteria import validate_criteria

        row = self.start([CHANGED])
        self.service.store.reserve_turn_criteria(
            row.task_id, validate_criteria([OTHER]), recorded_at="2026-08-15T09:00:00Z"
        )
        self.assertEqual(
            [c.path for c in self.service.turn_criteria(row.task_id, 1).criteria],
            ["src/a.py"],
        )
        self.assertEqual(
            [c.path for c in self.service.turn_criteria(row.task_id, 2).criteria],
            ["src/z.py"],
        )

    def test_turn_ones_rows_are_byte_identical_afterwards(self):
        from cofferdam.workstation.tasks.criteria import validate_criteria

        row = self.start([CHANGED, CREATED])
        before = [r for r in self.rows(row.task_id, ITEM_TABLE) if r["turn_number"] == 1]
        self.service.store.reserve_turn_criteria(
            row.task_id, validate_criteria([OTHER]), recorded_at="2026-08-15T09:00:00Z"
        )
        after = [r for r in self.rows(row.task_id, ITEM_TABLE) if r["turn_number"] == 1]
        self.assertEqual(before, after)


class ReplacementWindow(CriteriaTestCase):
    """The one state that permits replacement, and it is proven not assumed."""

    def test_only_captured_permits_replacement(self):
        self.assertEqual(REPLACEABLE_DISPATCH_STATES, (DISPATCH_CAPTURED,))

    def test_a_snapshot_may_be_replaced_before_dispatch_started(self):
        """The crash window: reserved, then the process died before the adapter."""
        from cofferdam.workstation.tasks.criteria import validate_criteria

        row = self.create(adapter_id="validation")
        store = self.service.store
        store.reserve_turn_criteria(
            row.task_id, validate_criteria([CHANGED]), recorded_at="2026-08-15T00:00:00Z"
        )
        first = store.turn_criteria(row.task_id, 2)
        self.assertEqual(store.turn_criteria_dispatch_state(row.task_id, 2), DISPATCH_CAPTURED)
        store.reserve_turn_criteria(
            row.task_id, validate_criteria([OTHER]), recorded_at="2026-08-15T00:01:00Z"
        )
        second = store.turn_criteria(row.task_id, 2)
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(second.criteria[0].path, "src/z.py")

    def test_a_replacement_leaves_no_orphan_criterion(self):
        from cofferdam.workstation.tasks.criteria import validate_criteria

        row = self.create(adapter_id="validation")
        store = self.service.store
        store.reserve_turn_criteria(
            row.task_id, validate_criteria([CHANGED, CREATED, MANUAL]),
            recorded_at="2026-08-15T00:00:00Z",
        )
        store.reserve_turn_criteria(
            row.task_id, validate_criteria([OTHER]), recorded_at="2026-08-15T00:01:00Z"
        )
        items = self.rows(row.task_id, ITEM_TABLE)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["path"], "src/z.py")


class RefusalAndRetry(CriteriaTestCase):
    """A refusal is a statement of intent, not a proof about side effects."""

    def extra_adapters(self):
        self.looker = LookingAdapter(
            self.home / "state" / "tasks" / "tasks.sqlite3", refuse=True
        )
        return (self.looker,)

    def test_a_refusal_records_the_outcome_and_does_not_reopen_replacement(self):
        row = self.start([CHANGED])
        stored = self.rows(row.task_id)[0]
        self.assertEqual(stored["dispatch_state"], DISPATCH_REFUSED)
        self.assertNotIn(DISPATCH_REFUSED, REPLACEABLE_DISPATCH_STATES)

    def test_the_snapshot_survives_the_refusal_intact(self):
        row = self.start([CHANGED, MANUAL])
        snapshot = self.service.turn_criteria(row.task_id, 1)
        self.assertEqual(snapshot.state, CRITERIA_PRESENT)
        self.assertEqual(len(snapshot.criteria), 2)

    def test_a_retry_after_refusal_uses_the_same_snapshot(self):
        row = self.start([CHANGED])
        before = self.service.turn_criteria(row.task_id, 1)
        from cofferdam.workstation.tasks.criteria import validate_criteria

        self.service.store.reserve_turn_criteria(
            row.task_id, validate_criteria([OTHER]), recorded_at="2026-08-15T09:00:00Z"
        )
        after = self.service.turn_criteria(row.task_id, 1)
        self.assertEqual(before.snapshot_id, after.snapshot_id)
        self.assertEqual(before.fingerprint, after.fingerprint)
        self.assertEqual([c.path for c in after.criteria], ["src/a.py"])


class CrashSemantics(CriteriaTestCase):
    """What a restart finds, for each point the process could have died."""

    def test_crash_after_criteria_before_baseline_leaves_the_snapshot_reserved(self):
        from cofferdam.workstation.tasks.criteria import validate_criteria

        row = self.create(adapter_id="validation")
        self.service.store.reserve_turn_criteria(
            row.task_id, validate_criteria([CHANGED]), recorded_at="2026-08-15T00:00:00Z"
        )
        self.restart()
        snapshot = self.service.turn_criteria(row.task_id, 2)
        self.assertEqual(snapshot.state, CRITERIA_PRESENT)
        self.assertEqual(
            self.service.store.turn_criteria_dispatch_state(row.task_id, 2),
            DISPATCH_CAPTURED,
        )
        self.assertIsNone(self.service.store.turn_baseline(row.task_id, 2))

    def test_crash_after_dispatch_started_leaves_it_frozen(self):
        from cofferdam.workstation.tasks.criteria import validate_criteria

        row = self.create(adapter_id="validation")
        store = self.service.store
        store.reserve_turn_criteria(
            row.task_id, validate_criteria([CHANGED]), recorded_at="2026-08-15T00:00:00Z"
        )
        store.mark_criteria_dispatch_started(row.task_id, 2)
        self.restart()
        self.assertEqual(
            self.service.store.turn_criteria_dispatch_state(row.task_id, 2),
            DISPATCH_STARTED,
        )
        # And a retry after the restart cannot replace it.
        self.service.store.reserve_turn_criteria(
            row.task_id,
            validate_criteria([OTHER]),
            recorded_at="2026-08-15T00:02:00Z",
        )
        self.assertEqual(
            [c.path for c in self.service.turn_criteria(row.task_id, 2).criteria],
            ["src/a.py"],
        )

    def test_a_crash_before_any_reservation_reads_legacy_unknown(self):
        row = self.create(adapter_id="validation")
        self.restart()
        self.assertEqual(
            self.service.turn_criteria(row.task_id, 2).state, CRITERIA_LEGACY_UNKNOWN
        )


class FollowupTurns(CriteriaTestCase):
    """A later turn may be given new criteria; the earlier ones stay frozen."""

    def test_a_new_turn_may_carry_a_new_snapshot(self):
        row = self.start([CHANGED])
        self.service.send_followup(row.task_id, "next", criteria=[CREATED, MANUAL])
        first = self.service.turn_criteria(row.task_id, 1)
        second = self.service.turn_criteria(row.task_id, 2)
        self.assertEqual([c.path for c in first.criteria], ["src/a.py"])
        self.assertEqual([c.path for c in second.criteria], ["src/b.py", None])
        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_the_earlier_turn_is_untouched_by_the_later_one(self):
        row = self.start([CHANGED])
        before = self.rows(row.task_id)[0]
        self.service.send_followup(row.task_id, "next", criteria=[OTHER])
        after = [r for r in self.rows(row.task_id) if r["turn_number"] == 1][0]
        self.assertEqual(before, after)

    def test_a_followup_with_no_criteria_records_not_provided_for_its_own_turn(self):
        row = self.start([CHANGED])
        self.service.send_followup(row.task_id, "next")
        self.assertEqual(
            self.service.turn_criteria(row.task_id, 2).state, CRITERIA_NOT_PROVIDED
        )
        self.assertEqual(
            self.service.turn_criteria(row.task_id, 1).state, CRITERIA_PRESENT
        )

    def test_two_turns_given_identical_criteria_share_a_fingerprint(self):
        row = self.start([CHANGED])
        self.service.send_followup(row.task_id, "next", criteria=[CHANGED])
        self.assertEqual(
            self.service.turn_criteria(row.task_id, 1).fingerprint,
            self.service.turn_criteria(row.task_id, 2).fingerprint,
        )
        self.assertNotEqual(
            self.service.turn_criteria(row.task_id, 1).snapshot_id,
            self.service.turn_criteria(row.task_id, 2).snapshot_id,
        )


class RefusedSubmissions(CriteriaTestCase):
    """A refused criteria set leaves no task, no event and no adapter call."""

    def test_an_invalid_submission_refuses_before_the_task_exists(self):
        before = len(self.service.list_tasks(limit=50))
        with self.assertRaises(CriteriaInvalid):
            self.start([{"kind": "evidence", "predicate": "path_changed", "path": "/etc/passwd"}])
        self.assertEqual(len(self.service.list_tasks(limit=50)), before)
        self.assertEqual(self.looker.seen, [])

    def test_the_refusal_detail_is_a_reason_code_and_a_position(self):
        with self.assertRaises(CriteriaInvalid) as caught:
            self.start([CHANGED, {"kind": "manual"}])
        self.assertIn("criterion_description_required", caught.exception.detail)
        self.assertIn("criterion 2", caught.exception.detail)

    def test_criteria_cannot_join_a_turn_that_is_already_running(self):
        """A resumed turn's snapshot froze when its own dispatch began."""
        row = self.start(None)
        self.service.store.transition(
            row.task_id,
            "waiting_for_user",
            event_type="waiting_for_user",
            actor="system",
            source="cofferdam",
            expected_state=self.service.get_task(row.task_id).state,
            waiting_reason="clarification",
        )
        with self.assertRaises(CriteriaInvalid):
            self.service.send_followup(row.task_id, "the answer", criteria=[CHANGED])

    def test_a_refused_followup_submission_delivers_nothing(self):
        row = self.start(None)
        calls = len(self.looker.seen)
        with self.assertRaises(CriteriaInvalid):
            self.service.send_followup(
                row.task_id, "next", criteria=[{"kind": "manual"}]
            )
        self.assertEqual(len(self.looker.seen), calls)
        self.assertEqual(len(self.service.store.turns(row.task_id)), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
