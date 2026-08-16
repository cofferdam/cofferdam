"""M2K PR11 — the whole walk, in one isolated home.

Four turns declaring four different relationships, the recovery case, the two
supersession shapes that matter, and every refusal. What this module exists to
demonstrate is that *which criteria are currently required* is now answerable
from frozen rows alone — derived, deterministic, replayable, and honestly
unavailable where the declarations do not determine it.

Also asserts the negative space explicitly: no schema change, no persisted
result, no aggregate, no `AGGREGATOR_VERSION`, no check runner, no command
execution, and no API, assessment, bridge or PWA widening.

PR11 discovered that PR10's write validation required a supersession's old-side
criterion to be stored in the **declared predecessor's own snapshot**, which
refused a legitimate revision of an inherited requirement. **M2K PR12 corrected
that** — see :class:`InheritedSupersessionTests` — so the old side is now checked
against the predecessor's *resolved active set*. The walk below keeps the shape it
was originally written with, which remains valid either way.
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
from cofferdam.workstation.tasks.continuity import (
    CONTINUITY_MODEL_VERSION,
    validate_declaration,
)
from cofferdam.workstation.tasks.criteria import (
    CRITERIA_MODEL_VERSION,
    validate_criteria,
)
from cofferdam.workstation.tasks.evaluation import EVALUATOR_VERSION
from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION
from cofferdam.workstation.tasks.identity import new_task_id
from cofferdam.workstation.tasks.lineage import (
    REASON_LEGACY_UNKNOWN,
    REASON_NOT_DECLARED,
    REASON_SUPERSESSION_TARGET_NOT_ACTIVE,
    RESOLUTION_RESOLVED,
    RESOLVER_VERSION,
    resolve,
)
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

PROJECT_ID = "demo"
REPO_ROOT = Path(__file__).resolve().parents[1]


class LineageEndToEnd(unittest.TestCase):
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
        self.task_id = self.make_task()

    # -- helpers ------------------------------------------------------------

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

    def make_task(self, task_id=None):
        task_id = task_id or new_task_id()
        self.store.storage_health()
        with self.sql() as connection:
            connection.execute(
                "INSERT INTO tasks (task_id, correlation_id, origin, adapter_id,"
                " project_id, state, created_at, updated_at, title, prompt)"
                " VALUES (?,'cor','pwa','validation',?,'running','x','x','t','p')",
                (task_id, PROJECT_ID),
            )
        return task_id

    def criteria_for(self, *labels):
        return [
            {"kind": "evidence", "predicate": "path_changed", "path": "%s.py" % label}
            for label in labels
        ]

    def turn(self, labels, declaration):
        self.store.reserve_turn_criteria(
            self.task_id, validate_criteria(self.criteria_for(*labels)), recorded_at="x"
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
        return number

    def snap(self, turn):
        return self.store.turn_criteria(self.task_id, turn).snapshot_id

    def crit(self, turn):
        return [
            item.criterion_id
            for item in self.store.turn_criteria(self.task_id, turn).criteria
        ]

    def active(self, turn):
        result = self.service.resolve_active_criteria(self.task_id, turn)
        self.assertTrue(result.resolved, getattr(result, "reason", None))
        return [entry.criterion.path for entry in result.active]

    def digest(self):
        return hashlib.sha256(self.database.read_bytes()).hexdigest()

    # -- the walk -----------------------------------------------------------

    def test_the_whole_walk(self):
        # 1. The schema does not move. This PR is pure read logic, so this floors
        # at the version it was written against rather than pinning the current
        # one — a later bump belongs to the PR that makes it.
        self.assertGreaterEqual(SCHEMA_VERSION, 9)

        # 2-3. Turn 1: root with A and B.
        self.turn(["a", "b"], {"mode": "root"})
        self.assertEqual(self.active(1), ["a.py", "b.py"])

        # 4-5. Turn 2: extend with C. The predecessor's set remains.
        self.turn(["c"], {"mode": "extend", "predecessor_snapshot_id": self.snap(1)})
        self.assertEqual(self.active(2), ["a.py", "b.py", "c.py"])

        # 6-7. Turn 3: revise, retiring C — a criterion of the declared
        # predecessor's own snapshot. Still valid after PR12, which widened the
        # rule rather than moving it.
        self.turn(
            ["d"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(2),
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": self.crit(2)[0]}
                ],
            },
        )
        self.assertEqual(self.active(3), ["a.py", "b.py", "d.py"])

        # 8-9. Turn 4: replace. The cut point — everything before it is gone,
        # and no predecessor active set was needed to say so.
        self.turn(["e"], {"mode": "replace", "predecessor_snapshot_id": self.snap(3)})
        self.assertEqual(self.active(4), ["e.py"])
        replaced = self.service.resolve_active_criteria(self.task_id, 4)
        self.assertEqual([step.turn_number for step in replaced.lineage], [4])

        # 10. An undeclared turn is unavailable, and the replace after it is not.
        self.turn(["f"], None)
        undeclared = self.service.resolve_active_criteria(self.task_id, 5)
        self.assertFalse(undeclared.resolved)
        self.assertEqual(undeclared.reason, REASON_NOT_DECLARED)
        self.turn(["g"], {"mode": "replace", "predecessor_snapshot_id": self.snap(5)})
        self.assertEqual(self.active(6), ["g.py"])

        # 11. Split: one requirement becomes two.
        self.turn(
            ["h", "i"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(6),
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": self.crit(6)[0]},
                    {"criterion_ordinal": 2, "predecessor_criterion_id": self.crit(6)[0]},
                ],
            },
        )
        self.assertEqual(self.active(7), ["h.py", "i.py"])

        # 12. Merge: two requirements become one.
        self.turn(
            ["j"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(7),
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": self.crit(7)[0]},
                    {"criterion_ordinal": 1, "predecessor_criterion_id": self.crit(7)[1]},
                ],
            },
        )
        self.assertEqual(self.active(8), ["j.py"])

        # 16. An empty replace resolves to a known empty set, which is not success.
        self.turn([], {"mode": "replace", "predecessor_snapshot_id": self.snap(8)})
        empty = self.service.resolve_active_criteria(self.task_id, 9)
        self.assertTrue(empty.resolved)
        self.assertEqual(empty.state, RESOLUTION_RESOLVED)
        self.assertEqual(empty.active_count, 0)
        for forbidden in ("met", "all_met", "passed", "outcome", "verdict"):
            self.assertFalse(hasattr(empty, forbidden))

        # 17. Deterministic across repeats and across a reopened database.
        before = self.service.resolve_active_criteria(self.task_id, 8).fingerprint
        self.assertEqual(
            before, self.service.resolve_active_criteria(self.task_id, 8).fingerprint
        )
        self.store.close()
        reopened = TaskStore(self.config)
        self.addCleanup(reopened.close)
        self.assertEqual(
            before, resolve(reopened.lineage_inputs(self.task_id, 8)).fingerprint
        )

        # 18. The repository is not an input. Delete it; nothing moves.
        shutil.rmtree(self.root)
        self.assertFalse(self.root.exists())
        self.assertEqual(
            before, resolve(reopened.lineage_inputs(self.task_id, 8)).fingerprint
        )
        self.assertEqual(
            ["j.py"],
            [
                entry.criterion.path
                for entry in resolve(reopened.lineage_inputs(self.task_id, 8)).active
            ],
        )

    # 13. A stale supersession target, over real rows.
    def test_a_stale_supersession_target_is_unavailable(self):
        self.turn(["a", "keep"], {"mode": "root"})
        self.turn(
            ["b"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(1),
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": self.crit(1)[0]}
                ],
            },
        )
        self.turn(
            ["c"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(2),
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": self.crit(2)[0]}
                ],
            },
        )
        self.assertEqual(self.active(3), ["keep.py", "c.py"])
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_criterion_supersessions"
                " SET predecessor_criterion_id = ?"
                " WHERE task_id = ? AND turn_number = 3",
                (self.crit(1)[0], self.task_id),
            )
        result = self.service.resolve_active_criteria(self.task_id, 3)
        self.assertFalse(result.resolved)
        self.assertEqual(result.reason, REASON_SUPERSESSION_TARGET_NOT_ACTIVE)

    # 15. A historical turn that predates continuity persistence.
    def test_a_legacy_turn_is_unavailable(self):
        self.store.reserve_turn_criteria(
            self.task_id, validate_criteria(self.criteria_for("a")), recorded_at="x"
        )
        # No continuity row at all — exactly what every turn on the live host
        # looks like, and what no backfill may ever change.
        with self.sql() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO task_turns (task_id, turn_number, provider,"
                " source, started_at, completed_at, outcome)"
                " VALUES (?,1,'validation','pwa','x','y','completed')",
                (self.task_id,),
            )
        result = self.service.resolve_active_criteria(self.task_id, 1)
        self.assertFalse(result.resolved)
        self.assertEqual(result.reason, REASON_LEGACY_UNKNOWN)

        # And a later explicit replace recovers, without ever guessing what the
        # legacy turn required.
        self.turn(["z"], {"mode": "replace", "predecessor_snapshot_id": self.snap(1)})
        recovered = self.service.resolve_active_criteria(self.task_id, 2)
        self.assertTrue(recovered.resolved)
        self.assertEqual([entry.criterion.path for entry in recovered.active], ["z.py"])
        self.assertEqual([step.turn_number for step in recovered.lineage], [2])

    # 19-20. Nothing is written, ever.
    def test_resolution_writes_nothing_and_moves_no_version(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend", "predecessor_snapshot_id": self.snap(1)})
        self.store.close()
        before = self.digest()
        store = TaskStore(self.config)
        service = TaskService(
            self.config,
            store,
            self.service.adapters,
            projects=self.service.projects,
        )
        for turn in (1, 2, 7):
            service.resolve_active_criteria(self.task_id, turn)
        store.close()
        self.assertEqual(self.digest(), before)

        with self.sql() as connection:
            self.assertEqual(
                int(
                    connection.execute(
                        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                    ).fetchone()[0]
                ),
                SCHEMA_VERSION,
            )

    def test_no_resolved_active_set_is_persisted_anywhere(self):
        """Derived on read. There is no table it could be written into."""
        self.turn(["a"], {"mode": "root"})
        self.service.resolve_active_criteria(self.task_id, 1)
        with self.sql() as connection:
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            ]
        for name in tables:
            for forbidden in ("active", "lineage", "resolv"):
                self.assertNotIn(forbidden, name.lower())
        schema = (REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "store.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("CREATE TABLE IF NOT EXISTS task_turn_active", schema)
        self.assertNotIn("resolved_fingerprint", schema)

    def test_the_versions_around_it_did_not_move(self):
        self.assertEqual(RESOLVER_VERSION, 1)
        # PR11/PR12 add no schema of their own; a later bump belongs to the PR
        # that makes it, so this floors rather than pins.
        self.assertGreaterEqual(SCHEMA_VERSION, 9)
        self.assertEqual(CRITERIA_MODEL_VERSION, 1)
        self.assertEqual(CONTINUITY_MODEL_VERSION, 1)
        self.assertEqual(ASSEMBLER_VERSION, 3)
        self.assertEqual(EVALUATOR_VERSION, 1)


class InheritedSupersessionTests(LineageEndToEnd):
    """M2K PR12 — a revise may retire whatever its predecessor actually stands on."""

    def test_a_revise_may_retire_a_criterion_it_inherited(self):
        """The case PR10 refused and PR11 recorded as a limitation."""
        self.turn(["a", "b"], {"mode": "root"})
        self.turn(["c"], {"mode": "extend", "predecessor_snapshot_id": self.snap(1)})
        self.turn(
            ["d"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(2),
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": self.crit(1)[1]}
                ],
            },
        )
        self.assertEqual(self.active(3), ["a.py", "c.py", "d.py"])
        result = self.service.resolve_active_criteria(self.task_id, 3)
        # Nothing was cut to make it work: all three turns are still consumed.
        self.assertEqual([step.turn_number for step in result.lineage], [1, 2, 3])

    def test_declaring_the_earlier_snapshot_still_works_and_still_cuts(self):
        """The old workaround remains legal, and still drops turn 2's criteria."""
        self.turn(["a", "b"], {"mode": "root"})
        self.turn(["c"], {"mode": "extend", "predecessor_snapshot_id": self.snap(1)})
        self.turn(
            ["d"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(1),
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": self.crit(1)[1]}
                ],
            },
        )
        self.assertEqual(self.active(3), ["a.py", "d.py"])
        result = self.service.resolve_active_criteria(self.task_id, 3)
        self.assertEqual([step.turn_number for step in result.lineage], [1, 3])


class NegativeSpaceTests(unittest.TestCase):
    """21-25. What PR11 did not build, asserted from structure rather than prose."""

    def python_sources(self):
        for path in sorted((REPO_ROOT / "cofferdam").rglob("*.py")):
            yield path, path.read_text(encoding="utf-8")

    def defined_names(self, text):
        names = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value)
        return names

    def test_no_module_defines_an_aggregator_version(self):
        import re

        for path, text in self.python_sources():
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
            "active_set_outcome",
        }
        for path, text in self.python_sources():
            defined = self.defined_names(text)
            self.assertEqual(
                set(), defined & forbidden, "%s defines %s" % (path, defined & forbidden)
            )

    def test_the_lineage_module_defines_no_judgement(self):
        from cofferdam.workstation.tasks import lineage

        defined = self.defined_names(
            Path(lineage.__file__).read_text(encoding="utf-8")
        )
        for forbidden in (
            "aggregate", "verdict", "overall", "score", "confidence", "risk",
            "passed", "failed", "all_met",
        ):
            self.assertNotIn(forbidden, defined)

    def test_the_lineage_module_cannot_execute_anything(self):
        from cofferdam.workstation.tasks import lineage

        text = Path(lineage.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "subprocess", "check_id", "Popen", "os.system", "shell=", "argv",
        ):
            self.assertNotIn(forbidden, text)

    def test_no_named_check_runner_exists(self):
        """Definitions only — `check_id` is *described* in PR6 prose as absent."""
        for path, text in self.python_sources():
            declared = set()
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    declared.add(node.name)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            declared.add(target.id)
                elif isinstance(node, ast.arg):
                    declared.add(node.arg)
            for forbidden in ("run_named_check", "CheckRunner", "run_check", "check_id"):
                self.assertNotIn(forbidden, declared, "%s defines %s" % (path, forbidden))

    def test_no_new_criterion_kind_was_added(self):
        from cofferdam.workstation.tasks.criteria import CRITERION_KINDS

        self.assertEqual(("evidence", "manual"), tuple(CRITERION_KINDS))

    def test_the_evaluator_vocabulary_is_unchanged(self):
        from cofferdam.workstation.tasks.evaluation import (
            RESULT_MET,
            RESULT_NOT_MET,
            RESULT_UNVERIFIED,
        )

        self.assertEqual(
            ("met", "not_met", "unverified"),
            (RESULT_MET, RESULT_NOT_MET, RESULT_UNVERIFIED),
        )

    def test_the_workstation_service_gained_no_lineage_surface(self):
        service = (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "resolve_active_criteria", "active_criteria", "lineage",
            "ResolvedActiveCriteria", "resolver_version", "/lineage",
        ):
            self.assertNotIn(forbidden, service, forbidden)

    def test_the_bridge_gained_no_lineage_surface(self):
        bridge = (REPO_ROOT / "cofferdam" / "actions_bridge" / "service.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "resolve_active_criteria", "active_criteria", "lineage", "resolver_version",
        ):
            self.assertNotIn(forbidden, bridge, forbidden)

    def test_the_pwa_gained_no_lineage_control(self):
        tasks_js = (REPO_ROOT / "web" / "tasks.js").read_text(encoding="utf-8")
        for forbidden in (
            "lineage", "active_criteria", "resolver_version", "supersession",
        ):
            self.assertNotIn(forbidden, tasks_js.lower(), forbidden)

    def test_the_assessment_response_is_unchanged(self):
        from cofferdam.workstation.tasks import assessment

        text = Path(assessment.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "lineage", "active_criteria", "resolver_version", "continuity",
            "resolved_fingerprint",
        ):
            self.assertNotIn(forbidden, text.lower(), forbidden)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
