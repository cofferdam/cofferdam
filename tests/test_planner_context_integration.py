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

    #: Written into real project documents. Must cross the egress boundary.
    ALLOWED_MARKER = "SENTINEL-ALLOWED-PROJECT-DECISION-7Q2"
    #: Carried only by the user message, which `project_context_external_v1`
    #: excludes. Must exist locally and must NOT cross.
    DENIED_MARKER = "SENTINEL-DENIED-LOCAL-ONLY-4X9"

    def write_project_documents(self) -> None:
        (self.project_root / "STATUS.md").write_text(
            f"# Status\n\nPlanner contracts merged. {self.ALLOWED_MARKER}\n",
            encoding="utf-8",
        )
        (self.project_root / "ROADMAP.md").write_text(
            "# Roadmap\n\n## M2L\n\nDurable planner persistence. No dispatch.\n",
            encoding="utf-8",
        )
        (self.project_root / "DECISIONS.md").write_text(
            "# Decisions\n\n## D-2026-08-20-2\n\nPlanner output is data, never "
            "execution.\n",
            encoding="utf-8",
        )
        (self.project_root / "DESIGN.md").write_text(
            "# Design\n\nA cloud planner receives only a CloudContextProjection.\n",
            encoding="utf-8",
        )

    def setUp(self) -> None:
        super().setUp()
        # Without this the builder emits only the user message and the policy
        # correctly excludes it, leaving an empty projection that every
        # structural assertion below would still pass. That is exactly how the
        # first version of this file passed while proving nothing.
        self.activate()
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
        # The real builder and the real projector, wired into the real service.
        # Nothing between the caller's intent and the provider is faked.
        self.service = PlannerService(
            store=self.store, planner=self.planner,
            context=self.builder, projector=self.projector,
        )

    def plan_over_real_context(self, message=None):
        message = message or f"ne yapmaliyiz? {self.DENIED_MARKER}"
        outcome = self.service.prepare_development_step(user_intent=message)
        request = self.planner.calls[0]
        return self.build(message), request.projection, outcome

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

    def test_the_local_pack_and_the_projection_are_both_non_empty(self):
        """The assertion whose absence made this file vacuous.

        Every other structural property here — a policy id, a timestamp,
        omissions carrying reasons — is equally true of a projection with zero
        parts. This is the one that is not.
        """
        pack, projection, _ = self.plan_over_real_context()
        self.assertGreater(len(pack.parts), 0, "the local pack was empty")
        self.assertGreater(
            len(projection.parts), 0,
            "the projection was empty; nothing crossed the boundary",
        )

    def test_allowed_project_content_actually_crosses(self):
        _, projection, outcome = self.plan_over_real_context()
        projected = json.dumps(projection.to_dict(), ensure_ascii=False)
        self.assertIn(self.ALLOWED_MARKER, projected)
        packet = json.dumps(
            self.store.request_payload(outcome.planner_request_id), ensure_ascii=False
        )
        self.assertIn(self.ALLOWED_MARKER, packet)

    def test_denied_content_exists_locally_and_does_not_cross(self):
        """Both halves, in one test — denial only means something if it was there."""
        pack, projection, outcome = self.plan_over_real_context()
        local = json.dumps([p.text for p in pack.parts], ensure_ascii=False)
        self.assertIn(self.DENIED_MARKER, local, "the denied marker was never local")

        projected = json.dumps(projection.to_dict(), ensure_ascii=False)
        self.assertNotIn(self.DENIED_MARKER, projected)
        packet = json.dumps(
            self.store.request_payload(outcome.planner_request_id)["project_context"],
            ensure_ascii=False,
        )
        self.assertNotIn(self.DENIED_MARKER, packet)

    def test_the_denial_is_explained_by_policy(self):
        _, projection, _ = self.plan_over_real_context()
        reasons = {o.reason for o in projection.omissions}
        self.assertIn("policy_excluded", reasons)

    def test_source_references_stay_semantic(self):
        """A reference names a role, never a location."""
        _, projection, _ = self.plan_over_real_context()
        for part in projection.parts:
            self.assertRegex(part.source_ref, r"^[a-z][a-z0-9_]*:")
            self.assertNotIn("/", part.source_ref)
            self.assertNotIn(str(self.project_root), part.source_ref)

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
