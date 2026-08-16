"""M2K PR14 — what the DEPLOYED PR10 runtime does when handed a v10 database.

This is the rollback question, and it is not answered by "the schema is
additive". The deployed build declares ``SCHEMA_VERSION = 9`` and its store
refuses anything higher, so the property to prove is that the refusal is
**clean**: the old runtime must not write to, migrate, or otherwise disturb a
database written by the newer one before it declines to use it.

The old runtime here is not a simulation. It is the real
``cofferdam/workstation/tasks/store.py`` from the deployed commit, loaded from
``git show`` into a module of its own, so what is measured is the code that is
actually running in production rather than a description of it.

If this file is ever unable to find the deployed source it **skips rather than
passes**, because a green tick that proved nothing would be worse than a gap.
"""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The commit **actually running in production** when PR14 was written: M2K PR10
#: in slot A. Not PR11 and not PR12 — both are merged and deliberately
#: undeployed, so testing either as the rollback runtime would measure a build
#: no service is running. This is the one whose refusal matters.
DEPLOYED_COMMIT = "1efd49b13fe6041c4bc4b22c9a07975f7f4738fe"


def deployed_store_source():
    """The store module exactly as the deployed build has it, or ``None``."""
    try:
        result = subprocess.run(
            ["git", "show", "%s:cofferdam/workstation/tasks/store.py" % DEPLOYED_COMMIT],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ForwardSchemaCase(unittest.TestCase):
    """A real v10 database, offered to the real deployed v9 store."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = deployed_store_source()

    def setUp(self) -> None:
        if self.source is None:  # pragma: no cover - environment dependent
            self.skipTest(
                "the deployed commit %s is not in this checkout" % DEPLOYED_COMMIT
            )
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)

        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.tasks.store import TaskStore

        self.config = load_config(self.home)
        self.config.ensure_dirs()
        self.database = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.database.parent.mkdir(parents=True, exist_ok=True)

        # Build a genuine v10 database with the current build, and put a task in
        # it so there is history for the deployed runtime to fail to touch.
        store = TaskStore(self.config)
        store.turns("nothing")
        store.close()
        with sqlite3.connect(str(self.database)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute(
                "INSERT INTO tasks (task_id, correlation_id, origin, adapter_id,"
                " project_id, state, lifecycle_revision, created_at, updated_at,"
                " prompt) VALUES ('task_v10', 'corr', 'pwa', 'validation', 'demo',"
                " 'completed', 1, '2026-08-16T00:00:00Z', '2026-08-16T00:00:01Z','p')"
            )
        # Checkpoint the WAL so the comparison below is against settled files.
        with sqlite3.connect(str(self.database)) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def old_store_module(self):
        """The deployed store, imported under its own name."""
        module = types.ModuleType("deployed_store")
        module.__file__ = str(
            REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "store.py"
        )
        module.__package__ = "cofferdam.workstation.tasks"
        code = compile(self.source, module.__file__, "exec")
        sys.modules["deployed_store"] = module
        self.addCleanup(sys.modules.pop, "deployed_store", None)
        exec(code, module.__dict__)
        return module

    def snapshot(self):
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            objects = connection.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            ).fetchall()
            tables = [n for t, n, _ in objects if t == "table"]
            rows = {
                table: connection.execute('SELECT * FROM "%s"' % table).fetchall()
                for table in tables
            }
            return {"objects": objects, "rows": rows}
        finally:
            connection.close()

    def files(self):
        out = {}
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.database) + suffix)
            out[suffix or "main"] = digest(path) if path.exists() else None
        return out


class TheDeployedRuntimeRefusesCleanly(ForwardSchemaCase):
    def test_the_deployed_build_declares_version_nine(self):
        module = self.old_store_module()
        self.assertEqual(9, module.SCHEMA_VERSION)

    def test_the_database_really_is_version_ten(self):
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            self.assertEqual(
                "10",
                connection.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()[0],
            )
        finally:
            connection.close()

    def test_the_deployed_store_refuses_to_open_it(self):
        module = self.old_store_module()
        store = module.TaskStore(self.config)
        self.addCleanup(store.close)
        with self.assertRaises(module.StoreUnavailable):
            store.turns("task_v10")

    def test_the_refusal_names_the_newer_version(self):
        module = self.old_store_module()
        store = module.TaskStore(self.config)
        self.addCleanup(store.close)
        try:
            store.turns("task_v10")
        except module.StoreUnavailable as refusal:
            # The reason is the `detail`; the message is the generic store one.
            self.assertIn("newer", (refusal.detail or "").lower())
            self.assertIn("task database", (refusal.detail or "").lower())
        else:  # pragma: no cover
            self.fail("the deployed runtime accepted a v10 database")

    def test_a_representative_read_also_refuses(self):
        module = self.old_store_module()
        store = module.TaskStore(self.config)
        self.addCleanup(store.close)
        for call in (
            lambda: store.turns("task_v10"),
            lambda: store.turn_criteria("task_v10", 1),
            lambda: store.list_tasks(limit=5),
        ):
            with self.assertRaises(module.StoreUnavailable):
                call()

    def test_the_refusal_mutates_no_row(self):
        before = self.snapshot()
        module = self.old_store_module()
        store = module.TaskStore(self.config)
        self.addCleanup(store.close)
        with self.assertRaises(module.StoreUnavailable):
            store.turns("task_v10")
        store.close()
        self.assertEqual(before["rows"], self.snapshot()["rows"])

    def test_the_refusal_mutates_no_table_definition(self):
        before = self.snapshot()
        module = self.old_store_module()
        store = module.TaskStore(self.config)
        self.addCleanup(store.close)
        with self.assertRaises(module.StoreUnavailable):
            store.turns("task_v10")
        store.close()
        after = self.snapshot()
        self.assertEqual(before["objects"], after["objects"])

    def test_the_continuity_tables_survive_the_refusal(self):
        module = self.old_store_module()
        store = module.TaskStore(self.config)
        self.addCleanup(store.close)
        with self.assertRaises(module.StoreUnavailable):
            store.turns("task_v10")
        store.close()
        names = {name for kind, name, _ in self.snapshot()["objects"] if kind == "table"}
        self.assertIn("task_turn_criteria_continuity", names)
        self.assertIn("task_turn_criterion_supersessions", names)

    def test_the_recorded_schema_version_is_not_downgraded(self):
        """The one write that would be catastrophic: stamping it back to 8."""
        module = self.old_store_module()
        store = module.TaskStore(self.config)
        self.addCleanup(store.close)
        with self.assertRaises(module.StoreUnavailable):
            store.turns("task_v10")
        store.close()
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            self.assertEqual(
                "10",
                connection.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()[0],
            )
        finally:
            connection.close()

    def test_the_main_database_file_is_byte_identical_afterwards(self):
        before = digest(self.database)
        module = self.old_store_module()
        store = module.TaskStore(self.config)
        self.addCleanup(store.close)
        with self.assertRaises(module.StoreUnavailable):
            store.turns("task_v10")
        store.close()
        self.assertEqual(before, digest(self.database))

    def test_the_wal_and_shm_sidecars_are_untouched_too(self):
        """Not even a journal write escapes the refusal.

        The main file being byte-identical would still be satisfied by a runtime
        that wrote into the WAL and simply never checkpointed — a rollback would
        then carry that write forward the next time anything opened the database.
        So the sidecars are measured as well, and the WAL stays empty: the old
        build reads `schema_meta`, sees a version it does not know, and declines
        before it opens a write transaction.
        """
        before = self.files()
        module = self.old_store_module()
        store = module.TaskStore(self.config)
        self.addCleanup(store.close)
        with self.assertRaises(module.StoreUnavailable):
            store.turns("task_v10")
        store.close()
        self.assertEqual(before, self.files())

    def test_integrity_and_foreign_keys_survive_the_refusal(self):
        module = self.old_store_module()
        store = module.TaskStore(self.config)
        self.addCleanup(store.close)
        with self.assertRaises(module.StoreUnavailable):
            store.turns("task_v10")
        store.close()
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

    def test_the_current_build_still_opens_it_after_the_refusal(self):
        """The refusal left it usable by the runtime it belongs to."""
        module = self.old_store_module()
        store = module.TaskStore(self.config)
        with self.assertRaises(module.StoreUnavailable):
            store.turns("task_v10")
        store.close()

        from cofferdam.workstation.tasks.store import TaskStore

        current = TaskStore(self.config)
        self.addCleanup(current.close)
        self.assertEqual([], current.turns("task_v10"))
        self.assertEqual(
            "legacy_unknown", current.turn_continuity("task_v10", 1).state
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
