"""M2K PR16 — the whole walk, in one isolated home, plus the negative space.

Four turns declaring four different relationships, each assessed at its own
boundary, demonstrating the one thing this PR exists to establish: *which
requirements are currently in force* and *what can honestly be said about each of
them here* are now answerable from frozen rows alone — derived, deterministic,
replayable, and honestly unavailable where the evidence does not reach.

The negative space is asserted structurally rather than by substring search: no
state predicate, no evaluator movement, no aggregate, no `AGGREGATOR_VERSION`, no
schema change, no route, no bridge operation and no PWA control.
"""

from __future__ import annotations

import ast
import hashlib
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
    CURRENT_ASSESSMENT_VERSION,
    DOMAIN_NOT_APPLICABLE,
    DOMAIN_TURN_CHANGE,
    REASON_INHERITED_CHANGE_NOT_CURRENT,
    REASON_MANUAL_AUTHORITY,
    REASON_TURN_CHANGE_EVALUATED,
)
from cofferdam.workstation.tasks.continuity import (
    CONTINUITY_MODEL_VERSION,
    validate_declaration,
)
from cofferdam.workstation.tasks.criteria import (
    CRITERIA_MODEL_VERSION,
    EVIDENCE_PREDICATES,
    validate_criteria,
)
from cofferdam.workstation.tasks.evaluation import EVALUATOR_VERSION, RESULT_UNVERIFIED
from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION
from cofferdam.workstation.tasks.finalstate import FINAL_STATE_OBSERVER_VERSION
from cofferdam.workstation.tasks.identity import new_task_id
from cofferdam.workstation.tasks.lineage import RESOLVER_VERSION
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

PROJECT_ID = "demo"
REPO_ROOT = Path(__file__).resolve().parents[1]


class CurrentAssessmentEndToEnd(unittest.TestCase):
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
            config, self.store, adapters, projects=load_projects(config, adapters.ids())
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

    def manual(self):
        return {"kind": "manual", "description": "somebody must look at it"}

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
        self.service.evaluate_closed_turns(self.task_id)
        return number

    def snap(self, turn):
        return self.store.turn_criteria(self.task_id, turn).snapshot_id

    def criteria_of(self, turn):
        return self.store.turn_criteria(self.task_id, turn).criteria

    def assess(self, turn):
        return self.service.current_criterion_assessment(self.task_id, turn)

    def shape(self, turn):
        """``(path-or-manual, source turn, result, reason)`` per active criterion."""
        return [
            (
                item.predicate or item.kind,
                item.source_turn_number,
                item.result,
                item.reason,
            )
            for item in self.assess(turn).assessments
        ]

    def digest(self):
        return hashlib.sha256(self.database.read_bytes()).hexdigest()

    # -- the walk -----------------------------------------------------------

    def test_the_whole_walk(self):
        # 1. PR16 adds no schema of its own; v11 is PR17's.
        self.assertGreaterEqual(SCHEMA_VERSION, 10)

        # 2-3. Turn 1, root: one change criterion and one manual one.
        first = self.turn([self.change("a"), self.manual()], {"mode": "root"})
        self.assertEqual(1, first)

        # 4. Both are current-turn criteria. The change one binds to turn 1's own
        # PR7 judgement; the manual one is unverified for its own reason.
        answer = self.assess(first)
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(2, answer.criterion_count)
        change_one, manual_one = answer.assessments
        self.assertEqual(DOMAIN_TURN_CHANGE, change_one.domain)
        self.assertEqual(REASON_TURN_CHANGE_EVALUATED, change_one.reason)
        self.assertEqual(DOMAIN_NOT_APPLICABLE, manual_one.domain)
        self.assertEqual(REASON_MANUAL_AUTHORITY, manual_one.reason)
        self.assertEqual(RESULT_UNVERIFIED, manual_one.result)

        # 5. Its provenance is the exact stored PR7 judgement, by fingerprint.
        stored = self.store.evaluation(self.task_id, first)
        self.assertEqual(stored.evaluation_fingerprint, change_one.evidence_fingerprint)
        self.assertEqual(stored.results[0].result, change_one.result)
        # And the manual one fabricates none.
        self.assertIsNone(manual_one.evidence_fingerprint)

        # 6-7. Turn 2 extends with a new change criterion.
        second = self.turn(
            [self.change("b")],
            {"mode": "extend", "predecessor_snapshot_id": self.snap(first)},
        )
        self.assertEqual(
            [
                ("path_changed", 1, RESULT_UNVERIFIED, REASON_INHERITED_CHANGE_NOT_CURRENT),
                ("manual", 1, RESULT_UNVERIFIED, REASON_MANUAL_AUTHORITY),
                ("path_changed", 2, self.assess(second).assessments[2].result,
                 REASON_TURN_CHANGE_EVALUATED),
            ],
            self.shape(second),
        )

        # 8. The inherited criterion carries no machine evidence identity, and
        # turn 1's judgement is NOT reused as turn 2's answer.
        inherited = self.assess(second).assessments[0]
        self.assertIsNone(inherited.evidence_fingerprint)
        self.assertNotEqual(
            stored.evaluation_fingerprint, inherited.evidence_fingerprint
        )
        self.assertTrue(inherited.inherited)
        self.assertEqual(1, inherited.source_turn_number)
        self.assertEqual(2, inherited.target_turn_number)

        # 9. Turn 1's own assessment is unchanged by turn 2 having happened.
        self.assertEqual(answer, self.assess(first))

        # 10-11. Turn 3 revises: C supersedes A.
        original_a = self.criteria_of(first)[0].criterion_id
        third = self.turn(
            [self.change("c")],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(second),
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": original_a}
                ],
            },
        )
        third_answer = self.assess(third)
        identifiers = [item.criterion_id for item in third_answer.assessments]

        # 12. A is gone entirely — not unverified, absent.
        self.assertNotIn(original_a, identifiers)
        self.assertEqual(
            [
                ("manual", 1, RESULT_UNVERIFIED, REASON_MANUAL_AUTHORITY),
                ("path_changed", 2, RESULT_UNVERIFIED, REASON_INHERITED_CHANGE_NOT_CURRENT),
                ("path_changed", 3, third_answer.assessments[2].result,
                 REASON_TURN_CHANGE_EVALUATED),
            ],
            self.shape(third),
        )

        # 13-14. Turn 4 replaces: only D survives, and nothing older appears.
        fourth = self.turn(
            [self.change("d")],
            {"mode": "replace", "predecessor_snapshot_id": self.snap(third)},
        )
        fourth_answer = self.assess(fourth)
        self.assertEqual(1, fourth_answer.criterion_count)
        only = fourth_answer.assessments[0]
        self.assertEqual(4, only.source_turn_number)
        self.assertFalse(only.inherited)
        self.assertEqual(DOMAIN_TURN_CHANGE, only.domain)
        self.assertEqual(
            self.store.evaluation(self.task_id, fourth).evaluation_fingerprint,
            only.evidence_fingerprint,
        )

        # 15. Every assessment binds the lineage that selected it.
        for number in (first, second, third, fourth):
            resolved = self.service.resolve_active_criteria(self.task_id, number)
            self.assertEqual(
                resolved.fingerprint, self.assess(number).lineage_fingerprint
            )

        # 16. Deleting the repository changes nothing: no world dependency.
        before = [self.assess(n) for n in (first, second, third, fourth)]
        shutil.rmtree(self.root)
        self.assertEqual(before, [self.assess(n) for n in (first, second, third, fourth)])

        # 17. And asking costs the database nothing.
        self.store.close()
        settled = self.digest()
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
        for number in (first, second, third, fourth):
            for _ in range(3):
                service.current_criterion_assessment(self.task_id, number)
        store.close()
        self.assertEqual(settled, self.digest())

        # 18. No version around it moved.
        self.assertEqual(2, CURRENT_ASSESSMENT_VERSION)
        self.assertEqual(1, EVALUATOR_VERSION)
        self.assertEqual(3, ASSEMBLER_VERSION)
        self.assertEqual(1, RESOLVER_VERSION)
        self.assertEqual(1, FINAL_STATE_OBSERVER_VERSION)
        self.assertEqual(1, CRITERIA_MODEL_VERSION)
        self.assertEqual(1, CONTINUITY_MODEL_VERSION)


class NegativeSpaceTests(unittest.TestCase):
    """Asserted from the syntax tree, not from substring searches."""

    def python_sources(self):
        for path in sorted((REPO_ROOT / "cofferdam").rglob("*.py")):
            yield path, path.read_text(encoding="utf-8")

    def defined_names(self, text):
        """Things this module actually *defines* — no string literals.

        Kept separate from :meth:`vocabulary` because a module may legitimately
        mention a name it does not implement: ``criteria.py`` lists ``check_id``
        among the server-owned fields it **refuses**, which is the opposite of
        defining one.
        """
        names = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                names.add(node.attr)
        return names

    def vocabulary(self, text):
        """Definitions **and** string literals, for closed-vocabulary checks."""
        names = self.defined_names(text)
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value)
        return names

    def test_no_state_predicate_is_evaluated(self):
        """M2K PR17 made them representable; nothing decides them.

        The original form of this test asserted the vocabulary held only the
        three change predicates. PR17 legitimately widened it, so what is
        asserted here is the invariant that actually survives: the binder binds
        change predicates only, and the evaluator has no handler for a state one.
        """
        from cofferdam.workstation.tasks import evaluation
        from cofferdam.workstation.tasks.binding import CHANGE_PREDICATES
        from cofferdam.workstation.tasks.criteria import STATE_PREDICATES

        self.assertEqual(("path_changed", "path_operation", "rename"), CHANGE_PREDICATES)
        self.assertEqual(("path_exists", "path_absent"), STATE_PREDICATES)
        for predicate in STATE_PREDICATES:
            self.assertNotIn(predicate, CHANGE_PREDICATES)
            self.assertNotIn(predicate, evaluation._PREDICATES)

    def test_the_criteria_predicate_constraint_is_unchanged(self):
        from cofferdam.workstation.tasks import store as store_module

        self.assertIn(
            "predicate IN ('path_changed', 'path_operation', 'rename',",
            store_module._SCHEMA,
        )
        self.assertIn("'path_exists', 'path_absent')", store_module._SCHEMA)

    def test_no_module_defines_an_aggregator_version(self):
        import re

        for path, text in self.python_sources():
            self.assertEqual(
                [],
                re.findall(r"^\s*AGGREGATOR_VERSION\s*[:=]", text, re.M),
                "%s defines AGGREGATOR_VERSION" % path,
            )

    def test_no_aggregate_or_runner_appeared(self):
        forbidden = {
            "all_met", "aggregate", "task_verdict", "acceptance_outcome",
            "CheckRunner", "run_check", "check_id", "overall_result", "passed",
        }
        for path, text in self.python_sources():
            defined = self.defined_names(text) & forbidden
            self.assertEqual(set(), defined, "%s defines %s" % (path, defined))

    def test_the_binder_adds_no_table(self):
        from cofferdam.workstation.tasks import store as store_module

        self.assertGreaterEqual(store_module.SCHEMA_VERSION, 10)
        for forbidden in ("current_assessment", "criterion_assessment", "binding"):
            self.assertNotIn(forbidden, store_module._SCHEMA)

    def test_no_new_http_route_reaches_the_binder(self):
        """The HTTP layer, not the task package that legitimately defines it."""
        surfaces = [REPO_ROOT / "cofferdam" / "workstation" / "service.py",
                    REPO_ROOT / "cofferdam" / "workstation" / "actions.py"]
        bridge = REPO_ROOT / "cofferdam" / "actions_bridge"
        if bridge.exists():
            surfaces.extend(sorted(bridge.rglob("*.py")))
        for path in surfaces:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("current_criterion_assessment", text, str(path))
            self.assertNotIn("CurrentAssessment", text, str(path))

    def test_the_assessment_response_is_unchanged(self):
        from cofferdam.workstation.tasks import assessment as assessment_module

        text = (
            Path(assessment_module.__file__).read_text(encoding="utf-8")
        )
        for forbidden in ("current_criterion_assessment", "CurrentAssessment", "binding"):
            self.assertNotIn(forbidden, text)

    def test_the_pwa_gained_no_current_assessment_control(self):
        base = REPO_ROOT / "cofferdam" / "workstation"
        for pattern in ("*.js", "*.html", "*.css"):
            for path in base.rglob(pattern):
                text = path.read_text(encoding="utf-8", errors="ignore")
                self.assertNotIn("current_criterion_assessment", text, str(path))
                self.assertNotIn("currentAssessment", text, str(path))

    def test_the_binder_is_not_reachable_from_the_bridge(self):
        base = REPO_ROOT / "cofferdam" / "actions_bridge"
        if base.exists():
            for path in base.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                modules = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        modules.add(node.module.rsplit(".", 1)[-1])
                    elif isinstance(node, ast.Import):
                        modules.update(a.name.rsplit(".", 1)[-1] for a in node.names)
                self.assertNotIn("binding", modules, str(path))

    def test_the_binder_cannot_execute_anything(self):
        path = REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "binding.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = {
            getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        for forbidden in ("run", "Popen", "system", "exec", "eval", "open", "connect"):
            self.assertNotIn(forbidden, called)

    def test_the_binder_imports_no_module_that_touches_the_world(self):
        """M2K PR18 legitimises `finalstate`; it legitimises nothing else.

        PR16 asserted the binder imported no final-state module at all. That
        assertion has been overtaken rather than weakened: PR18 reads stored
        observations, so the module is allowed and the store, the service, the
        evaluator, the observers and every stdlib module that can reach a disk,
        a clock or a network are still not.
        """
        path = REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "binding.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
            elif isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
        self.assertEqual(
            {"__future__", "dataclasses", "typing", "hashlib", "criteria",
             "evaluation", "finalstate"},
            modules,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
