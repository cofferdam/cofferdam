"""M2K PR10 — the whole walk, in one isolated home.

Four turns, each declaring a different relationship to the one before it, plus
the cases nobody declares anything. What this module exists to demonstrate is
that the lineage a future task-level aggregate would stand on is **complete,
frozen before the worker, and honest about what it does not know** — and that
nothing in this PR computes an aggregate from it.

Also asserts the negative space explicitly: no `AGGREGATOR_VERSION`, no task
verdict, no check runner, no command execution, no API or bridge widening.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.config import load_config
from cofferdam.workstation.tasks.continuity import (
    CONTINUITY_DECLARED,
    CONTINUITY_EXTEND,
    CONTINUITY_LEGACY_UNKNOWN,
    CONTINUITY_MODEL_VERSION,
    CONTINUITY_NOT_DECLARED,
    CONTINUITY_REPLACE,
    CONTINUITY_REVISE,
    CONTINUITY_ROOT,
)
from cofferdam.workstation.tasks.criteria import validate_criteria
from cofferdam.workstation.tasks.continuity import validate_declaration
from cofferdam.workstation.tasks.errors import ContinuityInvalid
from cofferdam.workstation.tasks.evaluation import EVALUATOR_VERSION
from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

PROJECT_ID = "demo"
REPO_ROOT = Path(__file__).resolve().parents[1]


class ContinuityEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.root = self.home / "projects" / PROJECT_ID
        self.root.mkdir(parents=True)

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

    # -- helpers ------------------------------------------------------------

    def criteria_for(self, *paths):
        return [
            {"kind": "evidence", "predicate": "path_changed", "path": p} for p in paths
        ]

    def close_turn(self, task_id, turn_number):
        with sqlite3.connect(str(self.database)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute(
                "INSERT OR IGNORE INTO task_turns (task_id, turn_number, provider,"
                " source, started_at, completed_at, outcome) VALUES (?,?,"
                "'validation','pwa','x','y','completed')",
                (task_id, turn_number),
            )

    def next_turn(self, task_id, criteria, continuity):
        """One follow-up turn's pre-work, in the order the service does it."""
        self.store.reserve_turn_criteria(
            task_id, validate_criteria(criteria), recorded_at="2026-08-16T05:00:00Z"
        )
        turn = self.store.reserve_turn_continuity(
            task_id,
            validate_declaration(continuity),
            recorded_at="2026-08-16T05:00:01Z",
        )
        self.store.mark_criteria_dispatch_started(task_id, turn)
        self.store.mark_continuity_dispatch_started(task_id, turn)
        return turn

    def snapshot_id(self, task_id, turn):
        return self.store.turn_criteria(task_id, turn).snapshot_id

    def criterion_ids(self, task_id, turn):
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            return [
                r[0]
                for r in connection.execute(
                    "SELECT criterion_id FROM task_turn_criterion_items"
                    " WHERE task_id=? AND turn_number=? ORDER BY ordinal",
                    (task_id, turn),
                )
            ]
        finally:
            connection.close()

    # -- the walk -----------------------------------------------------------

    def test_the_walk(self):
        # 1. schema and the versions that must NOT have moved
        self.assertGreaterEqual(SCHEMA_VERSION, 9, "PR10 added continuity at v9")
        self.assertEqual(1, EVALUATOR_VERSION, "PR10 does not touch the evaluator")
        self.assertEqual(3, ASSEMBLER_VERSION, "PR10 does not touch the assembler")
        self.assertEqual(1, CONTINUITY_MODEL_VERSION)

        # 2. turn one: a real dispatch, declared `root`
        row, _ = self.service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt="scenario: complete",
            origin="pwa",
            criteria=self.criteria_for("src/a.py", "src/b.py"),
            continuity={"mode": CONTINUITY_ROOT},
        )
        task = row.task_id
        first = self.store.turn_continuity(task, 1)
        self.assertEqual(CONTINUITY_DECLARED, first.state)
        self.assertEqual(CONTINUITY_ROOT, first.mode)
        self.assertIsNone(first.predecessor_snapshot_id)
        self.assertEqual(0, first.relation_count)
        # 3. and it was bound to the real turn once the turn opened
        self.assertEqual(
            "turn_opened", self.store.turn_continuity_dispatch_state(task, 1)
        )
        snapshot_one = self.snapshot_id(task, 1)
        retired = self.criterion_ids(task, 1)

        # 4. turn two extends turn one
        self.close_turn(task, 1)
        self.next_turn(
            task,
            self.criteria_for("src/c.py"),
            {"mode": CONTINUITY_EXTEND, "predecessor_snapshot_id": snapshot_one},
        )
        second = self.store.turn_continuity(task, 2)
        self.assertEqual(CONTINUITY_EXTEND, second.mode)
        self.assertEqual(snapshot_one, second.predecessor_snapshot_id)
        self.assertEqual(0, second.relation_count)
        snapshot_two = self.snapshot_id(task, 2)

        # 5. turn three replaces turn two's set outright
        self.close_turn(task, 2)
        self.next_turn(
            task,
            self.criteria_for("src/d.py"),
            {"mode": CONTINUITY_REPLACE, "predecessor_snapshot_id": snapshot_two},
        )
        third = self.store.turn_continuity(task, 3)
        self.assertEqual(CONTINUITY_REPLACE, third.mode)
        self.assertEqual(snapshot_two, third.predecessor_snapshot_id)
        self.assertEqual(0, third.relation_count)
        # nothing was deleted or rewritten
        self.assertEqual(2, self.store.turn_criteria(task, 1).criterion_count)
        self.assertEqual(1, self.store.turn_criteria(task, 2).criterion_count)
        snapshot_three = self.snapshot_id(task, 3)
        third_criteria = self.criterion_ids(task, 3)

        # 6. turn four partially revises turn three
        self.close_turn(task, 3)
        self.next_turn(
            task,
            self.criteria_for("src/e.py", "src/f.py"),
            {
                "mode": CONTINUITY_REVISE,
                "predecessor_snapshot_id": snapshot_three,
                "supersedes": [
                    {
                        "criterion_ordinal": 1,
                        "predecessor_criterion_id": third_criteria[0],
                    }
                ],
            },
        )
        fourth = self.store.turn_continuity(task, 4)
        self.assertEqual(CONTINUITY_REVISE, fourth.mode)
        self.assertEqual(1, fourth.relation_count)
        self.assertEqual(
            third_criteria[0], fourth.relations[0].predecessor_criterion_id
        )
        self.assertIn(fourth.relations[0].criterion_id, self.criterion_ids(task, 4))
        # the superseded criterion is still there, unchanged
        self.assertEqual(1, self.store.turn_criteria(task, 3).criterion_count)

        # 7. turn five: nobody declared anything
        self.close_turn(task, 4)
        self.next_turn(task, self.criteria_for("src/g.py"), None)
        fifth = self.store.turn_continuity(task, 5)
        self.assertEqual(CONTINUITY_NOT_DECLARED, fifth.state)
        self.assertIsNone(fifth.mode)
        self.assertTrue(fifth.recorded)

        # 8. every turn has a distinct lineage fingerprint
        prints = [
            self.store.turn_continuity(task, n).continuity_fingerprint
            for n in range(1, 6)
        ]
        self.assertEqual(5, len(set(prints)))

        # 9. a retry of turn five cannot re-point it
        self.next_turn(
            task,
            self.criteria_for("src/g.py"),
            {"mode": CONTINUITY_EXTEND, "predecessor_snapshot_id": snapshot_three},
        )
        self.assertEqual(
            CONTINUITY_NOT_DECLARED, self.store.turn_continuity(task, 5).state
        )

        # 10. a refusal does not re-open it either
        self.store.mark_continuity_dispatch_refused(task, 5)
        self.next_turn(
            task,
            self.criteria_for("src/g.py"),
            {"mode": CONTINUITY_EXTEND, "predecessor_snapshot_id": snapshot_three},
        )
        self.assertEqual(
            CONTINUITY_NOT_DECLARED, self.store.turn_continuity(task, 5).state
        )

        # 11. a restart changes nothing
        before = [self.store.turn_continuity(task, n) for n in range(1, 6)]
        self.store.close()
        reopened = TaskStore(self.config)
        self.addCleanup(reopened.close)
        after = [reopened.turn_continuity(task, n) for n in range(1, 6)]
        self.assertEqual(before, after)

        # 12. cross-task lineage is refused
        other, _ = self.service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt="scenario: complete",
            origin="pwa",
            criteria=self.criteria_for("src/z.py"),
        )
        self.store = reopened
        self.close_turn(other.task_id, 1)
        with self.assertRaises(ContinuityInvalid):
            self.next_turn(
                other.task_id,
                self.criteria_for("src/y.py"),
                {
                    "mode": CONTINUITY_EXTEND,
                    "predecessor_snapshot_id": snapshot_one,
                },
            )

        # 13. a historical turn reads legacy_unknown
        with sqlite3.connect(str(self.database)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute(
                "DELETE FROM task_turn_criteria_continuity"
                " WHERE task_id=? AND turn_number=1",
                (other.task_id,),
            )
        self.assertEqual(
            CONTINUITY_LEGACY_UNKNOWN,
            reopened.turn_continuity(other.task_id, 1).state,
        )

        # 14. integrity survives the whole walk
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            self.assertEqual(
                "ok", connection.execute("PRAGMA integrity_check").fetchone()[0]
            )
            self.assertEqual(
                [], connection.execute("PRAGMA foreign_key_check").fetchall()
            )
        finally:
            connection.close()


class TheNegativeSpace(unittest.TestCase):
    """What PR10 must not have built.

    Asserted from the shipped source **semantically** rather than by substring.
    A plain `in` check is not good enough here and the first version of this
    module proved it: `call_method` contains `all_met`, and `committed` contains
    `mitt`. The doctrine PR9 recorded says to inspect meaning, so these walk the
    syntax tree or match on identifier boundaries.
    """

    def python_sources(self):
        for path in (REPO_ROOT / "cofferdam").rglob("*.py"):
            yield path, path.read_text(encoding="utf-8")

    def defined_names(self, text):
        """Every name this module *defines* — functions, classes, assignments."""
        import ast

        names = set()
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover - the tree must parse
            return names
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # String literals matter too: a published key is a literal.
                names.add(node.value)
        return names

    def test_no_module_defines_an_aggregator_version(self):
        """It may be *named* in prose explaining its absence. Never assigned."""
        import re

        for path, text in self.python_sources():
            if path.name == "acceptance.py":
                continue  # M2K PR21; see test_the_acceptance_module_is_the_only_aggregate...
            self.assertEqual(
                [],
                re.findall(r"^\s*AGGREGATOR_VERSION\s*[:=]", text, re.M),
                "%s defines AGGREGATOR_VERSION" % path,
            )
            self.assertNotIn("AGGREGATOR_VERSION", self.defined_names(text))

    def test_no_module_defines_a_task_aggregate(self):
        forbidden = {
            "overall_result",
            "all_met",
            "aggregate",
            "task_aggregate",
            "aggregate_task",
            "Aggregator",
            "compute_aggregate",
            "task_verdict",
            "acceptance_outcome",
        }
        for path, text in self.python_sources():
            if path.name == "acceptance.py":
                continue  # M2K PR21; see test_the_acceptance_module_is_the_only_aggregate...
            defined = self.defined_names(text)
            self.assertEqual(
                set(),
                defined & forbidden,
                "%s defines %s" % (path, defined & forbidden),
            )


    def test_the_acceptance_module_is_the_only_aggregate_and_is_turn_scoped(self):
        """M2K PR21 built an aggregate; this pins how far it is allowed to go.

        The scans above used to ban `AGGREGATOR_VERSION` outright. That claim has
        been overtaken rather than weakened: it now lives in exactly one module,
        and what it defines is a **target-turn** aggregate with no task verdict,
        no check runner and no lifecycle vocabulary.
        """
        import ast as _ast

        definers = set()
        for path in sorted((REPO_ROOT / "cofferdam").rglob("*.py")):
            for node in _ast.walk(_ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, _ast.Assign):
                    for target in node.targets:
                        if isinstance(target, _ast.Name) and target.id == "AGGREGATOR_VERSION":
                            definers.add(path.name)
        self.assertEqual({"acceptance.py"}, definers)

        from cofferdam.workstation.tasks import acceptance

        self.assertEqual(1, acceptance.AGGREGATOR_VERSION)
        for forbidden in ("task_verdict", "task_acceptance", "overall_result",
                          "all_met", "latest_acceptance", "CheckRunner",
                          "run_check", "check_id", "project_acceptance"):
            self.assertFalse(hasattr(acceptance, forbidden))
            self.assertNotIn(forbidden, acceptance.__all__)

    def test_the_continuity_module_defines_no_judgement(self):
        from cofferdam.workstation.tasks import continuity

        defined = self.defined_names(
            Path(continuity.__file__).read_text(encoding="utf-8")
        )
        for forbidden in (
            "aggregate",
            "verdict",
            "overall",
            "score",
            "confidence",
            "risk",
            "passed",
            "failed",
        ):
            self.assertNotIn(forbidden, defined)

    def test_the_continuity_module_cannot_execute_anything(self):
        from cofferdam.workstation.tasks import continuity

        text = Path(continuity.__file__).read_text(encoding="utf-8")
        for forbidden in ("subprocess", "check_id", "Popen", "os.system", "shell="):
            self.assertNotIn(forbidden, text)

    def test_no_new_criterion_kind_was_added(self):
        from cofferdam.workstation.tasks.criteria import CRITERION_KINDS

        self.assertEqual(("evidence", "manual"), tuple(CRITERION_KINDS))

    def test_the_evaluator_result_vocabulary_is_unchanged(self):
        from cofferdam.workstation.tasks.evaluation import (
            RESULT_MET,
            RESULT_NOT_MET,
            RESULT_UNVERIFIED,
        )

        self.assertEqual(
            ("met", "not_met", "unverified"),
            (RESULT_MET, RESULT_NOT_MET, RESULT_UNVERIFIED),
        )

    def test_the_workstation_service_gained_no_continuity_surface(self):
        """PR10 is persistence. No route, no read surface, no request field.

        Matched on the criteria-continuity vocabulary rather than on the word
        `continuity`, which the M2J Working Context has legitimately used since
        long before this PR for its own bounded continuity fields.
        """
        service = (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "turn_continuity",
            "criteria_continuity",
            "supersession",
            "supersedes",
            "predecessor_snapshot",
            "/continuity",
        ):
            self.assertNotIn(forbidden, service, forbidden)

    def test_the_bridge_gained_no_continuity_surface(self):
        bridge = (
            REPO_ROOT / "cofferdam" / "actions_bridge" / "service.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "turn_continuity",
            "criteria_continuity",
            "supersession",
            "supersedes",
            "predecessor_snapshot",
        ):
            self.assertNotIn(forbidden, bridge, forbidden)

    def test_the_pwa_gained_no_continuity_control(self):
        """No panel, no editor, no declaration control.

        `superseded` already appears in a clarification message that predates
        this PR, so the check names the continuity vocabulary rather than an
        English participle.
        """
        tasks_js = (REPO_ROOT / "web" / "tasks.js").read_text(encoding="utf-8")
        # M2K PR22 gave the panel a read-only acceptance section, whose human
        # phrasings legitimately name the `continuity_*` reason codes PR20 fought
        # to preserve. So this asserts what it always meant — no declaration
        # **control** — rather than banning the vocabulary.
        for forbidden in (
            'method: "POST"', 'method: "PUT"', 'method: "PATCH"',
            'method: "DELETE"', "/continuity", "supersedes",
            "predecessor_snapshot", "criterion_ordinal",
        ):
            self.assertNotIn(forbidden, tasks_js, forbidden)

    def test_the_assessment_response_is_unchanged(self):
        from cofferdam.workstation.tasks import assessment

        text = Path(assessment.__file__).read_text(encoding="utf-8")
        for forbidden in ("continuity", "supersession", "predecessor_snapshot"):
            self.assertNotIn(forbidden, text.lower(), forbidden)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
