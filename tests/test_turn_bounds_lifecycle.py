"""M2K PR2 — the exact event-sequence boundaries a turn owns.

The model, stated once so every test below can be read against it:

    opened_after_event_sequence = tasks.event_cursor at the moment the turn
    opened, inside the same transaction as the turn row.

    A **closed** turn owns
        opened_after_event_sequence < event.sequence <= closed_through_event_sequence

    An **open** turn owns
        event.sequence > opened_after_event_sequence
    with ``closed_through_event_sequence`` NULL.

The transition event is appended *before* the turn is closed, so the transition
event belongs to the turn it ended. A follow-up that closes turn N and opens
turn N+1 in one transaction does both at the same cursor value, which gives
``(…, X]`` and ``(X, …]`` — adjacent, never overlapping.

**Timestamps are not authority.** Several tests below deliberately construct
turns whose timestamps are identical or inverted, and assert that attribution is
unchanged. If any of them start failing because a fallback was added, the
fallback is the bug.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.store import TaskStore, _TurnClose, _TurnDraft


def _open_store(home: Path) -> TaskStore:
    from cofferdam.workstation.config import load_config

    config = load_config(home)
    config.ensure_dirs()
    return TaskStore(config)


class BoundsFixture(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr2-turns-")
        self.home = Path(self._temp.name)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.store = _open_store(self.home)
        self.task_id = self._make_task()

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self._temp.cleanup()

    def _make_task(self) -> str:
        row, _ = self.store.create_task(
            origin="pwa",
            adapter_id="validation",
            project_id="synth",
            prompt="do a thing",
            title="t",
        )
        return row.task_id

    def _cursor(self) -> int:
        return self.store.get(self.task_id).event_cursor

    def _open(self, *, at: str = "2026-08-14T00:00:00Z", followup: str = None):
        return self.store.open_turn(
            self.task_id,
            provider="validation",
            source="internal_test",
            started_at=at,
            followup_request_id=followup,
        )

    def _note(self, text: str = "note") -> int:
        return self.store.append_event(
            self.task_id, "progress", actor="system", source="cofferdam", text=text
        )

    def _move(self, state: str, **kwargs):
        """One legal transition, with whatever turn work rides along with it."""
        return self.store.transition(
            self.task_id,
            state,
            event_type=kwargs.pop("event_type", "task_" + state),
            actor=kwargs.pop("actor", "system"),
            source=kwargs.pop("source", "cofferdam"),
            **kwargs,
        )

    def _run(self):
        """Walk the graph from ``created`` to ``running``. Three real events."""
        for state in ("queued", "starting", "running"):
            self._move(state)

    def _bound(self, turn_number: int):
        return self.store.turn_bound(self.task_id, turn_number)

    def _rows(self):
        with sqlite3.connect(str(self.path)) as db:
            db.row_factory = sqlite3.Row
            return db.execute(
                "SELECT * FROM task_turn_bounds WHERE task_id = ? ORDER BY turn_number",
                (self.task_id,),
            ).fetchall()


class OpenBoundTests(BoundsFixture):
    def test_the_first_turn_records_the_cursor_it_opened_after(self):
        cursor = self._cursor()
        self._open()
        bound = self._bound(1)
        self.assertIsNotNone(bound)
        self.assertEqual(bound.opened_after_event_sequence, cursor)
        self.assertIsNone(bound.closed_through_event_sequence)

    def test_an_open_turn_owns_everything_after_its_lower_bound(self):
        self._open()
        first = self._note("a")
        second = self._note("b")
        bound = self._bound(1)
        self.assertTrue(bound.owns(first))
        self.assertTrue(bound.owns(second))
        self.assertFalse(bound.owns(bound.opened_after_event_sequence))

    def test_the_event_before_the_turn_opened_is_not_owned(self):
        before = self._note("before")
        self._open()
        self.assertFalse(self._bound(1).owns(before))

    def test_every_turn_created_under_v5_has_a_bound(self):
        """The invariant: no turn row without its bound."""
        self._run()
        self._open()
        with sqlite3.connect(str(self.path)) as db:
            unbounded = db.execute(
                "SELECT COUNT(*) FROM task_turns t WHERE NOT EXISTS ("
                " SELECT 1 FROM task_turn_bounds b"
                "  WHERE b.task_id = t.task_id AND b.turn_number = t.turn_number)"
            ).fetchone()[0]
        self.assertEqual(unbounded, 0)

    def test_a_duplicate_followup_open_creates_neither_turn_nor_bound(self):
        self._open(followup="req-1")
        self._open(followup="req-1")
        self.assertEqual(self.store.turn_count(self.task_id), 1)
        self.assertEqual(len(self._rows()), 1)

    def test_a_bound_is_never_orphaned(self):
        self._open()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            orphans = db.execute(
                "SELECT COUNT(*) FROM task_turn_bounds b WHERE NOT EXISTS ("
                " SELECT 1 FROM task_turns t"
                "  WHERE t.task_id = b.task_id AND t.turn_number = b.turn_number)"
            ).fetchone()[0]
        self.assertEqual(orphans, 0)


class CloseBoundTests(BoundsFixture):
    def _close(self, state: str = "completed", outcome: str = None,
               at: str = "2026-08-14T00:10:00Z"):
        return self._move(
            state,
            actor="adapter",
            source="adapter",
            close_turn=_TurnClose(outcome=outcome or state, completed_at=at),
        )

    def test_a_normal_close_records_the_final_included_cursor(self):
        self._run()
        self._open()
        self._note("work")
        self._close("completed")
        bound = self._bound(1)
        self.assertEqual(bound.closed_through_event_sequence, self._cursor())

    def test_the_transition_event_belongs_to_the_closing_turn(self):
        self._run()
        self._open()
        self._close("completed")
        bound = self._bound(1)
        # The completion event is the last one, and the closed turn owns it.
        self.assertTrue(bound.owns(self._cursor()))

    def test_a_zero_event_turn_is_valid(self):
        """``opened_after == closed_through``: a turn that owned nothing.

        Reached here by opening and closing a turn with no transition event in
        between — ``ready_for_followup`` then a follow-up open, in the service,
        is the shape that produces it in production. It is a valid record and
        the assembler must not treat it as broken.
        """
        self._run()
        self._open()
        cursor = self._cursor()
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "UPDATE task_turn_bounds SET closed_through_event_sequence = ?"
                " WHERE task_id = ? AND turn_number = 1",
                (cursor, self.task_id),
            )
        bound = self._bound(1)
        self.assertEqual(
            bound.opened_after_event_sequence, bound.closed_through_event_sequence
        )
        self.assertEqual(bound.event_count, 0)
        self.assertFalse(bound.owns(cursor))
        self.assertEqual(self.store.events_in_bound(self.task_id, bound), [])

    def test_an_event_after_the_close_belongs_to_no_closed_turn(self):
        self._run()
        self._open()
        self._close("ready_for_followup", outcome="completed")
        bound = self._bound(1)
        after = self._note("late")
        self.assertGreater(after, bound.closed_through_event_sequence)
        self.assertFalse(bound.owns(after))
        self.assertNotIn(
            after,
            [event.sequence for event in self.store.events_in_bound(self.task_id, bound)],
        )

    def test_closing_a_second_time_does_not_move_the_bound(self):
        self._run()
        self._open()
        self._close("ready_for_followup", outcome="completed")
        first = self._bound(1).closed_through_event_sequence
        self._note("later")
        # A second close reaches a task with no open turn; the store's guard
        # matches nothing and the earlier bound stands.
        self._close("completed")
        self.assertEqual(self._bound(1).closed_through_event_sequence, first)

    def test_closing_with_no_open_turn_writes_nothing(self):
        self._move("queued")
        self._move(
            "starting",
            close_turn=_TurnClose(outcome="completed", completed_at="2026-08-14T00:01:00Z"),
        )
        self.assertEqual(self._rows(), [])

    def test_every_terminal_outcome_closes_the_same_way(self):
        for state, outcome in (
            ("failed", "failed"),
            ("cancelled", "cancelled"),
            ("interrupted", "interrupted"),
            ("completed", "completed"),
        ):
            with self.subTest(outcome=outcome):
                self.task_id = self._make_task()
                self._run()
                self._open()
                if state == "cancelled":
                    self._move("cancelling")
                self._close(state, outcome=outcome)
                bound = self._bound(1)
                self.assertIsNotNone(bound.closed_through_event_sequence)
                self.assertEqual(bound.closed_through_event_sequence, self._cursor())

    def test_a_closed_bound_is_never_below_its_open_bound(self):
        self._run()
        self._open()
        self._close("completed")
        bound = self._bound(1)
        self.assertGreaterEqual(
            bound.closed_through_event_sequence, bound.opened_after_event_sequence
        )

    def test_a_legacy_turn_gains_no_bound_when_it_closes(self):
        """A turn opened before v5 stays unbounded, even if it closes after."""
        self._run()
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "INSERT INTO task_turns (task_id, turn_number, provider, source,"
                " started_at) VALUES (?, 1, 'validation', 'internal_test',"
                " '2026-08-01T00:00:00Z')",
                (self.task_id,),
            )
        self.store.close()
        self.store = _open_store(self.home)
        self._close("completed")
        self.assertEqual(self._rows(), [])
        self.assertIsNone(self._bound(1))
        self.assertTrue(self.store.turns(self.task_id)[0].completed)


class FollowupAtomicityTests(BoundsFixture):
    """Close turn N and open turn N+1 in one transaction, at one boundary."""

    def _followup(self):
        return self._move(
            "running",
            event_type="followup_received",
            actor="user",
            close_turn=_TurnClose(
                outcome="completed", completed_at="2026-08-14T00:10:00Z"
            ),
            open_turn=_TurnDraft(
                provider="validation",
                source="internal_test",
                started_at="2026-08-14T00:10:00Z",
            ),
        )

    def _two_turns(self):
        self._run()
        self._open()
        self._note("turn one work")
        self._move("ready_for_followup", actor="adapter", source="adapter")
        self._followup()

    def test_close_and_open_in_one_transaction_share_the_boundary(self):
        self._two_turns()
        first, second = self._bound(1), self._bound(2)
        self.assertEqual(
            first.closed_through_event_sequence, second.opened_after_event_sequence
        )

    def test_no_event_belongs_to_both_turns(self):
        self._two_turns()
        self._note("turn two work")
        first, second = self._bound(1), self._bound(2)
        for sequence in range(0, self._cursor() + 2):
            self.assertFalse(
                first.owns(sequence) and second.owns(sequence),
                "sequence %d belongs to both turns" % sequence,
            )

    def test_the_boundary_is_contiguous(self):
        """Every event after turn one opened belongs to exactly one of the two."""
        self._two_turns()
        self._note("turn two work")
        first, second = self._bound(1), self._bound(2)
        for sequence in range(
            first.opened_after_event_sequence + 1, self._cursor() + 1
        ):
            self.assertTrue(
                first.owns(sequence) or second.owns(sequence),
                "sequence %d belongs to neither turn" % sequence,
            )

    def test_the_two_ranges_partition_the_events(self):
        self._two_turns()
        self._note("turn two work")
        first = [e.sequence for e in self.store.events_in_bound(self.task_id, self._bound(1))]
        second = [e.sequence for e in self.store.events_in_bound(self.task_id, self._bound(2))]
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(set(first) & set(second), set())
        self.assertEqual(max(first) + 1, min(second))

    def test_a_rollback_leaves_neither_turn_nor_bound(self):
        self._run()
        self._open()
        before_turns = self.store.turn_count(self.task_id)
        before_bounds = len(self._rows())
        with self.assertRaises(Exception):
            self._move(
                "queued",  # illegal from running; the whole transaction rolls back
                open_turn=_TurnDraft(
                    provider="validation",
                    source="internal_test",
                    started_at="2026-08-14T00:10:00Z",
                ),
            )
        self.assertEqual(self.store.turn_count(self.task_id), before_turns)
        self.assertEqual(len(self._rows()), before_bounds)

    def test_the_store_reopens_with_the_bounds_intact(self):
        self._two_turns()
        before = [tuple(row) for row in self._rows()]
        self.store.close()
        self.store = _open_store(self.home)
        self.assertEqual([tuple(row) for row in self._rows()], before)


class TimestampIrrelevanceTests(BoundsFixture):
    """Attribution must not consult a clock. These fixtures make sure of it."""

    def _followup(self, *, closed_at: str, started_at: str):
        return self._move(
            "running",
            event_type="followup_received",
            actor="user",
            close_turn=_TurnClose(outcome="completed", completed_at=closed_at),
            open_turn=_TurnDraft(
                provider="validation", source="internal_test", started_at=started_at
            ),
        )

    def test_identical_timestamps_do_not_confuse_attribution(self):
        stamp = "2026-08-14T00:00:00Z"
        self._run()
        self._open(at=stamp)
        first_event = self._note("one")
        self._move("ready_for_followup", actor="adapter", source="adapter")
        self._followup(closed_at=stamp, started_at=stamp)
        second_event = self._note("two")
        first, second = self._bound(1), self._bound(2)
        self.assertTrue(first.owns(first_event))
        self.assertFalse(first.owns(second_event))
        self.assertTrue(second.owns(second_event))

    def test_inverted_timestamps_do_not_change_ownership(self):
        """Turn two claims to have started before turn one. Sequence still rules."""
        self._run()
        self._open(at="2026-08-14T09:00:00Z")
        first_event = self._note("one")
        self._move("ready_for_followup", actor="adapter", source="adapter")
        self._followup(
            closed_at="2026-08-14T08:00:00Z", started_at="2026-08-13T00:00:00Z"
        )
        second_event = self._note("two")
        first, second = self._bound(1), self._bound(2)
        self.assertTrue(first.owns(first_event))
        self.assertFalse(second.owns(first_event))
        self.assertTrue(second.owns(second_event))
        self.assertFalse(first.owns(second_event))

    def test_rewriting_every_event_timestamp_changes_nothing(self):
        self._run()
        self._open()
        owned = self._note("one")
        self._move(
            "completed",
            actor="adapter",
            source="adapter",
            close_turn=_TurnClose(
                outcome="completed", completed_at="2026-08-14T00:10:00Z"
            ),
        )
        before = self._bound(1)
        with sqlite3.connect(str(self.path)) as db:
            db.execute("UPDATE task_events SET created_at = '1999-01-01T00:00:00Z'")
        self.store.close()
        self.store = _open_store(self.home)
        after = self._bound(1)
        self.assertEqual(
            (after.opened_after_event_sequence, after.closed_through_event_sequence),
            (before.opened_after_event_sequence, before.closed_through_event_sequence),
        )
        self.assertTrue(after.owns(owned))

    def test_a_turn_started_at_is_never_consulted(self):
        """Corrupt the turn's own timestamps; the bound is unmoved."""
        self._run()
        self._open()
        owned = self._note("one")
        self._move(
            "completed",
            actor="adapter",
            source="adapter",
            close_turn=_TurnClose(
                outcome="completed", completed_at="2026-08-14T00:10:00Z"
            ),
        )
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "UPDATE task_turns SET started_at='2030-01-01T00:00:00Z',"
                " completed_at='2029-01-01T00:00:00Z'"
            )
        self.store.close()
        self.store = _open_store(self.home)
        bound = self._bound(1)
        self.assertTrue(bound.owns(owned))
        self.assertIn(
            owned,
            [e.sequence for e in self.store.events_in_bound(self.task_id, bound)],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
