"""End to end, locally: a real bridge in front of a real Cofferdam daemon.

Everything above this file uses a double for one side or the other. This one
wires the two together and drives the whole path — external Bearer key, bridge
route, normalizer, fixed internal client, real HTTP, real daemon auth, real Task
Core, real SQLite — with only the *provider* replaced.

What is real
------------
The daemon (``create_app``), its task store, its project registry, its
clarification layer, both credentials, the bridge process's own app, and the
loopback HTTP hop between them.

What is not, and why
--------------------
The **adapter**. The validation adapter runs no program and calls no model: it
exercises the lifecycle deterministically. That is the point — this suite must
never spend model usage, touch the network, or run an agent on the machine.

A clarification round trip needs a provider that asks questions, and the
validation adapter does not. It is exercised here through a **sanitized fixture
adapter** defined in this file: a small in-process object that emits one
single-choice question with Cofferdam's own option ids, and records the answer
it is handed. No provider payload, no recorded session, no network.

What this suite deliberately does not prove
-------------------------------------------
That the Custom GPT works. Local HTTP over loopback says nothing about
ChatGPT's request shaping, its confirmation prompts, its retry behaviour or its
45-second budget. That is Gate A, and it needs a real Action call.
"""

from __future__ import annotations

import dataclasses
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

try:
    import httpx
    import uvicorn
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the extras are absent
    httpx = None
    uvicorn = None
    TestClient = None

DEVICE_TOKEN = "e2e-device-token-not-a-real-credential"
BRIDGE_INTERNAL_TOKEN = "e2e-internal-token-not-a-real-credential"
BRIDGE_EXTERNAL_KEY = "e2e-external-key-not-a-real-credential"
PROJECT_ID = "e2e-demo"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipIf(uvicorn is None, "workstation extras are not installed")
class LocalEndToEndTests(unittest.TestCase):
    """One disposable Cofferdam home, one real daemon, one real bridge."""

    @classmethod
    def setUpClass(cls) -> None:
        from cofferdam.actions_bridge.config import load_bridge_config
        from cofferdam.actions_bridge.internal import InternalTaskClient
        from cofferdam.actions_bridge.service import create_bridge_app
        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.service import create_app

        cls._home = tempfile.TemporaryDirectory()
        home = Path(cls._home.name)
        (home / "projects" / PROJECT_ID).mkdir(parents=True)

        config = load_config(home)
        config = type(config)(
            **{
                **config.__dict__,
                "enable_validation_task_adapter": True,
                "enable_actions_bridge_caller": True,
            }
        )
        config.ensure_dirs()
        (config.config_dir / "task-projects.json").write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "project_id": PROJECT_ID,
                            "display_name": "End-to-end demo",
                            "root": str(home / "projects" / PROJECT_ID),
                            "adapters": ["validation"],
                            "enabled": True,
                            "notes": "disposable; must not appear in any bridge response",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        config.actions_bridge_token_path.write_text(
            BRIDGE_INTERNAL_TOKEN + "\n", encoding="utf-8"
        )
        config.actions_bridge_token_path.chmod(0o600)
        cls.config = config
        cls.home = home

        # -- the real daemon, on a real loopback port ------------------------
        cls.daemon_port = _free_port()
        cls.daemon_app = create_app(
            config=config, token=DEVICE_TOKEN, adapter=StubAdapter(config)
        )
        cls.server = uvicorn.Server(
            uvicorn.Config(
                cls.daemon_app,
                host="127.0.0.1",
                port=cls.daemon_port,
                log_level="warning",
                access_log=False,
            )
        )
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if getattr(cls.server, "started", False):
                break
            time.sleep(0.05)
        else:  # pragma: no cover - a startup failure
            raise RuntimeError("the disposable daemon did not start")

        # -- the real bridge, pointed at it ----------------------------------
        # Rate limits raised for the suite, for the same reason the API tests
        # raise them: sixteen sequential end-to-end cases are not the traffic
        # the limiter exists to stop. RateLimitTests covers the real numbers.
        bridge_config = dataclasses.replace(
            load_bridge_config(
                home, internal_base_url=f"http://127.0.0.1:{cls.daemon_port}"
            ),
            rate_limit_per_minute=100000,
            rate_limit_burst=100000,
            mutation_rate_limit_per_minute=100000,
            mutation_rate_limit_burst=100000,
        )
        cls.internal_client = InternalTaskClient(
            base_url=bridge_config.internal_base_url,
            token=BRIDGE_INTERNAL_TOKEN,
            timeout=10.0,
        )
        cls.bridge_app = create_bridge_app(
            bridge_config,
            external_key=BRIDGE_EXTERNAL_KEY,
            internal_client=cls.internal_client,
        )
        cls.bridge = TestClient(cls.bridge_app)
        cls.auth = {"Authorization": "Bearer " + BRIDGE_EXTERNAL_KEY}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.should_exit = True
        cls.thread.join(timeout=20)
        cls.internal_client.close()
        cls.bridge_app.state.idempotency.close()
        cls._home.cleanup()

    def setUp(self) -> None:
        self._n = 0

    def key(self, label: str) -> str:
        self._n += 1
        return f"e2e-{label}-{id(self):x}-{self._n:03d}"

    def create(self, *, prompt: str = "scenario: complete", **overrides):
        body = {
            "project_id": PROJECT_ID,
            "task_text": prompt,
            "client_request_id": self.key("create"),
        }
        body.update(overrides)
        return self.bridge.post("/v1/tasks", headers=self.auth, json=body)

    # -- 1. list_projects ----------------------------------------------------

    def test_01_list_projects_over_the_real_daemon(self) -> None:
        response = self.bridge.get("/v1/projects", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        projects = response.json()["projects"]
        self.assertEqual([p["project_id"] for p in projects], [PROJECT_ID])
        self.assertEqual(projects[0]["task_adapters"], ["validation"])
        # The registry's note and root exist on disk and reach neither response.
        body = response.text
        self.assertNotIn("disposable", body)
        self.assertNotIn(str(self.home), body)

    # -- 2. create_task ------------------------------------------------------

    def test_02_create_task_runs_a_real_lifecycle(self) -> None:
        response = self.create()
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertTrue(body["task_id"].startswith("task_"))
        self.assertTrue(body["display_ref"].startswith("CF-"))
        self.assertTrue(body["created"])

        # And the daemon recorded it as the bridge's, not the phone's.
        row = self.daemon_app.state.tasks.store.get(body["task_id"])
        self.assertEqual(row.origin, "chatgpt_app")

    # -- 3. sync -------------------------------------------------------------

    def test_03_sync_returns_a_completed_state_and_result(self) -> None:
        task_id = self.create().json()["task_id"]
        response = self.bridge.get(f"/v1/tasks/{task_id}", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["state"], "completed")
        self.assertTrue(body["terminal"])
        self.assertTrue(body["result"]["available"])
        self.assertTrue(body["result"]["is_final"])
        self.assertTrue(body["result"]["succeeded"])
        self.assertIn("Validation scenario completed", body["result"]["text"])
        self.assertEqual(body["next_recommended_operation"], "nothing")
        self.assertFalse(body["artifacts_supported"])

    def test_04_sync_reports_a_failed_task_truthfully(self) -> None:
        task_id = self.create(prompt="scenario: fail").json()["task_id"]
        body = self.bridge.get(f"/v1/tasks/{task_id}", headers=self.auth).json()
        self.assertEqual(body["state"], "failed")
        self.assertFalse(body["result"]["succeeded"])
        self.assertIsNotNone(body["failure_code"])
        self.assertFalse(body["follow_up_available"])

    # -- 4. recent tasks -----------------------------------------------------

    def test_05_recent_tasks_recovers_a_lost_reference(self) -> None:
        created = self.create().json()
        response = self.bridge.get("/v1/tasks?limit=5", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        rows = response.json()["tasks"]
        self.assertLessEqual(len(rows), 5)
        match = [row for row in rows if row["task_id"] == created["task_id"]]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["display_ref"], created["display_ref"])
        # No content in a list, on the real path as well as against a double.
        self.assertNotIn("Validation scenario completed", response.text)

    # -- 5. idempotency ------------------------------------------------------

    def test_06_an_identical_create_retry_makes_one_real_task(self) -> None:
        request_id = self.key("replay")
        first = self.create(client_request_id=request_id)
        second = self.create(client_request_id=request_id)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["task_id"], second.json()["task_id"])
        self.assertTrue(second.json()["replayed"])

        rows = self.daemon_app.state.tasks.store.list_tasks(limit=100)
        matching = [row for row in rows if row.task_id == first.json()["task_id"]]
        self.assertEqual(len(matching), 1)

    def test_07_a_conflicting_reuse_is_refused_by_the_real_path(self) -> None:
        request_id = self.key("conflict")
        self.create(client_request_id=request_id)
        response = self.create(
            client_request_id=request_id, task_text="scenario: fail"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "idempotency_conflict")

    # -- 6. follow-up, cancel, finish ----------------------------------------

    def test_08_a_follow_up_reaches_the_same_task(self) -> None:
        """The ``wait`` scenario takes exactly one follow-up, then completes."""
        task_id = self.create(prompt="scenario: wait").json()["task_id"]
        waiting = self.bridge.get(f"/v1/tasks/{task_id}", headers=self.auth).json()
        self.assertEqual(waiting["state"], "waiting_for_user")

        response = self.bridge.post(
            f"/v1/tasks/{task_id}/followup",
            headers=self.auth,
            json={
                "followup_text": "carry on with the second step",
                "client_request_id": self.key("followup"),
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["accepted"])
        self.assertFalse(response.json()["replayed"])

        turns = self.daemon_app.state.tasks.store.turns(task_id)
        self.assertEqual(turns[-1].source, "future_gpt_bridge")

    def test_09_a_duplicate_follow_up_creates_one_turn(self) -> None:
        task_id = self.create(prompt="scenario: wait").json()["task_id"]
        request_id = self.key("dup-followup")
        payload = {
            "followup_text": "carry on",
            "client_request_id": request_id,
        }
        first = self.bridge.post(
            f"/v1/tasks/{task_id}/followup", headers=self.auth, json=payload
        )
        second = self.bridge.post(
            f"/v1/tasks/{task_id}/followup", headers=self.auth, json=payload
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["replayed"])
        # The `wait` scenario pauses *inside* its first turn, so the follow-up
        # closes that turn rather than opening a second — which is the correct
        # shape and the reason this asserts stability rather than a count of two.
        # What matters is that the replay added nothing.
        after_both = self.daemon_app.state.tasks.store.turns(task_id)
        third = self.bridge.post(
            f"/v1/tasks/{task_id}/followup", headers=self.auth, json=payload
        )
        self.assertTrue(third.json()["replayed"])
        self.assertEqual(
            len(self.daemon_app.state.tasks.store.turns(task_id)), len(after_both)
        )

    def test_10_cancellation_reaches_task_core(self) -> None:
        task_id = self.create(prompt="scenario: cancel").json()["task_id"]
        response = self.bridge.post(
            f"/v1/tasks/{task_id}/cancel",
            headers=self.auth,
            json={
                "client_request_id": self.key("cancel"),
                "reason": "user_changed_mind",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(response.json()["state"], ("cancelled", "cancelling"))

    def test_11_cancelling_one_task_leaves_another_alone(self) -> None:
        doomed = self.create(prompt="scenario: cancel").json()["task_id"]
        bystander = self.create(prompt="scenario: cancel").json()["task_id"]
        self.bridge.post(
            f"/v1/tasks/{doomed}/cancel",
            headers=self.auth,
            json={"client_request_id": self.key("cancel-one")},
        )
        other = self.bridge.get(f"/v1/tasks/{bystander}", headers=self.auth).json()
        self.assertNotIn(other["state"], ("cancelled", "cancelling"))

    def test_12_finish_from_an_illegal_state_is_refused_truthfully(self) -> None:
        """The validation adapter never reaches ``ready_for_followup``.

        So finish is genuinely illegal here, and the honest outcome is a bounded
        refusal rather than a success this build cannot deliver. Recorded as a
        limitation rather than skipped: the route is exercised, and what it
        proves is that an illegal transition comes back as ``not_allowed_now``.
        """
        task_id = self.create().json()["task_id"]
        response = self.bridge.post(
            f"/v1/tasks/{task_id}/finish",
            headers=self.auth,
            json={"client_request_id": self.key("finish")},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "not_allowed_now")

    # -- 7. the choice-answer round trip -------------------------------------

    def test_13_a_single_choice_question_round_trips(self) -> None:
        """Sanitized fixture question in, one option id out, no provider call.

        The fixture is stored through Task Core's own clarification layer, so
        the question the bridge publishes has real Cofferdam-minted option ids
        and the answer travels the real answer route. Nothing here contacts a
        provider: the adapter is replaced for the duration by a double that
        records what it was handed.
        """
        from cofferdam.workstation.tasks import clarifications as clar
        from cofferdam.workstation.tasks.delegated import ClarificationRequest

        # The `cancel` scenario simply stays running, which is the state a real
        # provider is in when it stops to ask something. Starting from `wait`
        # would already be `waiting_for_user`, and the transition below — the
        # one Task Core actually performs when a question arrives — would be
        # illegal rather than exercised.
        task_id = self.create(prompt="scenario: cancel").json()["task_id"]
        store = self.daemon_app.state.tasks.store
        registry = self.daemon_app.state.tasks.adapters
        adapter = registry.get("validation")

        request = ClarificationRequest.from_dict(
            {
                "category": "clarification",
                "question": "Where should the new test live?",
                "answer_mode": "single_choice",
                "allows_free_text": False,
                "schema_verified": True,
                "options": [
                    {
                        "label": "In the existing test file",
                        "value": "provider-value-alpha",
                        "option_id": "opt1",
                    },
                    {
                        "label": "In a new file",
                        "value": "provider-value-beta",
                        "option_id": "opt2",
                    },
                ],
            }
        )
        pending = clar.build_pending(
            task_id=task_id,
            provider="fixture",
            request=request,
            requested_at="2026-08-09T10:04:00Z",
        )
        # Stored through Task Core's own transition, so the question the bridge
        # reads back is one the daemon durably owns rather than a value injected
        # beside it.
        store.transition(
            task_id,
            "waiting_for_user",
            event_type="waiting_for_user",
            actor="adapter",
            source="cofferdam",
            detail="clarification",
            waiting_reason="clarification",
            open_clarification=pending,
        )

        delivered = []

        def _deliver(context, event_id, text):
            delivered.append(text)
            return True

        original = getattr(adapter, "deliver_clarification_answer", None)
        original_caps = adapter.capabilities
        adapter.deliver_clarification_answer = _deliver
        adapter.capabilities = lambda: original_caps().__class__(
            **{**original_caps().__dict__, "clarifications": True}
        )
        try:
            snapshot = self.bridge.get(
                f"/v1/tasks/{task_id}", headers=self.auth
            ).json()
            question = snapshot["pending_question"]
            self.assertTrue(question["clarification_supported"])
            self.assertEqual(question["answer_mode"], "single_choice")
            self.assertEqual(
                [option["option_id"] for option in question["options"]],
                ["opt1", "opt2"],
            )
            # The provider-facing option *value* never reaches the bridge.
            self.assertNotIn("provider-value-alpha", json.dumps(question))

            answer = self.bridge.post(
                f"/v1/tasks/{task_id}/answer",
                headers=self.auth,
                json={
                    "question_id": question["question_id"],
                    "option_id": "opt1",
                    "client_request_id": self.key("answer"),
                },
            )
            self.assertEqual(answer.status_code, 200, answer.text)
        finally:
            if original is not None:
                adapter.deliver_clarification_answer = original
            adapter.capabilities = original_caps

        self.assertEqual(len(delivered), 1, "one answer delivered, exactly once")
        stored = store.find_clarification(task_id, question["question_id"])
        self.assertEqual(stored.status, "answered")
        self.assertEqual(stored.answer.option_ids, ("opt1",))
        self.assertIsNone(stored.answer.text)
        self.assertEqual(
            stored.answer.provenance.source, clar.SOURCE_FUTURE_GPT_BRIDGE
        )

    # -- 8. no leakage on the real path --------------------------------------

    def test_14_no_secret_or_machine_value_crosses_the_bridge(self) -> None:
        task_id = self.create().json()["task_id"]
        bodies = [
            self.bridge.get("/v1/projects", headers=self.auth).text,
            self.bridge.get("/v1/tasks", headers=self.auth).text,
            self.bridge.get(f"/v1/tasks/{task_id}", headers=self.auth).text,
            self.bridge.get("/v1/health").text,
        ]
        forbidden = (
            DEVICE_TOKEN,
            BRIDGE_INTERNAL_TOKEN,
            BRIDGE_EXTERNAL_KEY,
            str(self.home),
            "/projects/",
            "provider_session_id",
            "correlation_id",
            "127.0.0.1",
            str(self.daemon_port),
            "disposable",
        )
        for body in bodies:
            for value in forbidden:
                with self.subTest(value=value):
                    self.assertNotIn(value, body)

    def test_15_the_daemon_still_refuses_the_bridge_elsewhere(self) -> None:
        """Over real HTTP this time, not through a TestClient shortcut."""
        with httpx.Client(
            base_url=f"http://127.0.0.1:{self.daemon_port}", timeout=10.0
        ) as client:
            for path in (
                "/api/status",
                "/api/actions",
                "/api/registries",
                "/api/remote-control/e2e-demo",
                "/api/task-adapters",
            ):
                with self.subTest(path=path):
                    response = client.get(
                        path,
                        headers={"Authorization": "Bearer " + BRIDGE_INTERNAL_TOKEN},
                    )
                    self.assertEqual(response.status_code, 401, path)
            # And the device token still works on the same routes.
            self.assertEqual(
                client.get(
                    "/api/status",
                    headers={"Authorization": "Bearer " + DEVICE_TOKEN},
                ).status_code,
                200,
            )

    def test_16_the_bridge_is_bound_to_loopback_by_default(self) -> None:
        from cofferdam.actions_bridge.config import (
            DEFAULT_BIND_HOST,
            load_bridge_config,
        )

        config = load_bridge_config(self.home)
        self.assertEqual(config.bind_host, DEFAULT_BIND_HOST)
        self.assertTrue(config.loopback_only)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
