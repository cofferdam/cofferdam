"""The Claude Agent SDK adapter: dependency boundary, policy, and behaviour.

Standard library only, and every test in this file runs on a machine where the
Agent SDK is **not installed**. That is not a compromise — it is the property
under test. The adapter is an optional extra, and "optional" has to mean that
ordinary Cofferdam works, imports, starts and is fully tested without it.

What is proven here: the dependency is lazy and its absence is reported
precisely; the provider configuration is fixed in source and cannot be
influenced by a caller; ``bypassPermissions`` cannot appear; SDK messages
normalize into bounded provider-neutral events with no payload surviving;
ordering, duplication and finality behave; cancellation reaches one session and
only that one; results are durable and truthful; and registration is opt-in
without displacing the Claude Code adapter.

What is **not** proven here, stated plainly because a test suite that implies
more than it checked is worse than a smaller one: that the real
:class:`SdkSession` drives a real SDK correctly. No test in this repository
calls Anthropic, consumes model usage, requires a login, touches the network or
starts a subprocess. The message doubles are shape-accurate against the
published distribution — see ``tests/_agent_sdk_doubles.py`` — which is what
makes the normalizer's coverage meaningful, but the transport itself is
evidenced by the published source and, if it is ever run, by a supervised live
spike recorded in a pull request.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from ._agent_sdk_doubles import (
    AssistantMessage,
    FakeClaudeAgentOptions,
    FakeSdkModule,
    FakeSession,
    ResultMessage,
    SomethingFromANewerSdk,
    StreamEvent,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskUpdatedMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from ._task_doubles import PROJECT_ID, TURKISH_PROMPT, TaskTestCase, python_code_only

from cofferdam.workstation.config import load_config
from cofferdam.workstation.tasks.adapters import (
    AdapterRegistry,
    DuplicateAdapterId,
    build_registry,
)
from cofferdam.workstation.tasks.adapters.claude_agent_sdk import (
    ADAPTER_ID,
    ClaudeAgentSdkAdapter,
)
from cofferdam.workstation.tasks.adapters.claude_agent_sdk import (
    normalize,
    options as option_policy,
    sdk as sdk_boundary,
    session as session_module,
)
from cofferdam.workstation.tasks.adapters.protocol import AdapterRefusal, TaskContext
from cofferdam.workstation.tasks.delegated import (
    KIND_ACTIVITY,
    KIND_CANCELLED,
    KIND_CLARIFICATION_REQUESTED,
    KIND_OUTPUT,
    KIND_PROVIDER_FAILED,
    KIND_SESSION_STARTED,
    KIND_SUCCEEDED,
    KIND_TOOL_APPROVAL_REQUESTED,
    KIND_TOOL_FINISHED,
    KIND_TOOL_STARTED,
    build_event,
)
from cofferdam.workstation.tasks.models import (
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (
    REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "adapters" / "claude_agent_sdk"
)
NOW = "2026-08-09T00:00:00Z"


def event(kind: str, sequence: int = 1, **fields):
    return build_event(
        kind=kind,
        provider=normalize.PROVIDER,
        provider_sequence=sequence,
        observed_at=NOW,
        **fields,
    )


def context(root: Path, task_id: str = "tsk_1", prompt: str = TURKISH_PROMPT) -> TaskContext:
    return TaskContext(
        task_id=task_id,
        correlation_id="cor_1",
        project_id=PROJECT_ID,
        project_root=root,
        adapter_id=ADAPTER_ID,
        prompt=prompt,
        state="running",
        lifecycle_revision=1,
    )


# -- 1. the optional dependency ----------------------------------------------


def _sdk_is_installed() -> bool:
    """Whether the ``agent-sdk`` extra is present in *this* interpreter.

    Answered with ``importlib.util.find_spec``, which locates the distribution
    without importing it — importing it here would pollute ``sys.modules`` and
    quietly invalidate the laziness test below.
    """
    import importlib.util

    try:
        return importlib.util.find_spec("claude_agent_sdk") is not None
    except (ImportError, ValueError):  # pragma: no cover - broken installation
        return False


SDK_INSTALLED = _sdk_is_installed()


class OptionalDependencyTests(unittest.TestCase):
    """Both halves: the absence path and the presence path.

    The suite runs on two machines — the stdlib-only CI job without the extra,
    and a workstation with it — and both are real deployments. So the tests
    branch on :data:`SDK_INSTALLED` rather than skipping, because a test that
    skipped on the machine where the adapter actually runs would be checking the
    boundary only where it cannot break.
    """

    def test_importing_cofferdam_and_registering_does_not_import_the_sdk(self) -> None:
        """Two questions, because they have different right answers.

        **Importing Cofferdam and building the registry must not import the
        SDK.** That is the property the extra exists for: a workstation that
        never enables the adapter, and every ordinary start of one that does,
        pays nothing for a 91 MB dependency.

        **Describing the adapter may.** ``describe()`` reports ``available``, and
        the only honest way to answer "can this be loaded" is to try — so an
        import there is the feature, not a leak. This test asserts the boundary
        is exactly where it is claimed to be rather than one step earlier, and it
        would fail just as loudly if construction started importing.

        Checked in a fresh interpreter because ``sys.modules`` is shared: an
        in-process assertion would pass or fail depending on what some earlier
        test had already loaded, which is the kind of order-dependent check that
        eventually gets deleted rather than fixed.

        Deliberately **not** importing ``cofferdam.workstation.service``: that
        pulls in FastAPI, absent on the stdlib-only CI job, and a check that only
        ran where the extras happen to be installed would be missing from the one
        place it is cheapest to break.
        """
        import subprocess
        import sys

        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys;"
                "import cofferdam.workstation.__main__;"
                "import cofferdam.workstation.tasks.adapters as a;"
                "registry = a.build_registry(enable_claude_agent_sdk_adapter=True);"
                "print('after_registry', 'claude_agent_sdk' in sys.modules);"
                "registry.describe();"
                "print('after_describe', 'claude_agent_sdk' in sys.modules)",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
        lines = dict(line.split() for line in completed.stdout.strip().splitlines())
        self.assertEqual(lines["after_registry"], "False")
        # True only where the SDK is actually installed; a failed import leaves
        # nothing behind, so an absent SDK reads False here too.
        self.assertEqual(lines["after_describe"], str(SDK_INSTALLED))

    def test_no_module_in_the_package_imports_the_sdk_at_module_scope(self) -> None:
        """Structural. A convenient top-level import added later fails here.

        Parsed from the real source rather than the comment-stripped form: an
        import is a syntax node, so the prose in ``sdk.py`` explaining that it
        does not import at module scope cannot trip this check anyway.
        """
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:  # module scope only
                with self.subTest(path=path.name):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertNotEqual(alias.name.split(".")[0], "claude_agent_sdk")
                    if isinstance(node, ast.ImportFrom):
                        self.assertNotEqual(
                            (node.module or "").split(".")[0], "claude_agent_sdk"
                        )

    def test_the_missing_dependency_message_names_the_install_command(self) -> None:
        """The constants, which say the same thing on both machines."""
        self.assertEqual(sdk_boundary.DISTRIBUTION_NAME, "claude-agent-sdk")
        self.assertEqual(sdk_boundary.IMPORT_NAME, "claude_agent_sdk")
        self.assertIn(sdk_boundary.EXTRA_NAME, sdk_boundary.MISSING_MESSAGE)
        self.assertIn("pip install", sdk_boundary.MISSING_MESSAGE)

    def test_the_adapter_describes_its_availability_truthfully(self) -> None:
        described = ClaudeAgentSdkAdapter().describe()
        self.assertEqual(described["available"], SDK_INSTALLED)
        if SDK_INSTALLED:
            self.assertIsNone(described["unavailable_reason"])
            self.assertEqual(
                described["sdk_version"], sdk_boundary.installed_version()
            )
        else:
            self.assertIn(sdk_boundary.EXTRA_NAME, described["unavailable_reason"])

    def test_enabling_the_adapter_does_not_crash(self) -> None:
        registry = build_registry(enable_claude_agent_sdk_adapter=True)
        self.assertEqual(registry.ids(), (ADAPTER_ID,))

    def test_resolving_the_adapter_matches_its_availability(self) -> None:
        """Unavailable is a refusal, not an entry that fails when pressed."""
        from cofferdam.workstation.tasks.errors import AdapterUnknown

        registry = build_registry(enable_claude_agent_sdk_adapter=True)
        if SDK_INSTALLED:
            self.assertEqual(registry.get(ADAPTER_ID).adapter_id, ADAPTER_ID)
        else:
            with self.assertRaises(AdapterUnknown):
                registry.get(ADAPTER_ID)

    def test_the_loader_refuses_an_incompatible_version_by_name(self) -> None:
        """Installed, but missing something this adapter drives."""
        import sys

        self._install_double(FakeSdkModule(omit=("ClaudeSDKClient",)))
        with self.assertRaises(sdk_boundary.AgentSdkUnavailable) as caught:
            sdk_boundary.load()
        self.assertIn("ClaudeSDKClient", caught.exception.message)

    def test_the_loader_accepts_a_module_providing_everything_it_names(self) -> None:
        self._install_double(FakeSdkModule())
        loaded = sdk_boundary.load()
        self.assertEqual(loaded.version, "0.2.134")

    def _install_double(self, module) -> None:
        """Put a double in ``sys.modules`` and put back whatever was there.

        Restored rather than popped, because on a machine with the extra
        installed the real module may already be loaded and popping it would
        leave the rest of the run in a state this test invented.
        """
        import sys

        previous = sys.modules.get("claude_agent_sdk", None)

        def restore() -> None:
            if previous is None:
                sys.modules.pop("claude_agent_sdk", None)
            else:
                sys.modules["claude_agent_sdk"] = previous

        self.addCleanup(restore)
        sys.modules["claude_agent_sdk"] = module

    def test_an_old_interpreter_gets_its_own_message(self) -> None:
        """Not "install it": on 3.9 no install would help."""
        original = sdk_boundary.python_supports_sdk
        sdk_boundary.python_supports_sdk = lambda: False
        self.addCleanup(setattr, sdk_boundary, "python_supports_sdk", original)
        with self.assertRaises(sdk_boundary.AgentSdkUnavailable) as caught:
            sdk_boundary.load()
        self.assertIn("Python", caught.exception.message)
        self.assertNotIn("pip install", caught.exception.message)


class PackagingTests(unittest.TestCase):
    def test_the_extra_declares_the_verified_distribution_with_a_marker(self) -> None:
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("agent-sdk = [", text)
        requirement = re.search(r'"(claude-agent-sdk[^"]*)"', text)
        self.assertIsNotNone(requirement, "the extra must name the distribution")
        line = requirement.group(1)
        self.assertIn(sdk_boundary.VERIFIED_SDK_VERSION, line)
        # Cofferdam supports 3.9 and the SDK does not, so the marker is not
        # cosmetic: without it a 3.9 install fails at resolution.
        self.assertIn("python_version >= '3.10'", line)
        # Pre-1.0 and self-declared alpha: an unbounded requirement would let a
        # minor bump change the API this adapter was written against.
        self.assertIn("<0.3", line)

    def test_the_new_package_is_declared_so_a_wheel_contains_it(self) -> None:
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(
            '"cofferdam.workstation.tasks.adapters.claude_agent_sdk"', text
        )

    def test_the_workstation_extra_did_not_gain_the_sdk(self) -> None:
        """The stdlib-only and workstation CI jobs must stay meaningful."""
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        workstation = text[text.index("workstation = [") : text.index("# The Claude Agent SDK")]
        self.assertNotIn("claude-agent-sdk", workstation)


# -- 2. project and configuration safety -------------------------------------


class OptionPolicyTests(unittest.TestCase):
    def build(self, root: Path = Path("/srv/project"), **kwargs):
        return option_policy.build_option_values(
            project_root=root,
            session_id=option_policy.new_session_id(),
            inherited_environment={},
            **kwargs,
        )

    def test_bypass_permissions_can_never_appear(self) -> None:
        values = self.build()
        self.assertEqual(values["permission_mode"], "acceptEdits")
        self.assertNotIn("bypassPermissions", repr(values))
        values["permission_mode"] = "bypassPermissions"
        with self.assertRaises(option_policy.OptionPolicyError):
            option_policy.verify_option_values(values)

    def test_every_forbidden_permission_mode_is_refused(self) -> None:
        for mode in option_policy.FORBIDDEN_PERMISSION_MODES:
            with self.subTest(mode=mode):
                values = self.build()
                values["permission_mode"] = mode
                with self.assertRaises(option_policy.OptionPolicyError):
                    option_policy.verify_option_values(values)

    def test_the_profile_has_no_shell(self) -> None:
        values = self.build()
        self.assertEqual(tuple(values["tools"]), option_policy.PROFILE_TOOLS)
        self.assertNotIn("Bash", values["tools"])
        self.assertIn("Bash", values["disallowed_tools"])

    def test_no_tool_is_auto_approved(self) -> None:
        values = self.build()
        self.assertEqual(values["allowed_tools"], [])
        values["allowed_tools"] = ["Read"]
        with self.assertRaises(option_policy.OptionPolicyError):
            option_policy.verify_option_values(values)

    def test_no_mcp_server_settings_file_or_extra_argument_is_passed(self) -> None:
        values = self.build()
        self.assertEqual(values["mcp_servers"], {})
        self.assertTrue(values["strict_mcp_config"])
        self.assertEqual(values["setting_sources"], [])
        self.assertIsNone(values["settings"])
        self.assertEqual(values["add_dirs"], [])
        self.assertEqual(values["extra_args"], {})
        for field, bad in (
            ("mcp_servers", {"evil": {}}),
            ("strict_mcp_config", False),
            ("setting_sources", ["user"]),
            ("settings", "/tmp/settings.json"),
            ("add_dirs", ["/"]),
            ("extra_args", {"dangerously-skip-permissions": None}),
        ):
            with self.subTest(field=field):
                broken = self.build()
                broken[field] = bad
                with self.assertRaises(option_policy.OptionPolicyError):
                    option_policy.verify_option_values(broken)

    def test_no_hook_subagent_or_plugin_is_configured(self) -> None:
        values = self.build()
        self.assertIsNone(values["hooks"])
        self.assertIsNone(values["agents"])
        self.assertEqual(values["plugins"], [])
        self.assertEqual(values["skills"], [])

    def test_no_model_or_effort_is_pinned_and_none_can_be_supplied(self) -> None:
        values = self.build()
        self.assertIsNone(values["model"])
        self.assertIsNone(values["effort"])
        self.assertIsNone(values["system_prompt"])

    def test_the_working_directory_is_the_server_resolved_project_root(self) -> None:
        values = self.build(Path("/srv/approved"))
        self.assertEqual(values["cwd"], "/srv/approved")

    def test_a_caller_cannot_supply_any_of_the_forbidden_options(self) -> None:
        """Structural: ``build_option_values`` has no parameter for them.

        The signature is the boundary, so the signature is what is asserted —
        the only inputs are a project root the server resolved, a session id this
        process minted, a CLI path found by a fixed search, and a mapping used
        for testing.
        """
        import inspect

        signature = inspect.signature(option_policy.build_option_values)
        self.assertEqual(
            sorted(signature.parameters),
            ["cli_path", "inherited_environment", "project_root", "session_id"],
        )
        for forbidden in option_policy.CALLER_FORBIDDEN_OPTIONS:
            if forbidden in option_policy.HOST_SUPPLIED_OPTIONS:
                # Named exception: the CLI path comes from the fixed host
                # search, never from a request. See the constant's own note.
                continue
            with self.subTest(option=forbidden):
                self.assertNotIn(forbidden, signature.parameters)
        self.assertEqual(option_policy.HOST_SUPPLIED_OPTIONS, ("cli_path",))

    def test_the_session_id_is_generated_and_unique(self) -> None:
        first = option_policy.new_session_id()
        self.assertNotEqual(first, option_policy.new_session_id())
        self.assertRegex(first, r"\A[0-9a-f-]{36}\Z")

    def test_the_environment_override_is_small_and_carries_no_secret(self) -> None:
        environment = option_policy.build_environment({"HOME": "/home/x"})
        self.assertEqual(set(environment), set(option_policy.ENVIRONMENT_FORCED))
        blob = repr(environment).lower()
        for word in ("token", "secret", "key", "password"):
            self.assertNotIn(word, blob)

    def test_cofferdam_own_secret_names_are_blanked_in_the_child(self) -> None:
        environment = option_policy.build_environment(
            {"COFFERDAM_TOKEN": "a-real-looking-token"}
        )
        self.assertEqual(environment["COFFERDAM_TOKEN"], "")
        self.assertNotIn("a-real-looking-token", repr(environment))

    def test_an_unexpected_environment_override_is_refused(self) -> None:
        values = self.build()
        values["env"] = {"LD_PRELOAD": "/tmp/evil.so"}
        with self.assertRaises(option_policy.OptionPolicyError):
            option_policy.verify_option_values(values)

    def test_the_built_options_use_only_names_the_sdk_defines(self) -> None:
        """Every key is a real ``ClaudeAgentOptions`` field.

        The double refuses an unknown keyword, so a name invented here — or one
        removed from a future SDK and left behind — fails rather than being
        silently swallowed by ``**kwargs``.
        """
        module = FakeSdkModule()
        built = option_policy.build_options(module, self.build(Path("/srv/p")))
        self.assertIsInstance(built, FakeClaudeAgentOptions)
        self.assertEqual(built.permission_mode, "acceptEdits")
        self.assertEqual(built.cwd, "/srv/p")

    def test_a_cli_path_is_passed_when_the_host_has_one(self) -> None:
        values = self.build(cli_path=Path("/home/x/.local/bin/claude"))
        self.assertEqual(values["cli_path"], "/home/x/.local/bin/claude")

    def test_the_tool_profile_matches_the_claude_code_adapter(self) -> None:
        """Two transports, one policy. A difference here would mean switching
        transport quietly changed what the agent may do."""
        from cofferdam.workstation.tasks.adapters.claude_code import cli

        self.assertEqual(option_policy.PROFILE_TOOLS, cli.PROFILE_TOOLS)
        self.assertEqual(
            option_policy.PROFILE_PERMISSION_MODE, cli.PROFILE_PERMISSION_MODE
        )


class SourceGuardTests(unittest.TestCase):
    def test_the_package_never_uses_a_shell_or_spawns_a_process(self) -> None:
        """Checked against imported module *names*, not substrings.

        Substring matching would flag ``empty_result`` for containing "pty",
        which is the kind of false positive that gets a guard deleted rather
        than fixed.
        """
        forbidden = {"subprocess", "pty", "shlex", "commands", "popen2"}
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            raw = path.read_text(encoding="utf-8")
            source = python_code_only(raw)
            tree = ast.parse(raw)
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.add((node.module or "").split(".")[0])
            with self.subTest(path=path.name):
                self.assertEqual(imported & forbidden, set())
                self.assertNotIn("shell=True", source)
                self.assertNotIn("os.system", source)

    def test_bypass_permissions_appears_only_where_it_is_forbidden(self) -> None:
        """The string may exist — in the forbidden list — and nowhere else."""
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            source = python_code_only(path.read_text(encoding="utf-8"))
            if "bypassPermissions" not in source:
                continue
            with self.subTest(path=path.name):
                self.assertEqual(path.name, "options.py")
                index = source.index("FORBIDDEN_PERMISSION_MODES")
                self.assertLess(index, source.index("bypassPermissions"))


# -- 3. normalization --------------------------------------------------------


class NormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.normalizer = normalize.MessageNormalizer()

    def kinds(self, message):
        return [event.kind for event in self.normalizer.normalize(message)]

    def test_a_session_init_becomes_a_session_start(self) -> None:
        events = self.normalizer.normalize(SystemMessage(subtype="init"))
        self.assertEqual([e.kind for e in events], [KIND_SESSION_STARTED])
        self.assertEqual(events[0].provider, "claude-agent-sdk")

    def test_the_provider_session_id_is_learned_and_preserved(self) -> None:
        self.normalizer.normalize(SystemMessage(subtype="init", session_id="sess-1"))
        self.assertEqual(self.normalizer.provider_session_id, "sess-1")
        events = self.normalizer.normalize(
            AssistantMessage(content=[TextBlock(text="hi")], session_id="sess-1")
        )
        self.assertEqual(events[0].provider_session_id, "sess-1")

    def test_a_second_different_session_id_does_not_overwrite_the_first(self) -> None:
        self.normalizer.normalize(SystemMessage(subtype="init", session_id="sess-1"))
        self.normalizer.normalize(SystemMessage(subtype="init", session_id="sess-2"))
        self.assertEqual(self.normalizer.provider_session_id, "sess-1")

    def test_assistant_text_becomes_bounded_output(self) -> None:
        events = self.normalizer.normalize(
            AssistantMessage(content=[TextBlock(text="x" * 100000)])
        )
        self.assertEqual([e.kind for e in events], [KIND_OUTPUT])
        self.assertLessEqual(len(events[0].text), 4001)

    def test_thinking_blocks_produce_nothing_at_all(self) -> None:
        events = self.normalizer.normalize(
            AssistantMessage(content=[ThinkingBlock(thinking="private reasoning")])
        )
        self.assertEqual(events, [])

    def test_a_tool_use_becomes_tool_activity_naming_the_tool(self) -> None:
        events = self.normalizer.normalize(
            AssistantMessage(
                content=[ToolUseBlock(id="t1", name="Read", input={"file_path": "/etc/passwd"})]
            )
        )
        self.assertEqual([e.kind for e in events], [KIND_TOOL_STARTED])
        self.assertEqual(events[0].tool_name, "Read")
        # The tool's *input* is the material that must not travel.
        self.assertNotIn("passwd", str(events[0].to_dict()))

    def test_a_tool_name_that_is_not_a_name_is_dropped_not_sanitised(self) -> None:
        events = self.normalizer.normalize(
            AssistantMessage(content=[ToolUseBlock(id="t1", name="<img onerror=x>")])
        )
        self.assertIsNone(events[0].tool_name)
        self.assertEqual(events[0].text, "Claude used a tool.")

    def test_a_tool_result_reports_its_shape_and_not_its_body(self) -> None:
        events = self.normalizer.normalize(
            UserMessage(
                content=[
                    ToolResultBlock(
                        tool_use_id="t1", content="root:x:0:0:...", is_error=False
                    )
                ]
            )
        )
        self.assertEqual([e.kind for e in events], [KIND_TOOL_FINISHED])
        self.assertNotIn("root:x", str(events[0].to_dict()))

    def test_a_failed_tool_result_says_so(self) -> None:
        events = self.normalizer.normalize(
            UserMessage(content=[ToolResultBlock(tool_use_id="t1", is_error=True)])
        )
        self.assertEqual(events[0].detail, "error")

    def test_a_success_result_becomes_a_terminal_success(self) -> None:
        events = self.normalizer.normalize(ResultMessage(result="the answer"))
        self.assertEqual([e.kind for e in events], [KIND_SUCCEEDED])
        self.assertEqual(events[0].result, "the answer")
        self.assertTrue(events[0].terminal)

    def test_an_error_result_becomes_a_bounded_provider_failure(self) -> None:
        events = self.normalizer.normalize(
            ResultMessage(is_error=True, subtype="error_max_turns", result="ran out")
        )
        self.assertEqual([e.kind for e in events], [KIND_PROVIDER_FAILED])
        self.assertEqual(events[0].failure_code, "claude_error_max_turns")

    def test_a_missing_is_error_is_treated_as_an_error(self) -> None:
        """An absent field must never be the reason a task is called complete."""
        events = self.normalizer.normalize(ResultMessage(is_error=None, result="hmm"))
        self.assertEqual([e.kind for e in events], [KIND_PROVIDER_FAILED])

    def test_success_with_nothing_to_show_is_a_failure(self) -> None:
        events = self.normalizer.normalize(ResultMessage(result=None))
        self.assertEqual([e.kind for e in events], [KIND_PROVIDER_FAILED])
        self.assertEqual(events[0].failure_code, "empty_result")

    def test_a_contradictory_subtype_does_not_become_the_failure_code(self) -> None:
        events = self.normalizer.normalize(
            ResultMessage(is_error=True, subtype="success", result="broke")
        )
        self.assertEqual(events[0].failure_code, "provider_error")

    def test_sub_task_notices_are_bounded_activity_not_lifecycle(self) -> None:
        for message in (
            TaskProgressMessage(),
            TaskNotificationMessage(),
            TaskUpdatedMessage(),
        ):
            with self.subTest(message=type(message).__name__):
                events = self.normalizer.normalize(message)
                self.assertEqual([e.kind for e in events], [KIND_ACTIVITY])
                self.assertFalse(events[0].terminal)

    def test_a_sub_task_output_file_path_is_never_recorded(self) -> None:
        events = self.normalizer.normalize(
            TaskNotificationMessage(output_file="/home/someone/private.txt")
        )
        self.assertNotIn("private.txt", str(events[0].to_dict()))

    def test_an_unknown_message_class_is_counted_and_dropped(self) -> None:
        self.assertEqual(self.normalizer.normalize(SomethingFromANewerSdk()), [])
        self.assertEqual(self.normalizer.unknown_messages, 1)

    def test_a_disabled_stream_event_is_counted_rather_than_parsed(self) -> None:
        self.assertEqual(self.normalizer.normalize(StreamEvent()), [])
        self.assertEqual(self.normalizer.unknown_messages, 1)

    def test_an_unrecognised_system_subtype_is_dropped_with_its_data(self) -> None:
        events = self.normalizer.normalize(
            SystemMessage(subtype="something", data={"secret": "value"})
        )
        self.assertEqual(events, [])

    def test_provider_sequence_increases_with_arrival(self) -> None:
        first = self.normalizer.normalize(SystemMessage(subtype="init"))[0]
        second = self.normalizer.normalize(ResultMessage(result="done"))[0]
        self.assertLess(first.provider_sequence, second.provider_sequence)

    def test_each_block_of_one_message_gets_its_own_event_id(self) -> None:
        events = self.normalizer.normalize(
            AssistantMessage(
                content=[TextBlock(text="one"), TextBlock(text="two")], uuid="u-1"
            )
        )
        self.assertEqual(len({e.provider_event_id for e in events}), 2)

    def test_no_raw_message_object_reaches_a_normalized_event(self) -> None:
        events = self.normalizer.normalize(
            AssistantMessage(content=[TextBlock(text="hello")], uuid="u-1")
        )
        for value in events[0].to_dict().values():
            self.assertNotIsInstance(value, AssistantMessage)
            self.assertNotIsInstance(value, TextBlock)


class ClarificationRecognitionTests(unittest.TestCase):
    """Conservative by design — the question tool's schema is unverified."""

    def test_a_question_tool_with_an_unmistakable_question_is_recognised(self) -> None:
        normalizer = normalize.MessageNormalizer()
        events = normalizer.normalize(
            AssistantMessage(
                content=[
                    ToolUseBlock(
                        id="t1",
                        name="AskUserQuestion",
                        input={
                            "question": "Rebase or merge?",
                            "options": [{"label": "rebase"}, {"label": "merge"}],
                        },
                    )
                ]
            )
        )
        self.assertEqual([e.kind for e in events], [KIND_CLARIFICATION_REQUESTED])
        self.assertEqual(events[0].clarification.question, "Rebase or merge?")
        self.assertIsNone(events[0].approval)

    def test_a_question_tool_this_build_cannot_read_degrades_to_activity(self) -> None:
        """No invented question. That mistake is much worse than a lost one."""
        normalizer = normalize.MessageNormalizer()
        for payload in ({}, {"prompt": "?"}, {"questions": []}, "not a dict", None):
            with self.subTest(payload=payload):
                events = normalizer.normalize(
                    AssistantMessage(
                        content=[
                            ToolUseBlock(id="t", name="AskUserQuestion", input=payload)
                        ]
                    )
                )
                self.assertEqual([e.kind for e in events], [KIND_TOOL_STARTED])

    def test_the_question_tool_is_not_in_the_running_profile(self) -> None:
        """Unreachable in this build, and stated as a fact rather than a hope."""
        for tool in normalize.QUESTION_TOOLS:
            self.assertNotIn(tool, option_policy.PROFILE_TOOLS)


class ApprovalRecognitionTests(unittest.TestCase):
    def test_an_approval_request_names_the_tool_and_nothing_else(self) -> None:
        request = normalize.approval_request("Bash")
        self.assertIsNotNone(request)
        self.assertEqual(request.tool_name, "Bash")
        self.assertEqual(request.tool_category, "execute")
        self.assertIsNone(request.reason)

    def test_an_approval_event_is_never_a_clarification(self) -> None:
        built = normalize.approval_event(
            request=normalize.approval_request("Bash"), provider_sequence=1
        )
        self.assertEqual(built.kind, KIND_TOOL_APPROVAL_REQUESTED)
        self.assertIsNone(built.clarification)
        self.assertEqual(built.to_dict()["request"]["category"], "tool_approval")

    def test_an_unusable_tool_name_produces_no_request(self) -> None:
        for bad in ("", None, 5, "  "):
            with self.subTest(value=bad):
                self.assertIsNone(normalize.approval_request(bad))

    def test_the_permission_handler_takes_only_the_tool_name(self) -> None:
        """Structural: the callback signature accepts the input, and this is the
        one function that turns a request into a stored record — it takes a
        name."""
        import inspect

        signature = inspect.signature(normalize.approval_request)
        self.assertEqual(sorted(signature.parameters), ["reason", "tool_name"])


# -- 4. the adapter ----------------------------------------------------------


class AdapterBehaviourTests(unittest.TestCase):
    """Driven through the injected session boundary. No SDK, no subprocess."""

    def setUp(self) -> None:
        import tempfile

        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.sessions = []

    def adapter(self, **session_kwargs) -> ClaudeAgentSdkAdapter:
        def factory(*, task_id, project_root, cli_path):
            session = FakeSession(task_id=task_id, **session_kwargs)
            session.project_root = project_root
            self.sessions.append(session)
            return session

        return ClaudeAgentSdkAdapter(
            session_factory=factory, availability=lambda: True
        )

    def test_start_passes_the_prompt_and_the_server_resolved_root(self) -> None:
        adapter = self.adapter()
        adapter.start(context(self.root))
        self.assertEqual(self.sessions[0].started_with, TURKISH_PROMPT)
        self.assertEqual(self.sessions[0].project_root, self.root)

    def test_start_is_refused_when_the_sdk_is_unavailable(self) -> None:
        adapter = ClaudeAgentSdkAdapter(availability=lambda: False)
        with self.assertRaises(AdapterRefusal):
            adapter.start(context(self.root))

    def test_a_second_start_for_one_task_is_refused(self) -> None:
        adapter = self.adapter()
        adapter.start(context(self.root))
        with self.assertRaises(AdapterRefusal):
            adapter.start(context(self.root))

    def test_the_concurrency_limit_refuses_truthfully(self) -> None:
        adapter = self.adapter()
        adapter.start(context(self.root, "tsk_1"))
        with self.assertRaises(AdapterRefusal) as caught:
            adapter.start(context(self.root, "tsk_2"))
        self.assertIn("one at a time", str(caught.exception))

    def test_a_failed_start_releases_the_slot(self) -> None:
        adapter = self.adapter(start_error="Claude could not be started")
        with self.assertRaises(AdapterRefusal):
            adapter.start(context(self.root, "tsk_1"))
        self.assertEqual(adapter.active_task_ids(), ())
        # And the next task is not blocked by the one that never ran.
        working = self.adapter()
        working.start(context(self.root, "tsk_2"))

    def test_inspect_reports_progress_without_ending_the_task(self) -> None:
        adapter = self.adapter(
            batches=[[event(KIND_ACTIVITY, 1, text="reading files")]]
        )
        adapter.start(context(self.root))
        outcome = adapter.inspect(context(self.root))
        self.assertIsNone(outcome.requested_state)
        self.assertEqual([e.text for e in outcome.events], ["reading files"])

    def test_an_event_is_reported_once(self) -> None:
        adapter = self.adapter(batches=[[event(KIND_ACTIVITY, 1, text="once")]])
        adapter.start(context(self.root))
        self.assertEqual(len(adapter.inspect(context(self.root)).events), 1)
        self.assertEqual(len(adapter.inspect(context(self.root)).events), 0)

    def test_a_duplicate_provider_event_does_not_become_two_events(self) -> None:
        duplicate = event(KIND_ACTIVITY, 1, text="same", provider_event_id="u1")
        adapter = self.adapter(batches=[[duplicate], [duplicate]])
        adapter.start(context(self.root))
        self.assertEqual(len(adapter.inspect(context(self.root)).events), 1)
        self.assertEqual(len(adapter.inspect(context(self.root)).events), 0)

    def test_a_success_completes_the_task_with_its_result(self) -> None:
        adapter = self.adapter(
            batches=[[event(KIND_SUCCEEDED, 1, text="all done", result="all done")]]
        )
        adapter.start(context(self.root))
        outcome = adapter.inspect(context(self.root))
        self.assertEqual(outcome.requested_state, STATE_COMPLETED)
        self.assertEqual(outcome.final_result, "all done")
        self.assertEqual(adapter.active_task_ids(), ())
        self.assertEqual(self.sessions[0].close_calls, 1)

    def test_a_provider_failure_fails_the_task_with_a_bounded_message(self) -> None:
        adapter = self.adapter(
            batches=[
                [event(KIND_PROVIDER_FAILED, 1, text="it broke", failure_code="claude_x")]
            ]
        )
        adapter.start(context(self.root))
        outcome = adapter.inspect(context(self.root))
        self.assertEqual(outcome.requested_state, STATE_FAILED)
        self.assertEqual(outcome.failure_code, "claude_x")
        self.assertEqual(outcome.failure_message, "it broke")

    def test_a_session_that_ends_with_no_result_is_a_failure(self) -> None:
        adapter = self.adapter()
        adapter.start(context(self.root))
        self.sessions[0].finish()
        outcome = adapter.inspect(context(self.root))
        self.assertEqual(outcome.requested_state, STATE_FAILED)
        self.assertEqual(outcome.failure_code, "agent_sdk_no_result")

    def test_a_late_event_after_a_terminal_one_is_ignored(self) -> None:
        adapter = self.adapter(
            batches=[
                [event(KIND_SUCCEEDED, 1, text="done", result="done")],
                [event(KIND_OUTPUT, 2, text="actually more")],
            ]
        )
        adapter.start(context(self.root))
        self.assertEqual(adapter.inspect(context(self.root)).requested_state, STATE_COMPLETED)
        # The task has been retired; a further inspect reports nothing at all
        # rather than resurrecting it.
        self.assertEqual(adapter.inspect(context(self.root)).requested_state, None)
        self.assertEqual(adapter.inspect(context(self.root)).events, ())


class CancellationTests(AdapterBehaviourTests):
    def test_cancel_reaches_the_session_and_reports_cancelled(self) -> None:
        adapter = self.adapter(
            cancel_events=[event(KIND_CANCELLED, 9, text="stopped")]
        )
        adapter.start(context(self.root))
        outcome = adapter.cancel(context(self.root))
        self.assertEqual(self.sessions[0].cancel_calls, 1)
        self.assertEqual(outcome.requested_state, STATE_CANCELLED)

    def test_a_cancel_that_did_not_land_is_refused_rather_than_claimed(self) -> None:
        adapter = self.adapter(cancel_succeeds=False)
        adapter.start(context(self.root))
        with self.assertRaises(AdapterRefusal) as caught:
            adapter.cancel(context(self.root))
        self.assertIn("did not stop", str(caught.exception))

    def test_a_result_that_already_arrived_wins_over_a_later_cancel(self) -> None:
        adapter = self.adapter(
            batches=[[event(KIND_SUCCEEDED, 1, text="finished", result="finished")]]
        )
        adapter.start(context(self.root))
        outcome = adapter.cancel(context(self.root))
        self.assertEqual(outcome.requested_state, STATE_COMPLETED)
        self.assertEqual(self.sessions[0].cancel_calls, 0)

    def test_a_result_arriving_after_a_cancellation_cannot_undo_it(self) -> None:
        adapter = self.adapter(
            cancel_events=[
                event(KIND_CANCELLED, 9, text="stopped"),
                event(KIND_SUCCEEDED, 10, text="too late", result="too late"),
            ]
        )
        adapter.start(context(self.root))
        outcome = adapter.cancel(context(self.root))
        self.assertEqual(outcome.requested_state, STATE_CANCELLED)
        self.assertEqual(
            adapter.result_for("tsk_1").terminal_state, STATE_CANCELLED
        )

    def test_cancelling_one_task_leaves_another_untouched(self) -> None:
        adapter = ClaudeAgentSdkAdapter(
            session_factory=lambda *, task_id, project_root, cli_path: self._named(
                task_id
            ),
            availability=lambda: True,
            max_concurrent=4,
        )
        adapter.start(context(self.root, "tsk_1"))
        adapter.start(context(self.root, "tsk_2"))
        adapter.cancel(context(self.root, "tsk_1"))
        by_id = {session.task_id: session for session in self.sessions}
        self.assertEqual(by_id["tsk_1"].cancel_calls, 1)
        self.assertEqual(by_id["tsk_2"].cancel_calls, 0)
        self.assertEqual(by_id["tsk_2"].close_calls, 0)
        self.assertIn("tsk_2", adapter.active_task_ids())

    def test_cancelling_an_unknown_task_is_a_refusal(self) -> None:
        adapter = self.adapter()
        with self.assertRaises(AdapterRefusal):
            adapter.cancel(context(self.root, "tsk_missing"))

    def test_shutdown_closes_every_session(self) -> None:
        adapter = ClaudeAgentSdkAdapter(
            session_factory=lambda *, task_id, project_root, cli_path: self._named(
                task_id
            ),
            availability=lambda: True,
            max_concurrent=4,
        )
        adapter.start(context(self.root, "tsk_1"))
        adapter.start(context(self.root, "tsk_2"))
        adapter.shutdown()
        self.assertEqual([s.close_calls for s in self.sessions], [1, 1])
        self.assertEqual(adapter.active_task_ids(), ())

    def _named(self, task_id: str) -> FakeSession:
        session = FakeSession(
            task_id=task_id, cancel_events=[event(KIND_CANCELLED, 9, text="stopped")]
        )
        self.sessions.append(session)
        return session


class ResultFoundationTests(AdapterBehaviourTests):
    def test_a_completed_task_has_a_durable_result_with_provenance(self) -> None:
        adapter = self.adapter(
            batches=[
                [
                    event(
                        KIND_SUCCEEDED,
                        1,
                        text="the answer",
                        result="the answer",
                        provider_session_id="sess-1",
                    )
                ]
            ]
        )
        adapter.start(context(self.root))
        adapter.inspect(context(self.root))
        result = adapter.result_for("tsk_1")
        self.assertTrue(result.succeeded)
        self.assertEqual(result.result, "the answer")
        self.assertEqual(result.provider, "claude-agent-sdk")
        self.assertEqual(result.provider_session_id, "sess-1")
        self.assertTrue(result.completed_at)

    def test_a_failed_task_stores_a_bounded_error_and_no_stack(self) -> None:
        adapter = self.adapter(
            batches=[
                [event(KIND_PROVIDER_FAILED, 1, text="y" * 4000, failure_code="claude_x")]
            ]
        )
        adapter.start(context(self.root))
        adapter.inspect(context(self.root))
        payload = adapter.result_for("tsk_1").to_dict()
        self.assertEqual(payload["failure_code"], "claude_x")
        self.assertLessEqual(len(payload["failure_summary"]), 500)
        for forbidden in ("Traceback", "File \"", "traceback", "exception"):
            self.assertNotIn(forbidden, str(payload))

    def test_a_task_with_no_result_yet_has_none(self) -> None:
        adapter = self.adapter()
        adapter.start(context(self.root))
        self.assertIsNone(adapter.result_for("tsk_1"))


class SessionSeamTests(unittest.TestCase):
    def test_follow_up_is_refused_truthfully_rather_than_silently_accepted(self) -> None:
        session = session_module.DelegatedSession()
        with self.assertRaises(session_module.SessionRefused):
            session.send_followup("anything")

    def test_the_adapter_does_not_claim_a_follow_up_capability(self) -> None:
        self.assertFalse(ClaudeAgentSdkAdapter().capabilities().followup)

    def test_the_adapter_claims_no_capability_it_has_not_implemented(self) -> None:
        capabilities = ClaudeAgentSdkAdapter().capabilities()
        self.assertFalse(capabilities.approvals)
        self.assertFalse(capabilities.recover_after_restart)
        self.assertFalse(capabilities.authentication_waits)
        self.assertTrue(capabilities.start)
        self.assertTrue(capabilities.cancel)

    def test_the_provider_session_id_survives_for_a_later_follow_up(self) -> None:
        session = FakeSession(provider_session_id="sess-1")
        self.assertEqual(session.provider_session_id, "sess-1")

    def _refusing_session(self, root: str) -> "session_module.SdkSession":
        """A real :class:`SdkSession` whose loader refuses.

        The loader is injected rather than left to find the real SDK, so this
        test behaves identically on a machine with the extra installed and on one
        without — and, crucially, **never launches a subprocess**. It exercises
        the real thread, the real event loop and the real failure path; only the
        provider is absent.
        """

        def loader():
            raise sdk_boundary.AgentSdkUnavailable(sdk_boundary.MISSING_MESSAGE)

        return session_module.SdkSession(
            task_id="tsk_1", project_root=Path(root), cli_path=None, loader=loader
        )

    def test_a_real_session_reports_a_missing_sdk_and_leaves_no_thread(self) -> None:
        """A start that cannot happen must not leave a thread behind.

        The failure this guards is invisible until a workstation has a dozen of
        them, so the thread count is checked rather than assumed.
        """
        import tempfile
        import threading

        before = threading.active_count()
        with tempfile.TemporaryDirectory() as root:
            session = self._refusing_session(root)
            with self.assertRaises(session_module.SessionRefused) as caught:
                session.start("do something")
        self.assertIn(sdk_boundary.EXTRA_NAME, str(caught.exception))
        self.assertTrue(session.finished)
        self.assertTrue(session.close())
        self.assertEqual(threading.active_count(), before)

    def test_a_real_session_refuses_a_second_start(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as root:
            session = self._refusing_session(root)
            with self.assertRaises(session_module.SessionRefused):
                session.start("first")
            with self.assertRaises(session_module.SessionRefused) as caught:
                session.start("second")
        self.assertIn("already been started", str(caught.exception))

    def test_a_real_session_mints_its_own_provider_session_id(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as root:
            first = session_module.SdkSession(
                task_id="a", project_root=Path(root), cli_path=None
            )
            second = session_module.SdkSession(
                task_id="b", project_root=Path(root), cli_path=None
            )
        self.assertNotEqual(first.provider_session_id, second.provider_session_id)

    def test_the_session_timeouts_are_bounded(self) -> None:
        """No unbounded wait anywhere on the boundary."""
        for value in (
            session_module.START_TIMEOUT_SECONDS,
            session_module.CANCEL_TIMEOUT_SECONDS,
            session_module.CLOSE_TIMEOUT_SECONDS,
        ):
            self.assertGreater(value, 0)
            self.assertLess(value, 600)
        self.assertGreater(session_module.MAX_BUFFERED_EVENTS, 0)


# -- 5. registration and regression ------------------------------------------


class RegistrationTests(unittest.TestCase):
    def test_the_adapter_is_absent_by_default(self) -> None:
        self.assertEqual(build_registry().ids(), ())
        self.assertEqual(build_registry(enable_claude_code_adapter=True).ids(), ("claude-code",))

    def test_the_flag_registers_it_and_leaves_claude_code_alone(self) -> None:
        registry = build_registry(
            enable_claude_code_adapter=True, enable_claude_agent_sdk_adapter=True
        )
        self.assertEqual(registry.ids(), ("claude-agent-sdk", "claude-code"))

    def test_the_two_adapters_have_different_ids(self) -> None:
        from cofferdam.workstation.tasks.adapters import (
            CLAUDE_AGENT_SDK_ADAPTER_ID,
            CLAUDE_CODE_ADAPTER_ID,
        )

        self.assertNotEqual(CLAUDE_AGENT_SDK_ADAPTER_ID, CLAUDE_CODE_ADAPTER_ID)

    def test_a_duplicate_adapter_id_is_refused_at_construction(self) -> None:
        with self.assertRaises(DuplicateAdapterId):
            AdapterRegistry((ClaudeAgentSdkAdapter(), ClaudeAgentSdkAdapter()))

    def test_the_config_default_is_off_and_host_owned(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as home:
            config = load_config(Path(home))
            self.assertFalse(config.enable_claude_agent_sdk_adapter)
            self.assertFalse(config.enable_claude_code_adapter)

    def test_the_environment_variable_turns_it_on(self) -> None:
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as home:
            os.environ["COFFERDAM_ENABLE_CLAUDE_AGENT_SDK_ADAPTER"] = "true"
            self.addCleanup(
                os.environ.pop, "COFFERDAM_ENABLE_CLAUDE_AGENT_SDK_ADAPTER", None
            )
            self.assertTrue(load_config(Path(home)).enable_claude_agent_sdk_adapter)

    def test_the_cli_flag_exists_and_does_not_disable_claude_code(self) -> None:
        import argparse
        import io
        import contextlib

        from cofferdam.workstation.__main__ import main

        help_text = io.StringIO()
        with contextlib.redirect_stdout(help_text):
            with self.assertRaises(SystemExit):
                main(["--help"])
        rendered = help_text.getvalue()
        self.assertIn("--enable-claude-agent-sdk-adapter", rendered)
        self.assertIn("--enable-claude-code-adapter", rendered)
        del argparse

    def test_no_deployment_file_enables_the_new_adapter(self) -> None:
        """This PR changes no unit, drop-in or installer."""
        for path in sorted((REPO_ROOT / "deploy").rglob("*")):
            if not path.is_file():
                continue
            with self.subTest(path=path.name):
                self.assertNotIn(
                    "enable-claude-agent-sdk-adapter",
                    path.read_text(encoding="utf-8", errors="replace"),
                )


class TaskCoreIntegrationTests(TaskTestCase):
    """Through the real service, store and transition graph."""

    enable_validation_adapter = False
    project_adapters = (ADAPTER_ID,)

    def setUp(self) -> None:
        self.sessions = []
        super().setUp()
        self.install_adapter(self._adapter())

    def _adapter(self) -> ClaudeAgentSdkAdapter:
        def factory(*, task_id, project_root, cli_path):
            session = FakeSession(
                task_id=task_id,
                batches=[
                    [
                        event(KIND_SESSION_STARTED, 1, text="Claude session ready."),
                        event(KIND_OUTPUT, 2, text="I read the file."),
                    ],
                    [event(KIND_SUCCEEDED, 3, text="done", result="done")],
                ],
                cancel_events=[event(KIND_CANCELLED, 9, text="stopped")],
            )
            self.sessions.append(session)
            return session

        return ClaudeAgentSdkAdapter(
            session_factory=factory, availability=lambda: True
        )

    def test_a_task_runs_through_to_a_stored_result(self) -> None:
        row = self.create(adapter_id=ADAPTER_ID)
        self.assertEqual(row.state, "running")
        self.service.refresh_task(row.task_id)
        settled = self.service.refresh_task(row.task_id)
        self.assertEqual(settled.state, STATE_COMPLETED)
        self.assertEqual(settled.final_result, "done")

    def test_the_prompt_never_appears_in_the_event_history_or_the_audit(self) -> None:
        row = self.create(adapter_id=ADAPTER_ID)
        self.service.refresh_task(row.task_id)
        history = " ".join(
            str(e.text or "") + str(e.detail or "")
            for e in self.store.events(row.task_id, limit=200)
        )
        self.assertNotIn(TURKISH_PROMPT, history)
        self.assertNotIn(TURKISH_PROMPT, self.audit_blob())

    def test_an_unknown_project_is_refused(self) -> None:
        from cofferdam.workstation.tasks.errors import ProjectUnknown

        with self.assertRaises(ProjectUnknown):
            self.create(adapter_id=ADAPTER_ID, project_id="not-configured")

    def test_a_disabled_project_is_refused(self) -> None:
        from cofferdam.workstation.tasks.errors import ProjectDisabled

        self.write_projects(
            [
                {
                    "project_id": PROJECT_ID,
                    "display_name": "Demo project",
                    "root": str(self.project_root),
                    "adapters": [ADAPTER_ID],
                    "enabled": False,
                }
            ]
        )
        self.service.reload_projects()
        with self.assertRaises(ProjectDisabled):
            self.create(adapter_id=ADAPTER_ID)

    def test_a_project_that_does_not_list_the_adapter_is_refused(self) -> None:
        from cofferdam.workstation.tasks.errors import AdapterNotPermitted

        self.write_projects(
            [
                {
                    "project_id": PROJECT_ID,
                    "display_name": "Demo project",
                    "root": str(self.project_root),
                    "adapters": [],
                }
            ]
        )
        self.service.reload_projects()
        with self.assertRaises(AdapterNotPermitted):
            self.create(adapter_id=ADAPTER_ID)

    def test_there_is_no_way_to_name_a_working_directory(self) -> None:
        """The adapter is handed a root the registry resolved, never a request
        value — there is no parameter for one anywhere on the path."""
        import inspect

        signature = inspect.signature(self.service.create_task)
        self.assertNotIn("root", signature.parameters)
        self.assertNotIn("cwd", signature.parameters)
        self.assertNotIn("project_root", signature.parameters)

    def test_a_restart_reports_the_task_interrupted_rather_than_resumed(self) -> None:
        row = self.create(adapter_id=ADAPTER_ID)
        service = self.restart()
        service._adapters._adapters[ADAPTER_ID] = self._adapter()
        settled = service.recover_after_restart()
        self.assertEqual([task.state for task in settled], ["interrupted"])
        self.assertEqual(service.store.get(row.task_id).state, "interrupted")

    def test_cancelling_records_a_cancellation_and_stops_only_that_task(self) -> None:
        first = self.create(adapter_id=ADAPTER_ID)
        cancelled = self.service.cancel_task(first.task_id)
        self.assertEqual(cancelled.state, STATE_CANCELLED)
        self.assertEqual(self.sessions[0].cancel_calls, 1)

    def test_cancelling_a_finished_task_is_a_truthful_refusal(self) -> None:
        from cofferdam.workstation.tasks.errors import TaskAlreadyFinished

        row = self.create(adapter_id=ADAPTER_ID)
        self.service.refresh_task(row.task_id)
        self.service.refresh_task(row.task_id)
        with self.assertRaises(TaskAlreadyFinished):
            self.service.cancel_task(row.task_id)

    def test_a_follow_up_is_refused_because_the_adapter_does_not_claim_it(self) -> None:
        from cofferdam.workstation.tasks.errors import FollowupUnsupported

        row = self.create(adapter_id=ADAPTER_ID)
        with self.assertRaises(FollowupUnsupported):
            self.service.send_followup(row.task_id, "and also this")

    def test_the_claude_code_adapter_is_untouched_by_any_of_this(self) -> None:
        from cofferdam.workstation.tasks.adapters.claude_code import ClaudeCodeAdapter

        capabilities = ClaudeCodeAdapter(executable=None).capabilities()
        self.assertTrue(capabilities.followup)
        self.assertTrue(capabilities.authentication_waits)


if __name__ == "__main__":
    unittest.main()
