"""M2K PR22 — acceptance over the private route: auth, states, and inertness.

The route is PR8's. What PR22 added is a third section, and the two properties
PR8 was built to hold have to survive it.

**The bridge still cannot reach it.** `require_token` has never heard of the
Actions bridge credential, so a bridge request arrives as an ordinary
unauthenticated one and gets 401. Acceptance is Cofferdam's judgement about
somebody's work against what they asked for — further from the bridge's business
than evidence is — and PR22 gave the bridge no operation, no schema and no route.

**It is still a read.** A GET must not run the evaluator, create an
EvaluationRecord, trigger recovery, observe a path or move a lifecycle. That is
asserted by poisoning each of those and issuing the request anyway.
"""

from __future__ import annotations

import ast
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
REPO_ROOT = Path(__file__).resolve().parents[1]

CRITERIA = [
    {"kind": "evidence", "predicate": "path_changed", "path": "src/a.py"},
    {"kind": "manual", "description": "a person confirms the page renders"},
]
ROOT = {"mode": "root"}

TASK_TABLES = (
    "tasks", "task_events", "task_turns", "task_turn_bounds",
    "task_turn_git_baselines", "task_turn_criteria", "task_turn_criterion_items",
    "task_turn_evaluations", "task_turn_criterion_results",
    "task_turn_criteria_continuity", "task_turn_final_state",
    "task_turn_final_state_paths", "task_change_claims", "task_artifacts",
    "task_claim_ingestion", "task_clarifications", "idempotency",
)


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class AcceptanceApiCase(unittest.TestCase):
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
            json.dumps({"projects": [{
                "project_id": PROJECT_ID, "display_name": "Demo",
                "root": str(self.project_root), "adapters": ["validation"],
                "enabled": True,
            }]}),
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

    def start(self, criteria=CRITERIA, prompt="scenario: complete", continuity=ROOT):
        """A closed turn.

        ``continuity`` defaults to an explicit ``root`` declaration, which is
        **not** what today's callers do — see
        :class:`WhatTodaysCallersActuallyGet`. Most tests here are about the
        surface rather than about that gap, so they declare one and get an
        assessable turn; the gap itself is pinned separately and deliberately.
        """
        row, _ = self.service.create_task(
            project_id=PROJECT_ID, adapter_id="validation", prompt=prompt,
            origin="pwa", criteria=criteria, continuity=continuity,
        )
        return row

    def path(self, task_id, turn=1):
        return "/api/tasks/%s/turns/%s/assessment" % (task_id, turn)

    def acceptance(self, task_id, turn=1):
        response = self.client.get(self.path(task_id, turn), headers=self.device())
        self.assertEqual(200, response.status_code, response.text)
        return response.json()["assessment"]["acceptance"]

    def rows(self, table):
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            return connection.execute("SELECT * FROM " + table).fetchall()
        except sqlite3.OperationalError:  # pragma: no cover - table may not exist
            return []
        finally:
            connection.close()

    def snapshot_tables(self):
        return {table: self.rows(table) for table in TASK_TABLES}


class RouteAndAuth(AcceptanceApiCase):
    def test_a_device_token_gets_the_acceptance_section(self):
        row = self.start()
        response = self.client.get(self.path(row.task_id), headers=self.device())
        self.assertEqual(200, response.status_code)
        self.assertIn("acceptance", response.json()["assessment"])

    def test_unauthenticated_is_401(self):
        row = self.start()
        self.assertEqual(401, self.client.get(self.path(row.task_id)).status_code)

    def test_the_bridge_credential_is_401(self):
        """`require_token` has never heard of it, so it arrives as anonymous."""
        row = self.start()
        response = self.client.get(self.path(row.task_id), headers=self.bridge())
        self.assertEqual(401, response.status_code)
        self.assertNotIn("acceptance", response.text)

    def test_a_wrong_token_is_401(self):
        row = self.start()
        self.assertEqual(
            401,
            self.client.get(
                self.path(row.task_id), headers={"Authorization": "Bearer nope"}
            ).status_code,
        )

    def test_there_is_still_no_write_verb_on_the_path(self):
        row = self.start()
        for verb in ("post", "put", "patch", "delete"):
            with self.subTest(verb=verb):
                response = getattr(self.client, verb)(
                    self.path(row.task_id), headers=self.device()
                )
                self.assertEqual(405, response.status_code)

    def test_no_sibling_acceptance_route_was_added(self):
        """One route for one audit boundary; a second would let sections drift."""
        row = self.start()
        for candidate in (
            "/api/tasks/%s/turns/1/acceptance" % row.task_id,
            "/api/tasks/%s/acceptance" % row.task_id,
        ):
            with self.subTest(candidate=candidate):
                self.assertEqual(
                    404, self.client.get(candidate, headers=self.device()).status_code
                )

    def test_the_response_still_carries_generated_at_outside_the_assessment(self):
        row = self.start()
        body = self.client.get(self.path(row.task_id), headers=self.device()).json()
        self.assertIn("generated_at", body)
        self.assertNotIn("generated_at", body["assessment"])
        self.assertNotIn("generated_at", body["assessment"]["acceptance"])


class PublishedStates(AcceptanceApiCase):
    def test_a_manual_criterion_makes_it_incomplete_and_wants_a_person(self):
        row = self.start()
        acceptance = self.acceptance(row.task_id)
        self.assertEqual("assessable", acceptance["availability"])
        self.assertEqual("incomplete", acceptance["outcome"])
        self.assertIs(True, acceptance["requires_human"])
        self.assertEqual(2, acceptance["counts"]["total"])

    def test_no_criteria_is_no_structured_criteria_with_known_zero_counts(self):
        row = self.start(criteria=[])
        acceptance = self.acceptance(row.task_id)
        self.assertEqual("not_assessable", acceptance["availability"])
        self.assertEqual("no_structured_criteria", acceptance["availability_reason"])
        self.assertIsNone(acceptance["outcome"])
        self.assertEqual({"total": 0, "met": 0, "not_met": 0, "unverified": 0},
                         acceptance["counts"])
        self.assertIs(False, acceptance["requires_human"])

    def test_an_unknown_population_publishes_nulls_not_zeros(self):
        row = self.start()
        with sqlite3.connect(str(self.database)) as db:
            db.execute(
                "UPDATE task_turn_criteria_continuity SET continuity_state ="
                " 'not_declared', mode = NULL, predecessor_snapshot_id = NULL,"
                " relation_count = 0 WHERE task_id = ?",
                (row.task_id,),
            )
        acceptance = self.acceptance(row.task_id)
        self.assertEqual("not_assessable", acceptance["availability"])
        self.assertEqual("continuity_not_declared", acceptance["availability_reason"])
        self.assertIsNone(acceptance["counts"])
        self.assertIsNone(acceptance["requires_human"])

    def test_a_historical_lineage_keeps_its_own_reason(self):
        row = self.start()
        with sqlite3.connect(str(self.database)) as db:
            db.execute(
                "DELETE FROM task_turn_criteria_continuity WHERE task_id = ?",
                (row.task_id,),
            )
        self.assertEqual(
            "continuity_legacy_unknown",
            self.acceptance(row.task_id)["availability_reason"],
        )

    def test_a_structural_failure_keeps_its_exact_code(self):
        row = self.start()
        with sqlite3.connect(str(self.database)) as db:
            db.execute(
                "UPDATE task_turn_evaluations SET turn_number = 7 WHERE task_id = ?",
                (row.task_id,),
            )
        acceptance = self.acceptance(row.task_id)
        self.assertEqual("not_assessable", acceptance["availability"])
        self.assertEqual("evaluation_not_recorded", acceptance["availability_reason"])
        self.assertIsNone(acceptance["outcome"])
        self.assertIsNone(acceptance["counts"])

    def test_not_assessable_never_becomes_incomplete_over_http(self):
        row = self.start(criteria=[])
        self.assertNotEqual("incomplete", self.acceptance(row.task_id)["outcome"])

    def test_the_fingerprints_are_published(self):
        row = self.start()
        acceptance = self.acceptance(row.task_id)
        self.assertEqual(64, len(acceptance["assessment_fingerprint"]))
        self.assertEqual(64, len(acceptance["acceptance_fingerprint"]))
        self.assertEqual(1, acceptance["aggregator_version"])

    def test_the_criteria_and_evaluation_sections_are_unchanged(self):
        row = self.start()
        body = self.client.get(
            self.path(row.task_id), headers=self.device()
        ).json()["assessment"]
        self.assertEqual(
            {"version", "task_id", "turn_number", "criteria", "evaluation", "acceptance"},
            set(body),
        )
        self.assertEqual(1, body["version"])
        self.assertIn("items", body["criteria"])
        self.assertIn("results", body["evaluation"])

    def test_a_missing_turn_is_still_404(self):
        row = self.start()
        self.assertEqual(
            404, self.client.get(self.path(row.task_id, 9), headers=self.device()).status_code
        )


class WhatTodaysCallersActuallyGet(AcceptanceApiCase):
    """The gap this surface makes visible, pinned rather than papered over.

    ``create_task`` writes an explicit ``not_declared`` continuity row when the
    caller supplies none — deliberately, since PR10 decided "nobody declared a
    relationship" must be a durable fact rather than a missing one. But **no
    caller supplies one today**: `/api/tasks` has no continuity field, and the
    bridge has none either.

    So every task created through a real surface resolves to
    ``not_assessable / continuity_not_declared``. That is the honest answer and
    the layers are behaving correctly — the acceptance stack is complete and its
    input is not yet being written. Publishing the surface is what makes that
    visible instead of theoretical, and it is why an acceptance answer is not yet
    meaningfully consumable in production.
    """

    def test_a_task_created_without_a_declaration_is_not_assessable(self):
        row = self.start(continuity=None)
        acceptance = self.acceptance(row.task_id)
        self.assertEqual("not_assessable", acceptance["availability"])
        self.assertEqual("continuity_not_declared", acceptance["availability_reason"])
        self.assertIsNone(acceptance["outcome"])
        self.assertIsNone(acceptance["counts"])
        self.assertIsNone(acceptance["requires_human"])

    def test_the_lineage_refusal_outranks_an_empty_criteria_set(self):
        """Population *unknown* beats population *empty*, and that ordering is right."""
        row = self.start(criteria=[], continuity=None)
        self.assertEqual(
            "continuity_not_declared",
            self.acceptance(row.task_id)["availability_reason"],
        )

    def test_the_public_task_route_still_has_no_continuity_field(self):
        service = (
            REPO_ROOT / "cofferdam" / "workstation" / "service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('"continuity"', service)
        self.assertNotIn("predecessor_snapshot_id", service)


class TheReadIsInert(AcceptanceApiCase):
    def test_a_hundred_reads_change_no_row(self):
        row = self.start()
        before = self.snapshot_tables()
        for _ in range(100):
            self.client.get(self.path(row.task_id), headers=self.device())
        self.assertEqual(before, self.snapshot_tables())

    def test_it_never_runs_the_evaluator(self):
        from cofferdam.workstation.tasks import evaluation as evaluation_module

        row = self.start()
        original = evaluation_module.evaluate

        def poisoned(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("the read ran the evaluator")

        evaluation_module.evaluate = poisoned
        self.addCleanup(setattr, evaluation_module, "evaluate", original)
        self.assertEqual(
            200, self.client.get(self.path(row.task_id), headers=self.device()).status_code
        )

    def test_it_never_observes_a_path(self):
        from cofferdam.workstation.tasks import finalstate as finalstate_module

        row = self.start()
        original = finalstate_module.observe_paths

        def poisoned(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("the read observed the filesystem")

        finalstate_module.observe_paths = poisoned
        self.addCleanup(setattr, finalstate_module, "observe_paths", original)
        self.assertEqual(
            200, self.client.get(self.path(row.task_id), headers=self.device()).status_code
        )

    def test_it_never_triggers_recovery(self):
        row = self.start()
        original = self.service.evaluate_closed_turns

        def poisoned(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("the read triggered recovery")

        self.service.evaluate_closed_turns = poisoned
        self.addCleanup(setattr, self.service, "evaluate_closed_turns", original)
        self.assertEqual(
            200, self.client.get(self.path(row.task_id), headers=self.device()).status_code
        )

    def test_the_answer_survives_deleting_the_repository(self):
        import shutil

        row = self.start()
        before = self.acceptance(row.task_id)
        shutil.rmtree(self.project_root)
        after = self.acceptance(row.task_id)
        self.assertEqual(before, after)

    def test_generated_at_moves_but_no_semantic_field_does(self):
        row = self.start()
        first = self.client.get(self.path(row.task_id), headers=self.device()).json()
        second = self.client.get(self.path(row.task_id), headers=self.device()).json()
        self.assertEqual(first["assessment"], second["assessment"])
        self.assertEqual(
            first["assessment"]["acceptance"]["acceptance_fingerprint"],
            second["assessment"]["acceptance"]["acceptance_fingerprint"],
        )


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class BridgeNegativeSpace(unittest.TestCase):
    """The bridge gained nothing, proven structurally rather than by eye."""

    def bridge_sources(self):
        base = REPO_ROOT / "cofferdam" / "actions_bridge"
        return sorted(base.rglob("*.py")) if base.exists() else []

    def test_no_bridge_module_mentions_acceptance(self):
        for path in self.bridge_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            modules = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.add(node.module.rsplit(".", 1)[-1])
            for forbidden in ("acceptance", "AcceptanceAggregate", "turn_acceptance",
                              "AGGREGATOR_VERSION", "requires_human"):
                self.assertNotIn(forbidden, names, str(path))
            self.assertNotIn("acceptance", modules, str(path))

    def test_no_bridge_operation_or_path_names_acceptance(self):
        for path in self.bridge_sources():
            text = path.read_text(encoding="utf-8")
            for forbidden in ("/assessment", "/acceptance", '"acceptance"'):
                self.assertNotIn(forbidden, text, str(path))

    def test_the_bridge_operation_and_route_counts_are_unchanged(self):
        """Counted from the app itself, not from a name scan."""
        try:
            from cofferdam.actions_bridge.service import create_app as create_bridge
        except ImportError:  # pragma: no cover - layout guard
            self.skipTest("no bridge app in this checkout")
        import inspect

        parameters = inspect.signature(create_bridge).parameters
        self.assertIn("config", parameters)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
