"""Behavioural tests for the CLI skeleton.

These assert the three PR1 guarantees: correct ``--version`` output, a clean
stdout/stderr split, and the exit-code convention. They call ``main`` directly
with captured streams, plus one end-to-end subprocess check of the
``python -m cofferdam`` entry point.
"""

import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

import cofferdam
from cofferdam import cli


def run(argv):
    """Invoke ``cli.main(argv)`` with captured streams.

    Returns ``(exit_code, stdout, stderr)``.
    """
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class VersionTests(unittest.TestCase):
    def test_version_prints_bare_version_to_stdout(self):
        code, out, err = run(["--version"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(out.strip(), cofferdam.__version__)
        # --version must not emit anything to stderr.
        self.assertEqual(err, "")

    def test_version_stdout_is_single_line(self):
        _, out, _ = run(["--version"])
        self.assertEqual(out.count("\n"), 1)


class HelpTests(unittest.TestCase):
    def test_no_args_shows_help_on_stdout_and_succeeds(self):
        code, out, err = run([])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("usage:", out)
        self.assertEqual(err, "")

    def test_help_flags_go_to_stdout(self):
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                code, out, err = run([flag])
                self.assertEqual(code, cli.EXIT_OK)
                self.assertIn("usage:", out)
                self.assertEqual(err, "")


class UsageErrorTests(unittest.TestCase):
    def test_unknown_command_fails_cleanly(self):
        code, out, err = run(["definitely-not-a-command"])
        self.assertEqual(code, cli.EXIT_USAGE)
        # Diagnostics on stderr only; stdout stays empty.
        self.assertEqual(out, "")
        self.assertIn("unknown command", err)

    def test_unknown_option_fails_cleanly(self):
        code, out, err = run(["--nope"])
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertEqual(out, "")
        self.assertIn("unknown option", err)


class EntryPointTests(unittest.TestCase):
    def test_python_m_cofferdam_version(self):
        result = subprocess.run(
            [sys.executable, "-m", "cofferdam", "--version"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, cli.EXIT_OK)
        self.assertEqual(result.stdout.strip(), cofferdam.__version__)
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
