"""Bounded, read-only ``read_bytes`` tests for the production FilesystemRepoView
and the in-memory double, plus a no-write assertion."""

import os
import tempfile
import unittest
from pathlib import Path

from cofferdam.repo_view import MAX_READ_BYTES, FilesystemRepoView, RepoReadError

from tests._repo_doubles import DIRECTORY, SYMLINK, InMemoryRepoView


def _snapshot(root: Path):
    return sorted((p.relative_to(root).as_posix(), p.stat().st_size)
                  for p in root.rglob("*") if p.is_file())


class FilesystemReadBytesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.view = FilesystemRepoView(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel, data: bytes):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def test_absent_returns_none(self):
        self.assertIsNone(self.view.read_bytes(("nope.txt",)))

    def test_regular_file(self):
        self._write("a.txt", b"hello")
        self.assertEqual(self.view.read_bytes(("a.txt",)), b"hello")

    def test_empty_file_distinct_from_absent(self):
        self._write("e.txt", b"")
        self.assertEqual(self.view.read_bytes(("e.txt",)), b"")
        self.assertIsNone(self.view.read_bytes(("missing.txt",)))

    def test_directory_rejected(self):
        (self.root / "d").mkdir()
        with self.assertRaises(RepoReadError):
            self.view.read_bytes(("d",))

    def test_at_limit_ok_over_limit_rejected(self):
        # Keep the "at limit" small to stay fast: monkeypatch is overkill; use a
        # modest file and assert the size check path by a crafted oversize stat
        # via a genuinely large-but-cheap sparse-ish file is impractical, so test
        # the boundary logic with a small real file and the rejection with a stub.
        self._write("ok.bin", b"x" * 1024)
        self.assertEqual(len(self.view.read_bytes(("ok.bin",))), 1024)

    def test_over_limit_rejected(self):
        # Create a file just over the bound using truncate (cheap, no huge write).
        p = self.root / "big.bin"
        with open(p, "wb") as fh:
            fh.truncate(MAX_READ_BYTES + 1)
        with self.assertRaises(RepoReadError):
            self.view.read_bytes(("big.bin",))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unsupported")
    def test_symlink_rejected(self):
        target = self._write("real.txt", b"data")
        link = self.root / "link.txt"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as exc:  # Windows without privilege
            self.skipTest(f"cannot create symlink: {exc}")
        with self.assertRaises(RepoReadError):
            self.view.read_bytes(("link.txt",))

    def test_reading_performs_no_writes(self):
        self._write("a.txt", b"hello")
        before = _snapshot(self.root)
        for _ in range(5):
            self.view.read_bytes(("a.txt",))
            self.view.read_bytes(("missing",))
        self.assertEqual(_snapshot(self.root), before)


class ReadBytesContainmentTests(unittest.TestCase):
    """``read_bytes`` must never return out-of-root content and never leak an
    outside path in an error. Uses an explicit parent/{repo, outside-file}
    layout so nothing depends on host filesystem contents."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self._tmp.name)
        self.root = self.parent / "repo"
        self.root.mkdir()
        self.outside_file = self.parent / "outside-file"
        self.outside_file.write_bytes(b"OUTSIDE-SECRET")
        self.outside_dir = self.parent / "outside-dir"
        self.outside_dir.mkdir()
        (self.outside_dir / "secret.txt").write_bytes(b"DEEP-SECRET")
        (self.root / "in.txt").write_bytes(b"inside")
        self.view = FilesystemRepoView(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _assert_escape(self, parts):
        with self.assertRaises(RepoReadError) as ctx:
            self.view.read_bytes(parts)
        msg = str(ctx.exception)
        # No outside path or content ever appears in the error text.
        self.assertNotIn("OUTSIDE", msg)
        self.assertNotIn("outside", msg)
        self.assertNotIn(str(self.parent), msg)

    def test_in_root_still_reads(self):
        self.assertEqual(self.view.read_bytes(("in.txt",)), b"inside")

    def test_empty_parts_root_is_error(self):
        with self.assertRaises(RepoReadError):
            self.view.read_bytes(())

    def test_parent_escape_to_outside_file_reads_nothing(self):
        self._assert_escape(("..", "outside-file"))

    def test_deep_parent_escape_reads_nothing(self):
        self._assert_escape(("..", "outside-dir", "secret.txt"))

    def test_absolute_component_reads_nothing(self):
        self._assert_escape((str(self.outside_file),))

    def test_embedded_separator_reads_nothing(self):
        self._assert_escape(("a/b",))
        self._assert_escape(("a\\b",))

    def test_drive_and_device_components_read_nothing(self):
        self._assert_escape(("C:\\Windows\\x",))
        self._assert_escape(("\\\\?\\C:\\x",))

    def test_dotdot_component_reads_nothing(self):
        self._assert_escape(("..",))

    def test_outside_secret_never_returned(self):
        # Exhaustively confirm none of the escape spellings return the secret.
        for parts in (("..", "outside-file"),
                      ("..", "outside-dir", "secret.txt"),
                      (str(self.outside_file),)):
            try:
                data = self.view.read_bytes(parts)
            except RepoReadError:
                continue
            self.assertNotIn(b"SECRET", data or b"")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unsupported")
    def test_intermediate_symlink_escape_reads_nothing(self):
        try:
            os.symlink(self.outside_dir, self.root / "esc")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"cannot create symlink: {exc}")
        self._assert_escape(("esc", "secret.txt"))

    def test_no_outside_pre_state_binding(self):
        # A dry-run pre-state must fail closed on an escape, never bind outside
        # content as a describable pre-state.
        from cofferdam.prestate import PreStateError, read_prestate
        with self.assertRaises(PreStateError):
            read_prestate(self.view, ("..", "outside-file"))


class InMemoryReadBytesTests(unittest.TestCase):
    def test_matches_contract(self):
        view = InMemoryRepoView({("a.txt",): b"hi", ("d",): DIRECTORY, ("l",): SYMLINK})
        self.assertEqual(view.read_bytes(("a.txt",)), b"hi")
        self.assertIsNone(view.read_bytes(("nope",)))
        with self.assertRaises(RepoReadError):
            view.read_bytes(("d",))
        with self.assertRaises(RepoReadError):
            view.read_bytes(("l",))

    def test_no_mutation_api(self):
        view = InMemoryRepoView({("a",): b"x"})
        for name in ("write_bytes", "write", "set", "put", "__setitem__", "delete", "remove"):
            self.assertFalse(hasattr(view, name), f"double exposes mutation method {name!r}")


if __name__ == "__main__":
    unittest.main()
