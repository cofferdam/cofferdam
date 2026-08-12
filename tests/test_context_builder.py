"""Assembling local context: what goes in, in what order, and what is left out.

The Context Builder answers one question — *given this message and this host's
current state, what bounded LOCAL context is appropriate?* — and every property
worth having is a property of the answer's **honesty** rather than its richness:

* the user's own sentence survives byte-for-byte or the build refuses;
* nothing is dropped silently;
* every part says where it came from, semantically, and never as a path;
* the same frozen inputs produce the same pack.

These tests run a real :class:`WorkspaceService` and a real :class:`MindService`
over real temporary directories, for the reason
:mod:`tests._mind_doubles` gives: the containment and the grant are properties of
the filesystem, and a double that returned strings would assert nothing.
"""

from __future__ import annotations

import unittest

from ._mind_doubles import PROJECT_ID, WORKSPACE_ID, MindHarness

FROZEN = "2026-08-13T09:00:00.000Z"


class ContextHarness(MindHarness):
    """A workspace over a project with three role-mapped documents, plus a vault."""

    grant_vault = True
    project_documents = {
        "status": "STATUS.md",
        "plan": "ROADMAP.md",
        "decisions": "DECISIONS.md",
        "design": "DESIGN.md",
    }
    vault_documents = {
        "user": "USER.md",
        "communication_style": "COMMUNICATION_STYLE.md",
        "preferences": "PREFERENCES.md",
        "cross_project": "CROSS_PROJECT.md",
    }

    def setUp(self) -> None:
        super().setUp()
        self.write_project_documents()
        self.write_vault_documents()
        self.builder = self.make_builder()

    def make_builder(self, **kwargs):
        from cofferdam.workstation.context import ContextBuilder

        return ContextBuilder(
            workspaces=self.workspaces,
            mind=self.mind,
            clock=lambda: FROZEN,
            **kwargs,
        )

    # -- documents -----------------------------------------------------------

    def write_project_documents(self) -> None:
        (self.project_root / "STATUS.md").write_text(
            "# Status\n\nThe host is up.\n\n## Right now\n\nOne task in flight.\n",
            encoding="utf-8",
        )
        (self.project_root / "ROADMAP.md").write_text(
            "# Roadmap\n"
            "\n"
            "## M2J\n"
            "\n"
            "Workspace and mind foundation.\n"
            "\n"
            "## M2K\n"
            "\n"
            "Evidence and evaluation.\n"
            "\n"
            "## M2L\n"
            "\n"
            "The local planner.\n",
            encoding="utf-8",
        )
        (self.project_root / "DECISIONS.md").write_text(
            "# Decisions\n"
            "\n"
            "## D-1\n"
            "\n"
            "The oldest decision.\n"
            "\n"
            "## D-2\n"
            "\n"
            "A middle decision.\n"
            "\n"
            "## D-3\n"
            "\n"
            "The newest decision.\n",
            encoding="utf-8",
        )
        (self.project_root / "DESIGN.md").write_text(
            "# Design\n\nArchitecture notes.\n", encoding="utf-8"
        )

    def write_vault_documents(self) -> None:
        (self.vault_root / "USER.md").write_text(
            "# User\n\nSENTINEL-USER-IDENTITY\n", encoding="utf-8"
        )
        (self.vault_root / "COMMUNICATION_STYLE.md").write_text(
            "# Communication style\n\nDirect, no filler.\n", encoding="utf-8"
        )
        (self.vault_root / "PREFERENCES.md").write_text(
            "# Preferences\n\nPython, stdlib first.\n", encoding="utf-8"
        )
        (self.vault_root / "CROSS_PROJECT.md").write_text(
            "# Cross-project\n\nSENTINEL-CROSS-PROJECT\n", encoding="utf-8"
        )

    # -- reading the pack ----------------------------------------------------

    def build(self, message="What should I do next?", **kwargs):
        return self.builder.build(message, **kwargs)

    @staticmethod
    def refs(pack):
        return [part.source_ref for part in pack.parts]

    @staticmethod
    def part(pack, source_ref):
        for candidate in pack.parts:
            if candidate.source_ref == source_ref:
                return candidate
        return None

    @staticmethod
    def omission(pack, source_ref):
        for candidate in pack.omissions:
            if candidate.source_ref == source_ref:
                return candidate
        return None

    @staticmethod
    def all_text(pack):
        return "\n".join(part.text for part in pack.parts)


# -- the current user message ------------------------------------------------


class CurrentMessage(ContextHarness):
    """The one part that is never trimmed, never reordered and never optional."""

    def test_the_message_is_preserved_byte_for_byte(self):
        from cofferdam.workstation.context import KIND_USER_INSTRUCTION, SELECTION_WHOLE

        message = "  Merhaba — ne yapmalıyız?\n\tTabs, emoji 🧱, and  double  spaces.  "
        self.activate()
        pack = self.build(message)

        part = pack.parts[0]
        self.assertEqual(part.source_kind, KIND_USER_INSTRUCTION)
        self.assertEqual(part.selection, SELECTION_WHOLE)
        self.assertEqual(part.text, message)
        self.assertFalse(part.truncated)
        self.assertEqual(part.content_bytes, len(message.encode("utf-8")))

    def test_the_message_is_always_the_first_part(self):
        from cofferdam.workstation.context import KIND_USER_INSTRUCTION

        self.activate()
        pack = self.build()
        self.assertEqual(pack.parts[0].source_kind, KIND_USER_INSTRUCTION)
        self.assertEqual(pack.parts[0].source_ref, "user:current_message")

    def test_the_message_is_present_with_no_workspace_at_all(self):
        pack = self.build("still a question")
        self.assertEqual(pack.parts[0].text, "still a question")

    def test_an_oversized_message_refuses_rather_than_being_trimmed(self):
        from cofferdam.workstation.context import CurrentMessageOversize

        self.activate()
        message = "x" * 5000
        with self.assertRaises(CurrentMessageOversize) as caught:
            self.build(message, budget_bytes=4096)
        self.assertEqual(caught.exception.budget_bytes, 4096)
        self.assertEqual(caught.exception.message_bytes, 5000)
        self.assertIn("4096", caught.exception.detail)
        self.assertIn("never trimmed", caught.exception.detail)

    def test_an_oversized_message_produces_no_pack_at_all(self):
        """No partial answer. A pack missing its highest-priority part is a lie."""
        from cofferdam.workstation.context import CurrentMessageOversize

        self.activate()
        try:
            self.build("x" * 5000, budget_bytes=4096)
        except CurrentMessageOversize as error:
            self.assertFalse(hasattr(error, "pack"))
        else:  # pragma: no cover - the assertion above is the test
            self.fail("expected a refusal")

    def test_a_message_that_exactly_fills_the_budget_is_accepted(self):
        self.activate()
        pack = self.build("x" * 4096, budget_bytes=4096)
        self.assertEqual(pack.budget.consumed, 4096)
        self.assertEqual(pack.budget.remaining, 0)
        self.assertEqual(len(pack.parts), 1)

    def test_multibyte_length_is_measured_in_bytes_not_characters(self):
        from cofferdam.workstation.context import CurrentMessageOversize

        self.activate()
        # 300 three-byte characters is 900 bytes and 300 characters.
        message = "ç" * 300  # two bytes each
        with self.assertRaises(CurrentMessageOversize):
            self.build(message, budget_bytes=400)
        pack = self.build(message, budget_bytes=700)
        self.assertEqual(pack.parts[0].content_bytes, 600)

    def test_an_empty_message_is_refused(self):
        from cofferdam.workstation.context import CurrentMessageInvalid

        self.activate()
        for value in ("", "   \n\t ", None, 42, b"bytes"):
            with self.assertRaises(CurrentMessageInvalid):
                self.build(value)

    def test_a_message_with_a_nul_is_refused(self):
        from cofferdam.workstation.context import CurrentMessageInvalid

        self.activate()
        with self.assertRaises(CurrentMessageInvalid):
            self.build("hello\x00world")


# -- priority ----------------------------------------------------------------


class Priority(ContextHarness):
    """The recorded order, asserted as an order rather than as a set."""

    def test_parts_appear_in_the_recorded_priority_order(self):
        self.activate()
        self.workspaces.update_context({"expected_next_step": "open the PR"})
        pack = self.build()

        self.assertEqual(
            self.refs(pack),
            [
                "user:current_message",
                "workspace:" + WORKSPACE_ID + ":working_context",
                "project:" + PROJECT_ID + ":status",
                "project:" + PROJECT_ID + ":plan",
                "project:" + PROJECT_ID + ":decisions",
                "global:communication_style",
                "global:preferences",
            ],
        )

    def test_lower_priority_material_is_dropped_before_higher(self):
        """A tight budget starves the tail, never the head."""
        from cofferdam.workstation.context import OMIT_BUDGET_EXHAUSTED

        self.activate()
        pack = self.build("a short question", budget_bytes=120)

        self.assertEqual(pack.parts[0].source_ref, "user:current_message")
        self.assertIsNone(self.part(pack, "global:preferences"))
        omitted = self.omission(pack, "global:preferences")
        self.assertEqual(omitted.reason, OMIT_BUDGET_EXHAUSTED)

    def test_the_evaluation_slot_is_reported_absent_rather_than_invented(self):
        """Priority position six has no source in this build, and says so."""
        from cofferdam.workstation.context import OMIT_NOT_IN_THIS_BUILD

        self.activate()
        pack = self.build()
        omitted = self.omission(pack, "evaluation:latest")
        self.assertEqual(omitted.reason, OMIT_NOT_IN_THIS_BUILD)
        self.assertIsNone(omitted.source_kind)
        self.assertNotIn("evaluation:latest", self.refs(pack))


# -- Working Context ---------------------------------------------------------


class WorkingContext(ContextHarness):
    def test_working_context_is_included_with_its_recorded_values(self):
        from cofferdam.workstation.context import KIND_WORKING_STATE, SELECTION_WHOLE

        self.activate()
        self.workspaces.set_objective("Ship the Context Builder")
        self.workspaces.update_context({"expected_next_step": "write the tests"})
        pack = self.build()

        part = self.part(pack, "workspace:" + WORKSPACE_ID + ":working_context")
        self.assertEqual(part.source_kind, KIND_WORKING_STATE)
        self.assertEqual(part.selection, SELECTION_WHOLE)
        self.assertIn("Ship the Context Builder", part.text)
        self.assertEqual(part.fields["objective"], "Ship the Context Builder")
        self.assertEqual(part.fields["expected_next_step"], "write the tests")

    def test_null_fields_stay_null_and_are_never_invented(self):
        self.activate()
        pack = self.build()
        part = self.part(pack, "workspace:" + WORKSPACE_ID + ":working_context")

        for name in (
            "objective",
            "plan_checkpoint",
            "pending_decision_ref",
            "latest_evidence_ref",
            "expected_next_step",
        ):
            self.assertIn(name, part.fields)
            self.assertIsNone(part.fields[name])
        # And the rendered text does not print a placeholder for any of them.
        self.assertNotIn("None", part.text)
        self.assertNotIn("null", part.text)

    def test_the_delegated_worker_stays_derived_from_the_project(self):
        """Never a stored copy — the same rule PR1's snapshot follows."""
        self.activate()
        pack = self.build()
        part = self.part(pack, "workspace:" + WORKSPACE_ID + ":working_context")
        self.assertIn("delegated_worker", part.fields)
        self.assertEqual(part.fields["delegated_worker"], "validation")

    def test_task_state_is_derived_and_absent_when_no_task_is_referenced(self):
        self.activate()
        pack = self.build()
        part = self.part(pack, "workspace:" + WORKSPACE_ID + ":working_context")
        self.assertIsNone(part.fields["active_task"])

    def test_with_no_active_workspace_everything_below_the_message_is_omitted(self):
        from cofferdam.workstation.context import OMIT_NO_ACTIVE_WORKSPACE

        pack = self.build()
        self.assertEqual(self.refs(pack), ["user:current_message"])
        self.assertIsNone(pack.workspace_id)
        self.assertIsNone(pack.project_id)
        reasons = {omission.reason for omission in pack.omissions}
        self.assertIn(OMIT_NO_ACTIVE_WORKSPACE, reasons)


# -- Project Mind ------------------------------------------------------------


class ProjectMind(ContextHarness):
    def test_only_the_three_recorded_roles_are_read(self):
        """`design` is mapped and readable, and is still not in the pack."""
        self.activate()
        pack = self.build()
        self.assertIsNotNone(self.part(pack, "project:" + PROJECT_ID + ":status"))
        self.assertIsNotNone(self.part(pack, "project:" + PROJECT_ID + ":plan"))
        self.assertIsNotNone(self.part(pack, "project:" + PROJECT_ID + ":decisions"))
        self.assertIsNone(self.part(pack, "project:" + PROJECT_ID + ":design"))
        self.assertNotIn("Architecture notes", self.all_text(pack))

    def test_an_unmapped_role_is_omitted_truthfully(self):
        from cofferdam.workstation.context import OMIT_SOURCE_ABSENT

        self.write_workspaces(documents={"status": "STATUS.md"})
        self.activate()
        pack = self.build()
        omitted = self.omission(pack, "project:" + PROJECT_ID + ":plan")
        self.assertEqual(omitted.reason, OMIT_SOURCE_ABSENT)

    def test_a_role_mapped_to_a_missing_file_is_omitted_truthfully(self):
        from cofferdam.workstation.context import OMIT_SOURCE_UNREADABLE

        (self.project_root / "ROADMAP.md").unlink()
        self.activate()
        pack = self.build()
        omitted = self.omission(pack, "project:" + PROJECT_ID + ":plan")
        self.assertEqual(omitted.reason, OMIT_SOURCE_UNREADABLE)

    def test_the_builder_reads_roles_and_never_filenames(self):
        """Re-map the role and the pack follows the role, not the old name."""
        (self.project_root / "OTHER_PLAN.md").write_text(
            "# Other\n\nA different plan document.\n", encoding="utf-8"
        )
        self.write_workspaces(
            documents={
                "status": "STATUS.md",
                "plan": "OTHER_PLAN.md",
                "decisions": "DECISIONS.md",
            }
        )
        self.activate()
        pack = self.build()
        part = self.part(pack, "project:" + PROJECT_ID + ":plan")
        self.assertIn("A different plan document", part.text)
        self.assertNotIn("Workspace and mind foundation", self.all_text(pack))

    def test_the_builder_exposes_no_way_to_name_a_file(self):
        from cofferdam.workstation.context import ContextBuilder

        code = ContextBuilder.build.__code__
        arguments = code.co_varnames[: code.co_argcount + code.co_kwonlyargcount]
        for forbidden in ("path", "filename", "root", "document", "file", "scope", "role"):
            self.assertNotIn(forbidden, arguments)


# -- section selection -------------------------------------------------------


class SectionSelection(ContextHarness):
    def test_an_explicit_plan_checkpoint_selects_that_section(self):
        from cofferdam.workstation.context import SELECTION_EXPLICIT

        self.activate()
        self.workspaces.update_context({"plan_checkpoint": "M2K"})
        pack = self.build()

        part = self.part(pack, "project:" + PROJECT_ID + ":plan#m2k")
        self.assertEqual(part.selection, SELECTION_EXPLICIT)
        self.assertEqual(part.section.heading, "M2K")
        self.assertIn("Evidence and evaluation", part.text)
        self.assertNotIn("The local planner", part.text)

    def test_an_explicit_pending_decision_selects_that_section(self):
        from cofferdam.workstation.context import SELECTION_EXPLICIT

        self.activate()
        self.workspaces.update_context({"pending_decision_ref": "D-2"})
        pack = self.build()

        part = self.part(pack, "project:" + PROJECT_ID + ":decisions#d-2")
        self.assertEqual(part.selection, SELECTION_EXPLICIT)
        self.assertIn("A middle decision", part.text)

    def test_a_reference_may_carry_a_leading_hash(self):
        self.activate()
        self.workspaces.update_context({"plan_checkpoint": "#m2l"})
        pack = self.build()
        self.assertIsNotNone(self.part(pack, "project:" + PROJECT_ID + ":plan#m2l"))

    def test_a_missing_referenced_section_omits_the_role_rather_than_substituting(self):
        """Answering a different question would be worse than answering none."""
        from cofferdam.workstation.context import OMIT_EXPLICIT_SECTION_MISSING

        self.activate()
        self.workspaces.update_context({"plan_checkpoint": "M9Z"})
        pack = self.build()

        self.assertIsNone(self.part(pack, "project:" + PROJECT_ID + ":plan"))
        for part in pack.parts:
            self.assertFalse(part.source_ref.startswith("project:" + PROJECT_ID + ":plan"))
        omitted = self.omission(pack, "project:" + PROJECT_ID + ":plan")
        self.assertEqual(omitted.reason, OMIT_EXPLICIT_SECTION_MISSING)
        self.assertNotIn("Workspace and mind foundation", self.all_text(pack))

    def test_without_a_reference_selection_is_structural_and_says_so(self):
        from cofferdam.workstation.context import SELECTION_STRUCTURAL

        self.activate()
        pack = self.build()
        part = self.part(pack, "project:" + PROJECT_ID + ":plan")
        self.assertEqual(part.selection, SELECTION_STRUCTURAL)
        self.assertIsNone(part.section)

    def test_decisions_are_selected_from_the_end_of_an_append_ordered_document(self):
        """`recent decisions` means the newest, and this file appends."""
        self.activate()
        pack = self.build(budget_bytes=1024)
        part = self.part(pack, "project:" + PROJECT_ID + ":decisions")
        self.assertIn("The newest decision", part.text)

    def test_a_long_document_is_bounded_and_marked_partial(self):
        self.activate()
        body = "\n".join("filler line " + str(index) for index in range(4000))
        (self.project_root / "STATUS.md").write_text(
            "# Status\n\n" + body + "\n", encoding="utf-8"
        )
        pack = self.build()
        part = self.part(pack, "project:" + PROJECT_ID + ":status")
        self.assertTrue(part.truncated)
        self.assertLess(part.content_bytes, len(body.encode("utf-8")))

    def test_truncation_never_splits_a_character(self):
        self.activate()
        (self.project_root / "STATUS.md").write_text(
            "# Status\n\n" + ("ş" * 40000) + "\n", encoding="utf-8"
        )
        pack = self.build()
        part = self.part(pack, "project:" + PROJECT_ID + ":status")
        self.assertTrue(part.truncated)
        part.text.encode("utf-8").decode("utf-8")  # would raise on a split character

    def test_headings_inside_fenced_code_are_not_sections(self):
        from cofferdam.workstation.context.sections import split_sections

        sections = split_sections("# Real\n\n```\n# Not a heading\n```\n\n## Also real\n")
        self.assertEqual([section.heading for section in sections], ["Real", "Also real"])

    def test_duplicate_headings_stay_distinguishable(self):
        from cofferdam.workstation.context.sections import split_sections

        sections = split_sections("## Notes\n\nfirst\n\n## Notes\n\nsecond\n")
        self.assertEqual([section.section_id for section in sections], ["notes", "notes-2"])

    def test_text_before_the_first_heading_is_kept(self):
        from cofferdam.workstation.context.sections import split_sections

        sections = split_sections("intro paragraph\n\n# First\n\nbody\n")
        self.assertIsNone(sections[0].heading)
        self.assertIn("intro paragraph", sections[0].body)


# -- Global Mind -------------------------------------------------------------


class GlobalMind(ContextHarness):
    def test_only_style_and_preferences_are_included(self):
        """`user` and `cross_project` are granted, readable, and still excluded."""
        self.activate()
        pack = self.build()

        self.assertIsNotNone(self.part(pack, "global:communication_style"))
        self.assertIsNotNone(self.part(pack, "global:preferences"))
        self.assertIsNone(self.part(pack, "global:user"))
        self.assertIsNone(self.part(pack, "global:cross_project"))
        self.assertNotIn("SENTINEL-USER-IDENTITY", self.all_text(pack))
        self.assertNotIn("SENTINEL-CROSS-PROJECT", self.all_text(pack))

    def test_the_policy_vocabulary_names_exactly_two_roles(self):
        from cofferdam.workstation.context import GLOBAL_CONTEXT_ROLES

        self.assertEqual(GLOBAL_CONTEXT_ROLES, ("communication_style", "preferences"))

    def test_no_grant_means_no_global_material(self):
        from cofferdam.workstation.context import OMIT_GRANT_ABSENT

        self.remove_grant()
        self.activate()
        pack = self.build()

        self.assertIsNone(self.part(pack, "global:communication_style"))
        omitted = self.omission(pack, "global:communication_style")
        self.assertEqual(omitted.reason, OMIT_GRANT_ABSENT)

    def test_a_grant_turned_off_means_no_global_material(self):
        from cofferdam.workstation.context import OMIT_GRANT_ABSENT

        self.write_grant(enabled=False)
        self.activate()
        pack = self.build()
        self.assertEqual(
            self.omission(pack, "global:preferences").reason, OMIT_GRANT_ABSENT
        )

    def test_global_extracts_are_bounded_more_tightly_than_project_material(self):
        from cofferdam.workstation.context.policy import SOURCE_CAPS

        self.assertLess(
            SOURCE_CAPS["global:communication_style"], SOURCE_CAPS["project:plan"]
        )


# -- provenance and the budget ----------------------------------------------


class Provenance(ContextHarness):
    def test_every_part_carries_the_three_required_fields(self):
        from cofferdam.workstation.context import SOURCE_KINDS

        self.activate()
        self.workspaces.set_objective("something")
        pack = self.build()
        self.assertGreater(len(pack.parts), 3)

        for part in pack.parts:
            self.assertIn(part.source_kind, SOURCE_KINDS)
            self.assertTrue(part.source_ref)
            self.assertEqual(part.observed_at, FROZEN)

    def test_observed_at_is_observation_time_not_document_time(self):
        import os
        import time

        old = time.time() - 60 * 60 * 24 * 365
        os.utime(self.project_root / "STATUS.md", (old, old))
        self.activate()
        pack = self.build()
        self.assertEqual(self.part(pack, "project:" + PROJECT_ID + ":status").observed_at, FROZEN)

    def test_only_the_kinds_this_build_can_honestly_produce_appear(self):
        from cofferdam.workstation.context import PRODUCIBLE_KINDS

        self.activate()
        pack = self.build()
        for part in pack.parts:
            self.assertIn(part.source_kind, PRODUCIBLE_KINDS)

    def test_the_reserved_kinds_are_declared_and_unreachable(self):
        from cofferdam.workstation.context import PRODUCIBLE_KINDS, SOURCE_KINDS

        reserved = set(SOURCE_KINDS) - set(PRODUCIBLE_KINDS)
        self.assertEqual(
            reserved,
            {
                "worker_result",
                "machine_observed",
                "external_model_output",
                "planner_inference",
            },
        )


class Budget(ContextHarness):
    def test_the_unit_is_declared_and_provider_independent(self):
        from cofferdam.workstation.context import BUDGET_UNIT

        self.activate()
        pack = self.build()
        self.assertEqual(BUDGET_UNIT, "utf8_bytes")
        self.assertEqual(pack.budget.unit, "utf8_bytes")

    def test_consumed_is_exactly_the_sum_of_part_bytes(self):
        self.activate()
        self.workspaces.set_objective("accounting")
        pack = self.build()
        self.assertEqual(
            pack.budget.consumed, sum(part.content_bytes for part in pack.parts)
        )

    def test_every_part_reports_its_own_encoded_length(self):
        self.activate()
        pack = self.build()
        for part in pack.parts:
            self.assertEqual(part.content_bytes, len(part.text.encode("utf-8")))

    def test_remaining_never_goes_negative_and_the_total_is_never_exceeded(self):
        self.activate()
        for budget in (200, 512, 4096, 65536):
            pack = self.build("a question", budget_bytes=budget)
            self.assertLessEqual(pack.budget.consumed, budget)
            self.assertEqual(pack.budget.remaining, budget - pack.budget.consumed)
            self.assertGreaterEqual(pack.budget.remaining, 0)

    def test_nothing_is_dropped_without_a_reason(self):
        """Every expected source is either a part or an omission. Never neither."""
        self.activate()
        pack = self.build("a question", budget_bytes=300)
        accounted = {part.source_ref for part in pack.parts}
        accounted |= {omission.source_ref for omission in pack.omissions}
        for expected in (
            "workspace:" + WORKSPACE_ID + ":working_context",
            "project:" + PROJECT_ID + ":status",
            "project:" + PROJECT_ID + ":plan",
            "project:" + PROJECT_ID + ":decisions",
            "global:communication_style",
            "global:preferences",
        ):
            base = expected.split("#")[0]
            self.assertTrue(
                any(ref.split("#")[0] == base for ref in accounted),
                base + " was neither included nor explained",
            )

    def test_an_invalid_budget_is_refused(self):
        from cofferdam.workstation.context import ContextBudgetInvalid

        self.activate()
        for value in (0, -1, "4096", 3.5, None if False else object()):
            with self.assertRaises(ContextBudgetInvalid):
                self.build(budget_bytes=value)


class Determinism(ContextHarness):
    def test_the_same_frozen_inputs_produce_the_same_pack(self):
        self.activate()
        self.workspaces.set_objective("be deterministic")
        first = self.build().to_dict()
        second = self.build().to_dict()
        self.assertEqual(first, second)

    def test_only_the_clock_moves_between_two_builds(self):
        stamps = iter(["2026-08-13T09:00:00.000Z", "2026-08-13T09:00:01.000Z"])
        builder = self.make_builder()
        builder = type(builder)(
            workspaces=self.workspaces, mind=self.mind, clock=lambda: next(stamps)
        )
        self.activate()
        first = builder.build("same question").to_dict()
        second = builder.build("same question").to_dict()

        def strip(payload):
            payload = dict(payload)
            payload["built_at"] = None
            payload["parts"] = [
                dict(part, observed_at=None) for part in payload["parts"]
            ]
            return payload

        self.assertNotEqual(first["built_at"], second["built_at"])
        self.assertEqual(strip(first), strip(second))


# -- the M2N seam ------------------------------------------------------------


class RetrievalSeam(ContextHarness):
    """One typed parameter, so M2N adds candidates without a redesign."""

    def candidate(self, **kwargs):
        from cofferdam.workstation.context import RetrievedCandidate

        defaults = {
            "source_kind": "memory",
            "source_ref": "project:" + PROJECT_ID + ":decisions#d-1",
            "text": "The oldest decision.",
        }
        defaults.update(kwargs)
        return RetrievedCandidate(**defaults)

    def test_no_candidates_are_supplied_by_this_build(self):
        self.activate()
        pack = self.build()
        from cofferdam.workstation.context import SELECTION_RETRIEVED

        for part in pack.parts:
            self.assertNotEqual(part.selection, SELECTION_RETRIEVED)

    def test_a_supplied_candidate_becomes_a_part_with_its_provenance(self):
        from cofferdam.workstation.context import SELECTION_RETRIEVED

        self.activate()
        pack = self.build(candidates=[self.candidate()])
        part = self.part(pack, "project:" + PROJECT_ID + ":decisions#d-1")
        self.assertEqual(part.selection, SELECTION_RETRIEVED)
        self.assertEqual(part.observed_at, FROZEN)
        self.assertEqual(part.text, "The oldest decision.")

    def test_candidates_are_budgeted_like_everything_else(self):
        from cofferdam.workstation.context import OMIT_BUDGET_EXHAUSTED

        self.activate()
        pack = self.build("a question", budget_bytes=120, candidates=[self.candidate()])
        self.assertLessEqual(pack.budget.consumed, 120)
        omitted = self.omission(pack, "project:" + PROJECT_ID + ":decisions#d-1")
        self.assertEqual(omitted.reason, OMIT_BUDGET_EXHAUSTED)

    def test_a_candidate_may_not_smuggle_a_path_into_provenance(self):
        from cofferdam.workstation.context import SourceRefInvalid

        self.activate()
        for ref in (
            "/home/nrgis/Documents/Cofferdam-Mind/USER.md",
            "project:demo:plan#../../etc/passwd",
            "file:///etc/passwd",
            "project:demo:plan#~root",
        ):
            with self.assertRaises(SourceRefInvalid):
                self.build(candidates=[self.candidate(source_ref=ref)])

    def test_a_candidate_may_not_claim_an_unproducible_kind(self):
        from cofferdam.workstation.context import SourceKindInvalid

        self.activate()
        with self.assertRaises(SourceKindInvalid):
            self.build(candidates=[self.candidate(source_kind="planner_inference")])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
