"""M2K PR1 — claim-ingestion completeness, and what is deliberately not kept.

The property under test throughout: **after a restart, Cofferdam can still tell
that the stored claim set is incomplete and why, without any of the rejected
material having been kept.**

Synthetic data only.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.claims import (
    CLAIM_DELETED,
    CLAIM_MODIFIED,
    MAX_CLAIMS_PER_OUTCOME,
    MAX_CLAIMS_PER_TASK,
    REASON_ARTIFACT_MISSING,
    REASON_CLAIM_INVALID,
    REASON_CLAIM_LIMIT_EXCEEDED,
    REASON_OK,
    REASON_PATH_DENIED_SENSITIVE,
    REASON_PATH_ESCAPE,
    REASON_PATH_INVALID,
    REASON_TASK_CLAIM_LIMIT_EXCEEDED,
    REJECTION_REASONS,
    ClaimIngestion,
    ClaimSubmission,
)
from cofferdam.workstation.tasks.store import TaskStore

FAKE_SECRET = "ZZINGESTSECRETZZ-not-a-real-credential"


class IngestionFixture(unittest.TestCase):
    def setUp(self):
        from cofferdam.workstation.config import load_config

        self._temp = tempfile.TemporaryDirectory(prefix="m2k-ing-")
        self.home = Path(self._temp.name)
        self.root = self.home / "project"
        self.root.mkdir()
        self.config = load_config(self.home)
        self.config.ensure_dirs()
        self.store = TaskStore(self.config)
        row, _ = self.store.create_task(
            origin="pwa", adapter_id="validation", project_id="synth",
            prompt="p", title="t",
        )
        self.task_id = row.task_id

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self._temp.cleanup()

    def record(self, submissions, turn=1):
        return self.store.record_change_claims(
            self.task_id, submissions, project_root=self.root, turn_number=turn
        )

    def reopen(self) -> TaskStore:
        self.store.close()
        self.store = TaskStore(self.config)
        return self.store

    def valid(self, count, start=0):
        return [
            ClaimSubmission(operation=CLAIM_MODIFIED, path="f%d.md" % i)
            for i in range(start, start + count)
        ]

    def db_bytes(self) -> bytes:
        self.store.close()
        return (self.home / "state" / "tasks" / "tasks.sqlite3").read_bytes()


class PerOutcomeLimitTests(IngestionFixture):
    """40 valid claims against a cap of 32."""

    def setUp(self):
        super().setUp()
        self.submitted = MAX_CLAIMS_PER_OUTCOME + 8
        for submission in self.valid(self.submitted):
            (self.root / submission.path).write_text("x\n", encoding="utf-8")
        self.claims, _, self.ingestion = self.record(self.valid(self.submitted))

    def test_exactly_the_cap_is_accepted(self):
        self.assertEqual(len(self.claims), MAX_CLAIMS_PER_OUTCOME)
        self.assertEqual(self.ingestion.accepted, MAX_CLAIMS_PER_OUTCOME)

    def test_the_excess_is_counted_not_discarded_silently(self):
        self.assertEqual(self.ingestion.submitted, self.submitted)
        self.assertEqual(self.ingestion.rejected, 8)

    def test_the_reason_is_the_per_outcome_limit(self):
        self.assertEqual(
            self.ingestion.reason_counts.get(REASON_CLAIM_LIMIT_EXCEEDED), 8
        )

    def test_the_set_is_marked_incomplete(self):
        self.assertTrue(self.ingestion.truncated)
        self.assertFalse(self.ingestion.complete)

    def test_the_durable_signal_survives_a_restart(self):
        reopened = self.reopen()
        summaries = reopened.claim_ingestion(self.task_id)
        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertEqual(summary.submitted, self.submitted)
        self.assertEqual(summary.accepted, MAX_CLAIMS_PER_OUTCOME)
        self.assertEqual(summary.rejected, 8)
        self.assertTrue(summary.truncated)
        self.assertFalse(summary.complete)
        self.assertEqual(
            summary.reason_counts.get(REASON_CLAIM_LIMIT_EXCEEDED), 8
        )

    def test_a_later_reader_can_tell_the_claim_set_was_incomplete(self):
        """The exact question M2K PR2 will need to ask."""
        reopened = self.reopen()
        stored = reopened.change_claims(self.task_id)
        summaries = reopened.claim_ingestion(self.task_id)
        self.assertEqual(len(stored), MAX_CLAIMS_PER_OUTCOME)
        self.assertFalse(all(s.complete for s in summaries))
        self.assertLess(sum(s.accepted for s in summaries),
                        sum(s.submitted for s in summaries))


class PerTaskLimitTests(IngestionFixture):
    def test_the_per_task_limit_is_counted_exactly(self):
        batches = MAX_CLAIMS_PER_TASK // MAX_CLAIMS_PER_OUTCOME
        for batch in range(batches):
            self.record(self.valid(MAX_CLAIMS_PER_OUTCOME, start=batch * 100))
        self.assertEqual(
            len(self.store.change_claims(self.task_id)), MAX_CLAIMS_PER_TASK
        )

        # The task is now full. Ten more valid claims must all be refused, and
        # counted under the per-task reason rather than the per-outcome one.
        claims, _, ingestion = self.record(self.valid(10, start=9000))
        self.assertEqual(len(claims), 0)
        self.assertEqual(ingestion.submitted, 10)
        self.assertEqual(ingestion.accepted, 0)
        self.assertEqual(ingestion.rejected, 10)
        self.assertEqual(
            ingestion.reason_counts.get(REASON_TASK_CLAIM_LIMIT_EXCEEDED), 10
        )
        self.assertNotIn(REASON_CLAIM_LIMIT_EXCEEDED, ingestion.reason_counts)
        self.assertTrue(ingestion.truncated)

    def test_a_partial_batch_at_the_boundary_splits_exactly(self):
        """Leave less room than one full outcome, so the batch genuinely splits."""
        batches = (MAX_CLAIMS_PER_TASK // MAX_CLAIMS_PER_OUTCOME) - 1
        for batch in range(batches):
            self.record(self.valid(MAX_CLAIMS_PER_OUTCOME, start=batch * 100))
        # Fill part of the last slot so the remaining room is a fraction of a cap.
        self.record(self.valid(MAX_CLAIMS_PER_OUTCOME - 10, start=8000))
        used = len(self.store.change_claims(self.task_id))
        room = MAX_CLAIMS_PER_TASK - used
        self.assertGreater(room, 0)
        self.assertLess(room, MAX_CLAIMS_PER_OUTCOME)

        claims, _, ingestion = self.record(
            self.valid(MAX_CLAIMS_PER_OUTCOME, start=9000)
        )
        self.assertEqual(len(claims), room)
        self.assertEqual(ingestion.accepted, room)
        self.assertEqual(
            ingestion.reason_counts.get(REASON_TASK_CLAIM_LIMIT_EXCEEDED),
            MAX_CLAIMS_PER_OUTCOME - room,
        )

    def test_the_per_task_signal_survives_a_restart(self):
        batches = MAX_CLAIMS_PER_TASK // MAX_CLAIMS_PER_OUTCOME
        for batch in range(batches):
            self.record(self.valid(MAX_CLAIMS_PER_OUTCOME, start=batch * 100))
        self.record(self.valid(5, start=9000))
        reopened = self.reopen()
        summaries = reopened.claim_ingestion(self.task_id)
        last = summaries[-1]
        self.assertEqual(last.submitted, 5)
        self.assertEqual(last.accepted, 0)
        self.assertEqual(last.rejected, 5)
        self.assertTrue(last.truncated)

    def test_no_excess_path_data_is_persisted(self):
        batches = MAX_CLAIMS_PER_TASK // MAX_CLAIMS_PER_OUTCOME
        for batch in range(batches):
            self.record(self.valid(MAX_CLAIMS_PER_OUTCOME, start=batch * 100))
        self.record(
            [ClaimSubmission(operation=CLAIM_MODIFIED, path="overflow-marker.md")]
        )
        self.assertNotIn(b"overflow-marker", self.db_bytes())


class InvalidClaimTests(IngestionFixture):
    """One class per rejection kind, each with its own durable reason."""

    CASES = (
        ("invalid operation", ClaimSubmission(operation="invented", path="a.md"),
         REASON_CLAIM_INVALID),
        ("empty path", ClaimSubmission(operation=CLAIM_MODIFIED, path=""),
         REASON_PATH_INVALID),
        ("absolute path", ClaimSubmission(operation=CLAIM_MODIFIED, path="/etc/ZZabs"),
         REASON_PATH_ESCAPE),
        ("dot dot escape", ClaimSubmission(operation=CLAIM_MODIFIED, path="../ZZesc.md"),
         REASON_PATH_ESCAPE),
        ("control character", ClaimSubmission(operation=CLAIM_MODIFIED, path="a\x00ZZnul.md"),
         REASON_PATH_INVALID),
        ("overlong path", ClaimSubmission(operation=CLAIM_MODIFIED, path="ZZlong/" * 500),
         REASON_PATH_INVALID),
    )

    def test_each_invalid_class_is_deterministically_rejected(self):
        for label, submission, expected in self.CASES:
            with self.subTest(label):
                claims, _, ingestion = self.record([submission])
                self.assertEqual(len(claims), 0, label)
                self.assertEqual(ingestion.submitted, 1, label)
                self.assertEqual(ingestion.accepted, 0, label)
                self.assertEqual(ingestion.rejected, 1, label)
                self.assertEqual(ingestion.reason_counts.get(expected), 1, label)
                self.assertFalse(ingestion.complete, label)

    def test_rejection_is_deterministic_across_repeats(self):
        for label, submission, expected in self.CASES:
            first = self.record([submission])[2]
            second = self.record([submission])[2]
            self.assertEqual(first.reason_counts, second.reason_counts, label)

    def test_no_rejected_path_value_reaches_the_database(self):
        self.record([submission for _, submission, _ in self.CASES])
        raw = self.db_bytes()
        for marker in (b"ZZabs", b"ZZesc", b"ZZnul", b"ZZlong", b"/etc/"):
            self.assertNotIn(marker, raw, marker)

    def test_the_reason_counts_column_holds_only_closed_codes(self):
        self.record([submission for _, submission, _ in self.CASES])
        self.store.close()
        with sqlite3.connect(str(self.home / "state/tasks/tasks.sqlite3")) as db:
            raw = db.execute(
                "SELECT reason_counts_json FROM task_claim_ingestion"
            ).fetchone()[0]
        parsed = json.loads(raw)
        for key, value in parsed.items():
            self.assertIn(key, REJECTION_REASONS, key)
            self.assertIsInstance(value, int)

    def test_a_mixed_batch_counts_each_reason_separately(self):
        (self.root / "good.md").write_text("x\n", encoding="utf-8")
        claims, _, ingestion = self.record(
            [
                ClaimSubmission(operation=CLAIM_MODIFIED, path="good.md"),
                ClaimSubmission(operation="invented", path="a.md"),
                ClaimSubmission(operation=CLAIM_MODIFIED, path="../ZZesc.md"),
            ]
        )
        self.assertEqual(len(claims), 1)
        self.assertEqual(ingestion.submitted, 3)
        self.assertEqual(ingestion.accepted, 1)
        self.assertEqual(ingestion.rejected, 2)
        self.assertEqual(ingestion.reason_counts.get(REASON_CLAIM_INVALID), 1)
        self.assertEqual(ingestion.reason_counts.get(REASON_PATH_ESCAPE), 1)


class ValidationVersusObservationTests(IngestionFixture):
    """The distinction the milestone must not blur."""

    def test_a_deleted_path_claim_is_accepted_not_rejected(self):
        claims, artifacts, ingestion = self.record(
            [ClaimSubmission(operation=CLAIM_DELETED, path="gone.md")]
        )
        self.assertEqual(len(claims), 1)
        self.assertEqual(ingestion.accepted, 1)
        self.assertEqual(ingestion.rejected, 0)
        self.assertTrue(ingestion.complete)
        self.assertEqual(artifacts[0].reason, REASON_ARTIFACT_MISSING)

    def test_a_missing_file_is_an_observation_failure_not_a_claim_failure(self):
        _, artifacts, ingestion = self.record(
            [ClaimSubmission(operation=CLAIM_MODIFIED, path="absent.md")]
        )
        self.assertEqual(ingestion.rejected, 0)
        self.assertEqual(
            ingestion.reason_counts.get(REASON_ARTIFACT_MISSING), 1
        )
        self.assertNotIn(REASON_ARTIFACT_MISSING, REJECTION_REASONS)
        self.assertIsNone(artifacts[0].digest)

    def test_a_denied_sensitive_path_is_accepted_with_its_content_withheld(self):
        (self.root / ".env").write_text("A=%s\n" % FAKE_SECRET, encoding="utf-8")
        claims, artifacts, ingestion = self.record(
            [ClaimSubmission(operation=CLAIM_MODIFIED, path=".env")]
        )
        # The claim was valid; only the bytes were refused.
        self.assertEqual(len(claims), 1)
        self.assertEqual(ingestion.accepted, 1)
        self.assertEqual(ingestion.rejected, 0)
        self.assertEqual(
            ingestion.reason_counts.get(REASON_PATH_DENIED_SENSITIVE), 1
        )
        self.assertIsNone(artifacts[0].preview)
        self.assertNotIn(FAKE_SECRET.encode(), self.db_bytes())

    def test_an_escaping_claim_is_rejected_outright(self):
        claims, artifacts, ingestion = self.record(
            [ClaimSubmission(operation=CLAIM_MODIFIED, path="../ZZoutside.md")]
        )
        self.assertEqual(claims, ())
        self.assertEqual(artifacts, ())
        self.assertEqual(ingestion.rejected, 1)

    def test_an_all_valid_batch_is_marked_complete(self):
        (self.root / "a.md").write_text("x\n", encoding="utf-8")
        (self.root / "b.md").write_text("y\n", encoding="utf-8")
        _, _, ingestion = self.record(
            [
                ClaimSubmission(operation=CLAIM_MODIFIED, path="a.md"),
                ClaimSubmission(operation=CLAIM_MODIFIED, path="b.md"),
            ]
        )
        self.assertTrue(ingestion.complete)
        self.assertFalse(ingestion.truncated)
        self.assertEqual(ingestion.rejected, 0)


class IngestionRecordShapeTests(IngestionFixture):
    def test_one_row_per_ingestion_with_its_turn(self):
        (self.root / "a.md").write_text("x\n", encoding="utf-8")
        self.record([ClaimSubmission(operation=CLAIM_MODIFIED, path="a.md")], turn=1)
        self.record([ClaimSubmission(operation=CLAIM_MODIFIED, path="a.md")], turn=2)
        summaries = self.store.claim_ingestion(self.task_id)
        self.assertEqual([s.turn_number for s in summaries], [1, 2])

    def test_the_summary_carries_no_verdict_vocabulary(self):
        (self.root / "a.md").write_text("x\n", encoding="utf-8")
        _, _, ingestion = self.record(
            [ClaimSubmission(operation=CLAIM_MODIFIED, path="a.md")]
        )
        published = json.dumps(ingestion.to_dict())
        for forbidden in (
            "verified", "verdict", "passed", "failed", "pass", "matched",
            "claim_matched", "confidence", "risk", "score", "evaluation",
        ):
            self.assertNotIn(forbidden, published, forbidden)

    def test_the_summary_has_no_field_for_a_rejected_payload(self):
        fields = set(ClaimIngestion.__dataclass_fields__)
        for forbidden in (
            "path", "paths", "rejected_paths", "operation", "label",
            "payload", "submissions", "raw",
        ):
            self.assertNotIn(forbidden, fields, forbidden)

    def test_a_task_that_reported_nothing_has_no_summary(self):
        self.assertEqual(self.store.claim_ingestion(self.task_id), ())

    def test_one_task_cannot_read_another_tasks_summary(self):
        other, _ = self.store.create_task(
            origin="pwa", adapter_id="validation", project_id="synth",
            prompt="other", title="t",
        )
        (self.root / "a.md").write_text("x\n", encoding="utf-8")
        self.record([ClaimSubmission(operation=CLAIM_MODIFIED, path="a.md")])
        self.assertEqual(len(self.store.claim_ingestion(self.task_id)), 1)
        self.assertEqual(self.store.claim_ingestion(other.task_id), ())

    def test_the_summary_lands_in_the_same_transaction_as_its_claims(self):
        """Claims and their completeness record are never separately visible."""
        (self.root / "a.md").write_text("x\n", encoding="utf-8")
        self.record([ClaimSubmission(operation=CLAIM_MODIFIED, path="a.md")])
        reopened = self.reopen()
        self.assertEqual(len(reopened.change_claims(self.task_id)), 1)
        self.assertEqual(len(reopened.claim_ingestion(self.task_id)), 1)


class NoPublicSurfaceTests(IngestionFixture):
    def test_no_route_publishes_the_ingestion_summary(self):
        """Read as source, not imported.

        `workstation/service.py` pulls in FastAPI, which the stdlib-only Trust
        Core run does not have — and this assertion is worth making on that run
        too, so it reads the file rather than importing the module.
        """
        source = (
            Path(__file__).resolve().parents[1]
            / "cofferdam"
            / "workstation"
            / "service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("claim_ingestion", source)
        # The service may call the recorder; it must not publish claims.
        self.assertNotIn("change_claims(", source.replace("_record_change_claims(", ""))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
