"""M2K PR3 — operation agreement, conflict, and what they are not.

PR2 could only ever say ``operation_agreement: unknown``, because the stored
observation was the constant word "changed". PR3 gives the machine side real
semantics, and this file is where the consequences are pinned.

The one sentence to keep in mind while reading it: **a conflict is a
disagreement between two records, and nothing more.** It is not a task failure,
not an acceptance failure, not dishonesty, and not a judgement about a worker. A
worker that modified a file and then deleted it produced a conflict and did
nothing wrong. Every test below is written so that the day somebody tries to
promote `claim_conflict` into a verdict, something here breaks.
"""

from __future__ import annotations

import json
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
from cofferdam.workstation.tasks.evidence import (
    ASSEMBLER_VERSION,
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    LIMIT_OBSERVATIONS_INCOMPLETE,
    OPERATION_AGREED,
    OPERATION_AGREEMENTS,
    OPERATION_DIFFERS,
    OPERATION_UNKNOWN,
    RELATIONSHIP_CLAIM_CONFLICT,
    RELATIONSHIP_CLAIM_ONLY,
    RELATIONSHIP_OBSERVED_ONLY,
    RELATIONSHIP_PATH_AGREED,
    operation_agreement,
)
from cofferdam.workstation.tasks.models import (
    CHANGE_CREATED,
    CHANGE_DELETED,
    CHANGE_KINDS,
    CHANGE_MODIFIED,
    CHANGE_RENAMED,
    CHANGE_UNKNOWN,
    EVIDENCE_ARTIFACT,
    EVIDENCE_FILE,
    EVIDENCE_GIT_OBSERVED,
    EvidenceReference,
)
from cofferdam.workstation.tasks.store import TaskStore, _TurnClose


def observation(path, kind=None, previous=None, status=None):
    """An eligible machine observation, PR3-shaped when ``kind`` is given."""
    return EvidenceReference(
        evidence_type=EVIDENCE_FILE,
        source=EVIDENCE_GIT_OBSERVED,
        identifier=path,
        operation="git status",
        result="changed",
        change_kind=kind,
        previous_identifier=previous,
        change_status=status,
        observed_at="2026-08-14T00:00:00Z",
    )


def legacy_observation(path):
    """Exactly what a pre-PR3 build wrote: a path, and no operation at all."""
    return EvidenceReference(
        evidence_type=EVIDENCE_FILE,
        source=EVIDENCE_GIT_OBSERVED,
        identifier=path,
        operation="git status",
        result="changed",
        observed_at="2026-08-14T00:00:00Z",
    )


def coverage(result=COVERAGE_COMPLETE):
    return EvidenceReference(
        evidence_type=EVIDENCE_ARTIFACT,
        source=EVIDENCE_GIT_OBSERVED,
        identifier=None,
        operation="git status",
        result=result,
        observed_at="2026-08-14T00:00:00Z",
    )


class TheMatrix(unittest.TestCase):
    """The table, asked directly. One helper answers; nothing else decides."""

    def test_every_pair_returns_one_of_exactly_three_answers(self):
        for claim in (CLAIM_CREATED, CLAIM_MODIFIED, CLAIM_DELETED, CLAIM_RENAMED):
            for kind in CHANGE_KINDS:
                with self.subTest(claim=claim, kind=kind):
                    self.assertIn(operation_agreement(claim, kind), OPERATION_AGREEMENTS)

    def test_the_agreeing_pairs(self):
        for claim, kind in (
            (CLAIM_CREATED, CHANGE_CREATED),
            (CLAIM_MODIFIED, CHANGE_MODIFIED),
            (CLAIM_DELETED, CHANGE_DELETED),
        ):
            with self.subTest(claim=claim, kind=kind):
                self.assertEqual(operation_agreement(claim, kind), OPERATION_AGREED)

    def test_the_incompatible_pairs(self):
        """Each of these cannot describe one path's state against one HEAD."""
        for claim, kind in (
            (CLAIM_CREATED, CHANGE_DELETED),
            (CLAIM_MODIFIED, CHANGE_DELETED),
            (CLAIM_DELETED, CHANGE_CREATED),
            (CLAIM_DELETED, CHANGE_MODIFIED),
        ):
            with self.subTest(claim=claim, kind=kind):
                self.assertEqual(operation_agreement(claim, kind), OPERATION_DIFFERS)

    def test_created_versus_modified_is_unknown_not_a_conflict(self):
        """Ordinary sequences produce this pair; it must not manufacture conflict.

        A worker that created a file and then edited it truthfully says
        "created", and Git reports whichever of the two the state against HEAD
        supports. Calling that a contradiction would invent conflicts out of
        normal work.
        """
        self.assertEqual(
            operation_agreement(CLAIM_CREATED, CHANGE_MODIFIED), OPERATION_UNKNOWN
        )
        self.assertEqual(
            operation_agreement(CLAIM_MODIFIED, CHANGE_CREATED), OPERATION_UNKNOWN
        )

    def test_an_unknown_machine_kind_is_never_evidence_either_way(self):
        for claim in (CLAIM_CREATED, CLAIM_MODIFIED, CLAIM_DELETED, CLAIM_RENAMED):
            with self.subTest(claim=claim):
                self.assertEqual(
                    operation_agreement(claim, CHANGE_UNKNOWN), OPERATION_UNKNOWN
                )

    def test_a_legacy_observation_carries_no_kind_and_stays_unknown(self):
        for claim in (CLAIM_CREATED, CLAIM_MODIFIED, CLAIM_DELETED, CLAIM_RENAMED):
            with self.subTest(claim=claim):
                self.assertEqual(operation_agreement(claim, None), OPERATION_UNKNOWN)

    def test_rename_is_not_answered_by_the_table(self):
        """A rename is a fact about two paths; one cell cannot hold it."""
        for kind in CHANGE_KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(
                    operation_agreement(CLAIM_RENAMED, kind), OPERATION_UNKNOWN
                )

    def test_nonsense_input_is_unknown_rather_than_an_exception(self):
        for claim, kind in ((None, None), (1, 2), ("", ""), ("teleported", "vanished")):
            with self.subTest(claim=claim, kind=kind):
                self.assertEqual(operation_agreement(claim, kind), OPERATION_UNKNOWN)


class BundleFixture(unittest.TestCase):
    def setUp(self):
        from cofferdam.workstation.config import load_config

        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr3-ops-")
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
        task_id = row.task_id
        for state in ("queued", "starting", "running"):
            self.store.transition(
                task_id, state, event_type="task_" + state,
                actor="system", source="cofferdam",
            )
        self.store.open_turn(
            task_id, provider="validation", source="internal_test",
            started_at="2026-08-14T00:00:00Z",
        )
        return task_id

    def observe(self, *references, text="Cofferdam checked the project itself."):
        return self.store.append_event(
            self.task_id, "progress", actor="system", source="cofferdam",
            text=text, evidence=references,
        )

    def claim(self, *submissions):
        for submission in submissions:
            target = self.root / submission.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x", encoding="utf-8")
        return self.store.record_change_claims(
            self.task_id, submissions, project_root=self.root, turn_number=1
        )

    def bundle(self):
        self.store.transition(
            self.task_id, "ready_for_followup", event_type="turn_complete",
            actor="adapter", source="adapter",
            close_turn=_TurnClose(outcome="completed", completed_at="2026-08-14T00:05:00Z"),
        )
        return self.store.evidence_bundle(self.task_id, 1)

    def groups(self):
        return {g.path: g for g in self.bundle().relationships}


class OperationAgreementInBundles(BundleFixture):
    def test_agreement_is_published_as_true(self):
        self.observe(observation("src/foo.py", CHANGE_MODIFIED), coverage())
        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py"))
        group = self.groups()["src/foo.py"]
        self.assertEqual(group.relationship, RELATIONSHIP_PATH_AGREED)
        self.assertTrue(group.path_agreement)
        self.assertEqual(group.operation_agreement, OPERATION_AGREED)
        self.assertEqual(group.observed_kinds, (CHANGE_MODIFIED,))

    def test_disagreement_is_published_as_false_and_becomes_a_conflict(self):
        """claim modified, machine deleted: the same path, incompatible states."""
        self.observe(observation("src/foo.py", CHANGE_DELETED), coverage())
        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py"))
        group = self.groups()["src/foo.py"]
        self.assertEqual(group.relationship, RELATIONSHIP_CLAIM_CONFLICT)
        self.assertEqual(group.operation_agreement, OPERATION_DIFFERS)
        # Path agreement is still TRUE: both records name this file. That is
        # exactly why the two questions are separate fields.
        self.assertTrue(group.path_agreement)

    def test_legacy_evidence_stays_unknown_and_never_conflicts(self):
        self.observe(legacy_observation("src/foo.py"))
        self.claim(ClaimSubmission(operation=CLAIM_DELETED, path="src/foo.py"))
        group = self.groups()["src/foo.py"]
        self.assertEqual(group.relationship, RELATIONSHIP_PATH_AGREED)
        self.assertEqual(group.operation_agreement, OPERATION_UNKNOWN)
        self.assertEqual(group.observed_kinds, ())

    def test_an_unknown_machine_kind_never_conflicts(self):
        self.observe(observation("src/foo.py", CHANGE_UNKNOWN), coverage())
        self.claim(ClaimSubmission(operation=CLAIM_DELETED, path="src/foo.py"))
        group = self.groups()["src/foo.py"]
        self.assertEqual(group.relationship, RELATIONSHIP_PATH_AGREED)
        self.assertEqual(group.operation_agreement, OPERATION_UNKNOWN)

    def test_a_contradiction_wins_over_a_simultaneous_agreement(self):
        """Conservative resolution: the disagreement is the fact to surface."""
        self.observe(
            observation("src/foo.py", CHANGE_MODIFIED),
            observation("src/foo.py", CHANGE_DELETED),
            coverage(),
        )
        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py"))
        group = self.groups()["src/foo.py"]
        self.assertEqual(group.operation_agreement, OPERATION_DIFFERS)
        self.assertEqual(group.relationship, RELATIONSHIP_CLAIM_CONFLICT)

    def test_one_unknown_among_agreements_leaves_the_group_unknown(self):
        self.observe(
            observation("src/foo.py", CHANGE_MODIFIED),
            observation("src/foo.py", CHANGE_UNKNOWN),
            coverage(),
        )
        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py"))
        self.assertEqual(
            self.groups()["src/foo.py"].operation_agreement, OPERATION_UNKNOWN
        )


class AbsenceIsNotConflict(BundleFixture):
    def test_a_claim_with_no_observation_is_claim_only(self):
        self.observe(coverage())
        self.claim(ClaimSubmission(operation=CLAIM_DELETED, path="src/foo.py"))
        group = self.groups()["src/foo.py"]
        self.assertEqual(group.relationship, RELATIONSHIP_CLAIM_ONLY)
        self.assertEqual(group.operation_agreement, OPERATION_UNKNOWN)

    def test_an_observation_with_no_claim_is_observed_only(self):
        self.observe(observation("src/surprise.py", CHANGE_DELETED), coverage())
        group = self.groups()["src/surprise.py"]
        self.assertEqual(group.relationship, RELATIONSHIP_OBSERVED_ONLY)
        self.assertEqual(group.operation_agreement, OPERATION_UNKNOWN)

    def test_a_truncated_observation_set_does_not_turn_absence_into_conflict(self):
        """The claimed path is unobserved *because the set was cut short*."""
        self.observe(observation("src/other.py", CHANGE_MODIFIED), coverage(COVERAGE_PARTIAL))
        self.claim(ClaimSubmission(operation=CLAIM_DELETED, path="src/foo.py"))
        bundle = self.bundle()
        groups = {g.path: g for g in bundle.relationships}
        self.assertEqual(groups["src/foo.py"].relationship, RELATIONSHIP_CLAIM_ONLY)
        self.assertFalse(bundle.machine_observations_complete)
        self.assertIn(LIMIT_OBSERVATIONS_INCOMPLETE, bundle.limitations)

    def test_no_conflict_appears_anywhere_without_explicit_incompatibility(self):
        self.observe(
            legacy_observation("a.py"),
            observation("b.py", CHANGE_UNKNOWN),
            observation("c.py", CHANGE_MODIFIED),
            coverage(),
        )
        self.claim(
            ClaimSubmission(operation=CLAIM_DELETED, path="a.py"),
            ClaimSubmission(operation=CLAIM_DELETED, path="b.py"),
            ClaimSubmission(operation=CLAIM_MODIFIED, path="c.py"),
            ClaimSubmission(operation=CLAIM_MODIFIED, path="d.py"),
        )
        payload = json.dumps(self.bundle().to_dict())
        self.assertNotIn(RELATIONSHIP_CLAIM_CONFLICT, payload)


class RenameSemantics(BundleFixture):
    def _rename_claim(self, source="old.py", destination="new.py"):
        return ClaimSubmission(
            operation=CLAIM_RENAMED, path=source, to_path=destination
        )

    def test_an_exact_rename_agrees_on_both_sides(self):
        self.observe(observation("new.py", CHANGE_RENAMED, previous="old.py"), coverage())
        self.claim(self._rename_claim())
        groups = self.groups()
        self.assertEqual(groups["new.py"].relationship, RELATIONSHIP_PATH_AGREED)
        self.assertEqual(groups["new.py"].operation_agreement, OPERATION_AGREED)

    def test_a_wrong_destination_leaves_the_claimed_destination_unmatched(self):
        """Machine renamed old.py -> other.py; the claim said old.py -> new.py."""
        self.observe(observation("other.py", CHANGE_RENAMED, previous="old.py"), coverage())
        self.claim(self._rename_claim())
        groups = self.groups()
        self.assertEqual(groups["new.py"].relationship, RELATIONSHIP_CLAIM_ONLY)
        self.assertEqual(groups["other.py"].relationship, RELATIONSHIP_OBSERVED_ONLY)

    def test_a_wrong_source_at_the_same_destination_is_a_conflict(self):
        """Both records rename *into* new.py, and disagree about from where."""
        self.observe(observation("new.py", CHANGE_RENAMED, previous="elsewhere.py"), coverage())
        self.claim(self._rename_claim())
        group = self.groups()["new.py"]
        self.assertEqual(group.operation_agreement, OPERATION_DIFFERS)
        self.assertEqual(group.relationship, RELATIONSHIP_CLAIM_CONFLICT)

    def test_a_rename_observation_without_a_source_stays_unknown(self):
        """Half a rename proves nothing about the other half."""
        self.observe(observation("new.py", CHANGE_RENAMED), coverage())
        self.claim(self._rename_claim())
        group = self.groups()["new.py"]
        self.assertEqual(group.operation_agreement, OPERATION_UNKNOWN)
        self.assertNotEqual(group.relationship, RELATIONSHIP_CLAIM_CONFLICT)

    def test_legacy_rename_unaware_evidence_stays_unknown(self):
        self.observe(legacy_observation("new.py"))
        self.claim(self._rename_claim())
        group = self.groups()["new.py"]
        self.assertEqual(group.operation_agreement, OPERATION_UNKNOWN)
        self.assertNotEqual(group.relationship, RELATIONSHIP_CLAIM_CONFLICT)

    def test_source_only_generic_evidence_does_not_confirm_a_rename(self):
        self.observe(observation("old.py", CHANGE_DELETED), coverage())
        self.claim(self._rename_claim())
        groups = self.groups()
        # The source was observed deleted, which is consistent with a rename but
        # does not establish one — the destination half is unobserved.
        self.assertEqual(groups["new.py"].relationship, RELATIONSHIP_CLAIM_ONLY)
        self.assertNotEqual(groups["old.py"].operation_agreement, OPERATION_AGREED)

    def test_destination_only_generic_evidence_does_not_confirm_a_rename(self):
        self.observe(observation("new.py", CHANGE_CREATED), coverage())
        self.claim(self._rename_claim())
        group = self.groups()["new.py"]
        self.assertEqual(group.operation_agreement, OPERATION_UNKNOWN)
        self.assertNotEqual(group.relationship, RELATIONSHIP_CLAIM_CONFLICT)

    def test_neither_side_observed_is_two_gaps(self):
        self.observe(coverage())
        self.claim(self._rename_claim())
        groups = self.groups()
        self.assertEqual(groups["old.py"].relationship, RELATIONSHIP_CLAIM_ONLY)
        self.assertEqual(groups["new.py"].relationship, RELATIONSHIP_CLAIM_ONLY)


class ConflictIsNotAVerdict(BundleFixture):
    def test_a_conflicting_bundle_carries_no_judgement_vocabulary(self):
        self.observe(observation("src/foo.py", CHANGE_DELETED), coverage())
        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py"))
        payload = json.dumps(self.bundle().to_dict()).lower()
        for forbidden in (
            "verdict", "confidence", "risk", "trusted", "lying", "dishonest",
            "failure", "failed", "success", "pass",
        ):
            self.assertNotIn(forbidden, payload, forbidden)

    def test_a_conflict_does_not_mark_the_claim_verified_or_false(self):
        self.observe(observation("src/foo.py", CHANGE_DELETED), coverage())
        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py"))
        published = self.bundle().to_dict()
        self.assertFalse(published["claims"][0]["verified"])
        self.assertEqual(published["claims"][0]["source"], "adapter_reported")
        self.assertEqual(published["claims"][0]["operation"], CLAIM_MODIFIED)

    def test_a_conflict_does_not_mutate_the_observation(self):
        self.observe(observation("src/foo.py", CHANGE_DELETED), coverage())
        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py"))
        published = self.bundle().to_dict()
        machine = published["observations"][0]
        self.assertEqual(machine["source"], EVIDENCE_GIT_OBSERVED)
        self.assertTrue(machine["verified"])
        self.assertEqual(machine["change_kind"], CHANGE_DELETED)

    def test_the_bundle_reports_the_new_assembler_version(self):
        self.observe(coverage())
        self.assertEqual(self.bundle().assembler_version, 2)
        self.assertEqual(ASSEMBLER_VERSION, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
