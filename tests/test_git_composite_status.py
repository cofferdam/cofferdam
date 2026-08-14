"""M2K PR3 follow-up — untracked enumeration and composite ``XY`` safety.

Two merge-blocking corrections, both about not letting a convenient collapse
become a false statement.

**Untracked files are enumerated individually.** Git's default reports a wholly
new directory as one record — ``?? newdir/`` — and Cofferdam's claim model is
file-level. A directory record cannot pair with a claim about
``newdir/a.py``, so the observation set was silently coarser than the thing it
is compared against. ``--untracked-files=all`` fixes that, in literal argv so it
cannot depend on a user's Git configuration.

**A two-letter status carries two true facts.** ``X`` is the index against HEAD;
``Y`` is the working tree against the index. ``RM`` means *renamed **and** then
modified*; ``AM`` means *added **and** then modified*; ``MD`` means *modified
**and** then deleted*. Collapsing each to one preferred word throws away a fact
that could be exactly the one reconciling a worker's claim — and if the discarded
fact is then treated as absent, an honest claim becomes a conflict.

So the exact ``XY`` is persisted and the compatibility logic reasons over the
**set** of facts it proves. A claim that matches any proven fact agrees; a claim
is contradicted only when it is incompatible with **every** proven fact.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.adapters.claude_code.evidence import (
    GIT_STATUS,
    PROBE_ENVIRONMENT,
    observe_git,
    status_facts,
)
from cofferdam.workstation.tasks.claims import (
    CLAIM_CREATED,
    CLAIM_DELETED,
    CLAIM_MODIFIED,
    CLAIM_RENAMED,
)
from cofferdam.workstation.tasks.evidence import (
    OPERATION_AGREED,
    OPERATION_DIFFERS,
    OPERATION_UNKNOWN,
    operation_agreement,
)
from cofferdam.workstation.tasks.models import (
    CHANGE_CREATED,
    CHANGE_DELETED,
    CHANGE_MODIFIED,
    CHANGE_RENAMED,
)

GIT = shutil.which("git")

_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "T",
    "GIT_AUTHOR_EMAIL": "t@e.st",
    "GIT_COMMITTER_NAME": "T",
    "GIT_COMMITTER_EMAIL": "t@e.st",
}


@unittest.skipIf(GIT is None, "git is not installed")
class RealRepo(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr3f-")
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "repo"
        self.root.mkdir()
        self.git("init", "-q", ".")
        self.write("base.txt", "base\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")

    def git(self, *args):
        subprocess.run(
            [GIT, *args], cwd=str(self.root), check=True, capture_output=True,
            env={**_ENV, "HOME": str(self.root)},
        )

    def write(self, relative, body="x\n"):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def changes(self):
        return {c.path: c for c in observe_git(self.root).changes}


class UntrackedFilesAreEnumerated(RealRepo):
    def test_the_probe_asks_for_every_untracked_file(self):
        """In literal argv, so no user Git config can turn it off."""
        self.assertEqual(
            GIT_STATUS,
            ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"),
        )

    def test_a_wholly_new_directory_yields_one_observation_per_file(self):
        """The behaviour the earlier E2E pinned as a limitation. It is not one now."""
        self.write("newdir/a.py")
        self.write("newdir/b.py")
        found = self.changes()
        self.assertIn("newdir/a.py", found)
        self.assertIn("newdir/b.py", found)
        self.assertNotIn("newdir/", found)
        self.assertNotIn("newdir", found)
        for path in ("newdir/a.py", "newdir/b.py"):
            self.assertEqual(found[path].kind, CHANGE_CREATED)

    def test_nested_untracked_directories_are_enumerated_to_the_leaf(self):
        self.write("newdir/a.py")
        self.write("newdir/deep/nested/c.py")
        found = self.changes()
        self.assertIn("newdir/deep/nested/c.py", found)
        self.assertFalse([p for p in found if p.endswith("/")])

    def test_awkward_filenames_under_an_untracked_directory_survive(self):
        self.write("newdir/has space.py")
        self.write("newdir/üñïçø∂é.py")
        self.write("newdir/arrow -> name.py")
        found = self.changes()
        for name in ("has space.py", "üñïçø∂é.py", "arrow -> name.py"):
            self.assertIn("newdir/" + name, found)

    def test_the_observation_is_complete_when_nothing_was_dropped(self):
        self.write("newdir/a.py")
        self.write("newdir/b.py")
        observation = observe_git(self.root)
        self.assertTrue(observation.complete)
        self.assertEqual(observation.refused_count, 0)
        self.assertFalse(observation.truncated)

    def test_more_untracked_files_than_the_parser_cap_is_truncation(self):
        from cofferdam.workstation.tasks.adapters.claude_code.evidence import (
            MAX_REPORTED_PATHS,
        )

        for index in range(MAX_REPORTED_PATHS + 5):
            self.write("newdir/f%02d.py" % index)
        observation = observe_git(self.root)
        self.assertTrue(observation.truncated)
        self.assertFalse(observation.complete)
        self.assertEqual(len(observation.changes), MAX_REPORTED_PATHS)
        self.assertGreater(observation.reported_count, MAX_REPORTED_PATHS)


class OptionalLocksAreAlreadyDisabled(RealRepo):
    """The side-effect audit. Cofferdam already does this; proven, not changed."""

    def test_the_probe_environment_disables_optional_locks(self):
        self.assertEqual(PROBE_ENVIRONMENT.get("GIT_OPTIONAL_LOCKS"), "0")

    def test_the_environment_is_actually_passed_to_the_subprocess(self):
        import inspect

        from cofferdam.workstation.tasks.adapters.claude_code import evidence

        source = inspect.getsource(evidence._run_bytes)
        self.assertIn("env=dict(PROBE_ENVIRONMENT)", source)

    def test_observing_does_not_rewrite_the_index(self):
        """The property the variable exists for, asserted on the file itself.

        ``git status`` may refresh cached stat information in the index as an
        optimisation — a write performed merely to look. With
        ``GIT_OPTIONAL_LOCKS=0`` Git skips it, so observing a project leaves the
        index byte-identical.
        """
        self.write("base.txt", "base\nchanged\n")
        index = self.root / ".git" / "index"
        before = index.read_bytes()
        for _ in range(3):
            observe_git(self.root)
        self.assertEqual(index.read_bytes(), before)

    def test_no_probe_writes_a_lock_file(self):
        self.write("base.txt", "base\nchanged\n")
        observe_git(self.root)
        self.assertFalse((self.root / ".git" / "index.lock").exists())


class StatusFactSets(unittest.TestCase):
    """What each ``XY`` actually proves, asked directly."""

    def test_simple_states_prove_exactly_one_fact(self):
        for xy, fact in (
            ("??", CHANGE_CREATED),
            ("A ", CHANGE_CREATED),
            ("M ", CHANGE_MODIFIED),
            (" M", CHANGE_MODIFIED),
            ("MM", CHANGE_MODIFIED),
            ("D ", CHANGE_DELETED),
            (" D", CHANGE_DELETED),
            ("R ", CHANGE_RENAMED),
        ):
            with self.subTest(xy=xy):
                self.assertEqual(status_facts(xy), frozenset({fact}))

    def test_composite_states_prove_two_facts(self):
        """Both columns are true. Neither may be discarded."""
        self.assertEqual(status_facts("AM"), frozenset({CHANGE_CREATED, CHANGE_MODIFIED}))
        self.assertEqual(status_facts("RM"), frozenset({CHANGE_RENAMED, CHANGE_MODIFIED}))
        self.assertEqual(status_facts("MD"), frozenset({CHANGE_MODIFIED, CHANGE_DELETED}))
        self.assertEqual(status_facts("AD"), frozenset({CHANGE_CREATED, CHANGE_DELETED}))
        self.assertEqual(status_facts("RD"), frozenset({CHANGE_RENAMED, CHANGE_DELETED}))

    def test_states_that_prove_nothing_publishable_are_empty(self):
        for xy in ("UU", "AA", "DD", "AU", "UA", "DU", "UD", "T ", " T", "C ", "!!", "", "ZZ"):
            with self.subTest(xy=xy):
                self.assertEqual(status_facts(xy), frozenset())

    def test_an_unknown_status_is_empty_rather_than_guessed(self):
        self.assertEqual(status_facts(None), frozenset())
        self.assertEqual(status_facts("toolong"), frozenset())


class CompositeStatesNeverCreateFalseConflicts(unittest.TestCase):
    """The blocker, stated as the property it protects.

    A composite status carries a fact that may be exactly the one reconciling a
    worker's claim. Discarding it and then reading the absence as evidence would
    turn an honest report into a contradiction.
    """

    def test_rm_with_a_modified_claim_agrees_and_never_conflicts(self):
        """The named case: renamed **and** modified. The worker said modified."""
        result = operation_agreement(CLAIM_MODIFIED, CHANGE_RENAMED, status="RM")
        self.assertEqual(result, OPERATION_AGREED)
        self.assertNotEqual(result, OPERATION_DIFFERS)

    def test_am_with_a_modified_claim_agrees_and_never_conflicts(self):
        result = operation_agreement(CLAIM_MODIFIED, CHANGE_CREATED, status="AM")
        self.assertEqual(result, OPERATION_AGREED)

    def test_am_with_a_created_claim_agrees(self):
        self.assertEqual(
            operation_agreement(CLAIM_CREATED, CHANGE_CREATED, status="AM"),
            OPERATION_AGREED,
        )

    def test_md_with_a_modified_claim_agrees_rather_than_conflicting(self):
        """Modified in the index, deleted from the tree. Both are true."""
        self.assertEqual(
            operation_agreement(CLAIM_MODIFIED, None, status="MD"), OPERATION_AGREED
        )

    def test_md_with_a_deleted_claim_agrees_too(self):
        self.assertEqual(
            operation_agreement(CLAIM_DELETED, None, status="MD"), OPERATION_AGREED
        )

    def test_rm_with_a_deleted_claim_is_unknown_not_false(self):
        """`deleted` contradicts `modified` but not `renamed`. Not all: unknown."""
        self.assertEqual(
            operation_agreement(CLAIM_DELETED, CHANGE_RENAMED, status="RM"),
            OPERATION_UNKNOWN,
        )

    def test_md_with_a_created_claim_is_unknown_not_false(self):
        self.assertEqual(
            operation_agreement(CLAIM_CREATED, None, status="MD"), OPERATION_UNKNOWN
        )

    def test_a_claim_matching_any_proven_fact_always_agrees(self):
        """The invariant, swept across every status this build knows."""
        for xy in ("??", "A ", "M ", " M", "MM", "D ", " D", "R ",
                   "AM", "RM", "MD", "AD", "RD"):
            # `renamed` is deliberately excluded: a rename needs both paths, so
            # this helper never agrees one however the status reads. See
            # `test_a_rename_claim_is_never_agreed_by_the_status_alone`.
            for claim in (CLAIM_CREATED, CLAIM_MODIFIED, CLAIM_DELETED):
                if claim in status_facts(xy):
                    with self.subTest(xy=xy, claim=claim):
                        self.assertEqual(
                            operation_agreement(claim, None, status=xy),
                            OPERATION_AGREED,
                            "%r proves %s; a claim of %s must agree" % (xy, claim, claim),
                        )

    def test_a_rename_claim_is_never_agreed_by_the_status_alone(self):
        """Same destination from a different source is a different event.

        Even ``R `` and ``RM``, which prove a rename happened, do not prove it
        is *this* rename. Only the comparison that uses both paths may agree one.
        """
        for xy in ("R ", " R", "RM", "RD", "??", "M ", "D "):
            with self.subTest(xy=xy):
                self.assertEqual(
                    operation_agreement(CLAIM_RENAMED, CHANGE_RENAMED, status=xy),
                    OPERATION_UNKNOWN,
                )

    def test_false_requires_incompatibility_with_every_proven_fact(self):
        """A single reconciling fact is enough to stop a contradiction."""
        incompatible = {
            (CLAIM_CREATED, CHANGE_DELETED),
            (CLAIM_MODIFIED, CHANGE_DELETED),
            (CLAIM_DELETED, CHANGE_CREATED),
            (CLAIM_DELETED, CHANGE_MODIFIED),
        }
        for xy in ("??", "A ", "M ", " M", "MM", "D ", " D", "R ",
                   "AM", "RM", "MD", "AD", "RD", "UU", "T ", "C "):
            facts = status_facts(xy)
            for claim in (CLAIM_CREATED, CLAIM_MODIFIED, CLAIM_DELETED, CLAIM_RENAMED):
                result = operation_agreement(claim, None, status=xy)
                if result == OPERATION_DIFFERS:
                    with self.subTest(xy=xy, claim=claim):
                        self.assertTrue(facts, "%r proves nothing yet contradicted" % xy)
                        for fact in facts:
                            self.assertIn(
                                (claim, fact),
                                incompatible,
                                "%r: claim %s contradicted despite fact %s"
                                % (xy, claim, fact),
                            )


class SimpleStatesStillDecide(unittest.TestCase):
    """Collapsing must not have cost the answers PR3 already established."""

    def test_a_simple_delete_still_contradicts_a_modified_claim(self):
        self.assertEqual(
            operation_agreement(CLAIM_MODIFIED, CHANGE_DELETED, status="D "),
            OPERATION_DIFFERS,
        )

    def test_a_simple_modify_still_contradicts_a_deleted_claim(self):
        self.assertEqual(
            operation_agreement(CLAIM_DELETED, CHANGE_MODIFIED, status="M "),
            OPERATION_DIFFERS,
        )

    def test_a_simple_create_still_contradicts_a_deleted_claim(self):
        self.assertEqual(
            operation_agreement(CLAIM_DELETED, CHANGE_CREATED, status="??"),
            OPERATION_DIFFERS,
        )

    def test_created_versus_modified_is_still_unknown(self):
        self.assertEqual(
            operation_agreement(CLAIM_CREATED, CHANGE_MODIFIED, status="M "),
            OPERATION_UNKNOWN,
        )

    def test_agreement_still_works_without_a_status(self):
        """A row carrying a kind but no XY falls back to the kind alone."""
        self.assertEqual(
            operation_agreement(CLAIM_MODIFIED, CHANGE_MODIFIED), OPERATION_AGREED
        )
        self.assertEqual(
            operation_agreement(CLAIM_MODIFIED, CHANGE_DELETED), OPERATION_DIFFERS
        )

    def test_a_legacy_row_with_neither_stays_unknown(self):
        self.assertEqual(operation_agreement(CLAIM_MODIFIED, None), OPERATION_UNKNOWN)

    def test_unmerged_never_decides_anything(self):
        for claim in (CLAIM_CREATED, CLAIM_MODIFIED, CLAIM_DELETED, CLAIM_RENAMED):
            with self.subTest(claim=claim):
                self.assertEqual(
                    operation_agreement(claim, None, status="UU"), OPERATION_UNKNOWN
                )


@unittest.skipIf(GIT is None, "git is not installed")
class CompositeStatesFromRealGit(RealRepo):
    """The same properties, against statuses Git actually produced."""

    def _build(self):
        for name in ("rm", "mm", "md", "ad", "rd"):
            self.write("%s.txt" % name, "base\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "more")
        # AM
        self.write("am_new.txt", "new\n")
        self.git("add", "am_new.txt")
        self.write("am_new.txt", "new\nmore\n")
        # RM
        self.git("mv", "rm.txt", "rm_moved.txt")
        self.write("rm_moved.txt", "base\nmore\n")
        # MM
        self.write("mm.txt", "base\nx\n")
        self.git("add", "mm.txt")
        self.write("mm.txt", "base\nx\ny\n")
        # MD
        self.write("md.txt", "base\nx\n")
        self.git("add", "md.txt")
        (self.root / "md.txt").unlink()
        return self.changes()

    def test_real_git_produces_the_composite_statuses(self):
        found = self._build()
        self.assertEqual(found["am_new.txt"].status, "AM")
        self.assertEqual(found["rm_moved.txt"].status, "RM")
        self.assertEqual(found["mm.txt"].status, "MM")
        self.assertEqual(found["md.txt"].status, "MD")

    def test_the_real_rm_status_agrees_with_a_modified_claim(self):
        found = self._build()
        change = found["rm_moved.txt"]
        self.assertEqual(
            operation_agreement(CLAIM_MODIFIED, change.kind, status=change.status),
            OPERATION_AGREED,
        )

    def test_the_real_rm_status_keeps_both_rename_paths(self):
        found = self._build()
        change = found["rm_moved.txt"]
        self.assertEqual(change.kind, CHANGE_RENAMED)
        self.assertEqual(change.previous_path, "rm.txt")

    def test_the_real_am_status_agrees_with_either_claim(self):
        found = self._build()
        change = found["am_new.txt"]
        for claim in (CLAIM_CREATED, CLAIM_MODIFIED):
            with self.subTest(claim=claim):
                self.assertEqual(
                    operation_agreement(claim, change.kind, status=change.status),
                    OPERATION_AGREED,
                )

    def test_the_real_md_status_never_contradicts_a_modified_claim(self):
        found = self._build()
        change = found["md.txt"]
        self.assertNotEqual(
            operation_agreement(CLAIM_MODIFIED, change.kind, status=change.status),
            OPERATION_DIFFERS,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
