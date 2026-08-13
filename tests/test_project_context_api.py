"""M2J PR4 — the HTTP surfaces, tested on the bytes that would be sent.

Everything here goes through a real app and asserts on a real response body. The
Python-object properties are proved in `test_project_context_read.py`; what this
file exists for is the gap between a correct object and a correct wire payload,
which is where an egress bug actually lives.
"""

from __future__ import annotations

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

import json
import unittest

from .test_workspace_api import (
    PROJECT_ID,
    TOKEN,
    WORKSPACE_ID,
    WorkspaceApiTestCase,
)

ROUTE = "/api/projects/" + PROJECT_ID + "/context"


class ContextApiHarness(WorkspaceApiTestCase):
    """The daemon, with a project, a workspace and real canonical documents."""

    def setUp(self) -> None:
        super().setUp()
        from cofferdam.workstation.config import load_or_create_actions_bridge_token
        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.service import create_app

        config = type(self.config)(
            **{**self.config.__dict__, "enable_actions_bridge_caller": True}
        )
        config.ensure_dirs()
        self.config = config
        self.bridge_token = load_or_create_actions_bridge_token(config)
        self.app = create_app(config=config, token=TOKEN, adapter=StubAdapter(config))
        self.client = TestClient(self.app)
        self.write_documents()

    def write_documents(self):
        (self.project_root / "STATUS.md").write_text(
            "# Status\n\nThe host is up.\n", encoding="utf-8"
        )
        (self.project_root / "ROADMAP.md").write_text(
            "# Roadmap\n\nM2J.\n", encoding="utf-8"
        )
        (self.project_root / "DECISIONS.md").write_text(
            "# Decisions\n\nD-1.\n", encoding="utf-8"
        )
        (self.project_root / "DESIGN.md").write_text(
            "# Design\n\nSENTINEL-DESIGN-BODY\n", encoding="utf-8"
        )
        self.write_workspaces(
            self.config,
            [
                {
                    "workspace_id": WORKSPACE_ID,
                    "display_name": "Demo workspace",
                    "project_id": PROJECT_ID,
                    "documents": {
                        "status": "STATUS.md",
                        "plan": "ROADMAP.md",
                        "decisions": "DECISIONS.md",
                        "design": "DESIGN.md",
                    },
                }
            ],
        )

    def bridge_auth(self):
        return {"Authorization": "Bearer " + self.bridge_token}

    def activate_workspace(self):
        return self.activate()

    def get_context(self, project_id=PROJECT_ID, **kwargs):
        return self.client.get("/api/projects/" + str(project_id) + "/context", **kwargs)


class Authorization(ContextApiHarness):
    """Two credentials reach this route, and neither gains anything else."""

    def test_an_unauthenticated_read_is_refused(self):
        response = self.client.get(ROUTE)
        self.assertEqual(response.status_code, 401)

    def test_a_wrong_token_is_refused(self):
        response = self.get_context(headers={"Authorization": "Bearer wrong"})
        self.assertEqual(response.status_code, 401)

    def test_the_device_token_is_accepted(self):
        self.activate_workspace()
        response = self.get_context(headers=self.auth)
        self.assertEqual(response.status_code, 200)

    def test_the_bridge_credential_reaches_this_route_and_no_mind_route(self):
        """The whole of what PR4 grants the bridge, stated as a test."""
        self.activate_workspace()
        bridge = self.bridge_auth()
        self.assertEqual(self.get_context(headers=bridge).status_code, 200)
        for denied in (
            "/api/mind",
            "/api/mind/proposals",
            "/api/workspace/current",
            "/api/workspaces",
            "/api/registries",
            "/api/actions",
            "/api/status",
        ):
            with self.subTest(route=denied):
                self.assertEqual(
                    self.client.get(denied, headers=bridge).status_code, 401
                )

    def test_the_bridge_credential_cannot_mutate_anything(self):
        self.activate_workspace()
        bridge = self.bridge_auth()
        for method, route, body in (
            ("put", "/api/workspace/objective", {"objective": "hijacked"}),
            ("put", "/api/workspace/active", {"workspace_id": WORKSPACE_ID}),
            ("put", "/api/workspace/context", {"plan_checkpoint": "x"}),
            ("post", "/api/mind/proposals", {}),
        ):
            with self.subTest(route=route):
                response = getattr(self.client, method)(route, json=body, headers=bridge)
                self.assertEqual(response.status_code, 401)

    def test_the_context_route_accepts_no_write_method(self):
        self.activate_workspace()
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(ROUTE, headers=self.auth)
                self.assertEqual(response.status_code, 405)


class Resolution(ContextApiHarness):
    """Fail closed, with a reason, and never a path."""

    def reason(self, response):
        return (response.json() or {}).get("error", {}).get("code")

    def test_no_active_workspace_is_refused(self):
        response = self.get_context(headers=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.reason(response), "workspace_not_active")

    def test_an_unknown_project_is_not_found(self):
        self.activate_workspace()
        response = self.get_context("no-such-project", headers=self.auth)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.reason(response), "project_not_found")

    def test_a_path_shaped_project_id_never_reaches_the_registry(self):
        self.activate_workspace()
        for hostile in ("..", "%2e%2e", "a%2Fb", "a b"):
            with self.subTest(value=hostile):
                response = self.get_context(hostile, headers=self.auth)
                self.assertIn(response.status_code, (404, 422))
                self.assertNotEqual(response.status_code, 200)

    def test_a_traversal_path_does_not_match_the_route_at_all(self):
        self.activate_workspace()
        for hostile in ("/api/projects/../../etc/passwd/context",
                        "/api/projects//context"):
            with self.subTest(path=hostile):
                response = self.client.get(hostile, headers=self.auth)
                self.assertNotEqual(response.status_code, 200)

    def test_no_refusal_body_carries_a_path_or_a_traceback(self):
        self.activate_workspace()
        for value in ("no-such-project", "..", "a b"):
            body = self.get_context(value, headers=self.auth).text
            for fragment in (str(self.home), str(self.project_root), "/home/",
                             "/tmp/", "Traceback", "File \"", ".py\", line"):
                self.assertNotIn(fragment, body)


class WhatCrossesTheWire(ContextApiHarness):
    """Asserted on `response.text` — the actual bytes."""

    def body(self):
        self.activate_workspace()
        response = self.get_context(headers=self.auth)
        self.assertEqual(response.status_code, 200)
        return response.text, response.json()

    def test_the_payload_is_a_projection_not_a_pack(self):
        _, payload = self.body()
        self.assertEqual(payload["context"]["policy_id"], "project_context_external_v1")
        self.assertIn("parts", payload["context"])
        self.assertNotIn("working_context", payload)

    def test_no_global_mind_content_crosses(self):
        text, _ = self.body()
        for sentinel in ("SENTINEL-USER-IDENTITY", "SENTINEL-CROSS-PROJECT",
                         "Direct, no filler", "Python, stdlib first"):
            self.assertNotIn(sentinel, text)

    def test_no_current_message_reference_crosses(self):
        text, _ = self.body()
        self.assertNotIn("user:current_message", text)
        self.assertNotIn("user_instruction", text)

    def test_no_unsafe_working_context_field_crosses(self):
        text, _ = self.body()
        for field in ("active_task", "delegated_worker", "latest_evidence_ref",
                      "objective_set_at", "objective_source", "\"revision\""):
            self.assertNotIn(field, text)

    def test_no_path_or_root_crosses(self):
        text, _ = self.body()
        for fragment in (str(self.home), str(self.project_root),
                         "/home/", "/tmp/", "slots/a", "slots/b", ".obsidian"):
            self.assertNotIn(fragment, text)

    def test_no_part_carries_fields_or_a_heading(self):
        _, payload = self.body()
        for part in payload["context"]["parts"]:
            self.assertNotIn("fields", part)
            self.assertNotIn("heading", part)
            self.assertNotIn("section", part)

    def test_the_response_is_not_stored_by_a_cache(self):
        self.activate_workspace()
        response = self.get_context(headers=self.auth)
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_the_serialized_body_is_under_the_transport_ceiling(self):
        from cofferdam.workstation.projectcontext import MAX_SERIALIZED_RESPONSE_BYTES

        text, _ = self.body()
        self.assertLess(len(text.encode("utf-8")), MAX_SERIALIZED_RESPONSE_BYTES)


class ProjectedMaterial(ContextApiHarness):
    """The eligible half survives, so the surface is actually useful."""

    def test_objective_and_next_step_survive(self):
        self.activate_workspace()
        self.client.put("/api/workspace/objective", json={"objective": "finish the read surface"}, headers=self.auth)
        self.client.put("/api/workspace/context", json={"expected_next_step": "open the PR"}, headers=self.auth)
        text = self.get_context(headers=self.auth).text
        self.assertIn("finish the read surface", text)
        self.assertIn("open the PR", text)

    def test_status_plan_and_decisions_survive(self):
        self.activate_workspace()
        payload = self.get_context(headers=self.auth).json()
        refs = {part["source_ref"] for part in payload["context"]["parts"]}
        for role in ("status", "plan", "decisions"):
            self.assertIn("project:" + PROJECT_ID + ":" + role, refs)

    def test_design_is_not_projected(self):
        self.activate_workspace()
        payload = self.get_context(headers=self.auth).json()
        refs = {part["source_ref"] for part in payload["context"]["parts"]}
        self.assertNotIn("project:" + PROJECT_ID + ":design", refs)


class SanitizerAtTheSurface(ContextApiHarness):
    """PR3.5.1's hardening, proved through HTTP rather than in isolation."""

    def write_status(self, body):
        (self.project_root / "STATUS.md").write_text(body, encoding="utf-8")

    def test_a_bare_credential_assignment_omits_the_whole_part(self):
        self.activate_workspace()
        self.write_status(
            "# Status\n\nA line that would otherwise be eligible.\n"
            "API_KEY=ZQXFAKEtok9d2f81b40c7ae653aa10\n"
        )
        response = self.get_context(headers=self.auth)
        text = response.text
        self.assertNotIn("ZQXFAKEtok9d2f81b40c7ae653aa10", text)
        self.assertNotIn("would otherwise be eligible", text)
        payload = response.json()
        reasons = {row["reason"] for row in payload["context"]["omissions"]}
        self.assertIn("sensitive_content_omitted", reasons)

    def test_a_prefixed_credential_assignment_also_omits(self):
        self.activate_workspace()
        self.write_status(
            "# Status\n\nCOFFERDAM_ACTIONS_TOKEN=ZQXFAKEtok9d2f81b40c7ae653aa10\n"
        )
        self.assertNotIn(
            "ZQXFAKEtok9d2f81b40c7ae653aa10",
            self.get_context(headers=self.auth).text,
        )

    def test_doubled_and_tripled_separators_are_redacted(self):
        self.activate_workspace()
        self.write_status(
            "# Status\n\nPaths: /home//fake-user/x and /root///secret and slots//a.\n"
        )
        text = self.get_context(headers=self.auth).text
        for fragment in ("/home//fake-user", "/root///secret", "slots//a"):
            self.assertNotIn(fragment, text)

    def test_a_known_root_with_duplicated_separators_is_redacted(self):
        self.activate_workspace()
        literal = str(self.project_root)
        head, _, tail = literal.rpartition("/")
        self.write_status("# Status\n\nThe root is " + head + "//" + tail + " here.\n")
        self.assertNotIn(literal, self.get_context(headers=self.auth).text)

    def test_api_routes_and_ordinary_urls_survive(self):
        self.activate_workspace()
        self.write_status(
            "# Status\n\nThe daemon exposes /api/tasks and /v1/projects.\n"
            "See https://example.com/home/docs for the writeup.\n"
        )
        text = self.get_context(headers=self.auth).text
        for keep in ("/api/tasks", "/v1/projects", "https://example.com/home/docs"):
            self.assertIn(keep, text)

    def test_the_lowercase_limitation_is_unchanged_and_declared(self):
        """PR3.5.1's documented limit, still true and still carried."""
        self.activate_workspace()
        self.write_status(
            "# Status\n\napi_key=ZQXFAKEtok9d2f81b40c7ae653aa10 in prose.\n"
        )
        payload = self.get_context(headers=self.auth).json()
        carried = " ".join(payload["context"]["limitations"]).lower()
        self.assertIn("upper case", carried)

    def test_large_unicode_content_stays_within_the_ceiling(self):
        from cofferdam.workstation.projectcontext import MAX_SERIALIZED_RESPONSE_BYTES

        self.activate_workspace()
        for name in ("STATUS.md", "ROADMAP.md", "DECISIONS.md"):
            (self.project_root / name).write_text(
                "# H\n\n" + (" é中" * 12000) + "\n", encoding="utf-8"
            )
        response = self.get_context(headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertLess(
            len(response.text.encode("utf-8")), MAX_SERIALIZED_RESPONSE_BYTES
        )


class NoMutation(ContextApiHarness):
    """Repeated reads change nothing observable."""

    def test_ten_reads_change_no_state(self):
        self.activate_workspace()
        self.client.put("/api/workspace/objective", json={"objective": "stay still"}, headers=self.auth)
        before_context = self.current().json()['working_context']
        before_files = {str(path) for path in self.home.rglob("*")}

        for _ in range(10):
            self.assertEqual(self.get_context(headers=self.auth).status_code, 200)

        after_context = self.current().json()['working_context']
        self.assertEqual(before_context, after_context)
        self.assertEqual(before_files, {str(path) for path in self.home.rglob("*")})

    def test_no_task_or_event_is_created_by_a_read(self):
        self.activate_workspace()
        before = self.client.get("/api/tasks", headers=self.auth).json()
        self.get_context(headers=self.auth)
        after = self.client.get("/api/tasks", headers=self.auth).json()
        self.assertEqual(before, after)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
