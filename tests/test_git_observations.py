"""M2K PR3 — structured machine-owned Git observations, against real Git.

These fixtures run **actual Git**. That is deliberate and it is the point of the
file: the format this parser depends on is Git's, not a format anybody here
invented, and a hand-written fixture proves only that the parser agrees with
whoever wrote the fixture. The one thing that has to be true is that the parser
agrees with `git`.

The finding that made this file necessary
-----------------------------------------

`git status --porcelain` and `git status --porcelain=v1 -z` **order rename paths
differently**, and the machine form is the reverse of the human one:

    human : R  tomove.txt -> moved.txt      old first, then new
    -z    : "R  moved.txt" NUL "tomove.txt"  NEW first, then old

Read the human output, write the obvious parser, and every rename comes out
backwards — silently, with both paths looking plausible. ``test_the_z_format_puts_
the_destination_first`` pins that against the installed Git so the assumption can
never quietly drift.

The second finding is that the deployed parser drops far more than it keeps: with
human porcelain, Git **quotes** any path containing a space, a tab, an arrow or a
non-ASCII byte, and `_safe_relative` refuses anything starting with a quote. So
today a file called ``has space.txt`` produces no evidence at all. Under ``-z``
Git emits those paths raw, and they survive.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.adapters.claude_code.evidence import (
    CHANGE_CREATED,
    CHANGE_DELETED,
    CHANGE_MODIFIED,
    CHANGE_RENAMED,
    CHANGE_UNKNOWN,
    GIT_STATUS,
    GitChange,
    classify_status,
    observe_git,
    parse_status_z,
)

GIT = shutil.which("git")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        [GIT, *args],
        cwd=str(root),
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(root),
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@e.st",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@e.st",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        },
    )


@unittest.skipIf(GIT is None, "git is not installed")
class RealRepositoryFixture(unittest.TestCase):
    """One real repository per test, with a real commit behind it."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr3-git-")
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "repo"
        self.root.mkdir()
        _git(self.root, "init", "-q", ".")
        self.write("tracked.txt", "one\n")
        self.write("tomove.txt", "two\n")
        self.write("todelete.txt", "three\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "base")

    def write(self, relative: str, body: str = "x\n") -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def raw_z(self) -> bytes:
        return subprocess.run(
            [GIT, *GIT_STATUS[1:]],
            cwd=str(self.root),
            check=True,
            capture_output=True,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C"},
        ).stdout

    def changes(self):
        return {c.path: c for c in observe_git(self.root).changes}


class TheCommandIsMachineReadable(RealRepositoryFixture):
    def test_the_probe_asks_for_the_nul_terminated_machine_format(self):
        """Not human output. `-z` is what makes framing unambiguous."""
        self.assertEqual(GIT_STATUS, ("git", "status", "--porcelain=v1", "-z"))

    def test_the_z_format_emits_raw_unquoted_paths(self):
        """The human form quotes these; `-z` does not, so they survive."""
        self.write("has space.txt")
        self.write("arrow -> name.txt")
        raw = self.raw_z()
        self.assertIn(b"has space.txt", raw)
        self.assertIn(b"arrow -> name.txt", raw)
        self.assertNotIn(b'"', raw)

    def test_the_z_format_puts_the_destination_first(self):
        """The load-bearing assumption, pinned against the installed Git.

        Human porcelain reads ``R  old -> new``. The machine form reverses it:
        the record holds the **new** path and the following NUL field holds the
        original. A parser written from the human output inverts every rename.
        """
        _git(self.root, "mv", "tomove.txt", "moved.txt")
        fields = [f for f in self.raw_z().split(b"\x00") if f]
        record = next(f for f in fields if f.startswith(b"R"))
        following = fields[fields.index(record) + 1]
        self.assertEqual(record[3:], b"moved.txt", "record should hold the DESTINATION")
        self.assertEqual(following, b"tomove.txt", "next field should hold the SOURCE")


class StatusClassification(unittest.TestCase):
    """The XY -> operation table, asked directly. No repository needed."""

    def test_the_unambiguous_single_states(self):
        for xy, expected in (
            ("??", CHANGE_CREATED),
            ("A ", CHANGE_CREATED),
            (" A", CHANGE_CREATED),
            ("M ", CHANGE_MODIFIED),
            (" M", CHANGE_MODIFIED),
            ("D ", CHANGE_DELETED),
            (" D", CHANGE_DELETED),
            ("R ", CHANGE_RENAMED),
        ):
            with self.subTest(xy=xy):
                self.assertEqual(classify_status(xy), expected)

    def test_combinations_resolve_to_the_net_effect(self):
        """Two columns, one file. The net effect is what Git proves."""
        for xy, expected in (
            ("MM", CHANGE_MODIFIED),   # staged modify + further worktree modify
            ("AM", CHANGE_CREATED),    # added, then modified again: still new
            ("RM", CHANGE_RENAMED),    # renamed, then modified: still a rename
        ):
            with self.subTest(xy=xy):
                self.assertEqual(classify_status(xy), expected)

    def test_states_that_must_stay_unknown(self):
        """`unknown` is the honest answer, and it is preferred to a wrong one."""
        for xy in (
            "UU",  # both modified — unmerged
            "AA",  # both added — unmerged
            "DD",  # both deleted — unmerged
            "AU", "UA", "DU", "UD",
            "T ",  # type change: file became a symlink, or the reverse
            " T",
            "C ",  # copy: the source still exists, so this is not a rename
            "MD",  # staged modify, then deleted in the worktree
            "!!",  # ignored
            "  ",
            "",
            "ZZ",  # nothing Git documents
        ):
            with self.subTest(xy=xy):
                self.assertEqual(classify_status(xy), CHANGE_UNKNOWN)

    def test_a_copy_is_not_reported_as_a_rename(self):
        """`C` has a surviving source. Calling it a rename would be a lie."""
        self.assertNotEqual(classify_status("C "), CHANGE_RENAMED)
        self.assertEqual(classify_status("C "), CHANGE_UNKNOWN)

    def test_the_vocabulary_is_closed(self):
        from cofferdam.workstation.tasks.adapters.claude_code.evidence import CHANGE_KINDS

        self.assertEqual(
            set(CHANGE_KINDS),
            {CHANGE_CREATED, CHANGE_MODIFIED, CHANGE_DELETED, CHANGE_RENAMED, CHANGE_UNKNOWN},
        )
        for xy in ("??", "A ", "M ", "D ", "R ", "UU", "T ", "C ", "xx"):
            self.assertIn(classify_status(xy), CHANGE_KINDS)


class ParsingRealOutput(RealRepositoryFixture):
    def test_a_modified_tracked_file(self):
        self.write("tracked.txt", "one\nchanged\n")
        change = self.changes()["tracked.txt"]
        self.assertEqual(change.kind, CHANGE_MODIFIED)
        self.assertIsNone(change.previous_path)

    def test_an_untracked_file_is_created(self):
        self.write("brand_new.txt")
        self.assertEqual(self.changes()["brand_new.txt"].kind, CHANGE_CREATED)

    def test_a_staged_addition_is_created(self):
        self.write("staged.txt")
        _git(self.root, "add", "staged.txt")
        self.assertEqual(self.changes()["staged.txt"].kind, CHANGE_CREATED)

    def test_a_deleted_file(self):
        _git(self.root, "rm", "-q", "todelete.txt")
        self.assertEqual(self.changes()["todelete.txt"].kind, CHANGE_DELETED)

    def test_a_rename_keeps_both_sides_the_right_way_round(self):
        """The regression this whole file exists to prevent."""
        _git(self.root, "mv", "tomove.txt", "moved.txt")
        change = self.changes()["moved.txt"]
        self.assertEqual(change.kind, CHANGE_RENAMED)
        self.assertEqual(change.path, "moved.txt", "path is the DESTINATION")
        self.assertEqual(change.previous_path, "tomove.txt", "previous_path is the SOURCE")
        self.assertNotIn("tomove.txt", self.changes(), "the source is not a separate entry")

    def test_a_rename_followed_by_an_edit_is_still_a_rename(self):
        _git(self.root, "mv", "tomove.txt", "moved.txt")
        self.write("moved.txt", "two\nedited\n")
        change = self.changes()["moved.txt"]
        self.assertEqual(change.kind, CHANGE_RENAMED)
        self.assertEqual(change.previous_path, "tomove.txt")

    def test_a_filename_with_a_space_survives(self):
        """It does not today: human porcelain quotes it and the parser drops it."""
        self.write("has space.txt")
        self.assertIn("has space.txt", self.changes())

    def test_a_filename_containing_a_literal_arrow_survives_intact(self):
        """`-z` frames by NUL, so an arrow in a name is just bytes."""
        self.write("arrow -> name.txt")
        found = self.changes()
        self.assertIn("arrow -> name.txt", found)
        self.assertEqual(found["arrow -> name.txt"].kind, CHANGE_CREATED)
        self.assertIsNone(found["arrow -> name.txt"].previous_path)

    def test_a_filename_with_a_tab_is_refused_and_the_refusal_is_counted(self):
        """A tab is refused on both sides, and the loss is not silent.

        This is the one place where "survives" would be the wrong answer. A tab
        is a control character, and PR1's `normalize_claim_path` refuses one — so
        a *claim* about `tab\\tname.txt` cannot exist. Accepting the observation
        would create a row that no claim could ever pair with: permanently
        `observed_only`, by construction rather than by evidence.

        The two gates therefore agree, and the refusal is **counted** so the
        bundle can say the machine observation set is incomplete rather than
        implying Cofferdam looked and saw nothing there.
        """
        from cofferdam.workstation.tasks.claims import ClaimPathInvalid, normalize_claim_path

        with self.assertRaises(ClaimPathInvalid):
            normalize_claim_path("tab\tname.txt")

        self.write("tab\tname.txt")
        observation = observe_git(self.root)
        self.assertNotIn("tab\tname.txt", [c.path for c in observation.changes])
        self.assertEqual(observation.refused_count, 1)
        self.assertFalse(observation.complete)

    def test_a_space_and_an_arrow_are_accepted_by_both_gates(self):
        """The contrast: these are ordinary names, and both sides take them."""
        from cofferdam.workstation.tasks.claims import normalize_claim_path

        for name in ("has space.txt", "arrow -> name.txt", "üñïçø∂é.txt"):
            with self.subTest(name=name):
                self.assertEqual(normalize_claim_path(name), name)
                self.write(name)
        observation = observe_git(self.root)
        for name in ("has space.txt", "arrow -> name.txt", "üñïçø∂é.txt"):
            self.assertIn(name, [c.path for c in observation.changes])
        self.assertEqual(observation.refused_count, 0)
        self.assertTrue(observation.complete)

    def test_a_unicode_filename_survives(self):
        self.write("üñïçø∂é.txt")
        self.assertIn("üñïçø∂é.txt", self.changes())

    def test_an_unmerged_path_is_unknown_rather_than_guessed(self):
        _git(self.root, "checkout", "-qb", "other")
        self.write("tracked.txt", "theirs\n")
        _git(self.root, "commit", "-qam", "theirs")
        _git(self.root, "checkout", "-q", "-")
        self.write("tracked.txt", "ours\n")
        _git(self.root, "commit", "-qam", "ours")
        subprocess.run(
            [GIT, "merge", "other"], cwd=str(self.root), capture_output=True,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.root),
                 "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
                 "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.st",
                 "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.st"},
        )
        self.assertEqual(self.changes()["tracked.txt"].kind, CHANGE_UNKNOWN)

    def test_a_type_change_is_unknown(self):
        self.write("target.txt")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "target")
        (self.root / "tracked.txt").unlink()
        (self.root / "tracked.txt").symlink_to("target.txt")
        self.assertEqual(self.changes()["tracked.txt"].kind, CHANGE_UNKNOWN)

    def test_a_clean_tree_reports_no_changes(self):
        observation = observe_git(self.root)
        self.assertEqual(observation.changes, ())
        self.assertTrue(observation.clean)

    def test_a_directory_that_is_not_a_repository_is_not_a_failure(self):
        outside = Path(self._temp.name) / "plain"
        outside.mkdir()
        observation = observe_git(outside)
        self.assertFalse(observation.is_repository)
        self.assertEqual(observation.changes, ())


class ParserRobustness(unittest.TestCase):
    """Records the parser must survive without inventing anything."""

    def test_a_record_shorter_than_a_status_is_skipped(self):
        self.assertEqual(parse_status_z(b"A\x00"), ())

    def test_an_empty_payload_is_no_changes(self):
        self.assertEqual(parse_status_z(b""), ())
        self.assertEqual(parse_status_z(b"\x00\x00"), ())

    def test_a_rename_record_with_no_following_field_is_dropped(self):
        """Half a rename is not a rename, and it is not a creation either."""
        self.assertEqual(parse_status_z(b"R  moved.txt\x00"), ())

    def test_undecodable_bytes_do_not_become_a_mangled_path(self):
        """A path that is not UTF-8 is refused, never repaired with U+FFFD."""
        self.assertEqual(parse_status_z(b"?? bad\xff\xfename.txt\x00"), ())

    def test_a_path_escaping_the_project_is_refused(self):
        for raw in (b"?? /etc/passwd\x00", b"?? ../outside.txt\x00", b"?? a/../../b\x00"):
            with self.subTest(raw=raw):
                self.assertEqual(parse_status_z(raw), ())

    def test_a_nul_or_control_character_cannot_reach_a_path(self):
        self.assertEqual(parse_status_z(b"?? bell\x07name.txt\x00"), ())

    def test_a_rename_whose_source_is_unsafe_is_dropped_entirely(self):
        """Not downgraded to a bare creation — both sides or nothing."""
        self.assertEqual(parse_status_z(b"R  ok.txt\x00/etc/passwd\x00"), ())

    def test_ordering_is_by_path_and_deterministic(self):
        raw = b"?? z.txt\x00?? a.txt\x00?? m.txt\x00"
        self.assertEqual([c.path for c in parse_status_z(raw)], ["a.txt", "m.txt", "z.txt"])

    def test_a_duplicate_path_is_reported_once(self):
        raw = b"?? a.txt\x00?? a.txt\x00"
        self.assertEqual(len(parse_status_z(raw)), 1)


class TheChangeRecord(unittest.TestCase):
    def test_it_carries_only_machine_facts(self):
        change = GitChange(path="src/foo.py", kind=CHANGE_MODIFIED, status="M ")
        self.assertEqual(
            sorted(change.to_dict()),
            ["kind", "path", "previous_path", "status"],
        )

    def test_a_rename_carries_both_paths(self):
        change = GitChange(
            path="new.py", kind=CHANGE_RENAMED, status="R ", previous_path="old.py"
        )
        self.assertEqual(change.path, "new.py")
        self.assertEqual(change.previous_path, "old.py")

    def test_it_holds_no_filesystem_authority(self):
        change = GitChange(path="src/foo.py", kind=CHANGE_MODIFIED, status="M ")
        blob = repr(change.to_dict())
        self.assertNotIn("/home", blob)
        self.assertNotIn("root", blob)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
