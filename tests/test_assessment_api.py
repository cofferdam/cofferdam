"""M2K PR8 — the private assessment route: auth, errors, and what it cannot do.

Two properties this module exists to hold.

**It is a read.** A GET here must never run the evaluator, create an
EvaluationRecord, trigger recovery, append an event, or move a task's lifecycle.
That is asserted by poisoning every one of those and issuing the request anyway,
and again by snapshotting every table and comparing after a hundred reads.

**The bridge cannot reach it.** The route uses `require_token`, which has never
heard of the Actions bridge credential, so a bridge request arrives as an
ordinary unauthenticated one. This is the same choice the evidence route made and
for a stronger version of the same reason: an assessment is Cofferdam's judgement
about somebody's work against what they asked for.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - stdlib-only CI path
    TestClient = None

DEVICE_TOKEN = "device-token-not-a-real-credential-0001"
BRIDGE_TOKEN = "bridge-internal-token-not-real-0002"
PROJECT_ID = "demo"

CRITERIA = [
    {"kind": "evidence", "predicate": "path_changed", "path": "src/a.py"},
    {"kind": "manual", "description": "a person confirms the page renders"},
]

TASK_TABLES = (
    "tasks", "task_events", "task_turns", "task_turn_bounds",
    "task_turn_git_baselines", "task_turn_criteria", "task_turn_criterion_items",
    "task_turn_evaluations", "task_turn_criterion_results", "task_change_claims",
    "task_artifacts", "task_claim_ingestion", "task_clarifications", "idempotency",
)


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class AssessmentApiCase(unittest.TestCase):
    """The evidence-API harness, with criteria supplied through the service.

    Tasks are created through ``app.state.tasks`` rather than the HTTP route,
    because ``/api/tasks`` deliberately has no ``criteria`` field — PR6 kept
    criteria an internal input and PR8 does not widen that. The route under test
    is still exercised over real HTTP.
    """

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
        bridge_path = config.actions_bridge_token_path
        bridge_path.write_text(BRIDGE_TOKEN + "\n", encoding="utf-8")
        bridge_path.chmod(0o600)

        self.config = config
        self.database = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.app = create_app(
            config=config, token=DEVICE_TOKEN, adapter=StubAdapter(config)
        )
        self.client = TestClient(self.app)
        self.service = self.app.state.tasks

    def device(self) -> dict:
        return {"Authorization": "Bearer " + DEVICE_TOKEN}

    def bridge(self) -> dict:
        return {"Authorization": "Bearer " + BRIDGE_TOKEN}

    def start(self, criteria=CRITERIA, prompt="scenario: complete"):
        """A task whose turn is closed by the time the request is made.

        The `complete` scenario runs to a terminal state, so turn one is durably
        closed and PR7's post-close pass has already evaluated it — which is the
        ordinary state the assessment route is read in.
        """
        row, _ = self.service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt=prompt,
            origin="pwa",
            criteria=criteria,
        )
        return row

    def start_two_turns(self):
        """A task with two closed turns, each with its own criteria and evaluation.

        Turn two is seeded through the store rather than driven through the
        service, because the validation adapter never reaches
        ``ready_for_followup`` and so cannot open a second turn. That is the
        right level for this test regardless: what is under examination is
        whether the *route* keeps two turns apart, and PR7's end-to-end test
        already proves the lifecycle can produce them.
        """
        from cofferdam.workstation.tasks.criteria import validate_criteria
        from cofferdam.workstation.tasks.evaluation import evaluate

        row = self.start()
        store = self.service.store
        store.reserve_turn_criteria(
            row.task_id,
            validate_criteria(
                [{"kind": "evidence", "predicate": "path_changed", "path": "tests/t.py"}]
            ),
            recorded_at="2026-08-16T02:00:00Z",
        )
        with sqlite3.connect(str(self.database)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute(
                "INSERT INTO task_turns (task_id,turn_number,provider,source,started_at,"
                "completed_at,outcome) VALUES (?,2,'validation','pwa','x','y','completed')",
                (row.task_id,),
            )
            db.execute(
                "INSERT INTO task_turn_bounds (task_id,turn_number,"
                "opened_after_event_sequence,closed_through_event_sequence)"
                " VALUES (?,2,0,99)",
                (row.task_id,),
            )
        snapshot = store.turn_criteria(row.task_id, 2)
        bundle = store.evidence_bundle(row.task_id, 2)
        store.record_evaluation(
            snapshot=snapshot,
            bundle=bundle,
            results=evaluate(snapshot, bundle),
            recorded_at="2026-08-16T02:01:00Z",
        )
        return row

    def assessment_path(self, task_id, turn=1):
        return "/api/tasks/%s/turns/%s/assessment" % (task_id, turn)

    def get(self, path, headers=None):
        return self.client.get(path, headers=self.device() if headers is None else headers)

    def rows(self, table):
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            return connection.execute("SELECT * FROM " + table).fetchall()
        finally:
            connection.close()


class RouteAndAuth(AssessmentApiCase):
    def test_an_authenticated_get_returns_200(self):
        row = self.start()
        response = self.get(self.assessment_path(row.task_id))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("assessment", body)
        self.assertIn("generated_at", body)

    def test_unauthenticated_is_401(self):
        row = self.start()
        self.assertEqual(self.client.get(self.assessment_path(row.task_id)).status_code, 401)

    def test_a_wrong_token_is_401(self):
        row = self.start()
        response = self.client.get(
            self.assessment_path(row.task_id),
            headers={"Authorization": "Bearer not-the-token"},
        )
        self.assertEqual(response.status_code, 401)

    def test_the_bridge_credential_is_refused(self):
        """`require_token` has never heard of it — the evidence route's rule.

        The bridge credential is real and configured in this fixture: it works on
        the ten task routes it is allowed to reach, and is refused here. That is
        the assertion — not that no such credential exists.
        """
        row = self.start()
        self.assertEqual(
            self.client.get(self.assessment_path(row.task_id), headers=self.bridge()).status_code,
            401,
        )
        # ...and the same credential is accepted where it is supposed to be.
        self.assertEqual(
            self.client.get("/api/tasks/" + row.task_id, headers=self.bridge()).status_code, 200
        )

    def test_an_unknown_task_is_404(self):
        self.assertEqual(
            self.get(self.assessment_path("task_00000000000000000000000000")).status_code, 404
        )

    def test_an_unknown_turn_is_404(self):
        row = self.start()
        self.assertEqual(self.get(self.assessment_path(row.task_id, 9)).status_code, 404)

    def test_a_turn_number_below_one_is_422(self):
        row = self.start()
        self.assertEqual(self.get(self.assessment_path(row.task_id, 0)).status_code, 422)

    def test_mutation_verbs_are_unavailable(self):
        row = self.start()
        path = self.assessment_path(row.task_id)
        for verb in ("post", "put", "patch", "delete"):
            response = getattr(self.client, verb)(path, headers=self.device())
            self.assertIn(response.status_code, (404, 405), verb)

    def test_there_is_no_rerun_route(self):
        row = self.start()
        for path in (
            "/api/tasks/%s/turns/1/assessment/rerun" % row.task_id,
            "/api/tasks/%s/turns/1/evaluation" % row.task_id,
            "/api/tasks/%s/assessment" % row.task_id,
        ):
            # 404 or 405 depending on how the router matches an unregistered
            # sub-path; both mean "no such endpoint". The structural proof that
            # no rerun route is declared anywhere lives in `RouteInventory`.
            self.assertIn(
                self.client.post(path, headers=self.device()).status_code, (404, 405), path
            )

    def test_the_response_is_no_store(self):
        row = self.start()
        response = self.get(self.assessment_path(row.task_id))
        self.assertIn("no-store", response.headers.get("cache-control", ""))


class PresentAssessment(AssessmentApiCase):
    def test_it_publishes_criteria_and_per_criterion_results(self):
        row = self.start()
        body = self.get(self.assessment_path(row.task_id)).json()["assessment"]
        self.assertEqual(body["task_id"], row.task_id)
        self.assertEqual(body["turn_number"], 1)
        self.assertEqual(body["criteria"]["state"], "present")
        self.assertTrue(body["criteria"]["recorded"])
        self.assertEqual(body["criteria"]["criterion_count"], 2)
        self.assertEqual(body["evaluation"]["state"], "recorded")
        self.assertEqual(body["evaluation"]["result_count"], 2)
        self.assertEqual(
            [i["criterion_id"] for i in body["criteria"]["items"]],
            [r["criterion_id"] for r in body["evaluation"]["results"]],
        )

    def test_the_fingerprints_match_the_stored_records(self):
        row = self.start()
        body = self.get(self.assessment_path(row.task_id)).json()["assessment"]
        snapshot = self.service.turn_criteria(row.task_id, 1)
        stored = self.service.turn_evaluation(row.task_id, 1)
        bundle = self.service.evidence_bundle(row.task_id, 1)
        self.assertEqual(body["criteria"]["snapshot_id"], snapshot.snapshot_id)
        self.assertEqual(body["criteria"]["criteria_fingerprint"], snapshot.fingerprint)
        self.assertEqual(body["evaluation"]["evaluation_id"], stored.evaluation_id)
        self.assertEqual(
            body["evaluation"]["evaluation_fingerprint"], stored.evaluation_fingerprint
        )
        self.assertEqual(
            body["evaluation"]["evidence_input_fingerprint"], bundle.input_fingerprint
        )

    def test_no_aggregate_reaches_the_wire(self):
        row = self.start()
        blob = self.get(self.assessment_path(row.task_id)).text
        for forbidden in (
            "overall", '"passed"', '"failed"', '"success"', "score", "percent",
            "all_met", "met_count", "confidence", '"risk"', "verdict",
        ):
            self.assertNotIn(forbidden, blob, forbidden)

    def test_no_host_or_evidence_body_reaches_the_wire(self):
        row = self.start()
        blob = self.get(self.assessment_path(row.task_id)).text
        for forbidden in (str(self.home), "/home/", "observations", "relationships", "claims"):
            self.assertNotIn(forbidden, blob, forbidden)


class AbsenceStates(AssessmentApiCase):
    def test_not_provided_is_explicit_and_not_a_pass(self):
        row = self.start(criteria=None)
        body = self.get(self.assessment_path(row.task_id)).json()["assessment"]
        self.assertEqual(body["criteria"]["state"], "not_provided")
        self.assertTrue(body["criteria"]["recorded"])
        self.assertEqual(body["criteria"]["criterion_count"], 0)
        self.assertEqual(body["criteria"]["items"], [])
        self.assertEqual(body["evaluation"]["state"], "recorded")
        self.assertEqual(body["evaluation"]["result_count"], 0)
        self.assertEqual(body["evaluation"]["results"], [])

    def test_legacy_unknown_is_200_and_explicit(self):
        row = self.start(criteria=None)
        with sqlite3.connect(str(self.database)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("DELETE FROM task_turn_evaluations WHERE task_id=?", (row.task_id,))
            db.execute("DELETE FROM task_turn_criteria WHERE task_id=?", (row.task_id,))
        response = self.get(self.assessment_path(row.task_id))
        self.assertEqual(response.status_code, 200, "a legacy turn is not a 404")
        body = response.json()["assessment"]
        self.assertEqual(body["criteria"]["state"], "legacy_unknown")
        self.assertFalse(body["criteria"]["recorded"])
        self.assertIsNone(body["criteria"]["snapshot_id"])
        self.assertEqual(body["criteria"]["items"], [])
        self.assertEqual(body["evaluation"]["state"], "criteria_legacy_unknown")
        self.assertFalse(body["evaluation"]["recorded"])

    def test_a_closed_criteria_bearing_turn_with_no_record_is_not_recorded(self):
        row = self.start()
        with sqlite3.connect(str(self.database)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("DELETE FROM task_turn_evaluations WHERE task_id=?", (row.task_id,))
        body = self.get(self.assessment_path(row.task_id)).json()["assessment"]
        self.assertEqual(body["evaluation"]["state"], "not_recorded")
        self.assertFalse(body["evaluation"]["recorded"])
        self.assertEqual(body["evaluation"]["results"], [])
        # And the criteria are still there and still say so.
        self.assertEqual(body["criteria"]["state"], "present")

    def test_reading_a_missing_evaluation_does_not_create_one(self):
        row = self.start()
        with sqlite3.connect(str(self.database)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("DELETE FROM task_turn_evaluations WHERE task_id=?", (row.task_id,))
        for _ in range(10):
            body = self.get(self.assessment_path(row.task_id)).json()["assessment"]
            self.assertEqual(body["evaluation"]["state"], "not_recorded")
        self.assertEqual(self.rows("task_turn_evaluations"), [])
        self.assertEqual(self.rows("task_turn_criterion_results"), [])


class TurnIsolation(AssessmentApiCase):
    def test_each_turn_returns_only_its_own_snapshot_and_evaluation(self):
        row = self.start_two_turns()
        one = self.get(self.assessment_path(row.task_id, 1)).json()["assessment"]
        two = self.get(self.assessment_path(row.task_id, 2)).json()["assessment"]
        self.assertEqual(one["turn_number"], 1)
        self.assertEqual(two["turn_number"], 2)
        self.assertNotEqual(
            one["criteria"]["snapshot_id"], two["criteria"]["snapshot_id"]
        )
        self.assertNotEqual(
            one["evaluation"]["evaluation_id"], two["evaluation"]["evaluation_id"]
        )
        self.assertNotEqual(
            one["evaluation"]["evidence_input_fingerprint"],
            two["evaluation"]["evidence_input_fingerprint"],
        )
        self.assertEqual([i["path"] for i in one["criteria"]["items"]], ["src/a.py", None])
        self.assertEqual([i["path"] for i in two["criteria"]["items"]], ["tests/t.py"])

    def test_there_is_no_task_level_latest_route(self):
        row = self.start()
        self.assertEqual(self.get("/api/tasks/%s/assessment" % row.task_id).status_code, 404)

    def test_no_result_row_leaks_across_turns(self):
        row = self.start_two_turns()
        one = self.get(self.assessment_path(row.task_id, 1)).json()["assessment"]
        two = self.get(self.assessment_path(row.task_id, 2)).json()["assessment"]
        ids_one = {r["criterion_id"] for r in one["evaluation"]["results"]}
        ids_two = {r["criterion_id"] for r in two["evaluation"]["results"]}
        self.assertTrue(ids_one)
        self.assertTrue(ids_two)
        self.assertEqual(ids_one & ids_two, set())


class PureRead(AssessmentApiCase):
    def test_a_get_runs_no_evaluator_and_no_recovery(self):
        row = self.start()

        def poison(*args, **kwargs):
            raise AssertionError("the assessment route reached a writer")

        saved = (
            self.service.store.record_evaluation,
            self.service.evaluate_closed_turns,
            self.service.recover_after_restart,
            self.service.refresh_task,
        )
        self.service.store.record_evaluation = poison
        self.service.evaluate_closed_turns = poison
        self.service.recover_after_restart = poison
        self.service.refresh_task = poison
        try:
            response = self.get(self.assessment_path(row.task_id))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["assessment"]["evaluation"]["state"], "recorded")
        finally:
            (
                self.service.store.record_evaluation,
                self.service.evaluate_closed_turns,
                self.service.recover_after_restart,
                self.service.refresh_task,
            ) = saved

    def test_a_get_runs_no_process_and_no_socket(self):
        import socket
        import subprocess

        row = self.start()

        def poison(*args, **kwargs):
            raise AssertionError("the assessment route reached the world")

        saved = (subprocess.run, subprocess.Popen)
        subprocess.run, subprocess.Popen = poison, poison
        try:
            self.assertEqual(self.get(self.assessment_path(row.task_id)).status_code, 200)
        finally:
            subprocess.run, subprocess.Popen = saved

    def test_repeated_gets_mutate_nothing(self):
        row = self.start()
        before = {t: self.rows(t) for t in TASK_TABLES}
        for _ in range(25):
            self.get(self.assessment_path(row.task_id))
        after = {t: self.rows(t) for t in TASK_TABLES}
        self.assertEqual(before, after)

    def test_the_response_is_identical_every_time(self):
        row = self.start()
        bodies = {
            json.dumps(self.get(self.assessment_path(row.task_id)).json()["assessment"], sort_keys=True)
            for _ in range(15)
        }
        self.assertEqual(len(bodies), 1)


class RouteInventory(unittest.TestCase):
    """The exact surface delta, read from the syntax tree."""

    def _routes(self):
        import ast
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1]
            / "cofferdam" / "workstation" / "service.py"
        ).read_text(encoding="utf-8")
        out = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and getattr(decorator.func, "attr", None) in (
                    "get", "post", "put", "delete", "patch", "websocket"
                ):
                    for argument in decorator.args:
                        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                            out.append((decorator.func.attr, argument.value))
        return out

    def test_exactly_one_route_was_added_and_it_is_a_get(self):
        routes = self._routes()
        assessment = [r for r in routes if "assessment" in r[1]]
        self.assertEqual(len(assessment), 1)
        self.assertEqual(assessment[0][0], "get")
        self.assertEqual(
            assessment[0][1], "/api/tasks/{task_id}/turns/{turn_number}/assessment"
        )
        self.assertEqual(len(routes), 80, "route count moved by more than the one GET")

    def test_no_evaluation_or_criteria_mutation_route_exists(self):
        for method, path in self._routes():
            lowered = path.lower()
            if any(w in lowered for w in ("assessment", "evaluat", "criteri")):
                self.assertEqual(method, "get", path)

    def test_the_bridge_surface_is_untouched(self):
        import ast
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1]
            / "cofferdam" / "actions_bridge" / "service.py"
        ).read_text(encoding="utf-8")
        routes = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and getattr(decorator.func, "attr", None) in (
                    "get", "post", "put", "delete", "patch"
                ):
                    paths = [a.value for a in decorator.args if isinstance(a, ast.Constant)]
                    routes.append(
                        (
                            paths[0] if paths else "?",
                            any(getattr(k, "arg", None) == "dependencies" for k in decorator.keywords),
                        )
                    )
        self.assertEqual(len(routes), 10)
        self.assertEqual(sum(1 for _, auth in routes if auth), 9)
        for path, _ in routes:
            for forbidden in ("assessment", "evaluat", "criteri", "evidence", "artifact", "claim"):
                self.assertNotIn(forbidden, path, path)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
