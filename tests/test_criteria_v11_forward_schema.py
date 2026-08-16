"""M2K PR17 — what older runtimes do when handed a v11 database.

Two of them, because two matter for different reasons.

**The deployed PR10 runtime at `1efd49b`** is what production is actually running
today, on schema v9. If this batch were ever rolled back to it, that build would
meet a v11 file.

**The merged pre-PR17 runtime at `c1d6f1d`** is the immediate predecessor on
schema v10 — the build a rollback would land on if PR11–PR16 had already been
deployed when PR17 went out.

Neither may migrate, downgrade or otherwise disturb a database written by a newer
build before declining to use it. `-shm` is deliberately not pinned: PR14
established that it is scratch state SQLite rewrites on an ordinary read-only
open, so asserting it byte-identical tests the wrong thing.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Production's actual runtime — M2K PR10, schema v9.
DEPLOYED_COMMIT = "1efd49b13fe6041c4bc4b22c9a07975f7f4738fe"

#: The merged build immediately before this one — schema v10.
PREVIOUS_COMMIT = "c1d6f1d07067f3b9c9b7cf89aeec83a2437b8a5a"


def store_source(commit):
    try:
        result = subprocess.run(
            ["git", "show", "%s:cofferdam/workstation/tasks/store.py" % commit],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    return result.stdout if result.returncode == 0 else None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ForwardSchemaCase(unittest.TestCase):
    """A real v11 database, offered to a real older store."""

    COMMIT = None
    EXPECTED_VERSION = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = store_source(cls.COMMIT) if cls.COMMIT else None

    def setUp(self) -> None:
        if self.COMMIT is None:
            self.skipTest("base class")
        if self.source is None:  # pragma: no cover - environment dependent
            self.skipTest("the commit %s is not in this checkout" % self.COMMIT)
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)

        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.tasks.store import TaskStore

        self.config = load_config(self.home)
        self.config.ensure_dirs()
        self.database = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.database.parent.mkdir(parents=True, exist_ok=True)

        store = TaskStore(self.config)
        store.turns("nothing")
        store.close()
        with sqlite3.connect(str(self.database)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                "INSERT INTO tasks (task_id, correlation_id, origin, adapter_id,"
                " project_id, state, lifecycle_revision, created_at, updated_at,"
                " prompt) VALUES ('task_v11','cor','pwa','validation','demo',"
                "'completed',1,'2026-08-17T00:00:00Z','2026-08-17T00:00:01Z','p')"
            )
        with sqlite3.connect(str(self.database)) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def old_store_module(self):
        module = types.ModuleType("older_store")
        module.__file__ = str(
            REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "store.py"
        )
        module.__package__ = "cofferdam.workstation.tasks"
        code = compile(self.source, module.__file__, "exec")
        sys.modules["older_store"] = module
        self.addCleanup(sys.modules.pop, "older_store", None)
        exec(code, module.__dict__)
        return module

    def snapshot(self):
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            objects = connection.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            ).fetchall()
            rows = {
                name: connection.execute('SELECT * FROM "%s"' % name).fetchall()
                for kind, name, _ in objects
                if kind == "table"
            }
            return {"objects": objects, "rows": rows}
        finally:
            connection.close()

    def refuse(self, module):
        store = module.TaskStore(self.config)
        self.addCleanup(store.close)
        with self.assertRaises(module.StoreUnavailable):
            store.turns("task_v11")
        store.close()

    # -- the assertions -----------------------------------------------------

    def test_the_old_build_declares_its_own_version(self):
        self.assertEqual(self.EXPECTED_VERSION, self.old_store_module().SCHEMA_VERSION)

    def test_the_database_really_is_version_eleven(self):
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            self.assertEqual(
                "11",
                connection.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()[0],
            )
        finally:
            connection.close()

    def test_it_refuses_to_open_it(self):
        self.refuse(self.old_store_module())

    def test_the_refusal_names_the_newer_version(self):
        module = self.old_store_module()
        store = module.TaskStore(self.config)
        self.addCleanup(store.close)
        try:
            store.turns("task_v11")
        except module.StoreUnavailable as failure:
            self.assertIn("newer version", str(getattr(failure, "detail", "") or failure))
        else:  # pragma: no cover
            self.fail("the old runtime did not refuse")

    def test_the_refusal_mutates_no_row(self):
        before = self.snapshot()["rows"]
        self.refuse(self.old_store_module())
        self.assertEqual(before, self.snapshot()["rows"])

    def test_the_refusal_mutates_no_table_definition(self):
        before = self.snapshot()["objects"]
        self.refuse(self.old_store_module())
        self.assertEqual(before, self.snapshot()["objects"])

    def test_the_criterion_items_table_is_not_rebuilt_backwards(self):
        """The one thing a downgrade must never attempt."""
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            before = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='task_turn_criterion_items'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertIn("'path_exists'", before)
        self.refuse(self.old_store_module())
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            after = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='task_turn_criterion_items'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(before, after)

    def test_the_recorded_version_is_not_downgraded(self):
        self.refuse(self.old_store_module())
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            self.assertEqual(
                "11",
                connection.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()[0],
            )
        finally:
            connection.close()

    def test_the_main_database_file_is_byte_identical(self):
        before = digest(self.database)
        self.refuse(self.old_store_module())
        self.assertEqual(before, digest(self.database))

    def test_no_journal_write_escapes_the_refusal(self):
        """`-shm` excluded: PR14 recorded why it is scratch state, not durable."""
        wal = Path(str(self.database) + "-wal")
        before = digest(wal) if wal.exists() else None
        self.refuse(self.old_store_module())
        after = digest(wal) if wal.exists() else None
        self.assertEqual(before, after)
        self.assertIn(after, (None, hashlib.sha256(b"").hexdigest()))

    def test_integrity_and_foreign_keys_survive(self):
        self.refuse(self.old_store_module())
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

    def test_the_current_build_still_opens_it_afterwards(self):
        from cofferdam.workstation.tasks.store import TaskStore

        self.refuse(self.old_store_module())
        current = TaskStore(self.config)
        self.addCleanup(current.close)
        self.assertEqual([], current.turns("task_v11"))


class TheDeployedPr10Runtime(ForwardSchemaCase):
    """`1efd49b` — what production is actually running, on schema v9."""

    COMMIT = DEPLOYED_COMMIT
    EXPECTED_VERSION = 9


class TheMergedPreMigrationRuntime(ForwardSchemaCase):
    """`c1d6f1d` — the immediate predecessor, on schema v10."""

    COMMIT = PREVIOUS_COMMIT
    EXPECTED_VERSION = 10


del ForwardSchemaCase  # the base class is not itself a test


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
