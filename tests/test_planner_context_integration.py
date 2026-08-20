"""The real egress path: Context Builder → ContextProjector → planner.

PR1c-a asserted the *type* boundary — a request will not accept a
``LocalContextPack``. That is necessary and not sufficient: it proves the wrong
object cannot be passed, not that the right object had anything removed from it.

So nothing here is faked except the provider. A real pack is built over real
documents, projected by the real projector under the real policy, and what the
planner actually received is read back out of the durable record. The property
under test is that content the local pack legitimately holds does **not** appear
in what left the host.
"""

from __future__ import annotations

import json

from cofferdam.workstation.planner import (
    ACTION_STOP,
    PlannerService,
    PlannerStore,
)

from .test_context_builder import FROZEN, ContextHarness
from .test_planner_durability import FakePlanner, a_result


class RealProjectionReachesThePlanner(ContextHarness):
    """A real pack, a real projection, and a durable record to check them against."""

    def setUp(self) -> None:
        super().setUp()
        from cofferdam.workstation.context.projection import (
            ContextProjector,
            HostRedactionEnvironment,
        )

        self.projector = ContextProjector(
            redaction=HostRedactionEnvironment(
                cofferdam_home=str(self.home),
                project_roots=(str(self.project_root),),
                vault_roots=(str(self.vault_root),),
                slot_roots=(
                    str(self.home / "slots" / "a"),
                    str(self.home / "slots" / "b"),
                ),
                home_directories=(str(self.home),),
            ),
            clock=lambda: FROZEN,
        )
        self.store = PlannerStore(self.home / "planner-state")
        self.planner = FakePlanner(result=a_result(ACTION_STOP))
        self.service = PlannerService(store=self.store, planner=self.planner)

    def plan_over_real_context(self, message="ne yapmaliyiz?"):
        pack = self.build(message)
        projection = self.projector.project(pack)
        outcome = self.service.prepare_development_step(
            projection=projection, user_intent=message
        )
        return pack, projection, outcome

    # -- the tests
    def test_the_planner_receives_a_real_projection(self):
        _, projection, outcome = self.plan_over_real_context()
        self.assertTrue(outcome.ok)
        request = self.planner.calls[0]
        self.assertIs(request.projection, projection)
        self.assertEqual(
            request.to_prompt_payload()["project_context"]["policy_id"],
            projection.policy_id,
        )

    def test_the_projection_is_produced_by_the_real_policy(self):
        """Not constructed by hand — it carries the policy that made it."""
        _, projection, _ = self.plan_over_real_context()
        self.assertTrue(projection.policy_id)
        self.assertTrue(projection.built_at)
        self.assertIsNotNone(projection.budget)

    def test_the_host_path_does_not_survive_into_what_the_planner_saw(self):
        """The redaction the projector performs, checked at the far end.

        A local pack knows where the project lives. What leaves must not, and
        the place to assert that is the durable record of the bounded packet —
        the thing that would be handed to a cloud provider.
        """
        _, _, outcome = self.plan_over_real_context()
        payload = json.dumps(
            self.store.request_payload(outcome.planner_request_id), ensure_ascii=False
        )
        self.assertNotIn(str(self.project_root), payload)
        self.assertNotIn(str(self.vault_root), payload)
        self.assertNotIn(str(self.home), payload)

    def test_omissions_are_recorded_rather_than_silent(self):
        """Whatever policy dropped is explained, not merely absent."""
        _, projection, _ = self.plan_over_real_context()
        for omission in projection.omissions:
            self.assertTrue(omission.reason, "an omission carried no reason")

    def test_provenance_survives_into_the_durable_record(self):
        _, projection, outcome = self.plan_over_real_context()
        record = self.store.get(outcome.planner_request_id)
        self.assertEqual(record.projection_policy_id, projection.policy_id)
        self.assertEqual(record.projection_built_at, projection.built_at)
        self.assertEqual(record.workspace_id, projection.workspace_id)

    def test_the_durable_packet_matches_what_the_provider_was_given(self):
        """Audit can prove what the model saw, because it is the same object."""
        _, _, outcome = self.plan_over_real_context()
        stored = self.store.request_payload(outcome.planner_request_id)
        sent = self.planner.calls[0].to_prompt_payload()
        self.assertEqual(stored, json.loads(json.dumps(sent, ensure_ascii=False)))

    def test_a_local_pack_is_never_what_the_planner_holds(self):
        pack, projection, _ = self.plan_over_real_context()
        request = self.planner.calls[0]
        self.assertIsNot(request.projection, pack)
        self.assertNotIsInstance(request.projection, type(pack))
