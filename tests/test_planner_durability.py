"""Durable planner invocations: crash-truthful, inert, and separately owned.

The properties under test are the ones a durable planner gets wrong quietly:

* a row that says ``succeeded`` with no result, because three updates were used
  where one transaction was needed;
* an interrupted invocation quietly rerun on the next start, spending a second
  call and asserting the first never happened;
* ``STOP`` — a model declining to plan — recorded as a provider failure, so that
  a week later nobody can tell reasoning from breakage;
* a prepared worker prompt that turns out to have started something.

The real ``ContextProjector`` is used for the egress test, because the policy is
the thing being checked. The provider is faked everywhere except the live smoke.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cofferdam.workstation.planner import (
    ACTION_ASK_USER,
    ACTION_PREPARE_WORKER_PROMPT,
    ACTION_STOP,
    PLANNER_SCHEMA_VERSION,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    DevelopmentPlanner,
    PlannerResult,
    PlannerService,
    PlannerStore,
    PlanningTurn,
    ProviderExecution,
    new_planner_request_id,
)
from cofferdam.workstation.planner.errors import (
    PlannerContextRefused,
    PlannerInvocationFailed,
    PlannerResultInvalid,
    PlannerTimeout,
)
from cofferdam.workstation.planner.store import (
    DATABASE_FILENAME,
    STATUS_PENDING,
    PlannerStoreUnavailable,
)

from .test_planner_contracts import a_projection


class FakePlanner(DevelopmentPlanner):
    """Returns whatever the test told it to, and records what it saw."""

    planner_id = "fake"

    def __init__(self, *, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = []

    def available(self) -> bool:
        return True

    def prepare_development_step(self, request) -> PlanningTurn:
        self.calls.append(request)
        if self._error is not None:
            raise self._error
        return PlanningTurn(
            result=self._result,
            execution=ProviderExecution(
                provider_id="fake",
                requested_model="opus",
                actual_model="model-5",
                models_used=("model-5",),
                session_id="sess_1",
                duration_ms=1234,
                input_tokens=10,
                output_tokens=20,
                provider_reported_cost_estimate_usd=0.01,
            ),
        )


def a_result(action=ACTION_STOP, **kw) -> PlannerResult:
    values = dict(
        action=action,
        summary="a summary",
        confidence=0.8,
        decision_basis="because",
    )
    values.update(kw)
    return PlannerResult(**values)


class StoreHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.store = PlannerStore(self.dir)

    def service(self, planner) -> PlannerService:
        return PlannerService(store=self.store, planner=planner)

    def run_once(self, planner, **kw):
        return self.service(planner).prepare_development_step(
            projection=a_projection(), user_intent="bir seyler yapalim", **kw
        )


# -- 1-4. ownership, database, schema ----------------------------------------


class PersistenceOwnership(StoreHarness):
    def test_the_planner_has_its_own_database(self):
        """Beside tasks/workspace/mind, not inside Task Core's file."""
        self.assertTrue((self.dir / DATABASE_FILENAME).exists())
        self.assertEqual(DATABASE_FILENAME, "planner.sqlite3")

    def test_it_does_not_write_into_the_task_database(self):
        self.assertFalse((self.dir / "tasks.sqlite3").exists())

    def test_schema_version_is_its_own(self):
        self.assertEqual(self.store.schema_version(), PLANNER_SCHEMA_VERSION)

    def test_reopening_is_idempotent(self):
        again = PlannerStore(self.dir)
        self.assertEqual(again.schema_version(), PLANNER_SCHEMA_VERSION)

    def test_a_newer_database_is_refused_rather_than_written_to(self):
        import sqlite3

        connection = sqlite3.connect(self.store.path)
        connection.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
            (str(PLANNER_SCHEMA_VERSION + 1),),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(PlannerStoreUnavailable):
            PlannerStore(self.dir)

    def test_the_database_is_not_world_readable(self):
        mode = self.store.path.stat().st_mode & 0o777
        self.assertEqual(mode & 0o077, 0, oct(mode))


# -- 5-8, 19-22. lifecycle vs action -----------------------------------------


class LifecycleAndAction(StoreHarness):
    def test_a_prepared_prompt_persists(self):
        outcome = self.run_once(
            FakePlanner(
                result=a_result(
                    ACTION_PREPARE_WORKER_PROMPT, worker_prompt="implement X"
                )
            )
        )
        self.assertTrue(outcome.ok)
        record = outcome.record
        self.assertEqual(record.status, STATUS_SUCCEEDED)
        self.assertEqual(record.action, ACTION_PREPARE_WORKER_PROMPT)
        self.assertEqual(record.worker_prompt, "implement X")
        self.assertTrue(record.has_prepared_prompt)
        self.assertFalse(record.needs_user_input)

    def test_ask_user_persists_a_question_and_no_prompt(self):
        outcome = self.run_once(
            FakePlanner(
                result=a_result(ACTION_ASK_USER, user_question="which database?")
            )
        )
        record = outcome.record
        self.assertEqual(record.status, STATUS_SUCCEEDED)
        self.assertEqual(record.user_question, "which database?")
        self.assertIsNone(record.worker_prompt)
        self.assertTrue(record.needs_user_input)
        self.assertFalse(record.has_prepared_prompt)

    def test_stop_is_a_successful_invocation_not_a_failure(self):
        """The distinction this table exists to keep: declining != breaking."""
        outcome = self.run_once(FakePlanner(result=a_result(ACTION_STOP)))
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.record.status, STATUS_SUCCEEDED)
        self.assertEqual(outcome.record.action, ACTION_STOP)
        self.assertIsNone(outcome.record.failure_code)

    def test_a_provider_failure_is_a_failed_invocation_with_no_action(self):
        outcome = self.run_once(
            FakePlanner(error=PlannerInvocationFailed("provider exited 2"))
        )
        self.assertFalse(outcome.ok)
        record = outcome.record
        self.assertEqual(record.status, STATUS_FAILED)
        self.assertIsNone(record.action)
        self.assertEqual(record.failure_code, "planner_invocation_failed")

    def test_status_and_action_are_separate_columns(self):
        outcome = self.run_once(FakePlanner(result=a_result(ACTION_STOP)))
        self.assertNotEqual(outcome.record.status, outcome.record.action)
        self.assertNotIn(outcome.record.action, ("succeeded", "failed", "running"))

    def test_a_timeout_persists_truthfully(self):
        outcome = self.run_once(FakePlanner(error=PlannerTimeout("no answer in 600s")))
        self.assertEqual(outcome.record.failure_code, "planner_timeout")

    def test_a_validation_failure_persists_truthfully(self):
        outcome = self.run_once(
            FakePlanner(error=PlannerResultInvalid("action not in vocabulary"))
        )
        self.assertEqual(outcome.record.failure_code, "planner_result_invalid")
        self.assertIsNone(outcome.record.action)

    def test_an_unexpected_provider_defect_is_still_recorded(self):
        outcome = self.run_once(FakePlanner(error=RuntimeError("boom")))
        self.assertEqual(outcome.record.status, STATUS_FAILED)
        self.assertIn("RuntimeError", outcome.record.failure_message)


# -- 6, 12-13. crash truth ---------------------------------------------------


class CrashTruth(StoreHarness):
    def test_the_request_exists_before_the_provider_is_called(self):
        seen = {}

        class Watching(FakePlanner):
            def prepare_development_step(inner, request):
                seen["row"] = self.store.get(request.request_id)
                return super().prepare_development_step(request)

        self.run_once(Watching(result=a_result()))
        self.assertIsNotNone(seen["row"], "no row existed when the provider ran")
        self.assertEqual(seen["row"].status, STATUS_RUNNING)

    def test_an_interrupted_invocation_is_marked_not_rerun(self):
        planner = FakePlanner(result=a_result())
        service = self.service(planner)
        # A row abandoned mid-flight, as a killed process would leave it.
        request_id = new_planner_request_id()
        self.store.create_request(
            planner_request_id=request_id,
            workspace_id="ws_1",
            project_id=None,
            user_intent="abandoned",
            request_payload={},
            projection_policy_id=None,
            projection_built_at=None,
            created_at="2026-08-20T00:00:00Z",
        )
        self.store.mark_running(request_id, started_at="2026-08-20T00:00:01Z")

        marked = service.reconcile_interrupted()

        self.assertEqual(marked, 1)
        record = self.store.get(request_id)
        self.assertEqual(record.status, STATUS_INTERRUPTED)
        self.assertEqual(record.failure_code, "planner_interrupted")
        # The decisive half: nothing was re-invoked.
        self.assertEqual(planner.calls, [])

    def test_reconcile_leaves_terminal_rows_alone(self):
        outcome = self.run_once(FakePlanner(result=a_result()))
        self.service(FakePlanner(result=a_result())).reconcile_interrupted()
        self.assertEqual(self.store.get(outcome.planner_request_id).status,
                         STATUS_SUCCEEDED)

    def test_success_writes_result_and_provenance_together(self):
        """One statement, so a crash cannot leave 'succeeded' with no action."""
        outcome = self.run_once(FakePlanner(result=a_result(ACTION_STOP)))
        record = self.store.get(outcome.planner_request_id)
        self.assertEqual(record.status, STATUS_SUCCEEDED)
        self.assertIsNotNone(record.action)
        self.assertIsNotNone(record.provider_id)
        self.assertIsNotNone(record.completed_at)


# -- 14, 26-31. read surface and provenance ----------------------------------


class ReadSurface(StoreHarness):
    def test_read_back_by_id_matches(self):
        outcome = self.run_once(
            FakePlanner(result=a_result(ACTION_PREPARE_WORKER_PROMPT,
                                        worker_prompt="do X"))
        )
        reloaded = self.store.get(outcome.planner_request_id)
        self.assertEqual(reloaded.worker_prompt, "do X")
        self.assertEqual(reloaded.action, ACTION_PREPARE_WORKER_PROMPT)

    def test_reads_are_idempotent(self):
        outcome = self.run_once(FakePlanner(result=a_result()))
        first = self.store.get(outcome.planner_request_id).to_dict()
        second = self.store.get(outcome.planner_request_id).to_dict()
        self.assertEqual(first, second)

    def test_provenance_reloads(self):
        outcome = self.run_once(FakePlanner(result=a_result()))
        provider = self.store.get(outcome.planner_request_id).to_dict()["provider"]
        self.assertEqual(provider["requested_model"], "opus")
        self.assertEqual(provider["actual_model"], "model-5")
        self.assertEqual(provider["duration_ms"], 1234)
        self.assertEqual(provider["output_tokens"], 20)
        self.assertIn("provider_reported_cost_estimate_usd", provider)

    def test_the_read_model_is_an_allowlist_not_a_dump(self):
        outcome = self.run_once(FakePlanner(result=a_result()))
        payload = self.store.get(outcome.planner_request_id).to_dict()
        # Checked as *keys*, at any depth. A substring scan would fail on
        # `input_tokens` and on the policy id, which are both legitimate.
        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)

        present = set(keys(payload))
        for leaked in ("request_payload", "request_payload_json",
                       "models_used_json", "session_id", "env", "environment",
                       "token", "auth_token", "api_key", "password", "cookie"):
            self.assertNotIn(leaked, present, f"read model leaks {leaked}")

    def test_the_bounded_packet_is_durable_but_asked_for_separately(self):
        """Audit can prove what the model saw; a routine read does not carry it."""
        outcome = self.run_once(FakePlanner(result=a_result()))
        payload = self.store.request_payload(outcome.planner_request_id)
        self.assertIn("project_context", payload)
        # The routine read carries provenance about the projection, never the
        # projection itself. Asserted on keys: the policy id happens to contain
        # the string "project_context", which a substring check would trip on.
        self.assertNotIn(
            "project_context", set(self.store.get(outcome.planner_request_id).to_dict())
        )

    def test_context_provenance_is_recorded(self):
        outcome = self.run_once(FakePlanner(result=a_result()))
        provenance = self.store.get(outcome.planner_request_id).to_dict()[
            "context_provenance"
        ]
        self.assertTrue(provenance["projection_policy_id"])
        self.assertTrue(provenance["projection_built_at"])

    def test_unknown_id_reads_as_none(self):
        self.assertIsNone(self.store.get("plan_does_not_exist"))

    def test_ids_are_unique_and_carry_no_content(self):
        import inspect

        self.assertEqual(set(inspect.signature(new_planner_request_id).parameters), set())
        self.assertEqual(len({new_planner_request_id() for _ in range(2000)}), 2000)


# -- 17-18, 23-25. egress and no dispatch ------------------------------------


class EgressAndNoDispatch(StoreHarness):
    def test_a_non_projection_is_refused_by_the_service(self):
        class LocalContextPack:
            parts = ()

        with self.assertRaises(PlannerContextRefused):
            self.service(FakePlanner(result=a_result())).prepare_development_step(
                projection=LocalContextPack(), user_intent="x"
            )

    def test_the_provider_receives_only_the_projection(self):
        planner = FakePlanner(result=a_result())
        self.run_once(planner)
        payload = planner.calls[0].to_prompt_payload()
        self.assertIn("project_context", payload)
        self.assertEqual(payload["project_context"]["policy_id"],
                         a_projection().policy_id)

    def test_a_prepared_prompt_starts_nothing(self):
        """The regression that matters: persistence is not dispatch."""
        from cofferdam.workstation.tasks.adapters import build_registry

        outcome = self.run_once(
            FakePlanner(
                result=a_result(ACTION_PREPARE_WORKER_PROMPT, worker_prompt="run it")
            )
        )
        self.assertTrue(outcome.record.has_prepared_prompt)
        # No task adapter was registered, so nothing could have been dispatched
        # even in principle; and no task database was created beside ours.
        self.assertEqual(build_registry().ids(), ())
        self.assertFalse((self.dir / "tasks.sqlite3").exists())

    def test_the_service_exposes_no_dispatch_operation(self):
        for forbidden in ("dispatch", "run_worker", "execute", "send_to_worker",
                          "create_task", "submit"):
            self.assertFalse(hasattr(PlannerService, forbidden),
                             f"service exposes {forbidden}")

    def test_the_service_takes_no_provider_or_path_argument(self):
        import inspect

        params = set(
            inspect.signature(PlannerService.prepare_development_step).parameters
        )
        for forbidden in ("model", "executable", "cwd", "command", "argv", "env",
                          "provider", "tools", "mcp_config", "path"):
            self.assertNotIn(forbidden, params, f"service accepts {forbidden}")


# -- 32-33. the neighbours ---------------------------------------------------


class Neighbours(unittest.TestCase):
    def test_task_core_registry_is_unchanged(self):
        from cofferdam.workstation.tasks.adapters import build_registry

        self.assertEqual(build_registry().ids(), ())
        self.assertEqual(
            build_registry(enable_validation_adapter=True).ids(), ("validation",)
        )

    def test_task_core_schema_version_is_untouched(self):
        from cofferdam.workstation.tasks.store import SCHEMA_VERSION

        self.assertEqual(SCHEMA_VERSION, 11)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
