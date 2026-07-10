"""PR3a must perform no mutation, network, or subprocess action.

The fixture (a real temp repo) is created *before* sabotage. Then every
mutation / network / subprocess surface is patched to raise, and the full PR3a
operation set (hashing, canonicalize, read-only pre-state, dry-run artifact) is
run over the fixture. Read-mode ``open`` is allowed (bounded pre-state reads);
write-mode ``open`` and all mutators must never be called.
"""

import builtins
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

from cofferdam import hashing
from cofferdam.canonicalize import canonical_root_bytes, canonicalize_target
from cofferdam.dryrun import build_dry_run_artifact
from cofferdam.paths import normalize_target
from cofferdam.prestate import read_prestate
from cofferdam.proposal import parse_proposal
from cofferdam.repo_view import FilesystemRepoView

MODIFY = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"


def _snapshot(root: Path):
    return sorted((p.relative_to(root).as_posix(),
                   p.stat().st_size if p.is_file() else -1)
                  for p in root.rglob("*"))


class NoSideEffectsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("old\n")
        self.view = FilesystemRepoView(self.root)

        self._saved = {}

        def forbid(name):
            def _raise(*a, **k):
                raise AssertionError(f"PR3a must not call {name}")
            return _raise

        real_open = builtins.open

        def guarded_open(file, mode="r", *a, **k):
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                raise AssertionError("PR3a must not open a file for writing")
            return real_open(file, mode, *a, **k)

        self._patch(socket, "socket", forbid("socket.socket"))
        self._patch(subprocess, "Popen", forbid("subprocess.Popen"))
        self._patch(subprocess, "run", forbid("subprocess.run"))
        self._patch(builtins, "open", guarded_open)
        self._patch(Path, "write_text", forbid("Path.write_text"))
        self._patch(Path, "write_bytes", forbid("Path.write_bytes"))
        self._patch(Path, "mkdir", forbid("Path.mkdir"))
        self._patch(os, "remove", forbid("os.remove"))
        self._patch(os, "unlink", forbid("os.unlink"))
        self._patch(os, "replace", forbid("os.replace"))
        self._patch(os, "rename", forbid("os.rename"))
        self._patch(os, "mkdir", forbid("os.mkdir"))
        self._patch(os, "makedirs", forbid("os.makedirs"))
        self._patch(os, "chmod", forbid("os.chmod"))

    def _patch(self, obj, name, value):
        self._saved[(obj, name)] = getattr(obj, name)
        setattr(obj, name, value)

    def tearDown(self):
        for (obj, name), value in self._saved.items():
            setattr(obj, name, value)
        self._tmp.cleanup()

    def test_pr3a_operations_have_no_side_effects(self):
        before = _snapshot(self.root)

        # hashing (pure)
        hashing.bound_hash("a", "b", "c", "d")

        # canonicalize (read-only stat)
        target = normalize_target("src/app.py").path
        canonicalize_target(self.root, target)
        canonical_root_bytes(self.root)

        # bounded read-only pre-state
        read_prestate(self.view, ("src", "app.py"))
        read_prestate(self.view, ("src", "missing.py"))

        # full dry-run artifact
        result = parse_proposal(
            {"schema_version": 1, "kind": "single_file_diff",
             "target_path": "src/app.py", "diff": MODIFY}
        )
        art = build_dry_run_artifact(result.proposal, self.view)
        self.assertEqual(art.guard_decision, "needs_approval")

        self.assertEqual(_snapshot(self.root), before)


if __name__ == "__main__":
    unittest.main()
