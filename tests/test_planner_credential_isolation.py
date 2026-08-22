"""The planner has its own Claude session, and cannot reach anybody else's.

The defect this file exists to prevent
---------------------------------------

Until M2M PR4 the planner provider called ``subprocess.run`` with **no ``env``
argument**. The CLI therefore inherited the daemon's environment — ``HOME``
included — and authenticated as *the operator*. While the only caller was a test
somebody ran by hand that was merely untidy. With a remote Custom GPT able to
trigger the invocation it became an authority hole: a request arriving over the
network would spend a human's personal subscription session, rotate their token,
and leave nothing saying it had.

Three credentials, and the planner may reach exactly one
---------------------------------------------------------

```
~/.claude                        the operator's own session          FORBIDDEN
<state>/claude-worker/config     the contained worker's session      FORBIDDEN
<state>/claude-planner/config    the planner's own session           the only one
```

The tests below assert that as a *negative* wherever they can: the operator and
worker credentials are made real and valid on disk, and the planner is required
to fail anyway. A test that only checked "the planner's directory is used" would
still pass if a fallback existed.

Why the environment is the boundary rather than a check
--------------------------------------------------------

There is no code anywhere that says "do not read the operator's credential".
There is a function that builds the whole environment from constants and this
namespace's paths, so ``HOME`` cannot name the operator's home and
``CLAUDE_CONFIG_DIR`` cannot name anybody else's config root. A ``~/.claude``
lookup has nowhere to resolve to — absent, not blocked, which is the same
argument the rest of this milestone makes about fields that do not exist.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the extras are absent
    TestClient = None

from cofferdam.workstation.claudeauth import session as claude_session
from cofferdam.workstation.planner import session as planner_session
from cofferdam.workstation.planner.errors import (
    PlannerAuthRequired,
    PlannerSessionExpired,
    PlannerInvocationFailed,
)
from cofferdam.workstation.planner.providers import claude_code
from cofferdam.workstation.worker import session as worker_session

from ._planner_session_doubles import (
    OPERATOR_CREDENTIAL_MARKER,
    PLANNER_CREDENTIAL_MARKER,
    WORKER_CREDENTIAL_MARKER,
    never_logged_in_planner_session,
    operator_home,
    signed_in_planner_session,
    signed_in_worker_session,
    unsafe_planner_session,
)


class SessionHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name) / "state"
        self.state.mkdir(parents=True)


# -- 1, 2: the two credentials the planner must never reach ---------------------


class TheOperatorSessionIsUnreachable(SessionHarness):
    def test_a_valid_operator_credential_does_not_make_the_planner_usable(self):
        """The strongest form: their credential is real, and it changes nothing."""
        home = operator_home(Path(self._tmp.name) / "operator-home")
        self.assertTrue((home / ".claude" / ".credentials.json").is_file())

        planner = claude_code.ClaudeCodePlanner(
            executable="/usr/bin/env", state_dir=self.state
        )
        with self.assertRaises(planner_session.PlannerSessionUnavailable) as caught:
            planner.require_session()
        self.assertEqual(caught.exception.status, planner_session.STATUS_UNPREPARED)

    def test_home_in_the_built_environment_is_never_the_operators(self):
        signed_in_planner_session(self.state)
        built = planner_session.environment(self.state)
        self.assertEqual(built["HOME"], str(self.state / "claude-planner"))
        self.assertNotEqual(built["HOME"], str(Path.home()))
        self.assertNotEqual(built["HOME"], os.environ.get("HOME"))
        # And ~/.claude under that HOME is not the operator's directory.
        self.assertNotEqual(
            Path(built["HOME"]) / ".claude", Path.home() / ".claude"
        )

    def test_the_built_environment_carries_no_inherited_variable(self):
        """Selection, not filtering. Anything not named is simply not there."""
        signed_in_planner_session(self.state)
        built = planner_session.environment(self.state)
        self.assertEqual(
            sorted(built),
            ["CLAUDE_CONFIG_DIR", "HOME", "NO_COLOR", "PATH", "TERM"],
        )

    def test_no_api_key_can_reach_the_subprocess(self):
        """Even with every credential variable set in this very process."""
        signed_in_planner_session(self.state)
        for name in claude_session.CREDENTIAL_ENVIRONMENT_NAMES:
            os.environ[name] = "SHOULD-NEVER-TRAVEL-" + name
            self.addCleanup(os.environ.pop, name, None)

        built = planner_session.environment(self.state)
        for name in claude_session.CREDENTIAL_ENVIRONMENT_NAMES:
            if name == "CLAUDE_CONFIG_DIR":
                # Present, and pointed at the planner's own root rather than the
                # inherited value.
                self.assertEqual(
                    built[name], str(planner_session.config_directory(self.state))
                )
                continue
            with self.subTest(variable=name):
                self.assertNotIn(name, built)
        self.assertNotIn("SHOULD-NEVER-TRAVEL", json.dumps(built))

    def test_extra_values_cannot_reintroduce_a_credential(self):
        """The escape hatch is not one. Named keys always win."""
        signed_in_planner_session(self.state)
        built = claude_session.environment(
            self.state,
            planner_session.NAMESPACE,
            extra={
                "ANTHROPIC_API_KEY": "sneaked-in",
                "CLAUDE_CONFIG_DIR": "/somebody/elses/config",
                "HOME": str(Path.home()),
                "LANG": "en_GB.UTF-8",
            },
        )
        self.assertNotIn("ANTHROPIC_API_KEY", built)
        self.assertEqual(
            built["CLAUDE_CONFIG_DIR"],
            str(planner_session.config_directory(self.state)),
        )
        self.assertEqual(built["HOME"], str(self.state / "claude-planner"))
        # A genuinely harmless value does survive, which is what it is for.
        self.assertEqual(built["LANG"], "en_GB.UTF-8")

    def test_nothing_in_the_session_modules_reads_the_operator_home(self):
        """Asserted from the syntax tree, not from a docstring."""
        for module in (claude_session, planner_session, worker_session):
            source = Path(module.__file__).read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                # Drop docstrings: this file's own prose names ~/.claude to say
                # it is never read, and prose must not fail a structural check.
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                    if (
                        node.body
                        and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)
                    ):
                        node.body = node.body[1:]
            code = ast.unparse(tree)
            with self.subTest(module=module.__name__):
                self.assertNotIn("Path.home()", code)
                self.assertNotIn("expanduser", code)
                self.assertNotIn("os.environ", code)


class TheWorkerSessionIsUnreachable(SessionHarness):
    def test_a_valid_worker_credential_does_not_make_the_planner_usable(self):
        signed_in_worker_session(self.state)
        self.assertTrue(worker_session.credential_path(self.state).is_file())

        found = planner_session.status(self.state)
        self.assertEqual(found.status, planner_session.STATUS_UNPREPARED)
        self.assertFalse(found.usable)

    def test_the_two_config_roots_are_different_directories(self):
        self.assertNotEqual(
            planner_session.config_directory(self.state),
            worker_session.config_directory(self.state),
        )
        self.assertNotIn(
            worker_session.SESSION_DIRNAME,
            str(planner_session.config_directory(self.state)),
        )

    def test_the_planner_environment_never_names_the_worker_root(self):
        signed_in_planner_session(self.state)
        signed_in_worker_session(self.state)
        built = json.dumps(planner_session.environment(self.state))
        self.assertNotIn(str(worker_session.config_directory(self.state)), built)
        self.assertNotIn(worker_session.SESSION_DIRNAME, built)

    def test_signing_the_planner_in_leaves_the_worker_credential_alone(self):
        signed_in_worker_session(self.state)
        before = worker_session.credential_path(self.state).read_text()
        signed_in_planner_session(self.state)
        self.assertEqual(
            worker_session.credential_path(self.state).read_text(), before
        )
        self.assertEqual(before, WORKER_CREDENTIAL_MARKER)

    def test_preparing_the_planner_copies_nothing_from_anywhere(self):
        """`prepare` creates an *empty* root. Not a migration, not an import."""
        signed_in_worker_session(self.state)
        operator_home(Path(self._tmp.name) / "operator-home")
        planner_session.prepare(self.state)
        self.assertFalse(planner_session.credential_path(self.state).exists())
        contents = list(planner_session.config_directory(self.state).iterdir())
        self.assertEqual(contents, [])

    def test_the_two_sessions_hold_different_locks(self):
        """A long worker run and a planning turn never wait on each other."""
        self.assertNotEqual(
            planner_session.lock_path(self.state),
            worker_session.lock_path(self.state),
        )

    def test_the_two_failures_are_different_types(self):
        """Catching the worker's must not swallow the planner's."""
        self.assertFalse(
            issubclass(
                planner_session.PlannerSessionUnavailable,
                worker_session.WorkerSessionUnavailable,
            )
        )
        self.assertFalse(
            issubclass(
                worker_session.WorkerSessionUnavailable,
                planner_session.PlannerSessionUnavailable,
            )
        )


# -- 3, 4: fail closed, and say which way ---------------------------------------


class MissingAuthFailsBeforeTheProvider(SessionHarness):
    def spawn_is_fatal(self):
        """Make any process launch a test failure for the rest of this test."""
        launched = []

        def refuse(*args, **kwargs):
            launched.append(args[0] if args else kwargs.get("args"))
            raise AssertionError("a process was launched: " + repr(launched[-1]))

        for name in ("run", "Popen", "call", "check_output"):
            original = getattr(subprocess, name)
            setattr(subprocess, name, refuse)
            self.addCleanup(setattr, subprocess, name, original)
        return launched

    def test_an_unprepared_session_refuses_without_spawning(self):
        launched = self.spawn_is_fatal()
        planner = claude_code.ClaudeCodePlanner(
            executable="/usr/bin/env", state_dir=self.state
        )
        with self.assertRaises(planner_session.PlannerSessionUnavailable) as caught:
            planner.require_session()
        self.assertEqual(caught.exception.status, planner_session.STATUS_UNPREPARED)
        self.assertEqual(launched, [])

    def test_a_never_logged_in_session_refuses_without_spawning(self):
        never_logged_in_planner_session(self.state)
        launched = self.spawn_is_fatal()
        planner = claude_code.ClaudeCodePlanner(
            executable="/usr/bin/env", state_dir=self.state
        )
        with self.assertRaises(planner_session.PlannerSessionUnavailable) as caught:
            planner.require_session()
        self.assertEqual(
            caught.exception.status, planner_session.STATUS_LOGIN_REQUIRED
        )
        self.assertEqual(launched, [])

    def test_an_unsafe_credential_is_refused_rather_than_used(self):
        unsafe_planner_session(self.state)
        planner = claude_code.ClaudeCodePlanner(
            executable="/usr/bin/env", state_dir=self.state
        )
        with self.assertRaises(planner_session.PlannerSessionUnavailable) as caught:
            planner.require_session()
        self.assertEqual(
            caught.exception.status, planner_session.STATUS_PERMISSIONS_UNSAFE
        )

    def test_a_provider_with_no_state_directory_refuses(self):
        """`None` means "no session", never "use whatever is around"."""
        planner = claude_code.ClaudeCodePlanner(executable="/usr/bin/env")
        with self.assertRaises(planner_session.PlannerSessionUnavailable):
            planner.require_session()

    def test_the_gate_runs_before_the_runtime_directory_is_even_prepared(self):
        """Ordering, asserted rather than assumed."""
        runtime = Path(self._tmp.name) / "planner-runtime"
        planner = claude_code.ClaudeCodePlanner(
            executable="/usr/bin/env", state_dir=self.state, runtime_dir=runtime,
            runner=lambda *a, **k: self.fail("the runner was reached"),
        )
        from .test_planner_contracts import a_request

        with self.assertRaises(planner_session.PlannerSessionUnavailable):
            planner.prepare_development_step(a_request())
        self.assertFalse(runtime.exists(), "the runtime directory was prepared")

    def test_a_missing_cli_is_distinct_from_a_missing_session(self):
        signed_in_planner_session(self.state)
        planner = claude_code.ClaudeCodePlanner(
            executable=str(self.state / "no-such-binary"), state_dir=self.state
        )
        with self.assertRaises(planner_session.PlannerSessionUnavailable) as caught:
            planner.require_session()
        self.assertEqual(caught.exception.status, planner_session.STATUS_CLI_MISSING)
        # And with the CLI present, the same session is fine.
        ok = claude_code.ClaudeCodePlanner(
            executable="/usr/bin/env", state_dir=self.state
        )
        self.assertEqual(
            ok.require_session(), planner_session.config_directory(self.state)
        )


class ExpiryIsItsOwnCondition(SessionHarness):
    """An expired session is not a code failure and not a project failure."""

    def planner_with_output(self, stderr: str, code: int = 1):
        signed_in_planner_session(self.state)
        planner = claude_code.ClaudeCodePlanner(
            executable="/usr/bin/env", state_dir=self.state
        )

        class Completed:
            returncode = code
            stdout = ""

        Completed.stderr = stderr
        return planner, Completed

    def test_the_cli_expiry_wording_becomes_a_session_refusal(self):
        planner, completed = self.planner_with_output(
            "Failed to authenticate: OAuth session expired and could not be refreshed"
        )
        with unittest.mock.patch("subprocess.run", return_value=completed):
            with self.assertRaises(planner_session.PlannerSessionUnavailable) as caught:
                planner._run_subprocess(
                    ["/usr/bin/env"], "", self.state, 10, {"PATH": "/usr/bin:/bin"}
                )
        self.assertEqual(
            caught.exception.status, planner_session.STATUS_SESSION_EXPIRED
        )

    def test_the_cli_login_wording_becomes_a_session_refusal(self):
        planner, completed = self.planner_with_output(
            "Not logged in · Please run /login"
        )
        with unittest.mock.patch("subprocess.run", return_value=completed):
            with self.assertRaises(planner_session.PlannerSessionUnavailable) as caught:
                planner._run_subprocess(
                    ["/usr/bin/env"], "", self.state, 10, {"PATH": "/usr/bin:/bin"}
                )
        self.assertEqual(
            caught.exception.status, planner_session.STATUS_LOGIN_REQUIRED
        )

    def test_an_ordinary_failure_is_not_relabelled_as_an_auth_problem(self):
        """Mislabelling in this direction sends somebody to a login screen."""
        planner, completed = self.planner_with_output("TypeError: bad schema")
        with unittest.mock.patch("subprocess.run", return_value=completed):
            with self.assertRaises(PlannerInvocationFailed):
                planner._run_subprocess(
                    ["/usr/bin/env"], "", self.state, 10, {"PATH": "/usr/bin:/bin"}
                )

    def test_the_refusal_carries_no_provider_stderr(self):
        """The CLI names the config root it was pointed at. That is a host path."""
        planner, completed = self.planner_with_output(
            "OAuth session expired. Config: " + str(self.state / "claude-planner")
        )
        with unittest.mock.patch("subprocess.run", return_value=completed):
            with self.assertRaises(planner_session.PlannerSessionUnavailable) as caught:
                planner._run_subprocess(
                    ["/usr/bin/env"], "", self.state, 10, {"PATH": "/usr/bin:/bin"}
                )
        rendered = str(caught.exception) + str(caught.exception.detail)
        self.assertNotIn(str(self.state), rendered)


# -- 5: a valid session is explicitly handed to the subprocess ------------------


class TheSessionIsPassedExplicitly(SessionHarness):
    def test_the_runner_receives_the_built_environment(self):
        signed_in_planner_session(self.state)
        captured = {}

        def runner(argv, stdin_text, cwd, timeout, env):
            captured["env"] = env
            return json.dumps(
                {
                    "structured_output": {
                        "schema_version": 1,
                        "action": "STOP",
                        "summary": "no",
                        "confidence": 1.0,
                        "decision_basis": "no",
                    }
                }
            )

        from .test_planner_contracts import a_request

        planner = claude_code.ClaudeCodePlanner(
            executable="/usr/bin/env",
            state_dir=self.state,
            runtime_dir=Path(self._tmp.name) / "rt",
            runner=runner,
        )
        planner.prepare_development_step(a_request())

        self.assertEqual(
            captured["env"]["CLAUDE_CONFIG_DIR"],
            str(planner_session.config_directory(self.state)),
        )
        self.assertEqual(
            captured["env"]["HOME"], str(self.state / "claude-planner")
        )
        self.assertNotIn("ANTHROPIC_API_KEY", captured["env"])

    def test_the_real_subprocess_call_passes_env_and_not_os_environ(self):
        """From the source that runs, so a future edit cannot quietly inherit.

        Comments and docstrings are stripped first. The first version of this
        scanned raw text and failed on the module's own comment, which says the
        environment is never ``os.environ`` — the same trap
        ``test_the_login_tool_never_handles_a_credential`` had to fix.
        """
        import textwrap

        # Round-tripped through the syntax tree, which drops comments and keeps
        # the code readable. The first version scanned raw text and failed on
        # this method's own comment, which says the environment is never
        # `os.environ` — the trap
        # `test_the_login_tool_never_handles_a_credential` documents.
        source = ast.unparse(
            ast.parse(
                textwrap.dedent(
                    inspect.getsource(claude_code.ClaudeCodePlanner._run_subprocess)
                )
            )
        )
        self.assertIn("env=env", source)
        self.assertNotIn("os.environ", source)
        self.assertIn("shell=False", source)

    def test_the_environment_reaches_a_real_process(self):
        """End to end against a real ``/usr/bin/env``, reading its output.

        The strongest available check that this is not merely constructed
        correctly: the variables are read back out of a process that actually
        ran, so an argument dropped between here and ``subprocess.run`` shows up.
        """
        signed_in_planner_session(self.state)
        planner = claude_code.ClaudeCodePlanner(
            executable="/usr/bin/env", state_dir=self.state
        )
        try:
            printed = planner._run_subprocess(
                ["/usr/bin/env"], "", self.state, 30, planner.environment()
            )
        except FileNotFoundError:  # pragma: no cover - no /usr/bin/env
            self.skipTest("/usr/bin/env is not available")

        seen = dict(
            line.split("=", 1) for line in printed.splitlines() if "=" in line
        )
        self.assertEqual(
            seen.get("CLAUDE_CONFIG_DIR"),
            str(planner_session.config_directory(self.state)),
        )
        self.assertEqual(seen.get("HOME"), str(self.state / "claude-planner"))
        self.assertNotIn("ANTHROPIC_API_KEY", seen)
        self.assertNotIn(OPERATOR_CREDENTIAL_MARKER, printed)


# -- 6: the session survives the service being rebuilt ---------------------------


class TheSessionIsPersistent(SessionHarness):
    def test_a_credential_written_once_is_found_by_a_new_provider(self):
        signed_in_planner_session(self.state)
        for _ in range(3):
            planner = claude_code.ClaudeCodePlanner(
                executable="/usr/bin/env", state_dir=self.state
            )
            self.assertEqual(
                planner.require_session(),
                planner_session.config_directory(self.state),
            )

    def test_the_directory_is_not_recreated_or_cleared_on_prepare(self):
        signed_in_planner_session(self.state)
        credential = planner_session.credential_path(self.state)
        before = credential.read_text()
        for _ in range(3):
            planner_session.prepare(self.state)
        self.assertEqual(credential.read_text(), before)
        self.assertEqual(before, PLANNER_CREDENTIAL_MARKER)

    def test_permissions_stay_restrictive_across_prepares(self):
        signed_in_planner_session(self.state)
        for _ in range(3):
            planner_session.prepare(self.state)
        mode = planner_session.config_directory(self.state).stat().st_mode & 0o777
        self.assertEqual(mode, 0o700)
        root_mode = planner_session.session_root(self.state).stat().st_mode & 0o777
        self.assertEqual(root_mode, 0o700)
        safe, detail = planner_session.permissions_safe(self.state)
        self.assertTrue(safe, detail)

    def test_a_fresh_directory_is_created_restricted(self):
        planner_session.prepare(self.state)
        mode = planner_session.config_directory(self.state).stat().st_mode & 0o777
        self.assertEqual(mode & 0o077, 0, f"mode {mode:o} is not owner-only")


# -- 7, 8: nothing leaks -----------------------------------------------------------


class TheStatusSurfaceLeaksNothing(SessionHarness):
    def test_describe_carries_no_path_and_no_credential(self):
        signed_in_planner_session(self.state)
        payload = json.dumps(planner_session.describe(self.state))
        self.assertNotIn(str(self.state), payload)
        self.assertNotIn(PLANNER_CREDENTIAL_MARKER, payload)
        self.assertNotIn(".credentials.json", payload)

    def test_describe_reports_the_status_and_not_the_token(self):
        signed_in_planner_session(self.state)
        payload = planner_session.describe(self.state)
        self.assertEqual(payload["status"], planner_session.STATUS_READY)
        self.assertTrue(payload["credential_present"])
        for forbidden in ("token", "credential_path", "config", "home"):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, payload)

    def test_the_provider_describe_names_no_directory(self):
        signed_in_planner_session(self.state)
        planner = claude_code.ClaudeCodePlanner(
            executable="/usr/bin/env", state_dir=self.state
        )
        payload = json.dumps(planner.describe())
        self.assertNotIn(str(self.state), payload)
        self.assertNotIn("claude-planner", payload)
        self.assertIn("session", json.loads(payload))

    def test_nothing_in_the_session_module_reads_the_credential(self):
        """The reason `describe` cannot leak a token: it never reads one.

        ``os.open`` is allowed and is the one exception, because the lock file is
        opened by descriptor and never read — so the check is on the *reading*
        calls, and on the lock being the only thing ``os.open`` is given.
        """
        tree = ast.parse(Path(claude_session.__file__).read_text(encoding="utf-8"))
        opens = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            attribute = getattr(target, "attr", None)
            name = attribute or getattr(target, "id", None)
            if name in ("read_text", "read_bytes", "readlines", "read"):
                self.fail(f"the session module reads a file at line {node.lineno}")
            if name == "open":
                opens.append(ast.unparse(node))

        # Exactly one, and it is the lock.
        self.assertEqual(len(opens), 1, opens)
        self.assertIn("os.open(path", opens[0])
        # 0o600, as `ast.unparse` renders it.
        self.assertIn("384", opens[0])

    def test_the_probe_keeps_only_three_named_fields(self):
        """A future CLI that added a token to this output would not leak it."""
        self.assertEqual(
            claude_session.PROBE_FIELDS, ("loggedIn", "authMethod", "apiProvider")
        )
        source = inspect.getsource(claude_session.probe)
        self.assertIn("for name in PROBE_FIELDS if name in payload", source)


# -- the remote surface: 7, 8, 9, 10 ----------------------------------------------


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class TheRemoteRouteFailsClosed(unittest.TestCase):
    """The whole point, through the real HTTP surface.

    Reuses the ingress harness so every other invariant of PR #84 is still in
    force while the session is broken — in particular that no approval, dispatch,
    task, commit or publication appears on any of these paths.
    """

    def setUp(self):
        from .test_development_ingress import IngressHarness

        class Harness(IngressHarness):
            def runTest(self):  # pragma: no cover - never executed
                pass

        self.harness = Harness()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    def break_session(self, status_name):
        self.harness.planner.session = status_name

    def submit(self, **kwargs):
        return self.harness.submit(**kwargs)

    def test_a_never_logged_in_planner_refuses_with_its_own_code(self):
        self.break_session(planner_session.STATUS_LOGIN_REQUIRED)
        response = self.submit()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"]["code"], "planner_auth_required"
        )

    def test_an_expired_planner_is_a_different_code(self):
        self.break_session(planner_session.STATUS_SESSION_EXPIRED)
        response = self.submit()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"]["code"], "planner_session_expired"
        )

    def test_the_two_auth_codes_differ_from_project_and_planner_failure(self):
        """Four conditions, four codes. None collapses into another."""
        seen = {}
        self.break_session(planner_session.STATUS_LOGIN_REQUIRED)
        seen["auth"] = self.submit().json()["error"]["code"]
        self.break_session(planner_session.STATUS_SESSION_EXPIRED)
        seen["expired"] = self.submit().json()["error"]["code"]

        self.harness.planner.session = planner_session.STATUS_READY
        seen["project"] = self.submit(project_id="no-such-project").json()["error"][
            "code"
        ]
        self.harness.planner.raises = PlannerInvocationFailed("provider exited 1")
        body = self.submit().json()
        seen["planner_failure"] = body["planner_failure_code"]

        self.assertEqual(len(set(seen.values())), 4, seen)
        self.assertEqual(seen["auth"], "planner_auth_required")
        self.assertEqual(seen["expired"], "planner_session_expired")
        self.assertEqual(seen["project"], "project_not_found")
        self.assertEqual(seen["planner_failure"], "planner_invocation_failed")

    def test_the_provider_is_never_invoked_when_auth_fails(self):
        self.break_session(planner_session.STATUS_LOGIN_REQUIRED)
        self.submit()
        self.assertEqual(self.harness.planner.calls, [])
        self.assertGreaterEqual(self.harness.planner.session_checks, 1)

    def test_nothing_durable_is_created_when_auth_fails(self):
        """Not even a receipt. The key stays usable once somebody logs in."""
        self.break_session(planner_session.STATUS_LOGIN_REQUIRED)
        self.submit(client_request_id="gpt-auth-000001")
        self.assertEqual(self.harness.rows("planner_requests"), [])
        self.assertEqual(self.harness.rows("planner_ingress_receipts"), [])

    def test_an_auth_failed_request_can_be_retried_after_a_login(self):
        """Requirement 9: the retry plans once, not twice, and not zero times."""
        self.break_session(planner_session.STATUS_LOGIN_REQUIRED)
        first = self.submit(client_request_id="gpt-auth-000001")
        self.assertEqual(first.status_code, 503)

        # A person logs the planner in.
        self.harness.planner.session = planner_session.STATUS_READY
        second = self.submit(client_request_id="gpt-auth-000001")
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(len(self.harness.planner.calls), 1)

        # And the *same* key still replays rather than planning again.
        third = self.submit(client_request_id="gpt-auth-000001")
        self.assertEqual(third.status_code, 200)
        self.assertTrue(third.json()["replayed"])
        self.assertEqual(len(self.harness.planner.calls), 1)

    def test_repeated_auth_failures_never_invoke_the_provider(self):
        self.break_session(planner_session.STATUS_SESSION_EXPIRED)
        for index in range(4):
            response = self.submit(client_request_id="gpt-auth-%06d" % index)
            self.assertEqual(response.status_code, 503)
        self.assertEqual(self.harness.planner.calls, [])
        self.assertEqual(self.harness.rows("planner_ingress_receipts"), [])

    def test_an_auth_refusal_leaks_no_path_or_credential(self):
        """The fake's own detail names a directory; it must not survive."""
        self.break_session(planner_session.STATUS_SESSION_EXPIRED)
        body = self.submit().text
        self.assertNotIn("/var/lib/secret/place", body)
        self.assertNotIn(str(self.harness.home), body)
        self.assertNotIn("claude-planner", body)
        self.assertNotIn(".credentials.json", body)
        for forbidden in ("CLAUDE_CONFIG_DIR", "ANTHROPIC", "HOME=", "token"):
            with self.subTest(value=forbidden):
                self.assertNotIn(forbidden, body)

    def test_all_the_authority_invariants_still_hold_under_auth_failure(self):
        """Requirement 10, restated where it is most likely to be forgotten."""
        for state in (
            planner_session.STATUS_LOGIN_REQUIRED,
            planner_session.STATUS_SESSION_EXPIRED,
            planner_session.STATUS_UNPREPARED,
            planner_session.STATUS_PERMISSIONS_UNSAFE,
        ):
            with self.subTest(session=state):
                self.break_session(state)
                self.submit(client_request_id="gpt-invariant-01")
                self.harness.assert_no_authority_anywhere()
        tasks = self.harness.client.get(
            "/api/tasks", headers=self.harness.bridge_auth
        ).json()
        self.assertEqual(tasks["tasks"], [])
        self.assertFalse((self.harness.roots["alpha"] / ".git").exists())


# -- the bootstrap surface --------------------------------------------------------


class TheBootstrapIsSafe(SessionHarness):
    def test_status_reports_a_fresh_host_truthfully(self):
        from cofferdam.workstation.planner import auth

        payload = auth.status_payload(self.state)
        self.assertIn(
            payload["status"],
            (planner_session.STATUS_UNPREPARED, planner_session.STATUS_CLI_MISSING),
        )
        self.assertFalse(payload["usable"])

    def test_status_never_prints_a_path_or_a_credential(self):
        from cofferdam.workstation.planner import auth

        signed_in_planner_session(self.state)
        payload = json.dumps(auth.status_payload(self.state))
        self.assertNotIn(str(self.state), payload)
        self.assertNotIn(PLANNER_CREDENTIAL_MARKER, payload)

    def test_the_login_flow_points_at_the_planner_root_only(self):
        built = claude_session.login_environment(
            self.state,
            planner_session.NAMESPACE,
            inherited={
                "HOME": "/home/operator",
                "DISPLAY": ":0",
                "ANTHROPIC_API_KEY": "leak",
                "CLAUDE_CONFIG_DIR": "/home/operator/.claude",
            },
        )
        self.assertEqual(
            built["CLAUDE_CONFIG_DIR"],
            str(planner_session.config_directory(self.state)),
        )
        self.assertNotIn("ANTHROPIC_API_KEY", built)
        # A login legitimately keeps the terminal and the display.
        self.assertEqual(built["DISPLAY"], ":0")
        self.assertEqual(built["HOME"], "/home/operator")

    def test_the_login_flow_cannot_touch_the_worker_session(self):
        built = claude_session.login_environment(
            self.state, planner_session.NAMESPACE, inherited={}
        )
        self.assertNotIn(
            str(worker_session.config_directory(self.state)),
            json.dumps(built),
        )

    def test_the_two_entry_points_are_different_commands(self):
        from cofferdam.workstation.planner import auth as planner_auth
        from cofferdam.workstation.worker import auth as worker_auth

        self.assertNotEqual(planner_auth.PROG, worker_auth.PROG)
        self.assertIn("planner.auth", planner_auth.PROG)
        self.assertIn("worker.auth", worker_auth.PROG)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
