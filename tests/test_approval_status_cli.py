"""Behavioural tests for the read-only ``cofferdam approval-status`` command."""

import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from cofferdam import cli, hashing
from cofferdam.approval_store import _ApprovalStore as ApprovalStore
from cofferdam.dryrun import build_dry_run_artifact
from cofferdam.proposal import parse_proposal
from cofferdam.repo_view import FilesystemRepoView

from tests._approval_doubles import make_approval_entry, seed_approval

_DIFF = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
_PROPOSAL = {
    "schema_version": 1,
    "kind": "single_file_diff",
    "target_path": "src/app.py",
    "diff": _DIFF,
}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class ApprovalStatusCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("old\n")
        self._old_cwd = os.getcwd()
        os.chdir(self.root)
        # LIFO cleanup: restore cwd BEFORE removing the temp dir (Windows cannot
        # remove a directory that is still the process cwd).
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(os.chdir, self._old_cwd)
        self.proposal_path = self.root / "proposal.json"
        self.proposal_path.write_text(json.dumps(_PROPOSAL))

    def _bound_hash(self):
        view = FilesystemRepoView(self.root)
        parsed = parse_proposal(_PROPOSAL)
        return build_dry_run_artifact(parsed.proposal, view).bound_hash

    def _seed_active(self):
        view = FilesystemRepoView(self.root)
        root_id = hashing.repo_root_id(view.root_bytes())
        now = int(time.time())
        entry = make_approval_entry(
            bound_hash=self._bound_hash(),
            repo_root_id=root_id,
            created_at=now - 5,
            ttl=3600,
        )
        seed_approval(ApprovalStore(view), entry)

    def test_no_active_exits_1(self):
        code, out, err = run(["approval-status", "--file", str(self.proposal_path)])
        self.assertEqual(code, 1)
        self.assertIn("no active approval", out)
        self.assertEqual(err, "")

    def test_read_only_creates_no_state(self):
        # Fresh repo: no .cofferdam. A read-only status query must create nothing.
        self.assertFalse((self.root / ".cofferdam").exists())
        before = sorted(p.name for p in self.root.iterdir())
        code, _, _ = run(["approval-status", "--file", str(self.proposal_path)])
        self.assertEqual(code, 1)
        self.assertFalse((self.root / ".cofferdam").exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)

    def test_active_exits_0(self):
        self._seed_active()
        code, out, err = run(["approval-status", "--file", str(self.proposal_path)])
        self.assertEqual(code, 0)
        self.assertIn("active approval", out)
        self.assertEqual(err, "")

    def test_reads_stdin_when_no_file(self):
        self._seed_active()
        import sys
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(_PROPOSAL))
        try:
            code, out, err = run(["approval-status"])
        finally:
            sys.stdin = old_stdin
        self.assertEqual(code, 0)

    def test_malformed_json_exits_2(self):
        bad = self.root / "bad.json"
        bad.write_text("not json")
        code, out, err = run(["approval-status", "--file", str(bad)])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("not valid JSON", err)

    def test_rejected_proposal_exits_2(self):
        bad = self.root / "bad2.json"
        bad.write_text(json.dumps({"schema_version": 1, "kind": "x", "target_path": "y", "diff": "z"}))
        code, out, err = run(["approval-status", "--file", str(bad)])
        self.assertEqual(code, 2)

    def test_no_patch_bytes_in_output(self):
        self._seed_active()
        _, out, _ = run(["approval-status", "--file", str(self.proposal_path)])
        self.assertNotIn("@@", out)
        self.assertNotIn("+new", out)

    def test_unknown_argument_exits_2(self):
        code, out, err = run(["approval-status", "--yes"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
