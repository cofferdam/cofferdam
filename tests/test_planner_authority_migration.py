"""planner.sqlite3 v1 → v2: additive, forward-only, and lossless.

The migration adds ``planner_authority_events`` and changes nothing else. That
claim is cheap to make and easy to break later, so what is asserted here is the
expensive half: a v1 database written by the shipped PR1c-b build is opened by
this one, and **every planner row reads back identically** — same status, same
action, same prepared prompt, same provenance, same durable request payload.

The v1 schema below is a copy, deliberately. Importing the current ``_SCHEMA``
would make this test migrate a database from the version it is testing, which is
a test that can never fail. What is written here is what v1 actually shipped.
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

#: `planner.sqlite3` exactly as PR1c-b created it. Do not update this when the
#: live schema moves — the point is that it is the *old* shape.
V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS planner_requests (
    planner_request_id   TEXT PRIMARY KEY,
    workspace_id         TEXT,
    project_id           TEXT,
    status               TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    started_at           TEXT,
    completed_at         TEXT,

    user_intent          TEXT NOT NULL,
    request_payload_json TEXT NOT NULL,
    projection_policy_id TEXT,
    projection_built_at  TEXT,

    result_schema_version INTEGER,
    action               TEXT,
    summary              TEXT,
    confidence           REAL,
    worker_prompt        TEXT,
    user_question        TEXT,
    decision_basis       TEXT,

    provider_id          TEXT,
    requested_model      TEXT,
    actual_model         TEXT,
    models_used_json     TEXT,
    session_id           TEXT,
    duration_ms          INTEGER,
    ttft_ms              INTEGER,
    input_tokens         INTEGER,
    output_tokens        INTEGER,
    provider_reported_cost_estimate_usd REAL,

    failure_code         TEXT,
    failure_message      TEXT
);

CREATE INDEX IF NOT EXISTS planner_requests_created
    ON planner_requests (created_at);
CREATE INDEX IF NOT EXISTS planner_requests_status
    ON planner_requests (status);
"""

#: Three rows a v1 database plausibly holds, one of each shape that matters.
V1_ROWS = (
    {
        "planner_request_id": "plan_aaaaaaaaaaaaaaaaaaaaaaaaaa",
        "status": "succeeded",
        "action": "PREPARE_WORKER_PROMPT",
        "worker_prompt": "implement the durable planner store",
        "user_question": None,
        "failure_code": None,
    },
    {
        "planner_request_id": "plan_bbbbbbbbbbbbbbbbbbbbbbbbbb",
        "status": "succeeded",
        "action": "ASK_USER",
        "worker_prompt": None,
        "user_question": "sqlite or postgres?",
        "failure_code": None,
    },
    {
        "planner_request_id": "plan_cccccccccccccccccccccccccc",
        "status": "failed",
        "action": None,
        "worker_prompt": None,
        "user_question": None,
        "failure_code": "planner_timeout",
    },
)


def write_v1(directory: Path, *, version: int = 1) -> Path:
    path = directory / DATABASE_FILENAME
    connection = sqlite3.connect(path)
    try:
        connection.executescript(V1_SCHEMA)
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
                    row["planner_request_id"], "ws_1", None, row["status"],
                    "2026-08-20T00:00:00Z", "2026-08-20T00:00:01Z",
                    "2026-08-20T00:00:02Z", "bir seyler yapalim",
                    json.dumps({"project_context": {"parts": []}}),
                    "policy_1", "2026-08-20T00:00:00Z",
                    1 if row["action"] else None, row["action"],
                    "a summary" if row["action"] else None,
                    0.8 if row["action"] else None,
                    row["worker_prompt"], row["user_question"], "because",
                    "claude_code", "opus", "claude-opus-5", json.dumps(["claude-opus-5"]),
                    "sess_1", 1234, 10, 20, 0.01,
                    row["failure_code"],
                    "provider did not answer" if row["failure_code"] else None,
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return path


def snapshot(path: Path) -> list:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM planner_requests ORDER BY planner_request_id"
            )
        ]
    finally:
        connection.close()


class MigrationHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)


class ForwardMigration(MigrationHarness):
    def test_a_v1_database_opens_and_becomes_v2(self):
        write_v1(self.dir)
        store = PlannerStore(self.dir)
        self.assertEqual(store.schema_version(), PLANNER_SCHEMA_VERSION)
        self.assertEqual(PLANNER_SCHEMA_VERSION, 2)

    def test_every_v1_row_survives_byte_for_byte(self):
        path = write_v1(self.dir)
        before = snapshot(path)
        PlannerStore(self.dir)
        self.assertEqual(snapshot(path), before)

    def test_the_planner_read_model_still_reads_v1_rows(self):
        write_v1(self.dir)
        store = PlannerStore(self.dir)

        prepared = store.get("plan_aaaaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertEqual(prepared.status, "succeeded")
        self.assertEqual(prepared.action, "PREPARE_WORKER_PROMPT")
        self.assertEqual(prepared.worker_prompt,
                         "implement the durable planner store")
        self.assertTrue(prepared.has_prepared_prompt)

        asked = store.get("plan_bbbbbbbbbbbbbbbbbbbbbbbbbb")
        self.assertEqual(asked.user_question, "sqlite or postgres?")
        self.assertTrue(asked.needs_user_input)

        failed = store.get("plan_cccccccccccccccccccccccccc")
        self.assertEqual(failed.failure_code, "planner_timeout")
        self.assertIsNone(failed.action)

    def test_the_durable_request_payload_survives(self):
        write_v1(self.dir)
        payload = PlannerStore(self.dir).request_payload(
            "plan_aaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
        self.assertEqual(payload, {"project_context": {"parts": []}})

    def test_a_migrated_v1_result_derives_its_gate(self):
        """The point of the migration: old results become answerable/approvable."""
        from cofferdam.workstation.planner import (
            GATE_ANSWER,
            GATE_CONFIRMATION,
            GATE_NONE,
            PlannerAuthorityService,
        )

        write_v1(self.dir)
        authority = PlannerAuthorityService(store=PlannerStore(self.dir))
        self.assertEqual(
            authority.gate("plan_aaaaaaaaaaaaaaaaaaaaaaaaaa").kind, GATE_CONFIRMATION
        )
        self.assertEqual(
            authority.gate("plan_bbbbbbbbbbbbbbbbbbbbbbbbbb").kind, GATE_ANSWER
        )
        self.assertEqual(
            authority.gate("plan_cccccccccccccccccccccccccc").kind, GATE_NONE
        )

    def test_a_decision_can_be_recorded_against_a_migrated_row(self):
        from cofferdam.workstation.planner import (
            GATE_STATE_APPROVED,
            AuthorityProvenance,
            PlannerAuthorityService,
        )

        write_v1(self.dir)
        authority = PlannerAuthorityService(store=PlannerStore(self.dir))
        gate = authority.approve_prepared_worker_prompt(
            "plan_aaaaaaaaaaaaaaaaaaaaaaaaaa",
            provenance=AuthorityProvenance.internal_test(),
        )
        self.assertEqual(gate.state, GATE_STATE_APPROVED)
        self.assertTrue(gate.binds_current_subject)

    def test_the_authority_table_appears_empty(self):
        write_v1(self.dir)
        path = PlannerStore(self.dir).path
        connection = sqlite3.connect(path)
        try:
            total = connection.execute(
                "SELECT COUNT(*) FROM planner_authority_events"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(total, 0, "a migration invented a human decision")

    def test_migrating_is_idempotent(self):
        path = write_v1(self.dir)
        PlannerStore(self.dir)
        after_first = snapshot(path)
        for _ in range(3):
            PlannerStore(self.dir)
        self.assertEqual(snapshot(path), after_first)
        self.assertEqual(PlannerStore(self.dir).schema_version(), 2)

    def test_a_crash_between_the_table_and_the_bump_is_recoverable(self):
        """Additive DDL then a version bump: doing it twice is doing it once."""
        path = write_v1(self.dir)
        PlannerStore(self.dir)
        # Rewind only the version, as a crash between the two steps would leave
        # it. The table is already there.
        connection = sqlite3.connect(path)
        connection.execute(
            "UPDATE schema_meta SET value = '1' WHERE key = 'schema_version'"
        )
        connection.commit()
        connection.close()

        store = PlannerStore(self.dir)
        self.assertEqual(store.schema_version(), 2)
        self.assertEqual(len(snapshot(path)), len(V1_ROWS))


class ForwardRefusal(MigrationHarness):
    def test_a_newer_database_is_refused(self):
        write_v1(self.dir, version=PLANNER_SCHEMA_VERSION + 1)
        with self.assertRaises(PlannerStoreUnavailable):
            PlannerStore(self.dir)

    def test_refusing_creates_nothing(self):
        """A refusal must not modify the thing it is refusing to touch."""
        path = write_v1(self.dir, version=PLANNER_SCHEMA_VERSION + 1)
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
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertNotIn("planner_authority_events", tables)
        self.assertEqual(version, str(PLANNER_SCHEMA_VERSION + 1))

    def test_an_unreadable_version_is_refused(self):
        path = write_v1(self.dir)
        connection = sqlite3.connect(path)
        connection.execute(
            "UPDATE schema_meta SET value = 'two' WHERE key = 'schema_version'"
        )
        connection.commit()
        connection.close()
        with self.assertRaises(PlannerStoreUnavailable):
            PlannerStore(self.dir)

    def test_a_fresh_database_is_created_at_the_current_version(self):
        store = PlannerStore(self.dir)
        self.assertEqual(store.schema_version(), PLANNER_SCHEMA_VERSION)
        self.assertTrue((self.dir / DATABASE_FILENAME).exists())

    def test_task_core_schema_was_not_bumped(self):
        """The planner owns its own version. Task Core's is not this PR's to move."""
        from cofferdam.workstation.tasks.store import SCHEMA_VERSION

        self.assertEqual(SCHEMA_VERSION, 11)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
