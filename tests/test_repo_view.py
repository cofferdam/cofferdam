"""Tests for the concrete read-only FilesystemRepoView, including **enforced root
containment** (PR02a).

Every containment test builds its own explicit layout::

    parent/
    ├── repo/          (the repository root)
    ├── outside-dir/   (a real directory OUTSIDE the root)
    └── outside-file   (a real regular file OUTSIDE the root)

so results never depend on the host having (or lacking) ``/etc``, ``C:\\Windows``,
or any other path — the previous ``../../etc`` test passed on Windows only because
that host path happened not to exist.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cofferdam.repo_view as repo_view_mod
from cofferdam.repo_view import FilesystemRepoView, PathType, RepoReadError, RepoView


def _symlink(src: Path, dst: Path) -> None:
    """Create a symlink or skip the test if the platform/privilege disallows it."""
    if not hasattr(os, "symlink"):
        raise unittest.SkipTest("symlink unsupported")
    try:
        os.symlink(src, dst)
    except (OSError, NotImplementedError) as exc:  # Windows without privilege
        raise unittest.SkipTest(f"cannot create symlink: {exc}")


class _Layout(unittest.TestCase):
    """Shared explicit parent/{repo, outside-dir, outside-file} layout."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self._tmp.name)
        self.root = self.parent / "repo"
        self.root.mkdir()
        self.outside_dir = self.parent / "outside-dir"
        self.outside_dir.mkdir()
        self.outside_file = self.parent / "outside-file"
        self.outside_file.write_text("OUTSIDE-SECRET", encoding="utf-8")
        (self.root / "file.txt").write_text("hello", encoding="utf-8")
        (self.root / "sub").mkdir()
        self.view = FilesystemRepoView(self.root)

    def tearDown(self):
        self._tmp.cleanup()


class FilesystemRepoViewTests(_Layout):
    def test_is_runtime_repo_view(self):
        self.assertIsInstance(self.view, RepoView)

    def test_regular_file(self):
        self.assertEqual(self.view.path_type(("file.txt",)), PathType.REGULAR)

    def test_directory(self):
        self.assertEqual(self.view.path_type(("sub",)), PathType.DIRECTORY)

    def test_missing(self):
        self.assertEqual(self.view.path_type(("nope.txt",)), PathType.MISSING)

    def test_empty_parts_is_root_directory(self):
        self.assertEqual(self.view.path_type(()), PathType.DIRECTORY)

    def test_read_only_no_mutation(self):
        before = sorted(p.name for p in self.root.iterdir())
        for parts in (("file.txt",), ("sub",), ("missing",), ("a", "b", "c"),
                      ("..", "outside-file"), ("/abs",)):
            self.view.path_type(parts)
        after = sorted(p.name for p in self.root.iterdir())
        self.assertEqual(before, after)


class LexicalContainmentTests(_Layout):
    """A would-be escape is fail-closed to MISSING and never discloses the real
    outside object's type — deterministically on every platform."""

    def test_single_parent_escape_to_real_outside_dir(self):
        # Escapes to the REAL existing parent/outside-dir; must NOT report DIRECTORY.
        self.assertEqual(self.view.path_type(("..", "outside-dir")), PathType.MISSING)

    def test_single_parent_escape_to_real_outside_file(self):
        self.assertEqual(self.view.path_type(("..", "outside-file")), PathType.MISSING)

    def test_repeated_parent_escape(self):
        self.assertEqual(self.view.path_type(("..", "..", "..", "x")), PathType.MISSING)

    def test_bare_parent(self):
        self.assertEqual(self.view.path_type(("..",)), PathType.MISSING)

    def test_absolute_posix_component(self):
        # An absolute component would replace the root under pathlib join.
        self.assertEqual(self.view.path_type((str(self.outside_dir),)), PathType.MISSING)
        self.assertEqual(self.view.path_type(("/" + self.outside_file.name,)), PathType.MISSING)

    def test_embedded_forward_slash(self):
        self.assertEqual(self.view.path_type(("a/b",)), PathType.MISSING)

    def test_embedded_backslash(self):
        self.assertEqual(self.view.path_type(("a\\b",)), PathType.MISSING)

    def test_dot_and_dotdot_components(self):
        self.assertEqual(self.view.path_type((".",)), PathType.MISSING)
        self.assertEqual(self.view.path_type(("sub", "..", "file.txt")), PathType.MISSING)

    def test_empty_component(self):
        self.assertEqual(self.view.path_type(("",)), PathType.MISSING)
        self.assertEqual(self.view.path_type(("sub", "")), PathType.MISSING)

    def test_windows_drive_qualified_component(self):
        self.assertEqual(self.view.path_type(("C:\\Windows",)), PathType.MISSING)

    def test_windows_drive_relative_component(self):
        self.assertEqual(self.view.path_type(("C:rel",)), PathType.MISSING)

    def test_colon_alternate_data_stream(self):
        self.assertEqual(self.view.path_type(("file.txt:stream",)), PathType.MISSING)

    def test_unc_component(self):
        self.assertEqual(self.view.path_type(("\\\\server\\share",)), PathType.MISSING)

    def test_device_namespace_components(self):
        self.assertEqual(self.view.path_type(("\\\\?\\C:\\x",)), PathType.MISSING)
        self.assertEqual(self.view.path_type(("\\\\.\\PhysicalDrive0",)), PathType.MISSING)

    def test_escape_never_discloses_outside_type(self):
        # Neither the outside dir nor the outside file may be distinguishable
        # from "missing" through path_type.
        self.assertEqual(self.view.path_type(("..", "outside-dir")), PathType.MISSING)
        self.assertEqual(self.view.path_type(("..", "outside-file")), PathType.MISSING)
        self.assertEqual(self.view.path_type(("..", "does-not-exist")), PathType.MISSING)


class SymlinkContainmentTests(_Layout):
    def test_final_symlink_inside_reported_as_symlink(self):
        _symlink(self.root / "file.txt", self.root / "link_in.txt")
        self.assertEqual(self.view.path_type(("link_in.txt",)), PathType.SYMLINK)

    def test_final_symlink_outside_reported_as_symlink_not_followed(self):
        _symlink(self.outside_file, self.root / "link_out.txt")
        # Reported as SYMLINK (never followed to the outside target).
        self.assertEqual(self.view.path_type(("link_out.txt",)), PathType.SYMLINK)

    def test_broken_final_symlink_reported_as_symlink(self):
        _symlink(self.root / "does-not-exist", self.root / "broken.txt")
        self.assertEqual(self.view.path_type(("broken.txt",)), PathType.SYMLINK)

    def test_intermediate_symlink_inside_root_fails_closed(self):
        # sub2 -> sub (inside root); an INTERMEDIATE symlink is rejected regardless.
        _symlink(self.root / "sub", self.root / "sub2")
        self.assertEqual(self.view.path_type(("sub2", "file.txt")), PathType.MISSING)

    def test_intermediate_symlink_outside_root_fails_closed(self):
        # esc -> outside-dir; ("esc","x") must not resolve out of the root.
        _symlink(self.outside_dir, self.root / "esc")
        self.assertEqual(self.view.path_type(("esc", "x")), PathType.MISSING)

    def test_prefix_walk_still_sees_symlink_component(self):
        # The guard detects symlink components by asking path_type per prefix;
        # a symlink as the FINAL component of a prefix must still be SYMLINK.
        _symlink(self.outside_dir, self.root / "esc")
        self.assertEqual(self.view.path_type(("esc",)), PathType.SYMLINK)


class IntermediateWalkOSErrorTests(_Layout):
    """Only ``FileNotFoundError`` on an intermediate component means "missing".
    Every other ``OSError`` (an uninspectable component we cannot prove is not a
    symlink escape) fails closed — never proceeding to ``realpath``/``open`` on a
    deeper target."""

    def setUp(self):
        super().setUp()
        (self.root / "sub" / "file.txt").write_text("deep", encoding="utf-8")

    def _lstat_raising_on_sub(self, exc):
        """A side_effect that raises ``exc`` for the intermediate ``sub`` prefix
        and delegates to the real ``lstat`` otherwise."""
        real = os.lstat

        def side_effect(path, *a, **k):
            if os.fspath(path).replace("\\", "/").endswith("/sub"):
                raise exc
            return real(path, *a, **k)

        return side_effect

    def _assert_fails_closed(self, exc):
        parts = ("sub", "file.txt")
        # Guard: neither realpath (step 3) nor open may be reached after the walk
        # fails closed on the intermediate component.
        with mock.patch("cofferdam.repo_view.os.lstat",
                        side_effect=self._lstat_raising_on_sub(exc)), \
             mock.patch("cofferdam.repo_view.os.path.realpath",
                        side_effect=AssertionError("realpath must not run")), \
             mock.patch("builtins.open",
                        side_effect=AssertionError("open must not run")):
            self.assertEqual(self.view.path_type(parts), PathType.MISSING)
            with self.assertRaises(RepoReadError) as ctx:
                self.view.read_bytes(parts)
        msg = str(ctx.exception)
        self.assertNotIn("sub", msg)          # no path/component in the error
        self.assertNotIn(str(self.root), msg)

    def test_permission_error_fails_closed(self):
        self._assert_fails_closed(PermissionError("denied"))

    def test_not_a_directory_error_fails_closed(self):
        self._assert_fails_closed(NotADirectoryError("not a dir"))

    def test_generic_oserror_fails_closed(self):
        self._assert_fails_closed(OSError("synthetic failure"))

    def test_file_not_found_intermediate_terminates_as_missing(self):
        # A genuinely absent intermediate (no mock): resolution terminates and
        # the target is MISSING/None — the ordinary missing-path contract, not
        # an error. This is what distinguishes FileNotFoundError from every
        # other OSError above (which fails closed to RepoReadError): *only*
        # FileNotFoundError maps to None.
        self.assertEqual(self.view.path_type(("nope", "file.txt")), PathType.MISSING)
        self.assertIsNone(self.view.read_bytes(("nope", "file.txt")))

        # A *mocked* FileNotFoundError on the intermediate `sub` must terminate
        # resolution immediately. Even though sub/file.txt really exists (setUp
        # wrote it), the resolver must NOT continue to inspect the deeper target:
        # it stays MISSING/None, and no deeper lstat, realpath, or open runs.
        real = os.lstat

        def fnf_on_sub_guard_deeper(path, *a, **k):
            norm = os.fspath(path).replace("\\", "/")
            if norm.endswith("/sub/file.txt"):
                raise AssertionError("deeper lstat must not run after intermediate FNF")
            if norm.endswith("/sub"):
                raise FileNotFoundError("gone")
            return real(path, *a, **k)

        with mock.patch("cofferdam.repo_view.os.lstat",
                        side_effect=fnf_on_sub_guard_deeper), \
             mock.patch("cofferdam.repo_view.os.path.realpath",
                        side_effect=AssertionError("realpath must not run after intermediate FNF")), \
             mock.patch("builtins.open",
                        side_effect=AssertionError("open must not run after intermediate FNF")):
            self.assertEqual(self.view.path_type(("sub", "file.txt")), PathType.MISSING)
            self.assertIsNone(self.view.read_bytes(("sub", "file.txt")))


class MissingTargetTerminationTests(_Layout):
    """An intermediate ``FileNotFoundError`` terminates resolution immediately as
    the ordinary *absent* state (MISSING / None), inspecting no deeper component,
    and the product behaviors that depend on missing targets staying absent (not
    becoming containment errors) continue to work."""

    def test_existing_deeper_target_hidden_by_intermediate_fnf(self):
        # Case A: a real deeper target must NOT be reported when an intermediate
        # lstat reports the component absent — resolution terminates at the
        # missing intermediate with no deeper lstat / realpath / open.
        (self.root / "sub" / "file.txt").write_text("deep", encoding="utf-8")
        real = os.lstat

        def fnf_on_sub(path, *a, **k):
            norm = os.fspath(path).replace("\\", "/")
            if norm.endswith("/sub/file.txt"):
                raise AssertionError("deeper lstat must not run")
            if norm.endswith("/sub"):
                raise FileNotFoundError("gone")
            return real(path, *a, **k)

        with mock.patch("cofferdam.repo_view.os.lstat", side_effect=fnf_on_sub), \
             mock.patch("cofferdam.repo_view.os.path.realpath",
                        side_effect=AssertionError("realpath must not run")), \
             mock.patch("builtins.open",
                        side_effect=AssertionError("open must not run")):
            self.assertEqual(self.view.path_type(("sub", "file.txt")), PathType.MISSING)
            self.assertIsNone(self.view.read_bytes(("sub", "file.txt")))

    def test_genuinely_absent_intermediate_is_absent_prestate(self):
        # Case B: a genuinely absent intermediate → MISSING / None and an
        # "absent" pre-state, with no open reached.
        from cofferdam.prestate import read_prestate
        with mock.patch("builtins.open",
                        side_effect=AssertionError("open must not run")):
            self.assertEqual(self.view.path_type(("absent", "file.txt")), PathType.MISSING)
            self.assertIsNone(self.view.read_bytes(("absent", "file.txt")))
            self.assertEqual(read_prestate(self.view, ("absent", "file.txt")).kind, "absent")

    def test_target_appearing_after_observation_only_affects_fresh_call(self):
        # Case C: an invocation that observes the intermediate absent returns
        # MISSING / None; creating the directory and file afterward changes only
        # a later, separate invocation (each call re-observes at call time).
        self.assertEqual(self.view.path_type(("later", "file.txt")), PathType.MISSING)
        self.assertIsNone(self.view.read_bytes(("later", "file.txt")))
        (self.root / "later").mkdir()
        (self.root / "later" / "file.txt").write_text("new", encoding="utf-8")
        self.assertEqual(self.view.path_type(("later", "file.txt")), PathType.REGULAR)
        self.assertEqual(self.view.read_bytes(("later", "file.txt")), b"new")

    def test_final_component_missing_never_opens(self):
        # Case D: a missing FINAL component (all intermediates present) is
        # MISSING / None, and open is never reached after the final lstat
        # reports the target absent.
        with mock.patch("builtins.open",
                        side_effect=AssertionError("open must not run for a missing target")):
            self.assertEqual(self.view.path_type(("sub", "missing.txt")), PathType.MISSING)
            self.assertIsNone(self.view.read_bytes(("sub", "missing.txt")))

    def test_dry_run_new_file_under_missing_dir_stays_absent(self):
        # Case E: the product regression — a pre-state / dry-run for a new file
        # beneath a not-yet-existing directory must yield an absent pre-state,
        # never raising, so proposals that create newdir/newfile.txt still work.
        from cofferdam.prestate import read_prestate
        ps = read_prestate(self.view, ("newdir", "newfile.txt"))
        self.assertEqual(ps.kind, "absent")
        self.assertEqual(ps.descriptor_fields(), (b"absent",))
        # A deeper still-absent chain must likewise not raise.
        self.assertEqual(read_prestate(self.view, ("a", "b", "c.txt")).kind, "absent")


class ControlCharacterTests(_Layout):
    """Components with C0 controls or DEL are rejected before any filesystem
    access (parity with ``normalize_target``)."""

    CASES = ["\x00", "\t", "\n", "\r", "\x1b", "\x1f", "\x7f",
             "safe\x00name", "safe\nname", "safe\x7fname"]

    def test_control_chars_fail_closed_without_fs_access(self):
        for comp in self.CASES:
            with mock.patch("cofferdam.repo_view.os.lstat",
                            side_effect=AssertionError("lstat must not run")), \
                 mock.patch("cofferdam.repo_view.os.path.realpath",
                            side_effect=AssertionError("realpath must not run")), \
                 mock.patch("builtins.open",
                            side_effect=AssertionError("open must not run")):
                self.assertEqual(self.view.path_type((comp,)), PathType.MISSING, repr(comp))
                with self.assertRaises(RepoReadError) as ctx:
                    self.view.read_bytes((comp,))
                # The hostile component never appears in the error text.
                self.assertNotIn(comp, str(ctx.exception), repr(comp))

    def test_control_char_as_intermediate_component(self):
        with mock.patch("cofferdam.repo_view.os.lstat",
                        side_effect=AssertionError("lstat must not run")):
            self.assertEqual(self.view.path_type(("a\x00b", "file.txt")), PathType.MISSING)
            with self.assertRaises(RepoReadError):
                self.view.read_bytes(("a\x1bb", "file.txt"))


if __name__ == "__main__":
    unittest.main()
