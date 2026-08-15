"""M2K PR5 — what the assembler does with committed-range evidence.

Three things are being defended here, and they are easy to confuse.

**The domains stay apart.** A path can be committed inside a turn and changed
again afterwards. Those are two machine facts at two moments, and the bundle must
carry both without either overwriting the other.

**Chronology cannot manufacture a conflict.** Within one domain, two
incompatible observations describe one instant and one of them must be wrong. Across
domains they describe different instants and both can be right — "committed as
modified, then deleted" is an ordinary morning's work, and a claim of "modified"
was true when it was made. So a reconciling fact in *either* domain stops a
contradiction.

**A dirty boundary cannot produce a conflict at all.** PR4 records whether the
repository was already dirty before the worker started. If it was, a change that
predates the turn can be committed inside the range and is indistinguishable from
the worker's own — so such a range may show change and may never contradict.
``unknown`` with a limitation is the answer, and this file makes sure it is not
quietly upgraded to ``false``.

The fingerprint tests are the other half: everything above is only durable if
none of it can change without the fingerprint changing.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.claims import (
    CLAIM_CREATED,
    CLAIM_DELETED,
    CLAIM_MODIFIED,
    CLAIM_RENAMED,
    ClaimSubmission,
)
from cofferdam.workstation.tasks import evidence as evidence_module
from cofferdam.workstation.tasks import gitrange as gitrange_module
from cofferdam.workstation.tasks.evidence import (
    ASSEMBLER_VERSION,
    LIMIT_RANGE_BOUNDARY_NOT_CLEAN,
    LIMIT_RANGE_HISTORY_DIVERGED,
    LIMIT_RANGE_INCOMPLETE,
    LIMIT_RANGE_NOT_RECORDED,
    LIMIT_RANGE_UNAVAILABLE,
    LIMIT_UNSUPPORTED_OBSERVATION,
    OPERATION_AGREED,
    OPERATION_DIFFERS,
    OPERATION_UNKNOWN,
    RANGE_ANCESTRY_DIVERGED,
    RANGE_ANCESTRY_IDENTICAL,
    RANGE_ANCESTRY_LINEAR,
    RANGE_BOUNDARY_CLEAN,
    RANGE_COVERAGE_COMPLETE,
    RANGE_COVERAGE_INCOMPLETE,
    RANGE_COVERAGE_UNAVAILABLE,
    RANGE_LIMITATION_NONE,
    RANGE_OPERATION_BASELINE,
    RANGE_OPERATION_COVERAGE,
    RANGE_OPERATION_LIMITATION,
    RANGE_OPERATION_PATH,
    RANGE_OPERATION_TARGET,
    RANGE_RESULT_COMMITTED,
    RELATIONSHIP_CLAIM_CONFLICT,
    RELATIONSHIP_OBSERVED_ONLY,
    RELATIONSHIP_PATH_AGREED,
    observation_domain,
)
from cofferdam.workstation.tasks.gitbaseline import (
    CAPTURE_CAPTURED,
    CAPTURE_UNAVAILABLE,
    COVERAGE_COMPLETE as BASELINE_COVERAGE_COMPLETE,
    COVERAGE_INCOMPLETE as BASELINE_COVERAGE_INCOMPLETE,
    COVERAGE_UNAVAILABLE as BASELINE_COVERAGE_UNAVAILABLE,
    HEAD_PRESENT,
    HEAD_UNBORN,
    WORKTREE_CLEAN,
    WORKTREE_DIRTY,
    WORKTREE_UNKNOWN,
    GitBaseline,
)
from cofferdam.workstation.tasks.gitrange import (
    BOUNDARY_CLEAN_COMPLETE,
    BOUNDARY_DIRTY,
    BOUNDARY_INCOMPLETE,
    BOUNDARY_UNAVAILABLE,
    MAX_RANGE_EVIDENCE_PATHS,
    REASON_EVIDENCE_BUDGET,
    CommittedChange,
    CommittedRange,
    boundary_quality,
    range_evidence,
)
from cofferdam.workstation.tasks.models import (
    CHANGE_CREATED,
    CHANGE_DELETED,
    CHANGE_MODIFIED,
    CHANGE_RENAMED,
    EVIDENCE_ARTIFACT,
    EVIDENCE_COMMIT,
    EVIDENCE_FILE,
    EVIDENCE_GIT_OBSERVED,
    MAX_EVIDENCE_ITEMS,
    OBSERVATION_DOMAIN_COMMITTED_RANGE,
    OBSERVATION_DOMAIN_WORKTREE,
    EvidenceReference,
)
from cofferdam.workstation.tasks.store import TaskStore, _TurnClose

BASE = "a" * 40
TARGET = "b" * 40


# -- the copy the assembler keeps ---------------------------------------------


class TheLiteralsMatchTheEmitter(unittest.TestCase):
    """``evidence.py`` restates ``gitrange.py``'s words rather than importing them.

    Deliberately, and for a reason worth defending in a test: the assembler must
    stay a standalone reader of stored rows. Importing the probe would put
    ``subprocess`` in the assembler's import graph and turn "assembly cannot
    reach the world" from a property somebody can check into a promise somebody
    has to trust. PR3 made the same choice for its coverage words.

    The cost of a copy is drift, so this is the test that makes drift loud.
    """

    def test_every_operation_word_is_the_word_the_emitter_writes(self):
        for reader, writer in (
            (RANGE_OPERATION_PATH, gitrange_module.RANGE_OP_PATH),
            (RANGE_OPERATION_BASELINE, gitrange_module.RANGE_OP_BASELINE),
            (RANGE_OPERATION_TARGET, gitrange_module.RANGE_OP_TARGET),
            (RANGE_OPERATION_COVERAGE, gitrange_module.RANGE_OP_COVERAGE),
            (RANGE_OPERATION_LIMITATION, gitrange_module.RANGE_OP_LIMITATION),
            (RANGE_LIMITATION_NONE, gitrange_module.RANGE_LIMITATION_NONE),
        ):
            with self.subTest(word=writer):
                self.assertEqual(reader, writer)

    def test_every_state_word_is_the_word_the_probe_writes(self):
        for reader, writer in (
            (RANGE_ANCESTRY_LINEAR, gitrange_module.ANCESTRY_LINEAR),
            (RANGE_ANCESTRY_IDENTICAL, gitrange_module.ANCESTRY_IDENTICAL),
            (RANGE_ANCESTRY_DIVERGED, gitrange_module.ANCESTRY_DIVERGED),
            (RANGE_COVERAGE_COMPLETE, gitrange_module.RANGE_COVERAGE_COMPLETE),
            (RANGE_COVERAGE_INCOMPLETE, gitrange_module.RANGE_COVERAGE_INCOMPLETE),
            (RANGE_COVERAGE_UNAVAILABLE, gitrange_module.RANGE_COVERAGE_UNAVAILABLE),
            (RANGE_BOUNDARY_CLEAN, gitrange_module.BOUNDARY_CLEAN_COMPLETE),
        ):
            with self.subTest(word=writer):
                self.assertEqual(reader, writer)

    def test_the_eligible_ancestries_are_the_probes_own(self):
        self.assertEqual(
            set(evidence_module.RANGE_ELIGIBLE_ANCESTRIES),
            set(gitrange_module.RANGE_ELIGIBLE_ANCESTRIES),
        )

    def test_the_assembler_does_not_import_the_probe(self):
        """Asked of the import statements, not of the file's text.

        The module discusses the probe at length by necessity — explaining why
        the copy exists is most of the reason it is safe — so a text search would
        fail on the prose and then be tuned until it stopped meaning anything.
        """
        import ast

        tree = ast.parse(Path(evidence_module.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        self.assertNotIn("gitrange", imported, "the assembler imported the probe")
        self.assertNotIn("gitbaseline", imported)
        self.assertNotIn("subprocess", imported)


# -- boundary quality ---------------------------------------------------------


class BoundaryQuality(unittest.TestCase):
    """PR4's row, reduced to the one word that governs what PR5 may conclude."""

    def quality(self, **kwargs):
        defaults = dict(
            capture_state=CAPTURE_CAPTURED,
            head_state=HEAD_PRESENT,
            head_revision=BASE,
            object_format="sha1",
            working_tree_state=WORKTREE_CLEAN,
            status_coverage=BASELINE_COVERAGE_COMPLETE,
        )
        defaults.update(kwargs)
        return boundary_quality(GitBaseline(**defaults))

    def test_a_clean_completely_read_tree_is_the_only_clean_boundary(self):
        self.assertEqual(self.quality(), BOUNDARY_CLEAN_COMPLETE)

    def test_a_dirty_tree_is_dirty_however_well_it_was_read(self):
        self.assertEqual(
            self.quality(working_tree_state=WORKTREE_DIRTY), BOUNDARY_DIRTY
        )
        self.assertEqual(
            self.quality(
                working_tree_state=WORKTREE_DIRTY,
                status_coverage=BASELINE_COVERAGE_INCOMPLETE,
            ),
            BOUNDARY_DIRTY,
            "one valid change is enough to know the tree was not clean",
        )

    def test_a_tree_whose_state_was_not_established_is_incomplete_not_clean(self):
        self.assertEqual(
            self.quality(
                working_tree_state=WORKTREE_UNKNOWN,
                status_coverage=BASELINE_COVERAGE_UNAVAILABLE,
            ),
            BOUNDARY_INCOMPLETE,
        )

    def test_no_baseline_row_is_unavailable_and_never_clean(self):
        """The mistake that would turn a missing record into a licence."""
        self.assertEqual(boundary_quality(None), BOUNDARY_UNAVAILABLE)
        self.assertEqual(boundary_quality("clean"), BOUNDARY_UNAVAILABLE)

    def test_an_unborn_or_failed_capture_is_unavailable(self):
        self.assertEqual(
            self.quality(head_state=HEAD_UNBORN, head_revision=None),
            BOUNDARY_UNAVAILABLE,
        )
        self.assertEqual(
            self.quality(capture_state=CAPTURE_UNAVAILABLE), BOUNDARY_UNAVAILABLE
        )


# -- the emitted shape --------------------------------------------------------


class TheEmittedEvidence(unittest.TestCase):
    def emit(self, observed, quality=BOUNDARY_CLEAN_COMPLETE):
        return range_evidence(observed, quality=quality, observed_at="2026-08-15T00:00:00Z")

    def linear(self, *changes, coverage=RANGE_COVERAGE_COMPLETE, reason=None):
        return CommittedRange(
            capture_state="captured",
            ancestry=RANGE_ANCESTRY_LINEAR,
            coverage=coverage,
            baseline_revision=BASE,
            target_revision=TARGET,
            object_format="sha1",
            changes=tuple(changes),
            reason=reason,
        )

    def test_the_four_metadata_rows_are_always_emitted(self):
        """Including on a range with nothing to say, so absence is never a hint."""
        emitted = self.emit(self.linear())
        operations = [reference.operation for reference in emitted]
        self.assertEqual(
            operations,
            [
                RANGE_OPERATION_BASELINE,
                RANGE_OPERATION_TARGET,
                RANGE_OPERATION_COVERAGE,
                RANGE_OPERATION_LIMITATION,
            ],
        )
        self.assertEqual(emitted[3].result, RANGE_LIMITATION_NONE)

    def test_each_metadata_row_names_one_subject_and_one_fact(self):
        baseline, target, coverage, limitation = self.emit(self.linear())[:4]
        self.assertEqual((baseline.identifier, baseline.result), (BASE, RANGE_BOUNDARY_CLEAN))
        self.assertEqual((target.identifier, target.result), (TARGET, RANGE_ANCESTRY_LINEAR))
        self.assertEqual((coverage.identifier, coverage.result), (None, RANGE_COVERAGE_COMPLETE))
        self.assertEqual(limitation.identifier, None)

    def test_every_row_is_git_observed_and_carries_the_domain(self):
        for reference in self.emit(self.linear(CommittedChange(kind="created", path="a"))):
            self.assertEqual(reference.source, EVIDENCE_GIT_OBSERVED)
            self.assertEqual(reference.domain, OBSERVATION_DOMAIN_COMMITTED_RANGE)

    def test_a_path_row_keeps_the_git_token_verbatim(self):
        emitted = self.emit(
            self.linear(
                CommittedChange(
                    kind="renamed", path="new.py", previous_path="old.py", status="R080"
                )
            )
        )
        change = emitted[-1]
        self.assertEqual(change.evidence_type, EVIDENCE_FILE)
        self.assertEqual(change.identifier, "new.py")
        self.assertEqual(change.previous_identifier, "old.py")
        self.assertEqual(change.change_kind, CHANGE_RENAMED)
        self.assertEqual(change.change_status, "R080")
        self.assertEqual(change.result, RANGE_RESULT_COMMITTED)

    def test_a_range_row_does_not_look_like_a_worktree_row(self):
        """`changed` and `committed` are what a reader sees first."""
        change = self.emit(self.linear(CommittedChange(kind="created", path="a")))[-1]
        self.assertNotEqual(change.result, "changed")
        self.assertNotEqual(change.operation, "git status")

    def test_a_copy_and_a_type_change_are_unknown_rather_than_guessed(self):
        for kind, status in (("copied", "C075"), ("type_changed", "T")):
            with self.subTest(kind=kind):
                change = self.emit(
                    self.linear(CommittedChange(kind=kind, path="p", status=status))
                )[-1]
                self.assertEqual(change.change_kind, "unknown")
                self.assertEqual(
                    change.change_status, status, "the exact Git token was lost"
                )

    def test_the_whole_event_fits_the_evidence_budget(self):
        emitted = self.emit(
            self.linear(
                *[
                    CommittedChange(kind="created", path="f%d.py" % index)
                    for index in range(MAX_RANGE_EVIDENCE_PATHS + 10)
                ]
            )
        )
        self.assertEqual(len(emitted), MAX_EVIDENCE_ITEMS)

    def test_exceeding_the_budget_is_recorded_rather_than_dropped(self):
        emitted = self.emit(
            self.linear(
                *[
                    CommittedChange(kind="created", path="f%d.py" % index)
                    for index in range(MAX_RANGE_EVIDENCE_PATHS + 1)
                ]
            )
        )
        by_operation = {r.operation: r.result for r in emitted[:4]}
        self.assertEqual(by_operation[RANGE_OPERATION_COVERAGE], RANGE_COVERAGE_INCOMPLETE)
        self.assertEqual(by_operation[RANGE_OPERATION_LIMITATION], REASON_EVIDENCE_BUDGET)

    def test_a_path_that_could_never_match_a_claim_is_refused_and_said_so(self):
        """Refused, not dropped. A silent omission reads as "nothing happened"."""
        emitted = self.emit(
            self.linear(
                CommittedChange(kind="created", path="good.py", status="A"),
                CommittedChange(kind="created", path="tab\tname.txt", status="A"),
            )
        )
        by_operation = {r.operation: r.result for r in emitted[:4]}
        self.assertEqual(by_operation[RANGE_OPERATION_COVERAGE], RANGE_COVERAGE_INCOMPLETE)
        self.assertEqual(by_operation[RANGE_OPERATION_LIMITATION], "unsafe_path_refused")
        self.assertEqual([r.identifier for r in emitted[4:]], ["good.py"])

    def test_the_refused_shapes(self):
        for path in (
            "/etc/passwd", "~/secrets", "../escape", "a/../b", "a//b", "./a",
            "back\\slash", "C:/windows", "nul\x00byte", "x" * 600,
        ):
            with self.subTest(path=path):
                emitted = self.emit(
                    self.linear(CommittedChange(kind="created", path=path, status="A"))
                )
                self.assertEqual(len(emitted), 4, path + " was recorded")

    def test_a_rename_needs_both_of_its_paths_to_be_recordable(self):
        """Half a rename is a different event from the one Git reported."""
        emitted = self.emit(
            self.linear(
                CommittedChange(
                    kind="renamed", path="new.py", previous_path="/absolute/old.py",
                    status="R100",
                )
            )
        )
        self.assertEqual(len(emitted), 4)
        by_operation = {r.operation: r.result for r in emitted[:4]}
        self.assertEqual(by_operation[RANGE_OPERATION_LIMITATION], "unsafe_path_refused")

    def test_ordinary_unicode_and_spaces_are_not_refused(self):
        """Türkçe karakterler are ordinary text and must never be a special case."""
        for path in ("with space.txt", "unicode-éè-日本語.txt", "src/Türkçe/dosya.py",
                     "literal -> arrow.txt"):
            with self.subTest(path=path):
                emitted = self.emit(
                    self.linear(CommittedChange(kind="created", path=path, status="A"))
                )
                self.assertEqual([r.identifier for r in emitted[4:]], [path])

    def test_the_probes_own_reason_is_kept_ahead_of_the_budgets(self):
        """The first wall the observation hit is the more useful one to record."""
        emitted = self.emit(
            self.linear(
                *[
                    CommittedChange(kind="created", path="f%d.py" % index)
                    for index in range(MAX_RANGE_EVIDENCE_PATHS + 1)
                ],
                coverage=RANGE_COVERAGE_INCOMPLETE,
                reason="output_truncated",
            )
        )
        by_operation = {r.operation: r.result for r in emitted[:4]}
        self.assertEqual(by_operation[RANGE_OPERATION_LIMITATION], "output_truncated")


# -- the bundle ---------------------------------------------------------------


def worktree(path, kind=None, previous=None, status=None):
    return EvidenceReference(
        evidence_type=EVIDENCE_FILE,
        source=EVIDENCE_GIT_OBSERVED,
        identifier=path,
        operation="git status",
        result="changed",
        change_kind=kind,
        previous_identifier=previous,
        change_status=status,
        observed_at="2026-08-15T00:00:00Z",
    )


def worktree_coverage(result="observed all changes"):
    return EvidenceReference(
        evidence_type=EVIDENCE_ARTIFACT,
        source=EVIDENCE_GIT_OBSERVED,
        identifier=None,
        operation="git status",
        result=result,
        observed_at="2026-08-15T00:00:00Z",
    )


class RangeBundleFixture(unittest.TestCase):
    def setUp(self):
        from cofferdam.workstation.config import load_config

        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr5-range-")
        self.addCleanup(self._temp.cleanup)
        self.home = Path(self._temp.name)
        self.root = self.home / "project"
        self.root.mkdir()
        config = load_config(self.home)
        config.ensure_dirs()
        self.config = config
        self.store = TaskStore(config)
        self.addCleanup(self._close)
        self.task_id = self._task()

    def _close(self):
        try:
            self.store.close()
        except Exception:
            pass

    def _task(self):
        row, _ = self.store.create_task(
            origin="pwa", adapter_id="validation", project_id="s", prompt="p", title="t"
        )
        for state in ("queued", "starting", "running"):
            self.store.transition(
                row.task_id, state, event_type="task_" + state,
                actor="system", source="cofferdam",
            )
        self.store.open_turn(
            row.task_id, provider="validation", source="internal_test",
            started_at="2026-08-15T00:00:00Z",
        )
        return row.task_id

    def observe(self, *references):
        return self.store.append_event(
            self.task_id, "progress", actor="system", source="cofferdam",
            text="Cofferdam checked the project itself.", evidence=references,
        )

    def observe_range(
        self,
        *changes,
        quality=BOUNDARY_CLEAN_COMPLETE,
        ancestry=RANGE_ANCESTRY_LINEAR,
        coverage=RANGE_COVERAGE_COMPLETE,
        reason=None,
        capture_state="captured",
    ):
        observed = CommittedRange(
            capture_state=capture_state,
            ancestry=ancestry,
            coverage=coverage,
            baseline_revision=BASE,
            target_revision=TARGET,
            object_format="sha1",
            changes=tuple(changes),
            reason=reason,
        )
        return self.store.append_event(
            self.task_id,
            "committed_range_observed",
            actor="system",
            source="cofferdam",
            evidence=range_evidence(
                observed, quality=quality, observed_at="2026-08-15T00:01:00Z"
            ),
        )

    def claim(self, *submissions):
        for submission in submissions:
            for name in (submission.path, submission.to_path):
                if not name:
                    continue
                target = self.root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("x", encoding="utf-8")
        return self.store.record_change_claims(
            self.task_id, submissions, project_root=self.root, turn_number=1
        )

    def bundle(self):
        if self.store.current_turn(self.task_id) is not None:
            self.store.transition(
                self.task_id, "ready_for_followup", event_type="turn_complete",
                actor="adapter", source="adapter",
                close_turn=_TurnClose(
                    outcome="completed", completed_at="2026-08-15T00:05:00Z"
                ),
            )
        return self.store.evidence_bundle(self.task_id, 1)

    def groups(self):
        return {group.path: group for group in self.bundle().relationships}


class TheSummaryIsRead(RangeBundleFixture):
    def test_a_recorded_range_is_read_back_whole(self):
        self.observe_range(CommittedChange(kind="created", path="a.py", status="A"))
        summary = self.bundle().committed_range

        self.assertTrue(summary.recorded)
        self.assertEqual(summary.baseline_revision, BASE)
        self.assertEqual(summary.target_revision, TARGET)
        self.assertEqual(summary.boundary_quality, RANGE_BOUNDARY_CLEAN)
        self.assertEqual(summary.ancestry, RANGE_ANCESTRY_LINEAR)
        self.assertEqual(summary.coverage, RANGE_COVERAGE_COMPLETE)
        self.assertIsNone(summary.limitation)
        self.assertTrue(summary.history_valid)
        self.assertTrue(summary.comparison_grade)

    def test_a_turn_with_no_range_says_nobody_looked(self):
        """`recorded=False`, not "nothing was committed"."""
        self.observe(worktree_coverage())
        bundle = self.bundle()
        self.assertFalse(bundle.committed_range.recorded)
        self.assertFalse(bundle.committed_range.comparison_grade)
        self.assertIn(LIMIT_RANGE_NOT_RECORDED, bundle.limitations)

    def test_the_no_limitation_sentinel_is_not_published_as_a_limitation(self):
        self.observe_range()
        self.assertIsNone(self.bundle().committed_range.limitation)

    def test_a_range_row_is_never_an_unsupported_shape(self):
        self.observe_range(CommittedChange(kind="modified", path="a.py", status="M"))
        self.assertNotIn(LIMIT_UNSUPPORTED_OBSERVATION, self.bundle().limitations)

    def test_the_summary_is_published(self):
        self.observe_range()
        published = self.bundle().to_dict()["committed_range"]
        self.assertEqual(published["baseline_revision"], BASE)
        self.assertTrue(published["history_valid"])
        self.assertTrue(published["comparison_grade"])

    def test_a_diverged_range_carries_its_own_words(self):
        self.observe_range(
            ancestry=RANGE_ANCESTRY_DIVERGED,
            coverage=RANGE_COVERAGE_UNAVAILABLE,
            reason="history_diverged",
            capture_state="unavailable",
        )
        bundle = self.bundle()
        self.assertFalse(bundle.committed_range.history_valid)
        self.assertFalse(bundle.committed_range.comparison_grade)
        self.assertIn(LIMIT_RANGE_HISTORY_DIVERGED, bundle.limitations)
        self.assertIn(LIMIT_RANGE_UNAVAILABLE, bundle.limitations)
        self.assertEqual(bundle.observations, ())

    def test_an_identical_range_is_history_valid_and_comparable(self):
        self.observe_range(ancestry=RANGE_ANCESTRY_IDENTICAL)
        summary = self.bundle().committed_range
        self.assertTrue(summary.history_valid)
        self.assertTrue(summary.comparison_grade)

    def test_a_truncated_range_is_incomplete_but_still_comparable(self):
        """Truncation is about paths that are missing, not the ones that are here."""
        self.observe_range(
            CommittedChange(kind="modified", path="a.py", status="M"),
            coverage=RANGE_COVERAGE_INCOMPLETE,
            reason="output_truncated",
        )
        bundle = self.bundle()
        self.assertIn(LIMIT_RANGE_INCOMPLETE, bundle.limitations)
        self.assertTrue(bundle.committed_range.comparison_grade)
        self.assertEqual(bundle.committed_range.limitation, "output_truncated")


class TheDomainsStayApart(RangeBundleFixture):
    def test_a_committed_observation_keeps_its_domain(self):
        self.observe_range(CommittedChange(kind="created", path="a.py", status="A"))
        (observation,) = self.bundle().observations
        self.assertEqual(observation.domain, OBSERVATION_DOMAIN_COMMITTED_RANGE)
        self.assertEqual(observation.path, "a.py")
        self.assertEqual(observation.change_kind, CHANGE_CREATED)

    def test_a_worktree_observation_keeps_being_a_worktree_observation(self):
        self.observe(worktree("a.py", CHANGE_MODIFIED, status=" M"), worktree_coverage())
        (observation,) = self.bundle().observations
        self.assertEqual(observation.domain, OBSERVATION_DOMAIN_WORKTREE)

    def test_a_pre_pr5_row_reads_as_a_worktree_row(self):
        """The absence is not a third domain; it is what those rows are."""
        self.assertEqual(
            observation_domain(worktree("a.py")), OBSERVATION_DOMAIN_WORKTREE
        )
        self.assertEqual(observation_domain(None), OBSERVATION_DOMAIN_WORKTREE)

    def test_the_same_path_in_both_domains_is_two_observations(self):
        self.observe_range(CommittedChange(kind="created", path="a.py", status="A"))
        self.observe(worktree("a.py", CHANGE_MODIFIED, status=" M"), worktree_coverage())

        bundle = self.bundle()
        self.assertEqual(len(bundle.observations), 2, "the domains were deduplicated")
        self.assertEqual(
            sorted(o.domain for o in bundle.observations),
            [OBSERVATION_DOMAIN_COMMITTED_RANGE, OBSERVATION_DOMAIN_WORKTREE],
        )
        group = {g.path: g for g in bundle.relationships}["a.py"]
        self.assertEqual(
            group.observation_domains,
            (OBSERVATION_DOMAIN_COMMITTED_RANGE, OBSERVATION_DOMAIN_WORKTREE),
        )
        self.assertEqual(
            sorted(group.observed_kinds), [CHANGE_CREATED, CHANGE_MODIFIED],
            "one domain's fact overwrote the other's",
        )

    def test_committed_only(self):
        self.observe_range(CommittedChange(kind="created", path="only-committed.py", status="A"))
        group = self.groups()["only-committed.py"]
        self.assertEqual(group.relationship, RELATIONSHIP_OBSERVED_ONLY)
        self.assertEqual(group.observation_domains, (OBSERVATION_DOMAIN_COMMITTED_RANGE,))

    def test_uncommitted_only(self):
        self.observe(worktree("only-dirty.py", CHANGE_MODIFIED, status=" M"))
        self.observe_range()
        group = self.groups()["only-dirty.py"]
        self.assertEqual(group.observation_domains, (OBSERVATION_DOMAIN_WORKTREE,))

    def test_a_name_status_token_is_never_read_through_the_porcelain_table(self):
        """Two alphabets, told apart by domain rather than by inspection.

        ``R080`` is not two characters so it cannot collide today. That is an
        accident of Git's formatting, not a guarantee, so the range reads its
        label and the porcelain table is never asked.
        """
        self.observe_range(
            CommittedChange(kind="renamed", path="new.py", previous_path="old.py", status="R080")
        )
        group = self.groups()["new.py"]
        self.assertEqual(group.observed_kinds, (CHANGE_RENAMED,))


class ChronologyDoesNotManufactureConflict(RangeBundleFixture):
    def test_a_clean_range_can_agree(self):
        self.observe_range(CommittedChange(kind="modified", path="src/foo.py", status="M"))
        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py"))
        group = self.groups()["src/foo.py"]
        self.assertEqual(group.operation_agreement, OPERATION_AGREED)
        self.assertEqual(group.relationship, RELATIONSHIP_PATH_AGREED)

    def test_a_clean_range_can_conflict(self):
        """The claim says created; the machine says the path was deleted."""
        self.observe_range(CommittedChange(kind="deleted", path="src/foo.py", status="D"))
        self.claim(ClaimSubmission(operation=CLAIM_CREATED, path="src/foo.py"))
        group = self.groups()["src/foo.py"]
        self.assertEqual(group.operation_agreement, OPERATION_DIFFERS)
        self.assertEqual(group.relationship, RELATIONSHIP_CLAIM_CONFLICT)
        self.assertTrue(group.path_agreement, "a conflict still agrees on the path")

    def test_committed_modified_then_worktree_deleted_is_not_a_conflict(self):
        """The sequence this rule exists for. The claim was true when it was made."""
        self.observe_range(CommittedChange(kind="modified", path="src/foo.py", status="M"))
        self.observe(worktree("src/foo.py", CHANGE_DELETED, status=" D"), worktree_coverage())
        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py"))

        group = self.groups()["src/foo.py"]
        self.assertEqual(
            group.operation_agreement, OPERATION_AGREED,
            "a later deletion contradicted a modification it did not disprove",
        )
        self.assertNotEqual(group.relationship, RELATIONSHIP_CLAIM_CONFLICT)

    def test_committed_created_then_worktree_modified_is_not_a_conflict(self):
        self.observe_range(CommittedChange(kind="created", path="src/foo.py", status="A"))
        self.observe(worktree("src/foo.py", CHANGE_MODIFIED, status=" M"), worktree_coverage())
        self.claim(ClaimSubmission(operation=CLAIM_CREATED, path="src/foo.py"))

        group = self.groups()["src/foo.py"]
        self.assertEqual(group.operation_agreement, OPERATION_AGREED)

    def test_a_contradiction_within_one_domain_still_stands(self):
        """One instant, one HEAD: both cannot be true, and the conflict is real."""
        self.observe(worktree("src/foo.py", CHANGE_DELETED, status=" D"), worktree_coverage())
        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py"))
        group = self.groups()["src/foo.py"]
        self.assertEqual(group.operation_agreement, OPERATION_DIFFERS)
        self.assertEqual(group.relationship, RELATIONSHIP_CLAIM_CONFLICT)

    def test_both_domains_contradicting_is_still_a_conflict(self):
        self.observe_range(CommittedChange(kind="deleted", path="src/foo.py", status="D"))
        self.observe(worktree("src/foo.py", CHANGE_DELETED, status=" D"), worktree_coverage())
        self.claim(ClaimSubmission(operation=CLAIM_CREATED, path="src/foo.py"))
        group = self.groups()["src/foo.py"]
        self.assertEqual(group.operation_agreement, OPERATION_DIFFERS)

    def test_a_rename_is_matched_on_both_paths_inside_the_range(self):
        self.observe_range(
            CommittedChange(
                kind="renamed", path="dst.py", previous_path="src.py", status="R100"
            )
        )
        self.claim(
            ClaimSubmission(operation=CLAIM_RENAMED, path="src.py", to_path="dst.py")
        )
        self.assertEqual(self.groups()["dst.py"].operation_agreement, OPERATION_AGREED)

    def test_a_rename_from_a_different_source_disagrees(self):
        self.observe_range(
            CommittedChange(
                kind="renamed", path="dst.py", previous_path="elsewhere.py", status="R100"
            )
        )
        self.claim(
            ClaimSubmission(operation=CLAIM_RENAMED, path="src.py", to_path="dst.py")
        )
        self.assertEqual(self.groups()["dst.py"].operation_agreement, OPERATION_DIFFERS)


class ADirtyBoundaryNeverContradicts(RangeBundleFixture):
    """The conservative rule, from every direction it could be got wrong.

    A change that existed before the worker started can be committed inside the
    range. Nothing in the record distinguishes it from the worker's own work, so
    nothing derived from it may call a claim false.
    """

    def unclean(self, quality):
        self.observe_range(
            CommittedChange(kind="deleted", path="src/foo.py", status="D"),
            quality=quality,
        )
        self.claim(ClaimSubmission(operation=CLAIM_CREATED, path="src/foo.py"))
        return self.groups()["src/foo.py"]

    def test_a_dirty_boundary_stays_unknown(self):
        group = self.unclean(BOUNDARY_DIRTY)
        self.assertEqual(group.operation_agreement, OPERATION_UNKNOWN)
        self.assertNotEqual(group.relationship, RELATIONSHIP_CLAIM_CONFLICT)

    def test_an_incomplete_boundary_stays_unknown(self):
        self.assertEqual(
            self.unclean(BOUNDARY_INCOMPLETE).operation_agreement, OPERATION_UNKNOWN
        )

    def test_an_unavailable_boundary_stays_unknown(self):
        self.assertEqual(
            self.unclean(BOUNDARY_UNAVAILABLE).operation_agreement, OPERATION_UNKNOWN
        )

    def test_a_dirty_boundary_still_shows_what_changed(self):
        """Withheld from the argument, not from the record."""
        self.observe_range(
            CommittedChange(kind="deleted", path="src/foo.py", status="D"),
            quality=BOUNDARY_DIRTY,
        )
        self.claim(ClaimSubmission(operation=CLAIM_CREATED, path="src/foo.py"))
        group = self.groups()["src/foo.py"]
        self.assertTrue(group.path_agreement)
        self.assertEqual(group.observed_kinds, (CHANGE_DELETED,))
        self.assertEqual(group.observation_domains, (OBSERVATION_DOMAIN_COMMITTED_RANGE,))

    def test_a_dirty_boundary_is_named_as_a_limitation(self):
        self.observe_range(quality=BOUNDARY_DIRTY)
        self.assertIn(LIMIT_RANGE_BOUNDARY_NOT_CLEAN, self.bundle().limitations)

    def test_a_clean_boundary_carries_no_such_limitation(self):
        self.observe_range(quality=BOUNDARY_CLEAN_COMPLETE)
        self.assertNotIn(LIMIT_RANGE_BOUNDARY_NOT_CLEAN, self.bundle().limitations)

    def test_a_dirty_range_does_not_silence_the_worktree_domain(self):
        """The worktree observation is unaffected and may still contradict."""
        self.observe_range(
            CommittedChange(kind="created", path="src/foo.py", status="A"),
            quality=BOUNDARY_DIRTY,
        )
        self.observe(worktree("src/foo.py", CHANGE_DELETED, status=" D"), worktree_coverage())
        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py"))
        self.assertEqual(
            self.groups()["src/foo.py"].operation_agreement, OPERATION_DIFFERS
        )

    def test_a_diverged_range_never_contradicts(self):
        self.observe_range(
            ancestry=RANGE_ANCESTRY_DIVERGED,
            coverage=RANGE_COVERAGE_UNAVAILABLE,
            reason="history_diverged",
            capture_state="unavailable",
        )
        self.claim(ClaimSubmission(operation=CLAIM_CREATED, path="src/foo.py"))
        self.assertEqual(
            self.groups()["src/foo.py"].operation_agreement, OPERATION_UNKNOWN
        )


class TheFingerprint(RangeBundleFixture):
    """Every assembly-relevant range fact binds, and nothing outside does."""

    def fingerprint(self):
        return self.bundle().input_fingerprint

    def test_a_repeated_read_is_identical(self):
        self.observe_range(CommittedChange(kind="created", path="a.py", status="A"))
        first = self.fingerprint()
        self.assertEqual(first, self.fingerprint())

    def test_it_survives_a_restart(self):
        self.observe_range(CommittedChange(kind="created", path="a.py", status="A"))
        first = self.fingerprint()
        self.store.close()
        self.store = TaskStore(self.config)
        self.assertEqual(self.store.evidence_bundle(self.task_id, 1).input_fingerprint, first)

    def test_it_survives_the_repository_being_deleted(self):
        """Assembly reads rows, never the project — so there is nothing to lose."""
        import shutil

        self.observe_range(CommittedChange(kind="created", path="a.py", status="A"))
        first = self.bundle()
        shutil.rmtree(self.root)
        second = self.store.evidence_bundle(self.task_id, 1)
        self.assertEqual(second.input_fingerprint, first.input_fingerprint)
        self.assertEqual(second.to_dict(), first.to_dict())

    def test_the_assembler_version_is_three(self):
        self.assertEqual(ASSEMBLER_VERSION, 3)
        self.observe_range()
        self.assertEqual(self.bundle().assembler_version, 3)


class TheFingerprintMoves(unittest.TestCase):
    """Each range fact, changed one at a time, against a fixed everything else.

    Built through :func:`input_fingerprint` directly rather than through a store,
    because the point is which *inputs* bind — and one store per variant would be
    comparing two bundles rather than two inputs.
    """

    def summary(self, **kwargs):
        defaults = dict(
            recorded=True,
            event_sequence=7,
            baseline_revision=BASE,
            target_revision=TARGET,
            boundary_quality=RANGE_BOUNDARY_CLEAN,
            ancestry=RANGE_ANCESTRY_LINEAR,
            coverage=RANGE_COVERAGE_COMPLETE,
            limitation=None,
        )
        defaults.update(kwargs)
        return evidence_module.CommittedRangeSummary(**defaults)

    def observation(self, **kwargs):
        defaults = dict(
            event_sequence=7,
            evidence_index=4,
            path="a.py",
            change_kind=CHANGE_CREATED,
            change_status="A",
            domain=OBSERVATION_DOMAIN_COMMITTED_RANGE,
        )
        defaults.update(kwargs)
        return evidence_module.MachineObservation(**defaults)

    def value(self, *, summary=None, observations=None):
        return evidence_module.input_fingerprint(
            task_id="t",
            turn_number=1,
            attribution="exact",
            bound=None,
            claims=(),
            observations=observations if observations is not None else (self.observation(),),
            ingestion=evidence_module.IngestionSummary(state="complete"),
            committed_range=summary if summary is not None else self.summary(),
        )

    def test_the_baseline_binds(self):
        self.assertNotEqual(self.value(), self.value(summary=self.summary(baseline_revision="c" * 40)))

    def test_the_target_binds(self):
        self.assertNotEqual(self.value(), self.value(summary=self.summary(target_revision="c" * 40)))

    def test_the_history_relation_binds(self):
        self.assertNotEqual(
            self.value(), self.value(summary=self.summary(ancestry=RANGE_ANCESTRY_DIVERGED))
        )

    def test_the_boundary_quality_binds(self):
        self.assertNotEqual(
            self.value(), self.value(summary=self.summary(boundary_quality=BOUNDARY_DIRTY))
        )

    def test_the_coverage_binds(self):
        self.assertNotEqual(
            self.value(), self.value(summary=self.summary(coverage=RANGE_COVERAGE_INCOMPLETE))
        )

    def test_the_limitation_binds(self):
        self.assertNotEqual(
            self.value(), self.value(summary=self.summary(limitation="output_truncated"))
        )

    def test_whether_anything_was_recorded_binds(self):
        """Nobody looked, and looked and found nothing, must not hash alike."""
        self.assertNotEqual(
            self.value(summary=evidence_module.CommittedRangeSummary()),
            self.value(summary=self.summary(
                baseline_revision=None, target_revision=None,
                boundary_quality=None, ancestry=None, coverage=None,
                event_sequence=None,
            )),
        )

    def test_the_observation_domain_binds(self):
        """The same path and kind in the two domains are different inputs."""
        self.assertNotEqual(
            self.value(),
            self.value(observations=(self.observation(domain=OBSERVATION_DOMAIN_WORKTREE),)),
        )

    def test_the_range_operation_binds(self):
        self.assertNotEqual(
            self.value(),
            self.value(observations=(self.observation(change_kind=CHANGE_DELETED),)),
        )

    def test_the_path_binds(self):
        self.assertNotEqual(
            self.value(), self.value(observations=(self.observation(path="b.py"),))
        )

    def test_the_rename_source_binds(self):
        self.assertNotEqual(
            self.value(),
            self.value(observations=(self.observation(
                change_kind=CHANGE_RENAMED, previous_path="old.py"
            ),)),
        )
        self.assertNotEqual(
            self.value(observations=(self.observation(
                change_kind=CHANGE_RENAMED, previous_path="old.py"
            ),)),
            self.value(observations=(self.observation(
                change_kind=CHANGE_RENAMED, previous_path="other.py"
            ),)),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
