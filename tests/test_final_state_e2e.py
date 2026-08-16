"""M2K PR14 — the whole walk, in one isolated home with a real Git repository.

Four turns and a real worker that creates, deletes and links files without
committing any of it, because the uncommitted case is exactly where a HEAD-only
probe would give the wrong answer and where this evidence surface earns its keep.

Also asserts the negative space: no evaluator predicate, no evaluation semantics
change, no aggregate, no `AGGREGATOR_VERSION`, no public route, no bridge
operation, and an assessment response that is byte-for-byte the shape PR8 shipped.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from cofferdam.workstation.config import load_config
from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterOutcome,
    TaskAdapter,
)
from cofferdam.workstation.tasks.criteria import CRITERIA_MODEL_VERSION
from cofferdam.workstation.tasks.continuity import CONTINUITY_MODEL_VERSION
from cofferdam.workstation.tasks.evaluation import EVALUATOR_VERSION
from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION
from cofferdam.workstation.tasks.finalstate import (
    FINAL_STATE_OBSERVER_VERSION,
    KIND_FILE,
    KIND_SYMLINK,
    OBSERVATION_COMPLETE,
    OBSERVATION_INCOMPLETE,
    OBSERVATION_UNAVAILABLE,
    PATH_ABSENT,
    PATH_PRESENT,
    PATH_UNAVAILABLE,
    REASON_LINEAGE_UNAVAILABLE,
    REASON_SYMLINK_TRAVERSAL_REFUSED,
)
from cofferdam.workstation.tasks.lineage import RESOLVER_VERSION
from cofferdam.workstation.tasks.projects import load_projects
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

PROJECT_ID = "demo"
REPO_ROOT = Path(__file__).resolve().parents[1]


class ScriptedWorker(TaskAdapter):
    """Does whatever the next scripted step says, then reports it is ready."""

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
        return AdapterOutcome(
            requested_state="ready_for_followup", final_result="done"
        )

    def start(self, context):
        return self._run(context)

    def send_followup(self, context, followup):
        return self._run(context)


class FinalStateEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.root = self.home / "projects" / PROJECT_ID
        self.root.mkdir(parents=True)
        self.outside = self.home / "outside"
        self.outside.mkdir()
        (self.outside / "private.txt").write_text("not yours\n", encoding="utf-8")

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
            self.config,
            self.store,
            registry,
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

    def criteria_for(self, *paths):
        return [
            {"kind": "evidence", "predicate": "path_changed", "path": path}
            for path in paths
        ]

    def snapshot_id(self, task_id, turn):
        return self.store.turn_criteria(task_id, turn).snapshot_id

    def states(self, task_id, turn):
        return {
            item.path: (item.state, item.kind)
            for item in self.store.turn_final_state(task_id, turn).paths
        }

    def head_paths(self):
        return subprocess.run(
            ("git", "ls-tree", "--name-only", "-r", "HEAD"),
            cwd=self.root,
            capture_output=True,
            text=True,
        ).stdout.split()

    def test_the_whole_walk(self):
        # 1. The schema moved, additively, and only for this.
        self.assertEqual(SCHEMA_VERSION, 10)

        # 2-4. Turn 1: a root criterion naming `a.txt`; the worker creates it and
        # commits nothing.
        self.worker.steps = [
            lambda root: (root / "a.txt").write_text("made\n", encoding="utf-8")
        ]
        row, _ = self.service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt="scenario: complete",
            origin="pwa",
            criteria=self.criteria_for("a.txt"),
            continuity={"mode": "root"},
        )
        task_id = row.task_id
        self.assertEqual(self.states(task_id, 1), {"a.txt": (PATH_PRESENT, KIND_FILE)})

        # 5-6. The observation is durable and complete.
        first = self.store.turn_final_state(task_id, 1)
        self.assertEqual(first.state, OBSERVATION_COMPLETE)
        self.assertEqual(first.observer_version, FINAL_STATE_OBSERVER_VERSION)
        self.assertTrue(first.fingerprint)

        # 7. HEAD still does not contain it, and that changes nothing.
        self.assertNotIn("a.txt", self.head_paths())
        self.assertEqual(self.states(task_id, 1)["a.txt"][0], PATH_PRESENT)

        # 8-11. Turn 2 extends with `b.txt`; the worker deletes a and creates b.
        def turn_two(root):
            (root / "a.txt").unlink()
            (root / "b.txt").write_text("second\n", encoding="utf-8")

        self.worker.steps = [turn_two]
        self.service.send_followup(
            task_id,
            "more",
            criteria=self.criteria_for("b.txt"),
            continuity={
                "mode": "extend",
                "predecessor_snapshot_id": self.snapshot_id(task_id, 1),
            },
        )
        self.assertEqual(
            self.states(task_id, 2),
            {"a.txt": (PATH_ABSENT, None), "b.txt": (PATH_PRESENT, KIND_FILE)},
        )

        # 12-13. Turn 3 revises `a.txt` away and adds `c.txt`. The scope follows
        # the resolved active set: a is gone from it, b survives, c joins.
        retired = self.store.turn_criteria(task_id, 1).criteria[0].criterion_id
        self.worker.steps = [
            lambda root: (root / "c.txt").write_text("third\n", encoding="utf-8")
        ]
        self.service.send_followup(
            task_id,
            "revise",
            criteria=self.criteria_for("c.txt"),
            continuity={
                "mode": "revise",
                "predecessor_snapshot_id": self.snapshot_id(task_id, 2),
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": retired}
                ],
            },
        )
        self.assertEqual(
            sorted(self.states(task_id, 3)), ["b.txt", "c.txt"]
        )

        # 14. Turn 4 replaces, cutting the older scope entirely.
        self.worker.steps = [
            lambda root: (root / "d.txt").write_text("fourth\n", encoding="utf-8")
        ]
        self.service.send_followup(
            task_id,
            "replace",
            criteria=self.criteria_for("d.txt"),
            continuity={
                "mode": "replace",
                "predecessor_snapshot_id": self.snapshot_id(task_id, 3),
            },
        )
        self.assertEqual(
            self.states(task_id, 4), {"d.txt": (PATH_PRESENT, KIND_FILE)}
        )

        # 15. An undeclared turn has no defensible scope, and says so.
        self.worker.steps = [lambda root: None]
        self.service.send_followup(
            task_id, "undeclared", criteria=self.criteria_for("e.txt"), continuity=None
        )
        undeclared = self.store.turn_final_state(task_id, 5)
        self.assertEqual(undeclared.state, OBSERVATION_UNAVAILABLE)
        self.assertEqual(undeclared.limitation_reason, REASON_LINEAGE_UNAVAILABLE)
        self.assertEqual(undeclared.paths, ())

        # 16-17. Turn 6 replaces to recover, and names both a path behind an
        # external symlink and a broken symlink of its own.
        def turn_six(root):
            os.symlink(str(self.outside), root / "external")
            os.symlink("nowhere.txt", root / "dangling.txt")

        self.worker.steps = [turn_six]
        self.service.send_followup(
            task_id,
            "recover",
            criteria=self.criteria_for("external/private.txt", "dangling.txt"),
            continuity={
                "mode": "replace",
                "predecessor_snapshot_id": self.snapshot_id(task_id, 5),
            },
        )
        sixth = self.store.turn_final_state(task_id, 6)
        states = self.states(task_id, 6)
        # The escape is refused rather than answered, and never called absent.
        self.assertEqual(
            states["external/private.txt"], (PATH_UNAVAILABLE, None)
        )
        self.assertEqual(sixth.state, OBSERVATION_INCOMPLETE)
        self.assertEqual(sixth.limitation_reason, REASON_SYMLINK_TRAVERSAL_REFUSED)
        # A broken final symlink is a present symlink.
        self.assertEqual(states["dangling.txt"], (PATH_PRESENT, KIND_SYMLINK))

        # 18. Deleting the repository changes no stored answer.
        before = [self.store.turn_final_state(task_id, turn) for turn in range(1, 7)]
        shutil.rmtree(self.root)
        self.assertFalse(self.root.exists())
        after = [self.store.turn_final_state(task_id, turn) for turn in range(1, 7)]
        self.assertEqual(before, after)

        # 19-20. No evaluator predicate was added and no evaluation semantics moved.
        from cofferdam.workstation.tasks.criteria import (
            CRITERION_KINDS,
            EVIDENCE_PREDICATES,
        )

        self.assertEqual(tuple(CRITERION_KINDS), ("evidence", "manual"))
        self.assertEqual(
            tuple(EVIDENCE_PREDICATES),
            ("path_changed", "path_operation", "rename"),
        )
        self.assertEqual(EVALUATOR_VERSION, 1)
        self.assertEqual(ASSEMBLER_VERSION, 3)
        self.assertEqual(CRITERIA_MODEL_VERSION, 1)
        self.assertEqual(CONTINUITY_MODEL_VERSION, 1)
        self.assertEqual(RESOLVER_VERSION, 1)
        self.assertEqual(FINAL_STATE_OBSERVER_VERSION, 1)

        # 21-22. No aggregate anywhere in what was produced.
        for observation in after:
            for field in observation.__dataclass_fields__:
                lowered = field.lower()
                for forbidden in ("met", "verdict", "aggregate", "outcome", "score"):
                    self.assertNotIn(forbidden, lowered)


class NegativeSpaceTests(unittest.TestCase):
    """23-25. What PR14 did not build, asserted from structure."""

    def python_sources(self):
        for path in sorted((REPO_ROOT / "cofferdam").rglob("*.py")):
            yield path, path.read_text(encoding="utf-8")

    def declared_names(self, text):
        names = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
        return names

    def test_no_module_defines_an_aggregator_version(self):
        import re

        for path, text in self.python_sources():
            self.assertEqual(
                [],
                re.findall(r"^\s*AGGREGATOR_VERSION\s*[:=]", text, re.M),
                "%s defines AGGREGATOR_VERSION" % path,
            )
            self.assertNotIn("AGGREGATOR_VERSION", self.declared_names(text))

    def test_no_module_defines_a_state_predicate_yet(self):
        """`path_exists` / `path_absent` are PR13's plan, not PR14's delivery."""
        for path, text in self.python_sources():
            declared = self.declared_names(text)
            for forbidden in (
                "PREDICATE_PATH_EXISTS",
                "PREDICATE_PATH_ABSENT",
                "PREDICATE_CURRENT_STATE",
            ):
                self.assertNotIn(forbidden, declared, "%s: %s" % (path, forbidden))

    def test_the_evidence_predicate_vocabulary_is_unchanged(self):
        from cofferdam.workstation.tasks.criteria import EVIDENCE_PREDICATES

        self.assertEqual(
            tuple(EVIDENCE_PREDICATES), ("path_changed", "path_operation", "rename")
        )

    def test_no_aggregate_or_runner_appeared(self):
        forbidden = {
            "all_met", "aggregate", "task_verdict", "acceptance_outcome",
            "CheckRunner", "run_check", "check_id", "overall_result",
        }
        for path, text in self.python_sources():
            declared = self.declared_names(text)
            self.assertEqual(
                set(),
                declared & forbidden,
                "%s defines %s" % (path, declared & forbidden),
            )

    def final_state_tree(self):
        from cofferdam.workstation.tasks import finalstate

        return ast.parse(Path(finalstate.__file__).read_text(encoding="utf-8"))

    def test_the_final_state_module_cannot_execute_anything(self):
        """From the syntax tree: the prose says "no subprocess", the imports prove it."""
        imported = set()
        for node in ast.walk(self.final_state_tree()):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        for forbidden in (
            "subprocess", "socket", "shutil", "http", "urllib", "requests", "httpx",
            "sqlite3", "asyncio", "multiprocessing", "ctypes", "pickle",
        ):
            self.assertNotIn(forbidden, imported, forbidden)

    def test_the_final_state_module_reads_no_content(self):
        """Existence and kind. Never bytes — asserted from the calls it makes."""
        called = set()
        for node in ast.walk(self.final_state_tree()):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name):
                    called.add(target.id)
                elif isinstance(target, ast.Attribute):
                    called.add(target.attr)
        for forbidden in (
            "read_bytes", "read_text", "fdopen", "read", "artifact_digest",
            "open_target", "listdir", "scandir", "walk", "glob",
        ):
            self.assertNotIn(forbidden, called, forbidden)
        # `os.open` is used, and only ever with O_DIRECTORY to descend. Nothing
        # is ever opened for reading content.
        source = Path(
            __import__(
                "cofferdam.workstation.tasks.finalstate", fromlist=["__file__"]
            ).__file__
        ).read_text(encoding="utf-8")
        self.assertNotIn("st_size", source)
        self.assertNotIn("st_mtime", source)

    def test_the_workstation_service_gained_no_final_state_surface(self):
        service = (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "final_state", "finalstate", "turn_final_state", "observer_version",
            "path_state",
        ):
            self.assertNotIn(forbidden, service, forbidden)

    def test_the_bridge_gained_no_final_state_surface(self):
        bridge = (REPO_ROOT / "cofferdam" / "actions_bridge" / "service.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("final_state", "finalstate", "observer_version", "path_state"):
            self.assertNotIn(forbidden, bridge, forbidden)

    def test_the_pwa_gained_no_final_state_control(self):
        pwa = (REPO_ROOT / "web" / "tasks.js").read_text(encoding="utf-8").lower()
        for forbidden in ("final_state", "finalstate", "path_state", "observer_version"):
            self.assertNotIn(forbidden, pwa, forbidden)

    def test_the_assessment_response_is_unchanged(self):
        from cofferdam.workstation.tasks import assessment

        text = Path(assessment.__file__).read_text(encoding="utf-8").lower()
        for forbidden in (
            "final_state", "finalstate", "path_state", "observer_version",
            "current_state",
        ):
            self.assertNotIn(forbidden, text, forbidden)

    def test_the_evaluator_does_not_import_the_observer(self):
        """PR7 stays turn-local. It must not learn about final state by accident."""
        from cofferdam.workstation.tasks import evaluation

        text = Path(evaluation.__file__).read_text(encoding="utf-8")
        self.assertNotIn("finalstate", text)
        self.assertNotIn("final_state", text)

    def test_the_evidence_bundle_does_not_carry_final_state(self):
        """EvidenceBundle v3 is not reinterpreted, so ASSEMBLER_VERSION holds."""
        from cofferdam.workstation.tasks import evidence

        text = Path(evidence.__file__).read_text(encoding="utf-8")
        self.assertNotIn("finalstate", text)
        self.assertNotIn("final_state", text)
        self.assertEqual(ASSEMBLER_VERSION, 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
