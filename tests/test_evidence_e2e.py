"""M2K PR2 — one isolated end-to-end pass over the whole feature.

An isolated ``COFFERDAM_HOME`` and a synthetic project. No real provider, no
model, no browser, no media, and nothing that touches the production host.

The unit tests each pin one rule. This one walks the path a real turn takes —
schema opens, task created, turn opens at an exact cursor, a claim is recorded,
an ingestion row lands, an observation is appended, the turn closes at an exact
cursor, the bundle is derived and served — and asserts the properties that only
show up when all of those happen in order.
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

from cofferdam.workstation.tasks.claims import CLAIM_MODIFIED, ClaimSubmission
from cofferdam.workstation.tasks.store import (
    SCHEMA_VERSION,
    TaskStore,
    _TurnClose,
    _TurnDraft,
)

from tests.test_evidence_bundle import head_observation, path_observation

DEVICE_TOKEN = "device-token-not-a-real-credential-e2e"
BRIDGE_TOKEN = "bridge-internal-token-not-real-e2e"
PROJECT_ID = "synthetic"


class EvidenceEndToEnd(unittest.TestCase):
    """Steps 1-22 of the validation list, against a real store."""

    def setUp(self):
        from cofferdam.workstation.config import load_config

        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr2-e2e-")
        self.addCleanup(self._temp.cleanup)
        self.home = Path(self._temp.name)
        self.root = self.home / "synthetic-project"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "foo.py").write_text("print('hi')\n", encoding="utf-8")
        (self.root / "src" / "bar.py").write_text("print('there')\n", encoding="utf-8")

        config = load_config(self.home)
        config.ensure_dirs()
        self.config = config
        self.database = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.store = TaskStore(config)
        self.addCleanup(self._close)

    def _close(self):
        try:
            self.store.close()
        except Exception:
            pass

    # -- helpers ----------------------------------------------------------

    def _task(self) -> str:
        row, _ = self.store.create_task(
            origin="pwa",
            adapter_id="validation",
            project_id=PROJECT_ID,
            prompt="change two files",
            title="synthetic",
        )
        return row.task_id

    def _move(self, task_id, state, **kwargs):
        return self.store.transition(
            task_id,
            state,
            event_type=kwargs.pop("event_type", "task_" + state),
            actor=kwargs.pop("actor", "system"),
            source=kwargs.pop("source", "cofferdam"),
            **kwargs,
        )

    def _run(self, task_id):
        for state in ("queued", "starting", "running"):
            self._move(task_id, state)

    def _cursor(self, task_id) -> int:
        return self.store.get(task_id).event_cursor

    # -- the walk ---------------------------------------------------------

    def test_the_whole_path(self):
        # 1. the current schema opens. v6 since M2K PR4; the bundle's inputs are
        #    unchanged by it, which is what the rest of this walk re-proves.
        self.assertEqual(SCHEMA_VERSION, 6)
        self.assertEqual(self.store.storage_health()["schema_version"], 6)

        # 2. a task is created.
        task_id = self._task()
        self._run(task_id)

        # 3. a turn opens at the exact current cursor.
        floor = self._cursor(task_id)
        self.store.open_turn(
            task_id,
            provider="validation",
            source="internal_test",
            started_at="2026-08-14T00:00:00Z",
        )
        bound = self.store.turn_bound(task_id, 1)
        self.assertEqual(bound.opened_after_event_sequence, floor)
        self.assertIsNone(bound.closed_through_event_sequence)

        # 6. an eligible observation is appended — alongside a HEAD observation,
        #    which must not be mistaken for a path.
        self.store.append_event(
            task_id,
            "progress",
            actor="system",
            source="cofferdam",
            text="Cofferdam checked the project itself.",
            evidence=(head_observation(), path_observation("src/foo.py")),
        )

        # 4, 5. a claim is recorded, and its ingestion row with it.
        claims, artifacts, ingestion = self.store.record_change_claims(
            task_id,
            (
                ClaimSubmission(operation=CLAIM_MODIFIED, path="src/foo.py"),
                ClaimSubmission(operation=CLAIM_MODIFIED, path="src/bar.py"),
            ),
            project_root=self.root,
            turn_number=1,
        )
        self.assertEqual(len(claims), 2)
        self.assertEqual(ingestion.submitted, 2)

        # 7. the turn closes at the exact cursor.
        self._move(
            task_id,
            "ready_for_followup",
            event_type="turn_complete",
            actor="adapter",
            source="adapter",
            close_turn=_TurnClose(
                outcome="completed", completed_at="2026-08-14T00:05:00Z"
            ),
        )
        bound = self.store.turn_bound(task_id, 1)
        self.assertEqual(bound.closed_through_event_sequence, self._cursor(task_id))

        # 8. the bundle is derived.
        bundle = self.store.evidence_bundle(task_id, 1)
        self.assertIsNotNone(bundle)
        groups = {group.path: group for group in bundle.relationships}

        # 9, 10. path_agreed, and the operation is not established.
        self.assertEqual(groups["src/foo.py"].relationship, "path_agreed")
        self.assertTrue(groups["src/foo.py"].path_agreement)
        self.assertEqual(groups["src/foo.py"].operation_agreement, "unknown")

        # 19. claim_only for the path nothing observed.
        self.assertEqual(groups["src/bar.py"].relationship, "claim_only")

        # 11, 12. provenance is untouched by matching.
        published = bundle.to_dict()
        self.assertEqual(published["claims"][0]["source"], "adapter_reported")
        self.assertFalse(published["claims"][0]["verified"])
        self.assertEqual(published["observations"][0]["source"], "git_observed")
        self.assertTrue(published["observations"][0]["verified"])

        # The HEAD observation is not among them.
        self.assertEqual([o.path for o in bundle.observations], ["src/foo.py"])

        # 13, 14. completeness and assembler version are present.
        self.assertEqual(bundle.ingestion.state, "complete")
        # 3 since M2K PR5, having been 2 since PR3: the rules that produced this
        # bundle changed twice — machine observation semantics, then committed-
        # range evidence and per-domain relationships — even though the bundle
        # *shape* stayed compatible both times.
        self.assertEqual(bundle.assembler_version, 3)

        # 15. the fingerprint survives a reopen.
        fingerprint = bundle.input_fingerprint
        self.store.close()
        self.store = TaskStore(self.config)
        self.assertEqual(
            self.store.evidence_bundle(task_id, 1).input_fingerprint, fingerprint
        )

        # 16. a second turn opens at the exact next boundary.
        self._move(
            task_id,
            "running",
            event_type="followup_received",
            actor="user",
            open_turn=_TurnDraft(
                provider="validation",
                source="internal_test",
                started_at="2026-08-14T00:06:00Z",
            ),
        )
        second = self.store.turn_bound(task_id, 2)
        self.assertGreaterEqual(
            second.opened_after_event_sequence, bound.closed_through_event_sequence
        )

        # 17, 18. turn two's evidence cannot reach turn one.
        self.store.append_event(
            task_id,
            "progress",
            actor="system",
            source="cofferdam",
            text="Cofferdam checked the project again.",
            evidence=(path_observation("src/bar.py"),),
        )
        again = self.store.evidence_bundle(task_id, 1)
        self.assertEqual(again.input_fingerprint, fingerprint)
        self.assertEqual(
            {g.path: g.relationship for g in again.relationships}["src/bar.py"],
            "claim_only",
        )

        # 20. observed_only in turn two, which claimed nothing.
        turn_two = self.store.evidence_bundle(task_id, 2)
        self.assertEqual(
            [g.relationship for g in turn_two.relationships], ["observed_only"]
        )

        # 21. an incomplete report reads as incomplete.
        self.store.record_change_claims(
            task_id,
            (
                ClaimSubmission(operation=CLAIM_MODIFIED, path="src/bar.py"),
                ClaimSubmission(operation="teleported", path="src/bar.py"),
            ),
            project_root=self.root,
            turn_number=2,
        )
        self.assertEqual(
            self.store.evidence_bundle(task_id, 2).ingestion.state, "incomplete"
        )

        # 26. repeated reads create no state.
        with sqlite3.connect(str(self.database)) as db:
            before = db.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
        for _ in range(25):
            self.store.evidence_bundle(task_id, 1)
            self.store.evidence_bundle(task_id, 2)
        with sqlite3.connect(str(self.database)) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM task_events").fetchone()[0], before
            )

        # 27-31. nothing an evaluator, a verdict, a check runner or a provider
        # would need exists on this path.
        payload = json.dumps(self.store.evidence_bundle(task_id, 1).to_dict()).lower()
        for forbidden in ("verdict", "confidence", "risk", "check", "provider"):
            self.assertNotIn(forbidden, payload, forbidden)

    def test_a_legacy_turn_in_the_same_database(self):
        """22. A v4-era turn beside v5 turns, in one task, reads honestly."""
        task_id = self._task()
        self._run(task_id)
        self.store.append_event(
            task_id,
            "progress",
            actor="system",
            source="cofferdam",
            text="Cofferdam checked the project itself.",
            evidence=(path_observation("src/foo.py"),),
        )
        with sqlite3.connect(str(self.database)) as db:
            db.execute(
                "INSERT INTO task_turns (task_id, turn_number, provider, source,"
                " started_at, completed_at, outcome) VALUES"
                " (?, 1, 'validation', 'internal_test', '2026-08-01T00:00:00Z',"
                " '2026-08-01T00:05:00Z', 'completed')",
                (task_id,),
            )
        self.store.close()
        self.store = TaskStore(self.config)

        bundle = self.store.evidence_bundle(task_id, 1)
        self.assertEqual(bundle.turn_attribution, "legacy_unknown")
        self.assertEqual(bundle.observations, ())
        self.assertIn("legacy_turn_attribution_unavailable", bundle.limitations)
        # The task-wide observation above must not have been borrowed.
        self.assertEqual(bundle.relationships, ())


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class EvidenceOverTheApi(unittest.TestCase):
    """23-25, 34-36: the route, the credentials, and the bridge's silence."""

    def setUp(self):
        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.service import create_app

        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr2-e2e-api-")
        self.addCleanup(self._temp.cleanup)
        self.home = Path(self._temp.name)
        self.root = self.home / "synthetic-project"
        self.root.mkdir(parents=True)

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
                            "display_name": "Synthetic",
                            "root": str(self.root),
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

        self.app = create_app(
            config=config, token=DEVICE_TOKEN, adapter=StubAdapter(config)
        )
        self.client = TestClient(self.app)
        self.device = {"Authorization": "Bearer " + DEVICE_TOKEN}
        self.bridge = {"Authorization": "Bearer " + BRIDGE_TOKEN}

        response = self.client.post(
            "/api/tasks",
            headers=self.device,
            json={
                "project_id": PROJECT_ID,
                "adapter_id": "validation",
                "prompt": "complete: change something",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.task_id = response.json()["task"]["task_id"]

    def url(self, turn=1):
        return "/api/tasks/%s/turns/%d/evidence" % (self.task_id, turn)

    def test_the_private_route_serves_the_device_token(self):
        response = self.client.get(self.url(), headers=self.device)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["evidence"]["task_id"], self.task_id)
        self.assertEqual(body["evidence"]["turn_number"], 1)

    def test_the_bridge_credential_is_refused(self):
        self.assertEqual(
            self.client.get(self.url(), headers=self.bridge).status_code, 401
        )

    def test_the_bridge_surface_did_not_grow(self):
        """36: `artifacts_supported` is still false and there is no new Action."""
        from cofferdam.actions_bridge import normalize

        import inspect

        source = inspect.getsource(normalize)
        self.assertIn('"artifacts_supported": False', source)
        for forbidden in ("evidence", "change_claim", "artifact_record", "turn_bound"):
            self.assertNotIn(forbidden, source.lower(), forbidden)

    def test_reading_evidence_creates_nothing(self):
        database = self.home / "state" / "tasks" / "tasks.sqlite3"
        with sqlite3.connect(str(database)) as db:
            before = db.execute("SELECT * FROM task_events").fetchall()
        for _ in range(15):
            self.client.get(self.url(), headers=self.device)
        with sqlite3.connect(str(database)) as db:
            self.assertEqual(db.execute("SELECT * FROM task_events").fetchall(), before)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
