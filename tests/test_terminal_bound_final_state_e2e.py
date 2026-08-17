"""M2K PR25 — the failed deployment smoke, recreated end to end and made to pass.

The 2026-08-17 Tier-2 deployment succeeded mechanically and failed its smoke. A
person declared ``path_exists("deploy-smoke.txt")`` from the PWA, the worker
created the file, and the acceptance section said the requirement was not met —
because PR14 had already recorded the file as absent three seconds before it
existed.

This module runs that path with nothing hand-built at either end:

* the **request** is produced by the shipped ``web/tasks.js`` through
  ``tests/tasks_harness.js``, so what is posted is what the panel actually sends
  when somebody chooses an explicit root and types a path;
* the **server** is the real ``create_app`` over real HTTP, with an
  **asynchronous** adapter whose worker runs between calls;
* the **read** is PR22's assessment route, unchanged, with the acceptance section
  PR21 folds.

A curl-shaped payload would have proved the backend and left the question of
whether a human can actually cause it open. That question is the one the
deployment answered badly.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - stdlib-only CI path
    TestClient = None

from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterOutcome,
    TaskAdapter,
)
from cofferdam.workstation.tasks.models import STATE_COMPLETED, STATE_RUNNING
from cofferdam.workstation.tasks.store import TaskStore

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_HARNESS = REPO_ROOT / "tests" / "tasks_harness.js"
DEVICE_TOKEN = "device-token-not-a-real-credential-0025"
PROJECT_ID = "demo"
TARGET = "deploy-smoke.txt"


def authored_request() -> dict:
    """The body the panel sends for an explicit root plus ``path_exists``."""
    node = shutil.which("node")
    if not node:  # pragma: no cover - depends on the runner
        raise unittest.SkipTest("node is not installed")
    completed = subprocess.run(
        [node, str(TASKS_HARNESS), "authoring-deploy-smoke-request"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:  # pragma: no cover - harness failure
        raise AssertionError("harness failed: " + completed.stderr[:800])
    payload = json.loads(completed.stdout)
    assert not payload.get("uncaught"), payload.get("uncaught")
    posts = payload["posts"]
    assert len(posts) == 1, f"expected one request, got {len(posts)}"
    return posts[0]["body"]


class DeploySmokeWorker(TaskAdapter):
    """Asynchronous, and the file appears strictly after ``start`` returns."""

    adapter_id = "validation"
    display_name = "Deploy Smoke Worker"

    def __init__(self):
        self.root = None
        self._pending = None

    def capabilities(self):
        return AdapterCapabilities(
            start=True, followup=True, structured_progress=True, final_result=True
        )

    def available(self):
        return True

    def session_available(self, task_id):
        return True

    def start(self, context):
        self.root = Path(context.project_root)
        return AdapterOutcome(requested_state=STATE_RUNNING)

    def work(self):
        (self.root / TARGET).write_text("smoke\n", encoding="utf-8")
        self._pending = STATE_COMPLETED

    def inspect(self, context):
        if self._pending is None:
            return AdapterOutcome()
        self._pending = None
        return AdapterOutcome(requested_state=STATE_COMPLETED, final_result="done")


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class DeploySmokeEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.service import create_app
        from cofferdam.workstation.tasks import build_registry
        from cofferdam.workstation.tasks.projects import load_projects
        from cofferdam.workstation.tasks.service import TaskService

        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.root = self.home / "projects" / PROJECT_ID
        self.root.mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "Test")
        (self.root / "seed.txt").write_text("seed\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")

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
                            "display_name": "Demo",
                            "root": str(self.root),
                            "adapters": ["validation"],
                            "enabled": True,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.config = config
        # The asynchronous worker is handed to `create_app` rather than swapped
        # in afterwards: the routes close over the service they were built with,
        # so an after-the-fact substitution would leave the HTTP layer talking to
        # a different one. Everything else — routes, auth, store, views — is the
        # shipped build.
        self.worker = DeploySmokeWorker()
        registry = type(build_registry(enable_validation_adapter=True))((self.worker,))
        self.service = TaskService(
            config,
            TaskStore(config),
            registry,
            projects=load_projects(config, registry.ids()),
        )
        self.addCleanup(self.service.store.close)
        self.app = create_app(
            config=config,
            token=DEVICE_TOKEN,
            adapter=StubAdapter(config),
            tasks=self.service,
        )
        self.client = TestClient(self.app)
        self.assertIs(self.service, self.app.state.tasks)

    def git(self, *arguments):
        subprocess.run(
            ("git",) + arguments, cwd=self.root, check=True, capture_output=True
        )

    def device(self) -> dict:
        return {"Authorization": "Bearer " + DEVICE_TOKEN}

    def assessment_url(self, task_id: str, turn: int = 1) -> str:
        return "/api/tasks/" + task_id + "/turns/" + str(turn) + "/assessment"

    def create(self) -> str:
        """One task, authored by the shipped panel and posted over real HTTP."""
        body = authored_request()
        response = self.client.post(
            "/api/tasks",
            headers=self.device(),
            json={**body, "project_id": PROJECT_ID, "adapter_id": "validation"},
        )
        self.assertEqual(201, response.status_code, response.text)
        return response.json()["task"]["task_id"]

    def test_the_human_authored_smoke_reaches_assessable_and_met(self):
        body = authored_request()

        # The panel really did author the requirement, rather than the test.
        self.assertEqual("root", body["continuity"]["mode"])
        self.assertEqual(
            [{"kind": "evidence", "predicate": "path_exists", "path": TARGET}],
            body["criteria"],
        )

        created = self.client.post(
            "/api/tasks",
            headers=self.device(),
            json={
                **body,
                "project_id": PROJECT_ID,
                "adapter_id": "validation",
            },
        )
        self.assertEqual(201, created.status_code, created.text)
        task_id = created.json()["task"]["task_id"]

        # The adapter returned `running` and the file does not exist. This is the
        # instant at which the deployment recorded `absent` as complete.
        self.assertFalse((self.root / TARGET).exists())
        self.assertEqual(
            "legacy_unknown", self.service.turn_final_state(task_id, 1).state
        )

        # Reading now must not fabricate an answer either.
        early = self.client.get(
            self.assessment_url(task_id), headers=self.device()
        )
        self.assertEqual(200, early.status_code, early.text)
        self.assertEqual(
            "not_assessable",
            early.json()["assessment"]["acceptance"]["availability"],
        )

        # The worker does its work, then reports terminal.
        self.worker.work()
        self.service.refresh_task(task_id)

        observation = self.service.turn_final_state(task_id, 1)
        self.assertEqual("complete", observation.state)
        self.assertEqual(2, observation.observer_version)
        self.assertEqual(
            [("deploy-smoke.txt", "present", "file")],
            [(item.path, item.state, item.kind) for item in observation.paths],
        )

        response = self.client.get(
            self.assessment_url(task_id), headers=self.device()
        )
        self.assertEqual(200, response.status_code, response.text)
        acceptance = response.json()["assessment"]["acceptance"]
        self.assertEqual("assessable", acceptance["availability"])
        self.assertEqual("met", acceptance["outcome"])
        self.assertEqual(1, acceptance["counts"]["met"])
        self.assertEqual(0, acceptance["counts"]["not_met"])
        self.assertEqual(0, acceptance["counts"]["unverified"])

    def test_the_assessment_response_shape_did_not_change(self):
        """PR22's HTTP contract is untouched; only nested version values moved."""
        from cofferdam.workstation.tasks.assessment import ASSESSMENT_API_VERSION

        self.assertEqual(1, ASSESSMENT_API_VERSION)

        task_id = self.create()
        self.worker.work()
        self.service.refresh_task(task_id)

        payload = self.client.get(
            self.assessment_url(task_id), headers=self.device()
        ).json()["assessment"]
        self.assertEqual(ASSESSMENT_API_VERSION, payload["version"])
        self.assertEqual(
            {"version", "task_id", "turn_number", "criteria", "evaluation", "acceptance"},
            set(payload),
        )
        # The one nested version this layer publishes. `AGGREGATOR_VERSION` did
        # not move in PR25 and neither did the response shape; the semantic
        # change is carried by the assessment fingerprint composed beneath it.
        self.assertEqual(1, payload["acceptance"]["aggregator_version"])
        self.assertEqual(
            {
                "aggregator_version", "availability", "availability_reason",
                "unavailable_cause", "unavailable_at_turn_number", "outcome",
                "counts", "requires_human", "assessment_fingerprint",
                "acceptance_fingerprint",
            },
            set(payload["acceptance"]),
        )

    def test_repeated_reads_change_nothing(self):
        import hashlib

        task_id = self.create()
        self.worker.work()
        self.service.refresh_task(task_id)

        database = self.service.store.path

        def digest():
            return hashlib.sha256(database.read_bytes()).hexdigest()

        # `generated_at` is when the response was built and is expected to move;
        # everything the response *asserts* must not.
        first = self.client.get(
            self.assessment_url(task_id), headers=self.device()
        ).json()["assessment"]
        before = digest()
        for _ in range(20):
            again = self.client.get(
                self.assessment_url(task_id), headers=self.device()
            ).json()["assessment"]
            self.assertEqual(first, again)
        self.assertEqual(before, digest())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
