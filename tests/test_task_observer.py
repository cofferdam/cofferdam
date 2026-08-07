"""The local observer: it watches, and that is all it can do.

The properties worth testing are the negative ones. A program that follows a
task is useful; a program that follows a task and can also touch it is a second
authority over something whose whole design rests on having one.
"""

from __future__ import annotations

import io
import sqlite3
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from ._task_doubles import PROJECT_ID, TaskTestCase, python_code_only

from cofferdam.workstation.tasks import observe

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "observe.py"


class Structure(unittest.TestCase):
    """What the module cannot do, asserted against its source."""

    @classmethod
    def setUpClass(cls):
        cls.raw = MODULE.read_text("utf-8")
        cls.code = python_code_only(cls.raw)

    def test_it_cannot_start_a_process(self):
        for forbidden in ("subprocess", "os.system", "os.popen", "Popen", "fork"):
            self.assertNotIn(forbidden, self.code, "observe.py names " + forbidden)

    def test_it_cannot_signal_a_process(self):
        for forbidden in ("kill", "SIGTERM", "SIGKILL", "signal", "pkill", "terminate"):
            self.assertNotIn(forbidden, self.code, "observe.py names " + forbidden)

    def test_it_opens_the_database_read_only(self):
        """Refused by SQLite, not by good intentions.

        Read off the raw source: `python_code_only` tokenises, so `uri=True`
        arrives as three separate tokens and a substring search for it always
        fails.
        """
        self.assertIn("mode=ro", self.raw)
        self.assertIn("uri=True", self.raw)

    def test_it_writes_no_sql_that_changes_anything(self):
        """Whole words, on code with prose removed.

        Two false positives to dodge at once: `CREATED_AT` is a column rather
        than a `CREATE`, and this module's own docstring explains why a future
        edit must not "just update one row".
        """
        import re

        for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER"):
            self.assertIsNone(
                re.search(r"\b" + forbidden + r"\b", self.code.upper()),
                "observe.py issues " + forbidden,
            )

    def test_it_is_not_a_route_and_not_a_server(self):
        for forbidden in ("app.get", "app.post", "FastAPI", "socket", "listen", "bind"):
            self.assertNotIn(forbidden, self.code)

    def test_it_reads_no_credential_or_environment_dump(self):
        self.assertNotIn("secrets", self.code)
        self.assertNotIn("token", self.code.lower())
        # It touches the environment in exactly one place, to find the database.
        self.assertEqual(self.code.count("os.environ") + self.code.count("environ\n.\nget"), 1)

    def test_it_does_not_log(self):
        for forbidden in ("logging", "logger", "journal", "syslog"):
            self.assertNotIn(forbidden, self.code)

    def test_the_daemon_does_not_import_it(self):
        """It is not part of the running service, which is the strongest form
        of "disabled by default": there is nothing to turn off."""
        import ast

        package = REPO_ROOT / "cofferdam" / "workstation"
        for path in sorted(package.rglob("*.py")):
            if path.name == "observe.py":
                continue
            tree = ast.parse(path.read_text("utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [a.name for a in node.names]
                for name in names:
                    # Exact module name. `observe_git` is a function in the
                    # Claude adapter's evidence module and has nothing to do
                    # with this — a substring match would flag it forever.
                    self.assertNotEqual(
                        name, "observe", str(path) + " imports the observer"
                    )
                    self.assertFalse(
                        name.endswith(".observe"),
                        str(path) + " imports the observer",
                    )

    def test_it_derives_the_database_name_from_the_store(self):
        """A copied constant is right until somebody renames the file."""
        self.assertIn("DATABASE_FILENAME", self.code)
        self.assertIn("TASKS_DIRNAME", self.code)


class Reading(TaskTestCase):
    """Against a real store written by the real service."""

    enable_validation_adapter = True
    project_adapters = ("validation",)

    def _observe(self, task_id, *extra):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = observe.main([task_id, "--home", str(self.home), *extra])
        return code, buffer.getvalue()

    def test_it_prints_a_task_and_its_history(self):
        row, _ = self.service.create_task(
            project_id=PROJECT_ID, adapter_id="validation", prompt="scenario: complete"
        )
        code, output = self._observe(row.task_id, "--once", "--result")
        self.assertEqual(code, 0)
        self.assertIn(row.task_id, output)
        self.assertIn("validation", output)
        self.assertIn("task_created", output)
        self.assertIn("state", output)

    def test_an_unknown_task_is_a_refusal_not_a_crash(self):
        # One real task first, because the store creates its file on the first
        # write — otherwise this would be testing "no database" rather than
        # "no such task".
        self.service.create_task(
            project_id=PROJECT_ID, adapter_id="validation", prompt="scenario: complete"
        )
        code, _ = self._observe("task_01aaaaaaaaaaaaaaaaaaaaaaaa", "--once")
        self.assertEqual(code, 2)

    def test_a_missing_database_says_so_rather_than_stack_tracing(self):
        with self.assertRaises(SystemExit) as caught:
            observe.main(["task_01aaaaaaaaaaaaaaaaaaaaaaaa", "--home", "/nonexistent"])
        self.assertIn("no task database", str(caught.exception))

    def test_evidence_is_printed_with_who_looked(self):
        row, _ = self.service.create_task(
            project_id=PROJECT_ID, adapter_id="validation", prompt="scenario: complete"
        )
        _, output = self._observe(row.task_id, "--once")
        if "evidence:" in output:
            self.assertTrue(
                "claimed" in output or "observed" in output,
                "evidence was printed without saying who looked",
            )

    def test_watching_cannot_write_to_the_store(self):
        """The handle itself refuses, so this is a property and not a habit."""
        row, _ = self.service.create_task(
            project_id=PROJECT_ID, adapter_id="validation", prompt="scenario: complete"
        )
        connection = observe._connect(observe.database_path(str(self.home)))
        self.addCleanup(connection.close)
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute("DELETE FROM task_events WHERE task_id = ?", (row.task_id,))
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute(
                "UPDATE tasks SET state = 'completed' WHERE task_id = ?", (row.task_id,)
            )
        # And the task is exactly as it was.
        self.assertTrue(self.store.get(row.task_id))

    def test_a_terminal_task_ends_the_watch_rather_than_spinning(self):
        row, _ = self.service.create_task(
            project_id=PROJECT_ID, adapter_id="validation", prompt="scenario: fail"
        )
        code, output = self._observe(row.task_id)
        self.assertEqual(code, 0)
        self.assertIn("failed", output)

    def test_long_text_is_bounded(self):
        self.assertLessEqual(len(observe._bounded("x" * 99999)), observe.MAX_LINE_CHARS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
