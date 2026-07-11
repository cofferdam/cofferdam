"""Authority-boundary, import-boundary, and no-side-effects assertions for PR3c1.

Proves the structural claims: no public mint API, ``_mint`` is an unexported
internal seam, ``approve_cli`` is imported only by ``cli.py`` (and tests), the
supported command exposes no dependency-injection knobs, and the full interactive
mint flow performs no network, subprocess, or repository-target mutation and
writes only under ``.cofferdam/``.
"""

import ast
import inspect
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cofferdam
from cofferdam import approval, cli
from cofferdam import approve_cli
from cofferdam.dryrun import build_dry_run_artifact
from cofferdam.proposal import parse_proposal
from cofferdam.repo_view import FilesystemRepoView

_PKG_DIR = Path(cofferdam.__path__[0])

_DIFF = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
_PROPOSAL = {
    "schema_version": 1,
    "kind": "single_file_diff",
    "target_path": "src/app.py",
    "diff": _DIFF,
}


def _package_sources():
    for path in sorted(_PKG_DIR.glob("*.py")):
        yield path, path.read_text(encoding="utf-8")


def _imported_basenames(tree):
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[-1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name.split(".")[-1])
    return mods


class _Stream(io.StringIO):
    def __init__(self, initial="", tty=True):
        super().__init__(initial)
        self._tty = tty

    def isatty(self):
        return self._tty


class AuthorityBoundaryTests(unittest.TestCase):
    def test_no_public_mint_symbol(self):
        for mod in (approval, approve_cli):
            for bad in ("create_approval", "mint_approval"):
                self.assertFalse(hasattr(mod, bad), f"{mod.__name__} exposes {bad}")

    def test_mint_is_internal_unexported(self):
        # Underscore-named and not exported through any __all__.
        self.assertTrue(hasattr(approve_cli, "_mint"))
        self.assertNotIn("_mint", getattr(approve_cli, "__all__", []))
        self.assertNotIn("_mint", getattr(approval, "__all__", []))

    def test_command_registered_but_no_executor(self):
        self.assertIs(cli.COMMANDS["approve"], approve_cli.approve_command)
        self.assertNotIn("execute", cli.COMMANDS)
        self.assertNotIn("apply", cli.COMMANDS)

    def test_command_has_no_di_parameters(self):
        params = list(inspect.signature(approve_cli.approve_command).parameters)
        self.assertEqual(params, ["args"])

    def test_mint_di_is_keyword_only_internal(self):
        # The DI seam exists only for tests; it is keyword-only, not positional.
        sig = inspect.signature(approve_cli._mint)
        for name in ("store", "clock", "token_hex"):
            self.assertEqual(sig.parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)


class ImportBoundaryTests(unittest.TestCase):
    def test_approve_cli_imported_only_by_cli(self):
        offenders = []
        for path, src in _package_sources():
            if path.name in ("approve_cli.py", "cli.py"):
                continue
            if "approve_cli" in _imported_basenames(ast.parse(src)):
                offenders.append(path.name)
        self.assertEqual(offenders, [], f"approve_cli imported by library modules: {offenders}")

    def test_approve_cli_forbidden_imports(self):
        forbidden = {"socket", "subprocess", "urllib", "http", "requests",
                     "httpx", "ssl", "asyncio"}
        mods = _imported_basenames(ast.parse((_PKG_DIR / "approve_cli.py").read_text("utf-8")))
        self.assertEqual(mods & forbidden, set())

    def test_approve_cli_no_system_or_popen(self):
        tree = ast.parse((_PKG_DIR / "approve_cli.py").read_text("utf-8"))
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        for bad in ("system", "popen", "Popen"):
            self.assertNotIn(bad, attrs)

    def test_production_does_not_import_test_double(self):
        for path, src in _package_sources():
            self.assertNotIn("_approval_doubles", _imported_basenames(ast.parse(src)),
                             f"{path.name} imports the test double")


class NoSideEffectsFlowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("old\n")
        self.proposal_path = self.root / "proposal.json"
        self.proposal_path.write_text(json.dumps(_PROPOSAL))
        self.addCleanup(self._tmp.cleanup)
        self.view = FilesystemRepoView(self.root)
        self.bh = build_dry_run_artifact(parse_proposal(_PROPOSAL).proposal, self.view).bound_hash

    def _run_success(self):
        phrase = "APPROVE " + self.bh[:12] + "\n"
        stdin, stdout, stderr = _Stream(phrase), _Stream(), _Stream()
        with mock.patch("cofferdam.approve_cli.sys.stdin", stdin), \
             mock.patch("cofferdam.approve_cli.sys.stdout", stdout), \
             mock.patch("cofferdam.approve_cli.sys.stderr", stderr), \
             mock.patch("cofferdam.cli.sys.stdout", stdout), \
             mock.patch("cofferdam.cli.sys.stderr", stderr), \
             mock.patch("cofferdam.approve_cli.os.getcwd", return_value=str(self.root)):
            return cli.main(["approve", "--file", str(self.proposal_path)])

    def test_flow_uses_no_network_or_subprocess(self):
        with mock.patch("socket.socket", side_effect=AssertionError("network used")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("subprocess used")):
            code = self._run_success()
        self.assertEqual(code, 0)

    def test_flow_mutates_no_target_and_writes_only_cofferdam(self):
        code = self._run_success()
        self.assertEqual(code, 0)
        # The proposal target is untouched.
        self.assertEqual((self.root / "src" / "app.py").read_text(), "old\n")
        # Only .cofferdam/ was added at the repo top level.
        top = sorted(p.name for p in self.root.iterdir())
        self.assertEqual(top, [".cofferdam", "proposal.json", "src"])
        # And the only files under .cofferdam are the ledger + lock.
        state = sorted(p.name for p in (self.root / ".cofferdam").iterdir())
        self.assertEqual(state, ["approvals.jsonl", "approvals.lock"])

    def test_ledger_persists_no_patch_or_confirmation(self):
        self._run_success()
        text = (self.root / ".cofferdam" / "approvals.jsonl").read_text("utf-8")
        for marker in ("@@", "+++", "---", "APPROVE", "old\n+new"):
            self.assertNotIn(marker, text)


if __name__ == "__main__":
    unittest.main()
