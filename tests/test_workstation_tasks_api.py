"""The authenticated task routes (M2F API and security).

What these assert is the *shape of the surface*, not the lifecycle — that is
covered against the real service in ``tests/test_task_core.py``. Here the
questions are the ones only a client can ask:

* is every route authenticated, including every read;
* does any ``GET`` change anything;
* is the accepted vocabulary genuinely closed — a body carrying
  ``working_directory``, ``command``, ``argv``, ``env`` or ``origin`` must be
  **refused**, not filtered;
* does an unsupported content type, an oversized body, or a malformed one fail
  before anything runs;
* can a client reach the validation adapter that the server did not enable.
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
TURKISH_PROMPT = "Işıkları kıs ve şarkıyı değiştir."


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class TaskApiTestCase(unittest.TestCase):
    enable_validation_adapter = True

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
            **{
                **config.__dict__,
                "enable_validation_task_adapter": self.enable_validation_adapter,
            }
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

        self.config = config
        self.app = create_app(config=config, token=TOKEN, adapter=StubAdapter(config))
        self.client = TestClient(self.app)
        self.auth = {"Authorization": "Bearer " + TOKEN}

    def create(self, **overrides):
        body = {
            "project_id": PROJECT_ID,
            "adapter_id": "validation",
            "prompt": TURKISH_PROMPT,
        }
        body.update(overrides)
        return self.client.post("/api/tasks", json=body, headers=self.auth)


class Authentication(TaskApiTestCase):
    ROUTES = (
        ("GET", "/api/tasks", None),
        ("GET", "/api/task-adapters", None),
        ("GET", "/api/task-projects", None),
        ("POST", "/api/tasks", {"project_id": "x", "adapter_id": "y", "prompt": "z"}),
    )

    def test_every_route_requires_the_device_token(self):
        """47, 48."""
        for method, path, body in self.ROUTES:
            response = self.client.request(method, path, json=body)
            self.assertEqual(response.status_code, 401, method + " " + path)

    def test_task_detail_and_events_require_the_token(self):
        """47, 48."""
        task_id = self.create().json()["task"]["task_id"]
        for path in (
            "/api/tasks/" + task_id,
            "/api/tasks/" + task_id + "/events",
        ):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        for path in (
            "/api/tasks/" + task_id + "/followups",
            "/api/tasks/" + task_id + "/cancel",
        ):
            self.assertEqual(self.client.post(path, json={}).status_code, 401, path)

    def test_a_wrong_token_is_refused(self):
        response = self.client.get(
            "/api/tasks", headers={"Authorization": "Bearer wrong"}
        )
        self.assertEqual(response.status_code, 401)


class ClosedVocabulary(TaskApiTestCase):
    #: Every field a client might try that would turn a task request into a
    #: command. None of these is validated and stripped — each is refused.
    FORBIDDEN = (
        "working_directory",
        "cwd",
        "path",
        "root",
        "executable",
        "command",
        "argv",
        "env",
        "environment",
        "shell",
        "api_key",
        "token",
        "return_url",
        "callback_url",
        "webhook_url",
        "pid",
        "unit",
        "origin",
        "task_id",
        "state",
        "scenario",
        "events",
    )

    def test_create_refuses_every_extra_field(self):
        """6, 7, 52, 53."""
        for field in self.FORBIDDEN:
            response = self.create(**{field: "anything"})
            self.assertEqual(response.status_code, 422, field + " was accepted")
            self.assertEqual(response.json()["error"]["code"], "invalid_params")

    def test_followup_refuses_every_extra_field(self):
        task_id = self.create(prompt="scenario: wait").json()["task"]["task_id"]
        for field in self.FORBIDDEN:
            response = self.client.post(
                "/api/tasks/" + task_id + "/followups",
                json={"followup": "hi", field: "anything"},
                headers=self.auth,
            )
            self.assertEqual(response.status_code, 422, field)

    def test_cancel_accepts_no_fields_at_all(self):
        task_id = self.create(prompt="scenario: cancel").json()["task"]["task_id"]
        response = self.client.post(
            "/api/tasks/" + task_id + "/cancel",
            json={"force": True},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 422)

    def test_the_client_cannot_choose_the_origin(self):
        """4. Refused as an unexpected field, and the server's own value stands."""
        self.assertEqual(self.create(origin="chatgpt_app").status_code, 422)
        self.assertEqual(self.create().json()["task"]["origin"], "pwa")

    def test_the_client_cannot_enable_the_validation_adapter(self):
        """52. There is no route, field or header that turns it on."""
        for attempt in (
            {"enable_validation_task_adapter": True},
            {"validation": True},
            {"adapters": ["validation"]},
        ):
            response = self.client.post("/api/tasks", json=attempt, headers=self.auth)
            self.assertEqual(response.status_code, 422)
        # And no write route exists for the adapter or project catalogues.
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for path in ("/api/task-adapters", "/api/task-projects"):
                response = self.client.request(method, path, headers=self.auth, json={})
                self.assertIn(response.status_code, (404, 405), method + " " + path)


class BodyDiscipline(TaskApiTestCase):
    def test_a_non_json_content_type_is_refused(self):
        """50."""
        response = self.client.post(
            "/api/tasks",
            content="project_id=demo",
            headers={**self.auth, "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(response.status_code, 415)

    def test_an_oversized_body_is_refused(self):
        """51. Before it is parsed."""
        response = self.client.post(
            "/api/tasks",
            content=b'{"prompt":"' + b"a" * 40000 + b'"}',
            headers={**self.auth, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_malformed_json_is_refused(self):
        response = self.client.post(
            "/api/tasks",
            content=b"not json at all",
            headers={**self.auth, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)

    def test_a_json_array_is_refused(self):
        response = self.client.post(
            "/api/tasks",
            content=b"[1, 2, 3]",
            headers={**self.auth, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)

    def test_an_oversized_prompt_is_refused(self):
        """25."""
        response = self.create(prompt="a" * 9000)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "task_prompt_invalid")

    def test_an_empty_prompt_is_refused(self):
        """24."""
        self.assertEqual(self.create(prompt="   ").status_code, 422)

    def test_control_characters_are_refused(self):
        """26."""
        self.assertEqual(self.create(prompt="bad\x00null").status_code, 422)


class ReadsDoNotMutate(TaskApiTestCase):
    def test_reading_the_list_creates_nothing(self):
        """49."""
        before = self.client.get("/api/tasks", headers=self.auth).json()
        for _ in range(3):
            self.client.get("/api/tasks", headers=self.auth)
            self.client.get("/api/task-adapters", headers=self.auth)
            self.client.get("/api/task-projects", headers=self.auth)
        after = self.client.get("/api/tasks", headers=self.auth).json()
        self.assertEqual(before["tasks"], after["tasks"])
        self.assertEqual(after["counts"], {})

    def test_reading_a_task_does_not_change_it(self):
        """49."""
        created = self.create().json()["task"]
        for _ in range(3):
            self.client.get("/api/tasks/" + created["task_id"], headers=self.auth)
        after = self.client.get(
            "/api/tasks/" + created["task_id"], headers=self.auth
        ).json()["task"]
        self.assertEqual(after["lifecycle_revision"], created["lifecycle_revision"])
        self.assertEqual(after["event_cursor"], created["event_cursor"])

    def test_the_task_routes_are_not_reachable_by_get(self):
        """49. Nothing that changes a task can be reached with a GET.

        404 or 405 — either is a refusal, and which one arrives depends on
        whether the static mount or the router answers first. What matters is
        that neither is a 200, so no link, prefetch or crawler can act on a task.
        """
        task_id = self.create().json()["task"]["task_id"]
        for path in (
            "/api/tasks/" + task_id + "/followups",
            "/api/tasks/" + task_id + "/cancel",
        ):
            self.assertIn(
                self.client.get(path, headers=self.auth).status_code, (404, 405), path
            )


class EventFeed(TaskApiTestCase):
    def test_events_are_paged_by_sequence(self):
        """19."""
        task_id = self.create().json()["task"]["task_id"]
        everything = self.client.get(
            "/api/tasks/" + task_id + "/events", headers=self.auth
        ).json()
        self.assertTrue(everything["events"])
        sequences = [event["sequence"] for event in everything["events"]]
        self.assertEqual(sequences, sorted(sequences))

        tail = self.client.get(
            "/api/tasks/" + task_id + "/events?after=" + str(sequences[2]),
            headers=self.auth,
        ).json()
        self.assertTrue(all(e["sequence"] > sequences[2] for e in tail["events"]))

    def test_an_unbounded_limit_is_capped(self):
        """19. No expensive scan is reachable from a query string."""
        task_id = self.create().json()["task"]["task_id"]
        response = self.client.get(
            "/api/tasks/" + task_id + "/events?limit=999999", headers=self.auth
        )
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.json()["events"]), 200)

    def test_a_negative_cursor_is_refused(self):
        task_id = self.create().json()["task"]["task_id"]
        response = self.client.get(
            "/api/tasks/" + task_id + "/events?after=-5", headers=self.auth
        )
        self.assertEqual(response.status_code, 422)

    def test_an_unknown_task_is_a_404(self):
        for hostile in ("task_zzzzzzzzzzzzzzzzzzzzzzzzzz", "not-a-task", "../../etc"):
            response = self.client.get(
                "/api/tasks/" + hostile, headers=self.auth
            )
            self.assertIn(response.status_code, (404, 405), hostile)


class Behaviour(TaskApiTestCase):
    def test_create_returns_201_and_the_stored_task(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload["created"])
        self.assertEqual(payload["task"]["state"], "completed")
        self.assertEqual(payload["task"]["project_id"], PROJECT_ID)

    def test_a_duplicate_request_returns_200_and_the_same_task(self):
        """30, 33."""
        first = self.create(client_request_id="tap-1")
        second = self.create(client_request_id="tap-1")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["created"])
        self.assertEqual(
            first.json()["task"]["task_id"], second.json()["task"]["task_id"]
        )
        self.assertEqual(len(self.client.get("/api/tasks", headers=self.auth).json()["tasks"]), 1)

    def test_the_same_key_with_a_different_prompt_is_refused(self):
        """31."""
        self.create(client_request_id="tap-2", prompt="first")
        second = self.create(client_request_id="tap-2", prompt="second")
        self.assertEqual(second.status_code, 409)
        self.assertEqual(
            second.json()["error"]["code"], "task_idempotency_conflict"
        )

    def test_an_unknown_project_is_a_404(self):
        """8."""
        response = self.create(project_id="nope")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "task_project_unknown")

    def test_an_unknown_adapter_is_a_404(self):
        """10."""
        response = self.create(adapter_id="claude-code")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "task_adapter_unknown")

    def test_the_waiting_and_followup_round_trip(self):
        """38, 42."""
        task_id = self.create(prompt="scenario: wait").json()["task"]["task_id"]
        detail = self.client.get("/api/tasks/" + task_id, headers=self.auth).json()["task"]
        self.assertEqual(detail["state"], "waiting_for_user")
        self.assertEqual(detail["waiting_reason"], "clarification")

        response = self.client.post(
            "/api/tasks/" + task_id + "/followups",
            json={"followup": "evet, devam et"},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["task"]["state"], "completed")

    def test_a_followup_to_a_finished_task_is_refused(self):
        """39."""
        task_id = self.create().json()["task"]["task_id"]
        response = self.client.post(
            "/api/tasks/" + task_id + "/followups",
            json={"followup": "too late"},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "task_already_finished")

    def test_cancel_and_repeat_cancel(self):
        """34, 35."""
        task_id = self.create(prompt="scenario: cancel").json()["task"]["task_id"]
        first = self.client.post(
            "/api/tasks/" + task_id + "/cancel", json={}, headers=self.auth
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["task"]["state"], "cancelled")

        second = self.client.post(
            "/api/tasks/" + task_id + "/cancel", json={}, headers=self.auth
        )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(
            self.client.get("/api/tasks/" + task_id, headers=self.auth)
            .json()["task"]["state"],
            "cancelled",
        )

    def test_the_list_filters_by_bucket(self):
        """46."""
        self.create(prompt="scenario: complete")
        self.create(prompt="scenario: wait")
        self.create(prompt="scenario: cancel")

        for bucket, expected in (
            ("active", "running"),
            ("waiting", "waiting_for_user"),
            ("finished", "completed"),
        ):
            payload = self.client.get(
                "/api/tasks?bucket=" + bucket, headers=self.auth
            ).json()
            self.assertEqual(len(payload["tasks"]), 1, bucket)
            self.assertEqual(payload["tasks"][0]["state"], expected)

    def test_an_unknown_bucket_is_refused(self):
        response = self.client.get("/api/tasks?bucket=everything", headers=self.auth)
        self.assertEqual(response.status_code, 422)

    def test_the_list_carries_no_task_content(self):
        """The list is rows; content belongs to the detail view.

        Asserted on the row's own keys rather than on the response text: the
        string ``final_result`` legitimately appears in the payload as an
        *adapter capability* name, and a substring check would confuse "this
        adapter can produce a result" with "here is the result".
        """
        self.create(prompt=TURKISH_PROMPT)
        payload = self.client.get("/api/tasks", headers=self.auth).json()
        row = payload["tasks"][0]
        self.assertNotIn("final_result", row)
        self.assertNotIn("latest_meaningful_output", row)
        self.assertNotIn("prompt", row)
        # And no content leaked through any other field.
        body = self.client.get("/api/tasks", headers=self.auth).text
        self.assertNotIn(TURKISH_PROMPT, body)
        self.assertNotIn("Validation scenario completed", body)

    def test_the_detail_view_carries_the_prompt(self):
        task_id = self.create(prompt=TURKISH_PROMPT).json()["task"]["task_id"]
        payload = self.client.get("/api/tasks/" + task_id, headers=self.auth).json()
        self.assertEqual(payload["task"]["prompt"], TURKISH_PROMPT)

    def test_no_response_carries_a_filesystem_path(self):
        """The project's root stays on the host."""
        task_id = self.create().json()["task"]["task_id"]
        for path in (
            "/api/tasks",
            "/api/tasks/" + task_id,
            "/api/tasks/" + task_id + "/events",
            "/api/task-projects",
            "/api/task-adapters",
            "/api/status",
        ):
            body = self.client.get(path, headers=self.auth).text
            self.assertNotIn(str(self.project_root), body, path)
            self.assertNotIn(str(self.home), body, path)

    def test_the_audit_record_carries_no_task_content(self):
        """56."""
        self.create(prompt=TURKISH_PROMPT)
        actions = self.client.get("/api/actions", headers=self.auth).json()["actions"]
        task_actions = [a for a in actions if a["action"].startswith("task_")]
        self.assertTrue(task_actions, "the task was not audited")
        blob = json.dumps(task_actions, ensure_ascii=False)
        self.assertNotIn(TURKISH_PROMPT, blob)
        self.assertNotIn("Validation scenario completed", blob)


class ValidationAdapterDisabled(TaskApiTestCase):
    """The default install: Task Core present, no adapter registered."""

    enable_validation_adapter = False

    def test_the_adapter_list_is_empty(self):
        """11."""
        payload = self.client.get("/api/task-adapters", headers=self.auth).json()
        self.assertEqual(payload["adapters"], [])

    def test_naming_it_is_an_unknown_adapter(self):
        """11, 52. Not disabled — absent."""
        response = self.create()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "task_adapter_unknown")

    def test_the_rest_of_the_task_surface_still_works(self):
        """A foundation with nothing registered is not a broken foundation."""
        self.assertEqual(self.client.get("/api/tasks", headers=self.auth).status_code, 200)
        self.assertEqual(
            self.client.get("/api/task-projects", headers=self.auth).status_code, 200
        )


class ProjectRootSafety(TaskApiTestCase):
    def test_a_symlinked_project_root_is_refused_through_the_api(self):
        """9."""
        outside = self.home / "outside"
        outside.mkdir()
        linked = self.home / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        (self.config.config_dir / "task-projects.json").write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "project_id": "linked",
                            "root": str(linked),
                            "adapters": ["validation"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.app.state.tasks.reload_projects()
        response = self.create(project_id="linked")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["error"]["code"], "task_project_root_invalid"
        )

    def test_the_project_list_never_publishes_a_root(self):
        payload = self.client.get("/api/task-projects", headers=self.auth).json()
        self.assertTrue(payload["projects"])
        for project in payload["projects"]:
            self.assertNotIn("root", project)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
