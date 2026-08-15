"""M2K PR7 — schema v8, the v7 → v8 migration, and the v7 runtime meeting v8.

Schema v8 adds exactly two tables: ``task_turn_evaluations`` and
``task_turn_criterion_results``. They exist because an evaluation is produced
**after** a turn is durably closed, and PR5's trick of writing an observation
into ``task_events.evidence_json`` cannot be reused: an event appended after the
close sits above ``closed_through_event_sequence`` and belongs to no turn, and
moving a closed bound to make room is exactly the rewrite bounds exist to
prevent.

The migration writes **nothing**. It does not evaluate a historical turn, parse a
prompt, interpret a claim or fabricate criteria. A live v7 database exists on the
production host with 25 tasks, 473 events and 3 turns, none of which was ever
asked a question — they have no criteria snapshot, so they can have no
evaluation, and the honest record of that is an empty table.

``V7_SCHEMA`` below is the v7 schema written out longhand — the v6 script this
repository already keeps in ``test_criteria_migration``, plus PR6's two criteria
tables typed out again. Importing the live constant would make the test pass by
construction the day somebody changes it; typing it out is what makes it a test
of the *upgrade*.
"""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.evaluation import EVALUATOR_VERSION
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

from .test_criteria_migration import V6_SCHEMA_CREATE_ONLY

EVALUATION_TABLE = "task_turn_evaluations"
RESULT_TABLE = "task_turn_criterion_results"

#: PR6's two tables, longhand, appended to the v6 script to make a v7 database.
V7_CRITERIA_TABLES = """
CREATE TABLE IF NOT EXISTS task_turn_criteria (
    task_id              TEXT    NOT NULL,
    turn_number          INTEGER NOT NULL,
    criteria_state       TEXT    NOT NULL,
    snapshot_id          TEXT    NOT NULL,
    criteria_fingerprint TEXT    NOT NULL,
    criterion_count      INTEGER NOT NULL,
    dispatch_state       TEXT    NOT NULL,
    recorded_at          TEXT    NOT NULL,
    PRIMARY KEY (task_id, turn_number),
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE,
    CHECK (turn_number >= 1),
    CHECK (criteria_state IN ('present', 'not_provided')),
    CHECK (dispatch_state IN
           ('captured', 'dispatch_started', 'dispatch_refused', 'turn_opened')),
    CHECK (criterion_count >= 0),
    CHECK ((criteria_state = 'present') = (criterion_count > 0)),
    CHECK (length(criteria_fingerprint) = 64),
    CHECK (length(snapshot_id) BETWEEN 8 AND 64)
);
CREATE UNIQUE INDEX IF NOT EXISTS criteria_by_snapshot
    ON task_turn_criteria (snapshot_id);
CREATE TABLE IF NOT EXISTS task_turn_criterion_items (
    criterion_id TEXT    PRIMARY KEY,
    task_id      TEXT    NOT NULL,
    turn_number  INTEGER NOT NULL,
    ordinal      INTEGER NOT NULL,
    kind         TEXT    NOT NULL,
    predicate    TEXT,
    path         TEXT,
    to_path      TEXT,
    operation    TEXT,
    description  TEXT,
    FOREIGN KEY (task_id, turn_number)
        REFERENCES task_turn_criteria (task_id, turn_number) ON DELETE CASCADE,
    UNIQUE (task_id, turn_number, ordinal),
    CHECK (ordinal >= 1),
    CHECK (kind IN ('evidence', 'manual')),
    CHECK ((kind = 'evidence') = (predicate IS NOT NULL)),
    CHECK ((kind = 'evidence') = (path IS NOT NULL)),
    CHECK (predicate IS NULL
           OR predicate IN ('path_changed', 'path_operation', 'rename')),
    CHECK ((predicate = 'path_operation') = (operation IS NOT NULL)),
    CHECK (operation IS NULL
           OR operation IN ('created', 'modified', 'deleted')),
    CHECK ((predicate = 'rename') = (to_path IS NOT NULL)),
    CHECK (to_path IS NULL OR to_path <> path),
    CHECK (kind <> 'manual' OR description IS NOT NULL),
    CHECK (path IS NULL OR length(path) BETWEEN 1 AND 512),
    CHECK (to_path IS NULL OR length(to_path) BETWEEN 1 AND 512),
    CHECK (description IS NULL OR length(description) BETWEEN 1 AND 500)
);
CREATE INDEX IF NOT EXISTS criterion_items_by_turn
    ON task_turn_criterion_items (task_id, turn_number, ordinal);
"""

V7_VERSION = 7
V7_SCHEMA_CREATE_ONLY = V6_SCHEMA_CREATE_ONLY + V7_CRITERIA_TABLES
V7_SCHEMA = V7_SCHEMA_CREATE_ONLY + (
    "INSERT INTO schema_meta (key, value) VALUES ('schema_version', '7');"
)


def _open_store(home: Path) -> TaskStore:
    from cofferdam.workstation.config import load_config

    config = load_config(home)
    config.ensure_dirs()
    return TaskStore(config)


class CleanDatabaseTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr7-clean-")
        self.addCleanup(self._temp.cleanup)
        self.home = Path(self._temp.name)
        self.store = _open_store(self.home)
        self.addCleanup(self.store.close)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.store.storage_health()

    def _db(self):
        return sqlite3.connect(str(self.path))

    def _tables(self):
        with self._db() as db:
            return {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }

    def test_the_schema_version_is_eight(self):
        self.assertEqual(SCHEMA_VERSION, 8)

    def test_both_evaluation_tables_exist(self):
        self.assertIn(EVALUATION_TABLE, self._tables())
        self.assertIn(RESULT_TABLE, self._tables())

    def test_the_evaluation_table_has_exactly_these_columns(self):
        """No verdict, no aggregate, no score, no copied evidence."""
        with self._db() as db:
            columns = {r[1] for r in db.execute("PRAGMA table_info(%s)" % EVALUATION_TABLE)}
        self.assertEqual(
            columns,
            {
                "evaluation_id", "task_id", "turn_number", "evaluator_version",
                "criteria_state", "criteria_snapshot_id", "criteria_fingerprint",
                "assembler_version", "evidence_input_fingerprint", "result_count",
                "evaluation_fingerprint", "recorded_at",
            },
        )

    def test_the_result_table_has_exactly_these_columns(self):
        """No explanation column. The absence is the design."""
        with self._db() as db:
            columns = {r[1] for r in db.execute("PRAGMA table_info(%s)" % RESULT_TABLE)}
        self.assertEqual(
            columns,
            {"evaluation_id", "criterion_id", "ordinal", "result", "reason"},
        )

    def test_the_evaluation_foreign_key_is_the_turn(self):
        """Unlike v6 and v7: an evaluation exists only for a turn that closed."""
        with self._db() as db:
            keys = [tuple(r) for r in db.execute("PRAGMA foreign_key_list(%s)" % EVALUATION_TABLE)]
        targets = {row[2] for row in keys}
        self.assertIn("task_turns", targets)
        self.assertIn("task_turn_criteria", targets)
        turn_key = [row for row in keys if row[2] == "task_turns"]
        self.assertEqual(
            {(row[3], row[4]) for row in turn_key},
            {("task_id", "task_id"), ("turn_number", "turn_number")},
        )
        self.assertEqual(len({row[0] for row in turn_key}), 1, "one composite FK")

    def test_an_evaluation_cannot_reference_a_turn_that_does_not_exist(self):
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (evaluation_id,task_id,turn_number,evaluator_version,"
                    "criteria_state,criteria_snapshot_id,criteria_fingerprint,"
                    "assembler_version,evidence_input_fingerprint,result_count,"
                    "evaluation_fingerprint,recorded_at) VALUES"
                    " ('evl_x','task_nope',1,1,'not_provided','acs_x','%s',3,'%s',0,'%s','x')"
                    % (EVALUATION_TABLE, "0" * 64, "0" * 64, "0" * 64)
                )

    def test_the_result_vocabulary_is_closed_in_the_schema(self):
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=OFF")
            for bad in ("failed", "passed", "error", "skipped", "maybe"):
                with self.assertRaises(sqlite3.IntegrityError, msg=bad):
                    db.execute(
                        "INSERT INTO %s (evaluation_id,criterion_id,ordinal,result,reason)"
                        " VALUES ('evl_x','acr_x',1,?,'r')" % RESULT_TABLE,
                        (bad,),
                    )

    def test_not_provided_cannot_carry_results(self):
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (evaluation_id,task_id,turn_number,evaluator_version,"
                    "criteria_state,criteria_snapshot_id,criteria_fingerprint,"
                    "assembler_version,evidence_input_fingerprint,result_count,"
                    "evaluation_fingerprint,recorded_at) VALUES"
                    " ('evl_x','t',1,1,'not_provided','acs_x','%s',3,'%s',2,'%s','x')"
                    % (EVALUATION_TABLE, "0" * 64, "0" * 64, "0" * 64)
                )

    def test_present_cannot_be_empty(self):
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (evaluation_id,task_id,turn_number,evaluator_version,"
                    "criteria_state,criteria_snapshot_id,criteria_fingerprint,"
                    "assembler_version,evidence_input_fingerprint,result_count,"
                    "evaluation_fingerprint,recorded_at) VALUES"
                    " ('evl_x','t',1,1,'present','acs_x','%s',3,'%s',0,'%s','x')"
                    % (EVALUATION_TABLE, "0" * 64, "0" * 64, "0" * 64)
                )

    def test_legacy_unknown_cannot_be_written_as_a_criteria_state(self):
        with self._db() as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (evaluation_id,task_id,turn_number,evaluator_version,"
                    "criteria_state,criteria_snapshot_id,criteria_fingerprint,"
                    "assembler_version,evidence_input_fingerprint,result_count,"
                    "evaluation_fingerprint,recorded_at) VALUES"
                    " ('evl_x','t',1,1,'legacy_unknown','acs_x','%s',3,'%s',0,'%s','x')"
                    % (EVALUATION_TABLE, "0" * 64, "0" * 64, "0" * 64)
                )

    def test_the_v7_tables_are_untouched(self):
        expected = {
            "task_turn_criteria": {
                "task_id", "turn_number", "criteria_state", "snapshot_id",
                "criteria_fingerprint", "criterion_count", "dispatch_state", "recorded_at",
            },
            "task_turn_criterion_items": {
                "criterion_id", "task_id", "turn_number", "ordinal", "kind",
                "predicate", "path", "to_path", "operation", "description",
            },
            "task_turn_git_baselines": {
                "task_id", "turn_number", "capture_state", "head_state", "head_revision",
                "object_format", "working_tree_state", "status_coverage", "reason",
                "dispatch_state", "captured_at",
            },
            "task_turn_bounds": {
                "task_id", "turn_number", "opened_after_event_sequence",
                "closed_through_event_sequence",
            },
        }
        with self._db() as db:
            for table, columns in expected.items():
                found = {r[1] for r in db.execute("PRAGMA table_info(%s)" % table)}
                self.assertEqual(found, columns, table)

    def test_there_is_no_check_runner_or_command_table(self):
        names = self._tables()
        for forbidden in (
            "task_checks", "task_check_runs", "task_commands", "task_verdicts",
            "task_evaluation_aggregates", "task_scores",
        ):
            self.assertNotIn(forbidden, names)


class MigrationTests(unittest.TestCase):
    """A real v7 database, opened by this build."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr7-mig-")
        self.addCleanup(self._temp.cleanup)
        self.home = Path(self._temp.name)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.path.parent.mkdir(parents=True)

    def _build_v7(self):
        with sqlite3.connect(str(self.path)) as db:
            db.executescript(V7_SCHEMA)
            db.execute(
                "INSERT INTO tasks (task_id, correlation_id, origin, adapter_id,"
                " project_id, state, created_at, updated_at, prompt, event_cursor)"
                " VALUES ('task_legacy','corr','pwa','validation','synth','completed',"
                " '2026-08-01T00:00:00Z','2026-08-01T00:05:00Z','make login work',3)"
            )
            for seq in (1, 2, 3):
                db.execute(
                    "INSERT INTO task_events (task_id,sequence,event_type,created_at,"
                    "actor,source,lifecycle_revision,evidence_json) VALUES"
                    " ('task_legacy',?,'progress','2026-08-01T00:01:00Z','system',"
                    "'cofferdam',0,NULL)",
                    (seq,),
                )
            db.execute(
                "INSERT INTO task_turns (task_id,turn_number,provider,source,started_at,"
                "completed_at,outcome,result) VALUES ('task_legacy',1,'validation','pwa',"
                "'2026-08-01T00:00:30Z','2026-08-01T00:04:00Z','completed','I did it')"
            )
            db.execute(
                "INSERT INTO task_turn_bounds (task_id,turn_number,"
                "opened_after_event_sequence,closed_through_event_sequence)"
                " VALUES ('task_legacy',1,0,3)"
            )
            db.execute(
                "INSERT INTO task_turn_git_baselines (task_id,turn_number,capture_state,"
                "head_state,head_revision,object_format,working_tree_state,status_coverage,"
                "reason,dispatch_state,captured_at) VALUES ('task_legacy',1,'captured',"
                "'present','%s','sha1','clean','complete',NULL,'turn_opened','x')" % ("a" * 40)
            )
            # A v7 task that DOES have criteria, so the migration has something
            # it could wrongly evaluate — and must not.
            db.execute(
                "INSERT INTO tasks (task_id, correlation_id, origin, adapter_id,"
                " project_id, state, created_at, updated_at, prompt, event_cursor)"
                " VALUES ('task_v7','corr2','pwa','validation','synth','completed',"
                " 'x','y','p',3)"
            )
            db.execute(
                "INSERT INTO task_turn_criteria (task_id,turn_number,criteria_state,"
                "snapshot_id,criteria_fingerprint,criterion_count,dispatch_state,recorded_at)"
                " VALUES ('task_v7',1,'present','acs_seeded000000000000000000','%s',1,"
                "'turn_opened','x')" % ("b" * 64)
            )
            db.execute(
                "INSERT INTO task_turn_criterion_items (criterion_id,task_id,turn_number,"
                "ordinal,kind,predicate,path) VALUES ('acr_seeded00000000000000000',"
                "'task_v7',1,1,'evidence','path_changed','src/a.py')"
            )
            db.execute(
                "INSERT INTO task_turns (task_id,turn_number,provider,source,started_at,"
                "completed_at,outcome) VALUES ('task_v7',1,'validation','pwa','x','y','completed')"
            )
            db.execute(
                "INSERT INTO task_turn_bounds (task_id,turn_number,"
                "opened_after_event_sequence,closed_through_event_sequence)"
                " VALUES ('task_v7',1,0,3)"
            )

    def _tables(self):
        with sqlite3.connect(str(self.path)) as db:
            return {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }

    def _fingerprints(self):
        reads = (
            "SELECT * FROM tasks ORDER BY task_id",
            "SELECT * FROM task_events ORDER BY task_id,sequence",
            "SELECT * FROM task_turns ORDER BY task_id,turn_number",
            "SELECT * FROM task_turn_bounds ORDER BY task_id,turn_number",
            "SELECT * FROM task_turn_git_baselines ORDER BY task_id,turn_number",
            "SELECT * FROM task_turn_criteria ORDER BY task_id,turn_number",
            "SELECT * FROM task_turn_criterion_items ORDER BY criterion_id",
            "SELECT task_id,sequence,evidence_json FROM task_events ORDER BY task_id,sequence",
        )
        out = {}
        with sqlite3.connect(str(self.path)) as db:
            for query in reads:
                h = hashlib.sha256()
                for row in db.execute(query):
                    h.update(repr(row).encode())
                out[query] = h.hexdigest()
        return out

    def test_the_fixture_starts_at_seven(self):
        self._build_v7()
        with sqlite3.connect(str(self.path)) as db:
            value = db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertEqual(int(value), 7)
        self.assertNotIn(EVALUATION_TABLE, self._tables())

    def test_opening_it_migrates_to_eight(self):
        self._build_v7()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        self.assertEqual(store.storage_health()["schema_version"], 8)
        with sqlite3.connect(str(self.path)) as db:
            value = db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertEqual(int(value), 8)

    def test_the_new_tables_are_created_empty(self):
        self._build_v7()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            for table in (EVALUATION_TABLE, RESULT_TABLE):
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0], 0, table
                )

    def test_the_migration_evaluates_nothing(self):
        """Not even the v7 turn that has criteria and a closed bound."""
        self._build_v7()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        self.assertIsNone(store.evaluation("task_v7", 1))
        self.assertIsNone(store.evaluation("task_legacy", 1))
        with sqlite3.connect(str(self.path)) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM %s" % EVALUATION_TABLE).fetchone()[0], 0
            )

    def test_the_historical_turn_is_never_evaluable(self):
        """No criteria snapshot, so `closed_turns_awaiting_evaluation` skips it."""
        self._build_v7()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        pending = store.closed_turns_awaiting_evaluation()
        self.assertNotIn(("task_legacy", 1), pending)
        self.assertIn(("task_v7", 1), pending)

    def test_every_historical_row_survives_byte_for_byte(self):
        self._build_v7()
        before = self._fingerprints()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        self.assertEqual(self._fingerprints(), before)

    def test_every_v7_table_keeps_its_columns(self):
        self._build_v7()
        with sqlite3.connect(str(self.path)) as db:
            before = {
                t: {r[1] for r in db.execute("PRAGMA table_info(%s)" % t)}
                for t in self._tables()
            }
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            for table, columns in before.items():
                found = {r[1] for r in db.execute("PRAGMA table_info(%s)" % table)}
                self.assertEqual(found, columns, table)

    def test_reopening_is_idempotent(self):
        self._build_v7()
        first = _open_store(self.home)
        first.storage_health()
        first.close()
        second = _open_store(self.home)
        self.addCleanup(second.close)
        self.assertEqual(second.storage_health()["schema_version"], 8)
        with sqlite3.connect(str(self.path)) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM %s" % EVALUATION_TABLE).fetchone()[0], 0
            )

    def test_the_migrated_database_is_intact(self):
        self._build_v7()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            self.assertEqual([r[0] for r in db.execute("PRAGMA integrity_check")], ["ok"])
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_the_migration_needs_no_git_provider_or_network(self):
        self._build_v7()
        import socket
        import subprocess

        def poison(*args, **kwargs):
            raise AssertionError("the migration reached the world")

        saved = (socket.socket, subprocess.run, subprocess.Popen)
        socket.socket, subprocess.run, subprocess.Popen = poison, poison, poison
        try:
            store = _open_store(self.home)
            self.addCleanup(store.close)
            self.assertEqual(store.storage_health()["schema_version"], 8)
        finally:
            socket.socket, subprocess.run, subprocess.Popen = saved


class OldRuntimeAgainstV8Tests(unittest.TestCase):
    """What a rollback to the PR6 build does when it meets a v8 database.

    Measured rather than assumed, for the reason the PR4 deployment found:
    ``TaskStore._connect`` runs the build's own ``_SCHEMA`` script **before** it
    checks the recorded schema version. ``V7_SCHEMA_CREATE_ONLY`` is the shipped
    v7 build's script written out longhand and ``_old_runtime_open`` reproduces
    ``_connect``'s ordering exactly, so this is a test of the rollback rather than
    of a paraphrase of it — and it runs in CI, where the v7 slot does not exist.
    """

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr7-rollback-")
        self.addCleanup(self._temp.cleanup)
        self.home = Path(self._temp.name)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"
        store = _open_store(self.home)
        store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "INSERT INTO tasks (task_id,correlation_id,origin,adapter_id,project_id,"
                "state,created_at,updated_at,prompt,event_cursor) VALUES"
                " ('task_v8','c','pwa','validation','demo','completed','x','y','p',3)"
            )
            db.execute(
                "INSERT INTO task_turn_criteria (task_id,turn_number,criteria_state,"
                "snapshot_id,criteria_fingerprint,criterion_count,dispatch_state,recorded_at)"
                " VALUES ('task_v8',1,'present','acs_v8000000000000000000000','%s',1,"
                "'turn_opened','x')" % ("b" * 64)
            )
            db.execute(
                "INSERT INTO task_turn_criterion_items (criterion_id,task_id,turn_number,"
                "ordinal,kind,predicate,path) VALUES ('acr_v8000000000000000000000',"
                "'task_v8',1,1,'evidence','path_changed','src/a.py')"
            )
            db.execute(
                "INSERT INTO task_turns (task_id,turn_number,provider,source,started_at,"
                "completed_at,outcome) VALUES ('task_v8',1,'validation','pwa','x','y','completed')"
            )
            db.execute(
                "INSERT INTO task_turn_bounds (task_id,turn_number,"
                "opened_after_event_sequence,closed_through_event_sequence)"
                " VALUES ('task_v8',1,0,3)"
            )
            db.execute(
                "INSERT INTO %s (evaluation_id,task_id,turn_number,evaluator_version,"
                "criteria_state,criteria_snapshot_id,criteria_fingerprint,assembler_version,"
                "evidence_input_fingerprint,result_count,evaluation_fingerprint,recorded_at)"
                " VALUES ('evl_v8000000000000000000000','task_v8',1,1,'present',"
                "'acs_v8000000000000000000000','%s',3,'%s',1,'%s','x')"
                % (EVALUATION_TABLE, "b" * 64, "f" * 64, "d" * 64)
            )
            db.execute(
                "INSERT INTO %s (evaluation_id,criterion_id,ordinal,result,reason) VALUES"
                " ('evl_v8000000000000000000000','acr_v8000000000000000000000',1,'met',"
                "'machine_change_observed')" % RESULT_TABLE
            )
        store.close()

    def _state(self):
        with sqlite3.connect("file:%s?mode=ro" % self.path, uri=True) as db:
            tables = sorted(r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"))
            rows = {t: db.execute("SELECT * FROM %s" % t).fetchall() for t in tables}
            schema = db.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name"
            ).fetchall()
            integrity = [r[0] for r in db.execute("PRAGMA integrity_check")]
        return {
            "bytes": hashlib.sha256(self.path.read_bytes()).hexdigest(),
            "schema": schema,
            "rows": rows,
            "integrity": integrity,
            "sidecars": {
                s: (Path(str(self.path) + s).stat().st_size
                    if Path(str(self.path) + s).exists() else None)
                for s in ("-wal", "-shm")
            },
        }

    def _old_runtime_open(self):
        """``TaskStore._connect``'s ordering, as the v7 build shipped it."""
        connection = sqlite3.connect(str(self.path), isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            # The schema script runs BEFORE the version check. This is the line
            # the PR4 deployment found, and the reason this test exists.
            connection.executescript(V7_SCHEMA_CREATE_ONLY)
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if int(row["value"]) > V7_VERSION:
                raise RuntimeError(
                    "the task database was written by a newer version of Cofferdam"
                )
            return False
        finally:
            connection.close()

    def test_the_old_runtime_refuses_a_v8_database(self):
        with self.assertRaises(RuntimeError) as caught:
            self._old_runtime_open()
        self.assertIn("newer version", str(caught.exception))

    def test_the_old_runtime_changes_nothing(self):
        before = self._state()
        with self.assertRaises(RuntimeError):
            self._old_runtime_open()
        after = self._state()
        self.assertEqual(before["schema"], after["schema"], "the schema changed")
        self.assertEqual(before["rows"], after["rows"], "rows changed")
        self.assertEqual(before["bytes"], after["bytes"], "the file changed")

    def test_the_v8_rows_survive(self):
        with self.assertRaises(RuntimeError):
            self._old_runtime_open()
        with sqlite3.connect("file:%s?mode=ro" % self.path, uri=True) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM %s" % EVALUATION_TABLE).fetchone()[0], 1
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM %s" % RESULT_TABLE).fetchone()[0], 1
            )

    def test_the_database_is_still_intact(self):
        with self.assertRaises(RuntimeError):
            self._old_runtime_open()
        with sqlite3.connect("file:%s?mode=ro" % self.path, uri=True) as db:
            db.execute("PRAGMA foreign_keys=ON")
            self.assertEqual([r[0] for r in db.execute("PRAGMA integrity_check")], ["ok"])
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_the_current_build_still_opens_it_afterwards(self):
        with self.assertRaises(RuntimeError):
            self._old_runtime_open()
        store = _open_store(self.home)
        self.addCleanup(store.close)
        stored = store.evaluation("task_v8", 1)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.evaluator_version, EVALUATOR_VERSION)
        self.assertEqual(stored.result_count, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
