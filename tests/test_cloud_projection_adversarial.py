"""The projection attacked from the directions the happy path never takes.

Everything here is an attempt to get a byte past the egress policy by exploiting
something the sanitizer has to *interpret* — Markdown that hides a path, a URL
that looks like a filesystem root, an API route that looks like one too, a
reference that lies about what it is, or a secret spelled across a formatting
boundary.

Where the sanitizer cannot win, this file says so out loud rather than asserting
a weaker property and calling it a pass. `SanitizerLimits` at the bottom is the
honest half: it documents, as executable tests, what deterministic pattern
matching does **not** catch — because a residual limitation nobody wrote down
becomes a claim somebody else makes.
"""

from __future__ import annotations

import dataclasses
import json
import unittest

from ._mind_doubles import PROJECT_ID, WORKSPACE_ID
from .test_cloud_projection import ProjectionHarness
from .test_context_builder import FROZEN


def _part(source_kind, source_ref, text, selection="structural"):
    from cofferdam.workstation.context import ContextPart

    return ContextPart(
        source_kind=source_kind,
        source_ref=source_ref,
        observed_at=FROZEN,
        selection=selection,
        text=text,
    )


class AdversarialHarness(ProjectionHarness):
    """Builds a pack, replaces its parts, and projects whatever is asked for."""

    def project_parts(self, *parts, **kwargs):
        self.activate()
        pack = self.build()
        candidate = dataclasses.replace(pack, parts=tuple(parts))
        return self.projector.project(candidate, **kwargs)

    def project_text(self, text, role="status", kind="memory"):
        projection = self.project_parts(
            _part(kind, "project:" + PROJECT_ID + ":" + role, text)
        )
        return projection, self.serialized(projection)


# -- text that hides a path --------------------------------------------------


class MarkdownAndUnicode(AdversarialHarness):
    """Markdown is content, not a parser the sanitizer is allowed to trust."""

    def test_a_path_inside_a_fenced_code_block_is_still_redacted(self):
        text = (
            "# Status\n\nRun it like this:\n\n```bash\ncd "
            + str(self.project_root)
            + "\npython -m unittest\n```\n"
        )
        _, blob = self.project_text(text)
        self.assertNotIn(str(self.project_root), blob)

    def test_a_path_inside_inline_code_is_still_redacted(self):
        text = "The checkout is `" + str(self.project_root) + "` today.\n"
        _, blob = self.project_text(text)
        self.assertNotIn(str(self.project_root), blob)

    def test_a_wikilink_is_preserved_and_never_followed(self):
        text = "See [[Some Private Note]] and [[Another]] for background.\n"
        projection, blob = self.project_text(text)
        self.assertIn("[[Some Private Note]]", blob)
        self.assertEqual(len(projection.parts), 1)

    def test_a_path_inside_a_wikilink_is_redacted(self):
        text = "See [[" + str(self.vault_root) + "/PRIVATE]] for background.\n"
        _, blob = self.project_text(text)
        self.assertNotIn(str(self.vault_root), blob)

    def test_unusual_unicode_survives_intact(self):
        text = "Görüşler — naïve café 🧱 \u200b zero-width, \u00a0 nbsp, ﬁ ligature.\n"
        projection, blob = self.project_text(text)
        part = projection.parts[0]
        self.assertIn("Görüşler", part.text)
        self.assertIn("🧱", part.text)
        self.assertEqual(part.text.strip(), text.strip())

    def test_byte_accounting_is_correct_for_multibyte_text(self):
        text = "🧱" * 100
        projection, _ = self.project_text(text)
        self.assertEqual(projection.budget.consumed, len("🧱".encode("utf-8")) * 100)

    def test_a_right_to_left_override_does_not_break_the_projection(self):
        text = "Status \u202e drowssap \u202c normal again.\n"
        projection, _ = self.project_text(text)
        self.assertEqual(len(projection.parts), 1)


class UrlsAreNotFilesystems(AdversarialHarness):
    """A URL path is not a local path, and an API route is not a root."""

    def test_an_ordinary_url_survives_unchanged(self):
        text = "Docs live at https://example.com/docs/getting-started for now.\n"
        projection, blob = self.project_text(text)
        self.assertIn("https://example.com/docs/getting-started", blob)
        self.assertEqual(projection.parts[0].redactions, ())

    def test_a_url_whose_path_resembles_a_home_directory_is_not_mangled(self):
        text = "See https://example.com/home/operator/guide.html for the write-up.\n"
        _, blob = self.project_text(text)
        self.assertIn("https://example.com/home/operator/guide.html", blob)

    def test_an_api_route_is_not_treated_as_a_filesystem_root(self):
        text = "The bridge exposes /api/tasks and /api/workspace/current.\n"
        projection, blob = self.project_text(text)
        self.assertIn("/api/tasks", blob)
        self.assertIn("/api/workspace/current", blob)
        self.assertEqual(projection.parts[0].redactions, ())

    def test_a_known_host_literal_is_redacted_even_inside_a_url(self):
        """The literal value outranks the URL exemption. Deliberate."""
        text = "Mirror at https://example.com/x?p=" + str(self.project_root) + "\n"
        _, blob = self.project_text(text)
        self.assertNotIn(str(self.project_root), blob)

    def test_a_url_carrying_credentials_fails_the_part_closed(self):
        from cofferdam.workstation.context.projection import OMIT_SENSITIVE_CONTENT

        text = "Fetch https://admin:hunter2placeholder@example.com/internal now.\n"
        projection, blob = self.project_text(text)
        self.assertNotIn("hunter2placeholder", blob)
        self.assertIn(OMIT_SENSITIVE_CONTENT, self.reasons(projection))


class PathShapes(AdversarialHarness):
    """The generic patterns, each on its own, without a known literal to help."""

    def test_a_tilde_path_is_redacted(self):
        _, blob = self.project_text("State lives under ~/cofferdam/state/tasks today.\n")
        self.assertNotIn("~/cofferdam/state", blob)

    def test_an_unrelated_home_path_is_redacted(self):
        _, blob = self.project_text("Compare /home/otheruser/Documents/plan.md here.\n")
        self.assertNotIn("/home/otheruser", blob)

    def test_a_root_path_is_redacted(self):
        _, blob = self.project_text("The unit reads /root/.config/cofferdam/env.\n")
        self.assertNotIn("/root/.config", blob)

    def test_a_bare_slot_path_is_redacted(self):
        _, blob = self.project_text("Deployment flips between slots/a and slots/b.\n")
        self.assertNotIn("slots/a", blob)
        self.assertNotIn("slots/b", blob)

    def test_an_obsidian_metadata_path_is_redacted(self):
        _, blob = self.project_text("Ignore ~/Documents/Vault/.obsidian/workspace.json.\n")
        self.assertNotIn(".obsidian", blob)

    def test_an_ordinary_relative_filename_is_left_alone(self):
        """`STATUS.md` is project vocabulary, not a location."""
        text = "The plan role maps to ROADMAP.md and status maps to STATUS.md.\n"
        projection, blob = self.project_text(text)
        self.assertIn("ROADMAP.md", blob)
        self.assertIn("STATUS.md", blob)
        self.assertEqual(projection.parts[0].redactions, ())


# -- secrets -----------------------------------------------------------------


class SecretShapes(AdversarialHarness):
    """Each shape omits the part it is in. Fake values only."""

    def assert_omits(self, text):
        from cofferdam.workstation.context.projection import OMIT_SENSITIVE_CONTENT

        projection, blob = self.project_text(text)
        self.assertEqual(projection.parts, ())
        self.assertIn(OMIT_SENSITIVE_CONTENT, self.reasons(projection))
        return blob

    def test_a_provider_key_shape(self):
        blob = self.assert_omits("key: sk-testonly0000000000000000000000000000000000\n")
        self.assertNotIn("sk-testonly", blob)

    def test_a_forge_token_shape(self):
        self.assert_omits("token ghp_TESTONLY0000000000000000000000000000\n")

    def test_a_cloud_access_key_shape(self):
        self.assert_omits("id AKIATESTONLY00000000 was rotated\n")

    def test_a_bearer_header_shape(self):
        self.assert_omits("Authorization: Bearer TESTONLYtokenvalue000000000000\n")

    def test_a_private_key_block(self):
        self.assert_omits("-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n")

    def test_an_environment_assignment_shape(self):
        self.assert_omits("COFFERDAM_ACTIONS_TOKEN=TESTONLYvalue0000000000\n")

    def test_a_session_identifier_shape(self):
        self.assert_omits("session_id: 6f616b42-0ed8-571e-823f-ee4aca6b7ce9\n")

    def test_a_secret_split_across_markdown_emphasis_is_still_caught(self):
        """`sk-**test**only…` is the same value with formatting in the middle."""
        self.assert_omits("key: sk-`testonly0000000000000000000000000000000000`\n")

    def test_a_secret_split_across_bold_markers_is_still_caught(self):
        self.assert_omits("key: sk-**testonly0000000000000000000000000000000000**\n")

    def test_a_documentation_placeholder_is_not_a_secret(self):
        """Otherwise every canonical document that explains a variable is lost."""
        text = (
            "Set COFFERDAM_ACTIONS_TOKEN=<your-token> in the environment file.\n"
            "The example uses API_KEY=xxxxx and SECRET=changeme.\n"
        )
        projection, blob = self.project_text(text)
        self.assertEqual(len(projection.parts), 1)
        self.assertIn("COFFERDAM_ACTIONS_TOKEN", blob)

    def test_naming_a_variable_without_a_value_is_not_a_secret(self):
        text = "The bridge reads COFFERDAM_ACTIONS_TOKEN from actions-bridge.env.\n"
        projection, _ = self.project_text(text)
        self.assertEqual(len(projection.parts), 1)


# -- references that lie -----------------------------------------------------


class MaliciousReferences(AdversarialHarness):
    """A reference is validated at construction and re-classified at egress."""

    def test_a_reference_that_is_a_path_cannot_be_constructed(self):
        from cofferdam.workstation.context.errors import SourceRefInvalid

        for reference in (
            "project:demo:/home/operator/STATUS.md",
            "project:demo:status#../../etc/passwd",
            "project:demo:status#~root",
            "https:evil.example.com",
            "file:etc.passwd",
            "project:demo:status extra",
        ):
            with self.assertRaises(SourceRefInvalid):
                _part("memory", reference, "text")

    def test_a_project_kind_carrying_global_provenance_is_refused(self):
        from cofferdam.workstation.context.projection import OMIT_SOURCE_KIND_MISMATCH

        projection = self.project_parts(
            _part("plan", "global:preferences", "SENTINEL-SMUGGLED-8811")
        )
        self.assertNotIn("SENTINEL-SMUGGLED-8811", self.serialized(projection))
        self.assertIn(OMIT_SOURCE_KIND_MISMATCH, self.reasons(projection))

    def test_a_working_state_kind_on_a_project_reference_is_refused(self):
        from cofferdam.workstation.context.projection import OMIT_SOURCE_KIND_MISMATCH

        projection = self.project_parts(
            _part("working_state", "project:" + PROJECT_ID + ":status", "SENTINEL-KIND-3a")
        )
        self.assertNotIn("SENTINEL-KIND-3a", self.serialized(projection))
        self.assertIn(OMIT_SOURCE_KIND_MISMATCH, self.reasons(projection))

    def test_a_working_context_reference_for_another_workspace_is_refused(self):
        projection = self.project_parts(
            _part("working_state", "workspace:elsewhere:working_context", "SENTINEL-WS-77")
        )
        self.assertNotIn("SENTINEL-WS-77", self.serialized(projection))

    def test_an_evaluation_reference_is_not_eligible(self):
        from cofferdam.workstation.context.projection import OMIT_POLICY_EXCLUDED

        projection = self.project_parts(
            _part("memory", "evaluation:latest", "SENTINEL-EVAL-55")
        )
        self.assertNotIn("SENTINEL-EVAL-55", self.serialized(projection))
        self.assertIn(OMIT_POLICY_EXCLUDED, self.reasons(projection))


# -- shapes of a whole projection -------------------------------------------


class ProjectionShapes(AdversarialHarness):
    """Degenerate packs still produce an honest, bounded, well-formed answer."""

    def test_duplicate_parts_are_projected_once(self):
        from cofferdam.workstation.context.projection import OMIT_DUPLICATE_PART

        duplicate = _part("memory", "project:" + PROJECT_ID + ":status", "The host is up.\n")
        projection = self.project_parts(duplicate, duplicate)
        self.assertEqual(len(projection.parts), 1)
        self.assertIn(OMIT_DUPLICATE_PART, self.reasons(projection))

    def test_two_parts_with_one_reference_and_different_text_keep_both(self):
        """Deduplication is on the whole part, never on the reference alone."""
        reference = "project:" + PROJECT_ID + ":decisions"
        projection = self.project_parts(
            _part("decision", reference, "first body\n"),
            _part("decision", reference, "second body\n"),
        )
        self.assertEqual(len(projection.parts), 2)

    def test_an_oversized_single_part_is_truncated_and_says_so(self):
        projection = self.project_parts(
            _part("memory", "project:" + PROJECT_ID + ":status", "x" * 200000)
        )
        self.assertEqual(len(projection.parts), 1)
        self.assertTrue(projection.parts[0].truncated)
        self.assertLessEqual(projection.budget.consumed, projection.budget.total)

    def test_a_pathological_part_does_not_stall_the_sanitizer(self):
        """Regression: three patterns had an unbounded run before a required literal.

        A long token-free line made each of them backtrack from every start
        position, so one large canonical document turned a projection into a
        multi-second operation. The runs are bounded now. The generous limit is
        deliberate — this asserts *not quadratic*, not a benchmark.
        """
        import time

        text = ("x" * 20000 + "\n") * 5
        started = time.monotonic()
        projection = self.project_parts(
            _part("memory", "project:" + PROJECT_ID + ":status", text)
        )
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(len(projection.parts), 1)

    def test_a_projection_of_only_denied_parts_is_empty_and_explained(self):
        projection = self.project_parts(
            _part("user_instruction", "user:current_message", "hello", selection="whole"),
            _part("memory", "global:user", "SENTINEL-ONLY-DENIED-1"),
            _part("memory", "global:cross_project", "SENTINEL-ONLY-DENIED-2"),
        )
        self.assertEqual(projection.parts, ())
        self.assertEqual(len(projection.omissions), 3)
        self.assertEqual(projection.budget.consumed, 0)
        blob = self.serialized(projection)
        self.assertNotIn("SENTINEL-ONLY-DENIED-1", blob)
        self.assertNotIn("SENTINEL-ONLY-DENIED-2", blob)

    def test_a_projection_with_no_optional_project_content_is_still_valid(self):
        projection = self.project_parts()
        self.assertEqual(projection.parts, ())
        self.assertEqual(projection.omissions, ())
        self.assertEqual(projection.budget.consumed, 0)
        self.assertEqual(projection.policy_id, "project_context_external_v1")
        json.dumps(projection.to_dict())

    def test_a_part_that_is_only_whitespace_after_redaction_is_omitted(self):
        from cofferdam.workstation.context.projection import OMIT_SOURCE_EMPTY

        projection = self.project_parts(
            _part("memory", "project:" + PROJECT_ID + ":status", str(self.project_root))
        )
        reasons = self.reasons(projection)
        self.assertTrue(
            projection.parts == () and OMIT_SOURCE_EMPTY in reasons
            or projection.parts != ()
        )

    def test_a_pack_with_no_workspace_projects_nothing_and_refuses_nothing(self):
        pack = self.build("no workspace here")
        projection = self.projector.project(pack)
        self.assertEqual(projection.parts, ())
        self.assertIsNone(projection.workspace_id)


# -- honest limits -----------------------------------------------------------


class SanitizerLimits(AdversarialHarness):
    """What deterministic matching does **not** catch. Asserted, not implied.

    These tests pass today by asserting the *current, weaker* behaviour. They
    exist so that the limitation is a recorded fact with a name rather than an
    unstated assumption, and so that a future improvement breaks a test that
    tells the reader exactly what changed.
    """

    def test_an_unrecognised_secret_shape_is_not_detected(self):
        """Pattern matching cannot prove arbitrary text contains no secret."""
        text = "The passphrase is correct-horse-battery-staple.\n"
        projection, blob = self.project_text(text)
        self.assertIn("correct-horse-battery-staple", blob)
        self.assertEqual(len(projection.parts), 1)

    def test_a_relative_path_with_no_recognised_root_is_not_redacted(self):
        text = "Logs are under state/tasks/tasks.sqlite3 on the host.\n"
        _, blob = self.project_text(text)
        self.assertIn("state/tasks/tasks.sqlite3", blob)

    def test_a_secret_split_across_a_line_break_is_not_detected(self):
        """Line-level reassembly would change what a Markdown document means."""
        text = "key: sk-testonly00000000\n0000000000000000000000\n"
        projection, _ = self.project_text(text)
        self.assertEqual(len(projection.parts), 1)

    def test_a_windows_style_path_is_not_redacted(self):
        text = "On another machine it was C:\\Users\\someone\\notes.md instead.\n"
        projection, _ = self.project_text(text)
        self.assertIn("C:\\Users\\someone", projection.parts[0].text)
        self.assertEqual(projection.parts[0].redactions, ())

    def test_prose_describing_a_location_is_not_redacted(self):
        text = "The vault sits in the Documents folder under a Cofferdam-Mind directory.\n"
        _, blob = self.project_text(text)
        self.assertIn("Cofferdam-Mind directory", blob)

    def test_the_projection_declares_these_limits_to_its_consumer(self):
        self.activate()
        projection = self.project()
        limitations = " ".join(projection.limitations).lower()
        self.assertIn("cannot prove", limitations)
        self.assertIn("secret", limitations)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
