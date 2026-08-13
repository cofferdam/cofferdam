"""The authenticated workspace routes (M2J PR1 API and security).

The questions here are the ones only a client can ask:

* is every route authenticated, including every read;
* does the **bridge credential** reach any of them — it must not. M2J PR4 gave
  the bridge exactly one context read, ``GET /api/projects/{id}/context``, which
  returns a `CloudContextProjection` and lives behind its own dependency. None of
  the routes in *this* file moved: ``syncWorkspace`` is M2M's (D-2026-08-13-4),
  and no external surface may read or write the workspace object itself;
* is the accepted vocabulary genuinely closed — a body carrying ``root``,
  ``adapter_id``, ``model``, ``provider`` or ``source`` must be **refused**,
  not filtered;
* does a read ever change anything;
* does an unconfigured host answer truthfully instead of erroring;
* is a filesystem path ever published.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the extras are absent
    TestClient = None

TOKEN = "test-device-token-not-a-real-credential"
PROJECT_ID = "demo"
WORKSPACE_ID = "demo-workspace"
TURKISH_OBJECTIVE = "M2J çalışma alanı temelini bitir."


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class WorkspaceApiTestCase(unittest.TestCase):
    configure_workspaces = True

    def setUp(self) -> None:
        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.service import create_app

        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.project_root = self.home / "projects" / PROJECT_ID
        self.project_root.mkdir(parents=True)

        config = load_config(self.home)
        config = type(config)(
            **{**config.__dict__, "enable_validation_task_adapter": True}
        )
        config.ensure_dirs()
        (config.config_dir / "task-projects.json").write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "project_id": PROJECT_ID,
                            "display_name": "Demo project",
                            "root": str(self.project_root),
                            "adapters": ["validation"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        if self.configure_workspaces:
            self.write_workspaces(
                config,
                [
                    {
                        "workspace_id": WORKSPACE_ID,
                        "display_name": "Demo workspace",
                        "project_id": PROJECT_ID,
                    }
                ],
            )

        self.config = config
        self.app = create_app(config=config, token=TOKEN, adapter=StubAdapter(config))
        self.client = TestClient(self.app)
        self.auth = {"Authorization": "Bearer " + TOKEN}

    @staticmethod
    def write_workspaces(config, entries):
        (config.config_dir / "workspaces.json").write_text(
            json.dumps({"workspaces": entries}), encoding="utf-8"
        )

    def activate(self, workspace_id=WORKSPACE_ID):
        return self.client.put(
            "/api/workspace/active", json={"workspace_id": workspace_id}, headers=self.auth
        )

    def current(self):
        return self.client.get("/api/workspace/current", headers=self.auth)


class Authentication(WorkspaceApiTestCase):
    ROUTES = (
        ("GET", "/api/workspaces", None),
        ("GET", "/api/workspace/current", None),
        ("GET", "/api/workspace/objective-history", None),
        ("PUT", "/api/workspace/active", {"workspace_id": WORKSPACE_ID}),
        ("PUT", "/api/workspace/objective", {"objective": "x"}),
        ("PUT", "/api/workspace/context", {"expected_next_step": "x"}),
    )

    def test_every_route_requires_the_device_token(self):
        for method, path, body in self.ROUTES:
            response = self.client.request(method, path, json=body)
            self.assertEqual(response.status_code, 401, method + " " + path)

    def test_a_wrong_token_is_refused(self):
        for method, path, body in self.ROUTES:
            response = self.client.request(
                method, path, json=body, headers={"Authorization": "Bearer wrong"}
            )
            self.assertEqual(response.status_code, 401, method + " " + path)

class BridgeBoundary(WorkspaceApiTestCase):
    """The Actions bridge credential reaches nothing here — on a host where it exists.

    Built with the caller **enabled**, so this is a real credential being
    presented and refused rather than a skip standing in for the property. The
    refusal is structural: these routes depend on ``require_token``, which has
    never heard of the bridge credential, so the request is a 401 because
    nothing here can recognise it — not because a check turns it away. That is
    the same argument D-2026-08-09-2 makes for the Remote Control routes.

    It matters now because M2J PR4 shipped an external context read. That read is
    a *different* route with a *different* dependency, returning a projection the
    host built — it did not widen these. ``syncWorkspace`` remains M2M's
    (D-2026-08-13-4), so no external surface reads or writes the workspace object.
    """

    def setUp(self) -> None:
        super().setUp()
        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.config import load_or_create_actions_bridge_token
        from cofferdam.workstation.service import create_app

        config = type(self.config)(
            **{**self.config.__dict__, "enable_actions_bridge_caller": True}
        )
        config.ensure_dirs()
        self.config = config
        self.bridge_token = load_or_create_actions_bridge_token(config)
        self.app = create_app(config=config, token=TOKEN, adapter=StubAdapter(config))
        self.client = TestClient(self.app)

    def test_the_bridge_credential_exists_on_this_host(self):
        """Otherwise the refusal below would prove nothing."""
        self.assertIsNotNone(self.bridge_token)

    def test_the_bridge_credential_reaches_no_workspace_route(self):
        for method, path, body in Authentication.ROUTES:
            response = self.client.request(
                method,
                path,
                json=body,
                headers={"Authorization": "Bearer " + self.bridge_token},
            )
            self.assertEqual(response.status_code, 401, method + " " + path)

    def test_the_same_credential_does_reach_a_task_route(self):
        """The control. Without it, the test above would also pass if the
        credential were simply invalid everywhere."""
        response = self.client.get(
            "/api/tasks", headers={"Authorization": "Bearer " + self.bridge_token}
        )
        self.assertEqual(response.status_code, 200)


class Reads(WorkspaceApiTestCase):
    def test_current_is_truthful_before_anything_is_activated(self):
        response = self.current()
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["active"])
        self.assertEqual(payload["problem"], "no_active_workspace")

    def test_current_sets_no_store(self):
        """The body carries somebody's objective: same class as a task result."""
        self.assertEqual(
            self.current().headers.get("cache-control"), "no-store"
        )

    def test_list_publishes_names_and_no_paths(self):
        response = self.client.get("/api/workspaces", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn(WORKSPACE_ID, body)
        self.assertNotIn(str(self.project_root), body)
        self.assertNotIn(str(self.home), body)

    def test_list_reports_whether_the_project_is_usable(self):
        payload = self.client.get("/api/workspaces", headers=self.auth).json()
        self.assertTrue(payload["workspaces"][0]["project_available"])

    def test_a_read_changes_nothing(self):
        before = self.current().json()
        self.current()
        self.client.get("/api/workspaces", headers=self.auth)
        self.assertEqual(self.current().json(), before)


class Mutations(WorkspaceApiTestCase):
    def test_activate_then_set_objective_and_next_step(self):
        self.assertEqual(self.activate().status_code, 200)
        self.assertEqual(
            self.client.put(
                "/api/workspace/objective",
                json={"objective": TURKISH_OBJECTIVE},
                headers=self.auth,
            ).status_code,
            200,
        )
        response = self.client.put(
            "/api/workspace/context",
            json={"expected_next_step": "PR'ı aç."},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        context = self.current().json()["working_context"]
        self.assertEqual(context["objective"], TURKISH_OBJECTIVE)
        self.assertEqual(context["expected_next_step"], "PR'ı aç.")

    def test_deactivate_with_an_explicit_null(self):
        self.activate()
        response = self.client.put(
            "/api/workspace/active", json={"workspace_id": None}, headers=self.auth
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["active"])

    def test_unknown_workspace_is_404(self):
        response = self.activate("no-such-workspace")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "workspace_unknown")

    def test_setting_an_objective_with_nothing_active_is_409(self):
        response = self.client.put(
            "/api/workspace/objective", json={"objective": "x"}, headers=self.auth
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "workspace_active_unset")

    def test_a_missing_required_field_is_refused(self):
        self.activate()
        for path in ("/api/workspace/active", "/api/workspace/objective"):
            response = self.client.put(path, json={}, headers=self.auth)
            self.assertEqual(response.status_code, 422, path)

    def test_an_empty_context_update_is_refused(self):
        self.activate()
        response = self.client.put("/api/workspace/context", json={}, headers=self.auth)
        self.assertEqual(response.status_code, 422)

    def test_an_over_long_objective_is_refused_by_the_field_bound(self):
        """Small enough to be parsed, too long to be an objective.

        Distinct from the oversized-body case below, and both matter: this one
        proves the *field* bound is real, rather than the body limit quietly
        doing all the work and the field bound never being reached.
        """
        self.activate()
        response = self.client.put(
            "/api/workspace/objective", json={"objective": "x" * 600}, headers=self.auth
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["error"]["code"], "workspace_context_field_invalid"
        )

    def test_an_objective_at_the_bound_is_accepted(self):
        self.activate()
        response = self.client.put(
            "/api/workspace/objective", json={"objective": "x" * 500}, headers=self.auth
        )
        self.assertEqual(response.status_code, 200)


class ClosedVocabulary(WorkspaceApiTestCase):
    """A body may not carry a path, a worker, a model or its own provenance."""

    FORBIDDEN = (
        ("/api/workspace/context", {"root": "/etc"}),
        ("/api/workspace/context", {"path": "/etc"}),
        ("/api/workspace/context", {"working_directory": "/etc"}),
        ("/api/workspace/context", {"adapter_id": "claude-agent-sdk"}),
        ("/api/workspace/context", {"delegated_adapter": "claude-agent-sdk"}),
        ("/api/workspace/context", {"model": "qwen3.5:9b"}),
        ("/api/workspace/context", {"provider": "ollama"}),
        ("/api/workspace/context", {"provider_session_id": "sess-1"}),
        ("/api/workspace/context", {"command": "rm -rf /"}),
        ("/api/workspace/context", {"argv": ["sh"]}),
        ("/api/workspace/context", {"env": {"X": "1"}}),
        ("/api/workspace/objective", {"objective": "x", "source": "planner"}),
        ("/api/workspace/objective", {"objective": "x", "workspace_id": "other"}),
        ("/api/workspace/active", {"workspace_id": WORKSPACE_ID, "project_id": "demo"}),
    )

    def test_forbidden_fields_are_refused_not_filtered(self):
        self.activate()
        for path, body in self.FORBIDDEN:
            with self.subTest(path=path, body=body):
                response = self.client.put(path, json=body, headers=self.auth)
                self.assertEqual(response.status_code, 422, str(body))
                self.assertIn("unexpected field", response.json()["error"]["message"])

    def test_provenance_is_assigned_by_the_server(self):
        """`source` is not a client field, for the reason task `origin` is not."""
        self.activate()
        self.client.put(
            "/api/workspace/objective", json={"objective": "A goal."}, headers=self.auth
        )
        self.assertEqual(
            self.current().json()["working_context"]["objective_source"], "user"
        )

    def test_a_non_json_content_type_is_refused(self):
        self.activate()
        response = self.client.put(
            "/api/workspace/objective",
            content="objective=x",
            headers={**self.auth, "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(response.status_code, 415)

    def test_an_oversized_body_is_refused(self):
        self.activate()
        response = self.client.put(
            "/api/workspace/objective",
            content=json.dumps({"objective": "x" * 20000}),
            headers={**self.auth, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_a_malformed_body_is_refused(self):
        self.activate()
        response = self.client.put(
            "/api/workspace/objective",
            content="{not json",
            headers={**self.auth, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)

    def test_a_non_object_body_is_refused(self):
        self.activate()
        response = self.client.put(
            "/api/workspace/objective",
            content="[1,2,3]",
            headers={**self.auth, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)


class TaskReferences(WorkspaceApiTestCase):
    def create_task(self):
        return self.client.post(
            "/api/tasks",
            json={
                "project_id": PROJECT_ID,
                "adapter_id": "validation",
                "prompt": "Işıkları kıs.",
            },
            headers=self.auth,
        ).json()["task"]["task_id"]

    def test_pointing_at_a_real_task_and_deriving_its_state(self):
        self.activate()
        task_id = self.create_task()
        response = self.client.put(
            "/api/workspace/context", json={"active_task_id": task_id}, headers=self.auth
        )
        self.assertEqual(response.status_code, 200)
        active = self.current().json()["working_context"]["active_task"]
        self.assertEqual(active["task_id"], task_id)
        self.assertIn(active["status"], ("live", "terminal"))
        self.assertIsNotNone(active["state"])

    def test_an_unknown_task_is_refused(self):
        self.activate()
        response = self.client.put(
            "/api/workspace/context",
            json={"active_task_id": "task-that-does-not-exist"},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 422)

    def test_the_payload_carries_no_prompt_or_session_id(self):
        self.activate()
        task_id = self.create_task()
        self.client.put(
            "/api/workspace/context", json={"active_task_id": task_id}, headers=self.auth
        )
        body = self.current().text
        self.assertNotIn("Işıkları", body)
        self.assertNotIn("provider_session", body)
        self.assertNotIn(str(self.project_root), body)


class UnconfiguredHost(WorkspaceApiTestCase):
    """No workspaces.json at all: the shipped default, and every existing flow works."""

    configure_workspaces = False

    def test_current_answers_rather_than_failing(self):
        response = self.current()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["active"])

    def test_the_list_is_empty_and_says_the_source_is_absent(self):
        payload = self.client.get("/api/workspaces", headers=self.auth).json()
        self.assertEqual(payload["workspaces"], [])
        self.assertFalse(payload["source_present"])

    def test_tasks_still_work_with_no_workspace_configured(self):
        """The backward-compatibility property, asserted end to end."""
        response = self.client.post(
            "/api/tasks",
            json={
                "project_id": PROJECT_ID,
                "adapter_id": "validation",
                "prompt": "Işıkları kıs.",
            },
            headers=self.auth,
        )
        self.assertIn(response.status_code, (200, 201))
        listing = self.client.get("/api/tasks", headers=self.auth)
        self.assertEqual(listing.status_code, 200)

    def test_no_workspace_database_is_created(self):
        self.current()
        self.client.get("/api/workspaces", headers=self.auth)
        self.assertFalse((self.config.state_dir / "workspace").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
