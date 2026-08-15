"""M2K PR8 — one end-to-end pass over the whole chain, isolated home and real Git.

Criteria frozen before dispatch (PR6), evidence observed and the turn closed
(PR2–PR5), the evaluation written after the close (PR7), and then the one thing
PR8 adds: a private GET that publishes both and changes nothing.

The absences are half the point and are asserted here rather than assumed: no
schema change, no aggregate, no pass/fail, no confidence, no risk, no evaluator
run on a read, no bridge Action.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - stdlib-only CI path
    TestClient = None

REPO_ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")
DEVICE_TOKEN = "device-token-not-a-real-credential-0001"
BRIDGE_TOKEN = "bridge-internal-token-not-real-0002"
PROJECT_ID = "demo"
GIT_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "T",
    "GIT_AUTHOR_EMAIL": "t@e.st",
    "GIT_COMMITTER_NAME": "T",
    "GIT_COMMITTER_EMAIL": "t@e.st",
}

CRITERIA = [
    {"kind": "evidence", "predicate": "path_changed", "path": "src/app.py"},
    {"kind": "evidence", "predicate": "path_changed", "path": "src/never.py"},
    {"kind": "manual", "description": "a person confirms the page renders"},
]


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
@unittest.skipIf(GIT is None, "git is not installed")
class AssessmentEndToEnd(unittest.TestCase):
    def setUp(self):
        import tempfile

        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.service import create_app

        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.project_root = self.home / "projects" / PROJECT_ID
        (self.project_root / "src").mkdir(parents=True)
        (self.project_root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "before")

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
            json.dumps({"projects": [{
                "project_id": PROJECT_ID, "display_name": "Demo",
                "root": str(self.project_root), "adapters": ["validation"],
                "enabled": True,
            }]}),
            encoding="utf-8",
        )
        config.actions_bridge_token_path.write_text(BRIDGE_TOKEN + "\n", encoding="utf-8")
        config.actions_bridge_token_path.chmod(0o600)

        self.config = config
        self.database = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.app = create_app(config=config, token=DEVICE_TOKEN, adapter=StubAdapter(config))
        self.client = TestClient(self.app)
        self.service = self.app.state.tasks

    def git(self, *args):
        subprocess.run(
            [GIT, *args], cwd=str(self.project_root), check=True, capture_output=True,
            env={**GIT_ENV, "HOME": str(self.project_root)},
        )

    def device(self):
        return {"Authorization": "Bearer " + DEVICE_TOKEN}

    def start(self, criteria=CRITERIA):
        row, _ = self.service.create_task(
            project_id=PROJECT_ID, adapter_id="validation",
            prompt="scenario: complete", origin="pwa", criteria=criteria,
        )
        return row

    def assessment(self, task_id, turn=1):
        return self.client.get(
            "/api/tasks/%s/turns/%s/assessment" % (task_id, turn), headers=self.device()
        )

    def snapshot(self):
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            names = [r[0] for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {n: connection.execute("SELECT * FROM " + n).fetchall() for n in names}
        finally:
            connection.close()

    # -- the walk ------------------------------------------------------------

    def test_the_walk(self):
        # 1. schema unchanged by this PR
        from cofferdam.workstation.tasks.store import SCHEMA_VERSION
        from cofferdam.workstation.tasks.evaluation import EVALUATOR_VERSION
        from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION

        self.assertEqual(SCHEMA_VERSION, 8, "PR8 is a read surface; the schema stays v8")
        self.assertEqual(EVALUATOR_VERSION, 1)
        self.assertEqual(ASSEMBLER_VERSION, 3)

        # 2-4. criteria before dispatch, evidence, evaluation after close
        row = self.start()
        self.assertEqual(self.service.turn_criteria(row.task_id, 1).state, "present")
        self.assertIsNotNone(self.service.turn_evaluation(row.task_id, 1))

        # 5-7. the published view
        response = self.assessment(row.task_id)
        self.assertEqual(response.status_code, 200)
        view = response.json()["assessment"]
        snapshot = self.service.turn_criteria(row.task_id, 1)
        stored = self.service.turn_evaluation(row.task_id, 1)
        bundle = self.service.evidence_bundle(row.task_id, 1)
        self.assertEqual(view["criteria"]["state"], "present")
        self.assertEqual(view["criteria"]["snapshot_id"], snapshot.snapshot_id)
        self.assertEqual(view["evaluation"]["evaluation_id"], stored.evaluation_id)
        self.assertEqual(
            view["evaluation"]["evidence_input_fingerprint"], bundle.input_fingerprint
        )
        self.assertEqual(view["evaluation"]["assembler_version"], 3)
        self.assertEqual(view["evaluation"]["evaluator_version"], 1)

        # 8-11. every result word is from the closed vocabulary, manual included
        results = {r["ordinal"]: r for r in view["evaluation"]["results"]}
        self.assertEqual(len(results), 3)
        for row_result in results.values():
            self.assertIn(row_result["result"], ("met", "not_met", "unverified"))
        self.assertEqual(results[3]["result"], "unverified")
        self.assertEqual(results[3]["reason"], "manual_criterion")

        # 19-21. nothing that looks like a verdict
        blob = response.text
        for forbidden in ("overall", '"passed"', '"failed"', "confidence", '"risk"', "score"):
            self.assertNotIn(forbidden, blob, forbidden)

        # 15-16. repeated reads change nothing, and the repository is not an input
        before = self.snapshot()
        for _ in range(10):
            self.assertEqual(self.assessment(row.task_id).status_code, 200)
        self.assertEqual(self.snapshot(), before)

        first = self.assessment(row.task_id).json()["assessment"]
        shutil.rmtree(self.project_root)
        after = self.assessment(row.task_id).json()["assessment"]
        self.assertEqual(
            json.dumps(first, sort_keys=True), json.dumps(after, sort_keys=True)
        )
        self.assertEqual(self.snapshot(), before)

    def test_a_not_provided_turn_is_explicit_and_not_a_pass(self):
        row = self.start(criteria=None)
        view = self.assessment(row.task_id).json()["assessment"]
        self.assertEqual(view["criteria"]["state"], "not_provided")
        self.assertTrue(view["criteria"]["recorded"])
        self.assertEqual(view["criteria"]["items"], [])
        self.assertEqual(view["evaluation"]["state"], "recorded")
        self.assertEqual(view["evaluation"]["result_count"], 0)

    def test_a_legacy_turn_gets_no_fabricated_evaluation(self):
        row = self.start(criteria=None)
        with sqlite3.connect(str(self.database)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("DELETE FROM task_turn_evaluations WHERE task_id=?", (row.task_id,))
            db.execute("DELETE FROM task_turn_criteria WHERE task_id=?", (row.task_id,))
        view = self.assessment(row.task_id).json()["assessment"]
        self.assertEqual(view["criteria"]["state"], "legacy_unknown")
        self.assertFalse(view["criteria"]["recorded"])
        self.assertEqual(view["evaluation"]["state"], "criteria_legacy_unknown")
        self.assertIsNone(view["evaluation"]["evaluation_id"])

    def test_a_missing_evaluation_is_neutral_and_no_read_repairs_it(self):
        row = self.start()
        with sqlite3.connect(str(self.database)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("DELETE FROM task_turn_evaluations WHERE task_id=?", (row.task_id,))
        before = self.snapshot()
        for _ in range(6):
            view = self.assessment(row.task_id).json()["assessment"]
            self.assertEqual(view["evaluation"]["state"], "not_recorded")
        self.assertEqual(self.snapshot(), before)

    def test_the_bridge_credential_is_refused(self):
        row = self.start()
        refused = self.client.get(
            "/api/tasks/%s/turns/1/assessment" % row.task_id,
            headers={"Authorization": "Bearer " + BRIDGE_TOKEN},
        )
        self.assertEqual(refused.status_code, 401)

    def test_no_evaluator_runs_on_a_get(self):
        row = self.start()

        def poison(*args, **kwargs):
            raise AssertionError("a GET reached a writer")

        saved = (self.service.store.record_evaluation, self.service.evaluate_closed_turns)
        self.service.store.record_evaluation = poison
        self.service.evaluate_closed_turns = poison
        try:
            self.assertEqual(self.assessment(row.task_id).status_code, 200)
        finally:
            (
                self.service.store.record_evaluation,
                self.service.evaluate_closed_turns,
            ) = saved

    def test_the_bridge_surface_gained_nothing(self):
        bridge = REPO_ROOT / "cofferdam" / "actions_bridge"
        offenders = [
            path.name
            for path in sorted(bridge.rglob("*.py"))
            if "assessment" in path.read_text(encoding="utf-8").lower()
        ]
        self.assertEqual(offenders, [])

    def test_no_check_runner_or_command_appeared(self):
        package = REPO_ROOT / "cofferdam" / "workstation" / "tasks"
        for path in sorted(package.glob("*.py")):
            if path.name in ("gitbaseline.py", "gitrange.py"):
                continue  # PR4 and PR5's host-owned Git probes
            self.assertNotIn("subprocess.", path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
