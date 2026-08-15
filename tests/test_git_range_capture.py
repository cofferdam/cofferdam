"""M2K PR5 — the committed-range probe, against real Git repositories.

The record grammar here is **not** PR3's. ``git status --porcelain=v1 -z`` puts
the two status characters and the path in one NUL-terminated field and, for a
rename, emits the destination before the source. ``git diff --name-status -z``
does neither: the status is its own field, the path is the next field, and a
rename is *three* fields in source-then-destination order. Reusing PR3's parser
would silently swap every rename. That was measured against git 2.53 before this
module was written, and these tests keep it measured.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.gitrange import (
    ANCESTRY_DIVERGED,
    ANCESTRY_IDENTICAL,
    ANCESTRY_LINEAR,
    ANCESTRY_UNKNOWN,
    GIT_DIFF_NAME_STATUS,
    GIT_IS_ANCESTOR,
    MAX_CAPTURE_ATTEMPTS,
    MAX_RANGE_RECORDS,
    RANGE_ALLOWED_COMMANDS,
    RANGE_CAPTURED,
    RANGE_UNAVAILABLE,
    RANGE_COVERAGE_COMPLETE,
    RANGE_COVERAGE_INCOMPLETE,
    RANGE_COVERAGE_UNAVAILABLE,
    REASON_BASELINE_UNAVAILABLE,
    REASON_HISTORY_DIVERGED,
    REASON_MALFORMED_RECORD,
    REASON_OUTPUT_TRUNCATED,
    REASON_PROBE_FAILED,
    REASON_TARGET_UNSTABLE,
    RANGE_REASONS,
    CommittedChange,
    capture_committed_range,
    parse_name_status,
)

GIT = shutil.which("git")
GIT_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "T",
    "GIT_AUTHOR_EMAIL": "t@e.st",
    "GIT_COMMITTER_NAME": "T",
    "GIT_COMMITTER_EMAIL": "t@e.st",
}


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(root), env=dict(GIT_ENV),
        capture_output=True, check=True, text=True,
    ).stdout.strip()


class RepoCase(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.root = Path(self._d.name)
        git(self.root, "init", "-q")

    def commit(self, message="c"):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)
        return git(self.root, "rev-parse", "HEAD")

    def write(self, name, body="x\n"):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")


# -- the parser ---------------------------------------------------------------


class NameStatusGrammar(unittest.TestCase):
    """The grammar, pinned as literal bytes."""

    def test_single_field_pairs(self):
        raw = b"A\0added.txt\0M\0mod.txt\0D\0gone.txt\0"
        changes = parse_name_status(raw)
        self.assertEqual(
            [(c.kind, c.path, c.previous_path, c.status) for c in changes],
            [
                ("created", "added.txt", None, "A"),
                ("modified", "mod.txt", None, "M"),
                ("deleted", "gone.txt", None, "D"),
            ],
        )

    def test_rename_is_three_fields_source_first(self):
        """The exact inversion of `git status --porcelain -z`."""
        raw = b"R100\0old/name.txt\0new/name.txt\0"
        (change,) = parse_name_status(raw)
        self.assertEqual(change.kind, "renamed")
        self.assertEqual(change.path, "new/name.txt", "destination is the second field")
        self.assertEqual(change.previous_path, "old/name.txt", "source is the first field")
        self.assertEqual(change.status, "R100")

    def test_copy_is_three_fields_and_keeps_its_source(self):
        raw = b"C75\0from.txt\0to.txt\0"
        (change,) = parse_name_status(raw)
        self.assertEqual(change.kind, "copied")
        self.assertEqual(change.path, "to.txt")
        self.assertEqual(change.previous_path, "from.txt")

    def test_type_change(self):
        (change,) = parse_name_status(b"T\0swapped\0")
        self.assertEqual(change.kind, "type_changed")

    def test_paths_are_raw_under_z(self):
        raw = "A\0with space.txt\0M\0unicode-éè-日本語.txt\0A\0literal -> arrow.txt\0".encode()
        changes = parse_name_status(raw)
        self.assertEqual(
            [c.path for c in changes],
            ["with space.txt", "unicode-éè-日本語.txt", "literal -> arrow.txt"],
        )

    def test_a_literal_arrow_in_a_filename_is_not_a_rename_separator(self):
        (change,) = parse_name_status(b"M\0a -> b.txt\0")
        self.assertEqual(change.kind, "modified")
        self.assertEqual(change.path, "a -> b.txt")
        self.assertIsNone(change.previous_path)

    def test_empty_output_is_zero_changes_not_an_error(self):
        self.assertEqual(parse_name_status(b""), ())
        self.assertEqual(parse_name_status(b"\0"), ())

    def test_truncated_rename_record_is_refused(self):
        """A rename missing its destination must not become a one-path change."""
        with self.assertRaises(ValueError):
            parse_name_status(b"R100\0only-source.txt\0")

    def test_dangling_status_without_path_is_refused(self):
        with self.assertRaises(ValueError):
            parse_name_status(b"A\0good.txt\0M\0")

    def test_unknown_status_letter_is_refused(self):
        with self.assertRaises(ValueError):
            parse_name_status(b"Z\0mystery.txt\0")

    def test_status_letter_must_not_be_empty(self):
        with self.assertRaises(ValueError):
            parse_name_status(b"\0path.txt\0")


# -- real Git fixtures --------------------------------------------------------


@unittest.skipIf(GIT is None, "git is not installed")
class RealGitOperations(RepoCase):
    def test_every_operation_kind_against_real_git(self):
        self.write("plain.txt", "one\n")
        self.write("to-rename.txt", "six\n")
        self.write("to-delete.txt", "seven\n")
        self.write("to-typechange", "eight\n")
        base = self.commit("base")

        self.write("plain.txt", "modified\n")
        git(self.root, "mv", "to-rename.txt", "renamed dest -> here.txt")
        git(self.root, "rm", "-q", "to-delete.txt")
        self.write("added file.txt", "new\n")
        (self.root / "to-typechange").unlink()
        (self.root / "to-typechange").symlink_to("plain.txt")
        target = self.commit("work")

        result = capture_committed_range(self.root, base)
        self.assertEqual(result.capture_state, RANGE_CAPTURED)
        self.assertEqual(result.ancestry, ANCESTRY_LINEAR)
        self.assertEqual(result.baseline_revision, base)
        self.assertEqual(result.target_revision, target)
        self.assertEqual(result.coverage, RANGE_COVERAGE_COMPLETE)

        by_path = {c.path: c for c in result.changes}
        self.assertEqual(by_path["added file.txt"].kind, "created")
        self.assertEqual(by_path["plain.txt"].kind, "modified")
        self.assertEqual(by_path["to-delete.txt"].kind, "deleted")
        self.assertEqual(by_path["to-typechange"].kind, "type_changed")
        renamed = by_path["renamed dest -> here.txt"]
        self.assertEqual(renamed.kind, "renamed")
        self.assertEqual(renamed.previous_path, "to-rename.txt",
                         "rename source and destination were swapped")

    def test_unicode_and_spaces_survive_the_round_trip(self):
        self.write("keep.txt")
        base = self.commit("base")
        self.write("unicode-éè-日本語.txt", "u\n")
        self.write("with space.txt", "s\n")
        self.commit("work")
        result = capture_committed_range(self.root, base)
        self.assertEqual(
            sorted(c.path for c in result.changes),
            ["unicode-éè-日本語.txt", "with space.txt"],
        )

    def test_one_commit(self):
        self.write("a.txt")
        base = self.commit("base")
        self.write("b.txt")
        self.commit("one")
        result = capture_committed_range(self.root, base)
        self.assertEqual([c.path for c in result.changes], ["b.txt"])
        self.assertEqual(result.ancestry, ANCESTRY_LINEAR)

    def test_multiple_commits_are_one_cumulative_range(self):
        self.write("a.txt")
        base = self.commit("base")
        self.write("b.txt"); self.commit("one")
        self.write("c.txt"); self.commit("two")
        self.write("a.txt", "changed\n"); self.commit("three")
        result = capture_committed_range(self.root, base)
        self.assertEqual(
            sorted((c.kind, c.path) for c in result.changes),
            [("created", "b.txt"), ("created", "c.txt"), ("modified", "a.txt")],
        )

    def test_baseline_equals_target_is_complete_zero_changes(self):
        self.write("a.txt")
        base = self.commit("base")
        result = capture_committed_range(self.root, base)
        self.assertEqual(result.capture_state, RANGE_CAPTURED)
        self.assertEqual(result.ancestry, ANCESTRY_IDENTICAL)
        self.assertEqual(result.changes, ())
        self.assertEqual(result.coverage, RANGE_COVERAGE_COMPLETE,
                         "an identical range is complete, not unavailable")
        self.assertIsNone(result.reason)

    def test_uncommitted_work_is_not_in_the_committed_range(self):
        """The domains are separate: PR5 reads commits, never the worktree."""
        self.write("a.txt")
        base = self.commit("base")
        self.write("never-committed.txt", "dirty\n")
        result = capture_committed_range(self.root, base)
        self.assertEqual(result.changes, ())
        self.assertEqual(result.ancestry, ANCESTRY_IDENTICAL)


@unittest.skipIf(GIT is None, "git is not installed")
class RenameSemanticsAreNotConfigurable(RepoCase):
    def test_repository_config_cannot_turn_a_rename_into_add_plus_delete(self):
        """`diff.renames=false` in the repo must not change what we observe."""
        self.write("a.txt", "content\n")
        base = self.commit("base")
        git(self.root, "config", "diff.renames", "false")
        git(self.root, "mv", "a.txt", "b.txt")
        self.commit("move")

        result = capture_committed_range(self.root, base)
        (change,) = result.changes
        self.assertEqual(change.kind, "renamed",
                         "repository config changed the observed operation")
        self.assertEqual(change.path, "b.txt")
        self.assertEqual(change.previous_path, "a.txt")

    def test_external_diff_config_is_not_consulted(self):
        self.write("a.txt", "one\n")
        base = self.commit("base")
        git(self.root, "config", "diff.external", "/bin/false")
        self.write("a.txt", "two\n")
        self.commit("change")
        result = capture_committed_range(self.root, base)
        self.assertEqual(result.capture_state, RANGE_CAPTURED)
        self.assertEqual([c.kind for c in result.changes], ["modified"])


# -- history relationship -----------------------------------------------------


@unittest.skipIf(GIT is None, "git is not installed")
class HistoryRelationship(RepoCase):
    def test_branch_switch_is_diverged_not_a_silent_tree_diff(self):
        self.write("a.txt")
        root_commit = self.commit("base")
        self.write("b.txt")
        baseline = self.commit("second")          # baseline is on main
        git(self.root, "checkout", "-q", "-b", "side", root_commit)
        self.write("side.txt")
        self.commit("side")                        # target diverges

        result = capture_committed_range(self.root, baseline)
        self.assertEqual(result.ancestry, ANCESTRY_DIVERGED)
        self.assertEqual(result.capture_state, RANGE_UNAVAILABLE)
        self.assertEqual(result.coverage, RANGE_COVERAGE_UNAVAILABLE)
        self.assertEqual(result.reason, REASON_HISTORY_DIVERGED)
        self.assertEqual(result.changes, (),
                         "a diverged history produced tree-diff changes labelled "
                         "as committed-since-baseline")

    def test_hard_reset_backwards_is_diverged(self):
        self.write("a.txt")
        root_commit = self.commit("base")
        self.write("b.txt")
        baseline = self.commit("second")
        git(self.root, "reset", "-q", "--hard", root_commit)

        result = capture_committed_range(self.root, baseline)
        self.assertEqual(result.ancestry, ANCESTRY_DIVERGED)
        self.assertEqual(result.reason, REASON_HISTORY_DIVERGED)
        self.assertEqual(result.changes, ())

    def test_rebase_rewrites_history_and_is_diverged(self):
        self.write("a.txt")
        root_commit = self.commit("base")
        trunk = git(self.root, "rev-parse", "--abbrev-ref", "HEAD")
        git(self.root, "checkout", "-q", "-b", "topic")
        self.write("t.txt")
        baseline = self.commit("topic work")   # the baseline Cofferdam recorded
        git(self.root, "checkout", "-q", trunk)
        self.write("m.txt")
        moved_trunk = self.commit("main work")
        git(self.root, "checkout", "-q", "topic")
        # Replays the topic commit onto the moved trunk, which gives it a new
        # parent and therefore a new object id. The baseline id still exists in
        # the reflog but is no longer an ancestor of anything HEAD can reach.
        git(self.root, "rebase", "-q", "--onto", moved_trunk, root_commit, "topic")
        rebased = git(self.root, "rev-parse", "HEAD")
        self.assertNotEqual(rebased, baseline, "the rebase did not rewrite history")

        result = capture_committed_range(self.root, baseline)
        self.assertEqual(result.ancestry, ANCESTRY_DIVERGED)
        self.assertEqual(result.changes, ())

    def test_missing_baseline_object_is_unavailable_not_diverged(self):
        """Exit 128 is a different fact from exit 1 and must not be merged."""
        self.write("a.txt")
        self.commit("base")
        result = capture_committed_range(self.root, "0" * 40)
        self.assertEqual(result.capture_state, RANGE_UNAVAILABLE)
        self.assertEqual(result.ancestry, ANCESTRY_UNKNOWN)
        self.assertEqual(result.reason, REASON_BASELINE_UNAVAILABLE)
        self.assertNotEqual(result.reason, REASON_HISTORY_DIVERGED)

    def test_a_non_repository_is_unavailable(self):
        plain = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, plain, True)
        result = capture_committed_range(plain, "0" * 40)
        self.assertEqual(result.capture_state, RANGE_UNAVAILABLE)
        self.assertIn(result.reason, RANGE_REASONS)


# -- target stability ---------------------------------------------------------


class TargetStability(unittest.TestCase):
    A = "a" * 40
    B = "b" * 40

    def _scripted(self, heads):
        seq = list(heads)
        calls = []

        def runner(cmd, root):
            calls.append(tuple(cmd))
            t = tuple(cmd)
            if t[:3] == ("git", "rev-parse", "--is-inside-work-tree"):
                return 0, b"true\n"
            if t[:3] == ("git", "rev-parse", "--show-object-format"):
                return 0, b"sha1\n"
            if t[:2] == ("git", "rev-parse"):
                return 0, (seq.pop(0) + "\n").encode()
            if t[:2] == ("git", "merge-base"):
                return 0, b""
            if t[:2] == ("git", "diff"):
                return 0, b""
            raise AssertionError(f"unexpected {t}")
        return runner, calls

    def test_stable_target_captures(self):
        runner, _ = self._scripted([self.A, self.A])
        result = capture_committed_range(Path("/"), self.A, runner=runner)
        self.assertEqual(result.target_revision, self.A)
        self.assertEqual(result.capture_state, RANGE_CAPTURED)

    def test_settling_on_a_later_attempt_succeeds(self):
        runner, _ = self._scripted([self.A, self.B, self.B, self.B])
        result = capture_committed_range(Path("/"), self.A, runner=runner)
        self.assertEqual(result.target_revision, self.B)

    def test_continuously_moving_target_exhausts_the_bound(self):
        moving = [f"{i:040x}" for i in range(2 * MAX_CAPTURE_ATTEMPTS + 4)]
        runner, _ = self._scripted(moving)
        result = capture_committed_range(Path("/"), self.A, runner=runner)
        self.assertEqual(result.capture_state, RANGE_UNAVAILABLE)
        self.assertEqual(result.reason, REASON_TARGET_UNSTABLE)
        self.assertIsNone(result.target_revision,
                          "an arbitrary A/B target revision was persisted")
        self.assertEqual(result.coverage, RANGE_COVERAGE_UNAVAILABLE)

    def test_the_bound_matches_pr4(self):
        self.assertEqual(MAX_CAPTURE_ATTEMPTS, 3)


# -- bounds and completeness --------------------------------------------------


@unittest.skipIf(GIT is None, "git is not installed")
class Completeness(RepoCase):
    def test_under_the_cap_is_complete(self):
        self.write("a.txt")
        base = self.commit("base")
        for i in range(5):
            self.write(f"f{i}.txt")
        self.commit("work")
        result = capture_committed_range(self.root, base)
        self.assertEqual(len(result.changes), 5)
        self.assertEqual(result.coverage, RANGE_COVERAGE_COMPLETE)

    def test_exactly_at_the_cap_is_complete(self):
        self.write("a.txt")
        base = self.commit("base")
        for i in range(MAX_RANGE_RECORDS):
            self.write(f"f{i:05d}.txt")
        self.commit("work")
        result = capture_committed_range(self.root, base)
        self.assertEqual(len(result.changes), MAX_RANGE_RECORDS)
        self.assertEqual(result.coverage, RANGE_COVERAGE_COMPLETE)

    def test_over_the_cap_is_incomplete_and_still_captured(self):
        self.write("a.txt")
        base = self.commit("base")
        for i in range(MAX_RANGE_RECORDS + 20):
            self.write(f"f{i:05d}.txt")
        self.commit("work")
        result = capture_committed_range(self.root, base)
        self.assertEqual(result.capture_state, RANGE_CAPTURED,
                         "an over-cap range stopped being an observation")
        self.assertEqual(result.coverage, RANGE_COVERAGE_INCOMPLETE)
        self.assertEqual(result.reason, REASON_OUTPUT_TRUNCATED)
        self.assertEqual(len(result.changes), MAX_RANGE_RECORDS)

    def test_a_malformed_record_is_incomplete_never_silently_dropped(self):
        def broken(cmd, root):
            t = tuple(cmd)
            if t[:3] == ("git", "rev-parse", "--is-inside-work-tree"):
                return 0, b"true\n"
            if t[:3] == ("git", "rev-parse", "--show-object-format"):
                return 0, b"sha1\n"
            if t[:2] == ("git", "rev-parse"):
                return 0, (b"c" * 40) + b"\n"
            if t[:2] == ("git", "merge-base"):
                return 0, b""
            if t[:2] == ("git", "diff"):
                return 0, b"R100\0dangling-source-only.txt\0"
            raise AssertionError(t)
        result = capture_committed_range(Path("/"), "a" * 40, runner=broken)
        self.assertEqual(result.capture_state, RANGE_UNAVAILABLE)
        self.assertEqual(result.reason, REASON_MALFORMED_RECORD)
        self.assertEqual(result.coverage, RANGE_COVERAGE_UNAVAILABLE)

    def test_no_changes_is_never_presented_as_complete_when_unavailable(self):
        result = capture_committed_range(Path("/nonexistent-root-xyz"), "a" * 40)
        self.assertEqual(result.changes, ())
        self.assertNotEqual(result.coverage, RANGE_COVERAGE_COMPLETE,
                            "an unavailable capture claimed complete zero changes")


# -- authority and side effects ----------------------------------------------


class Authority(unittest.TestCase):
    def test_the_command_set_is_closed(self):
        self.assertIn(GIT_DIFF_NAME_STATUS, RANGE_ALLOWED_COMMANDS)
        self.assertIn(GIT_IS_ANCESTOR, RANGE_ALLOWED_COMMANDS)
        for cmd in RANGE_ALLOWED_COMMANDS:
            self.assertIsInstance(cmd, tuple)
            self.assertEqual(cmd[0], "git")

    def test_the_diff_argv_pins_every_behaviour_that_config_could_change(self):
        argv = " ".join(GIT_DIFF_NAME_STATUS)
        self.assertIn("--name-status", argv)
        self.assertIn("-z", argv)
        self.assertIn("--find-renames", argv)
        self.assertIn("--no-ext-diff", argv)
        self.assertIn("--no-textconv", argv)

    def test_no_revision_is_interpolated_into_the_constant_argv(self):
        for cmd in RANGE_ALLOWED_COMMANDS:
            for arg in cmd:
                self.assertNotIn("{", arg)
                self.assertNotIn("%", arg)

    def test_a_revspec_is_refused_as_a_revision_argument(self):
        for bad in ("HEAD", "HEAD~5", "main", "@{upstream}", "v1.0^{commit}",
                    "--output=/tmp/x", "-M", "", "a" * 39, "g" * 40,
                    "a" * 40 + "\n", "../etc/passwd"):
            with self.subTest(bad=bad):
                result = capture_committed_range(Path("/"), bad)
                self.assertEqual(result.capture_state, RANGE_UNAVAILABLE)
                self.assertEqual(result.reason, REASON_BASELINE_UNAVAILABLE)

    def test_reasons_are_bounded_and_leak_nothing(self):
        for reason in RANGE_REASONS:
            self.assertLessEqual(len(reason), 40)
            self.assertNotIn("/", reason)
            self.assertRegex(reason, r"^[a-z_]+$")

    def test_capture_never_raises(self):
        def explode(cmd, root):
            raise OSError("boom /home/somebody/secret")
        result = capture_committed_range(Path("/"), "a" * 40, runner=explode)
        self.assertEqual(result.capture_state, RANGE_UNAVAILABLE)
        self.assertEqual(result.reason, REASON_PROBE_FAILED)


@unittest.skipIf(GIT is None, "git is not installed")
class NoSideEffects(RepoCase):
    def test_the_index_is_untouched_by_repeated_range_capture(self):
        self.write("a.txt", "one\n")
        base = self.commit("base")
        self.write("a.txt", "two\n")
        self.commit("work")
        os.utime(self.root / "a.txt", (0, 0))

        index = self.root / ".git" / "index"
        before = (index.read_bytes(), index.stat().st_mtime_ns, index.stat().st_ino)
        for _ in range(5):
            capture_committed_range(self.root, base)
        after = (index.read_bytes(), index.stat().st_mtime_ns, index.stat().st_ino)
        self.assertEqual(before, after, ".git/index changed")
        self.assertFalse((self.root / ".git" / "index.lock").exists())

    def test_no_file_content_is_read(self):
        secret = "SUPER-SECRET-RANGE-BODY"
        self.write("a.txt", "one\n")
        base = self.commit("base")
        self.write("confidential.txt", secret + "\n")
        self.commit("work")
        result = capture_committed_range(self.root, base)
        blob = repr(result)
        self.assertNotIn(secret, blob, "file content entered the observation")
        self.assertNotIn(str(self.root), blob, "the absolute root entered the observation")


if __name__ == "__main__":
    unittest.main()
