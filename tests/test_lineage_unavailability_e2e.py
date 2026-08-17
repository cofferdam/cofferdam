"""M2K PR20 — lineage-failure fidelity against a real database and lifecycle.

The focused suite constructs `LineageUnavailable` values directly, which proves
the mapping. This one never does: every failure here is produced by the real
resolver walking real stored rows, so it also proves the failures PR20 claims to
distinguish are ones Cofferdam actually produces.

The load-bearing case is `NestedPredecessorEndToEnd`. A target turn that is
itself perfectly well-formed can be unresolvable because a turn *behind* it is
not, and PR11 reports that as `predecessor_unavailable` with the real reason in
`cause`. Under V2 those became one string with one fingerprint — including the
`not_declared` / `legacy_unknown` pair PR9 specifically required to stay apart.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from cofferdam.workstation.config import load_config
from cofferdam.workstation.tasks.binding import (
    ASSESSMENT_RESOLVED,
    ASSESSMENT_UNAVAILABLE,
    CURRENT_ASSESSMENT_VERSION,
    REASON_LINEAGE_UNAVAILABLE,
)
from cofferdam.workstation.tasks.continuity import validate_declaration
from cofferdam.workstation.tasks.criteria import validate_criteria
from cofferdam.workstation.tasks.identity import new_task_id
from cofferdam.workstation.tasks.lineage import (
    REASON_LEGACY_UNKNOWN,
    REASON_NOT_DECLARED,
    REASON_PREDECESSOR_UNAVAILABLE,
    resolve,
)
from cofferdam.workstation.tasks.projects import load_projects
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

PROJECT_ID = "demo"
CHANGE = {"kind": "evidence", "predicate": "path_changed", "path": "a.py"}


class Harness(unittest.TestCase):
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
        self.store = TaskStore(config)
        self.addCleanup(self.store.close)
        self.store.storage_health()
        self.database = self.store.path

        from cofferdam.workstation.tasks import build_registry

        adapters = build_registry(enable_validation_adapter=True)
        self.service = TaskService(
            config, self.store, adapters,
            projects=load_projects(config, adapters.ids()),
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
            self.task_id,
            None if declaration is None else validate_declaration(declaration),
            recorded_at="x",
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
        self.service.evaluate_closed_turns(self.task_id)
        return number

    def snap(self, turn):
        return self.store.turn_criteria(self.task_id, turn).snapshot_id

    def assess(self, turn):
        return self.service.current_criterion_assessment(self.task_id, turn)

    def resolved(self, turn):
        return resolve(self.store.lineage_inputs(self.task_id, turn))

    # -- ways a real turn becomes unresolvable -------------------------------

    def make_legacy(self, turn):
        """`legacy_unknown` is the **absence** of a continuity row."""
        with self.sql() as connection:
            connection.execute(
                "DELETE FROM task_turn_criteria_continuity"
                " WHERE task_id = ? AND turn_number = ?",
                (self.task_id, turn),
            )

    def make_not_declared(self, turn):
        """`not_declared` is a **stored** row saying nobody declared anything."""
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_criteria_continuity SET continuity_state ="
                " 'not_declared', mode = NULL, predecessor_snapshot_id = NULL,"
                " relation_count = 0 WHERE task_id = ? AND turn_number = ?",
                (self.task_id, turn),
            )

    def chain_of_three(self):
        first = self.turn([CHANGE], {"mode": "root"})
        second = self.turn(
            [CHANGE], {"mode": "extend", "predecessor_snapshot_id": self.snap(first)}
        )
        third = self.turn(
            [CHANGE], {"mode": "extend", "predecessor_snapshot_id": self.snap(second)}
        )
        return first, second, third


class DirectFailureEndToEnd(Harness):
    """The failure is at the target turn itself."""

    def test_schema_is_v11(self):
        self.assertEqual(11, SCHEMA_VERSION)

    def test_a_turn_whose_own_continuity_is_undeclared(self):
        _, _, third = self.chain_of_three()
        self.make_not_declared(third)
        self.assertEqual(REASON_NOT_DECLARED, self.resolved(third).reason)
        answer = self.assess(third)
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(REASON_NOT_DECLARED, answer.unavailable_reason)
        self.assertIsNone(answer.unavailable_cause)
        self.assertEqual(third, answer.unavailable_at_turn_number)

    def test_a_turn_whose_own_continuity_is_historical(self):
        _, _, third = self.chain_of_three()
        self.make_legacy(third)
        self.assertEqual(REASON_LEGACY_UNKNOWN, self.resolved(third).reason)
        answer = self.assess(third)
        self.assertEqual(REASON_LEGACY_UNKNOWN, answer.unavailable_reason)
        self.assertIsNone(answer.unavailable_cause)

    def test_neither_is_the_generic_reason(self):
        _, _, third = self.chain_of_three()
        self.make_not_declared(third)
        self.assertNotEqual(
            REASON_LINEAGE_UNAVAILABLE, self.assess(third).unavailable_reason
        )

    def test_the_two_have_different_identities(self):
        """Same task, same target turn — only the stored fact differs."""
        _, _, third = self.chain_of_three()
        self.make_not_declared(third)
        undeclared = self.assess(third).fingerprint
        self.make_legacy(third)
        legacy = self.assess(third).fingerprint
        self.assertNotEqual(undeclared, legacy)


class NestedPredecessorEndToEnd(Harness):
    """The target is well-formed; a turn behind it is not. **The hard case.**"""

    def broken_predecessor(self, mutate):
        first, second, third = self.chain_of_three()
        mutate(second)
        return second, third

    def test_the_resolver_reports_it_as_an_inherited_failure(self):
        second, third = self.broken_predecessor(self.make_not_declared)
        outcome = self.resolved(third)
        self.assertEqual(REASON_PREDECESSOR_UNAVAILABLE, outcome.reason)
        self.assertEqual(REASON_NOT_DECLARED, outcome.cause)
        self.assertEqual(second, outcome.at_turn_number)

    def test_the_envelope_preserves_reason_cause_and_turn(self):
        second, third = self.broken_predecessor(self.make_not_declared)
        answer = self.assess(third)
        self.assertEqual(REASON_PREDECESSOR_UNAVAILABLE, answer.unavailable_reason)
        self.assertEqual(REASON_NOT_DECLARED, answer.unavailable_cause)
        self.assertEqual(second, answer.unavailable_at_turn_number)
        self.assertEqual(third, answer.target_turn_number)

    def test_the_historical_variant_is_preserved_too(self):
        _, third = self.broken_predecessor(self.make_legacy)
        answer = self.assess(third)
        self.assertEqual(REASON_PREDECESSOR_UNAVAILABLE, answer.unavailable_reason)
        self.assertEqual(REASON_LEGACY_UNKNOWN, answer.unavailable_cause)

    def test_the_pr9_pair_stays_apart_when_it_is_nested(self):
        """Top-level reason alone would have left exactly this pair collapsed."""
        first, second, third = self.chain_of_three()
        self.make_not_declared(second)
        undeclared = self.assess(third)
        self.make_legacy(second)
        legacy = self.assess(third)
        self.assertEqual(undeclared.unavailable_reason, legacy.unavailable_reason)
        self.assertNotEqual(undeclared.unavailable_cause, legacy.unavailable_cause)
        self.assertNotEqual(undeclared.fingerprint, legacy.fingerprint)

    def test_where_the_chain_broke_is_recorded(self):
        first, second, third = self.chain_of_three()
        fourth = self.turn(
            [CHANGE], {"mode": "extend", "predecessor_snapshot_id": self.snap(third)}
        )
        self.make_not_declared(second)
        answer = self.assess(fourth)
        self.assertEqual(second, answer.unavailable_at_turn_number)
        self.assertNotEqual(fourth, answer.unavailable_at_turn_number)

    def test_a_direct_failure_and_an_inherited_one_are_different_facts(self):
        first, second, third = self.chain_of_three()
        self.make_not_declared(third)
        direct = self.assess(third)
        self.make_not_declared(second)
        # third is repaired back to a normal extend; second is now the break.
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_criteria_continuity SET continuity_state ="
                " 'declared', mode = 'extend', predecessor_snapshot_id = ?"
                " WHERE task_id = ? AND turn_number = ?",
                (self.snap(second), self.task_id, third),
            )
        inherited = self.assess(third)
        self.assertEqual(REASON_NOT_DECLARED, direct.unavailable_reason)
        self.assertEqual(REASON_PREDECESSOR_UNAVAILABLE, inherited.unavailable_reason)
        self.assertNotEqual(direct.fingerprint, inherited.fingerprint)


class UnaffectedBehaviourEndToEnd(Harness):
    """A healthy lineage assesses exactly as it did under V2."""

    def test_a_normal_turn_still_resolves(self):
        _, _, third = self.chain_of_three()
        answer = self.assess(third)
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(3, answer.criterion_count)
        self.assertIsNone(answer.unavailable_reason)
        self.assertIsNone(answer.unavailable_cause)
        self.assertIsNone(answer.unavailable_at_turn_number)

    def test_criterion_results_are_unchanged(self):
        first, second, third = self.chain_of_three()
        answer = self.assess(third)
        # Two inherited change criteria, then this turn's own.
        self.assertEqual(
            ["unverified", "unverified"],
            [a.result for a in answer.assessments[:2]],
        )
        self.assertEqual(
            "turn_change_evaluated", answer.assessments[2].reason
        )

    def test_a_repaired_lineage_resolves_again(self):
        """The refusal describes stored facts, not a latched state."""
        first, second, third = self.chain_of_three()
        self.make_not_declared(second)
        self.assertEqual(ASSESSMENT_UNAVAILABLE, self.assess(third).state)
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_criteria_continuity SET continuity_state ="
                " 'declared', mode = 'extend', predecessor_snapshot_id = ?"
                " WHERE task_id = ? AND turn_number = ?",
                (self.snap(first), self.task_id, second),
            )
        self.assertEqual(ASSESSMENT_RESOLVED, self.assess(third).state)


class DerivedAndInertEndToEnd(Harness):
    def test_repeated_reads_do_not_mutate_the_database(self):
        _, second, third = self.chain_of_three()
        self.make_not_declared(second)
        self.assess(third)
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
        for _ in range(5):
            service.current_criterion_assessment(self.task_id, third)
        store.close()
        self.assertEqual(before, self.database.read_bytes())

    def test_nothing_is_repaired(self):
        _, second, third = self.chain_of_three()
        self.make_not_declared(second)
        for _ in range(3):
            self.assess(third)
        self.assertEqual("not_declared",
                         self.store.turn_continuity(self.task_id, second).state)

    def test_the_answer_survives_a_process_boundary(self):
        _, second, third = self.chain_of_three()
        self.make_not_declared(second)
        before = self.assess(third).fingerprint
        self.store.close()

        store = TaskStore(self.config)
        self.addCleanup(store.close)
        from cofferdam.workstation.tasks import build_registry

        adapters = build_registry(enable_validation_adapter=True)
        service = TaskService(
            self.config, store, adapters,
            projects=load_projects(self.config, adapters.ids()),
        )
        self.assertEqual(
            before, service.current_criterion_assessment(self.task_id, third).fingerprint
        )

    def test_deleting_the_repository_changes_nothing(self):
        _, second, third = self.chain_of_three()
        self.make_not_declared(second)
        before = self.assess(third)
        shutil.rmtree(self.root)
        self.assertFalse(self.root.exists())
        after = self.assess(third)
        self.assertEqual(before.fingerprint, after.fingerprint)
        self.assertEqual(before.unavailable_cause, after.unavailable_cause)

    def test_no_schema_change_and_no_new_table(self):
        with self.sql() as connection:
            names = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()["value"]
        self.assertEqual(11, int(version))
        for forbidden in ("task_turn_assessment", "task_turn_acceptance",
                          "task_turn_aggregate"):
            self.assertNotIn(forbidden, names)

    def test_the_version_is_three(self):
        self.assertEqual(3, CURRENT_ASSESSMENT_VERSION)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
