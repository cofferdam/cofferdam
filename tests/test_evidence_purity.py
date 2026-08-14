"""M2K PR2 — proof that assembling evidence touches nothing.

The claim being defended: a bundle describes what was **recorded**, not what the
repository looks like now. That claim only holds if assembly cannot reach the
world. If it could run ``git status``, then a bundle read a year later would
describe a repository somebody has since edited, and the historical record would
quietly become a live one — the specific failure that makes evidence worthless.

Two layers, because either alone is weak. The static layer reads the module's
own source and its import graph: a module that cannot import ``subprocess``
cannot shell out, whatever its functions do. The runtime layer poisons the
dangerous callables and assembles a real bundle, which catches an indirect route
the import scan would miss.
"""

from __future__ import annotations

import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks import evidence as evidence_module

MODULE_PATH = Path(evidence_module.__file__)

#: Anything that could reach the world. `hashlib` is fine — it is arithmetic.
FORBIDDEN_IMPORTS = frozenset(
    {
        "subprocess",
        "socket",
        "shutil",
        "http",
        "urllib",
        "requests",
        "httpx",
        "asyncio",
        "sqlite3",
        "os",
        "time",
        "datetime",
        "tempfile",
        "glob",
        "pathlib",
        "multiprocessing",
        "threading",
        "ssl",
        "ftplib",
        "smtplib",
        "webbrowser",
        "ctypes",
        "pickle",
        "random",
        "secrets",
    }
)

#: Names that would mean a call reached out, even without a module import.
FORBIDDEN_CALLS = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "input",
        "system",
        "popen",
        "run",
        "spawn",
        "connect",
        "urlopen",
        "now",
        "today",
        "time",
        "monotonic",
    }
)


def _tree() -> ast.Module:
    return ast.parse(MODULE_PATH.read_text(encoding="utf-8"))


class StaticPurityTests(unittest.TestCase):
    def test_the_assembler_imports_nothing_that_reaches_the_world(self):
        found = set()
        for node in ast.walk(_tree()):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
        offenders = found & FORBIDDEN_IMPORTS
        self.assertEqual(offenders, set(), "forbidden imports: %s" % sorted(offenders))

    def test_the_assembler_calls_nothing_that_reaches_the_world(self):
        offenders = set()
        for node in ast.walk(_tree()):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else target.attr if isinstance(target, ast.Attribute) else None
            )
            if name in FORBIDDEN_CALLS:
                offenders.add(name)
        self.assertEqual(offenders, set(), "forbidden calls: %s" % sorted(offenders))

    def test_the_module_holds_no_command_to_run(self):
        """Prose about Git is fine; an argv is not.

        Asked of the **string constants in the code**, via the AST, rather than
        of the file's text — the module's docstrings discuss ``git status`` at
        length by necessity, and a text search would either fail on those or be
        tuned until it stopped meaning anything. The one string literal that
        legitimately contains ``git`` is the eligibility comparison, which is a
        value being *matched*, never a value being *executed*.
        """
        tree = _tree()
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                text = ast.get_docstring(node, clean=False)
                if text is not None:
                    docstrings.add(text)

        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value not in docstrings
        ]
        #: The only literals in this module that name a Git operation. Both are
        #: values the eligibility test *compares against*, never values anything
        #: runs — and pinning them here means adding a third one is a deliberate
        #: edit to this list rather than a quiet widening.
        allowed_git_literals = {"git status", "rev-parse HEAD"}
        self.assertTrue(
            allowed_git_literals <= set(literals),
            "the eligibility comparisons are missing",
        )
        for value in literals:
            if value in allowed_git_literals:
                continue
            if value.isidentifier():
                # A bare Python identifier — an `__all__` entry, a field name.
                # It cannot be a command line, and `is_git_head_observation` is
                # a function this module deliberately exports.
                continue
            lowered = value.lower()
            self.assertNotIn("--", value, value)
            self.assertNotIn("git", lowered, value)
            self.assertNotIn("/bin/", lowered, value)
            self.assertNotIn("sh -c", lowered, value)

        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("shell=True", "argv", "check_output", "Popen"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_no_provider_or_model_import(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("anthropic", "openai", "llama", "planner", "model_client"):
            self.assertNotIn(forbidden, source.lower(), forbidden)

    def test_assemble_takes_every_input_as_an_argument(self):
        """Purity you can check by reading the signature."""
        import inspect

        signature = inspect.signature(evidence_module.assemble)
        self.assertEqual(
            sorted(signature.parameters),
            ["bound", "claims", "events", "ingestion_rows", "task_id", "turn"],
        )
        for parameter in signature.parameters.values():
            self.assertEqual(parameter.kind, parameter.KEYWORD_ONLY)


class RuntimePurityTests(unittest.TestCase):
    """Poison the dangerous callables, then assemble a real bundle."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr2-purity-")
        self.addCleanup(self._temp.cleanup)
        self.home = Path(self._temp.name)
        self.root = self.home / "project"
        self.root.mkdir()
        self.database = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.store = self._open()
        self.addCleanup(self._close)
        self.task_id = self._build()

    def _open(self):
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.tasks.store import TaskStore

        config = load_config(self.home)
        config.ensure_dirs()
        return TaskStore(config)

    def _close(self):
        try:
            self.store.close()
        except Exception:
            pass

    def _build(self) -> str:
        from cofferdam.workstation.tasks.claims import CLAIM_MODIFIED, ClaimSubmission

        from tests.test_evidence_bundle import path_observation

        row, _ = self.store.create_task(
            origin="pwa",
            adapter_id="validation",
            project_id="synth",
            prompt="p",
            title="t",
        )
        for state in ("queued", "starting", "running"):
            self.store.transition(
                row.task_id,
                state,
                event_type="task_" + state,
                actor="system",
                source="cofferdam",
            )
        self.store.open_turn(
            row.task_id,
            provider="validation",
            source="internal_test",
            started_at="2026-08-14T00:00:00Z",
        )
        self.store.append_event(
            row.task_id,
            "progress",
            actor="system",
            source="cofferdam",
            text="looked",
            evidence=(path_observation("src/foo.py"),),
        )
        (self.root / "src").mkdir()
        (self.root / "src" / "foo.py").write_text("x", encoding="utf-8")
        self.store.record_change_claims(
            row.task_id,
            (ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py"),),
            project_root=self.root,
            turn_number=1,
        )
        return row.task_id

    def _inputs(self):
        turn = self.store.turns(self.task_id)[0]
        bound = self.store.turn_bound(self.task_id, 1)
        return {
            "task_id": self.task_id,
            "turn": turn,
            "bound": bound,
            "events": self.store.events_in_bound(self.task_id, bound),
            "claims": self.store.change_claims(self.task_id),
            "ingestion_rows": self.store.claim_ingestion(self.task_id),
        }

    def test_assembly_runs_with_every_dangerous_callable_poisoned(self):
        import builtins
        import os
        import socket
        import subprocess
        import time

        inputs = self._inputs()

        def boom(*args, **kwargs):
            raise AssertionError("evidence assembly reached the world")

        patches = [
            (builtins, "open"),
            (os, "system"),
            (os, "popen"),
            (subprocess, "run"),
            (subprocess, "Popen"),
            (subprocess, "check_output"),
            (socket, "socket"),
            (socket, "create_connection"),
            (time, "time"),
            (time, "monotonic"),
        ]
        saved = [(obj, name, getattr(obj, name)) for obj, name in patches]
        for obj, name in patches:
            setattr(obj, name, boom)
        try:
            bundle = evidence_module.assemble(**inputs)
        finally:
            for obj, name, original in saved:
                setattr(obj, name, original)

        self.assertEqual(bundle.turn_number, 1)
        self.assertEqual(len(bundle.relationships), 1)
        self.assertEqual(bundle.relationships[0].relationship, "path_agreed")

    def test_assembly_writes_nothing_to_the_database(self):
        with sqlite3.connect(str(self.database)) as db:
            before = {
                name: db.execute("SELECT * FROM " + name).fetchall()
                for name in (
                    "tasks", "task_events", "task_turns", "task_turn_bounds",
                    "task_change_claims", "task_artifacts", "task_claim_ingestion",
                )
            }
        for _ in range(20):
            self.store.evidence_bundle(self.task_id, 1)
        with sqlite3.connect(str(self.database)) as db:
            after = {
                name: db.execute("SELECT * FROM " + name).fetchall()
                for name in (
                    "tasks", "task_events", "task_turns", "task_turn_bounds",
                    "task_change_claims", "task_artifacts", "task_claim_ingestion",
                )
            }
        self.assertEqual(after, before)

    def test_deleting_the_project_tree_does_not_change_the_bundle(self):
        """The historical-versus-live proof, stated as a fixture.

        The claimed file is removed from disk entirely. If assembly consulted
        the filesystem in any way, the bundle would change. It does not, because
        the input is the stored row and the repository is not an input.
        """
        import shutil

        before = self.store.evidence_bundle(self.task_id, 1).to_dict()
        shutil.rmtree(self.root)
        self.assertFalse(self.root.exists())
        self.assertEqual(self.store.evidence_bundle(self.task_id, 1).to_dict(), before)

    def test_rewriting_the_claimed_file_does_not_change_the_bundle(self):
        before = self.store.evidence_bundle(self.task_id, 1).to_dict()
        (self.root / "src" / "foo.py").write_text("completely different", encoding="utf-8")
        self.assertEqual(self.store.evidence_bundle(self.task_id, 1).to_dict(), before)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
