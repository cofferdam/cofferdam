"""M2K PR1 — the adapter boundary, end to end through the task service.

No provider, no model, no network, no shell: the validation adapter is
code-owned and emits a fixed claim, which is the whole reason it is the adapter
this milestone wires first.
"""

from __future__ import annotations

import unittest

from ._task_doubles import PROJECT_ID, TaskTestCase

from cofferdam.workstation.tasks.adapters.protocol import AdapterOutcome
from cofferdam.workstation.tasks.adapters.validation import VALIDATION_CLAIM_PATH
from cofferdam.workstation.tasks.claims import (
    CLAIM_MODIFIED,
    REASON_ARTIFACT_MISSING,
    REASON_OK,
    REASON_PATH_DENIED_SENSITIVE,
    ClaimSubmission,
    artifact_digest,
)
from cofferdam.workstation.tasks.models import (
    EVIDENCE_ADAPTER_REPORTED,
    EVIDENCE_GIT_OBSERVED,
    EVIDENCE_OS_OBSERVED,
    VERIFIED_EVIDENCE_SOURCES,
)


class AdapterBoundaryTests(unittest.TestCase):
    def test_the_outcome_field_defaults_to_empty(self):
        self.assertEqual(AdapterOutcome().change_claims, ())

    def test_an_adapter_with_nothing_structured_reports_no_claims(self):
        """The honest default. No prose is parsed to manufacture one."""
        outcome = AdapterOutcome(final_result="I changed src/foo.py and src/bar.py")
        self.assertEqual(outcome.change_claims, ())

    def test_the_claude_code_adapter_reports_no_claims_today(self):
        """It observes with git; it has no structured claim source. PR1 leaves it."""
        import inspect

        from cofferdam.workstation.tasks.adapters.claude_code import adapter as cc

        source = inspect.getsource(cc)
        self.assertNotIn("change_claims", source)

    def test_the_agent_sdk_adapter_reports_no_claims_today(self):
        """Its normalizer deliberately never reads a tool input; PR1 does not change that."""
        import inspect

        from cofferdam.workstation.tasks.adapters.claude_agent_sdk import normalize

        source = inspect.getsource(normalize)
        self.assertNotIn("change_claims", source)
        self.assertNotIn("ClaimSubmission", source)


class EndToEndTests(TaskTestCase):
    """Through the real service, with the real store."""

    enable_validation_adapter = True

    def _run_completing_task(self):
        row, _ = self.service.create_task(
            origin="pwa",
            adapter_id="validation",
            project_id=PROJECT_ID,
            prompt="scenario: complete",
        )
        return row

    def test_a_completed_validation_task_records_its_claim(self):
        row = self._run_completing_task()
        claims = self.store.change_claims(row.task_id)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].operation, CLAIM_MODIFIED)
        self.assertEqual(claims[0].path, VALIDATION_CLAIM_PATH)

    def test_the_recorded_claim_is_adapter_reported_and_unverified(self):
        row = self._run_completing_task()
        claim = self.store.change_claims(row.task_id)[0]
        self.assertEqual(claim.source, EVIDENCE_ADAPTER_REPORTED)
        self.assertFalse(claim.verified)
        self.assertNotIn(claim.source, VERIFIED_EVIDENCE_SOURCES)

    def test_the_claim_carries_the_turn_it_was_made_in(self):
        row = self._run_completing_task()
        claim = self.store.change_claims(row.task_id)[0]
        self.assertIsNotNone(claim.turn_number)

    def test_the_artifact_id_is_server_minted_not_the_adapter_label(self):
        row = self._run_completing_task()
        claim = self.store.change_claims(row.task_id)[0]
        self.assertIsNotNone(claim.artifact_id)
        self.assertTrue(claim.artifact_id.startswith("art_"))
        self.assertNotIn(VALIDATION_CLAIM_PATH, claim.artifact_id)
        self.assertNotEqual(claim.artifact_id, claim.adapter_label)

    def test_a_claim_about_a_file_that_does_not_exist_is_kept_honestly(self):
        """The validation adapter writes nothing, so its claim has no bytes."""
        row = self._run_completing_task()
        artifacts = self.store.task_artifacts(row.task_id)
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].reason, REASON_ARTIFACT_MISSING)
        self.assertIsNone(artifacts[0].digest)
        self.assertIsNone(artifacts[0].size_bytes)

    def test_a_claim_about_a_real_file_gets_a_machine_observed_digest(self):
        target = self.project_root / VALIDATION_CLAIM_PATH
        target.write_text("real content\n", encoding="utf-8")
        row = self._run_completing_task()
        artifact = self.store.task_artifacts(row.task_id)[0]
        self.assertEqual(artifact.reason, REASON_OK)
        self.assertEqual(artifact.digest, artifact_digest(b"real content\n"))
        self.assertEqual(artifact.size_bytes, 13)
        self.assertEqual(artifact.source, EVIDENCE_OS_OBSERVED)

    def test_the_claim_stays_a_claim_even_when_the_file_is_there(self):
        """Existence is not verification. This is the whole milestone in one test."""
        (self.project_root / VALIDATION_CLAIM_PATH).write_text("x\n", encoding="utf-8")
        row = self._run_completing_task()
        claim = self.store.change_claims(row.task_id)[0]
        artifact = self.store.task_artifacts(row.task_id)[0]
        self.assertIsNotNone(artifact.digest)     # Cofferdam read bytes
        self.assertFalse(claim.verified)          # the claim is still a claim
        self.assertEqual(claim.source, EVIDENCE_ADAPTER_REPORTED)

    def test_claims_survive_a_store_reopen(self):
        row = self._run_completing_task()
        before = self.store.change_claims(row.task_id)
        self.store.close()
        after = self.store.change_claims(row.task_id)
        self.assertEqual(len(after), len(before))
        self.assertEqual(after[0].claim_id, before[0].claim_id)

    def test_a_task_that_does_not_complete_records_no_claim(self):
        row, _ = self.service.create_task(
            origin="pwa",
            adapter_id="validation",
            project_id=PROJECT_ID,
            prompt="scenario: wait",
        )
        self.assertEqual(self.store.change_claims(row.task_id), ())

    def test_the_existing_evidence_path_is_unchanged(self):
        row = self._run_completing_task()
        events = self.store.events(row.task_id, after=0, limit=200)
        references = [ref for event in events for ref in event.evidence]
        # The adapter's own artifact EvidenceReference still arrives, still a claim.
        self.assertTrue(
            any(r.source == EVIDENCE_ADAPTER_REPORTED for r in references)
        )
        self.assertFalse(
            any(r.verified for r in references if r.identifier == "validation-artifact-1")
        )

    def test_no_verdict_is_produced_anywhere_in_the_task(self):
        row = self._run_completing_task()
        snapshot = self.store.get(row.task_id)
        blob = repr(snapshot) + repr(self.store.change_claims(row.task_id))
        blob += repr(self.store.task_artifacts(row.task_id))
        for forbidden in ("verdict", "CLAIM_MATCHED", "confidence", "risk_level"):
            self.assertNotIn(forbidden, blob)


class ServiceAuthorityTests(TaskTestCase):
    """The root comes from the registry at record time, never from the adapter."""

    enable_validation_adapter = True

    def test_a_claim_is_resolved_against_the_registry_root(self):
        (self.project_root / VALIDATION_CLAIM_PATH).write_text("in root\n", encoding="utf-8")
        row, _ = self.service.create_task(
            origin="pwa",
            adapter_id="validation",
            project_id=PROJECT_ID,
            prompt="scenario: complete",
        )
        artifact = self.store.task_artifacts(row.task_id)[0]
        self.assertEqual(artifact.digest, artifact_digest(b"in root\n"))

    def test_a_file_outside_the_root_is_not_reachable_by_a_claim(self):
        outside = self.project_root.parent / VALIDATION_CLAIM_PATH
        outside.write_text("outside\n", encoding="utf-8")
        row, _ = self.service.create_task(
            origin="pwa",
            adapter_id="validation",
            project_id=PROJECT_ID,
            prompt="scenario: complete",
        )
        artifact = self.store.task_artifacts(row.task_id)[0]
        self.assertNotEqual(artifact.digest, artifact_digest(b"outside\n"))
        self.assertEqual(artifact.reason, REASON_ARTIFACT_MISSING)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
