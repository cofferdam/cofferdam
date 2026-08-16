"""M2K PR10 — the v8 → v9 upgrade adds continuity and touches nothing else.

Written before the implementation, and it fails against v8 for the right reason:
the continuity tables do not exist.

The property under test is the one an additive migration exists to provide. A
version-8 database opened by a version-9 build gains two empty tables and a
version number, and **every historical row survives byte for byte**. There is no
backfill: a turn that ran before continuity was persisted has no continuity fact,
and inventing one — from a prompt, a title, worker prose, a claim, or by assuming
the latest turn wins — would manufacture lineage nobody declared.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore
from cofferdam.workstation.config import load_config

#: Every table a version-8 database has. None may lose a row or a column.
V8_TABLES = (
    "tasks",
    "task_events",
    "task_turns",
    "task_turn_bounds",
    "task_turn_git_baselines",
    "task_turn_criteria",
    "task_turn_criterion_items",
    "task_turn_evaluations",
    "task_turn_criterion_results",
    "task_change_claims",
    "task_artifacts",
    "task_claim_ingestion",
    "task_clarifications",
    "idempotency",
    "schema_meta",
)

#: What v9 adds, and all it adds.
V9_NEW_TABLES = (
    "task_turn_criteria_continuity",
    "task_turn_criterion_supersessions",
)


def snapshot(database: Path) -> dict:
    """Every table's full contents plus the object definitions, for comparison."""
    connection = sqlite3.connect("file:%s?mode=ro" % database, uri=True)
    try:
        objects = connection.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        tables = [name for kind, name, _ in objects if kind == "table"]
        rows = {}
        columns = {}
        for table in tables:
            rows[table] = connection.execute(
                'SELECT * FROM "%s"' % table
            ).fetchall()
            columns[table] = [
                row[1] for row in connection.execute("PRAGMA table_info(%s)" % table)
            ]
        return {"objects": objects, "rows": rows, "columns": columns}
    finally:
        connection.close()


class MigrationCase(unittest.TestCase):
    """A real version-8 database, built by the shipped v8 schema script."""

    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.config = load_config(self.home)
        self.config.ensure_dirs()
        self.database = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._build_v8()

    def _build_v8(self) -> None:
        """A v8 database with history in it, written without the v9 build.

        The v8 schema is the shipped script with **only** the continuity section
        excised — everything else, including the PR7 evaluation tables that sit
        after it, is kept — so this is the real prior format rather than an
        approximation of it.
        """
        v8_script = self.v8_schema()
        for table in V9_NEW_TABLES:
            self.assertNotIn(table, v8_script, "%s leaked into the v8 fixture" % table)
        for table in V8_TABLES:
            self.assertIn(table, v8_script, "%s is missing from the v8 fixture" % table)
        connection = sqlite3.connect(str(self.database))
        try:
            connection.executescript(v8_script)
            connection.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('schema_version', '8')"
            )
            connection.execute(
                "INSERT INTO tasks (task_id, correlation_id, origin, adapter_id,"
                " project_id, state, lifecycle_revision, created_at, updated_at,"
                " prompt) VALUES ('task_hist', 'corr', 'pwa', 'validation', 'demo',"
                " 'completed', 1, '2026-08-01T00:00:00Z', '2026-08-01T00:00:01Z', 'p')"
            )
            connection.execute(
                "INSERT INTO task_events (task_id, sequence, event_type, created_at,"
                " actor, source, lifecycle_revision, state)"
                " VALUES ('task_hist', 1, 'task_created', '2026-08-01T00:00:00Z',"
                " 'system', 'cofferdam', 1, 'queued')"
            )
            connection.execute(
                "INSERT INTO task_turns (task_id, turn_number, provider, source,"
                " started_at, completed_at, outcome) VALUES ('task_hist', 1,"
                " 'validation', 'pwa', '2026-08-01T00:00:00Z',"
                " '2026-08-01T00:00:05Z', 'completed')"
            )
            connection.commit()
        finally:
            connection.close()

    def v8_schema(self) -> str:
        """The shipped schema with the v9 continuity section removed.

        Cuts between the two section banners rather than at a table name, so the
        evaluation tables that follow continuity in the script survive into the
        fixture — which is the difference between testing the real v8 format and
        testing a truncation of it.
        """
        from cofferdam.workstation.tasks import store as store_module

        script = store_module._SCHEMA
        start = "-- Schema v9. What this turn's criteria say about the turn before it."
        end = "-- Schema v8. One evaluator version's deterministic judgement on one CLOSED turn."
        self.assertIn(start, script, "the v9 continuity section banner moved")
        self.assertIn(end, script, "the v8 evaluation section banner moved")
        head, _, rest = script.partition(start)
        _, _, tail = rest.partition(end)
        return head + end + tail

    def open_v9(self) -> TaskStore:
        """Open the database with the v9 build and force the connection.

        ``TaskStore`` connects lazily, so constructing one applies no schema. A
        real public read is used rather than reaching for the private connector,
        which also proves the upgraded database is usable rather than merely
        upgraded.
        """
        store = TaskStore(self.config)
        self.addCleanup(store.close)
        store.turns("task_hist")
        return store

    def schema_version(self) -> int:
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            return int(
                connection.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()[0]
            )
        finally:
            connection.close()


class TheUpgrade(MigrationCase):
    def test_the_build_declares_version_nine(self):
        self.assertEqual(9, SCHEMA_VERSION)

    def test_opening_a_v8_database_records_version_nine(self):
        self.assertEqual(8, self.schema_version())
        self.open_v9()
        self.assertEqual(9, self.schema_version())

    def test_every_v8_table_survives(self):
        before = snapshot(self.database)
        self.open_v9()
        after = snapshot(self.database)
        for table in V8_TABLES:
            self.assertIn(table, after["rows"], "%s was dropped" % table)

    def test_every_v8_column_survives_in_order(self):
        before = snapshot(self.database)
        self.open_v9()
        after = snapshot(self.database)
        for table in V8_TABLES:
            self.assertEqual(
                before["columns"][table],
                after["columns"][table],
                "%s columns changed" % table,
            )

    def test_every_historical_row_survives_unchanged(self):
        before = snapshot(self.database)
        self.open_v9()
        after = snapshot(self.database)
        for table in V8_TABLES:
            if table == "schema_meta":
                continue  # the version is the one thing that legitimately moves
            self.assertEqual(
                before["rows"][table],
                after["rows"][table],
                "%s rows changed" % table,
            )

    def test_no_v8_table_definition_is_rewritten(self):
        before = {
            (kind, name): sql
            for kind, name, sql in snapshot(self.database)["objects"]
        }
        self.open_v9()
        after = {
            (kind, name): sql
            for kind, name, sql in snapshot(self.database)["objects"]
        }
        for key, sql in before.items():
            self.assertEqual(sql, after[key], "%s was redefined" % (key,))

    def test_the_continuity_tables_are_created(self):
        self.open_v9()
        tables = snapshot(self.database)["rows"]
        for table in V9_NEW_TABLES:
            self.assertIn(table, tables)

    def test_the_continuity_tables_are_created_empty(self):
        self.open_v9()
        rows = snapshot(self.database)["rows"]
        for table in V9_NEW_TABLES:
            self.assertEqual([], rows[table], "%s was backfilled" % table)

    def test_a_historical_turn_gets_no_continuity_row(self):
        """The whole no-backfill rule, on the row that would tempt an inference."""
        store = self.open_v9()
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM task_turn_criteria_continuity"
                    " WHERE task_id = 'task_hist'"
                ).fetchone()[0],
            )
        finally:
            connection.close()

    def test_integrity_and_foreign_keys_stay_clean(self):
        self.open_v9()
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            self.assertEqual(
                "ok", connection.execute("PRAGMA integrity_check").fetchone()[0]
            )
            self.assertEqual(
                [], connection.execute("PRAGMA foreign_key_check").fetchall()
            )
        finally:
            connection.close()

    def test_reopening_is_idempotent(self):
        self.open_v9()
        after_first = snapshot(self.database)
        store = TaskStore(self.config)
        store.turns("task_hist")
        store.close()
        self.assertEqual(after_first, snapshot(self.database))
        self.assertEqual(9, self.schema_version())

    def test_a_fresh_database_arrives_at_version_nine(self):
        fresh = tempfile.TemporaryDirectory()
        self.addCleanup(fresh.cleanup)
        config = load_config(Path(fresh.name))
        config.ensure_dirs()
        (Path(fresh.name) / "state" / "tasks").mkdir(parents=True, exist_ok=True)
        store = TaskStore(config)
        self.addCleanup(store.close)
        store.turns("nothing")
        database = Path(fresh.name) / "state" / "tasks" / "tasks.sqlite3"
        connection = sqlite3.connect("file:%s?mode=ro" % database, uri=True)
        try:
            self.assertEqual(
                "9",
                connection.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()[0],
            )
        finally:
            connection.close()


class TheUpgradeRunsNothing(MigrationCase):
    """It is a `CREATE TABLE IF NOT EXISTS` and a version write. Nothing else."""

    def test_the_upgrade_runs_no_subprocess(self):
        import subprocess

        original = subprocess.run

        def refuse(*args, **kwargs):
            raise AssertionError("the migration ran a subprocess")

        subprocess.run = refuse
        try:
            self.open_v9()
        finally:
            subprocess.run = original
        self.assertEqual(9, self.schema_version())

    def test_the_upgrade_reads_no_repository(self):
        """No Git, no working tree, no project root — it is a schema change."""
        import subprocess

        original_popen = subprocess.Popen

        def refuse(*args, **kwargs):
            raise AssertionError("the migration started a process")

        subprocess.Popen = refuse
        try:
            self.open_v9()
        finally:
            subprocess.Popen = original_popen


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
