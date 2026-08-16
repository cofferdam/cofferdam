"""M2K PR11 — the read half: one coherent snapshot, no writes, no repairs.

The resolver's semantics are proven over hand-built graphs in
``test_lineage_resolver.py``. This module proves the other half: that
:meth:`~cofferdam.workstation.tasks.store.TaskStore.lineage_inputs` fetches
exactly the rows a resolution stands on, from **one** database state, changes
nothing, and hands corrupted rows through unrepaired for the resolver to refuse.

The corruption fixtures here go through raw SQL on purpose. Several of them are
shapes PR10's write validation refuses outright — which is the point: a read must
still answer safely when it meets one in a database that was restored, edited by
hand, or written by a future version that had a bug.
"""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from cofferdam.workstation.config import load_config
from cofferdam.workstation.tasks.continuity import validate_declaration
from cofferdam.workstation.tasks.criteria import validate_criteria
from cofferdam.workstation.tasks.lineage import (
    MAX_LINEAGE_DEPTH,
    REASON_CRITERIA_SNAPSHOT_MISSING,
    REASON_LEGACY_UNKNOWN,
    REASON_NOT_DECLARED,
    REASON_PREDECESSOR_FOREIGN_TASK,
    REASON_PREDECESSOR_NOT_EARLIER,
    REASON_PREDECESSOR_UNAVAILABLE,
    REASON_RELATIONS_MODE_MISMATCH,
    REASON_SNAPSHOT_MISMATCH,
    REASON_SUPERSESSION_TARGET_NOT_ACTIVE,
    resolve,
)
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

TASK = "tsk_pr11_store"
OTHER_TASK = "tsk_pr11_other"


class LineageStoreCase(unittest.TestCase):
    """A real store over an isolated home, driven the way the service drives it."""

    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        config = load_config(self.home)
        config.ensure_dirs()
        self.config = config
        self.store = TaskStore(config)
        self.addCleanup(self.store.close)
        self.store.storage_health()
        self.database = self.store.path
        self.make_task(TASK)

    # -- fixtures ------------------------------------------------------------

    @contextmanager
    def sql(self):
        """A second connection, as another process would have. Always closed."""
        connection = sqlite3.connect(str(self.database))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def make_task(self, task_id):
        with self.sql() as connection:
            connection.execute(
                "INSERT INTO tasks (task_id, correlation_id, origin, adapter_id,"
                " project_id, state, created_at, updated_at, title, prompt)"
                " VALUES (?,'cor','pwa','validation','demo','running','x','x','t','p')",
                (task_id,),
            )

    def criteria_for(self, *labels):
        return [
            {"kind": "evidence", "predicate": "path_changed", "path": "%s.py" % label}
            for label in labels
        ]

    def turn(self, labels, declaration, *, task_id=TASK):
        """One turn's pre-work, in the order the service performs it."""
        self.store.reserve_turn_criteria(
            task_id, validate_criteria(self.criteria_for(*labels)), recorded_at="x"
        )
        number = self.store.reserve_turn_continuity(
            task_id, validate_declaration(declaration), recorded_at="x"
        )
        self.store.mark_criteria_dispatch_started(task_id, number)
        self.store.mark_continuity_dispatch_started(task_id, number)
        with self.sql() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO task_turns (task_id, turn_number, provider,"
                " source, started_at, completed_at, outcome)"
                " VALUES (?,?,'validation','pwa','x','y','completed')",
                (task_id, number),
            )
        return number

    def snapshot_id(self, turn, task_id=TASK):
        return self.store.turn_criteria(task_id, turn).snapshot_id

    def criterion_ids(self, turn, task_id=TASK):
        return [
            item.criterion_id
            for item in self.store.turn_criteria(task_id, turn).criteria
        ]

    def resolved(self, turn, task_id=TASK):
        return resolve(self.store.lineage_inputs(task_id, turn))

    def paths(self, turn, task_id=TASK):
        return [entry.criterion.path for entry in self.resolved(turn, task_id).active]

    def digest(self):
        return hashlib.sha256(self.database.read_bytes()).hexdigest()


class FetchTests(LineageStoreCase):
    def test_the_walk_collects_exactly_the_chain(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend",
                          "predecessor_snapshot_id": self.snapshot_id(1)})
        self.turn(["c"], {"mode": "extend",
                          "predecessor_snapshot_id": self.snapshot_id(2)})
        inputs = self.store.lineage_inputs(TASK, 3)
        self.assertEqual(sorted(inputs.nodes), [1, 2, 3])
        self.assertEqual(inputs.earliest_snapshot_turn, 1)
        self.assertEqual(inputs.target_turn_number, 3)

    def test_a_replace_stops_the_walk_at_the_cut_point(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend",
                          "predecessor_snapshot_id": self.snapshot_id(1)})
        self.turn(["c"], {"mode": "replace",
                          "predecessor_snapshot_id": self.snapshot_id(2)})
        inputs = self.store.lineage_inputs(TASK, 3)
        self.assertEqual(sorted(inputs.nodes), [3])
        # The predecessor's identity is still fetched: a malformed declaration
        # must still be refusable.
        self.assertIn(self.snapshot_id(2), inputs.snapshot_owners)

    def test_a_root_stops_the_walk(self):
        self.turn(["a"], {"mode": "root"})
        inputs = self.store.lineage_inputs(TASK, 1)
        self.assertEqual(sorted(inputs.nodes), [1])
        self.assertEqual(inputs.snapshot_owners, {})

    def test_an_undeclared_turn_stops_the_walk(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], None)
        inputs = self.store.lineage_inputs(TASK, 2)
        self.assertEqual(sorted(inputs.nodes), [2])
        self.assertEqual(self.resolved(2).reason, REASON_NOT_DECLARED)

    def test_a_turn_with_no_rows_at_all(self):
        result = self.resolved(9)
        self.assertFalse(result.resolved)
        self.assertEqual(result.reason, REASON_LEGACY_UNKNOWN)

    def test_the_walk_is_bounded(self):
        """A long chain is fetched up to the bound and no further."""
        self.turn(["c1"], {"mode": "root"})
        for turn in range(2, 12):
            self.turn(
                ["c%d" % turn],
                {"mode": "extend", "predecessor_snapshot_id": self.snapshot_id(turn - 1)},
            )
        inputs = self.store.lineage_inputs(TASK, 11)
        self.assertEqual(len(inputs.nodes), 11)
        self.assertLessEqual(len(inputs.nodes), MAX_LINEAGE_DEPTH)
        self.assertEqual(len(self.resolved(11).active), 11)


class ReadOnlyTests(LineageStoreCase):
    def test_resolving_writes_nothing(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend",
                          "predecessor_snapshot_id": self.snapshot_id(1)})
        self.store.close()
        before = self.digest()
        store = TaskStore(self.config)
        self.addCleanup(store.close)
        for _ in range(5):
            self.assertTrue(resolve(store.lineage_inputs(TASK, 2)).resolved)
        store.close()
        self.assertEqual(self.digest(), before)

    def test_resolving_leaves_the_schema_version_alone(self):
        self.turn(["a"], {"mode": "root"})
        self.resolved(1)
        with self.sql() as connection:
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        self.assertEqual(int(version), SCHEMA_VERSION)

    def test_resolving_creates_no_table(self):
        before = self.tables()
        self.turn(["a"], {"mode": "root"})
        self.resolved(1)
        self.assertEqual(self.tables(), before)

    def tables(self):
        with self.sql() as connection:
            return sorted(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
                )
            )

    def test_the_answer_is_the_same_after_reopening_the_database(self):
        self.turn(["a", "b"], {"mode": "root"})
        self.turn(["c"], {"mode": "extend",
                          "predecessor_snapshot_id": self.snapshot_id(1)})
        first = self.resolved(2)
        self.store.close()
        reopened = TaskStore(self.config)
        self.addCleanup(reopened.close)
        second = resolve(reopened.lineage_inputs(TASK, 2))
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.active_criterion_ids, second.active_criterion_ids)


class ConsistentReadTests(LineageStoreCase):
    def test_the_fetch_holds_one_read_transaction(self):
        self.turn(["a"], {"mode": "root"})
        with self.store._read_snapshot() as connection:
            self.assertTrue(connection.in_transaction)
        self.assertFalse(connection.in_transaction)

    def test_a_foreign_commit_mid_walk_is_not_observed(self):
        """The window this exists to close, forced open deliberately.

        A second connection commits an extra criterion into turn 1 *after* the
        walk has read turn 2 and *before* it reads turn 1. Without the read
        transaction the graph would inherit a criterion that did not exist when
        the walk began — half old, half new. With it, the walk sees the state it
        started in, and the new criterion appears only on the next resolution.
        """
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend",
                          "predecessor_snapshot_id": self.snapshot_id(1)})
        self.assertEqual(self.paths(2), ["a.py", "b.py"])

        intruder = sqlite3.connect(str(self.database))
        intruder.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(intruder.close)
        original = self.store.turn_criteria
        seen = []

        def interleaved(task_id, turn_number):
            seen.append(int(turn_number))
            if len(seen) == 1:
                # Committed from a different connection, exactly as another
                # process would.
                intruder.execute("BEGIN IMMEDIATE")
                intruder.execute(
                    "INSERT INTO task_turn_criterion_items (criterion_id, task_id,"
                    " turn_number, ordinal, kind, predicate, path)"
                    " VALUES ('crt_intruder', ?, 1, 2, 'evidence', 'path_changed',"
                    " 'intruder.py')",
                    (TASK,),
                )
                intruder.execute(
                    "UPDATE task_turn_criteria SET criterion_count = 2"
                    " WHERE task_id = ? AND turn_number = 1",
                    (TASK,),
                )
                intruder.execute("COMMIT")
            return original(task_id, turn_number)

        self.store.turn_criteria = interleaved
        try:
            during = resolve(self.store.lineage_inputs(TASK, 2))
        finally:
            del self.store.turn_criteria

        self.assertEqual(
            [entry.criterion.path for entry in during.active], ["a.py", "b.py"]
        )
        self.assertEqual(self.paths(2), ["a.py", "intruder.py", "b.py"])


class CorruptedRowTests(LineageStoreCase):
    """Shapes PR10 refuses to write. A read must still fail closed on them."""

    def test_a_declaration_pointing_at_the_wrong_snapshot(self):
        self.turn(["a"], {"mode": "root"})
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_criteria_continuity"
                " SET current_snapshot_id = 'snp_somewhere_else'"
                " WHERE task_id = ? AND turn_number = 1",
                (TASK,),
            )
        self.assertEqual(self.resolved(1).reason, REASON_SNAPSHOT_MISMATCH)

    def test_a_cross_task_predecessor(self):
        self.make_task(OTHER_TASK)
        self.turn(["foreign"], {"mode": "root"}, task_id=OTHER_TASK)
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend",
                          "predecessor_snapshot_id": self.snapshot_id(1)})
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_criteria_continuity SET predecessor_snapshot_id = ?"
                " WHERE task_id = ? AND turn_number = 2",
                (self.snapshot_id(1, OTHER_TASK), TASK),
            )
        self.assertEqual(self.resolved(2).reason, REASON_PREDECESSOR_FOREIGN_TASK)

    def test_a_predecessor_from_a_later_turn(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend",
                          "predecessor_snapshot_id": self.snapshot_id(1)})
        self.turn(["c"], {"mode": "extend",
                          "predecessor_snapshot_id": self.snapshot_id(2)})
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_criteria_continuity SET predecessor_snapshot_id = ?"
                " WHERE task_id = ? AND turn_number = 2",
                (self.snapshot_id(3), TASK),
            )
        self.assertEqual(self.resolved(2).reason, REASON_PREDECESSOR_NOT_EARLIER)

    def test_an_impossible_root(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend",
                          "predecessor_snapshot_id": self.snapshot_id(1)})
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_criteria_continuity"
                " SET mode = 'root', predecessor_snapshot_id = NULL"
                " WHERE task_id = ? AND turn_number = 2",
                (TASK,),
            )
        result = self.resolved(2)
        self.assertFalse(result.resolved)
        self.assertEqual(result.reason, "root_not_first_snapshot")

    def test_a_mode_that_disagrees_with_its_relations(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(
            ["b"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snapshot_id(1),
                "supersedes": [
                    {"criterion_ordinal": 1,
                     "predecessor_criterion_id": self.criterion_ids(1)[0]}
                ],
            },
        )
        # The CHECK constraint forbids this pairing, which is the point: only a
        # database written *around* the schema can hold it, and a read must still
        # answer safely when handed one.
        with self.sql() as connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                "UPDATE task_turn_criteria_continuity SET mode = 'extend'"
                " WHERE task_id = ? AND turn_number = 2",
                (TASK,),
            )
        self.assertEqual(self.resolved(2).reason, REASON_RELATIONS_MODE_MISMATCH)

    def test_a_predecessor_snapshot_that_no_longer_exists(self):
        """Deleting the row takes its identity with it, so the link dangles."""
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend",
                          "predecessor_snapshot_id": self.snapshot_id(1)})
        with self.sql() as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute(
                "DELETE FROM task_turn_criteria WHERE task_id = ? AND turn_number = 1",
                (TASK,),
            )
        self.assertEqual(self.resolved(2).reason, "predecessor_missing")

    def test_a_declaration_whose_own_snapshot_is_gone(self):
        """A continuity row that survived its criteria row is refused, not read."""
        self.turn(["a"], {"mode": "root"})
        with self.sql() as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute(
                "DELETE FROM task_turn_criteria WHERE task_id = ? AND turn_number = 1",
                (TASK,),
            )
        result = self.resolved(1)
        self.assertFalse(result.resolved)
        self.assertEqual(result.reason, REASON_CRITERIA_SNAPSHOT_MISSING)

    def test_a_missing_criterion_row(self):
        self.turn(["a", "b"], {"mode": "root"})
        with self.sql() as connection:
            connection.execute(
                "DELETE FROM task_turn_criterion_items WHERE criterion_id = ?",
                (self.criterion_ids(1)[1],),
            )
        # The count no longer agrees with the rows, which is exactly the shape a
        # partial delete leaves behind.
        self.assertEqual(self.resolved(1).reason, "malformed_lineage")

    def test_a_stale_supersession_target(self):
        """The load-bearing invariant, over real rows.

        Turn 2 retires ``a``. Turn 3 legitimately retires ``b``, and the
        relation is then rewritten by hand to name ``a`` again — a shape PR10
        refuses at write time. ``a`` is still a real row and still part of the
        walked lineage, but it has not been active since turn 2.
        """
        self.turn(["a", "keep"], {"mode": "root"})
        self.turn(
            ["b"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snapshot_id(1),
                "supersedes": [
                    {"criterion_ordinal": 1,
                     "predecessor_criterion_id": self.criterion_ids(1)[0]}
                ],
            },
        )
        self.turn(
            ["c"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snapshot_id(2),
                "supersedes": [
                    {"criterion_ordinal": 1,
                     "predecessor_criterion_id": self.criterion_ids(2)[0]}
                ],
            },
        )
        self.assertEqual(self.paths(3), ["keep.py", "c.py"])

        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_criterion_supersessions"
                " SET predecessor_criterion_id = ?"
                " WHERE task_id = ? AND turn_number = 3",
                (self.criterion_ids(1)[0], TASK),
            )
        result = self.resolved(3)
        self.assertFalse(result.resolved)
        self.assertEqual(result.reason, REASON_SUPERSESSION_TARGET_NOT_ACTIVE)
        self.assertEqual(result.at_turn_number, 3)

    def test_a_stale_relation_is_never_ignored_into_an_answer(self):
        """Silently skipping it would give ``keep, b, c`` — a set nobody declared."""
        self.test_a_stale_supersession_target()
        self.assertFalse(hasattr(self.resolved(3), "active"))

    def test_a_self_referential_predecessor_terminates(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend",
                          "predecessor_snapshot_id": self.snapshot_id(1)})
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_criteria_continuity SET predecessor_snapshot_id = ?"
                " WHERE task_id = ? AND turn_number = 2",
                (self.snapshot_id(2), TASK),
            )
        result = self.resolved(2)
        self.assertFalse(result.resolved)
        self.assertIn(result.reason, ("cycle_detected", REASON_PREDECESSOR_NOT_EARLIER))

    def test_nothing_is_repaired_by_a_refusal(self):
        self.turn(["a"], {"mode": "root"})
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_criteria_continuity"
                " SET current_snapshot_id = 'snp_somewhere_else'"
                " WHERE task_id = ? AND turn_number = 1",
                (TASK,),
            )
        self.store.close()
        before = self.digest()
        store = TaskStore(self.config)
        self.addCleanup(store.close)
        self.assertFalse(resolve(store.lineage_inputs(TASK, 1)).resolved)
        store.close()
        self.assertEqual(self.digest(), before)


class WriteBoundaryTests(LineageStoreCase):
    """Which declarations the store accepts, and which it refuses. **M2K PR12.**

    The class this replaces pinned the opposite of the first test below: PR10
    required a retired criterion to be stored in the declared predecessor's own
    snapshot, and PR11 recorded that as a limitation to revisit. PR12 revisits it.
    """

    def test_revise_may_retire_a_criterion_the_predecessor_only_inherited(self):
        """The boundary PR11 discovered, now the right way round.

        Turn 1 introduces ``b``; turn 2 extends without touching it, so ``b`` is
        still active at turn 2. Turn 3 declares turn 2 as its predecessor and
        retires ``b`` — a requirement turn 2 genuinely stands on, even though
        turn 2's own snapshot does not contain it.
        """
        self.turn(["a", "b"], {"mode": "root"})
        self.turn(["c"], {"mode": "extend",
                          "predecessor_snapshot_id": self.snapshot_id(1)})
        self.assertEqual(self.paths(2), ["a.py", "b.py", "c.py"])
        self.turn(
            ["d"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snapshot_id(2),
                "supersedes": [
                    {"criterion_ordinal": 1,
                     "predecessor_criterion_id": self.criterion_ids(1)[1]}
                ],
            },
        )
        self.assertEqual(self.paths(3), ["a.py", "c.py", "d.py"])
        # The whole chain is still consumed, so nothing was cut to make this work.
        result = self.resolved(3)
        self.assertEqual([step.turn_number for step in result.lineage], [1, 2, 3])

    def test_a_criterion_this_task_never_had_is_still_unknown(self):
        from cofferdam.workstation.tasks.errors import ContinuityInvalid

        self.make_task(OTHER_TASK)
        self.turn(["foreign"], {"mode": "root"}, task_id=OTHER_TASK)
        self.turn(["a"], {"mode": "root"})
        self.store.reserve_turn_criteria(
            TASK, validate_criteria(self.criteria_for("b")), recorded_at="x"
        )
        with self.assertRaises(ContinuityInvalid) as caught:
            self.store.reserve_turn_continuity(
                TASK,
                validate_declaration(
                    {
                        "mode": "revise",
                        "predecessor_snapshot_id": self.snapshot_id(1),
                        "supersedes": [
                            {"criterion_ordinal": 1,
                             "predecessor_criterion_id":
                                 self.criterion_ids(1, OTHER_TASK)[0]}
                        ],
                    }
                ),
                recorded_at="x",
            )
        self.assertEqual(
            caught.exception.detail, "continuity_relation_predecessor_unknown"
        )

    def test_revise_with_an_empty_current_snapshot_is_refused(self):
        """A relation's new side must be a criterion of the current snapshot."""
        from cofferdam.workstation.tasks.errors import ContinuityInvalid

        self.turn(["a"], {"mode": "root"})
        self.store.reserve_turn_criteria(TASK, validate_criteria([]), recorded_at="x")
        with self.assertRaises(ContinuityInvalid):
            self.store.reserve_turn_continuity(
                TASK,
                validate_declaration(
                    {
                        "mode": "revise",
                        "predecessor_snapshot_id": self.snapshot_id(1),
                        "supersedes": [
                            {"criterion_ordinal": 1,
                             "predecessor_criterion_id": self.criterion_ids(1)[0]}
                        ],
                    }
                ),
                recorded_at="x",
            )

    def test_extend_with_an_empty_current_snapshot_is_permitted_and_inherits(self):
        self.turn(["a", "b"], {"mode": "root"})
        self.turn([], {"mode": "extend",
                       "predecessor_snapshot_id": self.snapshot_id(1)})
        self.assertEqual(self.paths(2), ["a.py", "b.py"])

    def test_a_replace_with_an_empty_current_snapshot_resolves_empty(self):
        self.turn(["a"], {"mode": "root"})
        self.turn([], {"mode": "replace",
                       "predecessor_snapshot_id": self.snapshot_id(1)})
        result = self.resolved(2)
        self.assertTrue(result.resolved)
        self.assertEqual(result.active_count, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
