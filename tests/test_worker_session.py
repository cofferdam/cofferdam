"""Cofferdam's own Claude session: owned, persistent, and never the operator's.

The defect these tests exist for
--------------------------------

PR1e copied ``~/.claude/.credentials.json`` into a throwaway home for every
dispatch. The worker refreshed the token *in the copy*, the provider rotated the
refresh token, the copy was discarded, and the operator's file kept a superseded
one. Both sessions then failed to authenticate — observed for real during PR1f
validation, not theorised.

So the first thing this file asserts is a **negative**: no code path copies,
reads, imports or falls back to the operator's credential. A negative is easy to
assert vacuously, so it is asserted three ways — the built argument vector, the
filesystem after a prepare, and the absence of the function that used to do it.

No real token is used anywhere in this file. The sentinel is fake, and nothing
here parses, prints or transmits credential material.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from cofferdam.workstation.tasks.adapters.claude_code_worker import adapter as worker_adapter
from cofferdam.workstation.tasks.adapters.claude_code_worker import cli
from cofferdam.workstation.worker import auth, sandbox, session

FAKE_TOKEN = "sk-ant-oat01-FAKESENTINEL-DO-NOT-EXFILTRATE-8W3Q"
FAKE_CREDENTIAL = json.dumps(
    {"claudeAiOauth": {"accessToken": FAKE_TOKEN, "refreshToken": "R-FAKE-9Z1"}}
)


class SessionHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.state_dir = self.dir / "state"
        self.state_dir.mkdir()

    def sign_in(self) -> Path:
        """Give the session a credential-shaped file. Fake, never a real token."""
        config = session.prepare(self.state_dir)
        credential = session.credential_path(self.state_dir)
        credential.write_text(FAKE_CREDENTIAL, encoding="utf-8")
        os.chmod(credential, 0o600)
        return config


# -- the negative: nothing copies the operator's credential -------------------


class TheOperatorCredentialIsNeverCopied(SessionHarness):
    """PR1g's whole point, asserted three independent ways."""

    def test_preparing_a_session_creates_nothing_but_a_directory(self):
        config = session.prepare(self.state_dir)
        self.assertTrue(config.is_dir())
        self.assertEqual(list(config.iterdir()), [], "prepare() populated the session")
        self.assertFalse(session.credential_path(self.state_dir).exists())

    def test_a_fresh_session_reports_login_required_not_ready(self):
        session.prepare(self.state_dir)
        found = session.status(self.state_dir)
        self.assertEqual(found.status, session.STATUS_LOGIN_REQUIRED)
        self.assertTrue(found.needs_login)
        self.assertFalse(found.usable)

    def test_the_copying_helper_no_longer_exists(self):
        """`build_home` and `default_credentials` were the copy. Both are gone."""
        self.assertFalse(hasattr(sandbox, "default_credentials"))
        self.assertFalse(hasattr(sandbox, "build_home"))

    def test_no_module_reads_the_operator_credential_path(self):
        """A grep-shaped check, but over *imported* modules rather than text."""
        for module in (session, sandbox, worker_adapter, auth):
            source = Path(module.__file__).read_text(encoding="utf-8")
            for forbidden in (
                'expanduser("~/.claude/.credentials.json")',
                "expanduser('~/.claude/.credentials.json')",
                "shutil.copyfile",
                "shutil.copy2",
            ):
                self.assertNotIn(forbidden, source, f"{module.__name__}: {forbidden}")

    def test_the_plan_never_binds_the_operator_session(self):
        config = self.sign_in()
        work = self.dir / "work"
        work.mkdir()
        with mock.patch.object(sandbox, "find_bwrap", return_value=Path("/usr/bin/bwrap")):
            plan = sandbox.build_plan(
                worktree=work, cli_directory=Path("/usr"),
                command=("/bin/true",), session_config=config,
            )
        operator = os.path.expanduser("~/.claude")
        for bound in plan.bound_host_paths():
            self.assertFalse(bound == operator or bound.startswith(operator + "/"))

    def test_an_expired_session_does_not_fall_back_to_the_operator(self):
        """The fallback that would silently reintroduce the whole defect."""
        session.prepare(self.state_dir)  # no credential at all
        with self.assertRaises(session.WorkerSessionUnavailable) as caught:
            session.require_usable(self.state_dir)
        self.assertTrue(caught.exception.needs_login)
        # And still nothing was created or imported.
        self.assertFalse(session.credential_path(self.state_dir).exists())


# -- where the session lives, and who may say ---------------------------------


class TheLocationIsCodeOwned(SessionHarness):
    def test_the_path_is_a_pure_function_of_the_state_directory(self):
        self.assertEqual(
            session.config_directory(self.state_dir),
            self.state_dir / "claude-worker" / "config",
        )

    def test_no_caller_can_select_the_session_path(self):
        """Neither the dispatch API nor the adapter takes a credential location."""
        import inspect

        from cofferdam.workstation.planner.dispatch_service import WorkerDispatchService

        dispatch = set(
            inspect.signature(
                WorkerDispatchService.dispatch_approved_worker_prompt
            ).parameters
        )
        self.assertEqual(dispatch, {"self", "planner_request_id", "provenance"})

        adapter_parameters = set(
            inspect.signature(worker_adapter.ClaudeCodeWorkerAdapter.__init__).parameters
        )
        for forbidden in (
            "credentials", "credential_path", "session_dir", "config_dir",
            "home", "claude_home", "token",
        ):
            self.assertNotIn(forbidden, adapter_parameters)

    def test_a_project_cannot_select_the_session_path(self):
        from cofferdam.workstation.tasks.projects import TaskProject

        fields = set(TaskProject.__dataclass_fields__)
        for forbidden in ("credentials", "claude_home", "session_dir", "config_dir"):
            self.assertNotIn(forbidden, fields)

    def test_the_session_is_not_inside_a_project_or_a_worktree(self):
        from cofferdam.workstation.worker import worktree

        config = session.config_directory(self.state_dir)
        worktrees = worktree.worktrees_root(self.state_dir)
        self.assertFalse(str(config).startswith(str(worktrees) + os.sep))

    def test_the_start_signature_takes_no_credential(self):
        import inspect

        parameters = set(
            inspect.signature(worker_adapter.ClaudeCodeWorkerAdapter.start).parameters
        )
        self.assertEqual(parameters, {"self", "context"})


class PermissionsAreChecked(SessionHarness):
    def test_a_prepared_session_is_owner_only(self):
        config = session.prepare(self.state_dir)
        self.assertEqual(config.stat().st_mode & 0o777, 0o700)
        ok, detail = session.permissions_safe(self.state_dir)
        self.assertTrue(ok, detail)

    def test_a_group_readable_session_is_refused(self):
        config = self.sign_in()
        os.chmod(config, 0o750)
        ok, detail = session.permissions_safe(self.state_dir)
        self.assertFalse(ok)
        self.assertEqual(
            session.status(self.state_dir).status, session.STATUS_PERMISSIONS_UNSAFE
        )
        with self.assertRaises(session.WorkerSessionUnavailable):
            session.require_usable(self.state_dir)

    def test_a_world_readable_credential_is_refused(self):
        self.sign_in()
        os.chmod(session.credential_path(self.state_dir), 0o644)
        ok, _ = session.permissions_safe(self.state_dir)
        self.assertFalse(ok)

    def test_preparing_twice_is_idempotent_and_keeps_the_credential(self):
        self.sign_in()
        before = session.credential_path(self.state_dir).read_text()
        session.prepare(self.state_dir)
        self.assertEqual(session.credential_path(self.state_dir).read_text(), before)


# -- persistence --------------------------------------------------------------


class TheSessionSurvives(SessionHarness):
    """Steps 8 and 9: across jobs, and across service reconstruction."""

    def test_the_same_directory_is_resolved_every_time(self):
        first = session.config_directory(self.state_dir)
        second = session.config_directory(Path(str(self.state_dir)))
        self.assertEqual(first, second)

    def test_a_rewrite_of_the_credential_persists(self):
        """What a token refresh does, and the property PR1e lacked.

        PR1e's copy lived in a per-dispatch directory that was thrown away, so a
        refresh written into it vanished. Here the refresh lands in the canonical
        location and is simply still there.
        """
        self.sign_in()
        credential = session.credential_path(self.state_dir)
        before = credential.stat().st_mtime_ns

        # Stand in for a refresh: the CLI rewrites the file in place.
        time.sleep(0.01)
        credential.write_text(
            json.dumps({"claudeAiOauth": {"accessToken": FAKE_TOKEN + "-ROTATED"}}),
            encoding="utf-8",
        )
        self.assertNotEqual(credential.stat().st_mtime_ns, before)
        self.assertIn("ROTATED", credential.read_text())

        # A whole new "process" resolves the same file and sees the rotation.
        rebuilt = session.config_directory(self.state_dir)
        self.assertIn("ROTATED", (rebuilt / session.CREDENTIAL_FILENAME).read_text())
        self.assertEqual(session.status(self.state_dir).status, session.STATUS_READY)

    def test_two_adapter_instances_share_one_session(self):
        """A per-dispatch directory is what the old design got wrong."""
        self.sign_in()
        first = worker_adapter.ClaudeCodeWorkerAdapter(state_dir=self.state_dir)
        second = worker_adapter.ClaudeCodeWorkerAdapter(state_dir=self.state_dir)
        self.assertEqual(
            session.require_usable(first._state_dir),
            session.require_usable(second._state_dir),
        )

    def test_the_session_path_contains_no_task_id(self):
        """If it did, it would be per-dispatch again by another name."""
        rendered = str(session.config_directory(self.state_dir))
        self.assertNotIn("task_", rendered)


# -- the lock -----------------------------------------------------------------


class InvocationsAreSerialized(SessionHarness):
    """Step 10: sharing was not proven safe, so it is not assumed."""

    def test_the_lock_is_exclusive_across_processes(self):
        session.prepare(self.state_dir)
        script = (
            "import sys, time\n"
            "sys.path.insert(0, %r)\n"
            "from cofferdam.workstation.worker import session\n"
            "with session.held(%r, timeout=0.2):\n"
            "    print('HELD', flush=True)\n"
            "    time.sleep(3)\n"
        ) % (str(Path(__file__).resolve().parents[1]), str(self.state_dir))
        child = subprocess.Popen(
            ["python3", "-c", script], stdout=subprocess.PIPE, text=True
        )
        try:
            self.assertEqual((child.stdout.readline() or "").strip(), "HELD")
            with self.assertRaises(session.WorkerSessionUnavailable):
                with session.held(self.state_dir, timeout=0.3):
                    self.fail("two processes held the Claude session at once")
        finally:
            child.kill()
            child.wait(timeout=10)

    def test_the_lock_is_released_when_the_holder_dies(self):
        """An flock, not a pidfile: a killed daemon must not wedge the session."""
        session.prepare(self.state_dir)
        script = (
            "import sys, time\n"
            "sys.path.insert(0, %r)\n"
            "from cofferdam.workstation.worker import session\n"
            "with session.held(%r):\n"
            "    print('HELD', flush=True)\n"
            "    time.sleep(30)\n"
        ) % (str(Path(__file__).resolve().parents[1]), str(self.state_dir))
        child = subprocess.Popen(
            ["python3", "-c", script], stdout=subprocess.PIPE, text=True
        )
        self.assertEqual((child.stdout.readline() or "").strip(), "HELD")
        child.kill()
        child.wait(timeout=10)
        with session.held(self.state_dir, timeout=5):
            pass  # acquired, so the dead holder's lock was released

    def test_the_lock_round_trips_in_one_process(self):
        session.prepare(self.state_dir)
        with session.held(self.state_dir) as config:
            self.assertEqual(config, session.config_directory(self.state_dir))
        with session.held(self.state_dir):
            pass

    def test_the_lock_file_is_not_the_credential(self):
        session.prepare(self.state_dir)
        with session.held(self.state_dir):
            pass
        self.assertNotEqual(
            session.lock_path(self.state_dir), session.credential_path(self.state_dir)
        )
        self.assertTrue(session.lock_path(self.state_dir).is_file())


# -- the typed auth failure ---------------------------------------------------


class AnUnusableSessionIsNotACodeFailure(SessionHarness):
    """Step 11: "needs login" and "is broken" must not look the same."""

    def test_the_two_cli_conditions_are_told_apart(self):
        self.assertEqual(
            session.classify_auth_failure("Not logged in · Please run /login"),
            session.STATUS_LOGIN_REQUIRED,
        )
        self.assertEqual(
            session.classify_auth_failure(
                "Failed to authenticate: OAuth session expired and could not be refreshed"
            ),
            session.STATUS_SESSION_EXPIRED,
        )

    def test_an_ordinary_failure_is_not_relabelled_as_needing_login(self):
        """Mislabelling this way sends a person to a login screen for a bug."""
        for ordinary in (
            "TypeError: expected str",
            "the worker produced no readable result",
            "git: command not found",
            "",
        ):
            self.assertIsNone(session.classify_auth_failure(ordinary), ordinary)

    def test_the_adapter_refuses_before_cutting_a_worktree(self):
        """An unauthenticated session must not leave a branch behind."""
        from cofferdam.workstation.tasks.adapters.protocol import (
            AdapterRefusal,
            TaskContext,
        )
        from cofferdam.workstation.worker import worktree

        session.prepare(self.state_dir)  # never logged in
        instance = worker_adapter.ClaudeCodeWorkerAdapter(state_dir=self.state_dir)
        project = self.dir / "project"
        project.mkdir()
        context = TaskContext(
            task_id="task_" + "a" * 26, correlation_id="c", project_id="alpha",
            project_root=project, adapter_id=worker_adapter.ADAPTER_ID,
            prompt="do it", state="running", lifecycle_revision=1,
        )
        with mock.patch.object(sandbox, "available", return_value=(True, None)):
            with mock.patch.object(cli, "find_executable", return_value=Path("/usr/bin/true")):
                with self.assertRaises(AdapterRefusal) as caught:
                    instance.start(context)
        self.assertIn("login", str(caught.exception).lower())
        self.assertFalse(worktree.worktrees_root(self.state_dir).exists())

    def test_the_refusal_names_the_status_rather_than_a_stack(self):
        session.prepare(self.state_dir)
        try:
            session.require_usable(self.state_dir)
        except session.WorkerSessionUnavailable as exc:
            payload = exc.to_dict()
            self.assertEqual(payload["status"], session.STATUS_LOGIN_REQUIRED)
            self.assertTrue(payload["needs_login"])
            self.assertIn("separate", payload["message"])
        else:  # pragma: no cover
            self.fail("an unauthenticated session was accepted")

    def test_every_status_has_a_sentence(self):
        for state in (
            session.STATUS_READY, session.STATUS_LOGIN_REQUIRED,
            session.STATUS_SESSION_EXPIRED, session.STATUS_CLI_MISSING,
            session.STATUS_UNPREPARED, session.STATUS_PERMISSIONS_UNSAFE,
        ):
            self.assertIn(state, session.SENTENCES)
            self.assertTrue(session.SENTENCES[state].strip())

    def test_the_login_sentences_say_the_operator_session_is_untouched(self):
        for state in session.NEEDS_LOGIN:
            sentence = session.SENTENCES[state].lower()
            self.assertTrue(
                "separate" in sentence or "unaffected" in sentence,
                f"{state} does not reassure the operator about their own session",
            )

    def test_there_is_no_retry_or_relogin_loop(self):
        """A worker job must never attempt a login, let alone repeatedly."""
        source = Path(worker_adapter.__file__).read_text(encoding="utf-8")
        for forbidden in ("auth login", "auth\", \"login", "setup-token", "/login"):
            self.assertNotIn(forbidden, source)


# -- the doctor ---------------------------------------------------------------


class TheDoctorNeverPrintsSecrets(SessionHarness):
    """Step 12 and 13. The strong reason is that nothing here reads a token."""

    def test_the_status_payload_contains_no_credential_material(self):
        self.sign_in()
        payload = session.describe(self.state_dir, cli_version="2.1.221")
        rendered = json.dumps(payload)
        self.assertNotIn(FAKE_TOKEN, rendered)
        self.assertNotIn("R-FAKE-9Z1", rendered)
        self.assertNotIn("accessToken", rendered)
        self.assertNotIn("refreshToken", rendered)

    def test_the_status_payload_contains_no_host_path(self):
        self.sign_in()
        rendered = json.dumps(session.describe(self.state_dir))
        self.assertNotIn(str(self.state_dir), rendered)
        self.assertNotIn(str(Path.home()), rendered)

    def test_the_doctor_reports_the_facts_it_can_have(self):
        self.sign_in()
        payload = session.describe(self.state_dir, cli_version="2.1.221")
        self.assertTrue(payload["prepared"])
        self.assertTrue(payload["credential_present"])
        self.assertTrue(payload["permissions_ok"])
        self.assertEqual(payload["cli_version"], "2.1.221")
        self.assertEqual(payload["status"], session.STATUS_READY)

    def test_the_probe_keeps_only_the_three_non_secret_fields(self):
        """A future CLI adding a token field would not leak it through here."""
        self.sign_in()
        fake_cli = self.dir / "fake-claude"
        fake_cli.write_text(
            "#!/bin/sh\n"
            'echo \'{"loggedIn": true, "authMethod": "oauth", '
            '"apiProvider": "firstParty", "accessToken": "' + FAKE_TOKEN + '"}\'\n'
        )
        os.chmod(fake_cli, 0o755)
        payload = session.probe(self.state_dir, fake_cli)
        self.assertEqual(payload.get("loggedIn"), True)
        self.assertNotIn("accessToken", payload)
        self.assertNotIn(FAKE_TOKEN, json.dumps(payload))

    def test_the_probe_survives_a_cli_that_prints_nothing_usable(self):
        self.sign_in()
        fake_cli = self.dir / "broken-claude"
        fake_cli.write_text("#!/bin/sh\necho not json\n")
        os.chmod(fake_cli, 0o755)
        payload = session.probe(self.state_dir, fake_cli)
        self.assertFalse(payload["reachable"])

    def test_the_probe_points_the_cli_at_the_worker_session(self):
        """Not the operator's — asserted by capturing the environment it uses."""
        self.sign_in()
        recorder = self.dir / "record-env"
        output = self.dir / "env.txt"
        recorder.write_text(
            f"#!/bin/sh\nprintenv CLAUDE_CONFIG_DIR > {output}\necho '{{}}'\n"
        )
        os.chmod(recorder, 0o755)
        session.probe(self.state_dir, recorder)
        self.assertEqual(
            output.read_text().strip(),
            str(session.config_directory(self.state_dir)),
        )

    def test_login_targets_the_worker_session_and_not_the_operator(self):
        """The one property that makes running `login` safe to recommend.

        A login pointed at the wrong config root would sign the *operator* out
        and rotate their token — the exact class of damage PR1g exists to stop.
        Checked by having the fake CLI record the environment it was handed.
        """
        recorder = self.dir / "record-login"
        output = self.dir / "login-env.txt"
        recorder.write_text(
            "#!/bin/sh\n"
            f"printenv CLAUDE_CONFIG_DIR > {output}\n"
            f"printenv ANTHROPIC_API_KEY >> {output} 2>/dev/null || true\n"
            "exit 0\n"
        )
        os.chmod(recorder, 0o755)
        with mock.patch.object(auth, "_executable", return_value=recorder):
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "should-be-dropped"}):
                auth.command_login(self.state_dir)
        recorded = output.read_text().strip().splitlines()
        self.assertEqual(
            recorded[0], str(session.config_directory(self.state_dir))
        )
        # An inherited API key would silently authenticate as somebody else.
        self.assertEqual(len(recorded), 1, f"a key leaked into the login: {recorded}")

    def test_login_never_touches_the_operator_directory(self):
        operator = Path(os.path.expanduser("~/.claude"))
        before = operator.stat().st_mtime_ns if operator.exists() else None
        recorder = self.dir / "noop-login"
        recorder.write_text("#!/bin/sh\nexit 0\n")
        os.chmod(recorder, 0o755)
        with mock.patch.object(auth, "_executable", return_value=recorder):
            auth.command_login(self.state_dir)
        if before is not None:
            self.assertEqual(operator.stat().st_mtime_ns, before)

    def test_the_auth_cli_status_is_serializable_and_secret_free(self):
        self.sign_in()
        with mock.patch.object(auth, "default_state_dir", return_value=self.state_dir):
            payload = auth.status_payload(self.state_dir)
        rendered = json.dumps(payload)
        self.assertNotIn(FAKE_TOKEN, rendered)
        self.assertNotIn("refreshToken", rendered)


# -- secrets never reach a read model -----------------------------------------


class NoFakeTokenReachesAnyReadSurface(SessionHarness):
    """Step 13, against every channel a credential could ride out on."""

    def test_the_scrubber_redacts_credential_shaped_text(self):
        for secret in (
            "my token is " + FAKE_TOKEN,
            '{"accessToken": "' + FAKE_TOKEN + '"}',
            '{"refreshToken":"R-FAKE-9Z1XXXXXXXX"}',
        ):
            self.assertIn("[redacted]", worker_adapter._scrub(secret), secret)

    def test_an_auth_failure_message_carries_no_token(self):
        """The path a real dead session takes, with a token in the CLI's text."""
        noisy = (
            "Failed to authenticate: OAuth session expired and could not be "
            "refreshed (token " + FAKE_TOKEN + ")"
        )
        classified = session.classify_auth_failure(noisy)
        self.assertEqual(classified, session.STATUS_SESSION_EXPIRED)
        # The adapter reports the *sentence*, not the CLI's text.
        self.assertNotIn(FAKE_TOKEN, session.SENTENCES[classified])

    def test_a_session_refusal_carries_no_token(self):
        session.prepare(self.state_dir)
        try:
            session.require_usable(self.state_dir)
        except session.WorkerSessionUnavailable as exc:
            self.assertNotIn(FAKE_TOKEN, json.dumps(exc.to_dict()))
            self.assertNotIn(FAKE_TOKEN, str(exc))

    def test_the_sandbox_plan_carries_no_token(self):
        config = self.sign_in()
        work = self.dir / "work"
        work.mkdir()
        with mock.patch.object(sandbox, "find_bwrap", return_value=Path("/usr/bin/bwrap")):
            plan = sandbox.build_plan(
                worktree=work, cli_directory=Path("/usr"),
                command=("/bin/true",), session_config=config,
            )
        rendered = " ".join(plan.argv) + json.dumps(plan.environment)
        self.assertNotIn(FAKE_TOKEN, rendered)
        self.assertNotIn("R-FAKE-9Z1", rendered)

    def test_the_environment_carries_a_path_not_a_token(self):
        self.assertEqual(
            sandbox.ENVIRONMENT["CLAUDE_CONFIG_DIR"], session.INTERIOR_CONFIG
        )
        for name, value in sandbox.ENVIRONMENT.items():
            self.assertNotIn("token", name.lower())
            self.assertNotIn("sk-ant", value)


# -- the namespace still contains what it did ---------------------------------


class ThePr1eBoundariesAreUnchanged(SessionHarness):
    """Step 5, 6 and 7: PR1g moved a directory, not a boundary."""

    def plan(self):
        config = self.sign_in()
        work = self.dir / "work"
        work.mkdir()
        with mock.patch.object(sandbox, "find_bwrap", return_value=Path("/usr/bin/bwrap")):
            return sandbox.build_plan(
                worktree=work, cli_directory=Path("/usr"),
                command=("/bin/true",), session_config=config,
            )

    def test_the_credential_is_outside_the_granted_work_scope(self):
        self.assertFalse(session.INTERIOR_CONFIG.startswith("/work"))
        granted = cli.scoped_tools(sandbox.INTERIOR_WORKTREE)
        for tool in granted:
            self.assertNotIn(session.INTERIOR_CONFIG, tool)

    def test_the_denied_paths_still_cover_the_session(self):
        """Defense in depth: the config root sits inside the denied path class."""
        self.assertTrue(session.INTERIOR_CONFIG.startswith("/home/worker/"))
        denied = " ".join(cli.DENIED_PATHS)
        for tool in ("Read", "Glob", "Grep"):
            self.assertIn(f"{tool}(/home/worker/**)", denied)

    def test_the_model_tool_surface_is_unchanged(self):
        self.assertEqual(
            set(cli.PROFILE_TOOLS), {"Read", "Write", "Edit", "Glob", "Grep"}
        )
        for tool in ("Bash", "WebFetch", "WebSearch", "Task", "Artifact", "ToolSearch"):
            self.assertIn(tool, cli.DENIED_TOOLS)

    def test_mcp_is_still_off(self):
        argv = cli.build_interior_argv(
            interior_cli="/opt/claude-cli", interior_worktree="/work"
        )
        self.assertIn("--strict-mcp-config", argv)
        self.assertNotIn("--mcp-config", argv)

    def check_argv(self):
        """A check plan on any host, including one with no bubblewrap.

        `checks.build_plan` refuses to build anything when it cannot find the
        executable — correct fail-closed behaviour, and the reason these two
        tests errored on every CI runner. The *lookup* is stubbed rather than the
        tests being skipped: these assert the credential-free and no-network
        properties of the check sandbox, and a runner is exactly where those most
        need to hold. Same remedy as `SandboxPlanIsBounded` in
        `test_worker_containment.py`.
        """
        from cofferdam.workstation.worker import checks

        work = self.dir / "work"
        work.mkdir(exist_ok=True)
        with mock.patch.object(
            checks.shutil, "which", return_value="/usr/bin/bwrap"
        ):
            return checks.build_plan(worktree=work, command=("true",))

    def test_the_controller_still_has_network_and_the_checks_do_not(self):
        plan = self.plan()
        self.assertNotIn("--unshare-net", plan.argv)
        self.assertIn("--unshare-net", self.check_argv())

    def test_the_check_sandbox_binds_no_claude_session(self):
        rendered = " ".join(self.check_argv())
        self.assertNotIn(str(session.config_directory(self.state_dir)), rendered)
        self.assertNotIn("claude-worker", rendered)
        self.assertNotIn(".claude", rendered)

    def test_no_github_credential_appears_anywhere(self):
        """Step 17: PR1g adds no publisher authority of any kind."""
        plan = self.plan()
        rendered = " ".join(plan.argv) + json.dumps(plan.environment)
        for forbidden in ("GITHUB_TOKEN", "GH_TOKEN", "deploy_key", "id_rsa", "ssh"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
