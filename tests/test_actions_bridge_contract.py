"""The published contract must describe the running bridge, and nothing else.

Three documents have to agree, and each pair can drift in a way nobody notices:

* the **routes** FastAPI actually serves,
* the **OpenAPI schema** pasted into the GPT editor,
* the **operator instructions** the model is given.

A route missing from the schema is a capability nobody reviewed. A schema entry
with no route is an Action that 404s in front of the user. An instruction that
names an Action which does not exist is a model improvising.

This file also holds the checks that stop a *secret or a machine-specific value*
being committed into either document — the two files in this repository that are
literally designed to be copied somewhere else.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - the extras are absent
    yaml = None

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

REPO = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO / "docs" / "custom-gpt" / "openapi.yaml"
INSTRUCTIONS_PATH = REPO / "docs" / "custom-gpt" / "INSTRUCTIONS.md"
BRIDGE_DOC_PATH = REPO / "docs" / "ACTIONS_BRIDGE.md"


def _load_schema():
    return yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))


@unittest.skipIf(yaml is None, "PyYAML is not installed (dev extra)")
class SchemaValidityTests(unittest.TestCase):
    def test_the_schema_parses(self) -> None:
        schema = _load_schema()
        self.assertEqual(schema["openapi"], "3.1.0")
        self.assertIn("info", schema)
        self.assertIn("paths", schema)

    def test_the_schema_validates_against_an_openapi_validator(self) -> None:
        try:
            from openapi_spec_validator import validate
        except ImportError:  # pragma: no cover - optional dev dependency
            self.skipTest("openapi-spec-validator is not installed (dev extra)")
        validate(_load_schema())

    def test_the_declared_version_is_the_one_openai_documents(self) -> None:
        """3.1.0, verified 2026-08-09 from OpenAI's Getting started page."""
        self.assertEqual(_load_schema()["openapi"], "3.1.0")

    def test_every_operation_has_a_unique_operation_id(self) -> None:
        schema = _load_schema()
        seen = []
        for item in schema["paths"].values():
            for method, operation in item.items():
                if method in ("parameters", "summary", "description"):
                    continue
                self.assertIn("operationId", operation)
                seen.append(operation["operationId"])
        self.assertEqual(len(seen), len(set(seen)), "duplicate operationId")

    def test_descriptions_stay_inside_openai_stated_limits(self) -> None:
        """<= 300 chars for an operation, <= 700 for a parameter.

        From OpenAI's production notes, retrieved 2026-08-09. Over the limit the
        description is truncated on their side, which silently removes exactly
        the guidance an Action description exists to give.
        """
        schema = _load_schema()
        for path, item in schema["paths"].items():
            for method, operation in item.items():
                if method in ("parameters", "summary", "description"):
                    continue
                with self.subTest(path=path, method=method):
                    self.assertLessEqual(len(operation.get("description", "")), 300)
                    self.assertLessEqual(len(operation.get("summary", "")), 300)
                for parameter in operation.get("parameters", []):
                    if "$ref" in parameter:
                        continue
                    with self.subTest(path=path, parameter=parameter.get("name")):
                        self.assertLessEqual(
                            len(parameter.get("description", "")), 700
                        )
        for name, parameter in schema["components"].get("parameters", {}).items():
            with self.subTest(component=name):
                self.assertLessEqual(len(parameter.get("description", "")), 700)

    def test_bearer_authentication_is_declared_and_required(self) -> None:
        schema = _load_schema()
        scheme = schema["components"]["securitySchemes"]["bridgeKey"]
        self.assertEqual(scheme["type"], "http")
        self.assertEqual(scheme["scheme"], "bearer")
        self.assertEqual(schema["security"], [{"bridgeKey": []}])

    def test_only_health_opts_out_of_authentication(self) -> None:
        schema = _load_schema()
        anonymous = [
            operation["operationId"]
            for item in schema["paths"].values()
            for method, operation in item.items()
            if method not in ("parameters", "summary", "description")
            and operation.get("security") == []
        ]
        self.assertEqual(anonymous, ["bridgeHealth"])

    def test_every_write_operation_is_marked_consequential(self) -> None:
        """``x-openai-isConsequential: true`` forces a confirmation prompt.

        The default would already be true for a POST, but it is written out on
        every one of them: an implicit default is a default a future OpenAI
        change could move, and these five Actions start agents, answer questions
        and stop work.
        """
        schema = _load_schema()
        for path, item in schema["paths"].items():
            for method, operation in item.items():
                if method not in ("get", "post"):
                    continue
                with self.subTest(path=path, method=method):
                    flag = operation.get("x-openai-isConsequential")
                    self.assertIsNotNone(flag, "the flag must be explicit")
                    self.assertEqual(flag, method == "post")

    @unittest.skipIf(TestClient is None, "workstation extras are not installed")
    def test_the_consequential_set_matches_the_code(self) -> None:
        """Exactly the five mutations are marked, and nothing else is.

        A read marked consequential would make ChatGPT ask before every sync,
        which trains somebody to click through confirmations. A write left
        unmarked would rely on OpenAI's implicit POST default — and this is the
        surface where that default is doing security work.
        """
        from cofferdam.actions_bridge.service import MUTATIONS

        schema = _load_schema()
        marked = {
            operation["operationId"]
            for item in schema["paths"].values()
            for method, operation in item.items()
            if method in ("get", "post")
            and operation.get("x-openai-isConsequential") is True
        }
        self.assertEqual(marked, set(MUTATIONS))

    def test_request_schemas_are_closed(self) -> None:
        """``additionalProperties: false`` on every request body schema."""
        schema = _load_schema()
        request_schemas = [
            "CreateTaskRequest",
            "AnswerRequest",
            "FollowupRequest",
            "CancelRequest",
            "FinishRequest",
            # M2M PR4. Closed for the same reason and one more: the workstation
            # builds this request's project context from its own state, so a
            # property this schema admitted would be a caller influencing what
            # leaves the host.
            "CreateDevelopmentRequest",
        ]
        for name in request_schemas:
            with self.subTest(schema=name):
                definition = schema["components"]["schemas"][name]
                self.assertFalse(definition["additionalProperties"])
                self.assertIn("required", definition)
                self.assertIn("client_request_id", definition["required"])

    def test_no_request_schema_offers_a_forbidden_field(self) -> None:
        schema = _load_schema()
        forbidden = {
            "path",
            "cwd",
            "working_directory",
            "project_root",
            "command",
            "argv",
            "shell",
            "env",
            "environment",
            "executable",
            "model",
            "effort",
            "tools",
            "tool_name",
            "tool_input",
            "permission_mode",
            "permissions",
            "budget",
            "approval_id",
            "approve",
            "allow",
            "deny",
            "decision",
            "behavior",
            "provider_session_id",
            "session_id",
            "resume",
            "mcp_config",
            "hooks",
            "answer",
            "text",
            "option_ids",
            "signal",
            "pid",
            # M2M PR4. The planner-side primitives, listed here so the same
            # single assertion covers both surfaces. A development request that
            # could name one of these would be the caller deciding what the
            # planner sees or what happens to its answer.
            "branch",
            "repo_root",
            "worker_prompt",
            "prompt",
            "planner_action",
            "action",
            "subject_fingerprint",
            "fingerprint",
            "dispatch_id",
            "task_id",
            "publication_id",
            "projection",
            "context",
            "cloud_context",
            "transcript",
            "messages",
            "conversation",
            "memory",
            "vault",
            "provider",
            "approved",
            "auto_approve",
        }
        request_schemas = (
            "CreateTaskRequest",
            "AnswerRequest",
            "FollowupRequest",
            "CancelRequest",
            "FinishRequest",
            "CreateDevelopmentRequest",
        )
        for name in request_schemas:
            definition = schema["components"]["schemas"][name]
            for field in definition.get("properties", {}):
                with self.subTest(schema=name, field=field):
                    self.assertNotIn(field, forbidden)

    def test_the_state_enum_matches_the_normalizer(self) -> None:
        from cofferdam.actions_bridge.normalize import NEXT_OPERATIONS, STATES

        schema = _load_schema()
        self.assertEqual(
            tuple(schema["components"]["schemas"]["TaskState"]["enum"]), STATES
        )
        self.assertEqual(
            tuple(schema["components"]["schemas"]["NextOperation"]["enum"]),
            NEXT_OPERATIONS,
        )

    def test_the_error_enum_matches_the_error_module(self) -> None:
        from cofferdam.actions_bridge.errors import ERROR_CODES

        schema = _load_schema()
        declared = schema["components"]["schemas"]["Error"]["properties"]["error"][
            "properties"
        ]["code"]["enum"]
        self.assertEqual(tuple(declared), ERROR_CODES)

    @unittest.skipIf(TestClient is None, "workstation extras are not installed")
    def test_the_cancel_reason_enum_matches_the_service(self) -> None:
        from cofferdam.actions_bridge.service import CANCEL_REASONS

        schema = _load_schema()
        declared = schema["components"]["schemas"]["CancelRequest"]["properties"][
            "reason"
        ]["enum"]
        self.assertEqual(set(declared), CANCEL_REASONS)

    def test_the_declared_text_bounds_match_the_enforced_ones(self) -> None:
        from cofferdam.actions_bridge.limits import (
            MAX_CLIENT_REQUEST_ID_CHARS,
            MAX_DEVELOPMENT_INSTRUCTION_CHARS,
            MAX_DEVELOPMENT_NOTES_CHARS,
            MAX_EXPECTED_OUTPUT_CHARS,
            MAX_FOLLOWUP_TEXT_CHARS,
            MAX_RECENT_TASKS,
            MAX_TASK_TEXT_CHARS,
            MAX_TITLE_CHARS,
            MIN_CLIENT_REQUEST_ID_CHARS,
        )

        schema = _load_schema()
        create = schema["components"]["schemas"]["CreateTaskRequest"]["properties"]
        self.assertEqual(create["task_text"]["maxLength"], MAX_TASK_TEXT_CHARS)
        self.assertEqual(
            create["expected_output"]["maxLength"], MAX_EXPECTED_OUTPUT_CHARS
        )
        self.assertEqual(create["title"]["maxLength"], MAX_TITLE_CHARS)
        follow = schema["components"]["schemas"]["FollowupRequest"]["properties"]
        self.assertEqual(
            follow["followup_text"]["maxLength"], MAX_FOLLOWUP_TEXT_CHARS
        )
        request_id = schema["components"]["schemas"]["ClientRequestId"]
        self.assertEqual(request_id["minLength"], MIN_CLIENT_REQUEST_ID_CHARS)
        self.assertEqual(request_id["maxLength"], MAX_CLIENT_REQUEST_ID_CHARS)
        limit = schema["paths"]["/v1/tasks"]["get"]["parameters"][0]["schema"]
        self.assertEqual(limit["maximum"], MAX_RECENT_TASKS)
        development = schema["components"]["schemas"]["CreateDevelopmentRequest"][
            "properties"
        ]
        self.assertEqual(
            development["instruction"]["maxLength"],
            MAX_DEVELOPMENT_INSTRUCTION_CHARS,
        )
        self.assertEqual(
            development["research_notes"]["maxLength"], MAX_DEVELOPMENT_NOTES_CHARS
        )

    def test_the_bridge_instruction_bound_matches_the_workstation(self) -> None:
        """One number, two processes. A caller must not be told two limits.

        The bridge refuses first and the workstation refuses independently. If
        the bridge were the looser of the two, a request between the numbers
        would be accepted here, forwarded, and refused there — a round trip and
        a confusing error for something the near side could have answered.
        """
        from cofferdam.actions_bridge.limits import (
            MAX_DEVELOPMENT_INSTRUCTION_CHARS,
            MAX_DEVELOPMENT_NOTES_CHARS,
        )
        from cofferdam.workstation.planner.ingress import (
            MAX_INSTRUCTION_CHARS,
            MAX_RESEARCH_NOTES_CHARS,
        )

        self.assertEqual(MAX_DEVELOPMENT_INSTRUCTION_CHARS, MAX_INSTRUCTION_CHARS)
        self.assertEqual(MAX_DEVELOPMENT_NOTES_CHARS, MAX_RESEARCH_NOTES_CHARS)


@unittest.skipIf(
    yaml is None or TestClient is None, "extras are not installed"
)
class SchemaMatchesRoutesTests(unittest.TestCase):
    """The two documents that must not drift: the schema and the app."""

    def setUp(self) -> None:
        import tempfile

        from cofferdam.actions_bridge.config import load_bridge_config
        from cofferdam.actions_bridge.service import create_bridge_app

        from ._actions_bridge_doubles import FakeInternalClient

        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        config = load_bridge_config(Path(self._home.name))
        self.app = create_bridge_app(
            config, external_key="k" * 32, internal_client=FakeInternalClient()
        )
        self.addCleanup(self.app.state.idempotency.close)
        self.schema = _load_schema()

    def _app_operations(self):
        found = set()
        for route in self.app.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/v1"):
                continue
            for method in getattr(route, "methods", set()):
                if method in ("HEAD", "OPTIONS"):
                    continue
                found.add((method.lower(), path))
        return found

    def _schema_operations(self):
        found = set()
        for path, item in self.schema["paths"].items():
            for method in item:
                if method in ("parameters", "summary", "description"):
                    continue
                found.add((method, path))
        return found

    def test_no_route_is_missing_from_the_schema(self) -> None:
        missing = self._app_operations() - self._schema_operations()
        self.assertEqual(missing, set(), f"routes absent from the schema: {missing}")

    def test_no_schema_entry_lacks_a_route(self) -> None:
        extra = self._schema_operations() - self._app_operations()
        self.assertEqual(extra, set(), f"schema entries with no route: {extra}")

    def test_every_operation_id_maps_to_exactly_one_route(self) -> None:
        from cofferdam.actions_bridge.service import OPERATION_IDS

        declared = [
            operation["operationId"]
            for item in self.schema["paths"].values()
            for method, operation in item.items()
            if method not in ("parameters", "summary", "description")
        ]
        self.assertEqual(sorted(declared), sorted(OPERATION_IDS))

    def test_the_schema_exposes_no_internal_cofferdam_route(self) -> None:
        from cofferdam.actions_bridge.internal import ALLOWED_UPSTREAM_ROUTES

        rendered = SCHEMA_PATH.read_text(encoding="utf-8")
        for path in self.schema["paths"]:
            self.assertTrue(path.startswith("/v1/"), path)
        for internal_route in ALLOWED_UPSTREAM_ROUTES:
            with self.subTest(route=internal_route):
                self.assertNotIn(internal_route, rendered)
        for forbidden in (
            "/api/",
            "/healthz",
            "/ws",
            "remote-control",
            "registries",
            "screenshots",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, rendered)


@unittest.skipIf(yaml is None, "PyYAML is not installed (dev extra)")
class NoSecretsCommittedTests(unittest.TestCase):
    """The two files designed to be copied elsewhere carry nothing real."""

    DOCUMENTS = (SCHEMA_PATH, INSTRUCTIONS_PATH)

    def test_no_real_hostname_or_machine_path_is_committed(self) -> None:
        """By pattern and by this machine's own values, never by literal.

        Hard-coding "the tailnet address" or "the developer's home directory"
        into an assertion would commit the very machine-specific values the
        assertion exists to keep out. So the shapes are matched by regex, and
        the *actual* values are read from the environment at run time — which
        also means this test protects whichever machine it runs on rather than
        the one it was written on.
        """
        import os
        import re

        machine_values = [
            str(Path.home()),
            os.environ.get("USER") or "",
            os.environ.get("HOSTNAME") or "",
        ]
        shapes = [
            # Any absolute home directory, anyone's.
            (r"/home/[A-Za-z0-9._-]+", "an absolute home path"),
            (r"/Users/[A-Za-z0-9._-]+", "an absolute macOS home path"),
            # The CGNAT range Tailscale hands out.
            (r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b", "a tailnet address"),
            (r"[A-Za-z0-9-]+\.ts\.net", "a tailnet hostname"),
            (r"[A-Za-z0-9-]+\.trycloudflare\.com", "a quick-tunnel hostname"),
            (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "an email address"),
        ]
        for path in self.DOCUMENTS:
            text = path.read_text(encoding="utf-8")
            for pattern, what in shapes:
                with self.subTest(file=path.name, shape=what):
                    self.assertIsNone(re.search(pattern, text), what)
            for value in machine_values:
                if len(value) < 4:
                    continue
                with self.subTest(file=path.name, value="<this machine>"):
                    self.assertNotIn(value, text)

    def test_the_server_url_is_an_unmistakable_placeholder(self) -> None:
        schema = _load_schema()
        url = schema["servers"][0]["url"]
        self.assertIn("REPLACE-ME", url)
        self.assertTrue(url.endswith(".invalid"), url)
        self.assertIn("PLACEHOLDER", schema["servers"][0]["description"])

    def test_no_example_contains_a_real_identifier(self) -> None:
        """Task ids, question ids and project ids in examples are invented."""
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        # A real task id is `task_` plus 26 base32 characters. None may appear.
        self.assertIsNone(
            re.search(r"task_[0-9a-hjkmnp-tv-z]{26}", text),
            "a task-id-shaped string is in the schema",
        )
        # A real question id is `q_` plus 24 hex.
        self.assertIsNone(re.search(r"\bq_[0-9a-f]{24}\b", text))

    def test_no_token_shaped_string_is_committed(self) -> None:
        for path in self.DOCUMENTS:
            text = path.read_text(encoding="utf-8")
            for pattern in (
                r"Bearer\s+[A-Za-z0-9_\-]{20,}",
                r"sk-[A-Za-z0-9]{16,}",
                r"[A-Za-z0-9_\-]{43,}=",
            ):
                with self.subTest(file=path.name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, text))


class InstructionsTests(unittest.TestCase):
    """The instructions are part of the security boundary, so they are tested.

    A model does what its instructions say. An instruction file that forgot to
    separate clarifications from approvals, or that promised a notification the
    product cannot send, would be a defect in the product rather than in the
    prose — so the sentences that carry weight are asserted here.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = INSTRUCTIONS_PATH.read_text(encoding="utf-8")
        cls.lowered = cls.text.lower()

    def test_every_action_is_named(self) -> None:
        # Deliberately a literal list rather than an import: this assertion is
        # about the prose, and it must run on a bare interpreter where the
        # bridge's own module (and therefore FastAPI) is not importable.
        for operation in (
            "listProjects",
            "createTask",
            "listRecentTasks",
            "syncTask",
            "submitChoiceAnswer",
            "sendFollowup",
            "cancelTask",
            "finishTask",
        ):
            with self.subTest(operation=operation):
                self.assertIn(operation, self.text)

    @unittest.skipIf(TestClient is None, "workstation extras are not installed")
    def test_no_action_is_named_that_does_not_exist(self) -> None:
        from cofferdam.actions_bridge.service import OPERATION_IDS

        named = set(re.findall(r"`([a-z][a-zA-Z]+)`", self.text))
        camel = {word for word in named if re.search(r"[a-z][A-Z]", word)}
        # `operationId` is OpenAPI vocabulary the instructions explain, not an
        # Action they call. It is the only such word, and naming it here is
        # cheaper than a regex that tries to tell prose from a call site.
        unknown = camel - set(OPERATION_IDS) - {"operationId"}
        self.assertEqual(unknown, set(), f"instructions name unknown Actions: {unknown}")

    def test_clarifications_and_approvals_are_separated(self) -> None:
        self.assertIn("Tool approvals are not clarifications", self.text)
        # Emphasis markers stripped and line breaks collapsed, because these
        # sentences must be present whether or not somebody reflows the file.
        plain = " ".join(self.text.replace("**", "").split())
        self.assertIn("no Action that can approve anything", plain)
        self.assertIn("there is no endpoint", plain)
        self.assertIn("permission to act", self.lowered)
        self.assertIn("asking for *information*", plain)

    def test_guessing_a_task_id_is_forbidden(self) -> None:
        self.assertIn("Never guess or construct a `task_id`", self.text)
        self.assertIn("listRecentTasks", self.text)
        self.assertIn("ambiguous", self.lowered)

    def test_high_impact_decisions_are_never_automatic(self) -> None:
        for phrase in (
            "architecture direction",
            "new dependency",
            "data migration",
            "deletion",
            "production change",
            "public exposure",
            "authentication",
            "permissions",
            "security boundary",
            "irreversible",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.lowered)
        self.assertIn("recommend, then wait", self.lowered)

    def test_the_other_option_case_is_described_correctly(self) -> None:
        self.assertIn('"Other"', self.text)
        self.assertIn("do not", self.lowered)
        self.assertIn("cannot carry", self.lowered)
        # And it must not suggest the two workarounds that would be wrong.
        self.assertIn("appended", self.lowered)
        self.assertIn("close enough", self.lowered)

    def test_no_background_push_is_promised(self) -> None:
        self.assertIn("cannot push", self.lowered)
        self.assertIn("I'll let you know when it's done", self.text)
        # The forbidden promises are quoted as things NOT to say, and the
        # surrounding sentence must be a prohibition.
        index = self.lowered.index("i'll let you know when it's done")
        window = self.lowered[max(0, index - 200) : index]
        self.assertIn("never promise otherwise", window + self.lowered[index : index + 200])

    def test_secrets_are_never_requested(self) -> None:
        self.assertIn("never ask the user for a password", self.lowered)

    def test_the_conventions_are_described_as_conventions(self) -> None:
        self.assertIn("not syntax", self.lowered)
        self.assertIn("@cf sync", self.text)
        self.assertIn("@cf answer", self.text)
        self.assertIn("@cf followup", self.text)
        self.assertIn("@cf cancel", self.text)
        self.assertIn("@cf finish", self.text)
        self.assertIn("@cf recent", self.text)
        self.assertIn("@cf projects", self.text)
        self.assertIn("@cf send", self.text)

    def test_natural_language_equivalents_are_required(self) -> None:
        self.assertIn("Claude ne yaptı?", self.text)
        self.assertIn("must work identically", self.lowered)

    def test_all_fifteen_worked_examples_are_present(self) -> None:
        headings = re.findall(r"^### (\d+)\. ", self.text, flags=re.MULTILINE)
        self.assertEqual([int(number) for number in headings], list(range(1, 16)))

    def test_the_artifact_absence_is_stated(self) -> None:
        self.assertIn("artifacts_supported", self.text)
        self.assertIn("always false", self.lowered)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
