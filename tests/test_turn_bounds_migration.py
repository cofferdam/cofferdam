"""M2K PR2 — schema v5, and the v4 → v5 migration.

Schema v5 adds exactly one table: ``task_turn_bounds``. It exists because the
PR1 audit proved a thing that is easy to assume away — **exact turn attribution
cannot be reconstructed from v4 durable data**. A ``ChangeClaim`` carries an
exact ``turn_number``; a ``task_events`` row carries an exact ``sequence``; and
``task_turns`` carries neither end of the event range it owns. The only bridge
between them in v4 is a pair of timestamps, and timestamps are not a shared
authoritative boundary: two events can share a millisecond, a clock can move
backwards, and ``started_at`` is written by a different call than the one that
allocates the sequence.

So v5 stores Cofferdam's own cursor boundaries, written inside the same
transaction as the turn lifecycle operation that established them.

The migration tests matter more than they look. A live v4 database exists on the
production host with 25 tasks, 473 events and 3 turns in it, and the one thing
this milestone must not do is make that history unreadable, different, or
*inferred*. Those three turns get no bounds. Not approximate ones, not ones
derived from ``started_at``, not ones derived from the nearest event: none.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore


def _open_store(home: Path) -> TaskStore:
    from cofferdam.workstation.config import load_config

    config = load_config(home)
    config.ensure_dirs()
    return TaskStore(config)


class CleanDatabaseTests(unittest.TestCase):
    """A database this build created from nothing."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr2-clean-")
        self.home = Path(self._temp.name)
        self.store = _open_store(self.home)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self._temp.cleanup()

    def test_the_schema_version_is_at_least_five(self):
        """v5 is this module's floor, not the current version.

        M2K PR4 took the schema to 6. Pinning `== 5` here would make this file
        fail every time a *later* milestone adds a table, which says nothing
        about whether v5's bounds still work — and that is all this module is
        for. The exact current number is pinned once, in
        `test_git_baseline_migration.py`, where it is the subject.
        """
        self.assertGreaterEqual(SCHEMA_VERSION, 5)

    def test_the_recorded_version_is_the_current_one(self):
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            value = db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertEqual(int(value), SCHEMA_VERSION)

    def test_the_bounds_table_exists(self):
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            names = {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        self.assertIn("task_turn_bounds", names)

    def test_the_bounds_table_has_exactly_these_columns(self):
        """No provider id, no path, no claim, no observation, no bundle, no verdict."""
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            columns = {r[1] for r in db.execute("PRAGMA table_info(task_turn_bounds)")}
        self.assertEqual(
            columns,
            {
                "task_id",
                "turn_number",
                "opened_after_event_sequence",
                "closed_through_event_sequence",
            },
        )

    def test_the_identity_is_the_turn(self):
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            primary = [
                r[1] for r in db.execute("PRAGMA table_info(task_turn_bounds)") if r[5]
            ]
        self.assertEqual(sorted(primary), ["task_id", "turn_number"])

    def test_the_foreign_key_is_the_composite_turn_key(self):
        """A bound can only exist for a turn that exists — both columns of it."""
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            keys = list(db.execute("PRAGMA foreign_key_list(task_turn_bounds)"))
        self.assertTrue(keys, "task_turn_bounds has no foreign key")
        tables = {row[2] for row in keys}
        self.assertEqual(tables, {"task_turns"})
        pairs = {(row[3], row[4]) for row in keys}
        self.assertEqual(pairs, {("task_id", "task_id"), ("turn_number", "turn_number")})

    def test_a_bound_cannot_reference_a_turn_that_does_not_exist(self):
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO task_turn_bounds (task_id, turn_number,"
                    " opened_after_event_sequence, closed_through_event_sequence)"
                    " VALUES ('tsk_nope', 1, 0, NULL)"
                )

    def test_a_closed_bound_below_its_open_bound_is_refused(self):
        row, _ = self.store.create_task(
            origin="pwa",
            adapter_id="validation",
            project_id="synth",
            prompt="p",
            title="t",
        )
        self.store.open_turn(
            row.task_id, provider="validation", source="internal_test", started_at="2026-08-14T00:00:00Z"
        )
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE task_turn_bounds"
                    " SET closed_through_event_sequence = opened_after_event_sequence - 1"
                    " WHERE task_id = ?",
                    (row.task_id,),
                )

    def test_a_negative_open_bound_is_refused(self):
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO task_turn_bounds (task_id, turn_number,"
                    " opened_after_event_sequence, closed_through_event_sequence)"
                    " VALUES ('tsk_x', 1, -1, NULL)"
                )

    def test_one_bound_per_turn(self):
        row, _ = self.store.create_task(
            origin="pwa",
            adapter_id="validation",
            project_id="synth",
            prompt="p",
            title="t",
        )
        self.store.open_turn(
            row.task_id, provider="validation", source="internal_test", started_at="2026-08-14T00:00:00Z"
        )
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO task_turn_bounds (task_id, turn_number,"
                    " opened_after_event_sequence, closed_through_event_sequence)"
                    " VALUES (?, 1, 0, NULL)",
                    (row.task_id,),
                )

    def test_the_pre_existing_tables_are_untouched(self):
        """Additive means every v4 table still has exactly its v4 columns."""
        self.store.storage_health()
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
            "task_change_claims": {
                "claim_id", "task_id", "turn_number", "operation", "path", "to_path",
                "adapter_label", "reported_at", "artifact_id", "reason",
            },
            "task_artifacts": {
                "artifact_id", "task_id", "claim_id", "path", "digest", "size_bytes",
                "preview", "preview_truncated", "reason", "observed_at",
            },
            "task_claim_ingestion": {
                "task_id", "sequence", "turn_number", "submitted_count",
                "accepted_count", "rejected_count", "truncated", "reason_counts_json",
                "recorded_at",
            },
        }
        with sqlite3.connect(str(self.path)) as db:
            for table, columns in expected.items():
                found = {r[1] for r in db.execute("PRAGMA table_info(%s)" % table)}
                self.assertEqual(found, columns, table)

    def test_there_is_no_evidence_bundle_table(self):
        """The bundle is derived. A table for it would be a second source of truth."""
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            names = {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        for forbidden in (
            "task_evidence_bundles",
            "evidence_bundles",
            "task_evidence",
            "task_relationships",
            "task_evaluations",
        ):
            self.assertNotIn(forbidden, names)


class MigrationTests(unittest.TestCase):
    """A real v4 database, opened by this build.

    ``_build_v4`` is the v4 schema written out longhand rather than imported.
    Importing would make the test pass by construction the day somebody changes
    the constant; typing it out is what makes it a test of the *upgrade*.
    """

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr2-mig-")
        self.home = Path(self._temp.name)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.path.parent.mkdir(parents=True)

    def tearDown(self):
        self._temp.cleanup()

    def _build_v4(self):
        """The v4 schema, exactly as version 4 shipped it, with real history."""
        with sqlite3.connect(str(self.path)) as db:
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
                    cancellation_json TEXT, event_cursor INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE task_events (
                    task_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL, created_at TEXT NOT NULL,
                    actor TEXT NOT NULL, source TEXT NOT NULL,
                    lifecycle_revision INTEGER NOT NULL, correlation_id TEXT,
                    state TEXT, text TEXT, detail TEXT, evidence_json TEXT,
                    PRIMARY KEY (task_id, sequence),
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
                );
                CREATE TABLE idempotency (
                    scope TEXT NOT NULL, request_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL, task_id TEXT NOT NULL,
                    result_json TEXT, created_at TEXT NOT NULL, created_ts REAL NOT NULL,
                    PRIMARY KEY (scope, request_key)
                );
                CREATE TABLE task_clarifications (
                    task_id TEXT NOT NULL, question_id TEXT NOT NULL,
                    provider TEXT NOT NULL, provider_session_id TEXT,
                    provider_event_id TEXT, provider_sequence INTEGER NOT NULL DEFAULT 0,
                    question TEXT NOT NULL, answer_mode TEXT NOT NULL, options_json TEXT,
                    schema_verified INTEGER NOT NULL DEFAULT 0, requested_at TEXT NOT NULL,
                    status TEXT NOT NULL, answered_at TEXT, answer_json TEXT,
                    PRIMARY KEY (task_id, question_id),
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
                );
                CREATE TABLE task_turns (
                    task_id TEXT NOT NULL, turn_number INTEGER NOT NULL,
                    provider TEXT NOT NULL, provider_session_id TEXT,
                    provider_turn_sequence INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL, followup_request_id TEXT,
                    started_at TEXT NOT NULL, completed_at TEXT, outcome TEXT,
                    result TEXT, failure_code TEXT, failure_summary TEXT,
                    PRIMARY KEY (task_id, turn_number),
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
                );
                CREATE TABLE task_change_claims (
                    claim_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                    turn_number INTEGER, operation TEXT NOT NULL, path TEXT NOT NULL,
                    to_path TEXT, adapter_label TEXT, reported_at TEXT NOT NULL,
                    artifact_id TEXT, reason TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
                );
                CREATE TABLE task_artifacts (
                    artifact_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                    claim_id TEXT NOT NULL, path TEXT NOT NULL, digest TEXT,
                    size_bytes INTEGER, preview TEXT,
                    preview_truncated INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE,
                    FOREIGN KEY (claim_id) REFERENCES task_change_claims (claim_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE task_claim_ingestion (
                    task_id TEXT NOT NULL, sequence INTEGER NOT NULL, turn_number INTEGER,
                    submitted_count INTEGER NOT NULL, accepted_count INTEGER NOT NULL,
                    rejected_count INTEGER NOT NULL, truncated INTEGER NOT NULL DEFAULT 0,
                    reason_counts_json TEXT, recorded_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, sequence),
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
                );
                INSERT INTO schema_meta VALUES ('schema_version', '4');
                INSERT INTO tasks (task_id, correlation_id, origin, adapter_id,
                    project_id, state, lifecycle_revision, created_at, updated_at,
                    prompt, event_cursor)
                VALUES ('tsk_legacy', 'cor_1', 'pwa', 'validation', 'synth',
                    'completed', 3, '2026-08-01T00:00:00Z', '2026-08-01T00:05:00Z',
                    'old prompt', 4);
                INSERT INTO task_events (task_id, sequence, event_type, created_at,
                    actor, source, lifecycle_revision, evidence_json)
                VALUES
                  ('tsk_legacy', 1, 'task_created',   '2026-08-01T00:00:00Z',
                   'user', 'cofferdam', 0, NULL),
                  ('tsk_legacy', 2, 'task_started',   '2026-08-01T00:01:00Z',
                   'system', 'cofferdam', 1, NULL),
                  ('tsk_legacy', 3, 'progress',       '2026-08-01T00:02:00Z',
                   'system', 'cofferdam', 1,
                   '[{"evidence_type": "file", "source": "git_observed", "identifier": "src/legacy.py", "operation": "git status", "result": "changed", "observed_at": "2026-08-01T00:02:00Z"}]'),
                  ('tsk_legacy', 4, 'task_completed', '2026-08-01T00:05:00Z',
                   'adapter', 'adapter', 3, NULL);
                INSERT INTO task_turns (task_id, turn_number, provider, source,
                    started_at, completed_at, outcome, result)
                VALUES
                  ('tsk_legacy', 1, 'validation', 'pwa', '2026-08-01T00:01:00Z',
                   '2026-08-01T00:03:00Z', 'completed', 'first'),
                  ('tsk_legacy', 2, 'validation', 'pwa', '2026-08-01T00:03:30Z',
                   '2026-08-01T00:05:00Z', 'completed', 'second'),
                  ('tsk_legacy', 3, 'validation', 'pwa', '2026-08-01T00:05:00Z',
                   NULL, NULL, NULL);
                INSERT INTO task_change_claims (claim_id, task_id, turn_number,
                    operation, path, reported_at, reason)
                VALUES ('chg_legacyclaim0000000000', 'tsk_legacy', 1, 'modified',
                    'src/legacy.py', '2026-08-01T00:02:00Z', 'ok');
                INSERT INTO task_artifacts (artifact_id, task_id, claim_id, path,
                    digest, size_bytes, preview, preview_truncated, reason, observed_at)
                VALUES ('art_legacyartifact0000000', 'tsk_legacy',
                    'chg_legacyclaim0000000000', 'src/legacy.py', 'ab' , 2, 'x', 0,
                    'ok', '2026-08-01T00:02:00Z');
                INSERT INTO task_claim_ingestion (task_id, sequence, turn_number,
                    submitted_count, accepted_count, rejected_count, truncated,
                    reason_counts_json, recorded_at)
                VALUES ('tsk_legacy', 1, 1, 1, 1, 0, 0, NULL, '2026-08-01T00:02:00Z');
                """
            )

    def _opened(self) -> TaskStore:
        store = _open_store(self.home)
        store.storage_health()
        self.addCleanup(store.close)
        return store

    def test_a_v4_database_opens_and_is_migrated_forward(self):
        self._build_v4()
        self._opened()
        with sqlite3.connect(str(self.path)) as db:
            value = db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertGreaterEqual(int(value), 5)
        self.assertEqual(int(value), SCHEMA_VERSION)

    def test_the_migration_creates_the_bounds_table(self):
        self._build_v4()
        self._opened()
        with sqlite3.connect(str(self.path)) as db:
            names = {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        self.assertIn("task_turn_bounds", names)

    def test_historical_turns_receive_no_bounds(self):
        """The heart of it. Three legacy turns, zero inferred bounds."""
        self._build_v4()
        self._opened()
        with sqlite3.connect(str(self.path)) as db:
            turns = db.execute(
                "SELECT COUNT(*) FROM task_turns WHERE task_id='tsk_legacy'"
            ).fetchone()[0]
            bounds = db.execute("SELECT COUNT(*) FROM task_turn_bounds").fetchone()[0]
        self.assertEqual(turns, 3)
        self.assertEqual(bounds, 0)

    def test_the_historical_events_are_byte_identical(self):
        self._build_v4()
        with sqlite3.connect(str(self.path)) as db:
            before = db.execute(
                "SELECT * FROM task_events ORDER BY sequence"
            ).fetchall()
        self._opened()
        with sqlite3.connect(str(self.path)) as db:
            after = db.execute("SELECT * FROM task_events ORDER BY sequence").fetchall()
        self.assertEqual(before, after)

    def test_the_pr1_claim_rows_survive_unchanged(self):
        self._build_v4()
        with sqlite3.connect(str(self.path)) as db:
            before = (
                db.execute("SELECT * FROM task_change_claims").fetchall(),
                db.execute("SELECT * FROM task_artifacts").fetchall(),
                db.execute("SELECT * FROM task_claim_ingestion").fetchall(),
            )
        self._opened()
        with sqlite3.connect(str(self.path)) as db:
            after = (
                db.execute("SELECT * FROM task_change_claims").fetchall(),
                db.execute("SELECT * FROM task_artifacts").fetchall(),
                db.execute("SELECT * FROM task_claim_ingestion").fetchall(),
            )
        self.assertEqual(before, after)

    def test_the_historical_turn_rows_survive_unchanged(self):
        self._build_v4()
        with sqlite3.connect(str(self.path)) as db:
            before = db.execute("SELECT * FROM task_turns ORDER BY turn_number").fetchall()
        self._opened()
        with sqlite3.connect(str(self.path)) as db:
            after = db.execute("SELECT * FROM task_turns ORDER BY turn_number").fetchall()
        self.assertEqual(before, after)

    def test_the_task_row_survives_unchanged(self):
        self._build_v4()
        with sqlite3.connect(str(self.path)) as db:
            before = db.execute("SELECT * FROM tasks").fetchall()
        self._opened()
        with sqlite3.connect(str(self.path)) as db:
            after = db.execute("SELECT * FROM tasks").fetchall()
        self.assertEqual(before, after)

    def test_the_migration_is_idempotent_across_reopens(self):
        self._build_v4()
        for _ in range(3):
            store = _open_store(self.home)
            store.storage_health()
            store.close()
        with sqlite3.connect(str(self.path)) as db:
            value = int(
                db.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()[0]
            )
            bounds = db.execute("SELECT COUNT(*) FROM task_turn_bounds").fetchone()[0]
            turns = db.execute("SELECT COUNT(*) FROM task_turns").fetchone()[0]
        self.assertEqual(value, SCHEMA_VERSION)
        self.assertEqual(bounds, 0)
        self.assertEqual(turns, 3)

    def test_integrity_and_foreign_keys_are_clean_after_migration(self):
        self._build_v4()
        self._opened()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            self.assertEqual(
                db.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
            self.assertEqual(list(db.execute("PRAGMA foreign_key_check")), [])

    def test_a_database_from_a_future_build_is_still_refused(self):
        """The forward-only gate is unchanged, wherever the current version sits.

        Written against `SCHEMA_VERSION + 1` rather than a literal, because the
        literal was 6 and 6 is now a real version this build writes. A gate
        tested against a number the code has since reached is a gate that stops
        being tested at all.
        """
        from cofferdam.workstation.tasks.errors import StoreUnavailable

        self._build_v4()
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "UPDATE schema_meta SET value=? WHERE key='schema_version'",
                (str(SCHEMA_VERSION + 1),),
            )
        store = _open_store(self.home)
        self.addCleanup(store.close)
        with self.assertRaises(StoreUnavailable):
            store.storage_health()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
