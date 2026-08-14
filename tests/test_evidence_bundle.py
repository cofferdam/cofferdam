"""M2K PR2 — the derived evidence bundle.

Read the vocabulary assertions first; they are the ones that matter in a year.
``path_agreed`` must never become ``agreed``, ``operation_agreement`` must stay
``unknown`` while today's observation is a bare "this path changed", and
``claim_only`` must never be rendered as a failure. Those are not style choices:
each one is a sentence somebody will read off a phone and act on.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.claims import (
    CLAIM_MODIFIED,
    CLAIM_RENAMED,
    ClaimSubmission,
)
from cofferdam.workstation.tasks.evidence import (
    ASSEMBLER_VERSION,
    ATTRIBUTION_EXACT,
    ATTRIBUTION_LEGACY_UNKNOWN,
    BUNDLE_VERSION,
    COMPLETENESS_COMPLETE,
    COMPLETENESS_INCOMPLETE,
    COMPLETENESS_INGESTION_MISSING,
    COMPLETENESS_LEGACY_UNKNOWN,
    LIMIT_INGESTION_MISSING,
    LIMIT_LEGACY_TURN,
    LIMIT_UNSUPPORTED_OBSERVATION,
    MAX_GROUP_SOURCES,
    OPERATION_UNKNOWN,
    RELATIONSHIP_CLAIM_ONLY,
    RELATIONSHIP_OBSERVED_ONLY,
    RELATIONSHIP_PATH_AGREED,
    is_clean_tree_observation,
    is_git_head_observation,
    observation_path,
)
from cofferdam.workstation.tasks.models import (
    EVIDENCE_ADAPTER_REPORTED,
    EVIDENCE_ARTIFACT,
    EVIDENCE_COMMIT,
    EVIDENCE_FILE,
    EVIDENCE_GIT_OBSERVED,
    EvidenceReference,
)
from cofferdam.workstation.tasks.store import TaskStore, _TurnClose, _TurnDraft


def _open_store(home: Path) -> TaskStore:
    from cofferdam.workstation.config import load_config

    config = load_config(home)
    config.ensure_dirs()
    return TaskStore(config)


def path_observation(path: str) -> EvidenceReference:
    """Exactly what `git_evidence` emits for a changed path."""
    return EvidenceReference(
        evidence_type=EVIDENCE_FILE,
        source=EVIDENCE_GIT_OBSERVED,
        identifier=path,
        operation="git status",
        result="changed",
        observed_at="2026-08-14T00:00:00Z",
    )


def head_observation(commit: str = "abcdef012345") -> EvidenceReference:
    """Exactly what `git_evidence` emits for HEAD. Not a path."""
    return EvidenceReference(
        evidence_type=EVIDENCE_COMMIT,
        source=EVIDENCE_GIT_OBSERVED,
        identifier=commit,
        operation="rev-parse HEAD",
        result="main",
        observed_at="2026-08-14T00:00:00Z",
    )


def clean_observation() -> EvidenceReference:
    return EvidenceReference(
        evidence_type=EVIDENCE_ARTIFACT,
        source=EVIDENCE_GIT_OBSERVED,
        identifier=None,
        operation="git status",
        result="no files changed",
        observed_at="2026-08-14T00:00:00Z",
    )


class BundleFixture(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr2-bundle-")
        self.home = Path(self._temp.name)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.root = self.home / "project"
        self.root.mkdir()
        self.store = _open_store(self.home)
        self.task_id = self._make_task()

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self._temp.cleanup()

    def _make_task(self) -> str:
        row, _ = self.store.create_task(
            origin="pwa",
            adapter_id="validation",
            project_id="synth",
            prompt="do a thing",
            title="t",
        )
        return row.task_id

    def _move(self, state: str, **kwargs):
        return self.store.transition(
            self.task_id,
            state,
            event_type=kwargs.pop("event_type", "task_" + state),
            actor=kwargs.pop("actor", "system"),
            source=kwargs.pop("source", "cofferdam"),
            **kwargs,
        )

    def _run(self):
        for state in ("queued", "starting", "running"):
            self._move(state)

    def _open(self):
        return self.store.open_turn(
            self.task_id,
            provider="validation",
            source="internal_test",
            started_at="2026-08-14T00:00:00Z",
        )

    def _observe(self, *references: EvidenceReference) -> int:
        return self.store.append_event(
            self.task_id,
            "progress",
            actor="system",
            source="cofferdam",
            text="Cofferdam checked the project itself.",
            evidence=references,
        )

    def _claim(self, *submissions: ClaimSubmission, turn: int = 1):
        self.write("keep.txt", "x")
        return self.store.record_change_claims(
            self.task_id, submissions, project_root=self.root, turn_number=turn
        )

    def write(self, relative: str, data: str) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding="utf-8")
        return target

    def _followup(self):
        return self._move(
            "running",
            event_type="followup_received",
            actor="user",
            close_turn=_TurnClose(outcome="completed", completed_at="2026-08-14T00:10:00Z"),
            open_turn=_TurnDraft(
                provider="validation",
                source="internal_test",
                started_at="2026-08-14T00:10:00Z",
            ),
        )

    def bundle(self, turn: int = 1):
        return self.store.evidence_bundle(self.task_id, turn)


class ObservationEligibilityTests(unittest.TestCase):
    """The three git_observed shapes, kept apart by name."""

    def test_a_changed_path_observation_is_eligible(self):
        self.assertEqual(observation_path(path_observation("src/foo.py")), "src/foo.py")

    def test_the_head_observation_is_not_a_path(self):
        reference = head_observation()
        self.assertIsNone(observation_path(reference))
        self.assertTrue(is_git_head_observation(reference))

    def test_a_commit_id_is_never_matched_as_a_filename(self):
        """The specific trap: a twelve-character hex identifier is not a path."""
        self.assertIsNone(observation_path(head_observation("0123456789ab")))

    def test_the_clean_tree_statement_is_not_a_path(self):
        reference = clean_observation()
        self.assertIsNone(observation_path(reference))
        self.assertTrue(is_clean_tree_observation(reference))
        self.assertFalse(is_git_head_observation(reference))

    def test_operation_alone_does_not_make_it_eligible(self):
        """`git status` is shared by the path shape and the clean-tree shape."""
        self.assertEqual(clean_observation().operation, path_observation("a").operation)

    def test_an_adapter_reported_file_reference_is_not_an_observation(self):
        claimed = EvidenceReference(
            evidence_type=EVIDENCE_FILE,
            source=EVIDENCE_ADAPTER_REPORTED,
            identifier="src/foo.py",
            operation="git status",
            result="changed",
        )
        self.assertIsNone(observation_path(claimed))

    def test_an_unreadable_identifier_is_not_reshaped_into_a_path(self):
        for bad in ("/etc/passwd", "../escape", "", None, "a\\b"):
            with self.subTest(identifier=bad):
                self.assertIsNone(
                    observation_path(
                        EvidenceReference(
                            evidence_type=EVIDENCE_FILE,
                            source=EVIDENCE_GIT_OBSERVED,
                            identifier=bad,
                            operation="git status",
                            result="changed",
                        )
                    )
                )

    def test_a_result_other_than_changed_is_not_eligible(self):
        self.assertIsNone(
            observation_path(
                EvidenceReference(
                    evidence_type=EVIDENCE_FILE,
                    source=EVIDENCE_GIT_OBSERVED,
                    identifier="src/foo.py",
                    operation="git status",
                    result="deleted",
                )
            )
        )


class BasicMatchingTests(BundleFixture):
    def test_a_claim_and_a_same_path_observation_are_path_agreed(self):
        self._run()
        self._open()
        self._observe(path_observation("keep.txt"))
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="keep.txt"))
        bundle = self.bundle()
        self.assertEqual(len(bundle.relationships), 1)
        group = bundle.relationships[0]
        self.assertEqual(group.relationship, RELATIONSHIP_PATH_AGREED)
        self.assertTrue(group.path_agreement)

    def test_path_agreement_is_not_operation_agreement(self):
        """The sentence this milestone exists to keep honest."""
        self._run()
        self._open()
        self._observe(path_observation("keep.txt"))
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="keep.txt"))
        group = self.bundle().relationships[0]
        self.assertEqual(group.operation_agreement, OPERATION_UNKNOWN)

    def test_the_word_is_never_a_bare_agreed(self):
        self._run()
        self._open()
        self._observe(path_observation("keep.txt"))
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="keep.txt"))
        payload = json.dumps(self.bundle().to_dict())
        self.assertIn("path_agreed", payload)
        self.assertNotIn('"agreed"', payload)

    def test_a_claim_with_no_observation_is_claim_only(self):
        self._run()
        self._open()
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="keep.txt"))
        group = self.bundle().relationships[0]
        self.assertEqual(group.relationship, RELATIONSHIP_CLAIM_ONLY)
        self.assertFalse(group.path_agreement)
        self.assertEqual(group.observation_refs, ())

    def test_an_observation_with_no_claim_is_observed_only(self):
        self._run()
        self._open()
        self._observe(path_observation("src/surprise.py"))
        group = self.bundle().relationships[0]
        self.assertEqual(group.relationship, RELATIONSHIP_OBSERVED_ONLY)
        self.assertEqual(group.claim_ids, ())

    def test_observed_only_travels_with_the_completeness_state(self):
        """Interpretation depends on it, so it must be in the same payload."""
        self._run()
        self._open()
        self._observe(path_observation("src/surprise.py"))
        payload = self.bundle().to_dict()
        self.assertEqual(
            payload["relationships"][0]["relationship"], RELATIONSHIP_OBSERVED_ONLY
        )
        self.assertIn("state", payload["ingestion"])

    def test_multiple_paths_each_get_a_group(self):
        self._run()
        self._open()
        self.write("a.txt", "a")
        self.write("b.txt", "b")
        self._observe(path_observation("a.txt"), path_observation("c.txt"))
        self._claim(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="a.txt"),
            ClaimSubmission(operation=CLAIM_MODIFIED, path="b.txt"),
        )
        bundle = self.bundle()
        by_path = {group.path: group.relationship for group in bundle.relationships}
        self.assertEqual(
            by_path,
            {
                "a.txt": RELATIONSHIP_PATH_AGREED,
                "b.txt": RELATIONSHIP_CLAIM_ONLY,
                "c.txt": RELATIONSHIP_OBSERVED_ONLY,
            },
        )

    def test_relationships_are_ordered_by_path(self):
        self._run()
        self._open()
        for name in ("z.txt", "a.txt", "m.txt"):
            self.write(name, name)
        self._claim(
            *[ClaimSubmission(operation=CLAIM_MODIFIED, path=name)
              for name in ("z.txt", "a.txt", "m.txt")]
        )
        paths = [group.path for group in self.bundle().relationships]
        self.assertEqual(paths, sorted(paths))

    def test_no_conflict_relationship_is_ever_emitted(self):
        """Absence is not conflict, and nothing here can prove incompatibility."""
        self._run()
        self._open()
        self._observe(path_observation("src/other.py"))
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="keep.txt"))
        payload = json.dumps(self.bundle().to_dict())
        self.assertNotIn("conflict", payload)

    def test_the_bundle_carries_no_verdict_vocabulary(self):
        self._run()
        self._open()
        self._observe(path_observation("keep.txt"))
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="keep.txt"))
        payload = json.dumps(self.bundle().to_dict()).lower()
        for forbidden in (
            "pass", "fail", "success", "trusted", "lying", "confidence", "risk",
            "score", "verdict",
        ):
            self.assertNotIn(forbidden, payload, forbidden)

    def test_the_clean_tree_statement_is_reported_as_its_own_fact(self):
        self._run()
        self._open()
        self._observe(clean_observation())
        bundle = self.bundle()
        self.assertTrue(bundle.repository_reported_clean)
        self.assertEqual(bundle.observations, ())

    def test_a_head_observation_produces_no_limitation(self):
        """Known and deliberately excluded — nothing was lost."""
        self._run()
        self._open()
        self._observe(head_observation())
        bundle = self.bundle()
        self.assertEqual(bundle.observations, ())
        self.assertNotIn(LIMIT_UNSUPPORTED_OBSERVATION, bundle.limitations)

    def test_an_unsupported_git_shape_becomes_a_bounded_limitation(self):
        self._run()
        self._open()
        self._observe(
            EvidenceReference(
                evidence_type="test_summary",
                source=EVIDENCE_GIT_OBSERVED,
                identifier="something",
                operation="git bisect",
                result="?",
            )
        )
        bundle = self.bundle()
        self.assertIn(LIMIT_UNSUPPORTED_OBSERVATION, bundle.limitations)
        self.assertEqual(bundle.observations, ())


class ProvenanceTests(BundleFixture):
    def test_a_matched_claim_is_still_adapter_reported_and_unverified(self):
        self._run()
        self._open()
        self._observe(path_observation("keep.txt"))
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="keep.txt"))
        bundle = self.bundle()
        published = bundle.to_dict()["claims"][0]
        self.assertEqual(published["source"], EVIDENCE_ADAPTER_REPORTED)
        self.assertFalse(published["verified"])

    def test_matched_git_evidence_is_still_git_observed(self):
        self._run()
        self._open()
        self._observe(path_observation("keep.txt"))
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="keep.txt"))
        published = self.bundle().to_dict()["observations"][0]
        self.assertEqual(published["source"], EVIDENCE_GIT_OBSERVED)
        self.assertTrue(published["verified"])

    def test_assembly_mutates_no_source_record(self):
        self._run()
        self._open()
        self._observe(path_observation("keep.txt"))
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="keep.txt"))
        with sqlite3.connect(str(self.path)) as db:
            before = (
                db.execute("SELECT * FROM task_change_claims").fetchall(),
                db.execute("SELECT * FROM task_events").fetchall(),
                db.execute("SELECT * FROM task_artifacts").fetchall(),
            )
        for _ in range(5):
            self.bundle()
        with sqlite3.connect(str(self.path)) as db:
            after = (
                db.execute("SELECT * FROM task_change_claims").fetchall(),
                db.execute("SELECT * FROM task_events").fetchall(),
                db.execute("SELECT * FROM task_artifacts").fetchall(),
            )
        self.assertEqual(before, after)

    def test_the_bundle_carries_no_artifact_preview(self):
        self._run()
        self._open()
        self.write("sentinel_body.txt", "SENTINEL-FILE-BODY")
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="sentinel_body.txt"))
        payload = json.dumps(self.bundle().to_dict())
        self.assertNotIn("SENTINEL-FILE-BODY", payload)
        self.assertNotIn("preview", payload)

    def test_the_bundle_carries_no_provider_session_id(self):
        self._run()
        self._open()
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="keep.txt"))
        payload = json.dumps(self.bundle().to_dict())
        self.assertNotIn("provider_session_id", payload)
        self.assertNotIn("session", payload)

    def test_the_bundle_carries_no_built_at(self):
        self._run()
        self._open()
        payload = self.bundle().to_dict()
        self.assertNotIn("built_at", payload)


class TurnIsolationTests(BundleFixture):
    def _two_turns(self):
        self._run()
        self._open()
        self._observe(path_observation("turn_one.txt"))
        self.write("turn_one.txt", "1")
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="turn_one.txt"), turn=1)
        self._move("ready_for_followup", actor="adapter", source="adapter")
        self._followup()
        self._observe(path_observation("turn_two.txt"))
        self.write("turn_two.txt", "2")
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="turn_two.txt"), turn=2)

    def test_turn_one_observation_cannot_match_turn_two_claim(self):
        self._two_turns()
        second = self.bundle(2)
        self.assertEqual(
            [group.path for group in second.relationships if group.path_agreement],
            ["turn_two.txt"],
        )
        self.assertNotIn(
            "turn_one.txt", [group.path for group in second.relationships]
        )

    def test_turn_two_observation_cannot_match_turn_one_claim(self):
        self._two_turns()
        first = self.bundle(1)
        self.assertNotIn(
            "turn_two.txt", [group.path for group in first.relationships]
        )

    def test_the_same_path_in_adjacent_turns_stays_isolated(self):
        self._run()
        self._open()
        self.write("shared.txt", "1")
        self._observe(path_observation("shared.txt"))
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="shared.txt"), turn=1)
        self._move("ready_for_followup", actor="adapter", source="adapter")
        self._followup()
        # Turn two claims the same path but Cofferdam observed nothing in it.
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="shared.txt"), turn=2)
        first = {g.path: g.relationship for g in self.bundle(1).relationships}
        second = {g.path: g.relationship for g in self.bundle(2).relationships}
        self.assertEqual(first["shared.txt"], RELATIONSHIP_PATH_AGREED)
        self.assertEqual(second["shared.txt"], RELATIONSHIP_CLAIM_ONLY)

    def test_an_event_outside_the_window_does_not_reach_the_bundle(self):
        self._two_turns()
        before = self.bundle(1)
        self._observe(path_observation("turn_one.txt"))
        after = self.bundle(1)
        self.assertEqual(before.to_dict(), after.to_dict())

    def test_an_open_turn_owns_everything_after_its_floor(self):
        self._run()
        self._open()
        self._observe(path_observation("live.txt"))
        bundle = self.bundle(1)
        self.assertTrue(bundle.turn_open)
        self.assertIsNone(bundle.closed_through_event_sequence)
        self.assertEqual([o.path for o in bundle.observations], ["live.txt"])


class LegacyTurnTests(BundleFixture):
    def _legacy_turn(self):
        """A turn row with no bound — exactly what a v4 database leaves behind."""
        self._run()
        self._observe(path_observation("legacy.txt"))
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "INSERT INTO task_turns (task_id, turn_number, provider, source,"
                " started_at, completed_at, outcome) VALUES"
                " (?, 1, 'validation', 'internal_test', '2026-08-01T00:00:00Z',"
                " '2026-08-01T00:05:00Z', 'completed')",
                (self.task_id,),
            )
        self.store.close()
        self.store = _open_store(self.home)

    def test_a_legacy_turn_reports_legacy_unknown(self):
        self._legacy_turn()
        self.assertEqual(self.bundle().turn_attribution, ATTRIBUTION_LEGACY_UNKNOWN)

    def test_a_legacy_turn_receives_no_machine_observations(self):
        """No timestamp fallback, no event-type fallback, no task-wide matching."""
        self._legacy_turn()
        bundle = self.bundle()
        self.assertEqual(bundle.observations, ())
        self.assertEqual(
            [g.relationship for g in bundle.relationships if g.path_agreement], []
        )

    def test_a_legacy_turn_says_so_as_a_limitation(self):
        self._legacy_turn()
        self.assertIn(LIMIT_LEGACY_TURN, self.bundle().limitations)

    def test_a_legacy_turn_keeps_its_own_claims(self):
        """Claims carry a durable turn number of their own and are not lost."""
        self._legacy_turn()
        self.write("legacy.txt", "x")
        self.store.record_change_claims(
            self.task_id,
            (ClaimSubmission(operation=CLAIM_MODIFIED, path="legacy.txt"),),
            project_root=self.root,
            turn_number=1,
        )
        bundle = self.bundle()
        self.assertEqual(len(bundle.claims), 1)
        self.assertEqual(
            bundle.relationships[0].relationship, RELATIONSHIP_CLAIM_ONLY
        )

    def test_a_legacy_turn_reports_legacy_completeness(self):
        self._legacy_turn()
        self.assertEqual(self.bundle().ingestion.state, COMPLETENESS_LEGACY_UNKNOWN)

    def test_no_such_turn_is_none(self):
        self._run()
        self._open()
        self.assertIsNone(self.bundle(9))
        self.assertIsNone(self.store.evidence_bundle(self.task_id, "1"))


class IngestionCompletenessTests(BundleFixture):
    def test_a_full_report_is_complete(self):
        self._run()
        self._open()
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="keep.txt"))
        self.assertEqual(self.bundle().ingestion.state, COMPLETENESS_COMPLETE)

    def test_a_turn_with_no_ingestion_row_is_not_called_complete(self):
        """The distinction the PR1 write path makes necessary."""
        self._run()
        self._open()
        bundle = self.bundle()
        self.assertEqual(bundle.ingestion.state, COMPLETENESS_INGESTION_MISSING)
        self.assertIn(LIMIT_INGESTION_MISSING, bundle.limitations)

    def test_rejections_make_it_incomplete(self):
        self._run()
        self._open()
        self.store.record_change_claims(
            self.task_id,
            (
                ClaimSubmission(operation=CLAIM_MODIFIED, path="keep.txt"),
                ClaimSubmission(operation="teleported", path="keep.txt"),
            ),
            project_root=self.root,
            turn_number=1,
        )
        summary = self.bundle().ingestion
        self.assertEqual(summary.state, COMPLETENESS_INCOMPLETE)
        self.assertEqual(summary.submitted, 2)
        self.assertEqual(summary.accepted, 1)
        self.assertEqual(summary.rejected, 1)

    def test_the_outcome_cap_reports_forty_into_thirty_two(self):
        self._run()
        self._open()
        submissions = tuple(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="f%02d.txt" % index)
            for index in range(40)
        )
        self.store.record_change_claims(
            self.task_id, submissions, project_root=self.root, turn_number=1
        )
        summary = self.bundle().ingestion
        self.assertEqual(summary.submitted, 40)
        self.assertEqual(summary.accepted, 32)
        self.assertTrue(summary.truncated)
        self.assertEqual(summary.state, COMPLETENESS_INCOMPLETE)

    def test_several_ingestion_rows_in_one_turn_are_aggregated(self):
        self._run()
        self._open()
        for index in range(3):
            self.store.record_change_claims(
                self.task_id,
                (ClaimSubmission(operation=CLAIM_MODIFIED, path="r%d.txt" % index),),
                project_root=self.root,
                turn_number=1,
            )
        summary = self.bundle().ingestion
        self.assertEqual(summary.submitted, 3)
        self.assertEqual(summary.accepted, 3)
        self.assertEqual(len(summary.sequences), 3)

    def test_reason_counts_merge_by_addition(self):
        self._run()
        self._open()
        for _ in range(2):
            self.store.record_change_claims(
                self.task_id,
                (ClaimSubmission(operation="nonsense", path="x.txt"),),
                project_root=self.root,
                turn_number=1,
            )
        summary = self.bundle().ingestion
        self.assertEqual(summary.reason_counts.get("claim_invalid"), 2)
        self.assertEqual(summary.rejected, 2)

    def test_another_turns_ingestion_is_not_counted(self):
        self._run()
        self._open()
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="keep.txt"), turn=1)
        self._move("ready_for_followup", actor="adapter", source="adapter")
        self._followup()
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="keep.txt"), turn=2)
        self.assertEqual(self.bundle(1).ingestion.submitted, 1)
        self.assertEqual(self.bundle(2).ingestion.submitted, 1)


class DuplicateAndRenameTests(BundleFixture):
    def test_duplicate_claims_and_observations_make_one_group(self):
        """No N x M explosion: eight and eight is one group, not sixty-four."""
        self._run()
        self._open()
        # Two separate observation events. The texts differ because the store
        # suppresses a byte-identical repeat of the event immediately before it
        # — correct behaviour, and it would otherwise silently halve this
        # fixture and make the duplicate case weaker than it looks.
        self._observe(*[path_observation("dup.txt") for _ in range(3)])
        self.store.append_event(
            self.task_id,
            "progress",
            actor="system",
            source="cofferdam",
            text="Cofferdam checked the project again.",
            evidence=tuple(path_observation("dup.txt") for _ in range(3)),
        )
        self.write("dup.txt", "x")
        self.store.record_change_claims(
            self.task_id,
            tuple(
                ClaimSubmission(operation=CLAIM_MODIFIED, path="dup.txt")
                for _ in range(6)
            ),
            project_root=self.root,
            turn_number=1,
        )
        bundle = self.bundle()
        groups = [g for g in bundle.relationships if g.path == "dup.txt"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].claim_count, 6)
        self.assertEqual(groups[0].observation_count, 6)
        self.assertEqual(groups[0].relationship, RELATIONSHIP_PATH_AGREED)

    def test_source_lists_are_bounded_and_the_fact_is_kept(self):
        self._run()
        self._open()
        self.write("many.txt", "x")
        self.store.record_change_claims(
            self.task_id,
            tuple(
                ClaimSubmission(operation=CLAIM_MODIFIED, path="many.txt")
                for _ in range(MAX_GROUP_SOURCES + 4)
            ),
            project_root=self.root,
            turn_number=1,
        )
        group = [g for g in self.bundle().relationships if g.path == "many.txt"][0]
        self.assertEqual(len(group.claim_ids), MAX_GROUP_SOURCES)
        self.assertEqual(group.claim_count, MAX_GROUP_SOURCES + 4)
        self.assertTrue(group.sources_truncated)

    def test_source_ordering_is_deterministic(self):
        self._run()
        self._open()
        self._observe(path_observation("d.txt"), path_observation("d.txt"))
        self.write("d.txt", "x")
        self.store.record_change_claims(
            self.task_id,
            tuple(
                ClaimSubmission(operation=CLAIM_MODIFIED, path="d.txt")
                for _ in range(4)
            ),
            project_root=self.root,
            turn_number=1,
        )
        first = self.bundle().to_dict()
        self.store.close()
        self.store = _open_store(self.home)
        self.assertEqual(first, self.bundle().to_dict())

    def test_a_rename_has_two_semantic_targets(self):
        self._run()
        self._open()
        self.write("to.txt", "x")
        self.store.record_change_claims(
            self.task_id,
            (ClaimSubmission(operation=CLAIM_RENAMED, path="from.txt", to_path="to.txt"),),
            project_root=self.root,
            turn_number=1,
        )
        paths = {g.path for g in self.bundle().relationships}
        self.assertEqual(paths, {"from.txt", "to.txt"})

    def test_both_rename_paths_observed_agrees_on_both_without_confirming(self):
        self._run()
        self._open()
        self._observe(path_observation("from.txt"), path_observation("to.txt"))
        self.write("to.txt", "x")
        self.store.record_change_claims(
            self.task_id,
            (ClaimSubmission(operation=CLAIM_RENAMED, path="from.txt", to_path="to.txt"),),
            project_root=self.root,
            turn_number=1,
        )
        groups = {g.path: g for g in self.bundle().relationships}
        self.assertEqual(groups["from.txt"].relationship, RELATIONSHIP_PATH_AGREED)
        self.assertEqual(groups["to.txt"].relationship, RELATIONSHIP_PATH_AGREED)
        # Never "rename confirmed".
        self.assertEqual(groups["from.txt"].operation_agreement, OPERATION_UNKNOWN)
        self.assertEqual(groups["to.txt"].operation_agreement, OPERATION_UNKNOWN)

    def test_only_the_source_observed_leaves_the_destination_claim_only(self):
        self._run()
        self._open()
        self._observe(path_observation("from.txt"))
        self.write("to.txt", "x")
        self.store.record_change_claims(
            self.task_id,
            (ClaimSubmission(operation=CLAIM_RENAMED, path="from.txt", to_path="to.txt"),),
            project_root=self.root,
            turn_number=1,
        )
        groups = {g.path: g.relationship for g in self.bundle().relationships}
        self.assertEqual(groups["from.txt"], RELATIONSHIP_PATH_AGREED)
        self.assertEqual(groups["to.txt"], RELATIONSHIP_CLAIM_ONLY)

    def test_only_the_destination_observed_leaves_the_source_claim_only(self):
        self._run()
        self._open()
        self._observe(path_observation("to.txt"))
        self.write("to.txt", "x")
        self.store.record_change_claims(
            self.task_id,
            (ClaimSubmission(operation=CLAIM_RENAMED, path="from.txt", to_path="to.txt"),),
            project_root=self.root,
            turn_number=1,
        )
        groups = {g.path: g.relationship for g in self.bundle().relationships}
        self.assertEqual(groups["to.txt"], RELATIONSHIP_PATH_AGREED)
        self.assertEqual(groups["from.txt"], RELATIONSHIP_CLAIM_ONLY)

    def test_neither_rename_path_observed_is_two_gaps_not_a_conflict(self):
        self._run()
        self._open()
        self.write("to.txt", "x")
        self.store.record_change_claims(
            self.task_id,
            (ClaimSubmission(operation=CLAIM_RENAMED, path="from.txt", to_path="to.txt"),),
            project_root=self.root,
            turn_number=1,
        )
        bundle = self.bundle()
        groups = {g.path: g.relationship for g in bundle.relationships}
        self.assertEqual(groups["from.txt"], RELATIONSHIP_CLAIM_ONLY)
        self.assertEqual(groups["to.txt"], RELATIONSHIP_CLAIM_ONLY)
        self.assertNotIn("conflict", json.dumps(bundle.to_dict()))


class BundleShapeTests(BundleFixture):
    def test_the_versions_are_published(self):
        self._run()
        self._open()
        payload = self.bundle().to_dict()
        self.assertEqual(payload["version"], BUNDLE_VERSION)
        self.assertEqual(payload["assembler_version"], ASSEMBLER_VERSION)

    def test_the_attribution_is_exact_for_a_v5_turn(self):
        self._run()
        self._open()
        self.assertEqual(self.bundle().turn_attribution, ATTRIBUTION_EXACT)

    def test_the_bundle_is_json_serializable_and_bounded(self):
        self._run()
        self._open()
        self._observe(path_observation("keep.txt"))
        self._claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="keep.txt"))
        payload = json.dumps(self.bundle().to_dict())
        self.assertLess(len(payload), 256 * 1024)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
