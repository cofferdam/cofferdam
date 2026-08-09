"""The structured clarification round trip: environment, schema, separation, lifecycle.

Standard library only, and every test here runs on a machine where the Claude
Agent SDK is **not installed** and where nothing starts a process, opens a socket
or reaches Anthropic. That is not a compromise; it is what the helper-process
boundary and the injected session double are for.

What is proven here
-------------------

*The environment an agent session receives is a complete code-owned allowlist*,
built by selection, containing no Cofferdam token and no unrelated provider key —
and a structural guard that the package cannot regress to forwarding
``os.environ``.

*A question is read conservatively.* Shapes this build cannot defend become a
bounded observation carrying names, types and counts, and never a fabricated
clarification.

*A clarification is not a tool approval*, in Python types, in serialized form, in
storage, at the API boundary and in the transition graph — asserted in both
directions, including against a request body that tries to smuggle one into the
other.

*The lifecycle is truthful.* A question moves a task to ``waiting_for_user``; an
accepted answer moves it back to ``running`` and resumes the same session; a
cancelled task's questions close with it; a restart does not claim any of it is
resumable.

What is **not** proven here, said plainly because a suite that implies more than
it checked is worse than a smaller one: that a real Claude session emits
``AskUserQuestion`` in the shape this build reads. That schema is not in the SDK
distribution — the string does not occur in it — and settling it needs a
supervised live spike. Until then
:data:`~cofferdam...question.SCHEMA_VERIFIED` is ``False``, every stored
clarification records that it was, and these tests use sanitized fixtures whose
content was invented here.
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ._task_doubles import PROJECT_ID, TURKISH_PROMPT, TaskTestCase, python_code_only

from cofferdam.workstation.tasks import clarifications as clar
from cofferdam.workstation.tasks import errors as task_errors
from cofferdam.workstation.tasks.adapters.claude_agent_sdk import (
    hostenv,
    hostproto,
    question,
)
from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    AdapterRefusal,
    TaskAdapter,
    TaskContext,
)
from cofferdam.workstation.tasks.delegated import (
    ANSWER_MODE_FREE_TEXT,
    ANSWER_MODE_MULTIPLE_CHOICE,
    ANSWER_MODE_SINGLE_CHOICE,
    ANSWER_MODE_UNKNOWN,
    CATEGORY_CLARIFICATION,
    CATEGORY_TOOL_APPROVAL,
    KIND_CLARIFICATION_REQUESTED,
    ClarificationRequest,
    DelegatedEventInvalid,
    ToolApprovalRequest,
    build_event,
)
from cofferdam.workstation.tasks.models import (
    STATE_CANCELLED,
    STATE_INTERRUPTED,
    STATE_RUNNING,
    STATE_WAITING_FOR_USER,
    WAITING_APPROVAL,
    WAITING_CLARIFICATION,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SDK_PACKAGE = (
    REPO_ROOT
    / "cofferdam"
    / "workstation"
    / "tasks"
    / "adapters"
    / "claude_agent_sdk"
)


# -- sanitized fixtures ------------------------------------------------------
#
# Every string below was invented for this file. None of it came from a
# transcript, a provider payload or a real session, and the schema these shapes
# assume is **unverified** — which is exactly why the reader under test is
# allowed to answer "I cannot read this".

SINGLE_CHOICE = {
    "questions": [
        {
            "question": "Which branch should this land on?",
            "options": [{"label": "main"}, {"label": "develop"}],
        }
    ]
}

MULTIPLE_CHOICE = {
    "questions": [
        {
            "question": "Which files may be touched?",
            "multiSelect": True,
            "options": [{"label": "README"}, {"label": "CHANGELOG"}],
        }
    ]
}

FREE_TEXT = {"questions": [{"question": "What should the flag be called?"}]}

TURKISH_QUESTION = {"questions": [{"question": "Hangi dosyayı düzenlemeli?"}]}


def clarification_request(payload: Dict[str, Any]) -> ClarificationRequest:
    from cofferdam.workstation.tasks.adapters.claude_agent_sdk import normalize

    parsed = question.read_question(payload)
    assert parsed is not None, "fixture must be readable"
    return normalize.clarification_from_observed(parsed)


# -- 1. the environment boundary ---------------------------------------------


class ChildEnvironmentTests(unittest.TestCase):
    """The finding this whole PR started from, and its fix.

    M2I PR1 verified from the published SDK source that
    ``ClaudeAgentOptions.env`` is layered over ``os.environ`` rather than
    replacing it, so an SDK child inherits the daemon's environment. These tests
    are the boundary that stops that mattering.
    """

    DAEMON = {
        # What a real workstation daemon plausibly has.
        "HOME": "/home/someone",
        "PATH": "/usr/bin:/bin",
        "USER": "someone",
        "LANG": "en_GB.UTF-8",
        "XDG_CONFIG_HOME": "/home/someone/.config",
        # ...and everything it must not pass on.
        "COFFERDAM_TOKEN": "a-real-looking-device-token",
        "COFFERDAM_HOME": "/home/someone/cofferdam",
        "ANTHROPIC_API_KEY": "sk-ant-not-a-real-key",
        "OPENAI_API_KEY": "sk-not-a-real-key",
        "CLOUDFLARE_API_TOKEN": "cf-not-a-real-token",
        "TS_AUTHKEY": "tskey-not-a-real-key",
        "GITHUB_TOKEN": "ghp-not-a-real-token",
        "DATABASE_URL": "postgres://user:password@localhost/db",
        "SPOTIFY_CLIENT_SECRET": "not-a-real-secret",
        "LD_PRELOAD": "/tmp/anything.so",
        "SOMETHING_ADDED_NEXT_YEAR": "whatever",
    }

    def child(self, **kwargs: Any) -> Dict[str, str]:
        return hostenv.build_child_environment(self.DAEMON, **kwargs)

    def test_the_child_environment_is_complete_and_explicit(self) -> None:
        """Every name is one this file names. Nothing arrives by inheritance."""
        built = self.child()
        self.assertEqual(
            set(built), set(hostenv.environment_key_names(built))
        )
        for name in built:
            self.assertIn(name, hostenv.permitted_names())

    def test_the_cofferdam_token_never_reaches_the_child(self) -> None:
        built = self.child()
        self.assertNotIn("COFFERDAM_TOKEN", built)
        self.assertNotIn("a-real-looking-device-token", json.dumps(built))

    def test_no_unrelated_provider_key_reaches_the_child(self) -> None:
        built = self.child()
        blob = json.dumps(built)
        for name in hostenv.EXCLUDED_ENVIRONMENT_NAMES:
            with self.subTest(name=name):
                self.assertNotIn(name, built)
        for secret in (
            "sk-ant-not-a-real-key",
            "sk-not-a-real-key",
            "cf-not-a-real-token",
            "tskey-not-a-real-key",
            "ghp-not-a-real-token",
            "not-a-real-secret",
            "password",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, blob)

    def test_a_variable_nobody_anticipated_is_excluded_too(self) -> None:
        """The property a denylist cannot have.

        ``LD_PRELOAD`` and a variable somebody adds next year are both absent —
        not because they are on a list, but because they are not on *the* list.
        This is the difference the helper process bought.
        """
        built = self.child()
        self.assertNotIn("LD_PRELOAD", built)
        self.assertNotIn("SOMETHING_ADDED_NEXT_YEAR", built)

    def test_the_login_variables_the_host_cli_needs_are_included(self) -> None:
        """``HOME`` is load-bearing: it is how the CLI finds its own sign-in.

        Cofferdam grants *reachability*, never *possession* — it passes ``HOME``
        and never reads a credential file, which is why there is no test here
        asserting anything about what is under it.
        """
        built = self.child()
        for name in ("HOME", "PATH", "USER", "LANG", "XDG_CONFIG_HOME"):
            self.assertEqual(built[name], self.DAEMON[name])

    def test_an_absent_optional_variable_is_simply_absent(self) -> None:
        built = hostenv.build_child_environment({"HOME": "/home/someone"})
        self.assertNotIn("TMPDIR", built)
        self.assertNotIn("XDG_CACHE_HOME", built)
        self.assertEqual(built["HOME"], "/home/someone")

    def test_the_forced_values_win_over_an_inherited_one(self) -> None:
        built = hostenv.build_child_environment({"NO_COLOR": "0", "TERM": "xterm"})
        self.assertEqual(built["NO_COLOR"], "1")
        self.assertEqual(built["PYTHONIOENCODING"], "utf-8")
        # ...and a genuinely inherited one still comes through.
        self.assertEqual(built["TERM"], "xterm")

    def test_the_session_environment_drops_the_bootstrap_name(self) -> None:
        """What the SDK will merge into is the session environment, not the boot one.

        ``PYTHONPATH`` exists only so the helper can import the same Cofferdam
        that launched it. The helper removes it before constructing anything from
        the SDK, so the CLI grandchild never sees it.
        """
        with_boot = self.child()
        without = self.child(include_bootstrap=False)
        self.assertIn(hostenv.BOOTSTRAP_PYTHONPATH, with_boot)
        self.assertNotIn(hostenv.BOOTSTRAP_PYTHONPATH, without)
        self.assertEqual(
            set(with_boot) - set(without), {hostenv.BOOTSTRAP_PYTHONPATH}
        )

    def test_the_bootstrap_path_is_derived_from_this_package(self) -> None:
        """Code-owned, not configured: it comes from ``__file__``."""
        root = hostenv.package_import_root()
        self.assertTrue((root / "cofferdam" / "__init__.py").is_file())
        self.assertEqual(self.child()[hostenv.BOOTSTRAP_PYTHONPATH], str(root))

    def test_an_unexpected_name_is_refused_before_a_process_exists(self) -> None:
        with self.assertRaises(hostenv.EnvironmentPolicyError):
            hostenv.verify_child_environment({"COFFERDAM_TOKEN": "x"})

    def test_a_refusal_names_the_key_and_never_the_value(self) -> None:
        """A guard whose failure mode prints the secret would be worse than none."""
        with self.assertRaises(hostenv.EnvironmentPolicyError) as caught:
            hostenv.verify_child_environment({"ANTHROPIC_API_KEY": "sk-ant-secret"})
        message = str(caught.exception)
        self.assertIn("ANTHROPIC_API_KEY", message)
        self.assertNotIn("sk-ant-secret", message)

    def test_a_non_text_value_is_refused_rather_than_coerced(self) -> None:
        with self.assertRaises(hostenv.EnvironmentPolicyError):
            hostenv.verify_child_environment({"HOME": 5})

    def test_only_names_can_be_described_never_values(self) -> None:
        """There is no function here that returns an environment's contents."""
        described = hostenv.environment_key_names(self.child())
        self.assertNotIn("a-real-looking-device-token", json.dumps(list(described)))
        self.assertEqual(list(described), sorted(described))

    def test_the_allowlist_matches_the_claude_code_adapter(self) -> None:
        """Two transports, one reachability policy.

        A name one transport passed and the other did not would mean switching
        transport quietly changed what the agent could reach.
        """
        from cofferdam.workstation.tasks.adapters.claude_code import cli

        self.assertEqual(
            hostenv.CHILD_ENVIRONMENT_ALLOWLIST, cli.ENVIRONMENT_ALLOWLIST
        )


# -- 2. reading a question ---------------------------------------------------


class QuestionSchemaTests(unittest.TestCase):
    def test_a_single_choice_question_is_read(self) -> None:
        parsed = question.read_question(SINGLE_CHOICE)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.question, "Which branch should this land on?")
        self.assertEqual(parsed.answer_mode, ANSWER_MODE_SINGLE_CHOICE)
        self.assertEqual([o.label for o in parsed.options], ["main", "develop"])
        self.assertEqual([o.option_id for o in parsed.options], ["opt1", "opt2"])
        self.assertFalse(parsed.allows_free_text)

    def test_a_multiple_choice_question_is_read_only_on_a_real_boolean(self) -> None:
        self.assertEqual(
            question.read_question(MULTIPLE_CHOICE).answer_mode,
            ANSWER_MODE_MULTIPLE_CHOICE,
        )
        # A provider sending the *string* "false" must not be read as multi.
        stringy = {
            "questions": [
                {
                    "question": "q",
                    "multiSelect": "false",
                    "options": [{"label": "a"}],
                }
            ]
        }
        self.assertEqual(
            question.read_question(stringy).answer_mode, ANSWER_MODE_SINGLE_CHOICE
        )

    def test_a_question_with_no_options_is_free_text(self) -> None:
        parsed = question.read_question(FREE_TEXT)
        self.assertEqual(parsed.answer_mode, ANSWER_MODE_FREE_TEXT)
        self.assertEqual(parsed.options, ())
        self.assertTrue(parsed.allows_free_text)

    def test_turkish_text_is_ordinary_text(self) -> None:
        """Not a special case, and it must never become one."""
        parsed = question.read_question(TURKISH_QUESTION)
        self.assertEqual(parsed.question, "Hangi dosyayı düzenlemeli?")

    def test_the_singular_form_is_read_too(self) -> None:
        parsed = question.read_question({"question": "One or two?"})
        self.assertEqual(parsed.question, "One or two?")

    def test_only_the_first_of_several_questions_is_read(self) -> None:
        """One active clarification per turn is the rule the lifecycle needs."""
        payload = {
            "questions": [
                {"question": "first"},
                {"question": "second"},
            ]
        }
        self.assertEqual(question.read_question(payload).question, "first")

    def test_more_questions_than_this_build_has_seen_are_refused(self) -> None:
        payload = {
            "questions": [
                {"question": "q" + str(index)}
                for index in range(question.MAX_QUESTIONS + 1)
            ]
        }
        self.assertIsNone(question.read_question(payload))

    def test_a_malformed_input_is_unreadable_rather_than_invented(self) -> None:
        for payload in (
            {},
            None,
            "not a dict",
            [],
            {"prompt": "?"},
            {"questions": []},
            {"questions": "not a list"},
            {"question": ""},
            {"question": "   "},
            {"question": 17},
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(question.read_question(payload))

    def test_a_present_but_unusable_option_list_refuses_the_whole_question(self) -> None:
        """Distinct from having no options, which is a legitimate free-text ask.

        Something offered choices and this build could not read them. Presenting
        that as free text would change what was asked, so it is refused.
        """
        for options in ([{"nope": 1}], [5], [{"label": "   "}], "not a list"):
            with self.subTest(options=options):
                self.assertIsNone(
                    question.read_question({"question": "q", "options": options})
                )

    def test_too_many_options_are_refused_rather_than_truncated(self) -> None:
        payload = {
            "question": "q",
            "options": [{"label": str(index)} for index in range(50)],
        }
        self.assertIsNone(question.read_question(payload))

    def test_oversized_text_is_truncated_to_a_named_bound(self) -> None:
        from cofferdam.workstation.tasks.delegated import (
            MAX_OPTION_LABEL_CHARS,
            MAX_QUESTION_CHARS,
        )

        parsed = question.read_question(
            {
                "question": "q" * (MAX_QUESTION_CHARS * 4),
                "options": [{"label": "o" * 400, "description": "d" * 900}],
            }
        )
        self.assertLessEqual(len(parsed.question), MAX_QUESTION_CHARS)
        self.assertLessEqual(len(parsed.options[0].label), MAX_OPTION_LABEL_CHARS)
        self.assertLessEqual(
            len(parsed.options[0].description), question.MAX_OPTION_DESCRIPTION_CHARS
        )

    def test_unknown_keys_are_ignored_rather_than_stored(self) -> None:
        parsed = question.read_question(
            {"question": "q", "surprise": {"nested": "value"}, "header": "h"}
        )
        self.assertEqual(parsed.question, "q")
        self.assertNotIn("surprise", repr(parsed))
        self.assertNotIn("nested", repr(parsed))

    def test_escape_sequences_and_bidi_overrides_do_not_survive(self) -> None:
        hostile = "\x1b]0;title\x07\x1b[31mred\x1b[0m‮gnitirw"
        parsed = question.read_question({"question": hostile})
        self.assertNotIn("\x1b", parsed.question)
        self.assertNotIn("0;title", parsed.question)
        self.assertNotIn("‮", parsed.question)

    def test_a_read_question_records_that_the_schema_is_unverified(self) -> None:
        """The honesty flag travels with the record, not with the build."""
        self.assertFalse(question.SCHEMA_VERIFIED)
        self.assertFalse(question.read_question(FREE_TEXT).schema_verified)
        self.assertFalse(clarification_request(FREE_TEXT).schema_verified)

    def test_the_evidence_a_live_spike_must_produce_is_written_down(self) -> None:
        self.assertGreaterEqual(len(question.SCHEMA_EVIDENCE_REQUIRED), 6)
        joined = " ".join(question.SCHEMA_EVIDENCE_REQUIRED).lower()
        for expected in ("tool name", "free-text", "session identifier"):
            self.assertIn(expected, joined)


class ObservationTests(unittest.TestCase):
    """The bounded capture seam. Names, types and counts — never a value."""

    def test_an_observation_carries_no_value_at_all(self) -> None:
        observed = question.observe(
            "AskUserQuestion",
            {
                "questions": [
                    {
                        "question": "a-very-distinctive-question-string",
                        "options": [{"label": "a-very-distinctive-option"}],
                    }
                ],
                "secret_field": "a-very-distinctive-value",
            },
        )
        blob = json.dumps(observed.to_dict())
        self.assertNotIn("a-very-distinctive-question-string", blob)
        self.assertNotIn("a-very-distinctive-option", blob)
        self.assertNotIn("a-very-distinctive-value", blob)
        # ...but the shape is there.
        self.assertIn("questions", observed.key_names)
        self.assertIn("secret_field", observed.key_names)
        self.assertEqual(observed.question_count, 1)
        self.assertEqual(observed.option_count, 1)

    def test_an_observation_never_raises_whatever_it_is_given(self) -> None:
        for payload in (None, 5, "text", [], {"a": object()}, {1: 2}):
            with self.subTest(payload=payload):
                observed = question.observe("AskUserQuestion", payload)
                self.assertIsInstance(observed.summary(), str)

    def test_an_observation_says_whether_the_reader_could_defend_it(self) -> None:
        self.assertTrue(question.observe("AskUserQuestion", FREE_TEXT).readable)
        self.assertFalse(question.observe("AskUserQuestion", {"nope": 1}).readable)

    def test_the_key_names_are_bounded_and_sanitized(self) -> None:
        payload = {("k" + str(index)): index for index in range(200)}
        payload["\x1b[31mhostile"] = 1
        observed = question.observe("AskUserQuestion", payload)
        self.assertLessEqual(len(observed.key_names), question.MAX_OBSERVED_KEYS)
        self.assertNotIn("\x1b", "".join(observed.key_names))

    def test_a_summary_is_one_bounded_line_naming_no_content(self) -> None:
        observed = question.observe("AskUserQuestion", {"nope": "distinctive-value"})
        summary = observed.summary()
        self.assertNotIn("distinctive-value", summary)
        self.assertIn("nope", summary)


# -- 3. clarification is not tool approval -----------------------------------


class SeparationTests(unittest.TestCase):
    """The safety boundary, asserted in both directions at every layer."""

    def test_a_clarification_cannot_deserialize_as_an_approval(self) -> None:
        payload = clarification_request(SINGLE_CHOICE).to_dict()
        with self.assertRaises(DelegatedEventInvalid):
            ToolApprovalRequest.from_dict(payload)

    def test_an_approval_cannot_deserialize_as_a_clarification(self) -> None:
        payload = ToolApprovalRequest.from_dict(
            {"category": CATEGORY_TOOL_APPROVAL, "tool_name": "Bash"}
        ).to_dict()
        with self.assertRaises(DelegatedEventInvalid):
            ClarificationRequest.from_dict(payload)

    def test_the_right_category_with_the_wrong_field_is_still_refused(self) -> None:
        """The refusal that matters: a payload that *looks* legitimate."""
        with self.assertRaises(DelegatedEventInvalid):
            ClarificationRequest.from_dict(
                {
                    "category": CATEGORY_CLARIFICATION,
                    "question": "may I?",
                    "tool_name": "Bash",
                }
            )
        with self.assertRaises(DelegatedEventInvalid):
            ToolApprovalRequest.from_dict(
                {
                    "category": CATEGORY_TOOL_APPROVAL,
                    "tool_name": "Bash",
                    "answer_mode": ANSWER_MODE_SINGLE_CHOICE,
                }
            )

    def test_a_stored_clarification_carries_no_tool_field(self) -> None:
        pending = clar.build_pending(
            task_id="task_x",
            provider="p",
            request=clarification_request(SINGLE_CHOICE),
            requested_at="2026-08-09T00:00:00Z",
        )
        payload = pending.to_dict()
        for forbidden in (
            "tool_name",
            "tool_category",
            "tool_input",
            "command",
            "path",
            "argv",
            "env",
            "permission_mode",
            "behavior",
        ):
            self.assertNotIn(forbidden, payload)
        self.assertEqual(payload["category"], CATEGORY_CLARIFICATION)

    def test_the_pending_record_has_nowhere_to_put_a_provider_payload(self) -> None:
        """Structural: the guarantee is the absence of a field."""
        forbidden = {"raw", "payload", "data", "message", "response", "tool_input"}
        for cls in (
            clar.PendingClarification,
            clar.ClarificationAnswer,
            clar.AnswerProvenance,
        ):
            with self.subTest(cls=cls.__name__):
                fields = set(getattr(cls, "__dataclass_fields__", {}))
                self.assertEqual(fields & forbidden, set())

    def test_a_clarification_category_cannot_be_written(self) -> None:
        pending = clar.build_pending(
            task_id="task_x",
            provider="p",
            request=clarification_request(FREE_TEXT),
            requested_at="now",
        )
        self.assertEqual(pending.category, CATEGORY_CLARIFICATION)
        with self.assertRaises((AttributeError, TypeError)):
            pending.category = CATEGORY_TOOL_APPROVAL  # type: ignore[misc]

    def test_an_answer_body_carrying_an_approval_field_is_refused(self) -> None:
        pending = clar.build_pending(
            task_id="task_x",
            provider="p",
            request=clarification_request(FREE_TEXT),
            requested_at="now",
        )
        provenance = clar.AnswerProvenance.build(
            source=clar.SOURCE_INTERNAL_TEST, received_at="now"
        )
        for intruder in (
            {"answer": "yes", "tool_name": "Bash"},
            {"answer": "yes", "approval_id": "a1"},
            {"answer": "yes", "behavior": "allow"},
            {"answer": "yes", "decision": "allow"},
            {"answer": "yes", "command": "rm -rf /"},
            {"answer": "yes", "path": "/etc/passwd"},
            {"answer": "yes", "permission_mode": "bypassPermissions"},
            {"answer": "yes", "env": {"X": "1"}},
            {"answer": "yes", "argv": ["sh"]},
        ):
            with self.subTest(body=sorted(intruder)):
                with self.assertRaises(clar.ClarificationInvalid):
                    clar.ClarificationAnswer.from_request(
                        intruder, clarification=pending, provenance=provenance
                    )

    def test_the_waiting_reasons_stay_two_different_words(self) -> None:
        clarify = build_event(
            kind=KIND_CLARIFICATION_REQUESTED,
            provider="p",
            provider_sequence=1,
            observed_at="now",
            clarification=clarification_request(FREE_TEXT),
        )
        approve = build_event(
            kind="tool_approval_requested",
            provider="p",
            provider_sequence=2,
            observed_at="now",
            approval=ToolApprovalRequest.from_dict(
                {"category": CATEGORY_TOOL_APPROVAL, "tool_name": "Bash"}
            ),
        )
        self.assertEqual(clarify.waiting_reason, WAITING_CLARIFICATION)
        self.assertEqual(approve.waiting_reason, WAITING_APPROVAL)

    def test_there_is_no_approval_table_in_the_schema(self) -> None:
        """Not a disabled one. None. There is no row to write into."""
        from cofferdam.workstation.tasks import store as store_module

        schema = store_module._SCHEMA.lower()
        self.assertIn("task_clarifications", schema)
        for forbidden in ("approval", "permission", "tool_use"):
            self.assertNotIn("create table if not exists task_" + forbidden, schema)

    def test_there_is_no_generic_answer_route(self) -> None:
        """One route per category, and no shared handler to collapse them into.

        A single "answer a request" endpoint is the shape somebody would reach
        for to avoid writing two similar handlers — and it would put the whole
        distinction inside one ``if``.
        """
        source = (
            REPO_ROOT / "cofferdam" / "workstation" / "service.py"
        ).read_text(encoding="utf-8")
        self.assertIn("/clarifications/{question_id}/answer", source)
        for forbidden in (
            "/requests/{request_id}/answer",
            "/approvals",
            "/answer-request",
            "approve_tool",
        ):
            self.assertNotIn(forbidden, source)

    def test_the_answer_route_accepts_only_two_fields(self) -> None:
        """Asserted against the route's own allowlist, read from source."""
        source = python_code_only(
            (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text("utf-8")
        )
        self.assertIn('allowed\n=\n{\n"answer"\n,\n"option_ids"\n}', source)


# -- 4. lifecycle ------------------------------------------------------------


class ClarifyingAdapter(TaskAdapter):
    """An adapter that asks one question, then finishes when it is answered.

    The narrowest double that can exercise the round trip: it holds a token, it
    reports a question through ``inspect``, it accepts an answer only for the
    token it is holding, and it records which task each call named so a test can
    prove an answer never reached a second session.
    """

    adapter_id = "clarifying"
    display_name = "Clarifying adapter"
    description = "A test double that asks questions."

    def __init__(
        self,
        *,
        adapter_id: str = "clarifying",
        payload: Optional[Dict[str, Any]] = None,
        token: str = "ask_token_1",
        accept_answer: bool = True,
        session_id: str = "sess-1",
    ) -> None:
        self.adapter_id = adapter_id
        self._payload = payload if payload is not None else SINGLE_CHOICE
        self._token = token
        self._accept = accept_answer
        self._session_id = session_id
        #: Set to ``True`` to make the *next* inspect report the same question
        #: again, as a provider that retried its event would.
        self.repeat_question = False
        self.ask_next_inspect = True
        self.answers: List[Any] = []
        self.answered_tasks: List[str] = []
        self.finish_next_inspect = False
        self.cancelled: List[str] = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            start=True,
            cancel=True,
            structured_progress=True,
            final_result=True,
            clarifications=True,
        )

    def available(self) -> bool:
        return True

    def start(self, context: TaskContext) -> AdapterOutcome:
        return AdapterOutcome(events=(AdapterEvent(text="started"),))

    def inspect(self, context: TaskContext) -> AdapterOutcome:
        if self.finish_next_inspect:
            self.finish_next_inspect = False
            return AdapterOutcome(
                events=(AdapterEvent(text="done"),),
                requested_state="completed",
                final_result="finished",
            )
        if not (self.ask_next_inspect or self.repeat_question):
            return AdapterOutcome()
        self.ask_next_inspect = False
        self.repeat_question = False
        return AdapterOutcome(
            events=(AdapterEvent(text="asking"),),
            requested_state=STATE_WAITING_FOR_USER,
            waiting_reason=WAITING_CLARIFICATION,
            clarification=clarification_request(self._payload),
            clarification_token=self._token,
        )

    def deliver_clarification_answer(
        self, context: TaskContext, token: str, answer: str
    ) -> bool:
        if token != self._token or not self._accept:
            return False
        self.answers.append(answer)
        self.answered_tasks.append(context.task_id)
        self.finish_next_inspect = True
        return True

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        self.cancelled.append(context.task_id)
        return AdapterOutcome(requested_state=STATE_CANCELLED)


class ClarificationLifecycleTests(TaskTestCase):
    project_adapters = ("validation", "scripted", "clarifying", "other")

    def setUp(self) -> None:
        super().setUp()
        self.agent = self.install_adapter(ClarifyingAdapter())

    def ask(self):
        """Create a task and drive it to ``waiting_for_user(clarification)``."""
        row = self.create(adapter_id="clarifying")
        row = self.service.refresh_task(row.task_id)
        return row

    def pending(self, task_id: str):
        items = self.service.pending_clarifications(task_id)
        self.assertEqual(len(items), 1)
        return items[0]

    # -- running → waiting ---------------------------------------------------

    def test_a_question_moves_the_task_to_waiting_for_a_clarification(self) -> None:
        row = self.ask()
        self.assertEqual(row.state, STATE_WAITING_FOR_USER)
        self.assertEqual(row.waiting_reason, WAITING_CLARIFICATION)

    def test_the_question_is_durable_and_bounded(self) -> None:
        row = self.ask()
        pending = self.pending(row.task_id)
        self.assertEqual(pending.status, clar.STATUS_PENDING)
        self.assertEqual(pending.question, "Which branch should this land on?")
        self.assertEqual(pending.answer_mode, ANSWER_MODE_SINGLE_CHOICE)
        self.assertEqual([o.option_id for o in pending.options], ["opt1", "opt2"])
        self.assertTrue(clar.valid_question_id(pending.question_id))

    def test_the_question_id_is_not_derived_from_the_question(self) -> None:
        first = self.ask()
        self.agent.ask_next_inspect = True
        second = self.create(adapter_id="clarifying")
        second = self.service.refresh_task(second.task_id)
        ids = {
            self.pending(first.task_id).question_id,
            self.pending(second.task_id).question_id,
        }
        self.assertEqual(len(ids), 2)

    def test_the_client_shape_publishes_no_provider_session_id(self) -> None:
        row = self.ask()
        payload = self.pending(row.task_id).to_dict()
        self.assertNotIn("provider_session_id", payload)
        self.assertNotIn("provider_event_id", payload)

    # -- waiting → running ---------------------------------------------------

    def test_an_accepted_answer_returns_the_task_to_running(self) -> None:
        row = self.ask()
        pending = self.pending(row.task_id)
        updated = self.service.answer_clarification(
            row.task_id,
            pending.question_id,
            {"option_ids": ["opt1"]},
            source=clar.SOURCE_INTERNAL_TEST,
        )
        self.assertEqual(updated.state, STATE_RUNNING)
        self.assertIsNone(updated.waiting_reason)

    def test_the_answer_reaches_the_provider_encoded_by_cofferdam(self) -> None:
        row = self.ask()
        pending = self.pending(row.task_id)
        self.service.answer_clarification(
            row.task_id,
            pending.question_id,
            {"option_ids": ["opt2"]},
            source=clar.SOURCE_INTERNAL_TEST,
        )
        self.assertEqual(self.agent.answers, ["Selected: develop"])

    def test_a_free_text_answer_is_delivered_unaltered(self) -> None:
        self.agent = self.install_adapter(
            ClarifyingAdapter(adapter_id="freeform", payload=FREE_TEXT)
        )
        row = self.create(adapter_id="freeform")
        row = self.service.refresh_task(row.task_id)
        pending = self.pending(row.task_id)
        self.service.answer_clarification(
            row.task_id,
            pending.question_id,
            {"answer": "Türkçe cevap"},
            source=clar.SOURCE_INTERNAL_TEST,
        )
        self.assertEqual(self.agent.answers, ["Türkçe cevap"])

    def test_the_question_is_marked_answered_and_keeps_its_history(self) -> None:
        row = self.ask()
        pending = self.pending(row.task_id)
        self.service.answer_clarification(
            row.task_id,
            pending.question_id,
            {"option_ids": ["opt1"]},
            source=clar.SOURCE_INTERNAL_TEST,
        )
        stored = self.store.find_clarification(row.task_id, pending.question_id)
        self.assertEqual(stored.status, clar.STATUS_ANSWERED)
        self.assertIsNotNone(stored.answered_at)
        self.assertEqual(stored.answer.option_ids, ("opt1",))
        self.assertEqual(self.service.pending_clarifications(row.task_id), [])

    def test_the_history_records_the_shape_of_an_answer_not_its_text(self) -> None:
        self.agent = self.install_adapter(
            ClarifyingAdapter(adapter_id="freeform2", payload=FREE_TEXT)
        )
        row = self.create(adapter_id="freeform2")
        row = self.service.refresh_task(row.task_id)
        pending = self.pending(row.task_id)
        self.service.answer_clarification(
            row.task_id,
            pending.question_id,
            {"answer": "a-very-distinctive-answer"},
            source=clar.SOURCE_INTERNAL_TEST,
        )
        blob = json.dumps(
            [event.to_dict() for event in self.store.events(row.task_id, limit=200)],
            ensure_ascii=False,
        )
        self.assertNotIn("a-very-distinctive-answer", blob)
        self.assertIn("Answer received", blob)

    # -- refusals ------------------------------------------------------------

    def test_the_same_question_cannot_be_answered_twice(self) -> None:
        row = self.ask()
        pending = self.pending(row.task_id)
        self.service.answer_clarification(
            row.task_id,
            pending.question_id,
            {"option_ids": ["opt1"]},
            source=clar.SOURCE_INTERNAL_TEST,
        )
        with self.assertRaises(task_errors.ClarificationClosed):
            self.service.answer_clarification(
                row.task_id,
                pending.question_id,
                {"option_ids": ["opt2"]},
                source=clar.SOURCE_INTERNAL_TEST,
            )
        self.assertEqual(len(self.agent.answers), 1)

    def test_an_unknown_question_id_is_refused(self) -> None:
        row = self.ask()
        with self.assertRaises(task_errors.ClarificationUnknown):
            self.service.answer_clarification(
                row.task_id,
                clar.new_question_id(),
                {"answer": "x"},
                source=clar.SOURCE_INTERNAL_TEST,
            )

    def test_a_malformed_question_id_is_refused_before_any_lookup(self) -> None:
        row = self.ask()
        for bad in ("", "q_", "not-an-id", 5, None, "q_" + "z" * 24):
            with self.subTest(value=bad):
                with self.assertRaises(task_errors.ClarificationUnknown):
                    self.service.answer_clarification(
                        row.task_id,
                        bad,
                        {"answer": "x"},
                        source=clar.SOURCE_INTERNAL_TEST,
                    )

    def test_a_question_cannot_be_answered_through_another_task(self) -> None:
        """The scoping is in the query, so there is nothing to compare wrongly."""
        first = self.ask()
        self.agent.ask_next_inspect = True
        second = self.create(adapter_id="clarifying")
        second = self.service.refresh_task(second.task_id)
        stolen = self.pending(first.task_id).question_id

        with self.assertRaises(task_errors.ClarificationUnknown):
            self.service.answer_clarification(
                second.task_id,
                stolen,
                {"option_ids": ["opt1"]},
                source=clar.SOURCE_INTERNAL_TEST,
            )
        self.assertEqual(self.agent.answers, [])
        self.assertEqual(
            self.store.find_clarification(first.task_id, stolen).status,
            clar.STATUS_PENDING,
        )

    def test_an_answer_that_does_not_fit_the_question_is_refused(self) -> None:
        row = self.ask()
        pending = self.pending(row.task_id)
        for body in (
            {},
            {"answer": ""},
            {"answer": "prose for a choice question"},
            {"option_ids": ["opt9"]},
            {"option_ids": ["opt1", "opt2"]},
            {"option_ids": ["opt1", "opt1"]},
            {"option_ids": "not a list"},
            {"answer": "x" * (clar.MAX_ANSWER_CHARS + 1), "option_ids": ["opt1"]},
        ):
            with self.subTest(body=body):
                with self.assertRaises(task_errors.ClarificationAnswerInvalid):
                    self.service.answer_clarification(
                        row.task_id,
                        pending.question_id,
                        body,
                        source=clar.SOURCE_INTERNAL_TEST,
                    )
        # Nothing was delivered and the question is still open.
        self.assertEqual(self.agent.answers, [])
        self.assertEqual(self.pending(row.task_id).status, clar.STATUS_PENDING)

    def test_an_undeliverable_answer_leaves_the_question_open(self) -> None:
        """Recording an answer the session never got would be a false success."""
        self.agent = self.install_adapter(
            ClarifyingAdapter(adapter_id="deaf", accept_answer=False)
        )
        row = self.create(adapter_id="deaf")
        row = self.service.refresh_task(row.task_id)
        pending = self.pending(row.task_id)
        with self.assertRaises(task_errors.ClarificationNotDelivered):
            self.service.answer_clarification(
                row.task_id,
                pending.question_id,
                {"option_ids": ["opt1"]},
                source=clar.SOURCE_INTERNAL_TEST,
            )
        self.assertEqual(self.store.get(row.task_id).state, STATE_WAITING_FOR_USER)
        self.assertEqual(self.pending(row.task_id).status, clar.STATUS_PENDING)

    def test_an_adapter_that_does_not_ask_questions_cannot_be_answered(self) -> None:
        row = self.create(adapter_id="validation")
        with self.assertRaises(task_errors.ClarificationUnsupported):
            self.service.answer_clarification(
                row.task_id,
                clar.new_question_id(),
                {"answer": "x"},
                source=clar.SOURCE_INTERNAL_TEST,
            )

    def test_the_future_bridge_source_is_reserved_and_not_accepted(self) -> None:
        """A vocabulary entry is not an enabled surface."""
        self.assertIn(clar.SOURCE_FUTURE_GPT_BRIDGE, clar.ANSWER_SOURCES)
        self.assertNotIn(clar.SOURCE_FUTURE_GPT_BRIDGE, clar.ACCEPTED_ANSWER_SOURCES)
        row = self.ask()
        pending = self.pending(row.task_id)
        with self.assertRaises(task_errors.ClarificationAnswerInvalid):
            self.service.answer_clarification(
                row.task_id,
                pending.question_id,
                {"option_ids": ["opt1"]},
                source=clar.SOURCE_FUTURE_GPT_BRIDGE,
            )

    # -- duplicates ----------------------------------------------------------

    def test_a_repeated_provider_question_event_opens_no_second_question(self) -> None:
        row = self.ask()
        self.agent.repeat_question = True
        self.service.refresh_task(row.task_id)
        self.assertEqual(len(self.service.pending_clarifications(row.task_id)), 1)

    # -- cancellation --------------------------------------------------------

    def test_cancelling_a_waiting_task_closes_its_question(self) -> None:
        row = self.ask()
        pending = self.pending(row.task_id)
        cancelled = self.service.cancel_task(row.task_id)
        self.assertEqual(cancelled.state, STATE_CANCELLED)
        stored = self.store.find_clarification(row.task_id, pending.question_id)
        self.assertEqual(stored.status, clar.STATUS_CANCELLED)
        self.assertEqual(self.service.pending_clarifications(row.task_id), [])

    def test_an_answer_after_a_cancellation_is_refused(self) -> None:
        row = self.ask()
        pending = self.pending(row.task_id)
        self.service.cancel_task(row.task_id)
        with self.assertRaises(
            (task_errors.ClarificationClosed, task_errors.TaskAlreadyFinished)
        ):
            self.service.answer_clarification(
                row.task_id,
                pending.question_id,
                {"option_ids": ["opt1"]},
                source=clar.SOURCE_INTERNAL_TEST,
            )
        self.assertEqual(self.agent.answers, [])

    def test_a_late_result_cannot_resurrect_a_cancelled_task(self) -> None:
        row = self.ask()
        self.service.cancel_task(row.task_id)
        self.agent.finish_next_inspect = True
        refreshed = self.service.refresh_task(row.task_id)
        self.assertEqual(refreshed.state, STATE_CANCELLED)

    def test_a_completed_task_closes_its_question_as_superseded(self) -> None:
        """Different from cancelled, and the difference is worth keeping."""
        row = self.ask()
        pending = self.pending(row.task_id)
        self.agent.finish_next_inspect = True
        # A question left open on a task that finished around it.
        self.service.answer_clarification(
            row.task_id,
            pending.question_id,
            {"option_ids": ["opt1"]},
            source=clar.SOURCE_INTERNAL_TEST,
        )
        done = self.service.refresh_task(row.task_id)
        self.assertEqual(done.state, "completed")
        self.assertEqual(self.service.pending_clarifications(row.task_id), [])

    # -- restart -------------------------------------------------------------

    def test_a_restart_while_waiting_is_reported_as_interrupted(self) -> None:
        row = self.ask()
        pending = self.pending(row.task_id)
        service = self.restart()
        settled = service.recover_after_restart()
        self.assertEqual([item.task_id for item in settled], [row.task_id])
        self.assertEqual(self.store.get(row.task_id).state, STATE_INTERRUPTED)
        stored = self.store.find_clarification(row.task_id, pending.question_id)
        self.assertEqual(stored.status, clar.STATUS_SUPERSEDED)

    def test_a_question_is_not_answerable_after_a_restart(self) -> None:
        """The honest half: the process that asked is gone, so nothing can reach it.

        The adapter is re-installed after the restart — a fresh instance with no
        memory, which is the actual situation a daemon comes back to. So the
        refusal below is about the *question's* state, not about a missing
        adapter: the surface is fully wired and the answer is still declined.
        """
        row = self.ask()
        pending = self.pending(row.task_id)
        service = self.restart()
        self.agent = self.install_adapter(ClarifyingAdapter())
        service.recover_after_restart()

        self.assertEqual(self.store.get(row.task_id).state, STATE_INTERRUPTED)
        with self.assertRaises(
            (task_errors.ClarificationClosed, task_errors.TaskAlreadyFinished)
        ):
            self.service.answer_clarification(
                row.task_id,
                pending.question_id,
                {"option_ids": ["opt1"]},
                source=clar.SOURCE_INTERNAL_TEST,
            )
        self.assertEqual(self.agent.answers, [])

    def test_no_adapter_claims_it_can_recover_a_question(self) -> None:
        self.assertFalse(self.agent.capabilities().recover_after_restart)

    def test_an_adapter_that_asks_questions_does_not_also_take_follow_ups(self) -> None:
        """Two channels, and today's adapter has exactly one of them.

        A follow-up is a new instruction; an answer resolves something the agent
        is blocked on. The adapter that asks questions declares ``followup=False``,
        so a follow-up to a waiting task is refused before anything moves.
        """
        capabilities = self.agent.capabilities()
        self.assertTrue(capabilities.clarifications)
        self.assertFalse(capabilities.followup)
        row = self.ask()
        with self.assertRaises(task_errors.FollowupUnsupported):
            self.service.send_followup(row.task_id, "a follow-up")
        self.assertEqual(self.store.get(row.task_id).state, STATE_WAITING_FOR_USER)
        self.assertEqual(len(self.service.pending_clarifications(row.task_id)), 1)

    def test_a_follow_up_would_close_an_open_question_rather_than_orphan_it(self) -> None:
        """The lifecycle stays coherent even for a pairing no adapter has yet.

        Driven through the store's own transition, because no shipped adapter can
        reach this combination — which is precisely why it is worth pinning: the
        guarantee is about the lifecycle, not about today's capability flags.
        """
        row = self.ask()
        pending = self.pending(row.task_id)
        moved = self.store.transition(
            row.task_id,
            STATE_RUNNING,
            event_type="followup_received",
            actor="user",
            source="cofferdam",
            expected_state=STATE_WAITING_FOR_USER,
            close_clarifications=self.service._close_pending(row.task_id, STATE_RUNNING),
        )
        self.assertEqual(moved.state, STATE_RUNNING)
        stored = self.store.find_clarification(row.task_id, pending.question_id)
        self.assertEqual(stored.status, clar.STATUS_SUPERSEDED)
        self.assertEqual(self.service.pending_clarifications(row.task_id), [])

    def extra_adapters(self):
        return ()


# -- 5. provenance -----------------------------------------------------------


class ProvenanceTests(TaskTestCase):
    project_adapters = ("validation", "scripted", "clarifying")

    def setUp(self) -> None:
        super().setUp()
        self.agent = self.install_adapter(ClarifyingAdapter())

    def answered(self):
        row = self.create(adapter_id="clarifying")
        row = self.service.refresh_task(row.task_id)
        pending = self.service.pending_clarifications(row.task_id)[0]
        self.service.answer_clarification(
            row.task_id,
            pending.question_id,
            {"option_ids": ["opt1"]},
            source=clar.SOURCE_INTERNAL_TEST,
        )
        return self.store.find_clarification(row.task_id, pending.question_id)

    def test_every_accepted_answer_records_bounded_provenance(self) -> None:
        stored = self.answered()
        provenance = stored.answer.provenance
        self.assertEqual(provenance.actor, "user")
        self.assertEqual(provenance.source, clar.SOURCE_INTERNAL_TEST)
        self.assertEqual(provenance.outcome, clar.OUTCOME_ACCEPTED)
        self.assertTrue(provenance.received_at)
        self.assertIsNone(provenance.rejection_reason)

    def test_the_provider_and_session_provenance_survive_on_the_record(self) -> None:
        stored = self.answered()
        self.assertEqual(stored.provider, "clarifying")
        self.assertEqual(stored.provider_event_id, "ask_token_1")

    def test_provenance_carries_no_header_token_or_payload(self) -> None:
        blob = json.dumps(self.answered().to_dict(), ensure_ascii=False).lower()
        for forbidden in (
            "authorization",
            "bearer",
            "token",
            "user-agent",
            "cookie",
            "remote_addr",
            "x-forwarded",
        ):
            self.assertNotIn(forbidden, blob)

    def test_a_caller_cannot_choose_how_its_answer_is_attributed(self) -> None:
        """``source`` is a parameter of the service, never a field of the body.

        Asserted against the route's own allowlist as well as here: a body key
        that is not ``answer`` or ``option_ids`` is refused rather than ignored.
        """
        row = self.create(adapter_id="clarifying")
        row = self.service.refresh_task(row.task_id)
        pending = self.service.pending_clarifications(row.task_id)[0]
        with self.assertRaises(clar.ClarificationInvalid):
            clar.AnswerProvenance.build(source="something_invented", received_at="now")
        # And the accepted path still records the server's own value.
        self.service.answer_clarification(
            row.task_id,
            pending.question_id,
            {"option_ids": ["opt1"]},
            source=clar.SOURCE_INTERNAL_TEST,
        )
        stored = self.store.find_clarification(row.task_id, pending.question_id)
        self.assertEqual(stored.answer.provenance.source, clar.SOURCE_INTERNAL_TEST)

    def test_a_rejected_answer_is_audited_without_its_text(self) -> None:
        row = self.create(adapter_id="clarifying")
        row = self.service.refresh_task(row.task_id)
        pending = self.service.pending_clarifications(row.task_id)[0]
        with self.assertRaises(task_errors.ClarificationAnswerInvalid):
            self.service.answer_clarification(
                row.task_id,
                pending.question_id,
                {"answer": "a-very-distinctive-rejected-answer"},
                source=clar.SOURCE_INTERNAL_TEST,
            )
        blob = self.audit_blob()
        self.assertNotIn("a-very-distinctive-rejected-answer", blob)
        self.assertIn(clar.OUTCOME_REJECTED, blob)

    def test_the_prompt_never_reaches_the_audit_through_this_path(self) -> None:
        row = self.create(adapter_id="clarifying", prompt=TURKISH_PROMPT)
        row = self.service.refresh_task(row.task_id)
        pending = self.service.pending_clarifications(row.task_id)[0]
        self.service.answer_clarification(
            row.task_id,
            pending.question_id,
            {"option_ids": ["opt1"]},
            source=clar.SOURCE_INTERNAL_TEST,
        )
        self.assertNotIn(TURKISH_PROMPT, self.audit_blob())


# -- 6. encoding what the provider sees --------------------------------------


class AnswerEncodingTests(unittest.TestCase):
    """The one place text that arrived over the network becomes text a model acts on."""

    def pending(self, payload: Dict[str, Any]) -> clar.PendingClarification:
        return clar.build_pending(
            task_id="task_x",
            provider="p",
            request=clarification_request(payload),
            requested_at="now",
        )

    def answer(self, pending, body) -> clar.ClarificationAnswer:
        return clar.ClarificationAnswer.from_request(
            body,
            clarification=pending,
            provenance=clar.AnswerProvenance.build(
                source=clar.SOURCE_INTERNAL_TEST, received_at="now"
            ),
        )

    def test_a_choice_is_encoded_from_the_stored_label_not_a_client_string(self) -> None:
        pending = self.pending(SINGLE_CHOICE)
        encoded = clar.encode_answer(pending, self.answer(pending, {"option_ids": ["opt1"]}))
        self.assertEqual(encoded, "Selected: main")

    def test_several_choices_are_joined_by_cofferdam(self) -> None:
        pending = self.pending(MULTIPLE_CHOICE)
        encoded = clar.encode_answer(
            pending, self.answer(pending, {"option_ids": ["opt1", "opt2"]})
        )
        self.assertEqual(encoded, "Selected: README, CHANGELOG")

    def test_free_text_is_passed_through_unaltered(self) -> None:
        pending = self.pending(FREE_TEXT)
        encoded = clar.encode_answer(
            pending, self.answer(pending, {"answer": "call it --dry-run"})
        )
        self.assertEqual(encoded, "call it --dry-run")

    def test_the_encoder_reads_only_two_fields_of_an_answer(self) -> None:
        """Structural: there is no template, no format string from a payload."""
        import inspect

        source = inspect.getsource(clar.encode_answer)
        self.assertNotIn(".format(", source)
        self.assertNotIn("% ", source)
        self.assertNotIn("eval", source)

    def test_an_answer_cannot_be_empty(self) -> None:
        pending = self.pending(FREE_TEXT)
        with self.assertRaises(clar.ClarificationInvalid):
            self.answer(pending, {"answer": "   "})


# -- 7. the helper protocol --------------------------------------------------


class ProtocolTests(unittest.TestCase):
    def test_an_event_survives_a_round_trip_through_the_pipe(self) -> None:
        original = build_event(
            kind=KIND_CLARIFICATION_REQUESTED,
            provider="claude-agent-sdk",
            provider_sequence=7,
            observed_at="2026-08-09T00:00:00Z",
            provider_session_id="sess-1",
            provider_event_id="ask_abcdef",
            text="Which branch should this land on?",
            clarification=clarification_request(SINGLE_CHOICE),
        )
        line = hostproto.encode_line(hostproto.event_payload(original))
        parsed = hostproto.decode_line(line)
        rebuilt = hostproto.event_from_payload(parsed["event"])
        self.assertIsNotNone(rebuilt)
        self.assertEqual(rebuilt.kind, original.kind)
        self.assertEqual(rebuilt.provider_event_id, "ask_abcdef")
        self.assertEqual(rebuilt.clarification.question, original.clarification.question)
        self.assertEqual(
            [o.option_id for o in rebuilt.clarification.options], ["opt1", "opt2"]
        )
        self.assertIsNone(rebuilt.approval)

    def test_a_turkish_question_survives_the_pipe(self) -> None:
        original = build_event(
            kind=KIND_CLARIFICATION_REQUESTED,
            provider="p",
            provider_sequence=1,
            observed_at="now",
            clarification=clarification_request(TURKISH_QUESTION),
        )
        line = hostproto.encode_line(hostproto.event_payload(original))
        # ASCII on the wire, characters at both ends.
        self.assertEqual(line.encode("ascii", "strict").decode("ascii"), line)
        rebuilt = hostproto.event_from_payload(
            hostproto.decode_line(line)["event"]
        )
        self.assertEqual(rebuilt.clarification.question, "Hangi dosyayı düzenlemeli?")

    def test_an_oversized_line_is_refused(self) -> None:
        self.assertIsNone(hostproto.decode_line("x" * (hostproto.MAX_LINE_BYTES + 1)))

    def test_a_line_from_a_different_protocol_version_is_refused(self) -> None:
        self.assertIsNone(hostproto.decode_line(json.dumps({"v": 99, "message": "ready"})))

    def test_malformed_lines_are_dropped_rather_than_raising(self) -> None:
        for raw in ("", "   ", "{", "[]", "null", "not json", 5, None):
            with self.subTest(raw=raw):
                self.assertIsNone(hostproto.decode_line(raw))

    def test_an_event_that_will_not_rebuild_is_refused(self) -> None:
        for payload in (
            None,
            {},
            {"kind": "invented"},
            {"kind": KIND_CLARIFICATION_REQUESTED},  # a clarification with no request
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(hostproto.event_from_payload(payload))

    def test_a_request_carrying_a_tool_field_cannot_cross_the_pipe(self) -> None:
        """The separation holds over the wire too, not only in memory."""
        payload = {
            "kind": KIND_CLARIFICATION_REQUESTED,
            "provider": "p",
            "provider_sequence": 1,
            "observed_at": "now",
            "request": {
                "category": CATEGORY_CLARIFICATION,
                "question": "may I?",
                "tool_name": "Bash",
            },
        }
        self.assertIsNone(hostproto.event_from_payload(payload))

    def test_the_command_vocabulary_carries_nothing_executable(self) -> None:
        """No command names a path, an executable, an environment or a tool."""
        self.assertEqual(
            set(hostproto.COMMANDS), {"start", "answer", "cancel", "close"}
        )
        built = hostproto.command(
            hostproto.COMMAND_ANSWER, token="ask_1", answer="yes"
        )
        self.assertEqual(set(built), {"v", "command", "token", "answer"})

    def test_an_unknown_command_or_message_name_is_refused(self) -> None:
        with self.assertRaises(hostproto.ProtocolError):
            hostproto.command("exec")
        with self.assertRaises(hostproto.ProtocolError):
            hostproto.message("whatever")


# -- 8. one session, and only its own ----------------------------------------


class SameSessionTests(unittest.TestCase):
    """The parent half of the helper boundary, driven against a protocol double.

    No process, no SDK, no network. What is proven is the routing: an answer
    reaches the session that asked, an unrelated session is untouched, the
    provider session id does not change because nothing was torn down, and a
    close actually releases the child.
    """

    def session(self, **kwargs: Any):
        from cofferdam.workstation.tasks.adapters.claude_agent_sdk.hostclient import (
            HostSession,
        )

        from ._agent_sdk_doubles import FakeHelperProcess

        helper = FakeHelperProcess(**kwargs)
        session = HostSession(
            task_id="task_" + kwargs.get("session_id", "sess-1"),
            project_root=Path("/srv/project"),
            launcher=lambda **_: helper,
        )
        self.addCleanup(helper.exit)
        return session, helper

    def started(self, **kwargs: Any):
        session, helper = self.session(**kwargs)
        session.start("do the thing")
        return session, helper

    def test_a_start_waits_for_both_acknowledgements(self) -> None:
        session, helper = self.started()
        self.assertEqual(session.provider_session_id, "sess-1")
        commands = [hostproto.decode_line(line) for line in helper.received]
        self.assertEqual([c["command"] for c in commands], ["start"])
        self.assertEqual(commands[0]["prompt"], "do the thing")

    def test_a_helper_that_refuses_produces_a_refusal_not_a_running_task(self) -> None:
        from cofferdam.workstation.tasks.adapters.claude_agent_sdk.session import (
            SessionRefused,
        )

        session, _ = self.session(error="the environment was not the one Cofferdam built")
        with self.assertRaises(SessionRefused) as caught:
            session.start("go")
        self.assertIn("environment", str(caught.exception))

    def test_a_question_is_remembered_by_its_own_token(self) -> None:
        session, _ = self.started(question_token="ask_alpha")
        events = session.drain()
        self.assertEqual([e.kind for e in events], [KIND_CLARIFICATION_REQUESTED])
        self.assertEqual(session.pending_question_token, "ask_alpha")

    def test_an_answer_reaches_the_session_that_asked(self) -> None:
        session, helper = self.started(question_token="ask_alpha")
        session.drain()
        self.assertTrue(session.submit_answer("ask_alpha", "Selected: alpha"))
        self.assertEqual(
            helper.answers, [{"token": "ask_alpha", "answer": "Selected: alpha"}]
        )

    def test_an_answer_for_another_token_is_refused_and_not_sent(self) -> None:
        session, helper = self.started(question_token="ask_alpha")
        session.drain()
        self.assertFalse(session.submit_answer("ask_beta", "Selected: alpha"))
        self.assertEqual(helper.answers, [])

    def test_one_question_cannot_be_answered_twice_at_this_layer(self) -> None:
        session, helper = self.started(question_token="ask_alpha")
        session.drain()
        self.assertTrue(session.submit_answer("ask_alpha", "first"))
        self.assertFalse(session.submit_answer("ask_alpha", "second"))
        self.assertEqual(len(helper.answers), 1)

    def test_an_unrelated_session_is_untouched_by_an_answer(self) -> None:
        first, first_helper = self.started(
            session_id="sess-1", question_token="ask_alpha"
        )
        second, second_helper = self.started(
            session_id="sess-2", question_token="ask_beta"
        )
        first.drain()
        second.drain()

        first.submit_answer("ask_alpha", "for the first")
        self.assertEqual(len(first_helper.answers), 1)
        self.assertEqual(second_helper.answers, [])
        self.assertEqual(second.pending_question_token, "ask_beta")

    def test_the_provider_session_id_is_unchanged_by_answering(self) -> None:
        """Continuation is a property of not having torn anything down."""
        session, _ = self.started(question_token="ask_alpha")
        session.drain()
        before = session.provider_session_id
        session.submit_answer("ask_alpha", "Selected: alpha")
        self.assertEqual(session.provider_session_id, before)
        self.assertEqual(session.provider_session_id, "sess-1")

    def test_the_session_id_comes_from_the_provider_not_the_caller(self) -> None:
        """There is no parameter anywhere in this object that could supply one."""
        import inspect

        from cofferdam.workstation.tasks.adapters.claude_agent_sdk.hostclient import (
            HostSession,
        )

        signature = inspect.signature(HostSession.__init__)
        self.assertEqual(
            sorted(signature.parameters),
            ["cli_path", "launcher", "project_root", "self", "task_id"],
        )

    def test_a_cancel_reaches_only_the_owning_helper(self) -> None:
        first, first_helper = self.started(session_id="sess-1")
        second, second_helper = self.started(session_id="sess-2")
        self.assertTrue(first.request_cancel())
        self.assertEqual(first_helper.cancelled, 1)
        self.assertEqual(second_helper.cancelled, 0)

    def test_a_cancel_closes_any_question_that_was_open(self) -> None:
        session, _ = self.started(question_token="ask_alpha")
        session.drain()
        session.request_cancel()
        self.assertIsNone(session.pending_question_token)
        self.assertFalse(session.submit_answer("ask_alpha", "too late"))

    def test_repeated_cancellation_records_one_notice(self) -> None:
        session, helper = self.started()
        session.request_cancel()
        session.request_cancel()
        kinds = [event.kind for event in session.drain()]
        self.assertEqual(kinds.count("cancellation_requested"), 1)
        self.assertEqual(helper.cancelled, 2)

    def test_closing_releases_the_child(self) -> None:
        session, helper = self.started()
        self.assertTrue(session.close())
        self.assertEqual(helper.closed, 1)
        self.assertTrue(session.finished)
        # It did not stop on the close command alone, so the escalation ran —
        # once, and no further.
        self.assertEqual(helper.terminated, 1)
        self.assertEqual(helper.killed, 0)

    def test_a_helper_that_exits_on_its_own_needs_no_escalation(self) -> None:
        session, helper = self.started()
        helper.exit(0)
        self.assertTrue(session.close())
        self.assertEqual(helper.terminated, 0)
        self.assertEqual(helper.killed, 0)

    def test_an_event_from_the_helper_is_rebuilt_through_the_usual_bounds(self) -> None:
        session, helper = self.started()
        helper.emit_event(
            build_event(
                kind="output",
                provider="claude-agent-sdk",
                provider_sequence=2,
                observed_at="2026-08-09T00:00:00Z",
                text="x" * 40000,
            )
        )
        # Give the reader thread the line it is blocked on.
        helper.exit(0)
        session.close()
        events = [event for event in session.drain() if event.kind == "output"]
        self.assertEqual(len(events), 1)
        self.assertLess(len(events[0].text), 40000)


# -- 9. the authenticated routes ---------------------------------------------

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the extras are absent
    TestClient = None

API_TOKEN = "test-device-token-not-a-real-credential"


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class ClarificationApiTests(TaskTestCase):
    """The two routes, over HTTP, against the real service and store.

    The app is built with the *same* ``TaskService`` the lifecycle tests use, so
    what is exercised here is genuinely the surface — authentication, the closed
    body vocabulary, the status codes — rather than a second wiring that happens
    to agree.
    """

    project_adapters = ("validation", "scripted", "clarifying")

    def setUp(self) -> None:
        super().setUp()
        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.service import create_app

        self.agent = self.install_adapter(ClarifyingAdapter())
        self.app = create_app(
            config=self.config,
            token=API_TOKEN,
            adapter=StubAdapter(self.config),
            tasks=self.service,
        )
        self.client = TestClient(self.app)
        self.auth = {"Authorization": "Bearer " + API_TOKEN}

    def waiting(self):
        row = self.create(adapter_id="clarifying")
        row = self.service.refresh_task(row.task_id)
        pending = self.service.pending_clarifications(row.task_id)[0]
        return row.task_id, pending.question_id

    def list_path(self, task_id: str) -> str:
        return "/api/tasks/" + task_id + "/clarifications"

    def answer_path(self, task_id: str, question_id: str) -> str:
        return "/api/tasks/" + task_id + "/clarifications/" + question_id + "/answer"

    # -- authentication ------------------------------------------------------

    def test_both_routes_require_the_device_token(self) -> None:
        task_id, question_id = self.waiting()
        self.assertEqual(self.client.get(self.list_path(task_id)).status_code, 401)
        self.assertEqual(
            self.client.post(self.answer_path(task_id, question_id), json={}).status_code,
            401,
        )

    def test_a_wrong_token_is_refused(self) -> None:
        task_id, _ = self.waiting()
        response = self.client.get(
            self.list_path(task_id), headers={"Authorization": "Bearer wrong"}
        )
        self.assertEqual(response.status_code, 401)

    # -- listing -------------------------------------------------------------

    def test_the_list_publishes_bounded_normalized_questions(self) -> None:
        task_id, question_id = self.waiting()
        payload = self.client.get(self.list_path(task_id), headers=self.auth).json()
        self.assertEqual(payload["state"], STATE_WAITING_FOR_USER)
        self.assertEqual(payload["waiting_reason"], WAITING_CLARIFICATION)
        self.assertEqual(len(payload["clarifications"]), 1)
        item = payload["clarifications"][0]
        self.assertEqual(item["question_id"], question_id)
        self.assertEqual(item["category"], CATEGORY_CLARIFICATION)
        self.assertEqual(item["answer_mode"], ANSWER_MODE_SINGLE_CHOICE)
        self.assertEqual([o["option_id"] for o in item["options"]], ["opt1", "opt2"])
        self.assertFalse(item["schema_verified"])

    def test_the_list_publishes_no_session_id_tool_or_path(self) -> None:
        task_id, _ = self.waiting()
        blob = self.client.get(self.list_path(task_id), headers=self.auth).text
        for forbidden in (
            "provider_session_id",
            "provider_event_id",
            "tool_name",
            "tool_input",
            "cwd",
            "argv",
            str(self.project_root),
        ):
            self.assertNotIn(forbidden, blob)

    def test_an_unknown_task_is_a_not_found(self) -> None:
        response = self.client.get(self.list_path("task_nope"), headers=self.auth)
        self.assertEqual(response.status_code, 404)

    # -- answering -----------------------------------------------------------

    def test_an_answer_returns_the_task_to_running(self) -> None:
        task_id, question_id = self.waiting()
        response = self.client.post(
            self.answer_path(task_id, question_id),
            json={"option_ids": ["opt1"]},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["task"]["state"], STATE_RUNNING)
        self.assertEqual(self.agent.answers, ["Selected: main"])

    def test_the_body_vocabulary_is_closed(self) -> None:
        """Refused, not filtered. Each of these would change what the route is.

        The tool-shaped fields are the point: a body that tries to turn an answer
        into a permission decision is rejected at the route before anything reads
        it, and rejected again by name inside the answer constructor.
        """
        task_id, question_id = self.waiting()
        for intruder in (
            {"option_ids": ["opt1"], "tool_name": "Bash"},
            {"option_ids": ["opt1"], "approval_id": "a1"},
            {"option_ids": ["opt1"], "behavior": "allow"},
            {"option_ids": ["opt1"], "decision": "allow"},
            {"option_ids": ["opt1"], "command": "rm -rf /"},
            {"option_ids": ["opt1"], "permission_mode": "bypassPermissions"},
            {"option_ids": ["opt1"], "cwd": "/etc"},
            {"option_ids": ["opt1"], "env": {"X": "1"}},
            {"option_ids": ["opt1"], "session_id": "sess-1"},
            {"option_ids": ["opt1"], "source": "future_gpt_bridge"},
            {"option_ids": ["opt1"], "project_id": "demo"},
            {"option_ids": ["opt1"], "origin": "cli"},
        ):
            with self.subTest(body=sorted(intruder)):
                response = self.client.post(
                    self.answer_path(task_id, question_id),
                    json=intruder,
                    headers=self.auth,
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "invalid_params")
        self.assertEqual(self.agent.answers, [])

    def test_answering_twice_is_a_conflict(self) -> None:
        task_id, question_id = self.waiting()
        first = self.client.post(
            self.answer_path(task_id, question_id),
            json={"option_ids": ["opt1"]},
            headers=self.auth,
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            self.answer_path(task_id, question_id),
            json={"option_ids": ["opt2"]},
            headers=self.auth,
        )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(
            second.json()["error"]["code"], "task_clarification_closed"
        )

    def test_an_unknown_question_is_a_not_found(self) -> None:
        task_id, _ = self.waiting()
        response = self.client.post(
            self.answer_path(task_id, clar.new_question_id()),
            json={"option_ids": ["opt1"]},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["error"]["code"], "task_clarification_unknown"
        )

    def test_an_answer_that_does_not_fit_is_unprocessable(self) -> None:
        task_id, question_id = self.waiting()
        response = self.client.post(
            self.answer_path(task_id, question_id),
            json={"answer": "prose for a choice question"},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["error"]["code"], "task_clarification_invalid"
        )

    def test_a_rejected_answer_is_never_echoed_back(self) -> None:
        task_id, question_id = self.waiting()
        response = self.client.post(
            self.answer_path(task_id, question_id),
            json={"answer": "a-very-distinctive-rejected-answer"},
            headers=self.auth,
        )
        self.assertNotIn("a-very-distinctive-rejected-answer", response.text)

    def test_an_oversized_body_fails_before_anything_runs(self) -> None:
        task_id, question_id = self.waiting()
        response = self.client.post(
            self.answer_path(task_id, question_id),
            content=json.dumps({"answer": "x" * 200000}),
            headers={**self.auth, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.agent.answers, [])

    def test_a_non_json_body_is_refused(self) -> None:
        task_id, question_id = self.waiting()
        response = self.client.post(
            self.answer_path(task_id, question_id),
            content="answer=yes",
            headers={**self.auth, "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(response.status_code, 415)

    def test_a_get_on_the_answer_route_is_not_allowed(self) -> None:
        task_id, question_id = self.waiting()
        self.assertIn(
            self.client.get(
                self.answer_path(task_id, question_id), headers=self.auth
            ).status_code,
            (404, 405),
        )

    def test_reading_the_list_does_not_answer_anything(self) -> None:
        task_id, _ = self.waiting()
        for _ in range(3):
            self.client.get(self.list_path(task_id), headers=self.auth)
        self.assertEqual(self.agent.answers, [])
        self.assertEqual(len(self.service.pending_clarifications(task_id)), 1)

    def test_there_is_no_approval_route_at_all(self) -> None:
        task_id, question_id = self.waiting()
        for path in (
            "/api/tasks/" + task_id + "/approvals",
            "/api/tasks/" + task_id + "/approvals/" + question_id + "/answer",
            "/api/tasks/" + task_id + "/clarifications/" + question_id + "/approve",
            "/api/approvals",
        ):
            with self.subTest(path=path):
                self.assertIn(
                    self.client.post(path, json={}, headers=self.auth).status_code,
                    (404, 405),
                    path,
                )

    def extra_adapters(self):
        return ()


# -- 10. the helper's own boundary -------------------------------------------


class HelperSourceTests(unittest.TestCase):
    def test_the_helper_prints_nothing_and_configures_no_logging(self) -> None:
        """stdout is the protocol. Anything printed would corrupt a frame."""
        source = python_code_only((SDK_PACKAGE / "host.py").read_text("utf-8"))
        for forbidden in ("print(", "logging", "logger", "basicConfig"):
            self.assertNotIn(forbidden, source)

    def test_the_helper_verifies_its_own_environment_before_the_sdk(self) -> None:
        """Both halves of the guarantee: the parent passes it, the child checks it."""
        tree = ast.parse((SDK_PACKAGE / "host.py").read_text("utf-8"))
        checked = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and ast.unparse(node.func).endswith("verify_child_environment")
        ]
        self.assertEqual(len(checked), 1)
        self.assertEqual(ast.unparse(checked[0].args[0]), "os.environ")

    def test_the_helper_takes_no_command_line_argument(self) -> None:
        """Everything it needs arrives as cwd, environment, or one protocol line."""
        from cofferdam.workstation.tasks.adapters.claude_agent_sdk import host

        self.assertEqual(host.main(["--anything"]), host.EXIT_PROTOCOL)

    def test_the_project_root_reaches_the_helper_as_a_working_directory(self) -> None:
        """Never parsed out of a string on the channel."""
        source = python_code_only((SDK_PACKAGE / "host.py").read_text("utf-8"))
        self.assertIn("project_root\n=\npath\n.\ncwd\n(\n)", source.lower())

    def test_no_module_in_the_package_names_a_raw_payload(self) -> None:
        for path in sorted(SDK_PACKAGE.rglob("*.py")):
            source = python_code_only(path.read_text("utf-8"))
            for word in ("raw_payload", "provider_payload", "raw_message"):
                with self.subTest(path=path.name, word=word):
                    self.assertNotIn(word, source)

    def test_the_tool_input_exists_only_where_the_sdk_forces_it_to(self) -> None:
        """``tool_input`` is a parameter of the SDK's callback, and nothing else.

        The SDK's ``can_use_tool`` signature requires the name, so the word has
        to exist somewhere. What matters is that it exists in exactly one file,
        as a **parameter**, and that the only things it is ever handed to are the
        two conservative readers in :mod:`.question` — which return names, types
        and counts, or bounded sanitized text, and never the value itself.
        """
        readers = {"question_reader.observe", "question_reader.read_question"}
        seen: List[str] = []
        for path in sorted(SDK_PACKAGE.rglob("*.py")):
            tree = ast.parse(path.read_text("utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "tool_input":
                    seen.append(path.name)
                # It is never subscripted, never has an attribute read off it,
                # and is never assigned to anything.
                if isinstance(node, ast.Subscript) and ast.unparse(node.value) == "tool_input":
                    self.fail(path.name + " subscripts the tool input")
                if isinstance(node, ast.Attribute) and ast.unparse(node.value) == "tool_input":
                    self.fail(path.name + " reads an attribute off the tool input")
                if isinstance(node, ast.Call) and "tool_input" in [
                    ast.unparse(argument) for argument in node.args
                ]:
                    with self.subTest(call=ast.unparse(node.func)):
                        self.assertIn(
                            ast.unparse(node.func),
                            readers | {"self._handle_question"},
                        )
        self.assertEqual(set(seen), {"session.py"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
