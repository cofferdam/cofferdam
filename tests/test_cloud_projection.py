"""What may leave the host, proved from the side that assumes it may not.

`LocalContextPack` is allowed to be rich because nothing can send it. The moment
something *can*, richness stops being a virtue — so this file tests the opposite
property to :mod:`tests.test_context_builder`: not *did the right material get
in*, but **did the wrong material stay out**, attempted from every direction a
later surface could take.

The three permissions D-2026-08-13-2 separates are separate here too. A role
being granted did not put it in a pack; a part being in a pack does not put it
in a projection. Every test below is an attempt to collapse the third gap.

Sentinels rather than assertions about structure
------------------------------------------------

Global memory is proved absent by putting a unique string in each of the four
vault documents and searching the **whole serialized projection** for it. A
structural assertion ("no part has a global source_ref") would pass while the
same bytes rode along inside somebody else's text; a sentinel search would not.
"""

from __future__ import annotations

import json
import unittest

from ._mind_doubles import PROJECT_ID, WORKSPACE_ID
from .test_context_builder import FROZEN, ContextHarness

#: One per global role. Distinct, unlikely, and searched for as raw bytes.
SENTINEL_USER = "SENTINEL-GLOBAL-USER-8f21bd"
SENTINEL_STYLE = "SENTINEL-GLOBAL-STYLE-1c04ae"
SENTINEL_PREFS = "SENTINEL-GLOBAL-PREFS-77abcd"
SENTINEL_CROSS = "SENTINEL-GLOBAL-CROSS-3d9e10"

GLOBAL_SENTINELS = (SENTINEL_USER, SENTINEL_STYLE, SENTINEL_PREFS, SENTINEL_CROSS)


class ProjectionHarness(ContextHarness):
    """A real pack over real documents, projected by a real projector.

    The redaction environment is built from the temporary directories this test
    actually created, so "the project root does not survive" is a statement
    about a path that genuinely is one rather than about a fixture string.
    """

    def setUp(self) -> None:
        super().setUp()
        self.projector = self.make_projector()

    def make_projector(self, **kwargs):
        from cofferdam.workstation.context.projection import ContextProjector

        return ContextProjector(
            redaction=self.redaction_environment(),
            clock=lambda: FROZEN,
            **kwargs,
        )

    def redaction_environment(self):
        from cofferdam.workstation.context.projection import HostRedactionEnvironment

        return HostRedactionEnvironment(
            cofferdam_home=str(self.home),
            project_roots=(str(self.project_root),),
            vault_roots=(str(self.vault_root),),
            slot_roots=(str(self.home / "slots" / "a"), str(self.home / "slots" / "b")),
            home_directories=(str(self.home),),
        )

    def write_vault_documents(self) -> None:
        """All four roles carry a sentinel, including the two a pack may hold."""
        (self.vault_root / "USER.md").write_text(
            "# User\n\n" + SENTINEL_USER + "\n", encoding="utf-8"
        )
        (self.vault_root / "COMMUNICATION_STYLE.md").write_text(
            "# Communication style\n\nDirect, no filler. " + SENTINEL_STYLE + "\n",
            encoding="utf-8",
        )
        (self.vault_root / "PREFERENCES.md").write_text(
            "# Preferences\n\nPython, stdlib first. " + SENTINEL_PREFS + "\n",
            encoding="utf-8",
        )
        (self.vault_root / "CROSS_PROJECT.md").write_text(
            "# Cross-project\n\n" + SENTINEL_CROSS + "\n", encoding="utf-8"
        )

    # -- projecting ----------------------------------------------------------

    def project(self, message="What should I do next?", **kwargs):
        pack = self.build(message)
        return self.projector.project(pack, **kwargs)

    @staticmethod
    def serialized(projection) -> str:
        """The whole object as text. What a later surface would actually send."""
        return json.dumps(projection.to_dict(), ensure_ascii=False, sort_keys=True)

    @staticmethod
    def prefs(projection):
        return [part.source_ref for part in projection.parts]

    @staticmethod
    def reasons(projection):
        return [omission.reason for omission in projection.omissions]

    @staticmethod
    def omitted(projection, source_ref):
        for omission in projection.omissions:
            if omission.source_ref == source_ref:
                return omission
        return None

    @staticmethod
    def projected(projection, source_ref):
        for part in projection.parts:
            if part.source_ref == source_ref:
                return part
        return None


# -- the type boundary -------------------------------------------------------


class TypeBoundary(ProjectionHarness):
    """D-2026-08-11-5 in the type system, not in a review comment."""

    def test_a_projection_is_not_a_pack_and_a_pack_is_not_a_projection(self):
        from cofferdam.workstation.context import LocalContextPack
        from cofferdam.workstation.context.projection import CloudContextProjection

        self.activate()
        projection = self.project()
        self.assertNotIsInstance(projection, LocalContextPack)
        self.assertFalse(issubclass(CloudContextProjection, LocalContextPack))
        self.assertFalse(issubclass(LocalContextPack, CloudContextProjection))

    def test_the_pack_has_no_method_that_produces_a_projection(self):
        from cofferdam.workstation.context import LocalContextPack

        for name in dir(LocalContextPack):
            if name.startswith("_"):
                continue
            for forbidden in ("cloud", "project", "egress", "external", "send", "wire"):
                self.assertNotIn(
                    forbidden,
                    name.lower(),
                    "LocalContextPack." + name + " reads as an egress path",
                )

    def test_projection_requires_the_explicit_projector_step(self):
        """There is no constructor that turns a pack into a projection."""
        from cofferdam.workstation.context.projection import CloudContextProjection

        self.activate()
        pack = self.build()
        for name in ("from_pack", "from_local", "of", "parse"):
            self.assertFalse(
                hasattr(CloudContextProjection, name),
                "CloudContextProjection." + name + " bypasses the policy step",
            )
        with self.assertRaises(TypeError):
            CloudContextProjection(pack)  # type: ignore[call-arg]

    def test_the_projector_refuses_anything_that_is_not_a_pack(self):
        from cofferdam.workstation.context.projection import ProjectionInputInvalid

        for value in ({"parts": []}, None, "a pack", 7, []):
            with self.assertRaises(ProjectionInputInvalid):
                self.projector.project(value)

    def test_a_cloud_part_cannot_carry_the_local_structures(self):
        """`fields` and `section` are the two that would leak by accident."""
        from cofferdam.workstation.context.projection import CloudContextPart

        self.activate()
        projection = self.project()
        self.assertTrue(projection.parts)
        for part in projection.parts:
            self.assertIsInstance(part, CloudContextPart)
            self.assertFalse(hasattr(part, "fields"))
            self.assertFalse(hasattr(part, "section"))
        self.assertNotIn("fields", self.serialized(projection))

    def test_the_redaction_environment_is_required(self):
        """Fail-closed by construction: a caller cannot forget the host values."""
        from cofferdam.workstation.context.projection import ContextProjector

        with self.assertRaises(TypeError):
            ContextProjector()  # type: ignore[call-arg]


# -- global mind -------------------------------------------------------------


class GlobalMindExclusion(ProjectionHarness):
    """All four roles are denied, including the two a pack legitimately holds."""

    def test_the_pack_really_does_contain_the_style_and_preference_sentinels(self):
        """The premise. Without this the exclusion tests would prove nothing."""
        self.activate()
        pack = self.build()
        text = self.all_text(pack)
        self.assertIn(SENTINEL_STYLE, text)
        self.assertIn(SENTINEL_PREFS, text)

    def test_no_global_sentinel_survives_projection(self):
        self.activate()
        projection = self.project()
        blob = self.serialized(projection)
        for sentinel in GLOBAL_SENTINELS:
            self.assertNotIn(sentinel, blob)

    def test_no_global_reference_is_a_projected_part(self):
        self.activate()
        projection = self.project()
        for part in projection.parts:
            self.assertFalse(part.source_ref.startswith("global:"))

    def test_the_two_included_global_roles_are_omitted_with_a_reason(self):
        from cofferdam.workstation.context.projection import OMIT_POLICY_EXCLUDED

        self.activate()
        projection = self.project()
        for role in ("communication_style", "preferences"):
            omission = self.omitted(projection, "global:" + role)
            self.assertIsNotNone(omission, role + " vanished without a row")
            self.assertEqual(omission.reason, OMIT_POLICY_EXCLUDED)

    def test_granting_all_four_roles_does_not_widen_a_projection(self):
        self.activate()
        projection = self.project()
        self.assertEqual(
            [p.source_ref for p in projection.parts if p.source_ref.startswith("global:")],
            [],
        )

    def test_the_projector_never_reads_a_role_itself(self):
        """It has no mind service, so re-reading an excluded role is impossible."""
        from cofferdam.workstation.context.projection import ContextProjector

        self.assertFalse(hasattr(self.projector, "mind"))
        self.assertFalse(hasattr(self.projector, "_mind"))
        public = [n for n in dir(ContextProjector) if not n.startswith("_")]
        self.assertEqual(public, ["project"])


# -- working context ---------------------------------------------------------


class WorkingContextProjection(ProjectionHarness):
    """Only the allowlisted fields, taken from structure rather than from text."""

    def working(self):
        self.activate()
        self.workspaces.set_objective("Ship the egress boundary")
        self.workspaces.update_context(
            {
                "expected_next_step": "Write the projection tests",
                "plan_checkpoint": "M2J#pr35",
                "pending_decision_ref": "DECISIONS#d-2026-08-13-3",
            }
        )
        return self.project()

    def test_the_allowed_fields_survive(self):
        projection = self.working()
        part = self.projected(projection, "workspace:" + WORKSPACE_ID + ":working_context")
        self.assertIsNotNone(part)
        self.assertIn("Ship the egress boundary", part.text)
        self.assertIn("Write the projection tests", part.text)
        self.assertIn("M2J#pr35", part.text)
        self.assertIn("DECISIONS#d-2026-08-13-3", part.text)

    def test_the_internal_fields_do_not(self):
        projection = self.working()
        blob = self.serialized(projection)
        for forbidden in (
            "delegated_worker",
            "delegated worker",
            "active_task",
            "active task",
            "objective_source",
            "objective_set_at",
            "latest_evidence_ref",
            "latest evidence",
            "revision",
            "delegation",
        ):
            self.assertNotIn(forbidden, blob, forbidden + " reached the projection")

    def test_a_canonical_task_id_never_reaches_the_projection(self):
        """The local part renders one; the projection is built from `fields`."""
        self.activate()
        pack = self.build()
        local = self.part(pack, "workspace:" + WORKSPACE_ID + ":working_context")
        self.assertIsNotNone(local)
        self.assertIn("active_task", local.fields)

        projection = self.projector.project(pack)
        blob = self.serialized(projection)
        self.assertNotIn("task_id", blob)

    def test_the_projection_is_built_from_fields_not_from_rendered_text(self):
        """Proved by corrupting the rendered text and seeing the values hold."""
        import dataclasses

        self.activate()
        self.workspaces.set_objective("Ship the egress boundary")
        pack = self.build()
        reference = "workspace:" + WORKSPACE_ID + ":working_context"
        local = self.part(pack, reference)
        poisoned = dataclasses.replace(local, text="delegated worker: claude-agent-sdk\nLEAK")
        pack = dataclasses.replace(
            pack,
            parts=tuple(poisoned if p.source_ref == reference else p for p in pack.parts),
        )

        projection = self.projector.project(pack)
        part = self.projected(projection, reference)
        self.assertIsNotNone(part)
        self.assertIn("Ship the egress boundary", part.text)
        self.assertNotIn("LEAK", self.serialized(projection))
        self.assertNotIn("claude-agent-sdk", self.serialized(projection))

    def test_working_context_with_no_allowed_field_is_omitted_not_empty(self):
        from cofferdam.workstation.context.projection import OMIT_SOURCE_EMPTY

        self.activate()
        projection = self.project()
        reference = "workspace:" + WORKSPACE_ID + ":working_context"
        if self.projected(projection, reference) is None:
            omission = self.omitted(projection, reference)
            self.assertIsNotNone(omission)
            self.assertEqual(omission.reason, OMIT_SOURCE_EMPTY)

    def test_malformed_fields_do_not_crash_the_projection(self):
        import dataclasses

        self.activate()
        pack = self.build()
        reference = "workspace:" + WORKSPACE_ID + ":working_context"
        local = self.part(pack, reference)
        for broken in (None, {}, {"objective": 17}, {"objective": ["a"]}, {"objective": None}):
            poisoned = dataclasses.replace(local, fields=broken)
            candidate = dataclasses.replace(
                pack,
                parts=tuple(poisoned if p.source_ref == reference else p for p in pack.parts),
            )
            projection = self.projector.project(candidate)
            self.assertNotIn("17", [p.text for p in projection.parts])


# -- project content ---------------------------------------------------------


class ProjectContent(ProjectionHarness):
    """The three roles the policy names, and nothing that merely resembles them."""

    def test_status_plan_and_decisions_are_eligible(self):
        self.activate()
        projection = self.project()
        refs = [p.source_ref for p in projection.parts]
        for role in ("status", "plan", "decisions"):
            self.assertTrue(
                any(r.startswith("project:" + PROJECT_ID + ":" + role) for r in refs),
                role + " was not projected",
            )

    def test_design_is_not_introduced_by_the_projection(self):
        """It is mapped and readable on this host and is in no pack. It stays out."""
        self.activate()
        projection = self.project()
        self.assertNotIn("Architecture notes", self.serialized(projection))
        for part in projection.parts:
            self.assertNotIn(":design", part.source_ref)

    def test_the_current_user_message_is_excluded(self):
        from cofferdam.workstation.context.projection import OMIT_POLICY_EXCLUDED

        self.activate()
        projection = self.project("SENTINEL-USER-MESSAGE-a91f")
        self.assertNotIn("SENTINEL-USER-MESSAGE-a91f", self.serialized(projection))
        omission = self.omitted(projection, "user:current_message")
        self.assertIsNotNone(omission)
        self.assertEqual(omission.reason, OMIT_POLICY_EXCLUDED)

    def test_an_unsupported_source_kind_is_excluded(self):
        import dataclasses

        from cofferdam.workstation.context import ContextPart
        from cofferdam.workstation.context.projection import OMIT_SOURCE_KIND_MISMATCH

        self.activate()
        pack = self.build()
        smuggled = ContextPart(
            source_kind="memory",
            source_ref="project:" + PROJECT_ID + ":plan",
            observed_at=FROZEN,
            selection="structural",
            text="wrong kind for this reference",
        )
        candidate = dataclasses.replace(pack, parts=pack.parts + (smuggled,))
        projection = self.projector.project(candidate)
        self.assertNotIn("wrong kind for this reference", self.serialized(projection))
        self.assertIn(OMIT_SOURCE_KIND_MISMATCH, self.reasons(projection))

    def test_a_project_role_outside_the_policy_is_excluded(self):
        import dataclasses

        from cofferdam.workstation.context import ContextPart
        from cofferdam.workstation.context.projection import OMIT_POLICY_EXCLUDED

        self.activate()
        pack = self.build()
        smuggled = ContextPart(
            source_kind="memory",
            source_ref="project:" + PROJECT_ID + ":design",
            observed_at=FROZEN,
            selection="structural",
            text="SENTINEL-DESIGN-ROLE-4b2c",
        )
        candidate = dataclasses.replace(pack, parts=pack.parts + (smuggled,))
        projection = self.projector.project(candidate)
        self.assertNotIn("SENTINEL-DESIGN-ROLE-4b2c", self.serialized(projection))
        self.assertIn(OMIT_POLICY_EXCLUDED, self.reasons(projection))

    def test_an_unrelated_project_is_excluded(self):
        import dataclasses

        from cofferdam.workstation.context import ContextPart

        self.activate()
        pack = self.build()
        smuggled = ContextPart(
            source_kind="memory",
            source_ref="project:someone-else:status",
            observed_at=FROZEN,
            selection="structural",
            text="SENTINEL-OTHER-PROJECT-9de1",
        )
        candidate = dataclasses.replace(pack, parts=pack.parts + (smuggled,))
        projection = self.projector.project(candidate)
        self.assertNotIn("SENTINEL-OTHER-PROJECT-9de1", self.serialized(projection))


# -- metadata path safety ----------------------------------------------------


class MetadataPathSafety(ProjectionHarness):
    """No generated metadata field is derived from a location."""

    def test_no_projection_metadata_carries_a_path(self):
        self.activate()
        projection = self.project()
        metadata = json.dumps(
            {
                "policy_id": projection.policy_id,
                "workspace_id": projection.workspace_id,
                "project_id": projection.project_id,
                "built_at": projection.built_at,
                "parts": [
                    {
                        "source_kind": p.source_kind,
                        "source_ref": p.source_ref,
                        "observed_at": p.observed_at,
                        "selection": p.selection,
                        "redactions": list(p.redactions),
                    }
                    for p in projection.parts
                ],
                "omissions": [o.to_dict() for o in projection.omissions],
            }
        )
        for marker in (
            str(self.home),
            str(self.project_root),
            str(self.vault_root),
            "/home/",
            "slots/a",
            "slots/b",
            "~/",
            "..",
        ):
            self.assertNotIn(marker, metadata, marker + " appeared in metadata")

    def test_every_reference_is_still_a_semantic_reference(self):
        from cofferdam.workstation.context.kinds import semantic_ref

        self.activate()
        projection = self.project()
        for part in projection.parts:
            self.assertEqual(semantic_ref(part.source_ref), part.source_ref)
        for omission in projection.omissions:
            self.assertEqual(semantic_ref(omission.source_ref), omission.source_ref)

    def test_the_summary_is_safe_to_log(self):
        """Counts and kinds. No text, no references, no headings."""
        self.activate()
        self.workspaces.set_objective("Ship the egress boundary")
        projection = self.project("SENTINEL-USER-MESSAGE-a91f")
        summary = json.dumps(projection.summary(), sort_keys=True)
        self.assertNotIn("SENTINEL-USER-MESSAGE-a91f", summary)
        self.assertNotIn("Ship the egress boundary", summary)
        self.assertNotIn("source_refs", summary)
        self.assertNotIn("#", summary)
        self.assertIn("policy_id", summary)
        self.assertIn("omission_reasons", summary)


# -- content path safety -----------------------------------------------------


class ContentPathSanitization(ProjectionHarness):
    """A clean `source_ref` says nothing about the text under it."""

    def write_project_documents(self) -> None:
        super().write_project_documents()
        (self.project_root / "STATUS.md").write_text(
            "# Status\n"
            "\n"
            "The canonical checkout is " + str(self.project_root) + " and the\n"
            "operational home is " + str(self.home) + ".\n"
            "Deployment uses " + str(self.home / "slots" / "a") + " and\n"
            + str(self.home / "slots" / "b")
            + ".\n"
            "The vault lives at " + str(self.vault_root) + ".\n"
            "A home-relative path such as ~/cofferdam/state is also local.\n"
            "So is /home/someone-else/notes/private.md.\n",
            encoding="utf-8",
        )

    def test_the_local_pack_really_does_carry_those_paths(self):
        """The premise: PR3 proved canonical Markdown legitimately contains them."""
        self.activate()
        pack = self.build()
        text = self.all_text(pack)
        self.assertIn(str(self.project_root), text)
        self.assertIn(str(self.vault_root), text)

    def test_no_controlled_local_path_survives_projection(self):
        self.activate()
        projection = self.project()
        blob = self.serialized(projection)
        for marker in (
            str(self.project_root),
            str(self.vault_root),
            str(self.home),
            str(self.home / "slots" / "a"),
            str(self.home / "slots" / "b"),
            "~/cofferdam/state",
            "/home/someone-else/notes/private.md",
        ):
            self.assertNotIn(marker, blob, marker + " survived projection")

    def test_redaction_is_declared_rather_than_silent(self):
        from cofferdam.workstation.context.projection import REDACTION_PATH

        self.activate()
        projection = self.project()
        part = self.projected(projection, "project:" + PROJECT_ID + ":status")
        self.assertIsNotNone(part)
        self.assertIn(REDACTION_PATH, part.redactions)
        self.assertGreater(projection.redacted_parts, 0)

    def test_the_surrounding_project_sentence_survives(self):
        """Redaction preserves useful content; it does not delete the part."""
        self.activate()
        projection = self.project()
        part = self.projected(projection, "project:" + PROJECT_ID + ":status")
        self.assertIn("The canonical checkout is", part.text)
        self.assertIn("Deployment uses", part.text)


# -- secrets -----------------------------------------------------------------


class SecretHandling(ProjectionHarness):
    """High-risk material fails the part closed rather than being rewritten."""

    FAKE_SECRET = "sk-testonly000000000000000000000000000000000000000"

    def write_project_documents(self) -> None:
        super().write_project_documents()
        (self.project_root / "STATUS.md").write_text(
            "# Status\n"
            "\n"
            "SENTINEL-STATUS-BODY-6a11 is ordinary project prose.\n"
            "\n"
            "## Credentials\n"
            "\n"
            "The bridge token is " + self.FAKE_SECRET + " on this host.\n",
            encoding="utf-8",
        )

    def test_the_fake_secret_does_not_survive_serialization(self):
        self.activate()
        projection = self.project()
        self.assertNotIn(self.FAKE_SECRET, self.serialized(projection))

    def test_the_affected_part_is_omitted_with_a_closed_reason(self):
        from cofferdam.workstation.context.projection import OMIT_SENSITIVE_CONTENT

        self.activate()
        projection = self.project()
        omission = self.omitted(projection, "project:" + PROJECT_ID + ":status")
        self.assertIsNotNone(omission, "the part vanished without a row")
        self.assertEqual(omission.reason, OMIT_SENSITIVE_CONTENT)

    def test_the_omission_detail_carries_no_secret_material(self):
        self.activate()
        projection = self.project()
        omission = self.omitted(projection, "project:" + PROJECT_ID + ":status")
        self.assertNotIn(self.FAKE_SECRET, json.dumps(omission.to_dict()))
        self.assertNotIn("sk-", json.dumps(omission.to_dict()))

    def test_omitting_is_preferred_to_a_lossy_rewrite(self):
        """No partial version of the affected part is emitted."""
        self.activate()
        projection = self.project()
        self.assertIsNone(self.projected(projection, "project:" + PROJECT_ID + ":status"))
        self.assertNotIn("SENTINEL-STATUS-BODY-6a11", self.serialized(projection))

    def test_the_other_roles_are_unaffected(self):
        """Fail-closed is per part, not per projection."""
        self.activate()
        projection = self.project()
        refs = [p.source_ref for p in projection.parts]
        self.assertTrue(any(r.startswith("project:" + PROJECT_ID + ":plan") for r in refs))


# -- provenance --------------------------------------------------------------


class Provenance(ProjectionHarness):
    """Every projected part remains judgeable without exposing the host."""

    def test_every_part_carries_kind_reference_and_observation_time(self):
        self.activate()
        projection = self.project()
        self.assertTrue(projection.parts)
        for part in projection.parts:
            self.assertTrue(part.source_kind)
            self.assertTrue(part.source_ref)
            self.assertEqual(part.observed_at, FROZEN)
            self.assertIn(part.selection, ("whole", "explicit", "structural", "retrieved"))

    def test_a_section_reference_survives_as_a_slug_not_a_heading(self):
        self.activate()
        self.workspaces.update_context({"plan_checkpoint": "M2J"})
        projection = self.project()
        part = self.projected(projection, "project:" + PROJECT_ID + ":plan#m2j")
        self.assertIsNotNone(part)
        self.assertIn("Workspace and mind foundation", part.text)

    def test_the_projection_names_its_policy(self):
        from cofferdam.workstation.context.projection import PROJECT_CONTEXT_EXTERNAL_V1

        self.activate()
        projection = self.project()
        self.assertEqual(projection.policy_id, PROJECT_CONTEXT_EXTERNAL_V1)
        self.assertEqual(projection.policy_id, "project_context_external_v1")


# -- budget ------------------------------------------------------------------


class Budget(ProjectionHarness):
    """The projection has its own bound, and it is not the pack's."""

    def test_the_egress_budget_is_its_own_number(self):
        from cofferdam.workstation.context.policy import DEFAULT_TOTAL_BUDGET_BYTES
        from cofferdam.workstation.context.projection import (
            DEFAULT_PROJECTION_BUDGET_BYTES,
        )

        self.assertNotEqual(DEFAULT_PROJECTION_BUDGET_BYTES, DEFAULT_TOTAL_BUDGET_BYTES)
        self.assertEqual(DEFAULT_PROJECTION_BUDGET_BYTES, 16 * 1024)

    def test_accounting_is_exact_utf8(self):
        self.activate()
        projection = self.project()
        consumed = sum(len(p.text.encode("utf-8")) for p in projection.parts)
        self.assertEqual(projection.budget.consumed, consumed)
        self.assertEqual(projection.budget.unit, "utf8_bytes")
        self.assertEqual(
            projection.budget.remaining,
            projection.budget.total - projection.budget.consumed,
        )
        self.assertLessEqual(projection.budget.consumed, projection.budget.total)

    def test_output_is_bounded(self):
        self.activate()
        projection = self.project(budget_bytes=512)
        consumed = sum(len(p.text.encode("utf-8")) for p in projection.parts)
        self.assertLessEqual(consumed, 512)

    def test_priority_order_is_preserved(self):
        self.activate()
        self.workspaces.set_objective("Ship the egress boundary")
        projection = self.project()
        refs = [p.source_ref for p in projection.parts]
        expected = [
            "workspace:" + WORKSPACE_ID + ":working_context",
            "project:" + PROJECT_ID + ":status",
            "project:" + PROJECT_ID + ":plan",
            "project:" + PROJECT_ID + ":decisions",
        ]
        self.assertEqual(refs, [r for r in expected if r in refs])

    def test_material_lost_to_the_budget_is_recorded(self):
        from cofferdam.workstation.context.projection import OMIT_BUDGET_EXHAUSTED

        self.activate()
        self.workspaces.set_objective("Ship the egress boundary")
        projection = self.project(budget_bytes=200)
        self.assertIn(OMIT_BUDGET_EXHAUSTED, self.reasons(projection))

    def test_nothing_disappears_without_a_row(self):
        """Every part of the pack is either projected or explained."""
        self.activate()
        self.workspaces.set_objective("Ship the egress boundary")
        pack = self.build()
        projection = self.projector.project(pack)
        accounted = {p.source_ref for p in projection.parts}
        accounted |= {o.source_ref for o in projection.omissions}
        for part in pack.parts:
            self.assertIn(part.source_ref, accounted, part.source_ref + " vanished")

    def test_truncation_is_declared(self):
        self.activate()
        projection = self.project(budget_bytes=300)
        for part in projection.parts:
            if len(part.text.encode("utf-8")) < 300:
                continue
            self.assertTrue(part.truncated)


# -- determinism -------------------------------------------------------------


class Determinism(ProjectionHarness):
    """One frozen pack, one policy, one environment: one answer."""

    def test_two_projections_of_one_pack_are_identical(self):
        self.activate()
        self.workspaces.set_objective("Ship the egress boundary")
        pack = self.build()
        first = self.projector.project(pack)
        second = self.projector.project(pack)
        self.assertEqual(self.serialized(first), self.serialized(second))


# -- candidates --------------------------------------------------------------


class Candidates(ProjectionHarness):
    """M2N admission to a pack is not admission to a projection."""

    def candidate_pack(self, source_kind, source_ref, text):
        from cofferdam.workstation.context import RetrievedCandidate

        self.activate()
        return self.build(
            candidates=[
                RetrievedCandidate(source_kind=source_kind, source_ref=source_ref, text=text)
            ]
        )

    def test_a_candidate_is_not_automatically_cloud_allowed(self):
        pack = self.candidate_pack("memory", "global:user", "SENTINEL-CANDIDATE-USER-2f80")
        projection = self.projector.project(pack)
        self.assertNotIn("SENTINEL-CANDIDATE-USER-2f80", self.serialized(projection))

    def test_a_candidate_cannot_bypass_global_mind_exclusion(self):
        from cofferdam.workstation.context.projection import OMIT_POLICY_EXCLUDED

        pack = self.candidate_pack(
            "memory", "global:cross_project", "SENTINEL-CANDIDATE-CROSS-5a13"
        )
        projection = self.projector.project(pack)
        self.assertNotIn("SENTINEL-CANDIDATE-CROSS-5a13", self.serialized(projection))
        self.assertIn(OMIT_POLICY_EXCLUDED, self.reasons(projection))

    def test_a_candidate_cannot_inject_a_path_shaped_reference(self):
        from cofferdam.workstation.context.errors import SourceRefInvalid

        for reference in (
            "project:demo:../../etc/passwd",
            "file:/home/operator/secrets",
            "project:demo:status#/home/operator",
            "global:user:~/.ssh/id_rsa",
        ):
            with self.assertRaises(SourceRefInvalid):
                self.candidate_pack("memory", reference, "text")

    def test_an_allowed_candidate_still_passes_sanitization_and_budget(self):
        from cofferdam.workstation.context.projection import REDACTION_PATH

        pack = self.candidate_pack(
            "plan",
            "project:" + PROJECT_ID + ":plan",
            "A retrieved plan fragment mentioning " + str(self.project_root) + " directly.",
        )
        projection = self.projector.project(pack)
        blob = self.serialized(projection)
        self.assertNotIn(str(self.project_root), blob)
        projected = [p for p in projection.parts if p.source_ref.endswith(":plan")]
        self.assertTrue(projected)
        self.assertTrue(any(REDACTION_PATH in p.redactions for p in projected))


# -- side effects ------------------------------------------------------------


class SideEffects(ProjectionHarness):
    """Projection reads and transforms. It does nothing else, to anything."""

    def test_projecting_opens_no_socket(self):
        import socket

        original = socket.socket

        def refuse(*args, **kwargs):
            raise AssertionError("the projector attempted a network call")

        socket.socket = refuse  # type: ignore[assignment]
        try:
            self.activate()
            projection = self.project()
            self.assertTrue(projection.parts)
        finally:
            socket.socket = original  # type: ignore[assignment]

    def test_the_package_imports_nothing_that_could_send_anything(self):
        from pathlib import Path

        import cofferdam.workstation.context.projection as package

        root = Path(package.__file__).parent
        forbidden = (
            "urllib",
            "http.client",
            "httpx",
            "requests",
            "socket",
            "subprocess",
            "asyncio",
            "anthropic",
            "openai",
            "ollama",
            "fastapi",
            "sqlite3",
        )
        modules = sorted(root.glob("*.py"))
        self.assertTrue(modules)
        for module in modules:
            for line in module.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not (stripped.startswith("import ") or stripped.startswith("from ")):
                    continue
                for name in forbidden:
                    self.assertNotIn(name, stripped, module.name + ": " + stripped)

    def test_the_projector_has_no_send_or_persist_method(self):
        from cofferdam.workstation.context.projection import ContextProjector

        public = [n for n in dir(ContextProjector) if not n.startswith("_")]
        self.assertEqual(public, ["project"])
        for name in dir(ContextProjector):
            for forbidden in ("send", "post", "upload", "submit", "save", "store", "persist"):
                self.assertNotIn(forbidden, name.lower())

    def test_projecting_writes_nothing_to_disk(self):
        before = self.snapshot()
        self.activate()
        pack = self.build()
        before_project = self.snapshot()
        self.projector.project(pack)
        self.assertEqual(self.snapshot(), before_project)
        self.assertEqual(self.snapshot(), before)

    def test_projecting_creates_no_memory_proposal(self):
        self.activate()
        pack = self.build()
        before = self.mind.list_proposals()
        self.projector.project(pack)
        self.assertEqual(self.mind.list_proposals(), before)

    def test_projecting_does_not_change_working_context(self):
        self.activate()
        self.workspaces.set_objective("Ship the egress boundary")
        before = self.workspaces.current()
        self.projector.project(self.build())
        self.assertEqual(self.workspaces.current(), before)

    def test_projecting_emits_no_log_record(self):
        import logging

        self.activate()
        pack = self.build()
        with self.assertNoLogs(logging.getLogger(), level=logging.DEBUG):
            self.projector.project(pack)

    def test_nothing_is_persisted_between_two_projections(self):
        """No cache, no history, no identifier that could join two of them."""
        self.activate()
        projection = self.project()
        for name in ("id", "projection_id", "cache", "history", "path"):
            self.assertFalse(hasattr(projection, name))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
