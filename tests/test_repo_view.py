"""Tests for the concrete read-only FilesystemRepoView."""

import os
import tempfile
import unittest
from pathlib import Path

from cofferdam.repo_view import FilesystemRepoView, PathType, RepoView


class FilesystemRepoViewTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "file.txt").write_text("hello", encoding="utf-8")
        (self.root / "sub").mkdir()
        self.view = FilesystemRepoView(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_is_runtime_repo_view(self):
        self.assertIsInstance(self.view, RepoView)

    def test_regular_file(self):
        self.assertEqual(self.view.path_type(("file.txt",)), PathType.REGULAR)

    def test_directory(self):
        self.assertEqual(self.view.path_type(("sub",)), PathType.DIRECTORY)

    def test_missing(self):
        self.assertEqual(self.view.path_type(("nope.txt",)), PathType.MISSING)

    def test_escape_attempt_is_missing_not_error(self):
        # Fail-closed: a would-be escape does not raise.
        self.assertEqual(self.view.path_type(("..", "..", "etc")), PathType.MISSING)

    def test_symlink_detected_without_following(self):
        link = self.root / "link.txt"
        try:
            os.symlink(self.root / "file.txt", link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not permitted on this platform")
        self.assertEqual(self.view.path_type(("link.txt",)), PathType.SYMLINK)

    def test_read_only_no_mutation(self):
        before = sorted(p.name for p in self.root.iterdir())
        for parts in (("file.txt",), ("sub",), ("missing",), ("a", "b", "c")):
            self.view.path_type(parts)
        after = sorted(p.name for p in self.root.iterdir())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
