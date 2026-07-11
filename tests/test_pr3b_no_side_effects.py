"""Authority-boundary, scope, and no-side-effects assertions for PR3b.

These prove the structural claims: no production approval mint exists, no
forbidden dependency is imported by package code, the test double is never
imported by production, the ledger persists no patch/file content, and the full
lookup/consume flow runs without any network/subprocess/repository mutation.
"""

import ast
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import inspect

import cofferdam
from cofferdam import approval, hashing
from cofferdam import approval_store as store_mod
from cofferdam import cli
from cofferdam.approval import (
    _consume_approval as consume_approval,
    _find_valid_approval as find_valid_approval,
)
from cofferdam.approval_store import _ApprovalStore as ApprovalStore
from cofferdam.repo_view import FilesystemRepoView

from tests._approval_doubles import FakeClock, make_approval_entry, seed_approval

_PKG_DIR = Path(cofferdam.__path__[0])


def _package_sources():
    for path in sorted(_PKG_DIR.glob("*.py")):
        yield path, path.read_text(encoding="utf-8")


def _imported_modules(tree):
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module.split(".")[0])
            if node.level:  # relative import
                mods.add("." * node.level)
    return mods


def _defined_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _attribute_names(tree):
    return {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}


class AuthorityBoundaryTests(unittest.TestCase):
    def test_no_production_mint_symbol(self):
        for bad in ("create_approval", "mint_approval", "TtyConfirmer", "Confirmer"):
            self.assertFalse(hasattr(approval, bad), f"approval exposes {bad!r}")

    def test_no_confirm_module(self):
        self.assertFalse((_PKG_DIR / "confirm.py").exists())

    def test_no_mint_or_confirmer_defined_anywhere(self):
        forbidden = {"create_approval", "mint_approval", "TtyConfirmer", "Confirmer"}
        for path, src in _package_sources():
            defined = _defined_names(ast.parse(src))
            self.assertEqual(
                defined & forbidden, set(), f"{path.name} defines a forbidden symbol"
            )

    def test_no_cli_approve_or_execute_command(self):
        self.assertNotIn("approve", cli.COMMANDS)
        self.assertNotIn("execute", cli.COMMANDS)
        self.assertIn("approval-status", cli.COMMANDS)

    def test_supported_wrappers_have_no_di_parameters(self):
        for fn in (approval.find_valid_approval, approval.consume_approval):
            params = list(inspect.signature(fn).parameters)
            self.assertEqual(params, ["bound_hash", "repo_view"], f"{fn.__name__} exposes DI")
            for banned in ("store", "clock", "lock", "state_path", "ttl",
                           "approval_id", "created_at", "expires_at"):
                self.assertNotIn(banned, params)

    def test_internal_di_functions_not_exported(self):
        self.assertEqual(
            set(approval.__all__),
            {"TTL_SECONDS", "SCHEMA_VERSION", "ApprovalError", "LedgerError",
             "ApprovalView", "find_valid_approval", "consume_approval"},
        )
        self.assertNotIn("_find_valid_approval", approval.__all__)
        self.assertNotIn("_consume_approval", approval.__all__)

    def test_store_is_internal_only(self):
        # No public ApprovalStore name; only the underscore-prefixed internal class.
        self.assertFalse(hasattr(store_mod, "ApprovalStore"))
        self.assertTrue(hasattr(store_mod, "_ApprovalStore"))
        # No public append surface on the module or the class.
        self.assertFalse(hasattr(store_mod, "append_approval"))
        self.assertFalse(hasattr(store_mod._ApprovalStore, "append_approval"))
        self.assertTrue(hasattr(store_mod._ApprovalStore, "_append_approval"))


class ForbiddenScopeTests(unittest.TestCase):
    FORBIDDEN_IMPORTS = {
        "socket", "subprocess", "urllib", "http", "requests", "httpx",
        "ssl", "asyncio", "secrets",
    }

    def test_no_forbidden_imports_in_package(self):
        for path, src in _package_sources():
            mods = _imported_modules(ast.parse(src))
            offenders = mods & self.FORBIDDEN_IMPORTS
            self.assertEqual(offenders, set(), f"{path.name} imports {offenders}")

    def test_no_token_hex_or_system_calls(self):
        for path, src in _package_sources():
            attrs = _attribute_names(ast.parse(src))
            for bad in ("token_hex", "system", "popen", "Popen"):
                self.assertNotIn(bad, attrs, f"{path.name} uses {bad}")

    def test_production_does_not_import_test_double(self):
        # AST-based (not a substring scan) so docstrings that merely *mention*
        # the test double do not trip the check.
        for path, src in _package_sources():
            mods = set()
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    mods.add(node.module)
                elif isinstance(node, ast.Import):
                    mods.update(alias.name for alias in node.names)
            for mod in mods:
                self.assertNotIn("_approval_doubles", mod, f"{path.name} imports the test double")
                self.assertFalse(
                    mod == "tests" or mod.startswith("tests."),
                    f"{path.name} imports {mod}",
                )


class PrivacyAndFlowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.view = FilesystemRepoView(self.root)
        self.root_id = hashing.repo_root_id(self.view.root_bytes())
        self.store = ApprovalStore(self.view)
        self.clock = FakeClock(1100)
        self.addCleanup(self._tmp.cleanup)

    def _seed(self):
        seed_approval(
            self.store,
            make_approval_entry(bound_hash="b" * 64, repo_root_id=self.root_id),
        )

    def test_ledger_has_no_patch_or_file_content(self):
        self._seed()
        consume_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        text = (self.root / ".cofferdam" / "approvals.jsonl").read_text(encoding="utf-8")
        for marker in ("@@", "---", "+++", "diff"):
            self.assertNotIn(marker, text)

    def test_flow_runs_without_network_or_subprocess(self):
        # Sabotage the forbidden surfaces; the flow must not touch them.
        with mock.patch("socket.socket", side_effect=AssertionError("network used")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("subprocess used")):
            self._seed()
            self.assertIsNotNone(
                find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
            )
            consumed = consume_approval(
                "b" * 64, store=self.store, repo_view=self.view, clock=self.clock
            )
            self.assertEqual(consumed.state, "consumed")

    def test_only_cofferdam_directory_is_written(self):
        (self.root / "keep.txt").write_text("keep")
        self._seed()
        consume_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        top = sorted(p.name for p in self.root.iterdir())
        self.assertEqual(top, [".cofferdam", "keep.txt"])
        self.assertEqual((self.root / "keep.txt").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
