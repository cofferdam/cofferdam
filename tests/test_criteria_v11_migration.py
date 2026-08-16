"""M2K PR17 — the v10 → v11 criterion-items rebuild, and everything it must not do.

The first destructive-shape migration this project has performed. Every schema
step before it was a pure `CREATE TABLE IF NOT EXISTS`; this one moves rows
between tables, so it is tested to a different standard: a realistic v10 fixture
is captured in full before, compared field-for-field after, and the rebuild is
interrupted at every step to prove it either completes or leaves v10 intact.

The fixture is built from the **merged pre-PR17 schema script**, read out of git
rather than reconstructed, so what is migrated is the real v10 format.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.config import load_config
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore
from cofferdam.workstation.tasks.errors import StoreUnavailable

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The merged commit immediately before PR17 — the last build on schema v10.
PRE_MIGRATION_COMMIT = "c1d6f1d07067f3b9c9b7cf89aeec83a2437b8a5a"

#: Every criterion shape v10 could store, including the awkward ones: a rename
#: with two paths, an operation, a manual criterion with no structured fields,
#: unicode, and a NULL description.
CRITERION_ROWS = (
    ("criterion_a1", "task_aaa", 1, 1, "evidence", "path_changed", "a.py", None, None, None),
    ("criterion_a2", "task_aaa", 1, 2, "evidence", "path_operation", "b.py", None, "created", "make b"),
    ("criterion_a3", "task_aaa", 1, 3, "evidence", "rename", "c.py", "d.py", None, None),
    ("criterion_a4", "task_aaa", 1, 4, "manual", None, None, None, None, "a person looks at it"),
    ("criterion_a5", "task_aaa", 2, 1, "evidence", "path_operation", "e.py", None, "deleted", None),
    ("criterion_b1", "task_bbb", 1, 1, "evidence", "path_changed", "z.py", None, None, "unicode ✓ kept"),
)


def v10_schema_script():
    """The shipped v10 schema, verbatim from the merged commit."""
    result = subprocess.run(
        ["git", "show", "%s:cofferdam/workstation/tasks/store.py" % PRE_MIGRATION_COMMIT],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    source = result.stdout
    start = source.index('_SCHEMA = """') + len('_SCHEMA = """')
    return source[start : source.index('"""', start)]


class MigrationCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.script = v10_schema_script()
        except (OSError, ValueError):  # pragma: no cover - environment dependent
            cls.script = None

    def setUp(self) -> None:
        if self.script is None:  # pragma: no cover - environment dependent
            self.skipTest("the pre-PR17 commit is not in this checkout")
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.config = load_config(self.home)
        self.config.ensure_dirs()
        self.database = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.build_v10()

    def build_v10(self) -> None:
        connection = sqlite3.connect(str(self.database))
        try:
            connection.executescript(self.script)
            self.assertNotIn(
                "'path_exists'",
                self.script,
                "the v10 fixture already admits the new predicate",
            )
            connection.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('schema_version','10')"
            )
            connection.execute("PRAGMA foreign_keys=ON")
            for task in ("task_aaa", "task_bbb"):
                connection.execute(
                    "INSERT INTO tasks (task_id,correlation_id,origin,adapter_id,"
                    "project_id,state,lifecycle_revision,created_at,updated_at,prompt)"
                    " VALUES (?,?,'pwa','validation','demo','completed',1,"
                    "'2026-01-01T00:00:00Z','2026-01-01T00:00:01Z','p')",
                    (task, "cor_" + task),
                )
            for task, turn in (("task_aaa", 1), ("task_aaa", 2), ("task_bbb", 1)):
                connection.execute(
                    "INSERT INTO task_turns (task_id,turn_number,provider,source,"
                    "started_at,completed_at,outcome)"
                    " VALUES (?,?,'validation','pwa','s','e','completed')",
                    (task, turn),
                )
                connection.execute(
                    "INSERT INTO task_turn_criteria (snapshot_id,task_id,turn_number,"
                    "criteria_state,criterion_count,criteria_fingerprint,dispatch_state,"
                    "recorded_at) VALUES (?,?,?,'present',?,?,'dispatch_started','r')",
                    (
                        "snapshot_%s_%d" % (task[-3:], turn),
                        task,
                        turn,
                        len([r for r in CRITERION_ROWS if r[1] == task and r[2] == turn]),
                        "f" * 64,
                    ),
                )
            for row in CRITERION_ROWS:
                connection.execute(
                    "INSERT INTO task_turn_criterion_items (criterion_id,task_id,"
                    "turn_number,ordinal,kind,predicate,path,to_path,operation,"
                    "description) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    row,
                )
            connection.execute(
                "INSERT INTO task_turn_evaluations (evaluation_id,task_id,turn_number,"
                "evaluator_version,criteria_state,criteria_snapshot_id,"
                "criteria_fingerprint,assembler_version,evidence_input_fingerprint,"
                "result_count,evaluation_fingerprint,recorded_at)"
                " VALUES ('eval_0001','task_aaa',1,1,'present','snapshot_aaa_1',?,3,?,4,?,'r')",
                ("f" * 64, "b" * 64, "d" * 64),
            )
            for ordinal, (criterion, result, reason) in enumerate(
                (
                    ("criterion_a1", "met", "machine_change_observed"),
                    ("criterion_a2", "not_met", "complete_incompatible_operation"),
                    ("criterion_a3", "unverified", "evidence_not_attributable"),
                    ("criterion_a4", "unverified", "manual_criterion"),
                ),
                start=1,
            ):
                connection.execute(
                    "INSERT INTO task_turn_criterion_results VALUES ('eval_0001',?,?,?,?)",
                    (criterion, ordinal, result, reason),
                )
            connection.execute(
                "INSERT INTO task_turn_criteria_continuity (task_id,turn_number,"
                "continuity_id,continuity_state,mode,current_snapshot_id,"
                "predecessor_snapshot_id,continuity_fingerprint,relation_count,"
                "dispatch_state,recorded_at) VALUES ('task_aaa',2,'cont_0001','declared',"
                "'revise','snapshot_aaa_2','snapshot_aaa_1',?,1,'dispatch_started','r')",
                ("f" * 64,),
            )
            connection.execute(
                "INSERT INTO task_turn_criterion_supersessions (task_id,turn_number,"
                "ordinal,predecessor_criterion_id,current_criterion_id)"
                " VALUES ('task_aaa',2,1,'criterion_a1','criterion_a5')"
            )
            connection.execute(
                "INSERT INTO task_turn_final_state (task_id,turn_number,observation_id,"
                "observer_version,observation_state,limitation_reason,lineage_fingerprint,"
                "head_revision,path_count,observation_fingerprint,recorded_at)"
                " VALUES ('task_aaa',1,'fst_0001',1,'complete',NULL,?,'abc123',1,?,'r')",
                ("f" * 64, "d" * 64),
            )
            connection.execute(
                "INSERT INTO task_turn_final_state_paths"
                " VALUES ('task_aaa',1,1,'a.py','present','file',NULL)"
            )
            connection.commit()
        finally:
            connection.close()

    # -- capture ------------------------------------------------------------

    def capture(self):
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            objects = {
                (row[0], row[1]): row[2]
                for row in connection.execute(
                    "SELECT type, name, sql FROM sqlite_master"
                )
            }
            rows = {}
            for (kind, name) in list(objects):
                if kind != "table":
                    continue
                rows[name] = sorted(
                    (tuple(item) for item in connection.execute('SELECT * FROM "%s"' % name)),
                    key=lambda value: json.dumps(value, default=str),
                )
            return {"objects": objects, "rows": rows}
        finally:
            connection.close()

    def open_v11(self):
        store = TaskStore(self.config)
        self.addCleanup(store.close)
        store.turns("task_aaa")
        return store

    def schema_version(self):
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            return int(
                connection.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()[0]
            )
        finally:
            connection.close()


class TheRebuild(MigrationCase):
    def test_the_build_declares_version_eleven(self):
        self.assertEqual(11, SCHEMA_VERSION)

    def test_the_fixture_really_is_version_ten(self):
        self.assertEqual(10, self.schema_version())

    def test_opening_records_version_eleven(self):
        self.open_v11()
        self.assertEqual(SCHEMA_VERSION, self.schema_version())

    def test_every_criterion_row_survives_field_for_field(self):
        before = self.capture()["rows"]["task_turn_criterion_items"]
        self.open_v11()
        self.assertEqual(before, self.capture()["rows"]["task_turn_criterion_items"])
        self.assertEqual(len(CRITERION_ROWS), len(before))

    def test_every_criterion_id_is_preserved_exactly(self):
        self.open_v11()
        stored = {row[0] for row in self.capture()["rows"]["task_turn_criterion_items"]}
        self.assertEqual({row[0] for row in CRITERION_ROWS}, stored)

    def test_no_other_table_loses_or_gains_a_row(self):
        before = self.capture()["rows"]
        self.open_v11()
        after = self.capture()["rows"]
        for table in sorted(set(before) & set(after)):
            if table == "schema_meta":
                continue
            self.assertEqual(before[table], after[table], "%s changed" % table)

    def test_the_evaluation_results_still_reference_their_criteria(self):
        before = self.capture()["rows"]["task_turn_criterion_results"]
        self.open_v11()
        self.assertEqual(before, self.capture()["rows"]["task_turn_criterion_results"])
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            orphans = connection.execute(
                "SELECT r.criterion_id FROM task_turn_criterion_results r"
                " LEFT JOIN task_turn_criterion_items i"
                " ON i.criterion_id = r.criterion_id WHERE i.criterion_id IS NULL"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual([], orphans)

    def test_the_supersession_edges_still_resolve(self):
        before = self.capture()["rows"]["task_turn_criterion_supersessions"]
        self.open_v11()
        self.assertEqual(
            before, self.capture()["rows"]["task_turn_criterion_supersessions"]
        )
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            for column in ("predecessor_criterion_id", "current_criterion_id"):
                orphans = connection.execute(
                    "SELECT s.%s FROM task_turn_criterion_supersessions s"
                    " LEFT JOIN task_turn_criterion_items i ON i.criterion_id = s.%s"
                    " WHERE i.criterion_id IS NULL" % (column, column)
                ).fetchall()
                self.assertEqual([], orphans, column)
        finally:
            connection.close()

    def test_integrity_and_foreign_keys_are_clean_afterwards(self):
        self.open_v11()
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

    def test_foreign_key_enforcement_is_restored_on_the_connection(self):
        """The failure that would be worse than a failed migration."""
        store = self.open_v11()
        connection = store._connect()
        self.assertEqual(1, connection.execute("PRAGMA foreign_keys").fetchone()[0])
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO task_turn_criterion_results"
                " VALUES ('eval_0001','ghost_criterion',9,'met','machine_change_observed')"
            )

    def test_only_the_criterion_items_definition_changes(self):
        before = self.capture()["objects"]
        self.open_v11()
        after = self.capture()["objects"]
        changed = {
            key
            for key in set(before) | set(after)
            if before.get(key) != after.get(key)
        }
        self.assertEqual({("table", "task_turn_criterion_items")}, changed)

    def test_the_index_is_restored(self):
        self.open_v11()
        objects = self.capture()["objects"]
        self.assertIn(("index", "criterion_items_by_turn"), objects)

    def test_no_rebuild_table_is_left_behind(self):
        self.open_v11()
        leftovers = [
            name for (_, name) in self.capture()["objects"] if name.endswith("_v11")
        ]
        self.assertEqual([], leftovers)

    def test_reopening_changes_nothing(self):
        self.open_v11()
        settled = self.capture()
        for _ in range(4):
            store = TaskStore(self.config)
            store.turns("task_aaa")
            store.close()
        self.assertEqual(settled, self.capture())

    def test_the_migration_runs_no_subprocess(self):
        import subprocess as subprocess_module

        original = subprocess_module.run

        def refuse(*args, **kwargs):  # pragma: no cover - the point is not calling it
            raise AssertionError("the migration ran a subprocess")

        subprocess_module.run = refuse
        try:
            self.open_v11()
        finally:
            subprocess_module.run = original
        self.assertEqual(SCHEMA_VERSION, self.schema_version())

    def test_the_migration_reads_no_repository(self):
        """It is a schema change: no project root, no Git, no working tree."""
        import cofferdam.workstation.tasks.finalstate as finalstate

        def refuse(*args, **kwargs):  # pragma: no cover
            raise AssertionError("the migration looked at the world")

        saved = finalstate.observe_path, finalstate.observe_paths
        finalstate.observe_path, finalstate.observe_paths = refuse, refuse
        try:
            self.open_v11()
        finally:
            finalstate.observe_path, finalstate.observe_paths = saved


class TheNewVocabulary(MigrationCase):
    def test_the_migrated_table_admits_the_state_predicates(self):
        self.open_v11()
        connection = sqlite3.connect(str(self.database))
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            for ordinal, predicate in ((8, "path_exists"), (9, "path_absent")):
                connection.execute(
                    "INSERT INTO task_turn_criterion_items (criterion_id,task_id,"
                    "turn_number,ordinal,kind,predicate,path)"
                    " VALUES (?,'task_aaa',1,?,'evidence',?,'n.py')",
                    ("criterion_new%d" % ordinal, ordinal, predicate),
                )
            connection.commit()
        finally:
            connection.close()

    def test_an_unknown_predicate_is_still_refused(self):
        self.open_v11()
        connection = sqlite3.connect(str(self.database))
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO task_turn_criterion_items (criterion_id,task_id,"
                    "turn_number,ordinal,kind,predicate,path)"
                    " VALUES ('criterion_bad','task_aaa',1,8,'evidence','nonsense','n.py')"
                )
        finally:
            connection.close()

    def test_a_state_predicate_may_not_carry_an_operation(self):
        self.open_v11()
        connection = sqlite3.connect(str(self.database))
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO task_turn_criterion_items (criterion_id,task_id,"
                    "turn_number,ordinal,kind,predicate,path,operation)"
                    " VALUES ('criterion_bad','task_aaa',1,8,'evidence','path_exists',"
                    "'n.py','created')"
                )
        finally:
            connection.close()

    def test_a_state_predicate_may_not_carry_a_destination(self):
        self.open_v11()
        connection = sqlite3.connect(str(self.database))
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO task_turn_criterion_items (criterion_id,task_id,"
                    "turn_number,ordinal,kind,predicate,path,to_path)"
                    " VALUES ('criterion_bad','task_aaa',1,8,'evidence','path_absent',"
                    "'n.py','o.py')"
                )
        finally:
            connection.close()

    def test_no_historical_row_was_converted(self):
        """`path_operation(created)` does not become `path_exists`, ever."""
        self.open_v11()
        stored = {
            row[0]: (row[5], row[8]) for row in self.capture()["rows"]["task_turn_criterion_items"]
        }
        self.assertEqual(("path_operation", "created"), stored["criterion_a2"])
        self.assertEqual(("path_operation", "deleted"), stored["criterion_a5"])
        predicates = {value[0] for value in stored.values()}
        self.assertNotIn("path_exists", predicates)
        self.assertNotIn("path_absent", predicates)


class FreshMatchesMigrated(MigrationCase):
    def fresh_manifest(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        config = load_config(Path(home.name))
        config.ensure_dirs()
        database = Path(home.name) / "state" / "tasks" / "tasks.sqlite3"
        database.parent.mkdir(parents=True, exist_ok=True)
        store = TaskStore(config)
        self.addCleanup(store.close)
        store.turns("nothing")
        connection = sqlite3.connect("file:%s?mode=ro" % database, uri=True)
        try:
            return {
                (row[0], row[1]): (row[2] or "")
                for row in connection.execute(
                    "SELECT type, name, sql FROM sqlite_master"
                )
            }
        finally:
            connection.close()

    @staticmethod
    def unquote(sql):
        """`ALTER TABLE ... RENAME TO` stores the name quoted. Inert, but visible."""
        return sql.replace(
            'CREATE TABLE "task_turn_criterion_items"',
            "CREATE TABLE task_turn_criterion_items",
            1,
        )

    def test_a_migrated_database_matches_a_fresh_one(self):
        self.open_v11()
        migrated = {
            key: (value or "") for key, value in self.capture()["objects"].items()
        }
        fresh = self.fresh_manifest()
        self.assertEqual(set(fresh), set(migrated), "the object sets differ")
        for key in sorted(fresh):
            self.assertEqual(
                fresh[key], self.unquote(migrated[key]), "%s differs" % (key,)
            )

    def test_the_only_textual_difference_is_the_quoted_name(self):
        """Stated explicitly so it is never mistaken for schema drift."""
        self.open_v11()
        migrated = self.capture()["objects"][("table", "task_turn_criterion_items")]
        fresh = self.fresh_manifest()[("table", "task_turn_criterion_items")]
        self.assertNotEqual(fresh, migrated)
        self.assertEqual(fresh, self.unquote(migrated))


class _FaultyConnection:
    """A connection that fails the Nth statement matching a fragment.

    Attribute writes are forwarded to the real connection rather than landing on
    the proxy — ``row_factory`` in particular is assigned right after connect,
    and swallowing it would quietly give the store tuples where it expects rows.
    """

    _OWN = ("_real", "_fragment", "_occurrence", "_seen")

    def __init__(self, real, fragment, occurrence=1):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_fragment", fragment)
        object.__setattr__(self, "_occurrence", occurrence)
        object.__setattr__(self, "_seen", 0)

    def execute(self, sql, *args, **kwargs):
        if self._fragment in sql:
            object.__setattr__(self, "_seen", self._seen + 1)
            if self._seen == self._occurrence:
                raise sqlite3.OperationalError(
                    "injected failure at: %s" % self._fragment
                )
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        if name in self._OWN:  # pragma: no cover - set via object.__setattr__
            object.__setattr__(self, name, value)
        else:
            setattr(self._real, name, value)


class MigrationFailure(MigrationCase):
    """Interrupted at every step, the database is either v10 or v11 — never half."""

    def migrate_with_failure(self, fragment, occurrence=1):
        import cofferdam.workstation.tasks.store as store_module

        original = store_module.sqlite3.connect

        def connect(*args, **kwargs):
            return _FaultyConnection(original(*args, **kwargs), fragment, occurrence)

        store_module.sqlite3.connect = connect
        try:
            store = TaskStore(self.config)
            with self.assertRaises((sqlite3.OperationalError, StoreUnavailable)):
                store.turns("task_aaa")
            try:
                store.close()
            except Exception:  # pragma: no cover - the connection may be broken
                pass
        finally:
            store_module.sqlite3.connect = original

    def assert_still_v10_and_whole(self):
        """The old shape, every row present, and nothing half-built."""
        self.assertEqual(10, self.schema_version())
        captured = self.capture()
        items = captured["objects"].get(("table", "task_turn_criterion_items"))
        self.assertIsNotNone(items, "the criterion items table is gone")
        self.assertNotIn("'path_exists'", items, "the table was left half-migrated")
        self.assertEqual(
            len(CRITERION_ROWS), len(captured["rows"]["task_turn_criterion_items"])
        )
        self.assertEqual(4, len(captured["rows"]["task_turn_criterion_results"]))
        self.assertEqual(1, len(captured["rows"]["task_turn_criterion_supersessions"]))
        self.assertEqual(
            [], [name for (_, name) in captured["objects"] if name.endswith("_v11")]
        )
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

    def test_failure_before_the_new_table_is_created(self):
        self.migrate_with_failure("CREATE TABLE IF NOT EXISTS task_turn_criterion_items_v11")
        self.assert_still_v10_and_whole()

    def test_failure_during_the_copy(self):
        self.migrate_with_failure("INSERT INTO task_turn_criterion_items_v11")
        self.assert_still_v10_and_whole()

    def test_failure_before_the_old_table_is_dropped(self):
        self.migrate_with_failure("DROP TABLE task_turn_criterion_items")
        self.assert_still_v10_and_whole()

    def test_failure_during_the_rename(self):
        self.migrate_with_failure("ALTER TABLE task_turn_criterion_items_v11 RENAME")
        self.assert_still_v10_and_whole()

    def test_failure_at_the_foreign_key_check(self):
        self.migrate_with_failure("PRAGMA foreign_key_check")
        self.assert_still_v10_and_whole()

    def test_failure_at_the_commit(self):
        self.migrate_with_failure("COMMIT")
        self.assert_still_v10_and_whole()

    def test_failure_at_the_transaction_start(self):
        self.migrate_with_failure("BEGIN IMMEDIATE")
        self.assert_still_v10_and_whole()

    def test_a_failed_migration_can_be_retried_successfully(self):
        """The whole point of rolling back cleanly."""
        self.migrate_with_failure("DROP TABLE task_turn_criterion_items")
        self.assert_still_v10_and_whole()
        self.open_v11()
        self.assertEqual(SCHEMA_VERSION, self.schema_version())
        self.assertEqual(
            len(CRITERION_ROWS), len(self.capture()["rows"]["task_turn_criterion_items"])
        )

    def test_an_interrupted_version_bump_does_not_rebuild_twice(self):
        """Crash after the rename, before the version row: shape is already right.

        Detection is by stored DDL rather than by the version number precisely so
        this case is a no-op instead of a second rebuild against a table that has
        already been rebuilt.
        """
        self.open_v11()
        connection = sqlite3.connect(str(self.database))
        try:
            connection.execute(
                "UPDATE schema_meta SET value = '10' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
        settled = self.capture()["rows"]["task_turn_criterion_items"]
        store = TaskStore(self.config)
        self.addCleanup(store.close)
        store.turns("task_aaa")
        self.assertEqual(settled, self.capture()["rows"]["task_turn_criterion_items"])
        self.assertEqual(SCHEMA_VERSION, self.schema_version())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
