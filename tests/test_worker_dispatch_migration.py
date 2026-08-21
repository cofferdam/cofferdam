"""planner.sqlite3 v2 → v3: one more additive table, nothing else disturbed.

Same discipline as the v1 → v2 test: the old schema below is a **copy**, so this
migrates a database from the version it is testing against rather than from
itself. What is asserted is that a v2 database written by the shipped PR1d build
opens here with every planner row and every authority event intact.
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cofferdam.workstation.planner.store import (
    DATABASE_FILENAME,
    PLANNER_SCHEMA_VERSION,
    PlannerStore,
    PlannerStoreUnavailable,
)

from .test_planner_authority_migration import V1_SCHEMA, V1_ROWS

#: The authority table exactly as PR1d shipped it. Not imported from the live
#: schema, for the reason the v1 copy is not.
V2_ADDITION = """
CREATE TABLE IF NOT EXISTS planner_authority_events (
    authority_event_id    TEXT PRIMARY KEY,
    planner_request_id    TEXT NOT NULL,
    gate_kind             TEXT NOT NULL,
    authority_action      TEXT NOT NULL,
    subject_fingerprint   TEXT NOT NULL,
    result_schema_version INTEGER NOT NULL,
    answer_text           TEXT,
    rejection_reason      TEXT,
    actor                 TEXT NOT NULL,
    source                TEXT NOT NULL,
    recorded_at           TEXT NOT NULL,

    FOREIGN KEY (planner_request_id)
        REFERENCES planner_requests (planner_request_id),

    CHECK (gate_kind IN ('answer', 'confirmation')),
    CHECK (authority_action IN ('answer', 'approve', 'reject')),
    CHECK (actor = 'user'),
    CHECK ((authority_action = 'answer') = (answer_text IS NOT NULL)),
    CHECK (rejection_reason IS NULL OR authority_action = 'reject')
);

CREATE UNIQUE INDEX IF NOT EXISTS planner_authority_one_per_request
    ON planner_authority_events (planner_request_id);
"""

APPROVAL = {
    "authority_event_id": "auth_11111111111111111111111111",
    "planner_request_id": "plan_aaaaaaaaaaaaaaaaaaaaaaaaaa",
    "gate_kind": "confirmation",
    "authority_action": "approve",
    "subject_fingerprint": "b" * 64,
    "result_schema_version": 1,
    "actor": "user",
    "source": "local_call",
    "recorded_at": "2026-08-20T12:00:00Z",
}


def write_v2(directory: Path, *, version: int = 2) -> Path:
    path = directory / DATABASE_FILENAME
    connection = sqlite3.connect(path)
    try:
        connection.executescript(V1_SCHEMA)
        connection.executescript(V2_ADDITION)
        connection.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
            (str(version),),
        )
        for row in V1_ROWS:
            connection.execute(
                "INSERT INTO planner_requests (planner_request_id, workspace_id, "
                "project_id, status, created_at, started_at, completed_at, "
                "user_intent, request_payload_json, projection_policy_id, "
                "projection_built_at, result_schema_version, action, summary, "
                "confidence, worker_prompt, user_question, decision_basis, "
                "provider_id, requested_model, actual_model, models_used_json, "
                "session_id, duration_ms, input_tokens, output_tokens, "
                "provider_reported_cost_estimate_usd, failure_code, failure_message) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["planner_request_id"], "ws_1", "alpha", row["status"],
                    "2026-08-20T00:00:00Z", "2026-08-20T00:00:01Z",
                    "2026-08-20T00:00:02Z", "bir seyler yapalim",
                    json.dumps({"project_context": {"parts": []}}),
                    "policy_1", "2026-08-20T00:00:00Z",
                    1 if row["action"] else None, row["action"],
                    "a summary" if row["action"] else None,
                    0.8 if row["action"] else None,
                    row["worker_prompt"], row["user_question"], "because",
                    "claude_code", "opus", "claude-opus-5",
                    json.dumps(["claude-opus-5"]), "sess_1", 1234, 10, 20, 0.01,
                    row["failure_code"],
                    "provider did not answer" if row["failure_code"] else None,
                ),
            )
        connection.execute(
            "INSERT INTO planner_authority_events (authority_event_id, "
            "planner_request_id, gate_kind, authority_action, subject_fingerprint, "
            "result_schema_version, answer_text, rejection_reason, actor, source, "
            "recorded_at) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)",
            (
                APPROVAL["authority_event_id"], APPROVAL["planner_request_id"],
                APPROVAL["gate_kind"], APPROVAL["authority_action"],
                APPROVAL["subject_fingerprint"], APPROVAL["result_schema_version"],
                APPROVAL["actor"], APPROVAL["source"], APPROVAL["recorded_at"],
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return path


def snapshot(path: Path, table: str) -> list:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
    finally:
        connection.close()


class MigrationHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)


class ForwardMigration(MigrationHarness):
    def test_a_v2_database_becomes_current(self):
        write_v2(self.dir)
        self.assertEqual(PlannerStore(self.dir).schema_version(), PLANNER_SCHEMA_VERSION)

    def test_the_current_planner_schema_version(self):
        """**The one place a planner schema bump has to be typed.**

        Every other assertion in this file compares against the constant, so a
        bump lands here and nowhere else. That is deliberate: a literal repeated
        across a suite turns one intentional change into a scavenger hunt, and
        the version this build writes is exactly the kind of fact that deserves a
        single test whose failure means *somebody changed the schema*.

        v4 is PR1f's ``planner_worker_reconciliations``.
        """
        self.assertEqual(PLANNER_SCHEMA_VERSION, 4)

    def test_every_planner_row_survives(self):
        path = write_v2(self.dir)
        before = snapshot(path, "planner_requests")
        PlannerStore(self.dir)
        self.assertEqual(snapshot(path, "planner_requests"), before)

    def test_every_authority_event_survives(self):
        """The decision a person made in the previous build is still theirs."""
        path = write_v2(self.dir)
        before = snapshot(path, "planner_authority_events")
        self.assertEqual(len(before), 1)
        PlannerStore(self.dir)
        after = snapshot(path, "planner_authority_events")
        self.assertEqual(after, before)
        self.assertEqual(after[0]["authority_action"], "approve")

    def test_the_authority_read_model_still_works(self):
        write_v2(self.dir)
        store = PlannerStore(self.dir)
        event = store.authority_event("plan_aaaaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertIsNotNone(event)
        self.assertEqual(event.subject_fingerprint, APPROVAL["subject_fingerprint"])

    def test_the_dispatch_table_appears_empty(self):
        path = write_v2(self.dir)
        PlannerStore(self.dir)
        connection = sqlite3.connect(path)
        try:
            total = connection.execute(
                "SELECT COUNT(*) FROM planner_worker_dispatches"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(total, 0, "a migration invented a dispatch")

    def test_migrating_is_idempotent(self):
        path = write_v2(self.dir)
        PlannerStore(self.dir)
        after_first = snapshot(path, "planner_requests")
        for _ in range(3):
            PlannerStore(self.dir)
        self.assertEqual(snapshot(path, "planner_requests"), after_first)
        self.assertEqual(
            PlannerStore(self.dir).schema_version(), PLANNER_SCHEMA_VERSION
        )

    def test_a_v1_database_migrates_straight_to_current(self):
        """Every version in one open. No hop invents anything."""
        from .test_planner_authority_migration import write_v1

        path = write_v1(self.dir)
        before = snapshot(path, "planner_requests")
        store = PlannerStore(self.dir)
        self.assertEqual(store.schema_version(), PLANNER_SCHEMA_VERSION)
        self.assertEqual(snapshot(path, "planner_requests"), before)
        self.assertEqual(snapshot(path, "planner_authority_events"), [])
        self.assertEqual(snapshot(path, "planner_worker_dispatches"), [])


class ForwardRefusal(MigrationHarness):
    def test_a_newer_database_is_refused(self):
        write_v2(self.dir, version=PLANNER_SCHEMA_VERSION + 1)
        with self.assertRaises(PlannerStoreUnavailable):
            PlannerStore(self.dir)

    def test_refusing_creates_no_dispatch_table(self):
        path = write_v2(self.dir, version=PLANNER_SCHEMA_VERSION + 1)
        with self.assertRaises(PlannerStoreUnavailable):
            PlannerStore(self.dir)
        connection = sqlite3.connect(path)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()
        self.assertNotIn("planner_worker_dispatches", tables)

    def test_task_core_schema_was_not_bumped(self):
        """Task Core needed no schema change: its idempotency already existed."""
        from cofferdam.workstation.tasks.store import SCHEMA_VERSION

        self.assertEqual(SCHEMA_VERSION, 11)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
