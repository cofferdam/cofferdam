"""M2K PR1 — adversarial cases against the claim recording path.

Synthetic data only. Every "secret" here is a fake value written into a
throwaway directory; nothing reads a real credential, a real host path, or the
operator's own project.

The question each test asks is the same one: **can something an adapter says get
Cofferdam to read, store or believe something it should not?**
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.claims import (
    CLAIM_CREATED,
    CLAIM_DELETED,
    CLAIM_MODIFIED,
    CLAIM_RENAMED,
    MAX_ARTIFACT_READ_BYTES,
    MAX_CLAIMS_PER_OUTCOME,
    MAX_PREVIEW_BYTES,
    REASON_ARTIFACT_MISSING,
    REASON_ARTIFACT_TOO_LARGE,
    REASON_OK,
    REASON_PATH_DENIED_SENSITIVE,
    ClaimSubmission,
    artifact_digest,
    observe_artifact,
    valid_artifact_id,
)
from cofferdam.workstation.tasks.models import (
    EVIDENCE_ADAPTER_REPORTED,
    EVIDENCE_GIT_OBSERVED,
    VERIFIED_EVIDENCE_SOURCES,
)
from cofferdam.workstation.tasks.store import TaskStore

FAKE_SECRET = "ZZFAKESECRETVALUEZZ-not-a-real-credential"


class AdversarialFixture(unittest.TestCase):
    def setUp(self):
        from cofferdam.workstation.config import load_config

        self._temp = tempfile.TemporaryDirectory(prefix="m2k-adv-")
        self.home = Path(self._temp.name)
        self.root = self.home / "project"
        self.root.mkdir()
        self.outside = self.home / "outside"
        self.outside.mkdir()
        config = load_config(self.home)
        config.ensure_dirs()
        self.store = TaskStore(config)
        row, _ = self.store.create_task(
            origin="pwa",
            adapter_id="validation",
            project_id="synth",
            prompt="p",
            title="t",
        )
        self.task_id = row.task_id

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self._temp.cleanup()

    def record(self, *submissions):
        return self.store.record_change_claims(
            self.task_id, list(submissions), project_root=self.root
        )

    def db_bytes(self) -> bytes:
        self.store.close()
        return (self.home / "state" / "tasks" / "tasks.sqlite3").read_bytes()


class EscapeTests(AdversarialFixture):
    def test_a_symlink_to_a_file_outside_the_root_reads_nothing(self):
        target = self.outside / "loot.txt"
        target.write_text(FAKE_SECRET + "\n", encoding="utf-8")
        os.symlink(target, self.root / "innocent.md")
        _, artifacts = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="innocent.md")
        )
        self.assertIsNone(artifacts[0].digest)
        self.assertIsNone(artifacts[0].preview)
        self.assertNotIn(FAKE_SECRET.encode(), self.db_bytes())

    def test_a_symlinked_directory_component_reads_nothing(self):
        (self.outside / "sub").mkdir()
        (self.outside / "sub" / "loot.md").write_text(FAKE_SECRET, encoding="utf-8")
        os.symlink(self.outside / "sub", self.root / "sub")
        _, artifacts = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="sub/loot.md")
        )
        self.assertIsNone(artifacts[0].digest)
        self.assertNotIn(FAKE_SECRET.encode(), self.db_bytes())

    def test_a_dot_dot_traversal_never_becomes_a_claim(self):
        (self.outside / "loot.md").write_text(FAKE_SECRET, encoding="utf-8")
        claims, _ = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="../outside/loot.md")
        )
        self.assertEqual(claims, ())
        self.assertNotIn(FAKE_SECRET.encode(), self.db_bytes())

    def test_an_absolute_path_never_becomes_a_claim(self):
        loot = self.outside / "loot.md"
        loot.write_text(FAKE_SECRET, encoding="utf-8")
        claims, _ = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path=str(loot))
        )
        self.assertEqual(claims, ())
        self.assertNotIn(FAKE_SECRET.encode(), self.db_bytes())

    def test_a_doubled_separator_is_refused_rather_than_collapsed(self):
        claims, _ = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="a//../outside/loot.md")
        )
        self.assertEqual(claims, ())

    def test_a_root_that_becomes_a_symlink_after_creation_is_refused(self):
        real = self.home / "real"
        real.mkdir()
        (real / "a.md").write_text(FAKE_SECRET, encoding="utf-8")
        linked = self.home / "linked"
        os.symlink(real, linked)
        observed = observe_artifact(linked, "a.md")
        self.assertIsNone(observed.digest)
        self.assertNotEqual(observed.reason, REASON_OK)

    def test_a_deleted_root_is_a_reason_not_a_crash(self):
        import shutil

        shutil.rmtree(self.root)
        _, artifacts = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="a.md")
        )
        self.assertIsNone(artifacts[0].digest)
        self.assertNotEqual(artifacts[0].reason, REASON_OK)


class SecretPolicyTests(AdversarialFixture):
    def test_a_denied_file_with_real_looking_content_never_enters_the_database(self):
        (self.root / ".env").write_text("API_KEY=%s\n" % FAKE_SECRET, encoding="utf-8")
        claims, artifacts = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path=".env")
        )
        self.assertEqual(claims[0].reason, REASON_PATH_DENIED_SENSITIVE)
        self.assertIsNone(artifacts[0].preview)
        self.assertNotIn(FAKE_SECRET.encode(), self.db_bytes())

    def test_every_denied_category_is_refused_with_content_present(self):
        cases = {
            ".env": "A=%s",
            "id_rsa": "-----BEGIN PRIVATE KEY-----\n%s",
            "certs/tls.pem": "-----BEGIN PRIVATE KEY-----\n%s",
            ".ssh/config": "IdentityFile %s",
            "secrets/app.md": "%s",
            ".git-credentials": "https://x:%s@host",
        }
        for relative, template in cases.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(template % FAKE_SECRET, encoding="utf-8")
        submissions = [
            ClaimSubmission(operation=CLAIM_MODIFIED, path=relative)
            for relative in cases
        ]
        _, artifacts = self.store.record_change_claims(
            self.task_id, submissions, project_root=self.root
        )
        for record in artifacts:
            self.assertEqual(record.reason, REASON_PATH_DENIED_SENSITIVE, record.path)
            self.assertIsNone(record.digest, record.path)
            self.assertIsNone(record.preview, record.path)
        self.assertNotIn(FAKE_SECRET.encode(), self.db_bytes())

    def test_an_adapter_cannot_override_the_deny_list(self):
        """There is no field to override it with — asserted structurally."""
        fields = set(ClaimSubmission.__dataclass_fields__)
        for forbidden in ("allow", "force", "override", "deny", "policy", "sensitive"):
            self.assertNotIn(forbidden, fields)

    def test_a_denied_path_is_still_recorded_as_a_claim_for_auditability(self):
        (self.root / ".env").write_text("A=%s" % FAKE_SECRET, encoding="utf-8")
        claims, _ = self.record(ClaimSubmission(operation=CLAIM_MODIFIED, path=".env"))
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].path, ".env")

    def test_a_denied_claim_cannot_be_resurrected_by_a_later_read(self):
        (self.root / ".env").write_text("A=%s" % FAKE_SECRET, encoding="utf-8")
        self.record(ClaimSubmission(operation=CLAIM_MODIFIED, path=".env"))
        for record in self.store.task_artifacts(self.task_id):
            self.assertIsNone(record.preview)
            self.assertIsNone(record.digest)


class ForgedAuthorityTests(AdversarialFixture):
    def test_an_adapter_supplied_digest_has_nowhere_to_go(self):
        fields = set(ClaimSubmission.__dataclass_fields__)
        self.assertNotIn("digest", fields)
        self.assertNotIn("size_bytes", fields)

    def test_an_adapter_supplied_artifact_id_has_nowhere_to_go(self):
        self.assertNotIn("artifact_id", set(ClaimSubmission.__dataclass_fields__))

    def test_a_claim_cannot_pretend_to_be_git_observed(self):
        self.assertNotIn("source", set(ClaimSubmission.__dataclass_fields__))
        (self.root / "a.md").write_text("x", encoding="utf-8")
        claims, _ = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="a.md", label="git_observed")
        )
        self.assertEqual(claims[0].source, EVIDENCE_ADAPTER_REPORTED)
        self.assertNotEqual(claims[0].source, EVIDENCE_GIT_OBSERVED)
        self.assertFalse(claims[0].verified)

    def test_a_label_claiming_verification_changes_nothing(self):
        (self.root / "a.md").write_text("x", encoding="utf-8")
        claims, _ = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="a.md", label="VERIFIED")
        )
        self.assertFalse(claims[0].verified)
        self.assertIs(claims[0].to_dict()["verified"], False)

    def test_a_claim_carries_no_executable_text_field(self):
        """D-2026-08-11-7, enforced by absence."""
        fields = set(ClaimSubmission.__dataclass_fields__)
        for forbidden in (
            "command", "argv", "shell", "bash", "script", "run",
            "test_command", "validation_command", "check", "check_command",
        ):
            self.assertNotIn(forbidden, fields)

    def test_a_minted_artifact_id_is_not_the_claimed_path(self):
        (self.root / "a.md").write_text("x", encoding="utf-8")
        _, artifacts = self.record(ClaimSubmission(operation=CLAIM_MODIFIED, path="a.md"))
        self.assertTrue(valid_artifact_id(artifacts[0].artifact_id))
        self.assertNotIn("a.md", artifacts[0].artifact_id)


class VolumeAndContentTests(AdversarialFixture):
    def test_a_huge_claim_list_is_bounded(self):
        submissions = [
            ClaimSubmission(operation=CLAIM_MODIFIED, path="f%d.md" % i)
            for i in range(5000)
        ]
        claims, _ = self.store.record_change_claims(
            self.task_id, submissions, project_root=self.root
        )
        self.assertEqual(len(claims), MAX_CLAIMS_PER_OUTCOME)

    def test_a_huge_path_string_is_refused(self):
        claims, _ = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="a/" * 10000 + "b.md")
        )
        self.assertEqual(claims, ())

    def test_unicode_and_control_characters_are_refused(self):
        for candidate in ("a\x00b.md", "a\u202eb.md", "a\rb.md", "a\x07b.md"):
            claims, _ = self.record(
                ClaimSubmission(operation=CLAIM_MODIFIED, path=candidate)
            )
            self.assertEqual(claims, (), candidate)

    def test_duplicate_claims_are_each_recorded_with_their_own_identity(self):
        (self.root / "a.md").write_text("x", encoding="utf-8")
        claims, _ = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="a.md"),
            ClaimSubmission(operation=CLAIM_MODIFIED, path="a.md"),
        )
        self.assertEqual(len(claims), 2)
        self.assertNotEqual(claims[0].claim_id, claims[1].claim_id)

    def test_a_very_large_file_yields_no_digest(self):
        (self.root / "big.md").write_bytes(b"z" * (MAX_ARTIFACT_READ_BYTES + 1024))
        _, artifacts = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="big.md")
        )
        self.assertEqual(artifacts[0].reason, REASON_ARTIFACT_TOO_LARGE)
        self.assertIsNone(artifacts[0].digest)
        self.assertIsNone(artifacts[0].preview)

    def test_a_binary_file_is_never_decoded_into_the_preview(self):
        (self.root / "blob.md").write_bytes(bytes(range(256)) * 8)
        _, artifacts = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="blob.md")
        )
        self.assertIsNone(artifacts[0].preview)
        self.assertIsNotNone(artifacts[0].digest)
        self.assertNotIn("�", str(artifacts[0].preview))

    def test_the_preview_never_exceeds_its_cap_in_the_database(self):
        (self.root / "long.md").write_text("q" * (MAX_PREVIEW_BYTES * 5), encoding="utf-8")
        self.record(ClaimSubmission(operation=CLAIM_MODIFIED, path="long.md"))
        stored = self.store.task_artifacts(self.task_id)[0]
        self.assertLessEqual(len(stored.preview.encode("utf-8")), MAX_PREVIEW_BYTES)
        self.assertTrue(stored.preview_truncated)

    def test_a_directory_claim_stores_no_content(self):
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "inner.md").write_text(FAKE_SECRET, encoding="utf-8")
        _, artifacts = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="pkg")
        )
        self.assertIsNone(artifacts[0].digest)
        self.assertNotIn(FAKE_SECRET.encode(), self.db_bytes())

    def test_content_changing_between_two_records_changes_the_digest(self):
        (self.root / "a.md").write_text("one", encoding="utf-8")
        _, first = self.record(ClaimSubmission(operation=CLAIM_MODIFIED, path="a.md"))
        (self.root / "a.md").write_text("two", encoding="utf-8")
        _, second = self.record(ClaimSubmission(operation=CLAIM_MODIFIED, path="a.md"))
        self.assertNotEqual(first[0].digest, second[0].digest)
        self.assertEqual(first[0].digest, artifact_digest(b"one"))
        self.assertEqual(second[0].digest, artifact_digest(b"two"))

    def test_the_digest_and_size_describe_the_same_bytes(self):
        body = b"consistent bytes\n" * 40
        (self.root / "c.md").write_bytes(body)
        _, artifacts = self.record(ClaimSubmission(operation=CLAIM_MODIFIED, path="c.md"))
        self.assertEqual(artifacts[0].size_bytes, len(body))
        self.assertEqual(artifacts[0].digest, artifact_digest(body))


class MalformedRowTests(AdversarialFixture):
    def test_a_malformed_stored_row_does_not_crash_the_reader(self):
        (self.root / "a.md").write_text("x", encoding="utf-8")
        self.record(ClaimSubmission(operation=CLAIM_MODIFIED, path="a.md"))
        path = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.store.close()
        with sqlite3.connect(str(path)) as db:
            db.execute("UPDATE task_artifacts SET size_bytes = NULL, digest = NULL")
            db.commit()
        records = self.store.task_artifacts(self.task_id)
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0].digest)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
