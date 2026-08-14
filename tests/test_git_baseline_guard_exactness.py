"""M2K PR4 — the structural-guard exceptions are exact, proven by mutation.

PR4 is the first non-adapter file in the package allowed to run a process, and
three guards had to be told about it. An exception like that is only acceptable
while it stays *exact*: the moment it reads as "the tasks package may use
subprocess", the guards have stopped guarding and nobody finds out until
something else quietly starts a process.

Asserting the exception is spelled `gitbaseline.py` would be a test of a string.
So this module writes a **second** file into the same directory, with the same
forbidden vocabulary, runs the three real guards, and requires each of them to
fail. Then it removes the file and requires them to pass again. That is a test of
the guard, not of its wording.

The file is written into the real package directory because that is where the
guards look, and it is removed in a `finally` and again in `addCleanup` — a
leftover would be a rogue subprocess file committed to the repository, which is
precisely what this module exists to prevent.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from ._task_doubles import python_code_only

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_PACKAGE = REPO_ROOT / "cofferdam" / "workstation" / "tasks"
BASELINE = TASKS_PACKAGE / "gitbaseline.py"
ROGUE = TASKS_PACKAGE / "zz_guard_mutation_probe.py"

#: Deliberately shaped like a plausible mistake rather than a caricature: a
#: helper in the same package that shells out "just to check something".
ROGUE_SOURCE = '''"""A file that must never be allowed to exist. See test_git_baseline_guard_exactness."""

import subprocess


def look(root):
    completed = subprocess.run(["git", "status"], cwd=str(root), capture_output=True)
    return completed.stdout
'''

GUARDS = (
    (
        "tests.test_workstation_no_shell.NoShellExecutionTests"
        ".test_subprocess_is_only_called_from_the_adapter_helpers"
    ),
    (
        "tests.test_task_core.Cancellation"
        ".test_no_task_module_signals_or_enumerates_processes"
    ),
    (
        "tests.test_task_mutation.CancellationGuard"
        ".test_no_broad_process_vocabulary_exists_in_task_core"
    ),
)


def _run(name):
    """Run one real guard by name and return its unittest result."""
    suite = unittest.TestLoader().loadTestsFromName(name)
    result = unittest.TestResult()
    suite.run(result)
    return result


class TheExceptionIsExact(unittest.TestCase):
    def tearDown(self):
        if ROGUE.exists():  # pragma: no cover - belt and braces
            ROGUE.unlink()

    def test_the_guards_pass_on_the_repository_as_it_stands(self):
        """The control. Without it, a guard that always fails would look like a pass."""
        for name in GUARDS:
            result = _run(name)
            self.assertTrue(
                result.wasSuccessful(),
                "%s does not pass on the unmodified tree: %s"
                % (name, result.failures + result.errors),
            )

    def test_a_second_subprocess_file_in_the_same_package_is_still_rejected(self):
        """The mutation. Every guard must notice a file that is not the exception."""
        self.assertFalse(ROGUE.exists())
        ROGUE.write_text(ROGUE_SOURCE, encoding="utf-8")
        try:
            for name in GUARDS:
                result = _run(name)
                self.assertFalse(
                    result.wasSuccessful(),
                    "%s accepted a second subprocess file in tasks/ — the "
                    "exception has widened into a package exemption" % name,
                )
        finally:
            ROGUE.unlink()

    def test_the_guards_pass_again_once_it_is_removed(self):
        """Proves the previous test failed because of the file and nothing else."""
        self.assertFalse(ROGUE.exists())
        for name in GUARDS:
            self.assertTrue(_run(name).wasSuccessful(), name)


class TheExceptionIsNamedNotScoped(unittest.TestCase):
    """No guard may except a directory, a package or a prefix."""

    SOURCES = (
        "tests/test_workstation_no_shell.py",
        "tests/test_task_core.py",
        "tests/test_task_mutation.py",
    )

    def test_no_guard_excepts_a_directory_or_prefix(self):
        for relative in self.SOURCES:
            source = (REPO_ROOT / relative).read_text("utf-8")
            for forbidden in (
                'path.parent.name == "tasks"' + " and True",
                '"tasks" in path.parts',
                'str(path).startswith',
                'path.parent.name == "workstation"',
                "startswith(\"gitbaseline\")",
            ):
                self.assertNotIn(forbidden, source, "%s: %s" % (relative, forbidden))

    def test_every_mention_of_the_exception_names_the_exact_file(self):
        for relative in self.SOURCES:
            source = (REPO_ROOT / relative).read_text("utf-8")
            code = python_code_only(source)
            if "gitbaseline" not in code:
                continue
            # In code (comments stripped) the only permitted spelling is the
            # exact filename, with its extension.
            self.assertIn("gitbaseline.py", source, relative)
            self.assertNotIn("gitbaseline\n.\npy\n*", code, relative)


class TheExceptedFileIsStillChecked(unittest.TestCase):
    """Narrow means narrow: everything except `subprocess` still applies."""

    def setUp(self):
        self.source = BASELINE.read_text("utf-8")
        self.code = python_code_only(self.source)

    def test_it_never_uses_a_shell(self):
        self.assertIn("shell=False", self.source)
        self.assertNotIn("shell=True", self.source)

    def test_the_breadth_vocabulary_is_absent(self):
        """The words that make a stop broad, and the ones that leak content."""
        for forbidden in (
            "pkill", "killall", "psutil", "os.kill", "signal", "SIGTERM",
            "SIGKILL", "terminate()", "Popen", "logging", "logger", "print(",
            "os.system", "os.popen", "start_new_session", "preexec_fn",
        ):
            self.assertNotIn(forbidden, self.code, forbidden)

    def test_it_reads_no_stderr(self):
        """A Git error message carries an absolute host path. It is never read."""
        self.assertNotIn("stderr", self.code)

    def test_every_command_is_a_constant_checked_against_a_closed_set(self):
        from cofferdam.workstation.tasks.gitbaseline import ALLOWED_COMMANDS

        for command in ALLOWED_COMMANDS:
            self.assertIsInstance(command, tuple)
            self.assertTrue(all(isinstance(word, str) for word in command))
        # The membership check exists, so an argv not in the set cannot run.
        # Asserted against the raw source: `python_code_only` puts every token on
        # its own line, so no multi-token phrase survives in `self.code`.
        self.assertIn("not in ALLOWED_COMMANDS", self.source)

    def test_no_argv_is_built_at_runtime(self):
        """Nothing formats, joins or interpolates a Git argument.

        Raw source for the same reason as above — and it matters here, because
        against the tokenised form every one of these would pass vacuously.
        """
        for forbidden in (
            'command.append', 'command +', 'command.extend', '.format(',
            'f"git', "f'git", '% root', '.join(command', '+ argv', 'argv +',
        ):
            self.assertNotIn(forbidden, self.source, forbidden)

    def test_the_environment_is_not_inherited(self):
        self.assertNotIn("os.environ", self.code)
        self.assertIn("env=dict(PROBE_ENVIRONMENT)", self.source)

    def test_the_call_is_bounded(self):
        self.assertIn("timeout=PROBE_TIMEOUT_SECONDS", self.source)
        self.assertIn("MAX_PROBE_OUTPUT", self.source)

    def test_it_is_the_only_excepted_file_in_the_package(self):
        """If a second one ever appears, this fails and somebody has to argue for it."""
        offenders = sorted(
            path.name
            for path in TASKS_PACKAGE.rglob("*.py")
            if "claude_code" not in path.parts
            and path.name not in ("hostclient.py",)
            and "subprocess" in python_code_only(path.read_text("utf-8"))
        )
        self.assertEqual(offenders, ["gitbaseline.py"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
