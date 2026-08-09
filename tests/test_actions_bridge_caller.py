"""The daemon's side of the boundary: a second credential, on ten routes only.

This is the change M2I.5 makes to `cofferdam.workstation`, and it is small
enough to state in three sentences.

1. When the host enables it, a second 0600 credential exists.
2. Ten task routes accept **either** credential and record which one arrived.
3. Every other route in the daemon still accepts the device token and nothing
   else — not because it checks, but because ``require_token`` has never heard
   of the second credential.

The provenance half matters as much as the access half. A task the bridge
created must be recorded as ``chatgpt_app`` / ``future_gpt_bridge``, because a
history that filed it under the phone would be a history that cannot answer
"did I ask for this, or did a model?".
"""

from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the extras are absent
    TestClient = None

DEVICE_TOKEN = "device-token-not-a-real-credential-0001"
BRIDGE_TOKEN = "bridge-internal-token-not-real-0002"
PROJECT_ID = "demo"


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class CallerTestCase(unittest.TestCase):
    enable_bridge_caller = True

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
                "enable_validation_task_adapter": True,
                "enable_actions_bridge_caller": self.enable_bridge_caller,
            }
        )
        config.ensure_dirs()
        (config.config_dir / "task-projects.json").write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "project_id": PROJECT_ID,
                            "display_name": "Demo",
                            "root": str(self.project_root),
                            "adapters": ["validation"],
                            "enabled": True,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        if self.enable_bridge_caller:
            path = config.actions_bridge_token_path
            path.write_text(BRIDGE_TOKEN + "\n", encoding="utf-8")
            path.chmod(0o600)

        self.config = config
        self.app = create_app(
            config=config, token=DEVICE_TOKEN, adapter=StubAdapter(config)
        )
        self.client = TestClient(self.app)

    def device(self) -> dict:
        return {"Authorization": "Bearer " + DEVICE_TOKEN}

    def bridge(self) -> dict:
        return {"Authorization": "Bearer " + BRIDGE_TOKEN}

    def create(self, headers) -> dict:
        response = self.client.post(
            "/api/tasks",
            headers=headers,
            json={
                "project_id": PROJECT_ID,
                "adapter_id": "validation",
                "prompt": "complete: do a thing",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["task"]


class BridgeCallerSurfaceTests(CallerTestCase):
    #: The ten routes the bridge is allowed to reach, and the method for each.
    BRIDGE_ROUTES = (
        ("GET", "/api/task-projects", None),
        ("GET", "/api/tasks", None),
        ("POST", "/api/tasks", {"project_id": PROJECT_ID, "prompt": "complete: x"}),
        ("GET", "/api/tasks/{task_id}", None),
        ("GET", "/api/tasks/{task_id}/result", None),
        ("GET", "/api/tasks/{task_id}/clarifications", None),
        ("POST", "/api/tasks/{task_id}/followups", {"followup": "more"}),
        ("POST", "/api/tasks/{task_id}/cancel", {}),
        ("POST", "/api/tasks/{task_id}/finish", {}),
    )

    #: A sample of everything else. Each must refuse the bridge credential.
    FORBIDDEN_ROUTES = (
        ("GET", "/api/status"),
        ("GET", "/api/actions"),
        ("POST", "/api/actions/screenshot"),
        ("POST", "/api/actions/open-url"),
        ("GET", "/api/registries"),
        ("GET", "/api/runtime"),
        ("GET", "/api/audio"),
        ("GET", "/api/spotify/playback"),
        ("GET", "/api/youtube/player"),
        ("GET", "/api/task-adapters"),
        ("GET", "/api/remote-control/demo"),
        ("POST", "/api/remote-control/demo/start"),
        ("GET", "/api/remote-control/demo/link"),
    )

    def test_the_bridge_credential_reaches_the_ten_task_routes(self) -> None:
        task = self.create(self.device())
        for method, template, body in self.BRIDGE_ROUTES:
            path = template.format(task_id=task["task_id"])
            with self.subTest(method=method, path=path):
                response = self.client.request(
                    method, path, headers=self.bridge(), json=body
                )
                # Any answer but 401 — a 409 for an illegal transition is the
                # route doing its job, and it is still the route being reached.
                self.assertNotEqual(response.status_code, 401, response.text)

    def test_the_bridge_credential_reaches_nothing_else(self) -> None:
        for method, path in self.FORBIDDEN_ROUTES:
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path, headers=self.bridge(), json={})
                self.assertEqual(response.status_code, 401, path)
                self.assertEqual(response.json()["error"]["code"], "unauthorized")

    def test_the_task_events_route_is_not_reachable_by_the_bridge(self) -> None:
        """Raw events are a transcript-shaped surface, so the bridge has no key for it."""
        task = self.create(self.device())
        response = self.client.get(
            f"/api/tasks/{task['task_id']}/events", headers=self.bridge()
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            self.client.get(
                f"/api/tasks/{task['task_id']}/events", headers=self.device()
            ).status_code,
            200,
        )

    def test_the_device_token_still_reaches_everything_it_did(self) -> None:
        """PR1 must not narrow the PWA's surface by one route."""
        task = self.create(self.device())
        for method, template, body in self.BRIDGE_ROUTES:
            path = template.format(task_id=task["task_id"])
            with self.subTest(path=path):
                response = self.client.request(
                    method, path, headers=self.device(), json=body
                )
                self.assertNotEqual(response.status_code, 401)
        for method, path in self.FORBIDDEN_ROUTES:
            with self.subTest(path=path):
                response = self.client.request(method, path, headers=self.device(), json={})
                self.assertNotEqual(response.status_code, 401)

    def test_no_credential_at_all_is_still_401_everywhere(self) -> None:
        task = self.create(self.device())
        for method, template, body in self.BRIDGE_ROUTES:
            path = template.format(task_id=task["task_id"])
            with self.subTest(path=path):
                self.assertEqual(
                    self.client.request(method, path, json=body).status_code, 401
                )

    def test_a_wrong_credential_is_401_on_the_shared_routes(self) -> None:
        for wrong in ("", "x", DEVICE_TOKEN[:-1], BRIDGE_TOKEN[:-1]):
            with self.subTest(token=wrong):
                response = self.client.get(
                    "/api/task-projects",
                    headers={"Authorization": "Bearer " + wrong},
                )
                self.assertEqual(response.status_code, 401)


class BridgeCallerDisabledTests(CallerTestCase):
    """With the flag off there is no second credential to present."""

    enable_bridge_caller = False

    def test_no_token_file_is_generated(self) -> None:
        self.assertFalse(self.config.actions_bridge_token_path.exists())

    def test_the_bridge_credential_is_refused_on_the_task_routes(self) -> None:
        self.assertEqual(
            self.client.get("/api/task-projects", headers=self.bridge()).status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/api/tasks", headers=self.bridge()).status_code, 401
        )

    def test_an_empty_bearer_cannot_become_the_bridge(self) -> None:
        """The absent-token case must not compare equal to an empty string."""
        for candidate in ("", " ", "None", "null"):
            with self.subTest(candidate=repr(candidate)):
                self.assertEqual(
                    self.client.get(
                        "/api/tasks",
                        headers={"Authorization": "Bearer " + candidate},
                    ).status_code,
                    401,
                )

    def test_the_device_token_is_unaffected(self) -> None:
        self.assertEqual(
            self.client.get("/api/task-projects", headers=self.device()).status_code,
            200,
        )


class ProvenanceTests(CallerTestCase):
    """Which credential arrived decides how the work is attributed."""

    def test_a_pwa_task_is_recorded_as_pwa(self) -> None:
        task = self.create(self.device())
        self.assertEqual(task["origin"], "pwa")

    def test_a_bridge_task_is_recorded_as_chatgpt_app(self) -> None:
        task = self.create(self.bridge())
        self.assertEqual(task["origin"], "chatgpt_app")

    def test_the_origin_cannot_be_chosen_by_the_caller(self) -> None:
        """There is no ``origin`` field. Sending one is a refusal, not an override."""
        response = self.client.post(
            "/api/tasks",
            headers=self.device(),
            json={
                "project_id": PROJECT_ID,
                "prompt": "complete: x",
                "origin": "chatgpt_app",
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_the_first_turn_source_follows_the_origin(self) -> None:
        from cofferdam.workstation.tasks.turns import source_for_origin

        pwa_task = self.create(self.device())
        bridge_task = self.create(self.bridge())
        store = self.app.state.tasks.store
        self.assertEqual(
            store.turns(pwa_task["task_id"])[0].source, "workstation_pwa"
        )
        self.assertEqual(
            store.turns(bridge_task["task_id"])[0].source, "future_gpt_bridge"
        )
        self.assertEqual(source_for_origin("chatgpt_app"), "future_gpt_bridge")

    def test_a_bridge_follow_up_is_recorded_as_the_bridge(self) -> None:
        """The ``wait`` scenario stops for one follow-up, which is what this needs."""
        created = self.client.post(
            "/api/tasks",
            headers=self.bridge(),
            json={
                "project_id": PROJECT_ID,
                "adapter_id": "validation",
                "prompt": "scenario: wait",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        task = created.json()["task"]
        self.assertEqual(task["state"], "waiting_for_user")

        response = self.client.post(
            f"/api/tasks/{task['task_id']}/followups",
            headers=self.bridge(),
            json={"followup": "carry on"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        turns = self.app.state.tasks.store.turns(task["task_id"])
        self.assertEqual(turns[-1].source, "future_gpt_bridge")
        self.assertNotEqual(turns[-1].source, "workstation_pwa")

    def test_a_pwa_follow_up_on_the_same_shape_is_still_the_pwa(self) -> None:
        created = self.client.post(
            "/api/tasks",
            headers=self.device(),
            json={
                "project_id": PROJECT_ID,
                "adapter_id": "validation",
                "prompt": "scenario: wait",
            },
        )
        task = created.json()["task"]
        self.client.post(
            f"/api/tasks/{task['task_id']}/followups",
            headers=self.device(),
            json={"followup": "carry on"},
        )
        turns = self.app.state.tasks.store.turns(task["task_id"])
        self.assertEqual(turns[-1].source, "workstation_pwa")

    def test_the_prompt_is_not_published_back_to_the_bridge(self) -> None:
        """The bridge composed it. Handing it back would let a provider re-read it."""
        task = self.create(self.bridge())
        bridge_view = self.client.get(
            f"/api/tasks/{task['task_id']}", headers=self.bridge()
        ).json()["task"]
        device_view = self.client.get(
            f"/api/tasks/{task['task_id']}", headers=self.device()
        ).json()["task"]
        self.assertNotIn("prompt", bridge_view)
        self.assertIn("prompt", device_view)
        self.assertIn("do a thing", device_view["prompt"])


class BridgeTokenGenerationTests(unittest.TestCase):
    """The credential is generated only when asked for, and never announced."""

    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)

    def _config(self, enabled: bool):
        from cofferdam.workstation.config import load_config

        config = load_config(self.home)
        return type(config)(
            **{**config.__dict__, "enable_actions_bridge_caller": enabled}
        )

    def test_nothing_is_generated_when_the_caller_is_disabled(self) -> None:
        from cofferdam.workstation.config import load_or_create_actions_bridge_token

        config = self._config(False)
        self.assertIsNone(load_or_create_actions_bridge_token(config))
        self.assertFalse(config.actions_bridge_token_path.exists())

    def test_a_generated_token_is_0600_and_stable(self) -> None:
        from cofferdam.workstation.config import load_or_create_actions_bridge_token

        config = self._config(True)
        first = load_or_create_actions_bridge_token(config)
        self.assertTrue(first)
        self.assertEqual(
            stat.S_IMODE(config.actions_bridge_token_path.stat().st_mode), 0o600
        )
        self.assertEqual(load_or_create_actions_bridge_token(config), first)

    def test_it_is_not_the_device_token(self) -> None:
        from cofferdam.workstation.config import (
            load_or_create_actions_bridge_token,
            load_or_create_token,
        )

        config = self._config(True)
        self.assertNotEqual(
            load_or_create_token(config), load_or_create_actions_bridge_token(config)
        )

    def test_there_is_no_environment_override(self) -> None:
        """Unlike the device token, deliberately — see the function's docstring."""
        import inspect

        from cofferdam.workstation import config as module

        source = inspect.getsource(module.load_or_create_actions_bridge_token)
        self.assertNotIn("os.environ", source)
        self.assertIn("no environment override", source)

    def test_the_flag_is_off_by_default(self) -> None:
        from cofferdam.workstation.config import (
            DEFAULT_ENABLE_ACTIONS_BRIDGE_CALLER,
            load_config,
        )

        self.assertFalse(DEFAULT_ENABLE_ACTIONS_BRIDGE_CALLER)
        self.assertFalse(load_config(self.home).enable_actions_bridge_caller)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
