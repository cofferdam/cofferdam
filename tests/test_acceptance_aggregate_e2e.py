"""M2K PR21 — the acceptance fold over a real lifecycle and a real repository.

The focused suite constructs envelopes directly, which proves the fold. This one
never does: every aggregate here is derived from rows a real worker and the real
pipeline produced, so it also proves the situations the fold claims to handle are
ones Cofferdam actually reaches.

The walk is the argument. One criterion set, followed across five turns, moves
through `met` → `not_met` → `incomplete` as the project breaks and is repaired —
and then through the two `not_assessable` shapes that must never be confused with
each other or with an outcome.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
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
    CriterionCounts,
)
from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterOutcome,
    TaskAdapter,
)
from cofferdam.workstation.tasks.binding import CURRENT_ASSESSMENT_VERSION
from cofferdam.workstation.tasks.criteria import (
    PREDICATE_PATH_ABSENT,
    PREDICATE_PATH_EXISTS,
)
from cofferdam.workstation.tasks.lineage import (
    REASON_LEGACY_UNKNOWN,
    REASON_NOT_DECLARED,
    REASON_PREDECESSOR_UNAVAILABLE,
)
from cofferdam.workstation.tasks.projects import load_projects
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

PROJECT_ID = "demo"
REPO_ROOT = Path(__file__).resolve().parents[1]


class ScriptedWorker(TaskAdapter):
    adapter_id = "validation"
    display_name = "Scripted"

    def __init__(self):
        self.steps = []

    def capabilities(self):
        return AdapterCapabilities(start=True, followup=True, final_result=True)

    def available(self):
        return True

    def session_available(self, task_id):
        return True

    def _run(self, context):
        root = Path(context.project_root)
        if self.steps:
            self.steps.pop(0)(root)
            # A real worker commits. Without it PR7 has no committed change to
            # decide on and answers `unverified`, which is its correct answer to
            # a different situation than the one these tests are about.
            subprocess.run(("git", "add", "-A"), cwd=root, check=True,
                           capture_output=True)
            subprocess.run(
                ("git", "-c", "user.email=w@example.invalid", "-c",
                 "user.name=Worker", "commit", "-qm", "worker"),
                cwd=root, check=False, capture_output=True,
            )
        return AdapterOutcome(requested_state="ready_for_followup", final_result="done")

    def start(self, context):
        return self._run(context)

    def send_followup(self, context, followup):
        return self._run(context)


class Harness(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.root = self.home / "projects" / PROJECT_ID
        self.root.mkdir(parents=True)

        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "Test")
        (self.root / "seed.txt").write_text("seed\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")

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
        self.worker = ScriptedWorker()

        from cofferdam.workstation.tasks import build_registry

        registry = type(build_registry(enable_validation_adapter=True))((self.worker,))
        self.service = TaskService(
            self.config, self.store, registry,
            projects=load_projects(self.config, registry.ids()),
        )

    def git(self, *arguments):
        subprocess.run(
            ("git",) + arguments, cwd=self.root, check=True, capture_output=True
        )

    @contextmanager
    def sql(self):
        connection = sqlite3.connect(str(self.database))
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    # -- authoring -----------------------------------------------------------

    def exists(self, path):
        return {"kind": "evidence", "predicate": PREDICATE_PATH_EXISTS, "path": path}

    def absent(self, path):
        return {"kind": "evidence", "predicate": PREDICATE_PATH_ABSENT, "path": path}

    def changed(self, path):
        return {"kind": "evidence", "predicate": "path_changed", "path": path}

    def manual(self):
        return {"kind": "manual", "description": "somebody must look at it"}

    def snap(self, turn):
        return self.store.turn_criteria(self.task_id, turn).snapshot_id

    def accept(self, turn):
        return self.service.turn_acceptance(self.task_id, turn)

    def start(self, criteria, step=None):
        self.worker.steps = [step] if step else []
        row, _ = self.service.create_task(
            project_id=PROJECT_ID, adapter_id="validation", prompt="scenario",
            origin="pwa", criteria=criteria, continuity={"mode": "root"},
        )
        self.task_id = row.task_id
        return row.task_id

    def followup(self, criteria, continuity, step=None):
        self.worker.steps = [step] if step else []
        self.service.send_followup(
            self.task_id, "more", criteria=criteria, continuity=continuity
        )


class TheWholeWalk(Harness):
    def test_the_whole_walk(self):
        # 1. Schema v11, unchanged: PR21 is derived read semantics.
        self.assertEqual(11, SCHEMA_VERSION)

        # 2. Turn 1: a state criterion the worker satisfies and a change
        #    criterion PR7 decides. Both met -> assessable / met.
        def turn_one(root):
            (root / "a.txt").write_text("made\n", encoding="utf-8")
            (root / "x.txt").write_text("changed\n", encoding="utf-8")

        self.start([self.exists("a.txt"), self.changed("x.txt")], turn_one)
        first = self.accept(1)
        self.assertEqual(AVAILABILITY_ASSESSABLE, first.availability)
        self.assertEqual(OUTCOME_MET, first.outcome)
        self.assertEqual(CriterionCounts(2, 2, 0, 0), first.counts)
        self.assertIs(False, first.requires_human)
        self.assertEqual(AGGREGATOR_VERSION, first.aggregator_version)

        # 3. Turn 2 extends. The worker deletes a.txt, so the inherited state
        #    criterion is re-assessed against turn 2's boundary and fails; the
        #    inherited change criterion is unverified; the new state criterion is
        #    met. not_met dominates the unverified.
        def turn_two(root):
            (root / "a.txt").unlink()

        self.followup(
            [self.absent("b.txt")],
            {"mode": "extend", "predecessor_snapshot_id": self.snap(1)},
            turn_two,
        )
        second = self.accept(2)
        self.assertEqual(AVAILABILITY_ASSESSABLE, second.availability)
        self.assertEqual(OUTCOME_NOT_MET, second.outcome)
        self.assertEqual(3, second.counts.total)
        self.assertEqual(1, second.counts.not_met)
        self.assertEqual(1, second.counts.unverified)
        self.assertEqual(1, second.counts.met)
        self.assertIs(False, second.requires_human)

        # 4. Turn 3 repairs a.txt. Nothing is not_met any more, and the inherited
        #    change criterion is still unverified -> incomplete.
        def turn_three(root):
            (root / "a.txt").write_text("back\n", encoding="utf-8")
            (root / "y.txt").write_text("y\n", encoding="utf-8")

        self.followup(
            [self.changed("y.txt")],
            {"mode": "extend", "predecessor_snapshot_id": self.snap(2)},
            turn_three,
        )
        third = self.accept(3)
        # Nothing is not_met any more; the inherited change criterion from turn 1
        # is still unverified, and one unverified is enough.
        self.assertEqual(OUTCOME_INCOMPLETE, third.outcome)
        self.assertEqual(0, third.counts.not_met)
        self.assertGreater(third.counts.unverified, 0)
        self.assertGreater(third.counts.met, 0)

        # 5. Turn 1's own answer never moved.
        self.assertEqual(OUTCOME_MET, self.accept(1).outcome)
        self.assertEqual(first.fingerprint, self.accept(1).fingerprint)

        # 6. Turn 4 adds a manual criterion: still incomplete, and now a person
        #    is needed. requires_human is orthogonal to the outcome.
        self.followup(
            [self.manual()],
            {"mode": "extend", "predecessor_snapshot_id": self.snap(3)},
        )
        fourth = self.accept(4)
        self.assertEqual(OUTCOME_INCOMPLETE, fourth.outcome)
        self.assertIs(True, fourth.requires_human)

        # 7. Turn 5 replaces with no criteria at all: a KNOWN empty population.
        self.followup(
            [], {"mode": "replace", "predecessor_snapshot_id": self.snap(4)}
        )
        fifth = self.accept(5)
        self.assertEqual(AVAILABILITY_NOT_ASSESSABLE, fifth.availability)
        self.assertEqual(REASON_NO_STRUCTURED_CRITERIA, fifth.availability_reason)
        self.assertIsNone(fifth.outcome)
        self.assertEqual(CriterionCounts(0, 0, 0, 0), fifth.counts)
        self.assertIs(False, fifth.requires_human)
        self.assertTrue(fifth.population_known)

        # 8. Deleting the repository changes nothing: every answer is derived
        #    from frozen rows.
        before = [self.accept(turn).fingerprint for turn in range(1, 6)]
        shutil.rmtree(self.root)
        self.assertFalse(self.root.exists())
        self.assertEqual(before, [self.accept(turn).fingerprint for turn in range(1, 6)])


class UnknownPopulationEndToEnd(Harness):
    """The other `not_assessable`, and it must not look like the first."""

    def chain(self):
        self.start([self.changed("x.txt")],
                   lambda root: (root / "x.txt").write_text("x\n", encoding="utf-8"))
        self.followup([self.changed("y.txt")],
                      {"mode": "extend", "predecessor_snapshot_id": self.snap(1)},
                      lambda root: (root / "y.txt").write_text("y\n", encoding="utf-8"))
        self.followup([self.changed("z.txt")],
                      {"mode": "extend", "predecessor_snapshot_id": self.snap(2)},
                      lambda root: (root / "z.txt").write_text("z\n", encoding="utf-8"))

    def make_legacy(self, turn):
        with self.sql() as connection:
            connection.execute(
                "DELETE FROM task_turn_criteria_continuity"
                " WHERE task_id = ? AND turn_number = ?",
                (self.task_id, turn),
            )

    def make_not_declared(self, turn):
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_criteria_continuity SET continuity_state ="
                " 'not_declared', mode = NULL, predecessor_snapshot_id = NULL,"
                " relation_count = 0 WHERE task_id = ? AND turn_number = ?",
                (self.task_id, turn),
            )

    def test_a_not_declared_target_keeps_its_exact_reason(self):
        self.chain()
        self.make_not_declared(3)
        answer = self.accept(3)
        self.assertEqual(AVAILABILITY_NOT_ASSESSABLE, answer.availability)
        self.assertEqual(REASON_NOT_DECLARED, answer.availability_reason)
        self.assertIsNone(answer.outcome)

    def test_a_legacy_target_keeps_its_exact_reason(self):
        self.chain()
        self.make_legacy(3)
        self.assertEqual(REASON_LEGACY_UNKNOWN, self.accept(3).availability_reason)

    def test_the_population_is_unknown_not_zero(self):
        """The red line, over real rows."""
        self.chain()
        self.make_not_declared(3)
        answer = self.accept(3)
        self.assertIsNone(answer.counts)
        self.assertIsNone(answer.requires_human)
        self.assertFalse(answer.population_known)

    def test_it_is_never_incomplete(self):
        self.chain()
        self.make_not_declared(3)
        self.assertNotEqual(OUTCOME_INCOMPLETE, self.accept(3).outcome)

    def test_a_nested_predecessor_cause_survives_the_fold(self):
        """PR20's fix would end here if the aggregate dropped it."""
        self.chain()
        self.make_not_declared(2)
        undeclared = self.accept(3)
        self.assertEqual(REASON_PREDECESSOR_UNAVAILABLE, undeclared.availability_reason)
        self.assertEqual(REASON_NOT_DECLARED, undeclared.unavailable_cause)
        self.assertEqual(2, undeclared.unavailable_at_turn_number)

        self.make_legacy(2)
        legacy = self.accept(3)
        self.assertEqual(REASON_LEGACY_UNKNOWN, legacy.unavailable_cause)
        self.assertNotEqual(undeclared.unavailable_cause, legacy.unavailable_cause)
        self.assertNotEqual(undeclared.fingerprint, legacy.fingerprint)

    def test_known_zero_and_unknown_population_never_share_an_identity(self):
        self.chain()
        self.make_not_declared(3)
        unknown = self.accept(3)
        # A separate task whose replace declares no criteria at all.
        other = Harness()
        other.setUp()
        other.start([other.changed("x.txt")],
                    lambda root: (root / "x.txt").write_text("x\n", encoding="utf-8"))
        other.followup([], {"mode": "replace",
                            "predecessor_snapshot_id": other.snap(1)})
        known = other.accept(2)
        other.store.close()
        self.assertEqual(known.availability, unknown.availability)
        self.assertNotEqual(known.availability_reason, unknown.availability_reason)
        self.assertIsNotNone(known.counts)
        self.assertIsNone(unknown.counts)
        self.assertNotEqual(known.fingerprint, unknown.fingerprint)


class StructuralUnavailableEndToEnd(Harness):
    """A corrupted evidence row refuses, and never becomes an outcome."""

    def prepared(self):
        self.start(
            [self.exists("a.txt"), self.changed("x.txt")],
            lambda root: (
                (root / "a.txt").write_text("a\n", encoding="utf-8"),
                (root / "x.txt").write_text("x\n", encoding="utf-8"),
            ),
        )
        self.assertEqual(OUTCOME_MET, self.accept(1).outcome)

    def test_a_corrupted_final_state_row_refuses(self):
        self.prepared()
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_final_state SET lineage_fingerprint = ?"
                " WHERE task_id = ?",
                ("f" * 64, self.task_id),
            )
        answer = self.accept(1)
        self.assertEqual(AVAILABILITY_NOT_ASSESSABLE, answer.availability)
        self.assertEqual("final_state_lineage_mismatch", answer.availability_reason)
        self.assertIsNone(answer.outcome)
        self.assertIsNone(answer.counts)

    def test_a_missing_evaluation_is_operational_and_not_an_outcome(self):
        self.prepared()
        with self.sql() as connection:
            connection.execute(
                "DELETE FROM task_turn_evaluations WHERE task_id = ?", (self.task_id,)
            )
        answer = self.accept(1)
        self.assertEqual("evaluation_not_recorded", answer.availability_reason)
        self.assertIsNone(answer.outcome)
        self.assertNotEqual(OUTCOME_INCOMPLETE, answer.outcome)

    def test_nothing_is_repaired_by_asking(self):
        self.prepared()
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_final_state SET lineage_fingerprint = ?"
                " WHERE task_id = ?",
                ("f" * 64, self.task_id),
            )
        for _ in range(3):
            self.accept(1)
        with self.sql() as connection:
            row = connection.execute(
                "SELECT lineage_fingerprint FROM task_turn_final_state"
                " WHERE task_id = ?",
                (self.task_id,),
            ).fetchone()
        self.assertEqual("f" * 64, row["lineage_fingerprint"])


class DerivedAndInertEndToEnd(Harness):
    def prepared(self):
        self.start(
            [self.exists("a.txt"), self.changed("x.txt"), self.manual()],
            lambda root: (
                (root / "a.txt").write_text("a\n", encoding="utf-8"),
                (root / "x.txt").write_text("x\n", encoding="utf-8"),
            ),
        )

    def test_repeated_reads_leave_the_database_byte_identical(self):
        self.prepared()
        self.accept(1)
        self.store.close()
        before = self.database.read_bytes()

        store = TaskStore(self.config)
        self.addCleanup(store.close)
        from cofferdam.workstation.tasks import build_registry

        registry = type(build_registry(enable_validation_adapter=True))((self.worker,))
        service = TaskService(
            self.config, store, registry,
            projects=load_projects(self.config, registry.ids()),
        )
        for _ in range(5):
            service.turn_acceptance(self.task_id, 1)
        store.close()
        self.assertEqual(before, self.database.read_bytes())

    def test_the_answer_survives_a_process_boundary(self):
        self.prepared()
        before = self.accept(1).fingerprint
        self.store.close()
        store = TaskStore(self.config)
        self.addCleanup(store.close)
        from cofferdam.workstation.tasks import build_registry

        registry = type(build_registry(enable_validation_adapter=True))((self.worker,))
        service = TaskService(
            self.config, store, registry,
            projects=load_projects(self.config, registry.ids()),
        )
        self.assertEqual(before, service.turn_acceptance(self.task_id, 1).fingerprint)

    def test_no_table_was_added(self):
        self.prepared()
        self.accept(1)
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
        for forbidden in ("task_turn_acceptance", "task_turn_aggregate",
                          "task_acceptance", "acceptance"):
            self.assertNotIn(forbidden, names)

    def test_the_service_takes_one_assessment_and_folds_it(self):
        """No second read of lineage, evaluation or final state."""
        self.prepared()
        envelope = self.service.current_criterion_assessment(self.task_id, 1)
        answer = self.accept(1)
        self.assertEqual(envelope.fingerprint, answer.assessment_fingerprint)


class NegativeSpaceTests(unittest.TestCase):
    def test_versions(self):
        from cofferdam.workstation.tasks.continuity import CONTINUITY_MODEL_VERSION
        from cofferdam.workstation.tasks.criteria import CRITERIA_MODEL_VERSION
        from cofferdam.workstation.tasks.evaluation import EVALUATOR_VERSION
        from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION
        from cofferdam.workstation.tasks.finalstate import FINAL_STATE_OBSERVER_VERSION
        from cofferdam.workstation.tasks.lineage import RESOLVER_VERSION

        self.assertEqual(11, SCHEMA_VERSION)
        self.assertEqual(4, CURRENT_ASSESSMENT_VERSION)
        self.assertEqual(1, AGGREGATOR_VERSION)
        self.assertEqual(1, EVALUATOR_VERSION)
        self.assertEqual(2, FINAL_STATE_OBSERVER_VERSION)
        self.assertEqual(1, RESOLVER_VERSION)
        self.assertEqual(1, CONTINUITY_MODEL_VERSION)
        self.assertEqual(1, CRITERIA_MODEL_VERSION)
        self.assertEqual(3, ASSEMBLER_VERSION)

    def test_no_migration_was_added(self):
        from cofferdam.workstation.tasks import store as store_module

        tree = ast.parse(Path(store_module.__file__).read_text(encoding="utf-8"))
        migrations = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_migrate")
        }
        self.assertNotIn("_migrate_to_v12", migrations)

    def test_the_store_knows_nothing_about_acceptance(self):
        """From the AST — the store may *discuss* a future aggregate in prose."""
        from cofferdam.workstation.tasks import store as store_module

        tree = ast.parse(Path(store_module.__file__).read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.rsplit(".", 1)[-1])
            elif isinstance(node, ast.Import):
                modules.update(a.name.rsplit(".", 1)[-1] for a in node.names)
        for forbidden in ("AcceptanceAggregate", "AGGREGATOR_VERSION", "aggregate"):
            self.assertNotIn(forbidden, names)
        self.assertNotIn("acceptance", modules)
        # The DDL legitimately says "acceptance criterion" in its comments. What
        # must not exist is a table or column named for one, so strip comments
        # before looking rather than banning the word.
        ddl = re.sub(r"--[^\n]*", "", store_module._SCHEMA)
        for forbidden in ("acceptance", "aggregate", "availability",
                          "requires_human", "outcome_"):
            self.assertNotIn(forbidden, ddl)

    def test_no_route_or_bridge_operation_reaches_it(self):
        surfaces = [
            REPO_ROOT / "cofferdam" / "workstation" / "service.py",
            REPO_ROOT / "cofferdam" / "workstation" / "actions.py",
        ]
        bridge = REPO_ROOT / "cofferdam" / "actions_bridge"
        if bridge.exists():
            surfaces.extend(sorted(bridge.rglob("*.py")))
        for path in surfaces:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for forbidden in ("turn_acceptance", "AcceptanceAggregate",
                              "AGGREGATOR_VERSION", "acceptance_fingerprint"):
                self.assertNotIn(forbidden, text, str(path))

    def test_the_pr8_assessment_response_is_widened_additively_only(self):
        """M2K PR22 published acceptance. PR21 had asserted it had not — correctly
        at the time, and overtaken rather than weakened.

        What still has to hold is that the widening is **additive**: the two
        sections PR8 shipped keep their exact keys and meanings, and the third is
        optional so the older shape stays constructible.
        """
        from cofferdam.workstation.tasks.assessment import assessment_view
        from cofferdam.workstation.tasks.criteria import CriteriaSnapshot

        snapshot = CriteriaSnapshot(
            task_id="task_01aaaaaaaaaaaaaaaaaaaaaaaa",
            turn_number=1,
            state="not_provided",
        )
        without = assessment_view(
            task_id="task_01aaaaaaaaaaaaaaaaaaaaaaaa",
            turn_number=1,
            snapshot=snapshot,
            record=None,
            turn_open=False,
        )
        self.assertEqual(
            {"version", "task_id", "turn_number", "criteria", "evaluation"},
            set(without),
        )

        from cofferdam.workstation.tasks.acceptance import aggregate
        from cofferdam.workstation.tasks.binding import bind, CurrentAssessment

        with_acceptance = assessment_view(
            task_id="task_01aaaaaaaaaaaaaaaaaaaaaaaa",
            turn_number=1,
            snapshot=snapshot,
            record=None,
            turn_open=False,
            acceptance=aggregate(
                CurrentAssessment(
                    task_id="task_01aaaaaaaaaaaaaaaaaaaaaaaa",
                    target_turn_number=1,
                    assessment_version=CURRENT_ASSESSMENT_VERSION,
                    state="resolved",
                    fingerprint="e" * 64,
                )
            ),
        )
        self.assertEqual(set(without) | {"acceptance"}, set(with_acceptance))
        for key in ("version", "task_id", "turn_number", "criteria", "evaluation"):
            self.assertEqual(without[key], with_acceptance[key])

    def test_the_pwa_gained_nothing(self):
        base = REPO_ROOT / "cofferdam" / "workstation"
        for pattern in ("*.js", "*.html", "*.css"):
            for path in base.rglob(pattern):
                text = path.read_text(encoding="utf-8", errors="ignore")
                for forbidden in ("turn_acceptance", "acceptance_fingerprint",
                                  "requires_human"):
                    self.assertNotIn(forbidden, text, str(path))

    def test_no_global_task_verdict_exists(self):
        from cofferdam.workstation.tasks.service import TaskService

        for forbidden in ("task_acceptance", "task_verdict", "overall_acceptance",
                          "latest_acceptance", "project_acceptance"):
            self.assertFalse(hasattr(TaskService, forbidden))

    def test_no_check_runner_exists(self):
        from cofferdam.workstation.tasks import acceptance

        for forbidden in ("run_check", "named_check", "CheckRunner", "check_id"):
            self.assertFalse(hasattr(acceptance, forbidden))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
