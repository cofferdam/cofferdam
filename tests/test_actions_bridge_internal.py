"""The internal client: the SSRF boundary, asserted structurally and on the wire.

Two kinds of test here, and both are needed.

**On the wire.** A stub ``httpx`` transport records the exact request the client
built — method, URL, headers, body — so the assertions are about what actually
leaves the process rather than about what the code appears to say.

**Structurally.** Some of the properties are absences, and an absence cannot be
observed by making a call. That a caller-supplied URL is impossible is a fact
about the *signature* of ten methods, and it is asserted by reading them.
"""

from __future__ import annotations

import inspect
import json
import unittest
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover - the extras are absent
    httpx = None

if httpx is not None:
    from cofferdam.actions_bridge import internal as internal_module
    from cofferdam.actions_bridge.config import (
        BridgeConfigError,
        load_bridge_config,
    )
    from cofferdam.actions_bridge.errors import BridgeError, from_upstream_code
    from cofferdam.actions_bridge.internal import (
        ALLOWED_UPSTREAM_ROUTES,
        InternalTaskClient,
    )

TOKEN = "internal-token-not-a-real-credential-0001"
TASK_ID = "task_01k0000000000000000000000a"
QUESTION_ID = "q_" + "ab12cd34ef56" * 2


@unittest.skipIf(httpx is None, "workstation extras are not installed")
class Recorder:
    """A transport that records requests and replays a scripted response."""

    def __init__(self) -> None:
        self.requests = []
        self.status = 200
        self.payload = {"ok": True}
        self.body = None

    def transport(self):
        def handler(request):
            self.requests.append(request)
            content = (
                self.body
                if self.body is not None
                else json.dumps(self.payload).encode("utf-8")
            )
            return httpx.Response(
                self.status, content=content, headers={"content-type": "application/json"}
            )

        return httpx.MockTransport(handler)

    @property
    def last(self):
        return self.requests[-1]


@unittest.skipIf(httpx is None, "workstation extras are not installed")
class InternalClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recorder = Recorder()
        self.client = InternalTaskClient(
            base_url="http://127.0.0.1:7101",
            token=TOKEN,
            timeout=1.0,
            transport=self.recorder.transport(),
        )
        self.addCleanup(self.client.close)

    # -- the wire ------------------------------------------------------------

    def test_every_call_carries_the_internal_bearer_and_nothing_else(self) -> None:
        self.client.list_projects()
        headers = self.recorder.last.headers
        self.assertEqual(headers["authorization"], "Bearer " + TOKEN)
        self.assertEqual(headers["accept"], "application/json")
        # No caller-derived header made it in. `user-agent`, `host`,
        # `connection` and `accept-encoding` are httpx's own.
        interesting = {
            name
            for name in headers
            if name
            not in (
                "authorization",
                "accept",
                "user-agent",
                "host",
                "connection",
                "accept-encoding",
                "content-type",
                "content-length",
            )
        }
        self.assertEqual(interesting, set())

    def test_each_operation_hits_exactly_its_allowlisted_route(self) -> None:
        cases = [
            (lambda: self.client.list_projects(), "GET", "/api/task-projects"),
            (lambda: self.client.list_tasks(limit=5), "GET", "/api/tasks"),
            (lambda: self.client.get_task(TASK_ID), "GET", f"/api/tasks/{TASK_ID}"),
            (
                lambda: self.client.get_result(TASK_ID),
                "GET",
                f"/api/tasks/{TASK_ID}/result",
            ),
            (
                lambda: self.client.list_clarifications(TASK_ID),
                "GET",
                f"/api/tasks/{TASK_ID}/clarifications",
            ),
            (
                lambda: self.client.answer_clarification(
                    task_id=TASK_ID, question_id=QUESTION_ID, option_id="opt1"
                ),
                "POST",
                f"/api/tasks/{TASK_ID}/clarifications/{QUESTION_ID}/answer",
            ),
            (
                lambda: self.client.send_followup(
                    task_id=TASK_ID, followup="go", client_request_id="req-00000001"
                ),
                "POST",
                f"/api/tasks/{TASK_ID}/followups",
            ),
            (
                lambda: self.client.cancel_task(TASK_ID),
                "POST",
                f"/api/tasks/{TASK_ID}/cancel",
            ),
            (
                lambda: self.client.finish_task(TASK_ID),
                "POST",
                f"/api/tasks/{TASK_ID}/finish",
            ),
        ]
        for call, method, path in cases:
            with self.subTest(path=path):
                call()
                self.assertEqual(self.recorder.last.method, method)
                self.assertEqual(self.recorder.last.url.path, path)
                self.assertEqual(self.recorder.last.url.host, "127.0.0.1")
                self.assertEqual(self.recorder.last.url.port, 7101)

    def test_the_answer_body_is_one_option_id_and_no_text(self) -> None:
        self.client.answer_clarification(
            task_id=TASK_ID, question_id=QUESTION_ID, option_id="opt2"
        )
        body = json.loads(self.recorder.last.content)
        self.assertEqual(body, {"option_ids": ["opt2"]})

    def test_the_create_body_is_assembled_from_named_parameters(self) -> None:
        self.client.create_task(
            project_id="demo",
            adapter_id=None,
            prompt="do the thing",
            client_request_id="req-00000001",
            title=None,
        )
        body = json.loads(self.recorder.last.content)
        self.assertEqual(
            body,
            {
                "project_id": "demo",
                "prompt": "do the thing",
                "client_request_id": "req-00000001",
            },
        )
        self.assertNotIn("adapter_id", body)
        self.assertNotIn("title", body)

    def test_the_only_query_string_is_a_clamped_integer(self) -> None:
        for asked, expected in ((5, 5), (0, 1), (-3, 1), (9999, 100)):
            with self.subTest(limit=asked):
                self.client.list_tasks(limit=asked)
                self.assertEqual(
                    self.recorder.last.url.query.decode(), f"limit={expected}"
                )

    # -- identifiers ---------------------------------------------------------

    def test_a_malformed_identifier_never_becomes_a_path_segment(self) -> None:
        hostile = [
            "../../api/status",
            "task_x/../../secrets",
            "task_%2e%2e%2fadmin",
            "task_" + "i" * 26,
            "",
            "/api/tasks",
            "task_01k0000000000000000000000a/extra",
        ]
        for value in hostile:
            with self.subTest(value=value):
                with self.assertRaises(BridgeError):
                    self.client.get_task(value)
        self.assertEqual(self.recorder.requests, [])

    def test_a_malformed_question_id_never_becomes_a_path_segment(self) -> None:
        for value in ("../../x", "q_short", "q_" + "z" * 24, "", "q_/../"):
            with self.subTest(value=value):
                with self.assertRaises(BridgeError):
                    self.client.answer_clarification(
                        task_id=TASK_ID, question_id=value, option_id="opt1"
                    )
        self.assertEqual(self.recorder.requests, [])

    def test_a_malformed_project_id_never_reaches_the_body(self) -> None:
        for value in ("../etc", "/abs", "Has Capitals", "x" * 200):
            with self.subTest(value=value):
                with self.assertRaises(BridgeError):
                    self.client.create_task(
                        project_id=value,
                        adapter_id=None,
                        prompt="p",
                        client_request_id="req-00000001",
                        title=None,
                    )
        self.assertEqual(self.recorder.requests, [])

    # -- upstream behaviour --------------------------------------------------

    def test_a_redirect_is_not_followed(self) -> None:
        def handler(request):
            return httpx.Response(302, headers={"location": "http://evil.example/"})

        client = InternalTaskClient(
            base_url="http://127.0.0.1:7101",
            token=TOKEN,
            timeout=1.0,
            transport=httpx.MockTransport(handler),
        )
        self.addCleanup(client.close)
        with self.assertRaises(BridgeError) as caught:
            client.list_projects()
        # A 3xx is >= 400? No — it is not, so it falls through to the JSON read
        # and fails there. Either way it is a bounded error and the client never
        # went to evil.example.
        self.assertIn(
            caught.exception.code, ("upstream_unavailable", "not_allowed_now")
        )

    def test_an_oversized_upstream_response_is_refused_before_parsing(self) -> None:
        self.recorder.body = b'{"padding": "' + b"x" * (200 * 1024) + b'"}'
        with self.assertRaises(BridgeError) as caught:
            self.client.list_projects()
        self.assertEqual(caught.exception.code, "upstream_unavailable")

    def test_a_non_json_upstream_response_is_a_bounded_error(self) -> None:
        self.recorder.body = b"<html>not json</html>"
        with self.assertRaises(BridgeError) as caught:
            self.client.list_projects()
        self.assertEqual(caught.exception.code, "upstream_unavailable")

    def test_an_upstream_refusal_carries_the_code_and_not_the_detail(self) -> None:
        self.recorder.status = 409
        self.recorder.payload = {
            "error": {
                "code": "task_not_waiting_for_input",
                "message": "that is not something this task can do now",
                "detail": "task is running in /home/someone/private/project",
            }
        }
        with self.assertRaises(BridgeError) as caught:
            self.client.get_task(TASK_ID)
        failure = caught.exception
        self.assertEqual(failure.code, "not_allowed_now")
        # The upstream *code* survives, because it is a closed vocabulary with
        # no content in it. The upstream *detail* does not.
        self.assertEqual(failure.detail, "task_not_waiting_for_input")
        self.assertNotIn("/home/", json.dumps(failure.to_payload()))

    def test_a_timeout_says_the_work_may_still_be_running(self) -> None:
        def handler(request):
            raise httpx.ReadTimeout("timed out", request=request)

        client = InternalTaskClient(
            base_url="http://127.0.0.1:7101",
            token=TOKEN,
            timeout=1.0,
            transport=httpx.MockTransport(handler),
        )
        self.addCleanup(client.close)
        with self.assertRaises(BridgeError) as caught:
            client.get_task(TASK_ID)
        self.assertEqual(caught.exception.code, "upstream_timeout")
        self.assertIn("sync", caught.exception.message.lower())

    def test_a_connection_failure_does_not_leak_the_internal_address(self) -> None:
        def handler(request):
            raise httpx.ConnectError(
                "failed connecting to http://127.0.0.1:7101", request=request
            )

        client = InternalTaskClient(
            base_url="http://127.0.0.1:7101",
            token=TOKEN,
            timeout=1.0,
            transport=httpx.MockTransport(handler),
        )
        self.addCleanup(client.close)
        with self.assertRaises(BridgeError) as caught:
            client.list_projects()
        rendered = json.dumps(caught.exception.to_payload())
        self.assertNotIn("127.0.0.1", rendered)
        self.assertNotIn("7101", rendered)

    def test_the_internal_token_never_appears_in_an_error(self) -> None:
        self.recorder.status = 401
        self.recorder.payload = {"error": {"code": "unauthorized", "message": "no"}}
        with self.assertRaises(BridgeError) as caught:
            self.client.list_projects()
        # A refused internal credential is the *bridge's* deployment fault, and
        # telling the caller "unauthorized" would send it to re-enter its own key.
        self.assertEqual(caught.exception.code, "upstream_unavailable")
        self.assertNotIn(TOKEN, json.dumps(caught.exception.to_payload()))


@unittest.skipIf(httpx is None, "workstation extras are not installed")
class StructuralTests(unittest.TestCase):
    """Absences. Asserted by reading the module, because a call cannot show them."""

    OPERATIONS = (
        "list_projects",
        # M2J PR4 — read-only project context.
        "get_project_context",
        "list_tasks",
        "create_task",
        "get_task",
        "get_result",
        "list_clarifications",
        "answer_clarification",
        "send_followup",
        "cancel_task",
        "finish_task",
    )

    def test_there_are_exactly_eleven_public_operations(self) -> None:
        public = {
            name
            for name, value in inspect.getmembers(
                InternalTaskClient, inspect.isfunction
            )
            if not name.startswith("_") and name != "close"
        }
        self.assertEqual(public, set(self.OPERATIONS))

    def test_no_operation_takes_a_url_method_path_or_header(self) -> None:
        forbidden = {
            "url",
            "method",
            "path",
            "headers",
            "header",
            "endpoint",
            "route",
            "host",
            "base_url",
            "params",
            "query",
        }
        for name in self.OPERATIONS:
            with self.subTest(operation=name):
                parameters = set(
                    inspect.signature(getattr(InternalTaskClient, name)).parameters
                )
                self.assertEqual(parameters & forbidden, set())

    def test_there_is_no_generic_request_helper_on_the_public_surface(self) -> None:
        for name in ("request", "get", "post", "call", "fetch", "send"):
            self.assertFalse(
                hasattr(InternalTaskClient, name),
                f"a generic {name}() would be a proxy seam",
            )

    def test_the_package_names_no_upstream_route_outside_the_allowlist(self) -> None:
        """Every ``/api`` literal in the package is one of the nine templates.

        This is what stops an eleventh operation appearing somewhere else in the
        package and quietly reaching a route nobody reviewed.
        """
        import re

        package = Path(internal_module.__file__).parent
        allowed = set(ALLOWED_UPSTREAM_ROUTES)
        pattern = re.compile(r'"(/api[^"]*)"')
        for source in sorted(package.glob("*.py")):
            for match in pattern.findall(source.read_text(encoding="utf-8")):
                with self.subTest(file=source.name, route=match):
                    self.assertIn(match, allowed)

    def test_redirects_are_disabled_and_said_so(self) -> None:
        source = inspect.getsource(InternalTaskClient.__init__)
        self.assertIn("follow_redirects=False", source)

    def test_the_token_is_not_exposed_by_any_attribute_or_method(self) -> None:
        recorder = Recorder()
        client = InternalTaskClient(
            base_url="http://127.0.0.1:7101",
            token=TOKEN,
            timeout=1.0,
            transport=recorder.transport(),
        )
        self.addCleanup(client.close)
        # The private ``_token`` attribute is where the credential lives, by
        # design. What must not exist is a *public* way to read it back: nothing
        # a route handler holds should be able to reach the secret, and nothing
        # that prints the object should print it.
        for name in dir(client):
            if name.startswith("_"):
                continue
            value = getattr(client, name, None)
            with self.subTest(attribute=name):
                self.assertNotEqual(value, TOKEN)
        self.assertNotIn(TOKEN, repr(client))
        # And no method returns it either.
        self.assertNotIn(TOKEN, json.dumps(client.list_projects()))

    def test_the_allowlist_is_exactly_the_daemon_routes_the_bridge_needs(self) -> None:
        self.assertEqual(
            sorted(ALLOWED_UPSTREAM_ROUTES),
            sorted(
                [
                    # M2J PR4 — read-only project context, the one upstream
                    # route whose response is shaped to leave the host.
                    "/api/projects/{project_id}/context",
                    "/api/task-projects",
                    "/api/tasks",
                    "/api/tasks/{task_id}",
                    "/api/tasks/{task_id}/cancel",
                    "/api/tasks/{task_id}/clarifications",
                    "/api/tasks/{task_id}/clarifications/{question_id}/answer",
                    "/api/tasks/{task_id}/finish",
                    "/api/tasks/{task_id}/followups",
                    "/api/tasks/{task_id}/result",
                ]
            ),
        )

    def test_no_remote_control_or_registry_route_is_reachable(self) -> None:
        for forbidden in (
            "/api/remote-control",
            "/api/registries",
            "/api/actions",
            "/api/runtime",
            "/api/status",
            "/api/task-adapters",
            "/api/tasks/{task_id}/events",
            "/api/screenshots",
            "/api/audio",
            "/api/spotify",
            "/api/youtube",
        ):
            with self.subTest(route=forbidden):
                self.assertNotIn(forbidden, ALLOWED_UPSTREAM_ROUTES)

    def test_the_bridge_imports_no_provider_or_task_core_module(self) -> None:
        """The bridge speaks HTTP to Cofferdam. It does not reach inside it.

        No SQLite handle on the task database, no adapter import, no provider
        session object — the whole boundary is one HTTP client, and an import of
        ``cofferdam.workstation`` anywhere in this package would be the first
        step to bypassing it.
        """
        package = Path(internal_module.__file__).parent
        for source in sorted(package.glob("*.py")):
            text = source.read_text(encoding="utf-8")
            for forbidden in (
                "from cofferdam.workstation",
                "import cofferdam.workstation",
                "claude_agent_sdk",
                "claude_code",
                "import sqlite3\nfrom cofferdam",
            ):
                with self.subTest(file=source.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, text)


@unittest.skipIf(httpx is None, "workstation extras are not installed")
class BaseUrlTests(unittest.TestCase):
    """A base URL is an origin. Anything else is a startup failure."""

    def test_an_origin_is_accepted(self) -> None:
        for value in (
            "http://127.0.0.1:7101",
            "https://127.0.0.1:7101",
            "http://localhost:7101/",
        ):
            with self.subTest(value=value):
                config = load_bridge_config(Path("/tmp"), internal_base_url=value)
                self.assertTrue(config.internal_base_url.startswith("http"))
                self.assertFalse(config.internal_base_url.endswith("/"))

    def test_a_path_query_fragment_or_credential_is_refused(self) -> None:
        hostile = [
            "http://127.0.0.1:7101/api",
            "http://127.0.0.1:7101/evil/..",
            "http://127.0.0.1:7101?x=1",
            "http://127.0.0.1:7101#frag",
            "http://user:pass@127.0.0.1:7101",
            "file:///etc/passwd",
            "ftp://127.0.0.1",
            "gopher://127.0.0.1",
            "not a url",
            "//127.0.0.1",
        ]
        for value in hostile:
            with self.subTest(value=value):
                with self.assertRaises(BridgeConfigError):
                    load_bridge_config(Path("/tmp"), internal_base_url=value)


@unittest.skipIf(httpx is None, "workstation extras are not installed")
class ErrorTranslationTests(unittest.TestCase):
    def test_every_task_core_code_maps_to_a_published_code(self) -> None:
        from cofferdam.actions_bridge.errors import ERROR_CODES

        task_core_codes = [
            "task_project_unknown",
            "task_project_disabled",
            "task_project_root_invalid",
            "task_adapter_unknown",
            "task_adapter_disabled",
            "task_adapter_not_permitted_for_project",
            "task_prompt_invalid",
            "task_followup_invalid",
            "task_request_id_invalid",
            "task_idempotency_conflict",
            "task_unknown",
            "task_illegal_transition",
            "task_already_finished",
            "task_followup_unsupported",
            "task_not_waiting_for_input",
            "task_cancel_unsupported",
            "task_adapter_failed",
            "task_store_unavailable",
            "task_clarification_unknown",
            "task_clarification_closed",
            "task_clarification_invalid",
            "task_clarification_unsupported",
            "task_clarification_not_delivered",
            "task_result_not_ready",
            "task_clarification_pending",
            "task_session_unavailable",
            "task_followup_in_flight",
            "task_turn_limit_reached",
            "unauthorized",
            "invalid_params",
            "not_found",
            "internal_error",
            "something_nobody_has_written_yet",
        ]
        for code in task_core_codes:
            with self.subTest(code=code):
                self.assertIn(from_upstream_code(code), ERROR_CODES)

    def test_an_upstream_unauthorized_is_not_relayed_as_unauthorized(self) -> None:
        self.assertEqual(from_upstream_code("unauthorized"), "upstream_unavailable")

    def test_an_unknown_code_falls_through_to_not_allowed_now(self) -> None:
        self.assertEqual(from_upstream_code("brand_new_code"), "not_allowed_now")
        self.assertEqual(from_upstream_code(None), "not_allowed_now")

    def test_an_unknown_bridge_code_cannot_be_raised(self) -> None:
        with self.assertRaises(ValueError):
            BridgeError(code="made_up", message="x")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
