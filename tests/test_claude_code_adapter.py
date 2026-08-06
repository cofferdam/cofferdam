"""The Claude Code adapter, asserted rather than trusted.

Organised by the question each group answers, because that is how somebody
reviewing an agent that runs processes on a personal machine reads it:

1.  Can a client turn it on, point it somewhere, or make it run something?
2.  Does the prompt stay out of argv, logs and audit?
3.  Does the parser survive hostile output?
4.  Is a state ever claimed without evidence for it?
5.  Can one task reach another task's session or process?
6.  Does cancellation hit exactly one process, and nothing else?

Most of these run a **real fake CLI in a real process** — see
``tests/_claude_doubles.py`` for why. Real pids, real ``/proc`` start times,
real signals to a real process group. The parser tests are pure and need no
process at all.
"""

from __future__ import annotations

import json
import os
import re
import signal
import tempfile
import time
import unittest
from pathlib import Path
from typing import Optional, Sequence

from ._claude_doubles import (
    FakeClaude,
    always_authenticated,
    make_fake,
    never_authenticated,
)
from ._task_doubles import PROJECT_ID, TaskTestCase, python_code_only

from cofferdam.workstation.config import load_config
from cofferdam.workstation.tasks.adapters import AdapterRegistry, build_registry
from cofferdam.workstation.tasks.adapters.claude_code import (
    ADAPTER_ID,
    ClaudeCodeAdapter,
    cli,
    evidence,
    frames,
    process,
)
from cofferdam.workstation.tasks.adapters.claude_code.process import ClaudeRun
from cofferdam.workstation.tasks.models import (
    EVIDENCE_ADAPTER_REPORTED,
    EVIDENCE_GIT_OBSERVED,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_RUNNING,
    STATE_WAITING_FOR_USER,
    WAITING_APPROVAL,
    WAITING_AUTHENTICATION,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "adapters" / "claude_code"


def package_sources():
    return sorted(PACKAGE.rglob("*.py"))


# ===========================================================================
# 1. Enablement — the client cannot turn it on
# ===========================================================================


class Enablement(unittest.TestCase):
    def test_adapter_is_absent_by_default(self):
        """1. A default build registers no Claude Code adapter."""
        self.assertNotIn(ADAPTER_ID, build_registry().ids())

    def test_default_config_leaves_it_off(self):
        """1. And the default configuration does not turn it on either."""
        with tempfile.TemporaryDirectory() as home:
            config = load_config(Path(home))
            self.assertFalse(config.enable_claude_code_adapter)

    def test_registry_registers_it_only_when_asked(self):
        registry = build_registry(enable_claude_code_adapter=True)
        self.assertEqual(registry.ids(), (ADAPTER_ID,))

    def test_validation_adapter_is_not_enabled_alongside_it(self):
        """Normal configuration must not expose the deterministic adapter."""
        registry = build_registry(enable_claude_code_adapter=True)
        self.assertNotIn("validation", registry.ids())

    def test_build_registry_takes_no_client_shaped_argument(self):
        """2. There is no parameter a request could ride in on.

        ``build_registry`` accepts two booleans and nothing else: no path, no
        class, no module name, no mapping. A client that could pass *anything*
        here would still not be able to name what runs.
        """
        import inspect

        parameters = inspect.signature(build_registry).parameters
        self.assertEqual(
            sorted(parameters), ["enable_claude_code_adapter", "enable_validation_adapter"]
        )
        for parameter in parameters.values():
            self.assertIsInstance(parameter.default, bool)

    def test_no_route_writes_the_enable_flag(self):
        """2. Nothing in the service assigns the flag from a request."""
        source = (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text("utf-8")
        for line in source.splitlines():
            if "enable_claude_code_adapter" in line:
                # The only mention is reading it off the server's own config.
                self.assertIn("config.enable_claude_code_adapter", line)

    def test_adapter_id_is_not_constructible_from_a_string(self):
        """A registry miss is a refusal, never an import."""
        registry = build_registry()
        from cofferdam.workstation.tasks.errors import AdapterUnknown

        with self.assertRaises(AdapterUnknown):
            registry.get(ADAPTER_ID)
        with self.assertRaises(AdapterUnknown):
            registry.get(
                "cofferdam.workstation.tasks.adapters.claude_code.ClaudeCodeAdapter"
            )


# ===========================================================================
# 2. The command line — what a client can never put in it
# ===========================================================================


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.argv = cli.build_argv(Path("/usr/bin/claude"), "s-1")

    def test_executable_is_fixed(self):
        """12. The program is the one this module found, at argv[0]."""
        self.assertEqual(self.argv[0], "/usr/bin/claude")
        self.assertEqual(cli.EXECUTABLE_NAME, "claude")

    def test_build_argv_accepts_nothing_but_a_session_id(self):
        """4, 5, 6, 7, 9, 10, 11. One caller-supplied value, and it is a uuid."""
        import inspect

        self.assertEqual(
            sorted(inspect.signature(cli.build_argv).parameters), ["executable", "session_id"]
        )

    def test_unsafe_permission_bypass_is_absent(self):
        """18. The bypass flags are not in the argv, in any form."""
        joined = " ".join(self.argv)
        self.assertNotIn("--dangerously-skip-permissions", joined)
        self.assertNotIn("--allow-dangerously-skip-permissions", joined)
        self.assertNotIn("bypassPermissions", joined)

    def test_no_forbidden_flag_is_present(self):
        for flag in cli.FORBIDDEN_FLAGS:
            self.assertNotIn(flag, self.argv, flag + " is in the argv")

    def test_permission_mode_is_the_one_profile(self):
        """9. The mode is a constant; there is no second choice to select."""
        self.assertEqual(cli.PROFILE_PERMISSION_MODE, "acceptEdits")
        self.assertEqual(
            self.argv[self.argv.index("--permission-mode") + 1], "acceptEdits"
        )

    def test_bash_is_not_among_the_tools(self):
        """10. The single most important line in the profile."""
        self.assertNotIn("Bash", cli.PROFILE_TOOLS)
        tools = self.argv[self.argv.index("--tools") + 1]
        self.assertNotIn("Bash", tools)
        self.assertNotIn("WebFetch", tools)

    def test_mcp_configuration_is_refused_wholesale(self):
        """11. Strict MCP with no config file means: none of them."""
        self.assertIn("--strict-mcp-config", self.argv)
        self.assertNotIn("--mcp-config", self.argv)

    def test_settings_sources_are_empty(self):
        """No user, project or local settings file can widen the profile."""
        self.assertEqual(self.argv[self.argv.index("--setting-sources") + 1], "")

    def test_the_run_is_bounded(self):
        self.assertIn("--max-turns", self.argv)
        self.assertIn("--max-budget-usd", self.argv)

    def test_environment_is_an_allowlist_not_a_denylist(self):
        """7. A new variable reaches the child only when somebody adds it here."""
        built = cli.build_environment(
            {"HOME": "/home/x", "SECRET_TOKEN": "abc", "ANTHROPIC_API_KEY": "sk-1"}
        )
        self.assertEqual(built["HOME"], "/home/x")
        self.assertNotIn("SECRET_TOKEN", built)
        self.assertNotIn("ANTHROPIC_API_KEY", built)
        for name in built:
            self.assertTrue(
                name in cli.ENVIRONMENT_ALLOWLIST or name in cli.ENVIRONMENT_FORCED,
                name + " reached the child without being named in source",
            )

    def test_no_api_key_variable_is_in_the_allowlist(self):
        for name in cli.ENVIRONMENT_ALLOWLIST:
            self.assertNotIn("ANTHROPIC", name.upper())
            self.assertNotIn("KEY", name.upper())
            self.assertNotIn("TOKEN", name.upper())


class NoShellAnywhere(unittest.TestCase):
    def test_no_shell_invocation_exists_in_the_package(self):
        """17. Structural: no shell, in any spelling."""
        for path in package_sources():
            source = python_code_only(path.read_text("utf-8"))
            for forbidden in (
                "shell=True",
                "os.system",
                "os.popen",
                "bash -c",
                "sh -c",
                "eval(",
                "exec(",
                "/bin/sh",
                "/bin/bash",
            ):
                self.assertNotIn(forbidden, source, str(path) + " uses " + forbidden)

    def test_every_subprocess_call_passes_shell_false(self):
        import ast

        found = 0
        for path in package_sources():
            tree = ast.parse(path.read_text("utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                target = node.func
                name = target.attr if isinstance(target, ast.Attribute) else None
                if name not in ("run", "Popen"):
                    continue
                keywords = {k.arg: k.value for k in node.keywords}
                self.assertIn("shell", keywords, str(path) + " omits shell=")
                self.assertIs(keywords["shell"].value, False)
                found += 1
        self.assertGreater(found, 0, "no subprocess call was inspected")

    def test_adapter_never_matches_a_process_by_name(self):
        """The replacement for the broad guard this package is excepted from."""
        for path in package_sources():
            source = python_code_only(path.read_text("utf-8"))
            for forbidden in ("pkill", "killall", "pidof", "psutil", "pgrep", "comm="):
                self.assertNotIn(forbidden, source, str(path) + " uses " + forbidden)


# ===========================================================================
# 3. The prompt — where it goes, and where it must not
# ===========================================================================


class ClaudeRunTestCase(unittest.TestCase):
    """Launches the fake CLI for real, and always cleans the process up."""

    behaviour = "complete"

    def setUp(self):
        self.fake, holder = make_fake(self.behaviour)
        self.addCleanup(holder.cleanup)
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.root = Path(self._root.name)
        self.runs = []

    def launch(self, **kwargs) -> ClaudeRun:
        run = ClaudeRun(
            task_id=kwargs.pop("task_id", "task-1"),
            executable=self.fake.path,
            project_root=self.root,
            environment=self.fake.environment(),
            **kwargs,
        )
        self.runs.append(run)
        self.addCleanup(self._cleanup, run)
        return run

    def _cleanup(self, run):
        try:
            run.stop(reason="test_cleanup")
            run.reap(timeout=3.0)
        except Exception:
            pass

    def result_of(self, run, timeout=20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with run.lock:
                if run.state.last_result is not None:
                    return run.state.last_result
            time.sleep(0.02)
        return None


class PromptTransport(ClaudeRunTestCase):
    def test_prompt_is_not_in_argv(self):
        """13, 52. Proven by the launched process, not by the launcher."""
        run = self.launch()
        self.assertTrue(run.start())
        secret = "SENTINEL-PROMPT-Ünïcode-42"
        self.assertTrue(run.send_turn(secret))
        self.assertIsNotNone(self.result_of(run))

        record = self.fake.launch_record()
        self.assertIsNotNone(record, "the fake CLI recorded no launch")
        for argument in record["argv"]:
            self.assertNotIn("SENTINEL", argument)
        self.assertNotIn("SENTINEL", " ".join(record["argv"]))

    def test_prompt_is_not_in_the_child_environment(self):
        """7, 52. Not smuggled in as a variable either."""
        run = self.launch()
        self.assertTrue(run.start())
        run.send_turn("SENTINEL-PROMPT")
        self.assertIsNotNone(self.result_of(run))
        record = self.fake.launch_record()
        for name in record["env"]:
            self.assertNotIn("SENTINEL", name)

    def test_prompt_is_delivered_through_the_documented_stdin_channel(self):
        """14. The turn arrives, and the fake echoes back what it read."""
        run = self.launch()
        self.assertTrue(run.start())
        run.send_turn("hello there")
        result = self.result_of(run)
        self.assertIsNotNone(result)
        self.assertIn("hello there", result.text)

    def test_turkish_and_unicode_survive_the_round_trip(self):
        """15. The characters that break naive encoding handling."""
        text = "Türkçe ğüşiöç İIı — ünlem! 日本語 🇹🇷"
        run = self.launch()
        self.assertTrue(run.start())
        run.send_turn(text)
        result = self.result_of(run)
        self.assertIsNotNone(result)
        self.assertIn("Türkçe ğüşiöç İIı", result.text)
        self.assertIn("🇹🇷", result.text)

    def test_the_turn_is_a_single_json_line(self):
        """A turn cannot be split across lines by content containing newlines."""
        run = self.launch()
        self.assertTrue(run.start())
        run.send_turn("line one\nline two\n{\"fake\": \"json\"}")
        result = self.result_of(run)
        self.assertIsNotNone(result)
        self.assertIn("line one", result.text)

    def test_the_process_runs_in_the_project_root(self):
        """The cwd is the verified root, and comes from nowhere else."""
        run = self.launch()
        self.assertTrue(run.start())
        run.send_turn("x")
        self.assertIsNotNone(self.result_of(run))
        record = self.fake.launch_record()
        self.assertEqual(
            Path(record["cwd"]).resolve(), self.root.resolve()
        )


class PrivacyOfSource(unittest.TestCase):
    def test_package_writes_no_log_line(self):
        """49, 50, 51. Nothing to leak into is stronger than filtering."""
        for path in package_sources():
            source = python_code_only(path.read_text("utf-8"))
            for forbidden in ("logging", "logger", "print(", "syslog", "journal"):
                self.assertNotIn(forbidden, source, str(path) + " uses " + forbidden)

    def test_the_auth_probe_keeps_no_account_identifier(self):
        """24. The probe's JSON carries an email; nothing here stores it."""
        document = json.dumps(
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "email": "someone@example.com",
                "orgId": "org-secret",
                "orgName": "Someone's Organisation",
            }
        )
        status = cli.probe_authentication(
            Path("/usr/bin/claude"), runner=lambda argv, env: (0, document)
        )
        self.assertTrue(status.logged_in)
        self.assertEqual(status.method, "claude.ai")
        self.assertEqual(
            sorted(cli.AuthStatus.__slots__), ["logged_in", "method", "probe_failed"]
        )
        for value in vars(status).values() if hasattr(status, "__dict__") else []:
            self.assertNotIn("example.com", str(value))
        for name in cli.AuthStatus.__slots__:
            self.assertNotIn("example.com", str(getattr(status, name)))
            self.assertNotIn("org-secret", str(getattr(status, name)))

    def test_a_failed_probe_is_not_a_claim_that_the_user_is_signed_out(self):
        def explode(argv, env):
            raise OSError("no such file")

        status = cli.probe_authentication(Path("/usr/bin/claude"), runner=explode)
        self.assertFalse(status.logged_in)
        self.assertTrue(status.probe_failed)

    def test_the_auth_probe_invocation_is_fixed(self):
        seen = {}

        def capture(argv, env):
            seen["argv"] = list(argv)
            return 0, '{"loggedIn": true}'

        cli.probe_authentication(Path("/usr/bin/claude"), runner=capture)
        self.assertEqual(
            seen["argv"], ["/usr/bin/claude", "auth", "status", "--json"]
        )


# ===========================================================================
# 4. The parser — bounded, closed, and hostile-input safe
# ===========================================================================


class Parser(unittest.TestCase):
    def setUp(self):
        self.state = frames.StreamState()

    def parse(self, frame):
        text = frame if isinstance(frame, str) else json.dumps(frame)
        return frames.parse_frame(text, self.state)

    def test_unknown_frame_types_never_become_events(self):
        """21. An unrecognised type is counted, not published."""
        records = self.parse({"type": "brand_new_thing", "payload": {"a": [1, 2]}})
        self.assertEqual(records, [])
        self.assertEqual(self.state.ignored_frames, 1)

    def test_malformed_json_is_counted_not_raised(self):
        self.assertEqual(self.parse("{not json"), [])
        self.assertEqual(self.parse("[1, 2, 3]"), [])
        self.assertEqual(self.state.malformed_frames, 2)

    def test_no_record_can_carry_a_cli_dictionary(self):
        """21. There is nowhere in the record type to put one."""
        fields = set(frames.StreamRecord.__dataclass_fields__)
        self.assertEqual(fields, {"kind", "text", "detail", "tool", "is_error"})
        for name in fields:
            annotation = frames.StreamRecord.__dataclass_fields__[name].type
            self.assertNotIn("Dict", str(annotation))
            self.assertNotIn("Any", str(annotation))

    def test_ansi_escapes_are_removed(self):
        """22. Escape sequences never survive into stored text."""
        text = frames.sanitize("\x1b[31mred\x1b[0m and \x1b]0;title\x07done")
        self.assertEqual(text, "red and done")
        self.assertNotIn("\x1b", text)

    def test_control_characters_are_removed_but_newlines_survive(self):
        text = frames.sanitize("a\x00b\x07c\nsecond line\td")
        self.assertNotIn("\x00", text)
        self.assertNotIn("\x07", text)
        self.assertIn("\n", text)
        self.assertIn("\t", text)

    def test_bidirectional_overrides_are_removed(self):
        text = frames.sanitize("safe‮txet desrever")
        self.assertNotIn("‮", text)

    def test_markup_is_not_executed_and_not_stripped_into_something_else(self):
        """22. It stays inert text; the panel renders with textContent."""
        text = frames.sanitize("<script>alert(1)</script>")
        self.assertEqual(text, "<script>alert(1)</script>")

    def test_thinking_blocks_are_never_parsed(self):
        """Hidden reasoning is not collected. No branch exists for it."""
        records = self.parse(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "SECRET"},
                        {"type": "redacted_thinking", "data": "SECRET"},
                        {"type": "text", "text": "visible"},
                    ],
                },
            }
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].text, "visible")
        for path in package_sources():
            source = python_code_only(path.read_text("utf-8"))
            self.assertNotIn('"thinking"', source.replace('"thinking_tokens"', ""))

    def test_tool_results_report_the_outcome_not_the_body(self):
        records = self.parse(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "content": "THE-ENTIRE-FILE-CONTENTS",
                            "is_error": False,
                        }
                    ],
                },
            }
        )
        self.assertEqual(len(records), 1)
        self.assertNotIn("THE-ENTIRE-FILE", records[0].text or "")

    def test_a_hostile_tool_name_is_dropped_rather_than_echoed(self):
        records = self.parse(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "<img onerror=x>"}],
                },
            }
        )
        self.assertEqual(records[0].text, "Claude used a tool.")
        self.assertIsNone(records[0].tool)

    def test_permission_denials_yield_tool_names_only(self):
        self.parse(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "no",
                "session_id": "s",
                "permission_denials": [
                    {"tool_name": "Bash", "tool_input": {"command": "SECRET-COMMAND"}}
                ],
            }
        )
        self.assertEqual(self.state.last_result.permission_denials, ("Bash",))
        self.assertNotIn("SECRET-COMMAND", json.dumps(self.state.last_result.text or ""))

    def test_a_missing_is_error_field_is_treated_as_an_error(self):
        """28, 29. An absent field never becomes a reason to claim success."""
        self.parse({"type": "result", "subtype": "success", "result": "x", "session_id": "s"})
        self.assertTrue(self.state.last_result.is_error)

    def test_result_text_is_bounded(self):
        """30. A very large result is truncated, not stored whole."""
        self.parse(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "y" * 100000,
                "session_id": "s",
            }
        )
        self.assertLessEqual(len(self.state.last_result.text), frames.MAX_RESULT_CHARS)

    def test_a_second_init_with_a_different_session_is_reported_as_an_error(self):
        self.parse({"type": "system", "subtype": "init", "session_id": "one"})
        records = self.parse({"type": "system", "subtype": "init", "session_id": "two"})
        self.assertTrue(records[0].is_error)
        self.assertEqual(self.state.session_id, "one")

    def test_records_are_capped(self):
        for index in range(frames.MAX_RECORDS + 60):
            self.state.add(frames.StreamRecord(kind="x", text=str(index)))
        self.assertEqual(len(self.state.records), frames.MAX_RECORDS)


class ParserBoundsUnderLoad(ClaudeRunTestCase):
    behaviour = "oversized"

    def test_oversized_frames_are_refused_without_being_buffered(self):
        """19, 20. A 400 KB frame is dropped; the stream stays synchronised."""
        run = self.launch()
        self.assertTrue(run.start())
        run.send_turn("go")
        result = self.result_of(run, timeout=25.0)
        self.assertIsNotNone(result, "the stream desynchronised after a huge frame")
        with run.lock:
            self.assertGreaterEqual(run.state.oversized_frames, 1)
            for record in run.state.records:
                self.assertLessEqual(len(record.text or ""), frames.MAX_TEXT_CHARS)


class ParserSurvivesGarbage(ClaudeRunTestCase):
    behaviour = "unknown_frames"

    def test_unknown_and_non_json_output_does_not_break_the_run(self):
        """20, 21. Garbage in the stream is counted, and the turn still lands."""
        run = self.launch()
        self.assertTrue(run.start())
        run.send_turn("go")
        result = self.result_of(run, timeout=25.0)
        self.assertIsNotNone(result)
        self.assertFalse(result.is_error)
        with run.lock:
            self.assertGreaterEqual(run.state.ignored_frames, 1)
            self.assertGreaterEqual(run.state.malformed_frames, 1)


class ParserStripsAnsiFromRealOutput(ClaudeRunTestCase):
    behaviour = "ansi"

    def test_no_escape_sequence_reaches_the_stored_state(self):
        """22. End to end, through a real process."""
        run = self.launch()
        self.assertTrue(run.start())
        run.send_turn("go")
        self.assertIsNotNone(self.result_of(run))
        with run.lock:
            for record in run.state.records:
                self.assertNotIn("\x1b", record.text or "")
                self.assertNotIn("\x07", record.text or "")


class ParserDropsThinking(ClaudeRunTestCase):
    behaviour = "thinking"

    def test_reasoning_never_reaches_the_state(self):
        run = self.launch()
        self.assertTrue(run.start())
        run.send_turn("go")
        self.assertIsNotNone(self.result_of(run))
        with run.lock:
            blob = json.dumps(
                [record.text for record in run.state.records]
                + [run.state.latest_output, run.state.latest_activity]
            )
        self.assertNotIn("SECRET-REASONING-TOKEN", blob)


# ===========================================================================
# 5. Process identity and cancellation
# ===========================================================================


class ProcessIdentity(ClaudeRunTestCase):
    behaviour = "hang"

    def test_start_time_is_read_from_proc(self):
        """39. The field that a recycled pid cannot reproduce."""
        run = self.launch()
        self.assertTrue(run.start())
        self.assertIsNotNone(run.start_time)
        self.assertEqual(process.read_start_time(run.pid), run.start_time)
        self.assertIsNone(process.read_start_time(999999999))

    def test_identity_requires_the_start_time_to_match(self):
        """39. A wrong start time means the pid is somebody else's."""
        run = self.launch()
        self.assertTrue(run.start())
        self.assertTrue(run.still_ours())
        run.start_time = (run.start_time or 0) + 12345
        self.assertFalse(run.still_ours())

    def test_identity_requires_the_process_group_to_match(self):
        run = self.launch()
        self.assertTrue(run.start())
        run.pgid = (run.pgid or 0) + 99999
        self.assertFalse(run.still_ours())

    def test_the_child_has_its_own_process_group(self):
        """40. Which is what makes a group signal targetable at one task."""
        run = self.launch()
        self.assertTrue(run.start())
        self.assertEqual(os.getpgid(run.pid), run.pid)
        self.assertNotEqual(run.pgid, os.getpgid(os.getpid()))

    def test_nothing_is_signalled_when_identity_is_lost(self):
        """39, 40. No signal is sent on the strength of a stale pid."""
        run = self.launch()
        self.assertTrue(run.start())
        run.start_time = (run.start_time or 0) + 4242
        outcome = run.stop(reason="test")
        self.assertEqual(outcome["result"], "identity_lost")
        self.assertEqual(outcome["signals"], [])
        self.assertEqual(run.signals_sent, [])


class Cancellation(ClaudeRunTestCase):
    behaviour = "hang"

    def test_cancel_stops_the_process_with_a_bounded_escalation(self):
        run = self.launch()
        self.assertTrue(run.start())
        run.send_turn("work forever")
        time.sleep(0.4)
        self.assertIsNone(run.poll())

        outcome = run.stop(reason="user_cancelled")
        run.reap()
        self.assertEqual(outcome["result"], "stopped")
        self.assertIn("SIGTERM", outcome["signals"])
        self.assertIsNotNone(run.exit_code)

    def test_repeated_cancel_is_safe(self):
        """43. And the second one reports the truth rather than repeating."""
        run = self.launch()
        self.assertTrue(run.start())
        run.send_turn("work forever")
        time.sleep(0.3)
        run.stop(reason="first")
        run.reap()
        second = run.stop(reason="second")
        self.assertEqual(second["result"], "already_exited")
        self.assertEqual(second["signals"], [])

    def test_cancel_cannot_affect_another_task(self):
        """41. Two real processes; stopping one leaves the other running."""
        first = self.launch(task_id="task-a")
        second = self.launch(task_id="task-b")
        self.assertTrue(first.start())
        self.assertTrue(second.start())
        first.send_turn("a")
        second.send_turn("b")
        time.sleep(0.4)
        self.assertNotEqual(first.pid, second.pid)
        self.assertNotEqual(first.pgid, second.pgid)

        first.stop(reason="cancel")
        first.reap()
        self.assertIsNotNone(first.exit_code)
        self.assertIsNone(second.poll(), "cancelling one task stopped the other")
        self.assertTrue(second.still_ours())

    def test_cancel_cannot_reach_an_unrelated_process(self):
        """42. A bystander in another process group is untouched.

        The bystander stands in for a Claude session somebody is running in
        their own terminal: same program name, same user, different group.
        Anything that matched by name would kill it.
        """
        import subprocess

        bystander = subprocess.Popen(
            [str(self.fake.path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=self.fake.environment(),
            start_new_session=True,
        )
        self.addCleanup(lambda: (bystander.kill(), bystander.wait()))

        run = self.launch()
        self.assertTrue(run.start())
        run.send_turn("x")
        time.sleep(0.3)
        run.stop(reason="cancel")
        run.reap()

        self.assertIsNone(bystander.poll(), "an unrelated process was signalled")

    def test_only_the_owned_group_is_signalled(self):
        """40. Assert on the call, not only on the survivors."""
        run = self.launch()
        self.assertTrue(run.start())
        run.send_turn("x")
        time.sleep(0.3)

        seen = []
        real_killpg = os.killpg

        def record(pgid, number):
            seen.append((pgid, number))
            return real_killpg(pgid, number)

        os.killpg = record
        try:
            run.stop(reason="cancel")
        finally:
            os.killpg = real_killpg
        run.reap()

        self.assertTrue(seen)
        for pgid, number in seen:
            self.assertEqual(pgid, run.pgid)
            self.assertIn(number, (signal.SIGTERM, signal.SIGKILL))


class LaunchEvidence(ClaudeRunTestCase):
    behaviour = "silent_start"

    def test_a_process_that_never_reports_a_session_is_a_failed_launch(self):
        """27. `running` requires evidence, not a successful fork."""
        original = process.START_EVIDENCE_TIMEOUT_SECONDS
        process.START_EVIDENCE_TIMEOUT_SECONDS = 1.0
        self.addCleanup(
            setattr, process, "START_EVIDENCE_TIMEOUT_SECONDS", original
        )
        run = self.launch()
        self.assertFalse(run.start())
        self.assertEqual(run.launch_error, "no_session_evidence")


class SessionMismatch(ClaudeRunTestCase):
    behaviour = "session_mismatch"

    def test_a_different_session_is_refused_rather_than_adopted(self):
        """27. Cofferdam chose the id; a different one is not this task."""
        run = self.launch()
        self.assertFalse(run.start())
        self.assertEqual(run.launch_error, "session_mismatch")


# ===========================================================================
# 6. Evidence — claims versus observations
# ===========================================================================


class Evidence(unittest.TestCase):
    def setUp(self):
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        self.root = Path(self._holder.name)

    def test_probes_are_a_closed_set(self):
        """56. A command outside the set is refused, even from inside the module."""
        with self.assertRaises(ValueError):
            evidence._run(("git", "log", "--all"), self.root)
        with self.assertRaises(ValueError):
            evidence._run(("rm", "-rf", "/"), self.root)

    def test_no_probe_takes_a_client_shaped_argument(self):
        import inspect

        self.assertEqual(
            sorted(inspect.signature(evidence.observe_git).parameters), ["root", "runner"]
        )
        for command in evidence.ALLOWED_PROBES:
            self.assertEqual(command[0], "git")
            for part in command:
                self.assertNotIn("{", part)
                self.assertNotIn("%", part)

    def test_paths_outside_the_root_are_dropped(self):
        """53. A path that escapes is not evidence about this project."""
        self.assertIsNone(evidence._safe_relative("/etc/passwd", self.root))
        self.assertIsNone(evidence._safe_relative("../../secrets.txt", self.root))
        self.assertIsNone(evidence._safe_relative('"quoted\\npath"', self.root))
        self.assertEqual(evidence._safe_relative("src/app.py", self.root), "src/app.py")

    def test_rename_entries_report_the_new_path(self):
        self.assertEqual(
            evidence._safe_relative("old.py -> new.py", self.root), "new.py"
        )

    def test_git_evidence_is_always_labelled_git_observed(self):
        """55. The source is not a parameter; nothing can be promoted into it."""
        observation = evidence.GitObservation(
            is_repository=True,
            branch="main",
            head="a" * 40,
            changed_paths=("one.py", "two.py"),
            clean=False,
        )
        references = evidence.git_evidence(observation)
        self.assertTrue(references)
        for reference in references:
            self.assertEqual(reference.source, EVIDENCE_GIT_OBSERVED)
            self.assertTrue(reference.verified)

    def test_a_non_repository_produces_no_git_evidence(self):
        self.assertEqual(evidence.git_evidence(evidence.GitObservation()), ())

    def test_a_head_that_is_not_a_commit_id_is_discarded(self):
        def runner(command, root):
            if command == evidence.GIT_IS_REPO:
                return 0, "true\n"
            if command == evidence.GIT_BRANCH:
                return 0, "main\n"
            if command == evidence.GIT_HEAD:
                return 0, "not-a-real-commit-id\n"
            return 0, ""

        observation = evidence.observe_git(self.root, runner=runner)
        self.assertIsNone(observation.head)

    def test_a_repository_with_no_commits_is_still_a_repository(self):
        """The unborn-HEAD case: a fresh sandbox before its first commit."""
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True, shell=False)
        (self.root / "new.txt").write_text("x", encoding="utf-8")

        observation = evidence.observe_git(self.root)
        self.assertTrue(observation.is_repository)
        self.assertIsNone(observation.head)
        self.assertIn("new.txt", observation.changed_paths)

    def test_a_real_repository_is_observed(self):
        import subprocess

        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@example.com"],
            ["git", "config", "user.name", "T"],
        ):
            subprocess.run(command, cwd=self.root, check=True, shell=False)
        (self.root / "a.txt").write_text("one", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True, shell=False)
        subprocess.run(
            ["git", "commit", "-q", "-m", "first"], cwd=self.root, check=True, shell=False
        )
        (self.root / "b.txt").write_text("two", encoding="utf-8")

        observation = evidence.observe_git(self.root)
        self.assertTrue(observation.is_repository)
        self.assertEqual(len(observation.head or ""), 40)
        self.assertIn("b.txt", observation.changed_paths)
        self.assertFalse(observation.clean)

    def test_no_predefined_test_command_is_run_from_the_project_file(self):
        """56, 57. The registry has no field for a command, by name."""
        from cofferdam.workstation.tasks.projects import FORBIDDEN_PROJECT_FIELDS

        for name in ("command", "cmd", "script", "exec", "argv", "env"):
            self.assertIn(name, FORBIDDEN_PROJECT_FIELDS)


# ===========================================================================
# 7. The adapter against real Task Core
# ===========================================================================


class ClaudeTaskTestCase(TaskTestCase):
    """Task Core wired to the Claude adapter, pointed at the fake CLI."""

    enable_validation_adapter = False
    project_adapters = (ADAPTER_ID,)
    behaviour = "complete"
    auth = staticmethod(always_authenticated)
    max_concurrent = 1

    def setUp(self):
        self.fake, holder = make_fake(self.behaviour)
        self.addCleanup(holder.cleanup)
        super().setUp()
        self.addCleanup(self.adapter.shutdown)

    def extra_adapters(self):
        environment = self.fake.environment()

        def run_factory(**kwargs):
            # The environment is injected by the *test*, never by the adapter:
            # production has no parameter for it, which is the property the
            # environment allowlist tests protect.
            return ClaudeRun(environment=environment, **kwargs)

        self.adapter = ClaudeCodeAdapter(
            executable=self.fake.path,
            max_concurrent=self.max_concurrent,
            run_factory=run_factory,
            auth_probe=self.auth,
        )
        return (self.adapter,)

    def build_adapters(self):
        return AdapterRegistry(tuple(self.extra_adapters()))

    def create(self, prompt="do the thing", **kwargs):
        row, created = self.service.create_task(
            project_id=PROJECT_ID, adapter_id=ADAPTER_ID, prompt=prompt, **kwargs
        )
        return row, created

    def settle(self, task_id, timeout=25.0, until=None):
        """Poll the read path until the task reaches a state worth asserting."""
        deadline = time.monotonic() + timeout
        row = self.service.get_task(task_id)
        while time.monotonic() < deadline:
            row = self.service.refresh_task(task_id)
            if until is None:
                if row.state in (
                    STATE_COMPLETED,
                    STATE_FAILED,
                    STATE_CANCELLED,
                    STATE_WAITING_FOR_USER,
                ):
                    return row
            elif until(row):
                return row
            time.sleep(0.05)
        return row


class HappyPath(ClaudeTaskTestCase):
    def test_a_task_runs_and_reaches_a_truthful_waiting_state(self):
        """27. Through queued and starting to running, on process evidence."""
        row, created = self.create("summarise the readme")
        self.assertTrue(created)
        self.assertEqual(row.state, STATE_RUNNING)

        row = self.settle(row.task_id)
        self.assertEqual(row.state, STATE_WAITING_FOR_USER)
        self.assertIn("turn 1 done", row.final_result or "")

    def test_the_history_records_the_lifecycle_in_order(self):
        row, _ = self.create()
        row = self.settle(row.task_id)
        types = [event.event_type for event in self.store.events(row.task_id)]
        self.assertEqual(types[0], "task_created")
        self.assertIn("task_queued", types)
        self.assertIn("adapter_starting", types)
        self.assertIn("task_started", types)

    def test_no_audit_record_or_lifecycle_event_carries_the_prompt(self):
        """49, 51. Where content may live, and where it may not.

        The task row and ``meaningful_output`` events are *designed* to carry
        bounded task content — that is what the authenticated detail view
        reads, and an agent's reply that quotes the prompt back is output, not
        a leak.

        What must never carry it is the audit trail, or any event that is a
        statement about the *lifecycle* rather than about the work. Those are
        the records that go to operators, get aggregated, and outlive the task.
        """
        secret = "SENTINEL-PROMPT-TEXT"
        row, _ = self.create(secret)
        row = self.settle(row.task_id)

        self.assertNotIn(secret, json.dumps(self.audit))

        lifecycle = {
            "task_created",
            "task_queued",
            "adapter_starting",
            "task_started",
            "waiting_for_user",
            "followup_received",
            "cancellation_requested",
            "task_cancelled",
            "task_completed",
            "task_failed",
            "task_interrupted",
            "action_rejected",
        }
        for event in self.store.events(row.task_id):
            if event.event_type in lifecycle:
                self.assertNotIn(secret, (event.text or "") + (event.detail or ""))

    def test_the_audit_hook_has_no_parameter_for_content(self):
        """49, 50, 51. A property of the signature, not of every caller."""
        import inspect

        parameters = set(inspect.signature(self.service._audit).parameters)
        self.assertEqual(
            parameters,
            {"operation", "result", "task_id", "adapter_id", "project_id", "correlation_id"},
        )

    def test_the_final_result_is_bounded(self):
        """30."""
        row, _ = self.create()
        row = self.settle(row.task_id)
        from cofferdam.workstation.tasks.models import MAX_RESULT_CHARS

        self.assertLessEqual(len(row.final_result or ""), MAX_RESULT_CHARS)

    def test_git_evidence_is_labelled_by_its_source(self):
        """54, 55. What Claude said, versus what Cofferdam looked at."""
        import subprocess

        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@example.com"],
            ["git", "config", "user.name", "T"],
        ):
            subprocess.run(command, cwd=self.project_root, check=True, shell=False)
        (self.project_root / "made.txt").write_text("x", encoding="utf-8")

        row, _ = self.create()
        row = self.settle(row.task_id)

        sources = set()
        for event in self.store.events(row.task_id):
            for reference in event.evidence:
                sources.add(reference.source)
        self.assertIn(EVIDENCE_GIT_OBSERVED, sources)
        # Nothing the adapter merely said was promoted to an observation.
        for event in self.store.events(row.task_id):
            for reference in event.evidence:
                if reference.source == EVIDENCE_GIT_OBSERVED:
                    self.assertIn(
                        reference.operation, ("rev-parse HEAD", "git status")
                    )


class FollowUp(ClaudeTaskTestCase):
    def test_a_follow_up_reaches_the_same_live_session(self):
        """31. Same process, same session id, second turn."""
        row, _ = self.create("first message")
        row = self.settle(row.task_id)
        self.assertEqual(row.state, STATE_WAITING_FOR_USER)

        run = self.adapter._runs[row.task_id]
        pid_before = run.pid
        session_before = run.session_id

        row = self.service.send_followup(row.task_id, "second message")
        row = self.settle(row.task_id)

        run_after = self.adapter._runs[row.task_id]
        self.assertEqual(run_after.pid, pid_before)
        self.assertEqual(run_after.session_id, session_before)
        self.assertIn("turn 2 done", row.final_result or "")
        self.assertIn("second message", row.final_result or "")

    def test_a_follow_up_records_an_event_without_its_text(self):
        """50. The history says one arrived and how long it was, not what it said."""
        row, _ = self.create()
        row = self.settle(row.task_id)
        self.service.send_followup(row.task_id, "SENTINEL-FOLLOWUP")
        row = self.settle(row.task_id)

        events = list(self.store.events(row.task_id))
        received = [e for e in events if e.event_type == "followup_received"]
        self.assertEqual(len(received), 1)
        self.assertNotIn("SENTINEL-FOLLOWUP", (received[0].text or ""))
        self.assertIn("characters", received[0].text or "")
        self.assertNotIn("SENTINEL-FOLLOWUP", json.dumps(self.audit))

    def test_idempotency_prevents_a_double_delivery(self):
        """33. A mobile retry does not send the answer twice."""
        row, _ = self.create()
        row = self.settle(row.task_id)

        self.service.send_followup(row.task_id, "only once", client_request_id="req-1")
        self.settle(row.task_id)
        self.service.send_followup(row.task_id, "only once", client_request_id="req-1")
        row = self.settle(row.task_id)

        run = self.adapter._runs[row.task_id]
        with run.lock:
            turns = run.state.turns
        self.assertEqual(turns, 2, "the follow-up was delivered twice")

    def test_a_follow_up_to_a_finished_task_is_refused(self):
        """34."""
        from cofferdam.workstation.tasks.errors import TaskAlreadyFinished

        row, _ = self.create()
        row = self.settle(row.task_id)
        row = self.service.cancel_task(row.task_id)
        self.assertEqual(row.state, STATE_CANCELLED)
        with self.assertRaises(TaskAlreadyFinished):
            self.service.send_followup(row.task_id, "too late")

    def test_a_client_cannot_name_a_session(self):
        """8, 32. There is no session parameter on the public path."""
        import inspect

        for method in (self.service.create_task, self.service.send_followup):
            self.assertNotIn("session", " ".join(inspect.signature(method).parameters))
        self.assertNotIn(
            "session", " ".join(inspect.signature(self.adapter.send_followup).parameters)
        )

    def test_a_follow_up_cannot_be_redirected_to_another_task(self):
        """32, 37. The run is found by the task's own id, and nothing else."""
        first, _ = self.create("task one")
        first = self.settle(first.task_id)
        # A second task cannot start while the first holds the slot, so finish
        # the first before creating the second.
        self.service.cancel_task(first.task_id)

        second, _ = self.create("task two")
        second = self.settle(second.task_id)

        self.service.send_followup(second.task_id, "for task two only")
        self.settle(second.task_id)

        # The first task's history gained nothing from the second's follow-up.
        first_after = self.service.get_task(first.task_id)
        self.assertEqual(first_after.state, STATE_CANCELLED)
        self.assertNotIn("for task two only", first_after.final_result or "")


class ConcurrencyAndIsolation(ClaudeTaskTestCase):
    def test_only_one_claude_task_runs_at_a_time(self):
        """36. The second is refused truthfully rather than queued silently."""
        first, _ = self.create("one")
        self.assertEqual(first.state, STATE_RUNNING)

        second, created = self.create("two")
        self.assertTrue(created)
        self.assertEqual(second.state, STATE_FAILED)
        self.assertIn("one at a time", (second.failure.message or "").lower())

    def test_a_double_tap_launches_one_process(self):
        """38. Same idempotency key, one task, one process."""
        first, created_first = self.create("do it", client_request_id="tap-1")
        second, created_second = self.create("do it", client_request_id="tap-1")

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.task_id, second.task_id)
        self.assertEqual(len(self.adapter.active_task_ids()), 1)

    def test_two_tasks_never_share_a_session_or_a_process(self):
        """37. Distinct pids, distinct process groups, distinct session ids."""
        first, _ = self.create("one")
        first_run = self.adapter._runs[first.task_id]
        first_pid, first_session = first_run.pid, first_run.session_id
        self.service.cancel_task(first.task_id)

        second, _ = self.create("two")
        second_run = self.adapter._runs[second.task_id]

        self.assertNotEqual(second_run.pid, first_pid)
        self.assertNotEqual(second_run.session_id, first_session)
        self.assertNotEqual(second_run.run_id, first_run.run_id)

    def test_a_failed_launch_frees_the_slot(self):
        adapter = ClaudeCodeAdapter(
            executable=Path("/nonexistent/claude"),
            auth_probe=always_authenticated,
        )
        from cofferdam.workstation.tasks.adapters.protocol import AdapterRefusal, TaskContext

        context = TaskContext(
            task_id="t-1",
            correlation_id="c-1",
            project_id=PROJECT_ID,
            project_root=self.project_root,
            adapter_id=ADAPTER_ID,
            prompt="x",
            state=STATE_RUNNING,
            lifecycle_revision=1,
        )
        with self.assertRaises(AdapterRefusal):
            adapter.start(context)
        self.assertEqual(adapter.active_task_ids(), ())

    def test_the_concurrency_limit_is_not_client_visible(self):
        """36. It is a number in source; no request carries one."""
        described = self.adapter.describe()
        self.assertEqual(described["max_concurrent_tasks"], 1)
        import inspect

        self.assertNotIn(
            "concurren", " ".join(inspect.signature(self.service.create_task).parameters)
        )


class CancellationThroughTaskCore(ClaudeTaskTestCase):
    behaviour = "hang"

    def test_cancel_reaches_cancelled_only_after_the_process_stopped(self):
        """28, 40, 44."""
        row, _ = self.create("work forever")
        self.assertEqual(row.state, STATE_RUNNING)
        run = self.adapter._runs[row.task_id]
        time.sleep(0.3)
        self.assertIsNone(run.poll())

        row = self.service.cancel_task(row.task_id)
        self.assertEqual(row.state, STATE_CANCELLED)
        self.assertIsNotNone(run.exit_code)
        self.assertEqual(self.adapter.active_task_ids(), ())

    def test_a_cancelled_task_cannot_later_be_completed(self):
        """44. Terminal is terminal; the graph refuses the reversal."""
        row, _ = self.create("work forever")
        row = self.service.cancel_task(row.task_id)
        self.assertEqual(row.state, STATE_CANCELLED)

        # A refresh after the fact must not resurrect it.
        again = self.service.refresh_task(row.task_id)
        self.assertEqual(again.state, STATE_CANCELLED)
        from cofferdam.workstation.tasks.errors import TaskAlreadyFinished

        with self.assertRaises(TaskAlreadyFinished):
            self.service.cancel_task(row.task_id)

    def test_cancellation_evidence_is_recorded(self):
        row, _ = self.create("work forever")
        row = self.service.cancel_task(row.task_id)
        found = False
        for event in self.store.events(row.task_id):
            for reference in event.evidence:
                if reference.operation == "stop":
                    found = True
                    self.assertEqual(reference.result, "stopped")
        self.assertTrue(found, "no cancellation evidence was recorded")


class NoFalseSuccess(ClaudeTaskTestCase):
    behaviour = "exit_zero_no_result"

    def test_exit_zero_without_a_result_is_a_failure(self):
        """28. The rule the whole milestone is named for."""
        row, _ = self.create()
        row = self.settle(row.task_id)
        self.assertEqual(row.state, STATE_FAILED)
        self.assertEqual(row.failure.code, "claude_no_result")


class StructuredErrorIsNotSuccess(ClaudeTaskTestCase):
    behaviour = "error_result"

    def test_a_structured_error_cannot_become_completed(self):
        """29."""
        row, _ = self.create()
        row = self.settle(row.task_id)
        self.assertEqual(row.state, STATE_FAILED)
        self.assertIn("error_during_execution", row.failure.code or "")


class EmptyResultIsNotSuccess(ClaudeTaskTestCase):
    behaviour = "empty_result"

    def test_a_result_with_no_text_is_not_a_completion(self):
        row, _ = self.create()
        row = self.settle(row.task_id)
        self.assertEqual(row.state, STATE_FAILED)
        self.assertEqual(row.failure.code, "claude_empty_result")


class ApprovalWaits(ClaudeTaskTestCase):
    behaviour = "denied"

    def test_a_refused_tool_becomes_a_truthful_approval_wait(self):
        """25, 26. Not auto-granted, not hidden, not called a completion."""
        row, _ = self.create()
        row = self.settle(row.task_id)
        self.assertEqual(row.state, STATE_WAITING_FOR_USER)
        self.assertEqual(row.waiting_reason, WAITING_APPROVAL)

    def test_approval_is_never_silently_granted(self):
        """26. The adapter does not claim the capability, and has no grant path."""
        self.assertFalse(self.adapter.capabilities().approvals)
        for path in package_sources():
            source = python_code_only(path.read_text("utf-8"))
            lowered = source.lower()
            # `approval` as a Task Core waiting-reason constant is expected and
            # is what a truthful wait is made of. What must not exist is a verb:
            # something this package could *call* to grant one.
            for forbidden in ("approve(", "auto_approve", "allow_tool", "grant("):
                self.assertNotIn(forbidden, lowered)
            self.assertNotIn("--allowedtools", lowered)
            self.assertNotIn("--permission-prompt-tool", lowered)

    def test_the_denied_tool_input_is_not_published(self):
        row, _ = self.create()
        row = self.settle(row.task_id)
        blob = json.dumps(
            [[event.text, event.detail] for event in self.store.events(row.task_id)]
            + [row.failure.message if row.failure else None, row.final_result]
        )
        self.assertNotIn("rm -rf", blob)


class AuthenticationWaits(ClaudeTaskTestCase):
    auth = staticmethod(never_authenticated)

    def test_unauthenticated_becomes_waiting_for_user_authentication(self):
        """23."""
        row, _ = self.create()
        self.assertEqual(row.state, STATE_WAITING_FOR_USER)
        self.assertEqual(row.waiting_reason, WAITING_AUTHENTICATION)

    def test_no_process_was_started(self):
        row, _ = self.create()
        self.assertEqual(self.adapter.active_task_ids(), ())
        self.assertIsNone(self.fake.launch_record())

    def test_no_credential_is_stored_on_the_task(self):
        """24."""
        row, _ = self.create()
        blob = json.dumps(
            [row.failure.message if row.failure else None, row.final_result]
            + [[event.text, event.detail] for event in self.store.events(row.task_id)]
        )
        # The account identifiers the probe's own JSON carries. None of them is
        # read into `AuthStatus`, so none of them can reach here.
        for forbidden in ("@example.com", "orgid", "sk-", "bearer", "loggedin"):
            self.assertNotIn(forbidden, blob.lower())
        # And nothing that looks like a stored secret, as opposed to the
        # warning sentence that tells somebody never to type one.
        self.assertNotIn("password:", blob.lower())
        self.assertNotIn("password=", blob.lower())

    def test_the_message_directs_the_person_to_the_workstation(self):
        """A waiting task has no `failure` — it did not fail. The sentence a
        person needs is on the task's activity line and in its history."""
        row, _ = self.create()
        self.assertIsNone(row.failure)
        text = " ".join(
            [row.latest_activity or ""]
            + [event.text or "" for event in self.store.events(row.task_id)]
        ).lower()
        self.assertIn("workstation", text)
        self.assertIn("sign in", text)
        self.assertIn("never type a password", text)


class RestartBehaviour(ClaudeTaskTestCase):
    behaviour = "hang"

    def test_a_running_task_becomes_interrupted_and_is_not_resumed(self):
        """45, 46."""
        row, _ = self.create("work forever")
        self.assertEqual(row.state, STATE_RUNNING)

        self.assertFalse(self.adapter.capabilities().recover_after_restart)
        settled = self.service.recover_after_restart()
        self.assertEqual(len(settled), 1)
        self.assertEqual(settled[0].state, STATE_INTERRUPTED)

    def test_terminal_task_history_survives_a_restart(self):
        """48."""
        row, _ = self.create("work forever")
        row = self.service.cancel_task(row.task_id)
        self.assertEqual(row.state, STATE_CANCELLED)
        before = len(self.store.events(row.task_id))

        self.service.recover_after_restart()
        after = self.service.get_task(row.task_id)
        self.assertEqual(after.state, STATE_CANCELLED)
        self.assertEqual(len(self.store.events(after.task_id)), before)

    def test_the_adapter_stops_its_children_on_shutdown(self):
        """47. No uncontrolled process is left behind by a stop."""
        row, _ = self.create("work forever")
        run = self.adapter._runs[row.task_id]
        self.assertIsNone(run.poll())

        self.adapter.shutdown()
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and run.poll() is None:
            time.sleep(0.05)
        self.assertIsNotNone(run.poll(), "a Claude process survived shutdown")

    def test_recovery_is_not_claimed_as_a_capability(self):
        """46."""
        self.assertFalse(self.adapter.capabilities().recover_after_restart)
        from cofferdam.workstation.tasks.adapters.protocol import AdapterRefusal

        described = self.adapter.describe()
        self.assertFalse(described["capabilities"]["recover_after_restart"])


class ProjectAuthority(ClaudeTaskTestCase):
    def test_an_unknown_project_is_refused(self):
        """3."""
        from cofferdam.workstation.tasks.errors import ProjectUnknown

        with self.assertRaises(ProjectUnknown):
            self.service.create_task(
                project_id="not-a-project", adapter_id=ADAPTER_ID, prompt="x"
            )

    def test_a_project_that_does_not_permit_the_adapter_is_refused(self):
        """3."""
        from cofferdam.workstation.tasks.errors import AdapterNotPermitted

        self.write_projects(
            [
                {
                    "project_id": PROJECT_ID,
                    "display_name": "Demo",
                    "root": str(self.project_root),
                    "adapters": [],
                }
            ]
        )
        self.service.reload_projects()
        with self.assertRaises(AdapterNotPermitted):
            self.create()

    def test_a_root_replaced_by_a_symlink_is_refused(self):
        """The re-check happens immediately before the launch."""
        from cofferdam.workstation.tasks.errors import ProjectRootInvalid

        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir()
        import shutil

        shutil.rmtree(self.project_root)
        self.project_root.symlink_to(elsewhere)
        with self.assertRaises(ProjectRootInvalid):
            self.create()

    def test_no_request_field_carries_a_path(self):
        """6. The creation signature has no cwd, root or path parameter."""
        import inspect

        parameters = " ".join(inspect.signature(self.service.create_task).parameters)
        for forbidden in ("cwd", "root", "path", "directory", "folder"):
            self.assertNotIn(forbidden, parameters)


class ApiSurface(ClaudeTaskTestCase):
    def test_the_creation_route_accepts_five_named_fields(self):
        """4-11, 59. The client vocabulary, read off the route's allowlist.

        The route validates against an explicit ``allowed={...}`` set rather
        than a permissive model, so this is the complete list of keys a request
        body may contain. Anything else is rejected before it reaches the
        service.
        """
        source = (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text("utf-8")
        block = source[source.index('@app.post("/api/tasks"') :][:1600]
        allowed = re.search(r"allowed=\{([^}]*)\}", block)
        self.assertIsNotNone(allowed, "the create route has no field allowlist")
        fields = set(re.findall(r'"([a-z_]+)"', allowed.group(1)))
        self.assertEqual(
            fields,
            {"project_id", "adapter_id", "prompt", "client_request_id", "title"},
        )
        for forbidden in (
            "command",
            "argv",
            "executable",
            "cwd",
            "env",
            "environment",
            "session_id",
            "permission_mode",
            "allowed_tools",
            "mcp",
            "model",
            "flags",
            "pid",
            "unit",
            "webhook",
            "callback",
        ):
            self.assertNotIn(forbidden, fields, "the create route accepts " + forbidden)

    def test_an_unknown_field_is_refused_rather_than_ignored(self):
        """4-11. Extra keys are a refusal, so a smuggled field cannot be silent."""
        source = (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text("utf-8")
        block = source[source.index("async def _task_body") :][:2000]
        self.assertIn("allowed", block)
        self.assertTrue(
            "unexpected" in block.lower() or "unknown" in block.lower(),
            "extra request fields are not refused by name",
        )

    def test_the_adapter_description_reaches_a_client_with_its_limits(self):
        """58."""
        described = self.adapter.describe()
        self.assertTrue(described["available"])
        self.assertTrue(described["limitations"])
        self.assertIn("no shell", " ".join(described["limitations"]).lower())
        # And it publishes no path.
        self.assertNotIn(str(self.fake.path), json.dumps(described))


# ===========================================================================
# 8. The PWA
# ===========================================================================


class Pwa(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (REPO_ROOT / "web" / "tasks.js").read_text("utf-8")
        cls.styles = (REPO_ROOT / "web" / "styles.css").read_text("utf-8")

    def test_no_command_or_path_field_exists(self):
        """59."""
        for forbidden in (
            "taskCommand",
            "taskCwd",
            "taskEnv",
            "taskArgv",
            "taskExecutable",
            "taskFlags",
            "taskSessionId",
            "taskPermissionMode",
        ):
            self.assertNotIn(forbidden, self.source)

    def test_the_panel_names_no_specific_agent(self):
        """58. Claude Code appears because the backend listed it, not because
        the panel knows the word."""
        lowered = re.sub(r"/\*.*?\*/", "", self.source, flags=re.S).lower()
        for forbidden in ("claude", "anthropic"):
            self.assertIsNone(
                re.search(r"\b" + forbidden + r"\b", lowered),
                "the panel hard-codes " + forbidden,
            )

    def test_no_raw_terminal_is_rendered(self):
        """60. No terminal emulator, and no escape-sequence renderer.

        ``<pre class="task-text">`` is not a terminal — it is a wrapped block
        for a prompt or a result, and the text inside it went through the
        parser's sanitiser and then the panel's escaper. What would make this a
        terminal is a library that turns escape sequences back into styling, or
        a byte stream to feed one, and neither exists.
        """
        lowered = self.source.lower()
        for forbidden in ("xterm", "ansi_up", "ansi-to-html", "ansitohtml", "vt100"):
            self.assertNotIn(forbidden, lowered)
        # Every preformatted block is the wrapped task-text class, never a raw
        # stream container.
        for match in re.findall(r"<pre[^>]*", self.source):
            self.assertIn('class="task-text"', match)
        # And nothing streams raw process output to it.
        self.assertNotIn("stdout", lowered)
        self.assertNotIn("raw_output", lowered)

    def test_no_task_value_is_interpolated_without_the_escaper(self):
        """22. The panel builds HTML, so every value must pass through esc().

        `innerHTML` is how this panel renders — that is the design, not a
        finding. What matters is that no *value* reaches it unescaped, so the
        assertion is on the interpolations rather than on the assignment.
        """
        self.assertNotIn("dangerouslySet", self.source)
        self.assertNotIn("document.write", self.source)
        self.assertNotIn("insertAdjacentHTML", self.source)
        # Only the panel host is assigned, and only from functions in this file.
        assignments = re.findall(r"(\w+)\.innerHTML\s*=", self.source)
        self.assertEqual(set(assignments), {"host"})

        # Every `+ <expr> +` inside a string concatenation that references a
        # task or adapter field goes through esc(...).
        for match in re.finditer(r'"\s*\+\s*([a-zA-Z_][\w.\[\]()]*)\s*\+\s*"', self.source):
            expression = match.group(1)
            if expression.startswith(("esc(", "String(")):
                continue
            self.assertNotRegex(
                expression,
                r"^(task|adapter|item|event|reference)\.",
                "unescaped interpolation of " + expression,
            )

    def test_the_authentication_wait_offers_no_text_field(self):
        """63. The property this milestone added to the panel."""
        self.assertIn("SECRET_WAITING_REASONS", self.source)
        self.assertIn("authentication", self.source)
        block = re.search(
            r"function detailActions\(task\) \{.*?\n  \}", self.source, re.S
        )
        self.assertIsNotNone(block)
        body = block.group(0)
        # The secret branch comes first and the textarea is in the `else`.
        secret_at = body.index("waitingForSecret(task)")
        textarea_at = body.index("taskFollowupText")
        self.assertLess(secret_at, textarea_at)
        self.assertIn("else if", body)
        self.assertIn("never type a password", body.lower())

    def test_follow_up_and_cancel_are_capability_driven(self):
        """61, 62."""
        self.assertIn('capabilityOf(task, "followup")', self.source)
        self.assertIn('capabilityOf(task, "cancel")', self.source)
        self.assertIn("!task.terminal && capabilityOf(task, \"cancel\")", self.source)

    def test_evidence_is_labelled_by_source_in_the_panel(self):
        """54."""
        self.assertIn("(observed)", self.source)
        self.assertIn("(adapter says)", self.source)

    def test_no_task_content_reaches_the_console(self):
        """The panel has no console call at all."""
        self.assertNotIn("console.", self.source)

    def test_new_blocks_cannot_widen_the_page(self):
        """66. Every new class carries the wrap rules the panel uses."""
        for block in (".task-limitations", ".task-secret-wait"):
            self.assertIn(block, self.styles)
        limitations = self.styles[self.styles.index(".task-limitations") :][:400]
        self.assertIn("min-width: 0", limitations)
        self.assertIn("overflow-wrap: anywhere", limitations)

    def test_duplicate_actions_are_guarded(self):
        """65."""
        self.assertIn("busy(", self.source)
        self.assertIn("locked()", self.source)


# ===========================================================================
# 9. The layer boundary
# ===========================================================================


class LayerBoundary(unittest.TestCase):
    def test_task_core_imports_nothing_from_this_package(self):
        """The architecture rule, asserted rather than trusted."""
        tasks = REPO_ROOT / "cofferdam" / "workstation" / "tasks"
        for path in sorted(tasks.rglob("*.py")):
            if "claude_code" in path.parts or path.parent.name == "adapters":
                continue
            source = python_code_only(path.read_text("utf-8"))
            self.assertNotIn("claude", source.lower(), str(path) + " names claude")

    def test_only_the_registry_constructs_the_adapter(self):
        tasks = REPO_ROOT / "cofferdam" / "workstation" / "tasks"
        for path in sorted(tasks.rglob("*.py")):
            if "claude_code" in path.parts:
                continue
            source = python_code_only(path.read_text("utf-8"))
            if "ClaudeCodeAdapter" in source:
                self.assertEqual(
                    path.name, "__init__.py", str(path) + " constructs the adapter"
                )

    def test_the_package_imports_no_task_core_service(self):
        """The dependency points one way."""
        for path in package_sources():
            source = python_code_only(path.read_text("utf-8"))
            self.assertNotIn("from ...service", source)
            self.assertNotIn("from ...store", source)
            self.assertNotIn("TaskService", source)

    def test_the_verified_cli_version_is_recorded(self):
        self.assertEqual(cli.VERIFIED_CLI_VERSION, "2.1.221")
        guide = (REPO_ROOT / "docs" / "CLAUDE_CODE_ADAPTER.md").read_text("utf-8")
        self.assertIn(cli.VERIFIED_CLI_VERSION, guide)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
