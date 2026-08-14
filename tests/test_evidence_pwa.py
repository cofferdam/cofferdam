"""M2K PR2 — the read-only evidence panel.

Two kinds of test, as the Tasks panel already does it:

* **Behavioural**, through ``tests/tasks_harness.js`` against the shipped
  ``web/tasks.js``. What a person actually reads off the screen.
* **Structural**, scanning the shipped file with comments stripped, for what the
  panel *cannot* say. The forbidden-vocabulary scan is the important one and it
  runs against code only, so the paragraph explaining why PASS is banned does
  not itself trip the ban.

The vocabulary is the point. "Path agreed" is the strongest claim the evidence
supports; PASS, FAIL, SUCCESS, TRUSTED, LYING, a confidence and a risk level are
none of them supported by anything the bundle contains, and a phone screen is
exactly where an unsupported word turns into somebody's decision.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from ._runtime_doubles import code_only

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"
TASKS_HARNESS = REPO_ROOT / "tests" / "tasks_harness.js"


def tasks_code() -> str:
    return code_only((WEB_DIR / "tasks.js").read_text(encoding="utf-8"))


def panel(name: str) -> dict:
    node = shutil.which("node")
    if not node:  # pragma: no cover - depends on the runner
        raise unittest.SkipTest("node is not installed")
    completed = subprocess.run(
        [node, str(TASKS_HARNESS), name],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if completed.returncode != 0:  # pragma: no cover - harness failure
        raise AssertionError("harness failed: " + completed.stderr[:800])
    payload = json.loads(completed.stdout)
    assert not payload.get("uncaught"), payload.get("uncaught")
    assert not payload.get("timerErrors"), payload.get("timerErrors")
    assert not payload.get("error"), payload.get("error")
    return payload


class SectionsAreDistinct(unittest.TestCase):
    """Claims, observations and relationships are three headings, not one list."""

    def setUp(self):
        self.html = panel("evidence-shows-all-three-relationships")["html"]

    def test_the_three_sections_are_present_and_separate(self):
        for heading in (
            "Worker claims",
            "Machine observations",
            "Relationships and gaps",
            "Claim ingestion",
        ):
            self.assertIn(heading, self.html, heading)

    def test_claims_come_before_observations(self):
        self.assertLess(
            self.html.index("Worker claims"), self.html.index("Machine observations")
        )

    def test_a_claim_is_labelled_as_the_adapter_s_word(self):
        self.assertIn("reported by the adapter, not verified", self.html)

    def test_an_observation_is_labelled_as_something_cofferdam_ran(self):
        self.assertIn("Cofferdam ran git status", self.html)

    def test_turn_attribution_is_stated(self):
        self.assertIn("Evidence — turn 1", self.html)
        self.assertIn("known exactly", self.html)


class RelationshipVocabulary(unittest.TestCase):
    def setUp(self):
        self.html = panel("evidence-shows-all-three-relationships")["html"]

    def test_all_three_relationships_render_with_their_own_words(self):
        self.assertIn("Path agreed", self.html)
        self.assertIn("Claim only", self.html)
        self.assertIn("Observed only", self.html)

    def test_every_group_carries_an_explicit_operation_statement(self):
        """One of the three answers, printed for every group without exception.

        Before M2K PR3 this asserted the literal "Operation not established" on
        all three, because that was the only answer the evidence could support.
        PR3 gives the machine side real semantics, so a group may now say the
        operation agreed or differed — but the property that matters is
        unchanged and is what is asserted here: **the question is never left
        unanswered on screen.** A group with no operation line would invite a
        reader to supply their own answer.
        """
        # The list item, not the `task-evidence-groups` container, whose class
        # name contains the same substring.
        groups = self.html.count('<li class="task-evidence-group">')
        stated = sum(
            self.html.count(phrase)
            for phrase in (
                "Operation agreed",
                "Operation differs",
                "Operation not established",
            )
        )
        self.assertEqual(groups, 3)
        self.assertEqual(stated, groups)

    def test_path_agreed_is_never_shortened_to_agreed(self):
        self.assertNotIn(">Agreed<", self.html)
        self.assertNotIn("Verified", self.html)
        self.assertNotIn("Confirmed", self.html)

    def test_claim_only_is_not_rendered_as_a_failure(self):
        self.assertNotIn("Missing", self.html)
        self.assertNotIn("Not done", self.html)
        self.assertNotIn("Unfulfilled", self.html)

    def test_observed_only_is_not_rendered_as_dishonesty(self):
        self.assertNotIn("Undeclared", self.html)
        self.assertNotIn("Concealed", self.html)
        self.assertNotIn("Hidden", self.html)


class CompletenessLanguage(unittest.TestCase):
    def test_an_incomplete_claim_set_says_so(self):
        html = panel("evidence-shows-all-three-relationships")["html"]
        self.assertIn("Claim set incomplete", html)
        self.assertIn("2 of 3 stored", html)

    def test_a_missing_ingestion_record_is_not_called_complete(self):
        html = panel("evidence-missing-ingestion")["html"]
        self.assertIn("No claim report was recorded for this turn", html)
        self.assertNotIn("Every reported claim was stored", html)

    def test_a_legacy_turn_explains_itself(self):
        html = panel("evidence-legacy-turn")["html"]
        self.assertIn("Legacy turn attribution unavailable", html)
        self.assertIn("No machine observations for this turn", html)

    def test_a_legacy_turns_missing_observations_are_not_read_as_evidence(self):
        """The sentence that stops an absence being read as a finding."""
        html = panel("evidence-legacy-turn")["html"]
        self.assertIn("is not evidence about the work", html)

    def test_a_clean_tree_is_reported_as_a_look_not_as_nothing(self):
        html = panel("evidence-clean-tree")["html"]
        self.assertIn("Cofferdam looked and the working tree was clean", html)


class ReadOnlyBehaviour(unittest.TestCase):
    def test_evidence_is_not_fetched_until_somebody_asks(self):
        result = panel("evidence-is-not-fetched-until-asked")
        self.assertEqual(result["requests"], 0)
        self.assertNotIn("Worker claims", result["html"])

    def test_three_taps_send_one_request(self):
        result = panel("evidence-double-tap-sends-one-request")
        self.assertEqual(result["before"], 0)
        self.assertEqual(result["after"], 1)

    def test_a_refusal_is_shown_as_a_refusal(self):
        html = panel("evidence-refusal-is-not-success")["html"]
        self.assertNotIn("Worker claims", html)
        self.assertIn("media-note err", html)

    def test_the_request_is_a_turn_qualified_get(self):
        requests = panel("evidence-shows-all-three-relationships")["requests"]
        evidence = [r for r in requests if "/evidence" in r["path"]]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["method"], "GET")
        self.assertIsNone(evidence[0]["body"])
        self.assertIn("/turns/1/evidence", evidence[0]["path"])


class ForbiddenVocabulary(unittest.TestCase):
    """Judgement words the evidence cannot support, banned in shipped code."""

    FORBIDDEN = (
        "PASS",
        "FAIL",
        "SUCCESS",
        "TRUSTED",
        "LYING",
        "confidence",
        "risk_level",
        "risk level",
        "Verdict",
        "verdict",
        "Score",
        "Trustworthy",
        "Dishonest",
    )

    def test_the_panel_ships_none_of_them(self):
        code = tasks_code()
        for word in self.FORBIDDEN:
            self.assertNotIn(word, code, word)

    def test_no_rendered_evidence_screen_contains_them(self):
        for scenario in (
            "evidence-shows-all-three-relationships",
            "evidence-legacy-turn",
            "evidence-missing-ingestion",
            "evidence-clean-tree",
        ):
            html = panel(scenario)["html"]
            for word in self.FORBIDDEN:
                self.assertNotIn(word, html, scenario + ": " + word)


class NoMutationControls(unittest.TestCase):
    """Nothing in the evidence section acts on anything."""

    def test_the_section_offers_no_control_but_the_read_itself(self):
        html = panel("evidence-shows-all-three-relationships")["html"]
        start = html.index("task-evidence-detail")
        end = html.index("task-actions", start)
        section = html[start:end]
        for control in ("<button", "<input", "<form", "<textarea", "<select"):
            self.assertNotIn(control, section, control)

    def test_the_panel_defines_no_evidence_write(self):
        code = tasks_code()
        for forbidden in (
            "evidence/approve",
            "evidence/dismiss",
            "evidence/verify",
            "mark_verified",
            "/evidence\", { method",
        ):
            self.assertNotIn(forbidden, code, forbidden)

    def test_the_only_evidence_request_is_a_get(self):
        code = tasks_code()
        index = code.index("/evidence")
        window = code[max(0, index - 400) : index + 200]
        self.assertNotIn("method:", window)
        self.assertNotIn("body:", window)


class MachineOperationLanguage(unittest.TestCase):
    """M2K PR3: the panel can now say what was observed, in neutral verbs."""

    def test_an_agreeing_operation_says_so(self):
        html = panel("evidence-shows-all-three-relationships")["html"]
        self.assertIn("Operation agreed", html)
        self.assertIn("Machine observed: modified", html)

    def test_a_differing_operation_is_a_records_disagreement_not_a_failure(self):
        html = panel("evidence-operation-differs")["html"]
        self.assertIn("Operation differs", html)
        self.assertIn("Records differ", html)
        self.assertIn("Machine observed: deleted", html)
        # The wording that keeps it evidence rather than judgement.
        self.assertIn("Both records are kept as they were", html)
        for forbidden in ("FAIL", "failed", "wrong", "lied", "violation", "error"):
            self.assertNotIn(forbidden, html, forbidden)

    def test_a_conflict_is_not_styled_as_an_error(self):
        """`warn` — something to look at. `err` would read as a failure."""
        html = panel("evidence-operation-differs")["html"]
        start = html.index("task-evidence-detail")
        end = html.index("task-actions", start)
        section = html[start:end]
        self.assertIn("badge warn", section)
        self.assertNotIn("badge err", section)

    def test_a_rename_shows_both_paths_in_order(self):
        html = panel("evidence-rename-observed")["html"]
        self.assertIn("src/old.py", html)
        self.assertIn("src/new.py", html)
        self.assertIn("Machine observed: renamed", html)
        self.assertLess(html.index("src/old.py"), html.index("→"))

    def test_an_incomplete_machine_set_says_a_gap_may_not_be_a_gap(self):
        html = panel("evidence-machine-incomplete")["html"]
        self.assertIn("only some of the changes Git", html)
        self.assertIn("may simply not have been looked at", html)

    def test_a_composite_status_is_shown_beside_the_primary_word(self):
        """`RM` proves renamed AND modified; the word alone would hide half."""
        html = panel("evidence-rename-observed")["html"]
        self.assertIn("[RM]", html)
        self.assertIn("Machine observed: renamed", html)

    def test_the_panel_reports_assembler_v2(self):
        html = panel("evidence-shows-all-three-relationships")["html"]
        self.assertIn("Assembler v2", html)

    def test_no_rendered_pr3_screen_carries_judgement_language(self):
        for scenario in (
            "evidence-operation-differs",
            "evidence-rename-observed",
            "evidence-machine-incomplete",
        ):
            html = panel(scenario)["html"]
            for word in ForbiddenVocabulary.FORBIDDEN:
                self.assertNotIn(word, html, scenario + ": " + word)


class NoLeakedInternals(unittest.TestCase):
    def test_the_panel_never_renders_an_artifact_body(self):
        html = panel("evidence-shows-all-three-relationships")["html"]
        self.assertNotIn("preview", html)
        self.assertNotIn("digest", html)

    def test_the_panel_never_renders_a_provider_session_id(self):
        html = panel("evidence-shows-all-three-relationships")["html"]
        self.assertNotIn("provider_session_id", html)

    def test_the_panel_does_not_state_the_generated_at_as_bundle_content(self):
        """Presentation metadata stays on the envelope and is not copied in."""
        code = tasks_code()
        self.assertNotIn("generated_at", code)

    def test_no_host_path_is_rendered(self):
        html = panel("evidence-shows-all-three-relationships")["html"]
        self.assertNotIn("/home/", html)
        self.assertNotIn("/tmp/", html)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
