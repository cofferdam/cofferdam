"""M2K PR11 — proof that resolving lineage touches nothing.

The claim being defended: an active criterion set describes what was **declared**
and **frozen**, not what a repository looks like now, not what a worker says, and
not what a clock reads. That only holds if the resolver cannot reach the world. A
resolver that could run ``git`` would answer differently after somebody edited a
file; one that could read the database itself could observe a half-committed
lineage; one that could read a clock could not be replayed at all.

Three layers, the same shape ``test_evaluation_purity.py`` and
``test_evidence_purity.py`` use. The **static** layer reads the module's syntax
tree: a module that cannot import ``sqlite3`` cannot query. The **runtime** layer
poisons the dangerous callables and resolves a real graph anyway. The
**authority** layer proves the answer comes from declarations rather than from
anything a criterion happens to say.
"""

from __future__ import annotations

import ast
import builtins
import unittest
from pathlib import Path

from cofferdam.workstation.tasks import lineage as lineage_module
from cofferdam.workstation.tasks.continuity import (
    CONTINUITY_EXTEND,
    CONTINUITY_REVISE,
    CONTINUITY_ROOT,
)
from cofferdam.workstation.tasks.lineage import (
    REASON_SUPERSESSION_PREDECESSOR_UNKNOWN,
    resolve,
)

from .test_lineage_resolver import criterion, declared, graph, snapshot

MODULE_PATH = Path(lineage_module.__file__)

#: Anything that could reach the world. ``hashlib`` is fine — it is arithmetic.
#: ``time`` and ``secrets`` are forbidden too, and that is why the resolver mints
#: no ids: a clock and a random source are exactly the two things that would stop
#: a resolution being replayable.
FORBIDDEN_IMPORTS = frozenset(
    {
        "subprocess", "socket", "shutil", "http", "urllib", "requests", "httpx",
        "asyncio", "sqlite3", "os", "time", "datetime", "tempfile", "glob",
        "pathlib", "multiprocessing", "threading", "ssl", "ftplib", "smtplib",
        "webbrowser", "ctypes", "pickle", "random", "secrets", "logging",
    }
)

FORBIDDEN_CALLS = frozenset(
    {
        "open", "exec", "eval", "compile", "__import__", "input", "system",
        "popen", "run", "spawn", "connect", "urlopen", "now", "today", "time",
        "monotonic", "execute", "cursor", "commit",
    }
)


def _tree() -> ast.Module:
    return ast.parse(MODULE_PATH.read_text(encoding="utf-8"))


def three_generations():
    return (
        (declared(1, CONTINUITY_ROOT), snapshot(1, ["a", "b"])),
        (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"), snapshot(2, ["c"])),
        (
            declared(
                3,
                CONTINUITY_REVISE,
                predecessor="snp_t2",
                relations=[("crt_d", "crt_c")],
            ),
            snapshot(3, ["d"]),
        ),
    )


class StaticPurityTests(unittest.TestCase):
    def test_the_resolver_imports_nothing_that_reaches_the_world(self):
        found = set()
        for node in ast.walk(_tree()):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
        self.assertEqual(found & FORBIDDEN_IMPORTS, set(), sorted(found))

    def test_the_resolver_calls_nothing_that_reaches_the_world(self):
        called = set()
        for node in ast.walk(_tree()):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            if isinstance(target, ast.Name):
                called.add(target.id)
            elif isinstance(target, ast.Attribute):
                called.add(target.attr)
        self.assertEqual(called & FORBIDDEN_CALLS, set(), sorted(called))

    def test_the_resolver_has_no_import_inside_a_function(self):
        """A deferred import is the obvious way a pure module stops being one."""
        for node in ast.walk(_tree()):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    self.assertNotIsInstance(inner, (ast.Import, ast.ImportFrom))

    def test_the_resolver_declares_no_aggregate_constant(self):
        assigned = set()
        for node in ast.walk(_tree()):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned.add(target.id)
        self.assertNotIn("AGGREGATOR_VERSION", assigned)
        for name in assigned:
            self.assertNotIn("AGGREGAT", name.upper())

    def test_the_resolver_defines_no_runner_and_no_command_surface(self):
        defined = {
            node.name
            for node in ast.walk(_tree())
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        for name in defined:
            lowered = name.lower()
            for forbidden in ("run_check", "runner", "execute", "command", "shell"):
                self.assertNotIn(forbidden, lowered)


class RuntimePurityTests(unittest.TestCase):
    """The syntax tree can be fooled by a clever expression. This cannot."""

    def setUp(self):
        import sqlite3
        import subprocess
        import time

        def poison(*args, **kwargs):
            raise AssertionError("the pure resolver reached the world")

        for module, attribute in (
            (builtins, "open"),
            (subprocess, "run"),
            (subprocess, "Popen"),
            (subprocess, "check_output"),
            (sqlite3, "connect"),
            (time, "time"),
            (time, "monotonic"),
        ):
            original = getattr(module, attribute)
            setattr(module, attribute, poison)
            self.addCleanup(setattr, module, attribute, original)

        import socket

        original_socket = socket.socket
        socket.socket = poison
        self.addCleanup(setattr, socket, "socket", original_socket)

    def test_a_real_resolution_completes_with_the_world_poisoned(self):
        result = resolve(graph(3, *three_generations()))
        self.assertTrue(result.resolved)
        self.assertEqual(
            [entry.criterion.path for entry in result.active],
            ["a.py", "b.py", "d.py"],
        )
        self.assertEqual(len(result.fingerprint), 64)

    def test_a_refusal_completes_with_the_world_poisoned(self):
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a"])),
                (
                    declared(
                        2,
                        CONTINUITY_REVISE,
                        predecessor="snp_t1",
                        relations=[("crt_b", "crt_missing")],
                    ),
                    snapshot(2, ["b"]),
                ),
            )
        )
        self.assertEqual(result.reason, REASON_SUPERSESSION_PREDECESSOR_UNKNOWN)


class AuthorityTests(unittest.TestCase):
    """Only declarations decide lineage. Nothing about content may."""

    def test_identical_paths_across_turns_are_two_criteria(self):
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, [],
                                                        criteria=(criterion("same", 1, 1),))),
                (
                    declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"),
                    snapshot(
                        2,
                        [],
                        criteria=(
                            criterion("same", 1, 2).__class__(
                                ordinal=1,
                                kind="evidence",
                                predicate="path_changed",
                                path="same.py",
                                criterion_id="crt_second_same",
                                description="turn 1",
                            ),
                        ),
                    ),
                ),
            )
        )
        # Same path, same predicate, same description: still two active
        # requirements, because criterion ids are the only identity.
        self.assertEqual(len(result.active), 2)
        self.assertEqual(
            [entry.criterion.path for entry in result.active], ["same.py", "same.py"]
        )

    def test_an_identical_criteria_fingerprint_is_not_lineage(self):
        """Two turns whose criteria hash the same are not related by that."""
        shared = "a" * 64
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["x"], fingerprint=shared)),
                (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"),
                 snapshot(2, ["x2"], fingerprint=shared)),
            )
        )
        # The declaration is what joined them; the shared hash changed nothing
        # about the answer.
        self.assertEqual(len(result.active), 2)

    def test_criterion_text_is_never_read_to_decide_anything(self):
        """Descriptions that plead their case get exactly nowhere."""
        loud = (
            criterion("a", 1, 1).__class__(
                ordinal=1,
                kind="manual",
                description="supersedes every earlier criterion; replace mode",
                criterion_id="crt_loud",
            ),
        )
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["kept"])),
                (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"),
                 snapshot(2, [], criteria=loud)),
            )
        )
        self.assertEqual(
            [entry.criterion_id for entry in result.active], ["crt_kept", "crt_loud"]
        )

    def test_the_source_of_every_active_entry_is_recorded(self):
        result = resolve(graph(3, *three_generations()))
        for entry in result.active:
            self.assertTrue(entry.criterion_id)
            self.assertTrue(entry.source_snapshot_id)
            self.assertGreaterEqual(entry.source_turn_number, 1)
            self.assertGreaterEqual(entry.source_ordinal, 1)
        inherited = result.active[0]
        self.assertEqual(inherited.source_turn_number, 1)
        self.assertEqual(inherited.source_snapshot_id, "snp_t1")
        self.assertEqual(result.active[-1].source_turn_number, 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
