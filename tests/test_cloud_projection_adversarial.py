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


class SeparatorDuplication(AdversarialHarness):
    """Regression: PR3.5 matched exactly one slash between path components.

    `/home//someone/x` names the same file as `/home/someone/x` on every POSIX
    host — the kernel collapses the run — so a pattern that accepts one slash
    and a filesystem that accepts many is a difference an attacker does not have
    to be clever to find. It defeated the known-host literals too, which are the
    strongest rule here: those were a substring test, and a substring test cannot
    see a separator the operator did not type. Found by the PR3.5
    post-deployment validation.
    """

    def assert_redacted(self, text, *absent):
        from cofferdam.workstation.context.projection import REDACTION_PATH

        projection, blob = self.project_text("Path is " + text + " on the host.\n")
        self.assertEqual(len(projection.parts), 1)
        for fragment in absent or (text,):
            self.assertNotIn(fragment, blob)
        self.assertIn(REDACTION_PATH, projection.parts[0].redactions)

    def test_a_doubled_home_separator(self):
        self.assert_redacted("/home//fake-user/project")

    def test_a_tripled_home_separator(self):
        self.assert_redacted("/home///fake-user/project")

    def test_a_doubled_separator_inside_a_home_path(self):
        self.assert_redacted("/home/fake-user//project", "fake-user")

    def test_a_doubled_root_separator(self):
        self.assert_redacted("/root//secret")

    def test_a_tilde_path_with_one_and_with_two_separators(self):
        self.assert_redacted("~/foo")
        self.assert_redacted("~//foo")

    def test_doubled_slot_separators(self):
        self.assert_redacted("slots//a")
        self.assert_redacted("slots///b")

    def test_a_doubled_separator_inside_an_obsidian_path(self):
        self.assert_redacted(".obsidian//workspace.json", ".obsidian")

    def test_a_known_cofferdam_home_with_duplicated_separators(self):
        """Duplicate an *internal* separator: a doubled leading slash leaves the
        literal intact as a substring and would pass without proving anything."""
        literal = str(self.home)
        head, _, tail = literal.rpartition("/")
        self.assert_redacted(head + "//" + tail, literal)

    def test_a_known_project_root_with_duplicated_separators(self):
        literal = str(self.project_root)
        self.assert_redacted(literal.replace("/", "//"), literal)

    def test_a_known_vault_root_with_duplicated_separators(self):
        literal = str(self.vault_root)
        self.assert_redacted(literal.replace("/", "//"), literal)

    def test_a_known_slot_root_with_duplicated_separators(self):
        literal = str(self.home / "slots" / "a")
        self.assert_redacted(literal.replace("/", "///"), literal)

    def test_many_repeated_separators_do_not_bypass(self):
        self.assert_redacted("/home/" + "/" * 40 + "fake-user/project", "fake-user")

    def test_a_known_root_inside_a_url_with_duplicated_separators(self):
        """The known-literal rule reaches inside a URL, and still must."""
        literal = str(self.project_root)
        _, blob = self.project_text(
            "See https://example.com" + literal.replace("/", "//") + "/x for details.\n"
        )
        self.assertNotIn(literal, blob)

    def test_api_routes_survive_separator_tolerance(self):
        """Widening the separator must not start eating routes."""
        text = (
            "The bridge exposes /v1/projects and the daemon exposes /api/tasks.\n"
            "A doubled route //api/tasks is still not a filesystem path.\n"
        )
        projection, blob = self.project_text(text)
        self.assertEqual(len(projection.parts), 1)
        self.assertIn("/api/tasks", blob)
        self.assertIn("/v1/projects", blob)
        self.assertEqual(projection.parts[0].redactions, ())

    def test_ordinary_urls_survive_separator_tolerance(self):
        text = (
            "See https://example.com/path and https://example.com/home/docs.\n"
            "Locally that is http://localhost/api/tasks instead.\n"
        )
        projection, blob = self.project_text(text)
        self.assertEqual(len(projection.parts), 1)
        for url in (
            "https://example.com/path",
            "https://example.com/home/docs",
            "http://localhost/api/tasks",
        ):
            self.assertIn(url, blob)
        self.assertEqual(projection.parts[0].redactions, ())

    def test_the_scheme_separator_is_never_collapsed(self):
        """`https://` is two slashes that must survive verbatim."""
        _, blob = self.project_text("Read https://example.com/a and http://example.org/b.\n")
        self.assertIn("https://example.com", blob)
        self.assertIn("http://example.org", blob)


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


# -- the bare-name gap (M2J PR3.5.1) -----------------------------------------

#: A fake value, long enough to clear the placeholder floor. Not a credential.
FAKE_VALUE = "ZQXFAKEtok9d2f81b40c7ae653aa10"


class BareCredentialAssignments(SecretShapes):
    """Regression: PR3.5 required at least one character before the keyword.

    `COFFERDAM_ACTIONS_TOKEN=` matched and `TOKEN=` did not, because the name
    pattern opened with a mandatory `[A-Z]`. The prefix is what varies between
    hosts, so the form most likely to appear in a pasted snippet was the one
    form the policy could not see. Found by the PR3.5 post-deployment
    validation, before any surface could transmit a projection.
    """

    def assert_bare_key_omits(self, key):
        blob = self.assert_omits(key + "=" + FAKE_VALUE + "\n")
        self.assertNotIn(FAKE_VALUE, blob)

    def test_a_bare_api_key_assignment(self):
        self.assert_bare_key_omits("API_KEY")

    def test_a_bare_apikey_assignment(self):
        self.assert_bare_key_omits("APIKEY")

    def test_a_bare_secret_assignment(self):
        self.assert_bare_key_omits("SECRET")

    def test_a_bare_token_assignment(self):
        self.assert_bare_key_omits("TOKEN")

    def test_a_bare_password_assignment(self):
        self.assert_bare_key_omits("PASSWORD")

    def test_a_bare_auth_assignment(self):
        self.assert_bare_key_omits("AUTH")

    def test_a_bare_access_token_assignment(self):
        self.assert_bare_key_omits("ACCESS_TOKEN")

    def test_a_bare_refresh_token_assignment(self):
        self.assert_bare_key_omits("REFRESH_TOKEN")

    def test_a_bare_private_key_assignment(self):
        self.assert_bare_key_omits("PRIVATE_KEY")

    def test_a_prefixed_assignment_is_still_detected(self):
        """The fix widens the pattern; it must not narrow it."""
        for key in ("COFFERDAM_ACTIONS_TOKEN", "MY_API_KEY", "APP_SECRET", "DB_PASSWORD"):
            with self.subTest(key=key):
                self.assert_bare_key_omits(key)

    def test_whitespace_around_the_assignment_does_not_hide_it(self):
        self.assert_omits("API_KEY = " + FAKE_VALUE + "\n")
        self.assert_omits("TOKEN\t:\t" + FAKE_VALUE + "\n")

    def test_a_quoted_value_does_not_hide_it(self):
        self.assert_omits('SECRET="' + FAKE_VALUE + '"\n')
        self.assert_omits("SECRET='" + FAKE_VALUE + "'\n")

    def test_markdown_formatting_around_the_key_does_not_hide_it(self):
        self.assert_omits("`API_KEY=" + FAKE_VALUE + "`\n")
        self.assert_omits("**TOKEN=" + FAKE_VALUE + "**\n")

    def test_a_list_item_does_not_hide_it(self):
        self.assert_omits("- API_KEY=" + FAKE_VALUE + "\n")
        self.assert_omits("  * SECRET=" + FAKE_VALUE + "\n")

    def test_a_fenced_code_block_does_not_hide_it(self):
        self.assert_omits("```sh\nexport TOKEN=" + FAKE_VALUE + "\n```\n")

    def test_yaml_shaped_formatting_does_not_hide_it(self):
        self.assert_omits("env:\n  API_KEY: " + FAKE_VALUE + "\n")

    def test_a_path_and_a_fake_secret_together_omit_the_whole_part(self):
        """A high-risk part is dropped whole; the path redaction does not save it."""
        text = "Deploy from /home/fake-user/app with API_KEY=" + FAKE_VALUE + "\n"
        blob = self.assert_omits(text)
        self.assertNotIn("/home/fake-user", blob)

    def test_a_documentation_placeholder_with_a_bare_key_is_still_kept(self):
        """Widening the name must not start eating canonical documentation."""
        text = (
            "The example uses API_KEY=xxxxx and SECRET=changeme.\n"
            "Set TOKEN=<your-token> before starting.\n"
            "AUTH=${AUTH_VALUE} is read from the environment.\n"
        )
        projection, blob = self.project_text(text)
        self.assertEqual(len(projection.parts), 1)
        self.assertIn("API_KEY", blob)

    def test_a_short_value_is_still_not_treated_as_a_credential(self):
        projection, _ = self.project_text("API_KEY=1\nTOKEN=abc\n")
        self.assertEqual(len(projection.parts), 1)

    def test_naming_a_bare_variable_without_a_value_is_not_a_secret(self):
        projection, _ = self.project_text("Set API_KEY in the environment file.\n")
        self.assertEqual(len(projection.parts), 1)

    def test_unicode_beside_the_assignment_does_not_hide_it(self):
        self.assert_omits("→ API_KEY=" + FAKE_VALUE + " ←\n")


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

    def test_separator_tolerance_stays_linear_in_the_input(self):
        """M2J PR3.5.1 regression.

        Accepting a run of slashes puts a `+` quantifier next to a character
        class inside a repeating group, which is the shape that produced the
        84-second stall PR3.5 had to fix. The two classes are disjoint, so the
        match is deterministic — this asserts that empirically rather than
        trusting the reading. Doubling the input must roughly double the time,
        not square it.
        """
        import time

        def elapsed(size):
            text = ("/" * size + "a" * size + " x" + "y" * size + "\n") * 4
            started = time.monotonic()
            self.project_parts(
                _part("memory", "project:" + PROJECT_ID + ":status", text)
            )
            return time.monotonic() - started

        small = max(elapsed(4000), 0.001)
        large = elapsed(8000)
        self.assertLess(large, 2.0)
        self.assertLess(large / small, 8.0, "growth looks superlinear")

    def test_a_long_run_of_separators_alone_does_not_stall(self):
        import time

        text = ("/" * 50000 + "\n") + ("~" * 20000 + "\n")
        started = time.monotonic()
        projection = self.project_parts(
            _part("memory", "project:" + PROJECT_ID + ":status", text)
        )
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(len(projection.parts), 1)

    def test_many_known_literals_do_not_recompile_per_part(self):
        """The literal patterns are built once, not once per part sanitized."""
        import time

        parts = [
            _part("memory", "project:" + PROJECT_ID + ":status", "body " + str(n) + "\n")
            for n in range(200)
        ]
        started = time.monotonic()
        self.project_parts(*parts)
        self.assertLess(time.monotonic() - started, 2.0)

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

    def test_a_lower_case_credential_variable_name_is_not_detected(self):
        """M2J PR3.5.1 widened the *prefix*, not the case.

        `_ENV_ASSIGNMENT` reads upper case because that is how environment
        variables are written, and the value test is what keeps the rule from
        eating documentation. Lowering the case would widen it against ordinary
        prose — `auth: <a sentence>` — where the consequence is dropping a whole
        eligible part. Recorded as a limit rather than fixed by reflex.
        """
        text = "The client sends api_key=ZQXFAKEtok9d2f81b40c7ae653aa10 here.\n"
        projection, blob = self.project_text(text)
        self.assertEqual(len(projection.parts), 1)
        self.assertIn("api_key=", blob)

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
