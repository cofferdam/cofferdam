"""M2K PR4 — what the host reads, and what it refuses to invent.

Real repositories where a real repository can express the case, and a scripted
runner where it cannot. A HEAD that moves *between two probes of the same
capture* is the obvious example: arranging that against real Git reliably would
mean racing a subprocess, and a test that sometimes proves the thing is a test
that sometimes proves nothing.

Every case here ends in a closed machine state. There is no path in this module
that produces an exception a caller has to interpret, and none that produces a
revision Cofferdam made up.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.gitbaseline import (
    ALLOWED_COMMANDS,
    CAPTURE_CAPTURED,
    CAPTURE_UNAVAILABLE,
    COVERAGE_COMPLETE,
    COVERAGE_INCOMPLETE,
    COVERAGE_UNAVAILABLE,
    GIT_HEAD,
    GIT_IS_REPO,
    GIT_OBJECT_FORMAT,
    GIT_STATUS,
    HEAD_NOT_A_REPOSITORY,
    HEAD_PRESENT,
    HEAD_UNAVAILABLE,
    HEAD_UNBORN,
    MAX_CAPTURE_ATTEMPTS,
    MAX_REVISION_CHARS,
    MAX_STATUS_RECORDS,
    OBJECT_FORMAT_LENGTHS,
    PROBE_ENVIRONMENT,
    REASON_HEAD_UNSTABLE,
    REASON_NOT_A_REPOSITORY,
    REASON_PROBE_FAILED,
    REASON_PROBE_TIMEOUT,
    REASON_ROOT_UNAVAILABLE,
    REASON_UNBORN_HEAD,
    REASONS,
    WORKTREE_CLEAN,
    WORKTREE_DIRTY,
    WORKTREE_UNKNOWN,
    GitBaseline,
    capture_baseline,
)

GIT = shutil.which("git")
ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "T",
    "GIT_AUTHOR_EMAIL": "t@e.st",
    "GIT_COMMITTER_NAME": "T",
    "GIT_COMMITTER_EMAIL": "t@e.st",
}


class _RepoCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr4-cap-")
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "proj"
        self.root.mkdir(parents=True)

    def git(self, *args):
        subprocess.run(
            [GIT, *args], cwd=str(self.root), check=True, capture_output=True,
            env={**ENV, "HOME": str(self.root)},
        )

    def write(self, relative, body="x\n"):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def head(self):
        completed = subprocess.run(
            [GIT, "rev-parse", "HEAD"], cwd=str(self.root), check=True,
            capture_output=True, env={**ENV, "HOME": str(self.root)},
        )
        return completed.stdout.decode().strip()

    def init_with_commit(self):
        self.git("init", "-q", ".")
        self.write("seed.txt", "one\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")


@unittest.skipIf(GIT is None, "git is not installed")
class TheCommandsAreLiteral(unittest.TestCase):
    def test_every_probe_is_a_constant_tuple(self):
        for command in ALLOWED_COMMANDS:
            self.assertIsInstance(command, tuple)
            for word in command:
                self.assertIsInstance(word, str)

    def test_the_status_probe_is_the_machine_format(self):
        self.assertEqual(
            GIT_STATUS,
            ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"),
        )

    def test_the_head_probe_resolves_rather_than_prints(self):
        """`--verify --quiet` makes an unborn HEAD an exit code, not a string to match."""
        self.assertEqual(GIT_HEAD, ("git", "rev-parse", "--verify", "--quiet", "HEAD"))

    def test_no_probe_takes_a_revision_argument(self):
        """Nothing here can be pointed at a caller's revision."""
        for command in ALLOWED_COMMANDS:
            for word in command:
                self.assertNotIn("~", word)
                self.assertNotIn("^", word)
                self.assertNotIn("@{", word)
                self.assertNotIn("..", word)

    def test_the_environment_is_closed_and_protects_the_index(self):
        self.assertEqual(PROBE_ENVIRONMENT["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(PROBE_ENVIRONMENT["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(PROBE_ENVIRONMENT["LC_ALL"], "C")
        self.assertEqual(
            set(PROBE_ENVIRONMENT),
            {"GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT", "LC_ALL", "PATH"},
        )

    def test_the_environment_carries_no_git_redirection(self):
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
            self.assertNotIn(name, PROBE_ENVIRONMENT)


@unittest.skipIf(GIT is None, "git is not installed")
class CleanRepository(_RepoCase):
    def test_a_clean_repository_is_captured_exactly(self):
        self.init_with_commit()
        baseline = capture_baseline(self.root)
        self.assertEqual(baseline.capture_state, CAPTURE_CAPTURED)
        self.assertEqual(baseline.head_state, HEAD_PRESENT)
        self.assertEqual(baseline.head_revision, self.head())
        self.assertEqual(baseline.working_tree_state, WORKTREE_CLEAN)
        self.assertEqual(baseline.status_coverage, COVERAGE_COMPLETE)
        self.assertIsNone(baseline.reason)
        self.assertFalse(baseline.preexisting_dirty)

    def test_the_revision_is_a_resolved_object_id(self):
        self.init_with_commit()
        baseline = capture_baseline(self.root)
        revision = baseline.head_revision
        self.assertEqual(len(revision), OBJECT_FORMAT_LENGTHS[baseline.object_format])
        self.assertTrue(all(c in "0123456789abcdef" for c in revision))
        self.assertLessEqual(len(revision), MAX_REVISION_CHARS)

    def test_the_object_format_is_read_from_the_repository(self):
        """Git 2.29 shipped SHA-256. "Forty hex characters" is no longer a rule."""
        self.init_with_commit()
        self.assertEqual(capture_baseline(self.root).object_format, "sha1")

    def test_a_sha256_repository_is_captured_with_its_own_id_length(self):
        self.git("init", "-q", "--object-format=sha256", ".")
        self.write("seed.txt", "one\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        baseline = capture_baseline(self.root)
        self.assertEqual(baseline.head_state, HEAD_PRESENT)
        self.assertEqual(baseline.object_format, "sha256")
        self.assertEqual(len(baseline.head_revision), 64)
        self.assertEqual(baseline.head_revision, self.head())


@unittest.skipIf(GIT is None, "git is not installed")
class DirtyRepository(_RepoCase):
    def test_a_tracked_modification_is_dirty(self):
        self.init_with_commit()
        self.write("seed.txt", "one\nedited\n")
        baseline = capture_baseline(self.root)
        self.assertEqual(baseline.working_tree_state, WORKTREE_DIRTY)
        self.assertEqual(baseline.status_coverage, COVERAGE_COMPLETE)
        self.assertTrue(baseline.preexisting_dirty)

    def test_an_untracked_file_is_dirty(self):
        self.init_with_commit()
        self.write("brand_new.txt", "n\n")
        self.assertEqual(
            capture_baseline(self.root).working_tree_state, WORKTREE_DIRTY
        )

    def test_a_staged_file_is_dirty(self):
        self.init_with_commit()
        self.write("staged.txt", "s\n")
        self.git("add", "staged.txt")
        self.assertEqual(
            capture_baseline(self.root).working_tree_state, WORKTREE_DIRTY
        )

    def test_a_composite_status_is_dirty(self):
        """Added then modified — the AM case PR3 pinned."""
        self.init_with_commit()
        self.write("am.txt", "a\n")
        self.git("add", "am.txt")
        self.write("am.txt", "a\nmore\n")
        self.assertEqual(
            capture_baseline(self.root).working_tree_state, WORKTREE_DIRTY
        )

    def test_a_deletion_is_dirty(self):
        self.init_with_commit()
        self.git("rm", "-q", "seed.txt")
        self.assertEqual(
            capture_baseline(self.root).working_tree_state, WORKTREE_DIRTY
        )

    def test_the_baseline_stores_no_path_and_no_content(self):
        """A boundary is a revision. Project content has no business in this row."""
        self.init_with_commit()
        self.write("secret_looking_name.txt", "SUPER-SECRET-BODY-TEXT\n")
        baseline = capture_baseline(self.root)
        rendered = repr(baseline)
        self.assertNotIn("secret_looking_name", rendered)
        self.assertNotIn("SUPER-SECRET-BODY-TEXT", rendered)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("/", rendered.replace("//", ""))

    def test_an_over_cap_dirty_tree_stays_dirty_and_says_it_is_incomplete(self):
        self.init_with_commit()
        for index in range(MAX_STATUS_RECORDS + 40):
            self.write("over_%04d.txt" % index, "%d\n" % index)
        baseline = capture_baseline(self.root)
        self.assertEqual(baseline.working_tree_state, WORKTREE_DIRTY)
        self.assertEqual(baseline.status_coverage, COVERAGE_INCOMPLETE)
        self.assertTrue(baseline.preexisting_dirty)

    def test_clean_can_never_rest_on_an_incomplete_status(self):
        """The one dishonest combination, refused by the value as well as the schema."""
        with self.assertRaises(ValueError):
            GitBaseline(
                capture_state=CAPTURE_CAPTURED,
                head_state=HEAD_PRESENT,
                head_revision="a" * 40,
                object_format="sha1",
                working_tree_state=WORKTREE_CLEAN,
                status_coverage=COVERAGE_INCOMPLETE,
            )


@unittest.skipIf(GIT is None, "git is not installed")
class UnbornAndNonGit(_RepoCase):
    def test_a_repository_with_no_commit_is_unborn(self):
        self.git("init", "-q", ".")
        baseline = capture_baseline(self.root)
        self.assertEqual(baseline.capture_state, CAPTURE_CAPTURED)
        self.assertEqual(baseline.head_state, HEAD_UNBORN)
        self.assertIsNone(baseline.head_revision)
        self.assertEqual(baseline.reason, REASON_UNBORN_HEAD)

    def test_an_unborn_head_is_never_given_an_invented_revision(self):
        """Not the empty-tree object, not a zero id, not anything."""
        self.git("init", "-q", ".")
        baseline = capture_baseline(self.root)
        self.assertIsNone(baseline.head_revision)
        self.assertIsNone(baseline.object_format)
        self.assertNotIn("4b825dc", repr(baseline))

    def test_an_unborn_repository_still_reports_its_dirt(self):
        self.git("init", "-q", ".")
        self.write("untracked.txt", "u\n")
        baseline = capture_baseline(self.root)
        self.assertEqual(baseline.head_state, HEAD_UNBORN)
        self.assertEqual(baseline.working_tree_state, WORKTREE_DIRTY)

    def test_a_directory_that_is_not_a_repository_says_so(self):
        baseline = capture_baseline(self.root)
        self.assertEqual(baseline.capture_state, CAPTURE_UNAVAILABLE)
        self.assertEqual(baseline.head_state, HEAD_NOT_A_REPOSITORY)
        self.assertEqual(baseline.reason, REASON_NOT_A_REPOSITORY)
        self.assertIsNone(baseline.head_revision)
        self.assertEqual(baseline.working_tree_state, WORKTREE_UNKNOWN)

    def test_a_root_that_is_not_a_path_is_unavailable(self):
        baseline = capture_baseline("/not/a/path/object")
        self.assertEqual(baseline.capture_state, CAPTURE_UNAVAILABLE)
        self.assertEqual(baseline.reason, REASON_ROOT_UNAVAILABLE)

    def test_a_repository_deleted_during_capture_is_unavailable(self):
        self.init_with_commit()
        shutil.rmtree(self.root)
        baseline = capture_baseline(self.root)
        self.assertEqual(baseline.capture_state, CAPTURE_UNAVAILABLE)
        self.assertIn(baseline.reason, REASONS)
        self.assertIsNone(baseline.head_revision)


class ScriptedFailures(unittest.TestCase):
    """Cases a real repository cannot be made to produce reliably."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr4-script-")
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def _runner(self, script):
        calls = []

        def runner(command, root):
            calls.append(command)
            return script(command, len([c for c in calls if c == command]))

        runner.calls = calls
        return runner

    def test_a_head_that_moves_across_the_observation_is_never_trusted(self):
        """Neither read describes the moment, so neither is stored."""
        first = "a" * 40
        second = "b" * 40
        state = {"n": 0}

        def script(command, _count):
            if command == GIT_IS_REPO:
                return 0, b"true\n"
            if command == GIT_OBJECT_FORMAT:
                return 0, b"sha1\n"
            if command == GIT_STATUS:
                return 0, b""
            state["n"] += 1
            return 0, (first if state["n"] % 2 else second).encode() + b"\n"

        runner = self._runner(script)
        baseline = capture_baseline(self.root, runner=runner)
        self.assertEqual(baseline.capture_state, CAPTURE_UNAVAILABLE)
        self.assertEqual(baseline.head_state, HEAD_UNAVAILABLE)
        self.assertEqual(baseline.reason, REASON_HEAD_UNSTABLE)
        self.assertIsNone(baseline.head_revision)
        self.assertNotIn(first, repr(baseline))
        self.assertNotIn(second, repr(baseline))

    def test_the_retry_is_bounded(self):
        state = {"n": 0}

        def script(command, _count):
            if command == GIT_IS_REPO:
                return 0, b"true\n"
            if command == GIT_OBJECT_FORMAT:
                return 0, b"sha1\n"
            if command == GIT_STATUS:
                return 0, b""
            state["n"] += 1
            return 0, ("%040x" % state["n"]).encode() + b"\n"

        runner = self._runner(script)
        capture_baseline(self.root, runner=runner)
        head_reads = [c for c in runner.calls if c == GIT_HEAD]
        self.assertEqual(len(head_reads), MAX_CAPTURE_ATTEMPTS * 2)

    def test_a_head_that_settles_is_captured_on_a_later_attempt(self):
        stable = "c" * 40
        state = {"n": 0}

        def script(command, _count):
            if command == GIT_IS_REPO:
                return 0, b"true\n"
            if command == GIT_OBJECT_FORMAT:
                return 0, b"sha1\n"
            if command == GIT_STATUS:
                return 0, b""
            state["n"] += 1
            if state["n"] <= 2:  # the first attempt disagrees with itself
                return 0, ("%040x" % state["n"]).encode() + b"\n"
            return 0, stable.encode() + b"\n"

        baseline = capture_baseline(self.root, runner=self._runner(script))
        self.assertEqual(baseline.capture_state, CAPTURE_CAPTURED)
        self.assertEqual(baseline.head_revision, stable)

    def test_a_timeout_is_a_closed_reason(self):
        def script(command, _count):
            raise subprocess.TimeoutExpired(cmd=list(command), timeout=15.0)

        baseline = capture_baseline(self.root, runner=self._runner(script))
        self.assertEqual(baseline.capture_state, CAPTURE_UNAVAILABLE)
        self.assertEqual(baseline.reason, REASON_PROBE_TIMEOUT)

    def test_an_os_error_is_a_closed_reason(self):
        def script(command, _count):
            raise OSError("something the operating system said, with /a/host/path in it")

        baseline = capture_baseline(self.root, runner=self._runner(script))
        self.assertEqual(baseline.capture_state, CAPTURE_UNAVAILABLE)
        self.assertEqual(baseline.reason, REASON_PROBE_FAILED)
        self.assertNotIn("/a/host/path", repr(baseline))

    def test_a_malformed_revision_is_refused_rather_than_stored(self):
        def script(command, _count):
            if command == GIT_IS_REPO:
                return 0, b"true\n"
            if command == GIT_OBJECT_FORMAT:
                return 0, b"sha1\n"
            if command == GIT_STATUS:
                return 0, b""
            return 0, b"HEAD~5\n"

        baseline = capture_baseline(self.root, runner=self._runner(script))
        self.assertEqual(baseline.capture_state, CAPTURE_UNAVAILABLE)
        self.assertIsNone(baseline.head_revision)
        self.assertNotIn("HEAD~5", repr(baseline))

    def test_an_unknown_object_format_is_refused(self):
        def script(command, _count):
            if command == GIT_IS_REPO:
                return 0, b"true\n"
            if command == GIT_OBJECT_FORMAT:
                return 0, b"sha512-someday\n"
            return 0, b""

        baseline = capture_baseline(self.root, runner=self._runner(script))
        self.assertEqual(baseline.capture_state, CAPTURE_UNAVAILABLE)
        self.assertIsNone(baseline.head_revision)

    def test_a_status_failure_leaves_the_tree_unknown_not_clean(self):
        def script(command, _count):
            if command == GIT_IS_REPO:
                return 0, b"true\n"
            if command == GIT_OBJECT_FORMAT:
                return 0, b"sha1\n"
            if command == GIT_STATUS:
                return 128, b""
            return 0, (b"d" * 40) + b"\n"

        baseline = capture_baseline(self.root, runner=self._runner(script))
        self.assertEqual(baseline.working_tree_state, WORKTREE_UNKNOWN)
        self.assertEqual(baseline.status_coverage, COVERAGE_UNAVAILABLE)
        self.assertNotEqual(baseline.working_tree_state, WORKTREE_CLEAN)

    def test_capture_never_raises(self):
        def script(command, _count):
            raise RuntimeError("an adapter-shaped disaster")

        with self.assertRaises(RuntimeError):
            # Deliberately outside the caught set, to show the boundary of the
            # promise: `capture_baseline` catches OS, value and subprocess
            # errors. The service wraps the rest — see
            # `TaskService._record_pre_work_baseline`.
            capture_baseline(self.root, runner=self._runner(script))


@unittest.skipIf(GIT is None, "git is not installed")
class NoSideEffects(_RepoCase):
    """Observation, not mutation. The index in particular."""

    def _index_state(self):
        path = self.root / ".git" / "index"
        stat = path.stat()
        return (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            stat.st_size,
            stat.st_mtime_ns,
        )

    def _tree_state(self):
        out = []
        for dirpath, _dirs, files in os.walk(self.root):
            if ".git" in Path(dirpath).parts:
                continue
            for name in sorted(files):
                target = Path(dirpath) / name
                out.append(
                    (
                        str(target.relative_to(self.root)),
                        hashlib.sha256(target.read_bytes()).hexdigest(),
                    )
                )
        return sorted(out)

    def test_repeated_capture_does_not_touch_the_index(self):
        self.init_with_commit()
        self.write("seed.txt", "one\nedited\n")
        self.write("untracked.txt", "u\n")
        before_index = self._index_state()
        before_tree = self._tree_state()
        for _ in range(5):
            capture_baseline(self.root)
        self.assertEqual(self._index_state(), before_index)
        self.assertEqual(self._tree_state(), before_tree)

    def test_no_index_lock_is_left_behind(self):
        self.init_with_commit()
        for _ in range(3):
            capture_baseline(self.root)
        self.assertFalse((self.root / ".git" / "index.lock").exists())

    def test_capture_writes_nothing_into_the_working_tree(self):
        self.init_with_commit()
        before = sorted(p.name for p in self.root.iterdir())
        capture_baseline(self.root)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)


class TheValueRefusesNonsense(unittest.TestCase):
    def test_only_a_present_head_may_carry_a_revision(self):
        for state in (HEAD_UNBORN, HEAD_UNAVAILABLE, HEAD_NOT_A_REPOSITORY):
            with self.assertRaises(ValueError, msg=state):
                GitBaseline(
                    capture_state=CAPTURE_UNAVAILABLE,
                    head_state=state,
                    head_revision="a" * 40,
                    object_format="sha1",
                )

    def test_a_present_head_must_carry_one(self):
        with self.assertRaises(ValueError):
            GitBaseline(capture_state=CAPTURE_CAPTURED, head_state=HEAD_PRESENT)

    def test_a_revspec_is_not_a_revision(self):
        for bad in ("HEAD", "HEAD~5", "main", "@{upstream}", "v1.0^{commit}",
                    "../etc/passwd", "a" * 39, "a" * 41, "A" * 40, "g" * 40,
                    "a" * 39 + " ", "a" * 39 + "\n"):
            with self.assertRaises(ValueError, msg=bad):
                GitBaseline(
                    capture_state=CAPTURE_CAPTURED,
                    head_state=HEAD_PRESENT,
                    head_revision=bad,
                    object_format="sha1",
                )

    def test_the_vocabularies_are_closed(self):
        with self.assertRaises(ValueError):
            GitBaseline(capture_state="probably", head_state=HEAD_UNBORN)
        with self.assertRaises(ValueError):
            GitBaseline(capture_state=CAPTURE_CAPTURED, head_state="sortof")
        with self.assertRaises(ValueError):
            GitBaseline(
                capture_state=CAPTURE_CAPTURED,
                head_state=HEAD_UNBORN,
                working_tree_state="messy",
            )
        with self.assertRaises(ValueError):
            GitBaseline(
                capture_state=CAPTURE_CAPTURED,
                head_state=HEAD_UNBORN,
                status_coverage="partial",
            )

    def test_the_reason_vocabulary_is_closed(self):
        with self.assertRaises(ValueError):
            GitBaseline(
                capture_state=CAPTURE_UNAVAILABLE,
                head_state=HEAD_UNAVAILABLE,
                reason="FileNotFoundError: /home/somebody/project/.git",
            )

    def test_a_baseline_is_frozen(self):
        baseline = GitBaseline(
            capture_state=CAPTURE_UNAVAILABLE, head_state=HEAD_UNAVAILABLE
        )
        with self.assertRaises(Exception):
            baseline.head_revision = "a" * 40


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
