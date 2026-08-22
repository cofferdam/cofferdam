"""Where the planner finds Claude, and why nobody outside the host may say.

The defect this file exists to prevent
---------------------------------------

M2M PR4 shipped the planner provider with ``DEFAULT_EXECUTABLE = "/usr/bin/claude"``
and decided availability with ``Path(...).exists()``. The official installer puts
the CLI in ``~/.local/bin``, so on the machine this was built for the planner
reported ``available: False`` on every request. ``createDevelopmentRequest``
answered ``502 upstream_unavailable`` with ``planner_unavailable``, no planner
row was written, and Opus was never reached.

Nothing about the credential isolation was wrong. The provider simply could not
find the program — and the constant that said where it was had never been true
on this host.

What is asserted here
---------------------

That the planner uses the **same** resolution policy Remote Control, the CLI
adapter and the worker already shared; that the resolved path is absolute and is
the exact string handed to the subprocess; that the sanitized environment does
not have to contain ``~/.local/bin`` for any of it to work; that nothing a
remote caller can send reaches the choice; and that a host with no CLI fails
closed before anything is invoked.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import shutil
import stat
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the extras are absent
    TestClient = None

from cofferdam.workstation.claudeauth import executable as shared
from cofferdam.workstation.planner import session as planner_session
from cofferdam.workstation.planner.errors import PlannerUnavailable
from cofferdam.workstation.planner.providers import claude_code
from cofferdam.workstation.sessions import claude as remote_control_cli
from cofferdam.workstation.tasks.adapters.claude_code import cli as adapter_cli
from cofferdam.workstation.tasks.adapters.claude_code_worker import cli as worker_cli
from cofferdam.workstation.worker import session as worker_session

from ._planner_session_doubles import (
    OPERATOR_CREDENTIAL_MARKER,
    signed_in_planner_session,
    signed_in_worker_session,
)


def make_executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    """A real file with a real execute bit. Not a mock of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class ResolverHarness(unittest.TestCase):
    """A fake host filesystem, with the search directories redirected at it."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.local_bin = self.root / "home" / "operator" / ".local" / "bin"
        self.usr_local_bin = self.root / "usr" / "local" / "bin"
        self.usr_bin = self.root / "usr" / "bin"
        for directory in (self.local_bin, self.usr_local_bin, self.usr_bin):
            directory.mkdir(parents=True)
        self.state = self.root / "state"
        self.state.mkdir()

    def redirect(self, directories=None):
        """Point the shared policy at the fake host, and empty ``PATH``."""
        original = shared.SEARCH_DIRECTORIES
        shared.SEARCH_DIRECTORIES = tuple(
            str(d) for d in (directories if directories is not None
                             else (self.local_bin, self.usr_local_bin, self.usr_bin))
        )
        self.addCleanup(setattr, shared, "SEARCH_DIRECTORIES", original)
        previous_path = os.environ.get("PATH")
        os.environ["PATH"] = str(self.root / "nothing-here")
        self.addCleanup(
            lambda: os.environ.__setitem__("PATH", previous_path)
            if previous_path is not None
            else os.environ.pop("PATH", None)
        )


# -- 1: the exact production failure, reproduced and fixed ----------------------


class TheProductionFailure(ResolverHarness):
    def test_usr_bin_absent_and_local_bin_present_resolves(self):
        """The exact shape of the production host, as a fixture.

        `/usr/bin/claude` does not exist; `~/.local/bin/claude` does. Under the
        old constant this was `available: False`; it must now resolve.
        """
        self.redirect()
        installed = make_executable(self.local_bin / "claude")
        self.assertFalse((self.usr_bin / "claude").exists())

        found = shared.find_executable()
        self.assertEqual(found, installed)

        planner = claude_code.ClaudeCodePlanner(state_dir=self.state)
        self.assertTrue(planner.available(), planner.unavailable_reason())
        self.assertIsNone(planner.unavailable_reason())

    def test_the_old_hard_coded_constant_is_gone(self):
        """Named, so it cannot come back by copy-paste."""
        self.assertFalse(hasattr(claude_code, "DEFAULT_EXECUTABLE"))
        source = Path(claude_code.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.assertNotEqual(target.id, "DEFAULT_EXECUTABLE")
        # And no literal absolute claude path is assigned anywhere in the module.
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.assertNotIn(
                    "/bin/claude", node.value,
                    f"a hard-coded CLI path survives at line {node.lineno}",
                )

    def test_no_user_specific_path_is_hard_coded_in_resolution(self):
        """No operator home in anything that decides where the CLI is.

        Scoped to the two modules this hotfix owns. The worker profile is
        excluded deliberately and by name: its `/home/worker` strings are
        *interior sandbox* permission patterns, a namespace constant rather than
        a host location, and a blanket regex would fail on them for the wrong
        reason.
        """
        import re

        for module in (shared, claude_code):
            code = ast.unparse(
                ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            )
            with self.subTest(module=module.__name__):
                self.assertIsNone(re.search(r"/home/[A-Za-z0-9._-]+", code))
                self.assertIsNone(re.search(r"/Users/[A-Za-z0-9._-]+", code))


# -- 2, 3: absolute, retained, and the exact thing executed ---------------------


class TheResolvedPathIsAbsoluteAndUsedVerbatim(ResolverHarness):
    def test_the_resolver_returns_an_absolute_path(self):
        self.redirect()
        make_executable(self.local_bin / "claude")
        self.assertTrue(shared.find_executable().is_absolute())

    def test_the_provider_stores_an_absolute_path(self):
        self.redirect()
        installed = make_executable(self.local_bin / "claude")
        planner = claude_code.ClaudeCodePlanner(state_dir=self.state)
        self.assertTrue(planner._executable.is_absolute())
        self.assertEqual(planner._executable, installed)

    def test_a_relative_override_is_refused_rather_than_made_absolute(self):
        """A cwd-relative program name is the ambiguity this removes."""
        self.assertIsNone(shared.absolute_executable(Path("claude")))
        self.assertIsNone(shared.absolute_executable(Path("./claude")))
        planner = claude_code.ClaudeCodePlanner(executable="claude")
        self.assertIsNone(planner._executable)
        self.assertFalse(planner.available())

    def test_the_symlink_is_deliberately_not_resolved(self):
        """`claude update` replaces the link target; pinning it would rot.

        The reasoning is `claude_code/cli.py`'s and predates this hotfix. It is
        asserted here because "absolute" and "canonical" are different
        requirements and only the first one is wanted.
        """
        self.redirect()
        real = make_executable(self.root / "versions" / "2.1.221" / "claude")
        link = self.local_bin / "claude"
        link.symlink_to(real)
        found = shared.find_executable()
        self.assertEqual(found, link)
        self.assertNotEqual(found, real)
        self.assertNotEqual(found, Path(os.path.realpath(link)))

    def test_the_invocation_uses_that_exact_absolute_path(self):
        self.redirect()
        installed = make_executable(self.local_bin / "claude")
        signed_in_planner_session(self.state)
        captured = {}

        def runner(argv, stdin_text, cwd, timeout, env):
            captured["argv"] = argv
            captured["env"] = env
            return json.dumps(
                {"structured_output": {
                    "schema_version": 1, "action": "STOP",
                    "summary": "no", "confidence": 1.0, "decision_basis": "no"}}
            )

        from .test_planner_contracts import a_request

        planner = claude_code.ClaudeCodePlanner(
            state_dir=self.state,
            runtime_dir=self.root / "rt",
            runner=runner,
        )
        planner.prepare_development_step(a_request())

        self.assertEqual(captured["argv"][0], str(installed))
        self.assertTrue(Path(captured["argv"][0]).is_absolute())
        # Not a bare program name that the minimal PATH would have to find.
        self.assertNotEqual(captured["argv"][0], "claude")


# -- 4: the sanitized environment stays minimal ---------------------------------


class TheSanitizedEnvironmentDoesNotDiscoverTheBinary(ResolverHarness):
    def test_the_planner_environment_path_is_still_two_system_directories(self):
        signed_in_planner_session(self.state)
        env = planner_session.environment(self.state)
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        self.assertNotIn(".local/bin", env["PATH"])

    def test_resolution_succeeds_even_though_that_PATH_could_not_find_it(self):
        """The whole point of resolving on the host side.

        The CLI lives somewhere the subprocess PATH does not name, and the run
        still works, because selection happened before the environment was
        built and the answer travels in argv.
        """
        self.redirect()
        installed = make_executable(self.local_bin / "claude")
        signed_in_planner_session(self.state)

        planner = claude_code.ClaudeCodePlanner(state_dir=self.state)
        env = planner.environment()
        self.assertTrue(planner.available())
        self.assertNotIn(str(self.local_bin), env["PATH"])
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        # And `which` under that PATH genuinely would not find it.
        self.assertIsNone(shutil.which("claude", path=env["PATH"]))
        self.assertTrue(installed.is_file())

    def test_the_environment_is_still_exactly_five_keys(self):
        """PR #84's property, unchanged by this hotfix."""
        signed_in_planner_session(self.state)
        env = planner_session.environment(self.state)
        self.assertEqual(
            sorted(env), ["CLAUDE_CONFIG_DIR", "HOME", "NO_COLOR", "PATH", "TERM"]
        )


# -- 5: fail closed --------------------------------------------------------------


class NoExecutableFailsClosed(ResolverHarness):
    def spawn_is_fatal(self):
        launched = []

        def refuse(*args, **kwargs):
            launched.append(args[0] if args else kwargs.get("args"))
            raise AssertionError("a process was launched: " + repr(launched[-1]))

        for name in ("run", "Popen", "call", "check_output"):
            original = getattr(subprocess, name)
            setattr(subprocess, name, refuse)
            self.addCleanup(setattr, subprocess, name, original)
        return launched

    def test_nothing_installed_resolves_to_nothing(self):
        self.redirect()
        self.assertIsNone(shared.find_executable())

    def test_the_provider_is_unavailable_and_says_why_without_a_path(self):
        self.redirect()
        planner = claude_code.ClaudeCodePlanner(state_dir=self.state)
        self.assertFalse(planner.available())
        reason = planner.unavailable_reason()
        self.assertEqual(reason, "no Claude Code CLI was found on this host")
        # Not the resolved path, and not the searched ones either.
        self.assertNotIn(str(self.root), reason)
        self.assertNotIn("/", reason)

    def test_preparing_a_step_refuses_before_any_process(self):
        self.redirect()
        signed_in_planner_session(self.state)
        launched = self.spawn_is_fatal()
        from .test_planner_contracts import a_request

        planner = claude_code.ClaudeCodePlanner(
            state_dir=self.state, runtime_dir=self.root / "rt"
        )
        with self.assertRaises(PlannerUnavailable):
            planner.prepare_development_step(a_request())
        self.assertEqual(launched, [])
        self.assertFalse((self.root / "rt").exists())

    def test_a_non_executable_file_is_not_the_cli(self):
        """`exists()` said yes to this. `os.access(X_OK)` does not."""
        self.redirect()
        candidate = self.local_bin / "claude"
        candidate.write_text("not executable", encoding="utf-8")
        candidate.chmod(0o644)
        self.assertIsNone(shared.find_executable())
        self.assertFalse(claude_code.ClaudeCodePlanner(state_dir=self.state).available())

    def test_a_directory_named_claude_is_not_the_cli(self):
        self.redirect()
        (self.local_bin / "claude").mkdir()
        self.assertIsNone(shared.find_executable())

    def test_a_binary_removed_after_construction_is_noticed(self):
        """Resolution is pinned; runnability is re-checked every time."""
        self.redirect()
        installed = make_executable(self.local_bin / "claude")
        planner = claude_code.ClaudeCodePlanner(state_dir=self.state)
        self.assertTrue(planner.available())
        installed.unlink()
        self.assertFalse(planner.available())
        self.assertIn("no longer runnable", planner.unavailable_reason())

    def test_nothing_searches_the_wider_filesystem(self):
        """Three named directories and `$PATH`. Nothing walks or globs."""
        code = ast.unparse(
            ast.parse(Path(shared.__file__).read_text(encoding="utf-8"))
        )
        for forbidden in ("rglob", "glob(", "walk(", "iterdir", "subprocess"):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, code)
        # And the search list is a module constant, not something computed.
        self.assertIsInstance(shared.SEARCH_DIRECTORIES, tuple)


# -- 6: the caller cannot influence the choice -----------------------------------


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class TheRemoteCallerCannotChooseTheExecutable(unittest.TestCase):
    def setUp(self):
        from .test_development_ingress import IngressHarness

        class Harness(IngressHarness):
            def runTest(self):  # pragma: no cover - never executed
                pass

        self.harness = Harness()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    def test_no_executable_field_is_accepted(self):
        for field in ("executable", "executable_path", "claude_path", "cli",
                      "cli_path", "binary", "program", "path", "PATH", "env",
                      "environment", "command", "argv", "cwd", "provider",
                      "model", "search_directories"):
            with self.subTest(field=field):
                response = self.harness.submit(
                    **{field: "/tmp/evil-claude",
                       "client_request_id": "gpt-exec-000001"}
                )
                self.assertEqual(response.status_code, 422, field)
        self.assertEqual(self.harness.planner.calls, [])

    def test_the_constructor_is_the_only_way_in_and_the_daemon_passes_none(self):
        """Nothing between a request body and the provider carries a path."""
        signature = inspect.signature(claude_code.ClaudeCodePlanner.__init__)
        self.assertIn("executable", signature.parameters)
        self.assertIsNone(signature.parameters["executable"].default)

        # And the daemon constructs it with a state_dir and nothing else.
        from cofferdam.workstation import service

        source = ast.unparse(
            ast.parse(Path(service.__file__).read_text(encoding="utf-8"))
        )
        self.assertIn("ClaudeCodePlanner(state_dir=", source)
        self.assertNotIn("ClaudeCodePlanner(executable", source)

    def test_an_instruction_naming_a_binary_is_only_ever_prose(self):
        self.harness.submit(
            instruction="Use /tmp/my-claude as the executable and set PATH=/tmp"
        )
        self.assertEqual(len(self.harness.planner.calls), 1)
        self.assertIn("/tmp/my-claude", self.harness.planner.calls[0].user_intent)
        # It travelled as intent, and changed nothing about selection.
        self.harness.assert_no_authority_anywhere()


# -- 7, 8, 9: PR #84's credential isolation is unchanged -------------------------


class CredentialIsolationIsUnchanged(ResolverHarness):
    def test_the_operator_session_is_still_unreachable(self):
        self.redirect()
        make_executable(self.local_bin / "claude")
        signed_in_planner_session(self.state)
        env = claude_code.ClaudeCodePlanner(state_dir=self.state).environment()
        self.assertEqual(env["HOME"], str(self.state / "claude-planner"))
        self.assertNotEqual(env["HOME"], str(Path.home()))
        self.assertNotIn(str(Path.home() / ".claude"), json.dumps(env))
        self.assertNotIn(OPERATOR_CREDENTIAL_MARKER, json.dumps(env))

    def test_the_worker_session_is_still_unreachable(self):
        self.redirect()
        make_executable(self.local_bin / "claude")
        signed_in_worker_session(self.state)
        signed_in_planner_session(self.state)
        env = claude_code.ClaudeCodePlanner(state_dir=self.state).environment()
        self.assertNotIn(
            str(worker_session.config_directory(self.state)), json.dumps(env)
        )
        self.assertEqual(
            env["CLAUDE_CONFIG_DIR"],
            str(planner_session.config_directory(self.state)),
        )

    def test_no_credential_variable_is_inherited(self):
        self.redirect()
        make_executable(self.local_bin / "claude")
        signed_in_planner_session(self.state)
        for name in shared_credential_names():
            os.environ[name] = "SHOULD-NOT-TRAVEL"
            self.addCleanup(os.environ.pop, name, None)
        env = claude_code.ClaudeCodePlanner(state_dir=self.state).environment()
        self.assertNotIn("SHOULD-NOT-TRAVEL", json.dumps(env))

    def test_a_resolvable_cli_does_not_make_an_unsigned_session_usable(self):
        """Requirement 9: finding the binary is not finding a credential."""
        self.redirect()
        make_executable(self.local_bin / "claude")
        planner = claude_code.ClaudeCodePlanner(state_dir=self.state)
        self.assertTrue(planner.available())
        with self.assertRaises(planner_session.PlannerSessionUnavailable):
            planner.require_session()

    def test_availability_and_session_remain_two_answers(self):
        self.redirect()
        planner_no_cli = claude_code.ClaudeCodePlanner(state_dir=self.state)
        signed_in_planner_session(self.state)
        self.assertFalse(planner_no_cli.available())
        self.assertEqual(
            planner_no_cli.session_status().status,
            planner_session.STATUS_CLI_MISSING,
        )

        make_executable(self.local_bin / "claude")
        planner_with_cli = claude_code.ClaudeCodePlanner(state_dir=self.state)
        self.assertTrue(planner_with_cli.available())
        self.assertEqual(
            planner_with_cli.session_status().status, planner_session.STATUS_READY
        )


def shared_credential_names():
    from cofferdam.workstation.claudeauth.session import (
        CREDENTIAL_ENVIRONMENT_NAMES,
    )

    return [n for n in CREDENTIAL_ENVIRONMENT_NAMES if n != "CLAUDE_CONFIG_DIR"]


# -- 10: PR #84's authority invariants -------------------------------------------


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class TheAuthorityInvariantsStillHold(unittest.TestCase):
    def setUp(self):
        from .test_development_ingress import IngressHarness

        class Harness(IngressHarness):
            def runTest(self):  # pragma: no cover
                pass

        self.harness = Harness()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    def test_a_successful_plan_still_approves_dispatches_and_runs_nothing(self):
        response = self.harness.submit()
        self.assertEqual(response.status_code, 201, response.text)
        self.harness.assert_no_authority_anywhere()
        tasks = self.harness.client.get(
            "/api/tasks", headers=self.harness.bridge_auth
        ).json()
        self.assertEqual(tasks["tasks"], [])
        self.assertFalse((self.harness.roots["alpha"] / ".git").exists())
        body = response.json()
        self.assertFalse(body["authority"]["approved"])
        self.assertFalse(body["authority"]["dispatched"])
        self.assertFalse(body["authority"]["executed"])

    def test_no_check_runner_or_git_process_is_launched(self):
        launched = []

        def refuse(*args, **kwargs):
            launched.append(args[0] if args else kwargs.get("args"))
            raise AssertionError("a process was launched: " + repr(launched[-1]))

        for name in ("run", "Popen", "call", "check_output"):
            original = getattr(subprocess, name)
            setattr(subprocess, name, refuse)
            self.addCleanup(setattr, subprocess, name, original)

        self.assertEqual(self.harness.submit().status_code, 201)
        self.assertEqual(launched, [])
        self.harness.assert_no_authority_anywhere()


# -- 11: the three existing consumers do not regress ------------------------------


class ExistingResolutionDoesNotRegress(ResolverHarness):
    CONSUMERS = (
        ("remote control", remote_control_cli),
        ("cli task adapter", adapter_cli),
        ("development worker", worker_cli),
    )

    def test_every_consumer_keeps_its_public_names(self):
        for label, module in self.CONSUMERS:
            with self.subTest(consumer=label):
                self.assertEqual(module.EXECUTABLE_NAME, "claude")
                self.assertEqual(
                    tuple(module.SEARCH_DIRECTORIES),
                    ("~/.local/bin", "/usr/local/bin", "/usr/bin"),
                )
                self.assertTrue(callable(module.find_executable))
                self.assertIn("find_executable", module.__all__)
                self.assertIn("EXECUTABLE_NAME", module.__all__)

    def test_every_consumer_resolves_identically_to_the_shared_policy(self):
        self.redirect()
        installed = make_executable(self.local_bin / "claude")
        answers = {label: module.find_executable() for label, module in self.CONSUMERS}
        answers["shared"] = shared.find_executable()
        answers["planner"] = claude_code.ClaudeCodePlanner(
            state_dir=self.state
        )._executable
        for label, answer in answers.items():
            with self.subTest(consumer=label):
                self.assertEqual(answer, installed)

    def test_every_consumer_fails_closed_identically(self):
        self.redirect()
        for label, module in self.CONSUMERS:
            with self.subTest(consumer=label):
                self.assertIsNone(module.find_executable())
        self.assertIsNone(shared.find_executable())

    def test_there_is_exactly_one_implementation_of_the_search(self):
        """Four consumers, one loop. Asserted from the syntax trees."""
        implementations = 0
        for module in (shared, remote_control_cli, adapter_cli, worker_cli,
                       claude_code):
            code = ast.unparse(
                ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            )
            if "shutil.which(EXECUTABLE_NAME)" in code:
                implementations += 1
        self.assertEqual(
            implementations, 1,
            "the search loop exists in more than one module again",
        )

    def test_the_worker_adapter_availability_still_works(self):
        self.redirect()
        make_executable(self.local_bin / "claude")
        self.assertIsNotNone(worker_cli.find_executable())
        # `resolve_cli_directory` still resolves the symlink, which is its own
        # deliberate difference — it becomes a bind-mount source.
        real = make_executable(self.root / "versions" / "x" / "claude")
        link = self.usr_local_bin / "claude"
        link.symlink_to(real)
        self.assertEqual(worker_cli.resolve_cli_directory(link), real)


# -- 12: an installed package resolves the same way -------------------------------


class TheInstalledPackageResolvesIdentically(unittest.TestCase):
    def test_the_shared_module_is_declared_in_the_package_list(self):
        """`claudeauth` ships, so `executable.py` ships with it."""
        import tomllib

        root = Path(__file__).resolve().parents[1]
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        packages = data["tool"]["setuptools"]["packages"]
        self.assertIn("cofferdam.workstation.claudeauth", packages)

    def test_resolution_reads_no_source_tree_relative_path(self):
        """It must not depend on `__file__`, a repo root or a cwd.

        A resolver that walked from its own location would work in a checkout
        and fail from site-packages — the exact class of bug requirement 12
        asks about, and the reason this is asserted structurally.
        """
        code = ast.unparse(
            ast.parse(Path(shared.__file__).read_text(encoding="utf-8"))
        )
        for forbidden in ("__file__", "parents[", "os.getcwd", "Path.cwd", "sys.prefix"):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, code)

    def test_resolution_from_a_foreign_working_directory_is_unchanged(self):
        """Run the resolver from `/` and require the same answer."""
        here = os.getcwd()
        expected = shared.find_executable()
        try:
            os.chdir("/")
            self.assertEqual(shared.find_executable(), expected)
        finally:
            os.chdir(here)

    def test_a_subprocess_with_no_inherited_environment_resolves_the_same(self):
        """The strongest available check short of installing a wheel.

        A fresh interpreter, `env -i`-style: no PATH, no HOME from this process.
        If resolution depended on ambient state this would differ.
        """
        expected = shared.find_executable()
        code = (
            "from cofferdam.workstation.claudeauth.executable import find_executable;"
            "print(find_executable())"
        )
        # `PYTHONPATH` rather than the cwd, which is the point: an installed
        # package is reached through a `sys.path` entry, never through the
        # working directory, and this proves resolution does not depend on
        # being run from a checkout.
        root = str(Path(shared.__file__).resolve().parents[3])
        completed = subprocess.run(
            [os.sys.executable, "-c", code],
            capture_output=True, text=True, timeout=60,
            env={
                "HOME": os.path.expanduser("~"),
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": root,
            },
            cwd="/",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), str(expected))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
