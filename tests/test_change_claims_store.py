"""M2K PR1 — schema v4, the store API, and the migration from v3.

The migration tests matter more than they look. A live v3 database exists on the
production host with real task history in it, and the one thing this milestone
must not do is make that history unreadable or different.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.claims import (
    CLAIM_CREATED,
    CLAIM_DELETED,
    CLAIM_MODIFIED,
    CLAIM_RENAMED,
    MAX_CLAIMS_PER_OUTCOME,
    MAX_CLAIMS_PER_TASK,
    REASON_ARTIFACT_MISSING,
    REASON_OK,
    REASON_PATH_DENIED_SENSITIVE,
    ClaimSubmission,
    artifact_digest,
    valid_artifact_id,
    valid_claim_id,
)
from cofferdam.workstation.tasks.models import (
    EVIDENCE_ADAPTER_REPORTED,
    EVIDENCE_GIT_OBSERVED,
    EVIDENCE_OS_OBSERVED,
    EvidenceReference,
    VERIFIED_EVIDENCE_SOURCES,
)
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore


def _open_store(home: Path) -> TaskStore:
    from cofferdam.workstation.config import load_config

    config = load_config(home)
    config.ensure_dirs()
    return TaskStore(config)


class StoreFixture(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-store-")
        self.home = Path(self._temp.name)
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

    def _make_task(self, label: str = "one") -> str:
        row, _ = self.store.create_task(
            origin="pwa",
            adapter_id="validation",
            project_id="synth",
            prompt="do a thing (%s)" % label,
            title="t",
        )
        return row.task_id

    def write(self, relative: str, data) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            target.write_text(data, encoding="utf-8")
        else:
            target.write_bytes(data)
        return target


class SchemaTests(StoreFixture):
    def test_the_schema_version_is_four(self):
        self.assertEqual(SCHEMA_VERSION, 4)

    def test_all_new_tables_exist(self):
        with sqlite3.connect(str(self.home / "state" / "tasks" / "tasks.sqlite3")) as db:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("task_change_claims", names)
        self.assertIn("task_artifacts", names)
        self.assertIn("task_claim_ingestion", names)

    def test_the_ingestion_table_has_no_payload_column(self):
        with sqlite3.connect(str(self.home / "state" / "tasks" / "tasks.sqlite3")) as db:
            columns = {
                r[1] for r in db.execute("PRAGMA table_info(task_claim_ingestion)")
            }
        self.assertEqual(
            columns,
            {
                "task_id", "sequence", "turn_number", "submitted_count",
                "accepted_count", "rejected_count", "truncated",
                "reason_counts_json", "recorded_at",
            },
        )

    def test_the_recorded_version_is_four(self):
        with sqlite3.connect(str(self.home / "state" / "tasks" / "tasks.sqlite3")) as db:
            value = db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertEqual(int(value), 4)

    def test_the_pre_existing_tables_are_untouched(self):
        """Additive means the v3 tables still have exactly their v3 columns."""
        expected = {
            "task_events": {
                "task_id", "sequence", "event_type", "created_at", "actor", "source",
                "lifecycle_revision", "correlation_id", "state", "text", "detail",
                "evidence_json",
            },
            "task_turns": {
                "task_id", "turn_number", "provider", "provider_session_id",
                "provider_turn_sequence", "source", "followup_request_id",
                "started_at", "completed_at", "outcome", "result", "failure_code",
                "failure_summary",
            },
        }
        with sqlite3.connect(str(self.home / "state" / "tasks" / "tasks.sqlite3")) as db:
            for table, columns in expected.items():
                found = {r[1] for r in db.execute("PRAGMA table_info(%s)" % table)}
                self.assertEqual(found, columns, table)


class MigrationTests(unittest.TestCase):
    """A real v3 database, opened by this build."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-mig-")
        self.home = Path(self._temp.name)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.path.parent.mkdir(parents=True)

    def tearDown(self):
        self._temp.cleanup()

    def _build_v3(self):
        """The v3 schema, exactly as version 3 shipped it."""
        db = sqlite3.connect(str(self.path))
        db.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY, correlation_id TEXT NOT NULL,
                parent_task_id TEXT, origin TEXT NOT NULL, adapter_id TEXT NOT NULL,
                project_id TEXT NOT NULL, state TEXT NOT NULL, waiting_reason TEXT,
                lifecycle_revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                started_at TEXT, updated_at TEXT NOT NULL, completed_at TEXT,
                title TEXT, prompt TEXT NOT NULL, latest_activity TEXT,
                latest_output TEXT, final_result TEXT, failure_json TEXT,
                cancellation_json TEXT, event_cursor INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE task_events (
                task_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL, created_at TEXT NOT NULL,
                actor TEXT NOT NULL, source TEXT NOT NULL,
                lifecycle_revision INTEGER NOT NULL, correlation_id TEXT,
                state TEXT, text TEXT, detail TEXT, evidence_json TEXT,
                PRIMARY KEY (task_id, sequence),
                FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE);
            CREATE TABLE idempotency (
                scope TEXT NOT NULL, request_key TEXT NOT NULL,
                payload_hash TEXT NOT NULL, task_id TEXT NOT NULL, result_json TEXT,
                created_at TEXT NOT NULL, created_ts REAL NOT NULL,
                PRIMARY KEY (scope, request_key));
            CREATE TABLE task_clarifications (
                task_id TEXT NOT NULL, question_id TEXT NOT NULL,
                provider TEXT NOT NULL, provider_session_id TEXT,
                provider_event_id TEXT, provider_sequence INTEGER NOT NULL DEFAULT 0,
                question TEXT NOT NULL, answer_mode TEXT NOT NULL, options_json TEXT,
                schema_verified INTEGER NOT NULL DEFAULT 0, requested_at TEXT NOT NULL,
                status TEXT NOT NULL, answered_at TEXT, answer_json TEXT,
                PRIMARY KEY (task_id, question_id),
                FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE);
            CREATE TABLE task_turns (
                task_id TEXT NOT NULL, turn_number INTEGER NOT NULL,
                provider TEXT NOT NULL, provider_session_id TEXT,
                provider_turn_sequence INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL, followup_request_id TEXT,
                started_at TEXT NOT NULL, completed_at TEXT, outcome TEXT,
                result TEXT, failure_code TEXT, failure_summary TEXT,
                PRIMARY KEY (task_id, turn_number),
                FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE);
            INSERT INTO schema_meta VALUES ('schema_version', '3');
            """
        )
        evidence = json.dumps(
            [
                {
                    "evidence_type": "file",
                    "source": "git_observed",
                    "identifier": "src/legacy.py",
                    "operation": "git status",
                    "result": "changed",
                    "observed_at": "2026-08-01T00:00:00.000Z",
                }
            ],
            ensure_ascii=False,
        )
        db.execute(
            "INSERT INTO tasks (task_id, correlation_id, origin, adapter_id, project_id,"
            " state, created_at, updated_at, prompt) VALUES (?,?,?,?,?,?,?,?,?)",
            ("task_old", "tcor-1", "pwa", "validation", "synth", "completed",
             "2026-08-01T00:00:00.000Z", "2026-08-01T00:00:00.000Z", "old prompt"),
        )
        db.execute(
            "INSERT INTO task_events (task_id, sequence, event_type, created_at, actor,"
            " source, lifecycle_revision, evidence_json) VALUES (?,?,?,?,?,?,?,?)",
            ("task_old", 1, "progress", "2026-08-01T00:00:00.000Z", "system",
             "cofferdam", 1, evidence),
        )
        db.execute(
            "INSERT INTO task_turns (task_id, turn_number, provider, source, started_at,"
            " completed_at, outcome, result) VALUES (?,?,?,?,?,?,?,?)",
            ("task_old", 1, "validation", "pwa", "2026-08-01T00:00:00.000Z",
             "2026-08-01T00:01:00.000Z", "completed", "old result"),
        )
        db.commit()
        db.close()
        return evidence

    def test_a_v3_database_opens_and_becomes_v4(self):
        self._build_v3()
        store = _open_store(self.home)
        try:
            store.get("task_old")
        finally:
            store.close()
        with sqlite3.connect(str(self.path)) as db:
            value = db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(int(value), 4)
        self.assertIn("task_change_claims", names)
        self.assertIn("task_artifacts", names)

    def test_existing_rows_survive_byte_for_byte(self):
        evidence = self._build_v3()
        store = _open_store(self.home)
        try:
            row = store.get("task_old")
            self.assertEqual(row.prompt, "old prompt")
            self.assertEqual(row.state, "completed")
        finally:
            store.close()
        with sqlite3.connect(str(self.path)) as db:
            stored = db.execute(
                "SELECT evidence_json FROM task_events WHERE task_id='task_old'"
            ).fetchone()[0]
            turn = db.execute(
                "SELECT result FROM task_turns WHERE task_id='task_old'"
            ).fetchone()[0]
        self.assertEqual(stored, evidence)
        self.assertEqual(turn, "old result")

    def test_old_evidence_json_still_reads_with_its_original_provenance(self):
        self._build_v3()
        store = _open_store(self.home)
        try:
            events = store.events("task_old", after=0, limit=50)
            references = [ref for event in events for ref in event.evidence]
        finally:
            store.close()
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].source, EVIDENCE_GIT_OBSERVED)
        self.assertTrue(references[0].verified)
        self.assertEqual(references[0].identifier, "src/legacy.py")

    def test_a_v3_task_simply_has_no_claims(self):
        self._build_v3()
        store = _open_store(self.home)
        try:
            self.assertEqual(store.change_claims("task_old"), ())
            self.assertEqual(store.task_artifacts("task_old"), ())
            self.assertEqual(store.claim_ingestion("task_old"), ())
        finally:
            store.close()

    def test_the_migration_creates_the_ingestion_table_too(self):
        self._build_v3()
        store = _open_store(self.home)
        try:
            store.get("task_old")
        finally:
            store.close()
        with sqlite3.connect(str(self.path)) as db:
            names = {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            rows = db.execute("SELECT COUNT(*) FROM task_claim_ingestion").fetchone()[0]
        self.assertIn("task_claim_ingestion", names)
        self.assertEqual(rows, 0)

    def test_the_migration_is_idempotent_across_reopens(self):
        self._build_v3()
        for _ in range(4):
            store = _open_store(self.home)
            try:
                store.get("task_old")
            finally:
                store.close()
        with sqlite3.connect(str(self.path)) as db:
            value = db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            claims = db.execute("SELECT COUNT(*) FROM task_change_claims").fetchone()[0]
            events = db.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
        self.assertEqual(int(value), 4)
        self.assertEqual(claims, 0)
        self.assertEqual(events, 1)

    def test_a_newer_database_is_still_refused(self):
        from cofferdam.workstation.tasks.errors import StoreUnavailable

        self._build_v3()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("UPDATE schema_meta SET value='99' WHERE key='schema_version'")
            db.commit()
        store = _open_store(self.home)
        with self.assertRaises(StoreUnavailable):
            store.get("task_old")


class RecordingTests(StoreFixture):
    def test_a_valid_claim_is_recorded_with_an_artifact(self):
        self.write("src/foo.py", "print('x')\n")
        claims, artifacts, _ = self.store.record_change_claims(
            self.task_id,
            [ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py")],
            project_root=self.root,
            turn_number=1,
        )
        self.assertEqual(len(claims), 1)
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(claims[0].operation, CLAIM_MODIFIED)
        self.assertEqual(claims[0].path, "src/foo.py")
        self.assertEqual(artifacts[0].digest, artifact_digest(b"print('x')\n"))
        self.assertEqual(artifacts[0].size_bytes, 11)
        self.assertEqual(artifacts[0].preview, "print('x')\n")

    def test_the_claim_is_adapter_reported_and_the_artifact_is_os_observed(self):
        self.write("src/foo.py", "x\n")
        claims, artifacts, _ = self.store.record_change_claims(
            self.task_id,
            [ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py")],
            project_root=self.root,
        )
        self.assertEqual(claims[0].source, EVIDENCE_ADAPTER_REPORTED)
        self.assertEqual(artifacts[0].source, EVIDENCE_OS_OBSERVED)
        self.assertFalse(claims[0].verified)
        self.assertNotIn(claims[0].source, VERIFIED_EVIDENCE_SOURCES)

    def test_the_digest_is_not_attributed_to_the_adapter(self):
        """The specific confusion D-2026-08-11-6 forbids."""
        self.write("src/foo.py", "x\n")
        _, artifacts, _ = self.store.record_change_claims(
            self.task_id,
            [ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py")],
            project_root=self.root,
        )
        published = artifacts[0].to_dict()
        self.assertIsNotNone(published["digest"])
        self.assertEqual(published["source"], EVIDENCE_OS_OBSERVED)
        self.assertNotEqual(published["source"], EVIDENCE_ADAPTER_REPORTED)

    def test_ids_are_server_minted(self):
        self.write("a.py", "x\n")
        claims, artifacts, _ = self.store.record_change_claims(
            self.task_id,
            [ClaimSubmission(operation=CLAIM_CREATED, path="a.py")],
            project_root=self.root,
        )
        self.assertTrue(valid_claim_id(claims[0].claim_id))
        self.assertTrue(valid_artifact_id(artifacts[0].artifact_id))
        self.assertEqual(claims[0].artifact_id, artifacts[0].artifact_id)

    def test_a_denied_path_records_the_claim_and_stores_no_content(self):
        self.write(".env", "SECRET_TOKEN=abcdefghijklmnop\n")
        claims, artifacts, _ = self.store.record_change_claims(
            self.task_id,
            [ClaimSubmission(operation=CLAIM_MODIFIED, path=".env")],
            project_root=self.root,
        )
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].reason, REASON_PATH_DENIED_SENSITIVE)
        self.assertIsNone(artifacts[0].digest)
        self.assertIsNone(artifacts[0].preview)
        self.assertIsNone(artifacts[0].size_bytes)

    def test_denied_content_is_absent_from_the_database_file_itself(self):
        self.write(".env", "SECRET_TOKEN=abcdefghijklmnop\n")
        self.store.record_change_claims(
            self.task_id,
            [ClaimSubmission(operation=CLAIM_MODIFIED, path=".env")],
            project_root=self.root,
        )
        self.store.close()
        raw = (self.home / "state" / "tasks" / "tasks.sqlite3").read_bytes()
        self.assertNotIn(b"SECRET_TOKEN", raw)
        self.assertNotIn(b"abcdefghijklmnop", raw)

    def test_a_deleted_file_claim_is_kept_with_an_honest_reason(self):
        claims, artifacts, _ = self.store.record_change_claims(
            self.task_id,
            [ClaimSubmission(operation=CLAIM_DELETED, path="src/gone.py")],
            project_root=self.root,
        )
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].operation, CLAIM_DELETED)
        self.assertEqual(artifacts[0].reason, REASON_ARTIFACT_MISSING)
        self.assertIsNone(artifacts[0].digest)

    def test_an_invalid_claim_is_dropped_rather_than_stored_raw(self):
        claims, _, _ = self.store.record_change_claims(
            self.task_id,
            [
                ClaimSubmission(operation=CLAIM_MODIFIED, path="../escape.py"),
                ClaimSubmission(operation="invented", path="a.py"),
                ClaimSubmission(operation=CLAIM_MODIFIED, path="/abs.py"),
            ],
            project_root=self.root,
        )
        self.assertEqual(claims, ())
        self.assertEqual(self.store.change_claims(self.task_id), ())

    def test_the_per_outcome_limit_is_enforced(self):
        submissions = [
            ClaimSubmission(operation=CLAIM_MODIFIED, path="f%d.py" % i)
            for i in range(MAX_CLAIMS_PER_OUTCOME + 25)
        ]
        claims, _, _ = self.store.record_change_claims(
            self.task_id, submissions, project_root=self.root
        )
        self.assertEqual(len(claims), MAX_CLAIMS_PER_OUTCOME)

    def test_the_per_task_limit_is_enforced_across_outcomes(self):
        for _ in range(12):
            self.store.record_change_claims(
                self.task_id,
                [
                    ClaimSubmission(operation=CLAIM_MODIFIED, path="f%d.py" % i)
                    for i in range(MAX_CLAIMS_PER_OUTCOME)
                ],
                project_root=self.root,
            )
        self.assertLessEqual(
            len(self.store.change_claims(self.task_id)), MAX_CLAIMS_PER_TASK
        )

    def test_a_rename_keeps_both_paths(self):
        claims, _, _ = self.store.record_change_claims(
            self.task_id,
            [ClaimSubmission(operation=CLAIM_RENAMED, path="a.py", to_path="b.py")],
            project_root=self.root,
        )
        self.assertEqual(claims[0].path, "a.py")
        self.assertEqual(claims[0].to_path, "b.py")

    def test_claims_persist_across_a_store_reopen(self):
        self.write("a.py", "x\n")
        self.store.record_change_claims(
            self.task_id,
            [ClaimSubmission(operation=CLAIM_CREATED, path="a.py")],
            project_root=self.root,
        )
        before = self.store.change_claims(self.task_id)
        self.store.close()
        reopened = _open_store(self.home)
        try:
            after = reopened.change_claims(self.task_id)
        finally:
            reopened.close()
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0].claim_id, before[0].claim_id)
        self.assertEqual(after[0].path, "a.py")

    def test_the_turn_number_is_recorded(self):
        claims, _, _ = self.store.record_change_claims(
            self.task_id,
            [ClaimSubmission(operation=CLAIM_DELETED, path="a.py")],
            project_root=self.root,
            turn_number=3,
        )
        self.assertEqual(claims[0].turn_number, 3)

    def test_one_task_cannot_read_another_tasks_claims(self):
        other = self._make_task("two")
        self.write("a.py", "x\n")
        self.store.record_change_claims(
            self.task_id,
            [ClaimSubmission(operation=CLAIM_CREATED, path="a.py")],
            project_root=self.root,
        )
        self.assertEqual(len(self.store.change_claims(self.task_id)), 1)
        self.assertEqual(self.store.change_claims(other), ())
        self.assertEqual(self.store.task_artifacts(other), ())

    def test_a_stale_artifact_id_returns_nothing_for_the_wrong_task(self):
        other = self._make_task("three")
        self.write("a.py", "x\n")
        _, artifacts, _ = self.store.record_change_claims(
            self.task_id,
            [ClaimSubmission(operation=CLAIM_CREATED, path="a.py")],
            project_root=self.root,
        )
        found = {record.artifact_id for record in self.store.task_artifacts(other)}
        self.assertNotIn(artifacts[0].artifact_id, found)

    def test_no_verdict_field_exists_anywhere_on_the_records(self):
        self.write("a.py", "x\n")
        claims, artifacts, _ = self.store.record_change_claims(
            self.task_id,
            [ClaimSubmission(operation=CLAIM_CREATED, path="a.py")],
            project_root=self.root,
        )
        published = dict(claims[0].to_dict())
        published.update(artifacts[0].to_dict())
        for forbidden in (
            "verdict", "passed", "failed", "confidence", "risk", "risk_level",
            "criteria", "expected", "matched", "claim_matched", "evaluation",
            "score", "command", "argv",
        ):
            self.assertNotIn(forbidden, published)


class GitObservedCompatibilityTests(StoreFixture):
    def test_git_observed_evidence_is_unchanged_by_this_milestone(self):
        self.store.append_event(
            self.task_id,
            "progress",
            actor="system",
            source="cofferdam",
            text="Cofferdam checked the project itself.",
            evidence=(
                EvidenceReference(
                    evidence_type="file",
                    source=EVIDENCE_GIT_OBSERVED,
                    identifier="src/foo.py",
                    operation="git status",
                    result="changed",
                ),
            ),
        )
        events = self.store.events(self.task_id, after=0, limit=50)
        references = [ref for event in events for ref in event.evidence]
        self.assertTrue(any(r.source == EVIDENCE_GIT_OBSERVED for r in references))
        self.assertTrue(any(r.verified for r in references))

    def test_a_claim_and_an_observation_about_one_path_stay_separate(self):
        """The end state PR1 is built to produce, asserted directly."""
        self.write("src/foo.py", "x\n")
        self.store.append_event(
            self.task_id,
            "progress",
            actor="system",
            source="cofferdam",
            text="Cofferdam checked the project itself.",
            evidence=(
                EvidenceReference(
                    evidence_type="file",
                    source=EVIDENCE_GIT_OBSERVED,
                    identifier="src/foo.py",
                    operation="git status",
                    result="changed",
                ),
            ),
        )
        claims, _, _ = self.store.record_change_claims(
            self.task_id,
            [ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py")],
            project_root=self.root,
        )
        observations = [
            ref
            for event in self.store.events(self.task_id, after=0, limit=50)
            for ref in event.evidence
            if ref.source == EVIDENCE_GIT_OBSERVED
        ]
        # Same path. Two records. Different provenance. No reconciliation.
        self.assertEqual(observations[0].identifier, claims[0].path)
        self.assertTrue(observations[0].verified)
        self.assertFalse(claims[0].verified)
        self.assertEqual(claims[0].reason, REASON_OK)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
