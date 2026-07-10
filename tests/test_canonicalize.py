"""Canonicalization tests against real temporary directories.

Absolute real paths are machine-specific, so these assert *behavior*
(acceptance, rejection, containment, determinism) rather than a fixed hash
vector — the stable hash vectors live in test_hashing.py.
"""

import os
import tempfile
import unittest
from pathlib import Path

from cofferdam.canonicalize import (
    CanonicalizationError,
    canonical_root_bytes,
    canonicalize_target,
)
from cofferdam.paths import normalize_target


def norm(path: str):
    result = normalize_target(path)
    assert result.ok, (path, result.reasons)
    return result.path


class CanonicalizeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_absent_target_under_valid_dir(self):
        (self.root / "src").mkdir()
        ct = canonicalize_target(self.root, norm("src/new.py"))
        self.assertTrue(ct.real_path.is_absolute())
        self.assertIn(b"src/new.py", ct.path_bytes)

    def test_existing_regular_target(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("x")
        ct = canonicalize_target(self.root, norm("src/app.py"))
        self.assertTrue(ct.real_path.exists())

    def test_deterministic(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("x")
        a = canonicalize_target(self.root, norm("src/app.py"))
        b = canonicalize_target(self.root, norm("src/app.py"))
        self.assertEqual(a.path_bytes, b.path_bytes)
        self.assertEqual(a.real_path, b.real_path)

    def test_existing_directory_target_rejected(self):
        (self.root / "src").mkdir()
        with self.assertRaises(CanonicalizationError):
            canonicalize_target(self.root, norm("src"))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unsupported")
    def test_symlink_component_rejected(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        (self.root).mkdir(exist_ok=True)
        try:
            os.symlink(outside, self.root / "link", target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"cannot create symlink: {exc}")
        with self.assertRaises(CanonicalizationError):
            canonicalize_target(self.root, norm("link/secret.txt"))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unsupported")
    def test_symlink_final_target_rejected(self):
        (self.root / "real.txt").write_text("data")
        try:
            os.symlink(self.root / "real.txt", self.root / "alias.txt")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"cannot create symlink: {exc}")
        with self.assertRaises(CanonicalizationError):
            canonicalize_target(self.root, norm("alias.txt"))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unsupported")
    def test_symlink_escape_out_of_root(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        (outside / "secret.txt").write_text("s")
        try:
            os.symlink(outside, self.root / "esc", target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"cannot create symlink: {exc}")
        with self.assertRaises(CanonicalizationError):
            canonicalize_target(self.root, norm("esc/secret.txt"))

    def test_root_bytes_deterministic_and_scoped(self):
        first = canonical_root_bytes(self.root)
        self.assertEqual(first, canonical_root_bytes(self.root))
        other = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(other, ignore_errors=True))
        self.assertNotEqual(first, canonical_root_bytes(other))


if __name__ == "__main__":
    unittest.main()
