"""M2K PR6 — schema v7, and the v6 → v7 migration.

Schema v7 adds exactly two tables: ``task_turn_criteria`` and
``task_turn_criterion_items``. They exist because after five PRs of evidence work
the database can say a great deal about what happened and holds nothing at all
about what was **required**. There is no criterion type, no criterion set, no
criterion identity and no per-turn criteria authority, so a future evaluator has
nothing to evaluate against.

The migration tests matter more than they look. A live v6 database exists on the
production host with 25 tasks, 473 events and 3 turns in it, and those three
turns predate v5 itself. They get **no criteria**. Not parsed out of a prompt,
not derived from a task title, not reconstructed from what a worker claimed it
did, not inferred from the fact that somebody marked the task completed. They
read ``legacy_unknown``, which is the honest answer and the only one available.

The one distinction this module exists to pin above all others: a **missing
row** is ``legacy_unknown`` and a stored ``not_provided`` is a fact somebody
wrote down. Collapsing them would let a task from before this build was written
be read as one deliberately given no requirements.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.criteria import (
    CRITERIA_LEGACY_UNKNOWN,
    CRITERIA_NOT_PROVIDED,
    CRITERIA_PRESENT,
    STORED_CRITERIA_STATES,
)
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

SNAPSHOT_TABLE = "task_turn_criteria"
ITEM_TABLE = "task_turn_criterion_items"
BASELINE_TABLE = "task_turn_git_baselines"


def _open_store(home: Path) -> TaskStore:
    from cofferdam.workstation.config import load_config

    config = load_config(home)
    config.ensure_dirs()
    return TaskStore(config)


class CleanDatabaseTests(unittest.TestCase):
    """A database this build created from nothing."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr6-clean-")
        self.home = Path(self._temp.name)
        self.store = _open_store(self.home)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.store.storage_health()

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self._temp.cleanup()

    def _db(self):
        return sqlite3.connect(str(self.path))

    def _tables(self):
        with self._db() as db:
            return {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }

    def test_the_schema_version_is_at_least_seven(self):
        """Seven is when the criteria tables arrived; later versions add on top.

        Moved off an equality by M2K PR7, which took the constant to 8. The
        literal pin for the *current* version lives in `test_task_core.py` and in
        the newest migration module; what matters here is that the build under
        test has the criteria tables.
        """
        self.assertGreaterEqual(SCHEMA_VERSION, 7)

    def test_the_recorded_version_is_at_least_seven(self):
        with self._db() as db:
            value = db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertGreaterEqual(int(value), 7)

    def test_both_criteria_tables_exist(self):
        names = self._tables()
        self.assertIn(SNAPSHOT_TABLE, names)
        self.assertIn(ITEM_TABLE, names)

    def test_the_snapshot_table_has_exactly_these_columns(self):
        """No verdict, no result, no score, no command, no provider id."""
        with self._db() as db:
            columns = {r[1] for r in db.execute("PRAGMA table_info(%s)" % SNAPSHOT_TABLE)}
        self.assertEqual(
            columns,
            {
                "task_id",
                "turn_number",
                "criteria_state",
                "snapshot_id",
                "criteria_fingerprint",
                "criterion_count",
                "dispatch_state",
                "recorded_at",
            },
        )

    def test_the_item_table_has_exactly_these_columns(self):
        """The absent names are the point: no command, argv, script or check id."""
        with self._db() as db:
            columns = {r[1] for r in db.execute("PRAGMA table_info(%s)" % ITEM_TABLE)}
        self.assertEqual(
            columns,
            {
                "criterion_id",
                "task_id",
                "turn_number",
                "ordinal",
                "kind",
                "predicate",
                "path",
                "to_path",
                "operation",
                "description",
            },
        )

    def test_the_snapshot_identity_is_the_turn(self):
        with self._db() as db:
            primary = [
                r[1] for r in db.execute("PRAGMA table_info(%s)" % SNAPSHOT_TABLE) if r[5]
            ]
        self.assertEqual(sorted(primary), ["task_id", "turn_number"])

    def test_the_snapshot_foreign_key_is_the_task_not_the_turn(self):
        """PR4's lesson, applied again: the turn row does not exist yet."""
        with self._db() as db:
            keys = list(db.execute("PRAGMA foreign_key_list(%s)" % SNAPSHOT_TABLE))
        self.assertTrue(keys, "%s has no foreign key" % SNAPSHOT_TABLE)
        self.assertEqual({row[2] for row in keys}, {"tasks"})
        self.assertEqual({(row[3], row[4]) for row in keys}, {("task_id", "task_id")})

    def test_the_item_foreign_key_is_the_snapshot(self):
        """The parent DOES exist — it is written a line above, same transaction."""
        with self._db() as db:
            keys = list(db.execute("PRAGMA foreign_key_list(%s)" % ITEM_TABLE))
        self.assertTrue(keys, "%s has no foreign key" % ITEM_TABLE)
        self.assertEqual({row[2] for row in keys}, {SNAPSHOT_TABLE})
        self.assertEqual(
            {(row[3], row[4]) for row in keys},
            {("task_id", "task_id"), ("turn_number", "turn_number")},
        )

    def test_a_snapshot_cannot_reference_a_task_that_does_not_exist(self):
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (task_id, turn_number, criteria_state, snapshot_id,"
                    " criteria_fingerprint, criterion_count, dispatch_state, recorded_at)"
                    " VALUES ('task_nope', 1, 'not_provided', 'acs_x0000000',"
                    " '%s', 0, 'captured', '2026-08-15T00:00:00Z')"
                    % (SNAPSHOT_TABLE, "0" * 64)
                )

    def test_a_criterion_cannot_exist_without_a_snapshot(self):
        """The whole reason this foreign key is composite and enforceable."""
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (criterion_id, task_id, turn_number, ordinal, kind,"
                    " predicate, path) VALUES ('acr_x', 'task_x', 1, 1, 'evidence',"
                    " 'path_changed', 'a.py')" % ITEM_TABLE
                )

    def test_a_turn_number_below_one_is_refused(self):
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (task_id, turn_number, criteria_state, snapshot_id,"
                    " criteria_fingerprint, criterion_count, dispatch_state, recorded_at)"
                    " VALUES ('task_x', 0, 'not_provided', 'acs_x0000000', '%s', 0,"
                    " 'captured', '2026-08-15T00:00:00Z')" % (SNAPSHOT_TABLE, "0" * 64)
                )

    def test_legacy_unknown_cannot_be_written(self):
        """A value meaning 'nobody recorded anything' must not be recordable."""
        self.assertNotIn(CRITERIA_LEGACY_UNKNOWN, STORED_CRITERIA_STATES)
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (task_id, turn_number, criteria_state, snapshot_id,"
                    " criteria_fingerprint, criterion_count, dispatch_state, recorded_at)"
                    " VALUES ('task_x', 1, ?, 'acs_x0000000', '%s', 0, 'captured',"
                    " '2026-08-15T00:00:00Z')" % (SNAPSHOT_TABLE, "0" * 64),
                    (CRITERIA_LEGACY_UNKNOWN,),
                )

    def test_not_provided_cannot_carry_criteria(self):
        """`not_provided` is not an empty criterion SET that automatically passes."""
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (task_id, turn_number, criteria_state, snapshot_id,"
                    " criteria_fingerprint, criterion_count, dispatch_state, recorded_at)"
                    " VALUES ('task_x', 1, ?, 'acs_x0000000', '%s', 2, 'captured',"
                    " '2026-08-15T00:00:00Z')" % (SNAPSHOT_TABLE, "0" * 64),
                    (CRITERIA_NOT_PROVIDED,),
                )

    def test_present_cannot_be_empty(self):
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (task_id, turn_number, criteria_state, snapshot_id,"
                    " criteria_fingerprint, criterion_count, dispatch_state, recorded_at)"
                    " VALUES ('task_x', 1, ?, 'acs_x0000000', '%s', 0, 'captured',"
                    " '2026-08-15T00:00:00Z')" % (SNAPSHOT_TABLE, "0" * 64),
                    (CRITERIA_PRESENT,),
                )

    def _insert_item(self, db, **overrides):
        values = {
            "criterion_id": "acr_x",
            "task_id": "task_x",
            "turn_number": 1,
            "ordinal": 1,
            "kind": "evidence",
            "predicate": "path_changed",
            "path": "a.py",
            "to_path": None,
            "operation": None,
            "description": None,
        }
        values.update(overrides)
        db.execute(
            "INSERT INTO %s (criterion_id, task_id, turn_number, ordinal, kind,"
            " predicate, path, to_path, operation, description)"
            " VALUES (:criterion_id, :task_id, :turn_number, :ordinal, :kind,"
            " :predicate, :path, :to_path, :operation, :description)" % ITEM_TABLE,
            values,
        )

    def test_the_criterion_vocabularies_are_closed(self):
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=OFF")
            for overrides in (
                {"kind": "command"},
                {"kind": "check"},
                {"predicate": "path_matches_regex"},
                {"predicate": "tests_pass"},
                {"predicate": "path_operation", "operation": "renamed"},
                {"predicate": "path_operation", "operation": "changed"},
            ):
                with self.assertRaises(sqlite3.IntegrityError, msg=str(overrides)):
                    self._insert_item(db, **overrides)

    def test_an_evidence_criterion_must_carry_a_predicate_and_a_path(self):
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                self._insert_item(db, predicate=None)
            with self.assertRaises(sqlite3.IntegrityError):
                self._insert_item(db, path=None)

    def test_a_manual_criterion_must_carry_a_description_and_nothing_structured(self):
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                self._insert_item(
                    db, kind="manual", predicate=None, path=None, description=None
                )
            with self.assertRaises(sqlite3.IntegrityError):
                # A manual criterion with a path would look evidence-shaped.
                self._insert_item(
                    db, kind="manual", predicate=None, path="a.py", description="check"
                )

    def test_an_operation_belongs_to_exactly_one_predicate(self):
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                self._insert_item(db, predicate="path_changed", operation="created")
            with self.assertRaises(sqlite3.IntegrityError):
                self._insert_item(db, predicate="path_operation", operation=None)

    def test_a_rename_needs_a_destination_and_nothing_else_may_have_one(self):
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                self._insert_item(db, predicate="rename", to_path=None)
            with self.assertRaises(sqlite3.IntegrityError):
                self._insert_item(db, predicate="path_changed", to_path="b.py")
            with self.assertRaises(sqlite3.IntegrityError):
                self._insert_item(db, predicate="rename", path="a.py", to_path="a.py")

    def test_the_pre_existing_tables_are_untouched(self):
        """Additive means every v6 table still has exactly its v6 columns."""
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
            "task_turn_bounds": {
                "task_id", "turn_number", "opened_after_event_sequence",
                "closed_through_event_sequence",
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
            BASELINE_TABLE: {
                "task_id", "turn_number", "capture_state", "head_state",
                "head_revision", "object_format", "working_tree_state",
                "status_coverage", "reason", "dispatch_state", "captured_at",
            },
        }
        with self._db() as db:
            for table, columns in expected.items():
                found = {r[1] for r in db.execute("PRAGMA table_info(%s)" % table)}
                self.assertEqual(found, columns, table)

    def test_there_is_no_evaluation_table(self):
        """The next PR's problem. A table here would be a promise this one cannot keep."""
        names = self._tables()
        for forbidden in (
            "task_evaluations",
            "task_evaluation_records",
            "task_criterion_results",
            "task_verdicts",
            "task_checks",
            "task_check_runs",
            "task_commands",
        ):
            self.assertNotIn(forbidden, names)


class MigrationTests(unittest.TestCase):
    """A real v6 database, opened by this build.

    ``_build_v6`` is the v6 schema written out longhand rather than imported.
    Importing would make the test pass by construction the day somebody changes
    the constant; typing it out is what makes it a test of the *upgrade*.
    """

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr6-mig-")
        self.home = Path(self._temp.name)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.path.parent.mkdir(parents=True)

    def tearDown(self):
        self._temp.cleanup()

    def _build_v6(self):
        """The v6 schema, exactly as version 6 shipped it, with real history."""
        with sqlite3.connect(str(self.path)) as db:
            db.executescript(V6_SCHEMA)
            db.execute(
                "INSERT INTO tasks (task_id, correlation_id, origin, adapter_id,"
                " project_id, state, created_at, updated_at, prompt, event_cursor)"
                " VALUES ('task_legacy', 'corr_legacy', 'pwa', 'validation',"
                " 'synth', 'completed', '2026-08-01T00:00:00Z',"
                " '2026-08-01T00:05:00Z', 'make the login page work', 3)"
            )
            for seq in (1, 2, 3):
                db.execute(
                    "INSERT INTO task_events (task_id, sequence, event_type,"
                    " created_at, actor, source, lifecycle_revision, evidence_json)"
                    " VALUES ('task_legacy', ?, 'progress', '2026-08-01T00:01:00Z',"
                    " 'system', 'cofferdam', 0, NULL)",
                    (seq,),
                )
            db.execute(
                "INSERT INTO task_turns (task_id, turn_number, provider, source,"
                " started_at, completed_at, outcome, result)"
                " VALUES ('task_legacy', 1, 'validation', 'pwa',"
                " '2026-08-01T00:00:30Z', '2026-08-01T00:04:00Z', 'completed',"
                " 'I updated the login page and the tests pass')"
            )
            db.execute(
                "INSERT INTO task_turn_bounds (task_id, turn_number,"
                " opened_after_event_sequence, closed_through_event_sequence)"
                " VALUES ('task_legacy', 1, 0, 3)"
            )
            db.execute(
                "INSERT INTO task_turn_git_baselines (task_id, turn_number,"
                " capture_state, head_state, head_revision, object_format,"
                " working_tree_state, status_coverage, reason, dispatch_state,"
                " captured_at) VALUES ('task_legacy', 1, 'captured', 'present',"
                " '%s', 'sha1', 'clean', 'complete', NULL, 'turn_opened',"
                " '2026-08-01T00:00:29Z')" % ("a" * 40)
            )
            db.execute(
                "INSERT INTO task_change_claims (claim_id, task_id, turn_number,"
                " operation, path, reported_at, reason) VALUES ('chg_legacy',"
                " 'task_legacy', 1, 'modified', 'web/login.js',"
                " '2026-08-01T00:03:00Z', 'ok')"
            )

    def _tables(self):
        with sqlite3.connect(str(self.path)) as db:
            return {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }

    def _columns(self, table):
        with sqlite3.connect(str(self.path)) as db:
            return {r[1] for r in db.execute("PRAGMA table_info(%s)" % table)}

    def test_the_fixture_starts_at_six(self):
        self._build_v6()
        with sqlite3.connect(str(self.path)) as db:
            value = db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertEqual(int(value), 6)
        names = self._tables()
        self.assertNotIn(SNAPSHOT_TABLE, names)
        self.assertNotIn(ITEM_TABLE, names)

    def test_opening_it_migrates_past_six(self):
        """A v6 database is upgraded to whatever this build is, in one open."""
        self._build_v6()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        self.assertGreaterEqual(store.storage_health()["schema_version"], 7)
        with sqlite3.connect(str(self.path)) as db:
            value = db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertGreaterEqual(int(value), 7)

    def test_the_new_tables_are_created_empty(self):
        self._build_v6()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        names = self._tables()
        self.assertIn(SNAPSHOT_TABLE, names)
        self.assertIn(ITEM_TABLE, names)
        with sqlite3.connect(str(self.path)) as db:
            for table in (SNAPSHOT_TABLE, ITEM_TABLE):
                count = db.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
                self.assertEqual(count, 0, table)

    def test_the_historical_turn_gets_no_criteria(self):
        """No backfill. No prompt parsing. No claim-to-criteria conversion."""
        self._build_v6()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT * FROM %s WHERE task_id='task_legacy'" % SNAPSHOT_TABLE
                ).fetchall(),
                [],
            )
            self.assertEqual(
                db.execute(
                    "SELECT * FROM %s WHERE task_id='task_legacy'" % ITEM_TABLE
                ).fetchall(),
                [],
            )

    def test_the_historical_turn_reads_legacy_unknown(self):
        self._build_v6()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        snapshot = store.turn_criteria("task_legacy", 1)
        self.assertEqual(snapshot.state, CRITERIA_LEGACY_UNKNOWN)
        self.assertNotEqual(snapshot.state, CRITERIA_NOT_PROVIDED)
        self.assertFalse(snapshot.recorded)
        self.assertIsNone(snapshot.snapshot_id)
        self.assertIsNone(snapshot.fingerprint)
        self.assertEqual(snapshot.criteria, ())
        self.assertEqual(snapshot.criterion_count, 0)

    def test_the_historical_prompt_and_result_are_not_read_as_criteria(self):
        """The fixture's prompt and result both describe requirements in prose."""
        self._build_v6()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        snapshot = store.turn_criteria("task_legacy", 1)
        self.assertEqual(snapshot.criteria, ())
        blob = repr(snapshot)
        self.assertNotIn("login", blob)
        self.assertNotIn("tests pass", blob)

    def test_the_historical_claim_is_not_converted_into_a_criterion(self):
        self._build_v6()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            rows = db.execute(
                "SELECT path FROM %s" % ITEM_TABLE
            ).fetchall()
        self.assertEqual(rows, [])

    def test_the_historical_contents_survive_exactly(self):
        self._build_v6()
        reads = (
            "SELECT * FROM tasks",
            "SELECT * FROM task_events",
            "SELECT * FROM task_turns",
            "SELECT * FROM task_turn_bounds",
            "SELECT * FROM task_turn_git_baselines",
            "SELECT * FROM task_change_claims",
        )
        with sqlite3.connect(str(self.path)) as db:
            before = {query: db.execute(query).fetchall() for query in reads}
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            after = {query: db.execute(query).fetchall() for query in reads}
        self.assertEqual(before, after)

    def test_every_v6_table_survives_with_its_columns(self):
        self._build_v6()
        before = {t: self._columns(t) for t in self._tables()}
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        for table, columns in before.items():
            self.assertEqual(self._columns(table), columns, table)

    def test_reopening_is_idempotent(self):
        self._build_v6()
        first = _open_store(self.home)
        first.storage_health()
        first.close()
        second = _open_store(self.home)
        self.addCleanup(second.close)
        self.assertGreaterEqual(second.storage_health()["schema_version"], 7)
        with sqlite3.connect(str(self.path)) as db:
            for table in (SNAPSHOT_TABLE, ITEM_TABLE):
                count = db.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
                self.assertEqual(count, 0, table)

    def test_the_migrated_database_is_intact(self):
        self._build_v6()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            self.assertEqual(
                [r[0] for r in db.execute("PRAGMA integrity_check")], ["ok"]
            )
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])


class OldRuntimeAgainstV7Tests(unittest.TestCase):
    """What a rollback to the v6 build does when it meets a v7 database.

    Measured rather than assumed, and the reason it is measured is a specific
    discovery from the PR4 deployment: ``TaskStore._connect`` executes the
    build's own ``_SCHEMA`` script **before** it checks the recorded schema
    version. So "there is a forward version gate" does not by itself establish
    that the older build touches nothing on its way to refusing.

    ``V6_SCHEMA`` and ``V6_VERSION`` below are the shipped v6 build's own script
    and constant, written out longhand, and ``_old_runtime_open`` reproduces
    ``_connect``'s ordering exactly. That makes this a test of the rollback
    rather than of a paraphrase of it, and it runs in CI where the v6 slot does
    not exist.
    """

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr6-rollback-")
        self.home = Path(self._temp.name)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"
        store = _open_store(self.home)
        store.storage_health()
        # Real v7 content, so the probe meets rows rather than empty tables.
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "INSERT INTO tasks (task_id, correlation_id, origin, adapter_id,"
                " project_id, state, created_at, updated_at, prompt, event_cursor)"
                " VALUES ('task_v7', 'corr_v7', 'pwa', 'validation', 'demo',"
                " 'completed', '2026-08-15T00:00:00Z', '2026-08-15T00:01:00Z',"
                " 'do the work', 0)"
            )
        store.reserve_turn_criteria(
            "task_v7",
            _sample_criteria(),
            recorded_at="2026-08-15T00:00:01Z",
        )
        store.close()

    def tearDown(self):
        self._temp.cleanup()

    def _fingerprint_everything(self):
        """Every row of every table, plus the schema text, as one comparable value."""
        with sqlite3.connect(str(self.path)) as db:
            schema = db.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY name"
            ).fetchall()
            tables = [
                r[0]
                for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            rows = {}
            for table in tables:
                rows[table] = db.execute(
                    "SELECT * FROM %s" % table
                ).fetchall()
        return schema, rows

    def _old_runtime_open(self):
        """``TaskStore._connect``'s ordering, as the v6 build shipped it."""
        connection = sqlite3.connect(str(self.path), isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            # The schema script runs BEFORE the version check. This is the line
            # the PR4 deployment found and the reason this test exists.
            connection.executescript(V6_SCHEMA_CREATE_ONLY)
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            found = int(row["value"])
            if found > V6_VERSION:
                raise RuntimeError(
                    "the task database was written by a newer version of Cofferdam"
                )
            return False
        finally:
            connection.close()

    def test_the_old_runtime_refuses_a_v7_database(self):
        with self.assertRaises(RuntimeError) as caught:
            self._old_runtime_open()
        self.assertIn("newer version", str(caught.exception))

    def test_the_old_runtime_changes_nothing(self):
        before = self._fingerprint_everything()
        with self.assertRaises(RuntimeError):
            self._old_runtime_open()
        after = self._fingerprint_everything()
        self.assertEqual(before[0], after[0], "the schema changed")
        self.assertEqual(before[1], after[1], "rows changed")

    def test_the_v7_tables_survive_the_old_runtime(self):
        with self.assertRaises(RuntimeError):
            self._old_runtime_open()
        with sqlite3.connect(str(self.path)) as db:
            names = {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            self.assertIn(SNAPSHOT_TABLE, names)
            self.assertIn(ITEM_TABLE, names)
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM %s" % ITEM_TABLE).fetchone()[0], 3
            )

    def test_the_database_is_still_intact_afterwards(self):
        with self.assertRaises(RuntimeError):
            self._old_runtime_open()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            self.assertEqual(
                [r[0] for r in db.execute("PRAGMA integrity_check")], ["ok"]
            )
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_the_recorded_version_is_unchanged_by_the_probe(self):
        """The fixture is built by the *current* store, so its version tracks it.

        Since M2K PR7 that is 8 rather than 7, which leaves this module still
        proving what it was written to prove: the v6 runtime refuses a newer
        database and changes nothing on its way out.
        """
        before = self._fingerprint_everything()
        with self.assertRaises(RuntimeError):
            self._old_runtime_open()
        with sqlite3.connect(str(self.path)) as db:
            value = db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertGreaterEqual(int(value), 7)
        self.assertEqual(self._fingerprint_everything()[1], before[1])

    def test_the_current_build_still_opens_it_afterwards(self):
        with self.assertRaises(RuntimeError):
            self._old_runtime_open()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        snapshot = store.turn_criteria("task_v7", 1)
        self.assertEqual(snapshot.state, CRITERIA_PRESENT)
        self.assertEqual(len(snapshot.criteria), 3)


def _sample_criteria():
    from cofferdam.workstation.tasks.criteria import validate_criteria

    return validate_criteria(
        [
            {"kind": "evidence", "predicate": "path_changed", "path": "src/a.py"},
            {
                "kind": "evidence",
                "predicate": "path_operation",
                "path": "src/b.py",
                "operation": "created",
            },
            {"kind": "manual", "description": "a person confirms the page renders"},
        ]
    )


#: The v6 schema, longhand. Used both as the migration fixture and as the old
#: runtime's own ``_SCHEMA`` script in the rollback probe above.
V6_VERSION = 6

V6_SCHEMA_CREATE_ONLY = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id            TEXT PRIMARY KEY,
    correlation_id     TEXT NOT NULL,
    parent_task_id     TEXT,
    origin             TEXT NOT NULL,
    adapter_id         TEXT NOT NULL,
    project_id         TEXT NOT NULL,
    state              TEXT NOT NULL,
    waiting_reason     TEXT,
    lifecycle_revision INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    started_at         TEXT,
    updated_at         TEXT NOT NULL,
    completed_at       TEXT,
    title              TEXT,
    prompt             TEXT NOT NULL,
    latest_activity    TEXT,
    latest_output      TEXT,
    final_result       TEXT,
    failure_json       TEXT,
    cancellation_json  TEXT,
    event_cursor       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS tasks_by_state   ON tasks (state, created_at DESC);
CREATE INDEX IF NOT EXISTS tasks_by_created ON tasks (created_at DESC);
CREATE TABLE IF NOT EXISTS task_events (
    task_id            TEXT NOT NULL,
    sequence           INTEGER NOT NULL,
    event_type         TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    actor              TEXT NOT NULL,
    source             TEXT NOT NULL,
    lifecycle_revision INTEGER NOT NULL,
    correlation_id     TEXT,
    state              TEXT,
    text               TEXT,
    detail             TEXT,
    evidence_json      TEXT,
    PRIMARY KEY (task_id, sequence),
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS idempotency (
    scope        TEXT NOT NULL,
    request_key  TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    task_id      TEXT NOT NULL,
    result_json  TEXT,
    created_at   TEXT NOT NULL,
    created_ts   REAL NOT NULL,
    PRIMARY KEY (scope, request_key)
);
CREATE INDEX IF NOT EXISTS idempotency_by_age ON idempotency (created_ts);
CREATE TABLE IF NOT EXISTS task_clarifications (
    task_id             TEXT NOT NULL,
    question_id         TEXT NOT NULL,
    provider            TEXT NOT NULL,
    provider_session_id TEXT,
    provider_event_id   TEXT,
    provider_sequence   INTEGER NOT NULL DEFAULT 0,
    question            TEXT NOT NULL,
    answer_mode         TEXT NOT NULL,
    options_json        TEXT,
    schema_verified     INTEGER NOT NULL DEFAULT 0,
    requested_at        TEXT NOT NULL,
    status              TEXT NOT NULL,
    answered_at         TEXT,
    answer_json         TEXT,
    PRIMARY KEY (task_id, question_id),
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS clarifications_by_status
    ON task_clarifications (task_id, status, provider_sequence);
CREATE UNIQUE INDEX IF NOT EXISTS clarifications_by_provider_event
    ON task_clarifications (task_id, provider_event_id);
CREATE TABLE IF NOT EXISTS task_turns (
    task_id                TEXT    NOT NULL,
    turn_number            INTEGER NOT NULL,
    provider               TEXT    NOT NULL,
    provider_session_id    TEXT,
    provider_turn_sequence INTEGER NOT NULL DEFAULT 0,
    source                 TEXT    NOT NULL,
    followup_request_id    TEXT,
    started_at             TEXT    NOT NULL,
    completed_at           TEXT,
    outcome                TEXT,
    result                 TEXT,
    failure_code           TEXT,
    failure_summary        TEXT,
    PRIMARY KEY (task_id, turn_number),
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS turns_by_completion
    ON task_turns (task_id, completed_at, turn_number);
CREATE UNIQUE INDEX IF NOT EXISTS turns_by_followup_request
    ON task_turns (task_id, followup_request_id);
CREATE TABLE IF NOT EXISTS task_change_claims (
    claim_id      TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    turn_number   INTEGER,
    operation     TEXT NOT NULL,
    path          TEXT NOT NULL,
    to_path       TEXT,
    adapter_label TEXT,
    reported_at   TEXT NOT NULL,
    artifact_id   TEXT,
    reason        TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS claims_by_task
    ON task_change_claims (task_id, reported_at, claim_id);
CREATE TABLE IF NOT EXISTS task_artifacts (
    artifact_id       TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL,
    claim_id          TEXT NOT NULL,
    path              TEXT NOT NULL,
    digest            TEXT,
    size_bytes        INTEGER,
    preview           TEXT,
    preview_truncated INTEGER NOT NULL DEFAULT 0,
    reason            TEXT NOT NULL,
    observed_at       TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE,
    FOREIGN KEY (claim_id) REFERENCES task_change_claims (claim_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS artifacts_by_task
    ON task_artifacts (task_id, observed_at, artifact_id);
CREATE TABLE IF NOT EXISTS task_claim_ingestion (
    task_id            TEXT    NOT NULL,
    sequence           INTEGER NOT NULL,
    turn_number        INTEGER,
    submitted_count    INTEGER NOT NULL,
    accepted_count     INTEGER NOT NULL,
    rejected_count     INTEGER NOT NULL,
    truncated          INTEGER NOT NULL DEFAULT 0,
    reason_counts_json TEXT,
    recorded_at        TEXT    NOT NULL,
    PRIMARY KEY (task_id, sequence),
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS claim_ingestion_by_task
    ON task_claim_ingestion (task_id, sequence);
CREATE TABLE IF NOT EXISTS task_turn_bounds (
    task_id                       TEXT    NOT NULL,
    turn_number                   INTEGER NOT NULL,
    opened_after_event_sequence   INTEGER NOT NULL,
    closed_through_event_sequence INTEGER,
    PRIMARY KEY (task_id, turn_number),
    FOREIGN KEY (task_id, turn_number)
        REFERENCES task_turns (task_id, turn_number) ON DELETE CASCADE,
    CHECK (opened_after_event_sequence >= 0),
    CHECK (closed_through_event_sequence IS NULL
           OR closed_through_event_sequence >= opened_after_event_sequence)
);
CREATE TABLE IF NOT EXISTS task_turn_git_baselines (
    task_id            TEXT    NOT NULL,
    turn_number        INTEGER NOT NULL,
    capture_state      TEXT    NOT NULL,
    head_state         TEXT    NOT NULL,
    head_revision      TEXT,
    object_format      TEXT,
    working_tree_state TEXT    NOT NULL,
    status_coverage    TEXT    NOT NULL,
    reason             TEXT,
    dispatch_state     TEXT    NOT NULL,
    captured_at        TEXT    NOT NULL,
    PRIMARY KEY (task_id, turn_number),
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE,
    CHECK (turn_number >= 1),
    CHECK (capture_state IN ('captured', 'unavailable')),
    CHECK (dispatch_state IN
           ('captured', 'dispatch_started', 'dispatch_refused', 'turn_opened')),
    CHECK (head_state IN ('present', 'unborn', 'unavailable', 'not_a_repository')),
    CHECK (working_tree_state IN ('clean', 'dirty', 'unknown')),
    CHECK (status_coverage IN ('complete', 'incomplete', 'unavailable')),
    CHECK ((head_state = 'present') = (head_revision IS NOT NULL)),
    CHECK ((head_revision IS NULL) = (object_format IS NULL)),
    CHECK (object_format IS NULL OR object_format IN ('sha1', 'sha256')),
    CHECK (head_revision IS NULL OR length(head_revision) BETWEEN 40 AND 64),
    CHECK (reason IS NULL OR length(reason) <= 40),
    CHECK (NOT (working_tree_state = 'clean' AND status_coverage <> 'complete'))
);
"""

V6_SCHEMA = V6_SCHEMA_CREATE_ONLY + (
    "INSERT INTO schema_meta (key, value) VALUES ('schema_version', '6');"
)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
