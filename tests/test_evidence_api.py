"""M2K PR2 — the private, turn-qualified evidence route.

    GET /api/tasks/{task_id}/turns/{turn_number}/evidence

Device token only. The Actions bridge reads ten task routes with its own
credential and this is deliberately not an eleventh: it is guarded by
``require_token``, which has never heard of the bridge credential, so a bridge
request arrives as an ordinary unauthenticated one. That is asserted here rather
than assumed, because "the bridge cannot read evidence" is a sentence somebody
will rely on when deciding what a model provider can see.

The zero-mutation tests are the other half. A route that describes what a task
did must not be able to change what it did, and a client polling it must not be
able to drive an adapter, grow the event log or open a turn by looking.
"""

from __future__ import annotations

import json
import sqlite3
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
class EvidenceApiTestCase(unittest.TestCase):
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
        path = config.actions_bridge_token_path
        path.write_text(BRIDGE_TOKEN + "\n", encoding="utf-8")
        path.chmod(0o600)

        self.config = config
        self.database = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.app = create_app(
            config=config, token=DEVICE_TOKEN, adapter=StubAdapter(config)
        )
        self.client = TestClient(self.app)
        self.task_id = self._create()

    def device(self) -> dict:
        return {"Authorization": "Bearer " + DEVICE_TOKEN}

    def bridge(self) -> dict:
        return {"Authorization": "Bearer " + BRIDGE_TOKEN}

    def _create(self) -> str:
        response = self.client.post(
            "/api/tasks",
            headers=self.device(),
            json={
                "project_id": PROJECT_ID,
                "adapter_id": "validation",
                "prompt": "complete: do a thing",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["task"]["task_id"]

    def url(self, turn: object = 1, task_id: str = None) -> str:
        return "/api/tasks/%s/turns/%s/evidence" % (task_id or self.task_id, turn)

    def snapshot(self):
        """Every row of every task table, for the zero-mutation assertions."""
        with sqlite3.connect(str(self.database)) as db:
            return {
                name: db.execute("SELECT * FROM " + name).fetchall()
                for name in (
                    "tasks",
                    "task_events",
                    "task_turns",
                    "task_turn_bounds",
                    "task_change_claims",
                    "task_artifacts",
                    "task_claim_ingestion",
                    "task_clarifications",
                )
            }


class AuthTests(EvidenceApiTestCase):
    def test_the_device_token_reads_it(self):
        response = self.client.get(self.url(), headers=self.device())
        self.assertEqual(response.status_code, 200, response.text)

    def test_no_token_is_refused(self):
        self.assertEqual(self.client.get(self.url()).status_code, 401)

    def test_a_wrong_token_is_refused(self):
        response = self.client.get(
            self.url(), headers={"Authorization": "Bearer nope"}
        )
        self.assertEqual(response.status_code, 401)

    def test_the_bridge_credential_is_refused(self):
        """The sentence somebody will rely on. `require_token`, not the pair."""
        response = self.client.get(self.url(), headers=self.bridge())
        self.assertEqual(response.status_code, 401, response.text)

    def test_the_bridge_is_refused_even_for_a_task_it_could_read(self):
        detail = self.client.get("/api/tasks/" + self.task_id, headers=self.bridge())
        self.assertEqual(detail.status_code, 200, detail.text)
        evidence = self.client.get(self.url(), headers=self.bridge())
        self.assertEqual(evidence.status_code, 401)

    def test_the_route_is_not_on_the_bridge_surface(self):
        """Asserted against the dependency, not only against a response code."""
        import inspect

        from cofferdam.workstation import service as module

        source = inspect.getsource(module.create_app)
        marker = '"/api/tasks/{task_id}/turns/{turn_number}/evidence"'
        self.assertIn(marker, source)
        block = source[source.index(marker) : source.index(marker) + 400]
        self.assertIn("require_token", block)
        self.assertNotIn("require_task_caller", block)


class MethodAndParameterTests(EvidenceApiTestCase):
    def test_only_get_is_supported(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = self.client.request(
                method, self.url(), headers=self.device(), json={}
            )
            self.assertEqual(response.status_code, 405, method)

    def test_an_unknown_turn_is_not_found(self):
        response = self.client.get(self.url(99), headers=self.device())
        self.assertEqual(response.status_code, 404)

    def test_a_turn_number_below_one_is_refused(self):
        response = self.client.get(self.url(0), headers=self.device())
        self.assertEqual(response.status_code, 422)

    def test_a_non_numeric_turn_is_refused(self):
        response = self.client.get(self.url("latest"), headers=self.device())
        self.assertEqual(response.status_code, 422)

    def test_an_unknown_task_is_not_found(self):
        response = self.client.get(
            self.url(1, "task_nope"), headers=self.device()
        )
        self.assertEqual(response.status_code, 404)

    def test_there_is_no_root_or_path_selector(self):
        """Nothing in this request can name a location on the host."""
        for query in (
            "?root=/etc",
            "?path=/etc/passwd",
            "?project_root=/",
            "?policy=wide",
            "?include_preview=1",
        ):
            response = self.client.get(self.url() + query, headers=self.device())
            self.assertEqual(response.status_code, 200, query)
            payload = json.dumps(response.json())
            self.assertNotIn("/etc", payload)
            self.assertNotIn("passwd", payload)

    def test_the_response_is_no_store(self):
        response = self.client.get(self.url(), headers=self.device())
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_the_response_is_bounded(self):
        response = self.client.get(self.url(), headers=self.device())
        self.assertLess(len(response.content), 256 * 1024)


class PayloadTests(EvidenceApiTestCase):
    def payload(self):
        response = self.client.get(self.url(), headers=self.device())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_the_envelope_carries_the_bundle_and_presentation_metadata(self):
        body = self.payload()
        self.assertIn("evidence", body)
        self.assertIn("generated_at", body)

    def test_generated_at_is_outside_the_bundle(self):
        """Presentation metadata, never bundle identity."""
        bundle = self.payload()["evidence"]
        self.assertNotIn("generated_at", bundle)
        self.assertNotIn("built_at", bundle)

    def test_the_fingerprint_is_stable_across_reads_despite_generated_at(self):
        first, second = self.payload(), self.payload()
        self.assertNotEqual(first["generated_at"], "")
        self.assertEqual(
            first["evidence"]["input_fingerprint"],
            second["evidence"]["input_fingerprint"],
        )
        self.assertEqual(first["evidence"], second["evidence"])

    def test_the_bundle_publishes_its_versions_and_attribution(self):
        bundle = self.payload()["evidence"]
        self.assertEqual(bundle["version"], 1)
        self.assertEqual(bundle["assembler_version"], 2)
        self.assertIn(bundle["turn_attribution"], ("exact", "legacy_unknown"))

    def test_the_bundle_carries_no_artifact_body(self):
        payload = json.dumps(self.payload())
        self.assertNotIn("preview", payload)
        self.assertNotIn("digest", payload)

    def test_the_bundle_carries_no_provider_session_id(self):
        self.assertNotIn("provider_session_id", json.dumps(self.payload()))

    def test_the_bundle_carries_no_verdict_vocabulary(self):
        payload = json.dumps(self.payload()).lower()
        for forbidden in ("verdict", "confidence", "risk", "trusted", "lying"):
            self.assertNotIn(forbidden, payload, forbidden)


class ZeroMutationTests(EvidenceApiTestCase):
    def test_repeated_reads_change_no_row(self):
        before = self.snapshot()
        for _ in range(20):
            self.assertEqual(
                self.client.get(self.url(), headers=self.device()).status_code, 200
            )
        self.assertEqual(self.snapshot(), before)

    def test_reading_creates_no_event(self):
        before = len(self.snapshot()["task_events"])
        for _ in range(10):
            self.client.get(self.url(), headers=self.device())
        self.assertEqual(len(self.snapshot()["task_events"]), before)

    def test_reading_creates_no_turn_or_bound(self):
        before = self.snapshot()
        for _ in range(10):
            self.client.get(self.url(), headers=self.device())
        after = self.snapshot()
        self.assertEqual(after["task_turns"], before["task_turns"])
        self.assertEqual(after["task_turn_bounds"], before["task_turn_bounds"])

    def test_reading_creates_no_claim_artifact_or_ingestion(self):
        before = self.snapshot()
        for _ in range(10):
            self.client.get(self.url(), headers=self.device())
        after = self.snapshot()
        for table in ("task_change_claims", "task_artifacts", "task_claim_ingestion"):
            self.assertEqual(after[table], before[table], table)

    def test_a_not_found_read_changes_nothing_either(self):
        before = self.snapshot()
        self.client.get(self.url(99), headers=self.device())
        self.client.get(self.url(1, "task_nope"), headers=self.device())
        self.assertEqual(self.snapshot(), before)

    def test_an_unauthenticated_read_changes_nothing(self):
        before = self.snapshot()
        self.client.get(self.url())
        self.client.get(self.url(), headers=self.bridge())
        self.assertEqual(self.snapshot(), before)

    def test_reading_does_not_refresh_the_task(self):
        """No adapter call. The task's updated_at is untouched by a read."""
        with sqlite3.connect(str(self.database)) as db:
            before = db.execute(
                "SELECT updated_at, lifecycle_revision, event_cursor FROM tasks"
            ).fetchall()
        for _ in range(5):
            self.client.get(self.url(), headers=self.device())
        with sqlite3.connect(str(self.database)) as db:
            after = db.execute(
                "SELECT updated_at, lifecycle_revision, event_cursor FROM tasks"
            ).fetchall()
        self.assertEqual(after, before)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
