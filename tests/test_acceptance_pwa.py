"""M2K PR22 — what the acceptance section says, and the words it must never say.

Behavioural, run through ``tests/tasks_harness.js`` against the shipped
``web/tasks.js`` — so these assert what a person actually sees.

Two requirements carry this file.

**Nothing may read as a global verdict.** The aggregate answers *acceptance at
this turn, over the requirements active at this turn*. A screen is exactly where
"met" becomes "the task passed", so every word here is scoped to the turn's
requirements and there is no PASS, FAIL, SUCCESS or "task" anywhere in the
section.

**`not_assessable` is not a fourth outcome.** It is the absence of one. Rendered
beside met/not-met/incomplete as though it ranked with them, it would tell
somebody their work fell short when what actually happened is that Cofferdam
could not work out what was required.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"
TASKS_HARNESS = REPO_ROOT / "tests" / "tasks_harness.js"

from .test_tasks_pwa import code_only  # noqa: E402  (shared source-scrubber)


def panel(name: str) -> dict:
    node = shutil.which("node")
    if not node:  # pragma: no cover - depends on the runner
        raise unittest.SkipTest("node is not installed")
    completed = subprocess.run(
        [node, str(TASKS_HARNESS), name],
        capture_output=True, text=True, timeout=90, check=False,
    )
    if completed.returncode != 0:  # pragma: no cover - harness failure
        raise AssertionError("harness failed: " + completed.stderr[:800])
    payload = json.loads(completed.stdout)
    assert not payload.get("uncaught"), payload.get("uncaught")
    assert not payload.get("timerErrors"), payload.get("timerErrors")
    return payload


def tasks_code() -> str:
    return code_only((WEB_DIR / "tasks.js").read_text(encoding="utf-8"))


def acceptance_section(html: str) -> str:
    """Exactly the acceptance block, bounded at both ends.

    Slicing to the end of the document would sweep in the rest of the panel —
    including controls that belong to other sections — and quietly turn a
    "no controls here" assertion into one that can never fail for the right
    reason. The block ends with its audit disclosure.
    """
    start = html.index("task-acceptance")
    end = html.index("</details>", start) + len("</details>")
    return html[start:end]


def acceptance_source() -> str:
    """Just the panel function, for the same reason."""
    code = tasks_code()
    start = code.index("function acceptanceBlock")
    end = code.index("function assessmentBlock", start)
    return code[start:end]


class OutcomeWording(unittest.TestCase):
    """Scoped to this turn's requirements, never to the task."""

    def test_met_is_scoped_to_requirements_at_this_turn(self):
        html = panel("acceptance-met")["html"]
        self.assertIn("Requirements met at this turn", html)

    def test_met_never_reads_as_a_passed_task(self):
        html = panel("acceptance-met")["html"].lower()
        for forbidden in ("task passed", "task succeeded", "passed", "success",
                          "all good", "ready to merge", "ready to deploy"):
            self.assertNotIn(forbidden, html, forbidden)

    def test_not_met_names_a_requirement_rather_than_the_work(self):
        html = panel("acceptance-not-met")["html"]
        self.assertIn("A requirement is not met at this turn", html)
        self.assertNotIn("failed", html.lower())

    def test_incomplete_is_about_the_assessment_not_the_work(self):
        html = panel("acceptance-incomplete-needs-human")["html"]
        self.assertIn("Requirement assessment incomplete", html)

    def test_the_three_outcomes_are_materially_different_words(self):
        met = panel("acceptance-met")["html"]
        not_met = panel("acceptance-not-met")["html"]
        incomplete = panel("acceptance-incomplete-needs-human")["html"]
        self.assertNotEqual(met, not_met)
        self.assertNotEqual(not_met, incomplete)
        self.assertNotEqual(met, incomplete)

    def test_incomplete_is_not_styled_as_an_error(self):
        """An evidence limitation is not an accusation, here as everywhere."""
        section = acceptance_section(panel("acceptance-incomplete-needs-human")["html"])
        self.assertNotIn("err", section[: section.index("Acceptance identifiers")])


class NotAssessableIsDistinct(unittest.TestCase):
    def test_it_is_not_rendered_as_an_outcome(self):
        html = panel("acceptance-unknown-population")["html"]
        self.assertIn("Not assessable", html)
        for forbidden in ("Requirements met at this turn",
                          "A requirement is not met at this turn",
                          "Requirement assessment incomplete"):
            self.assertNotIn(forbidden, html)

    def test_no_structured_criteria_says_there_was_nothing_to_assess(self):
        html = panel("acceptance-no-structured-criteria")["html"]
        self.assertIn("Not assessable", html)
        self.assertIn("No structured requirements were declared", html)

    def test_no_structured_criteria_never_reads_as_a_pass(self):
        html = panel("acceptance-no-structured-criteria")["html"].lower()
        for forbidden in ("met at this turn", "passed", "success"):
            self.assertNotIn(forbidden, html, forbidden)

    def test_an_undeclared_lineage_says_so_in_its_own_words(self):
        html = panel("acceptance-unknown-population")["html"]
        self.assertIn("requirement lineage for this turn was never declared", html)

    def test_a_historical_lineage_is_a_different_sentence(self):
        undeclared = panel("acceptance-unknown-population")["html"]
        nested = panel("acceptance-nested-cause")["html"]
        self.assertIn("predates requirement lineage", nested)
        self.assertNotIn("predates requirement lineage", undeclared)

    def test_a_nested_cause_and_its_turn_are_shown(self):
        html = panel("acceptance-nested-cause")["html"]
        self.assertIn("Underlying cause", html)
        self.assertIn("found at turn 2", html)

    def test_a_structural_failure_is_shown_in_the_error_tone(self):
        """Somebody should look; it must not be prettified into uncertainty."""
        section = acceptance_section(panel("acceptance-structural")["html"])
        self.assertIn("err", section[: section.index("Acceptance identifiers")])
        self.assertIn("does not satisfy its own invariants", section)

    def test_an_operational_gap_is_not_shown_as_an_error(self):
        structural = acceptance_section(panel("acceptance-structural")["html"])
        operational = acceptance_section(panel("acceptance-unknown-population")["html"])
        self.assertNotIn("err", operational[: operational.index("Acceptance identifiers")])
        self.assertNotEqual(structural, operational)


class CountsAndHuman(unittest.TestCase):
    def test_known_counts_are_shown(self):
        html = panel("acceptance-not-met")["html"]
        self.assertIn("3 active", html)
        self.assertIn("1 met", html)
        self.assertIn("1 not met", html)

    def test_known_zero_counts_are_shown_as_zero(self):
        html = panel("acceptance-no-structured-criteria")["html"]
        self.assertIn("0 active", html)

    def test_unknown_counts_say_unknown_rather_than_zero(self):
        """The red line, at the last place it could be lost."""
        html = panel("acceptance-unknown-population")["html"]
        self.assertIn("counts unknown", html)
        self.assertNotIn("0 active", html)

    def test_requires_human_true(self):
        self.assertIn(
            "a requirement needs a person",
            panel("acceptance-incomplete-needs-human")["html"],
        )

    def test_requires_human_false(self):
        self.assertIn("nothing here needs a person", panel("acceptance-met")["html"])

    def test_requires_human_null_is_unknown_and_never_no(self):
        html = panel("acceptance-unknown-population")["html"]
        self.assertIn("Unknown — the requirement set could not be established", html)
        self.assertNotIn("nothing here needs a person", html)

    def test_the_three_states_read_differently(self):
        yes = panel("acceptance-incomplete-needs-human")["html"]
        no = panel("acceptance-met")["html"]
        unknown = panel("acceptance-unknown-population")["html"]
        self.assertNotEqual(yes, no)
        self.assertNotEqual(no, unknown)
        self.assertNotEqual(yes, unknown)


class NoControls(unittest.TestCase):
    def test_there_is_no_action_control_in_the_section(self):
        for scenario in ("acceptance-met", "acceptance-not-met",
                         "acceptance-unknown-population"):
            with self.subTest(scenario=scenario):
                section = acceptance_section(panel(scenario)["html"])
                for forbidden in ("<button", "<input", "<form", "<select",
                                  "onclick", "data-action"):
                    self.assertNotIn(forbidden, section, forbidden)

    def test_the_source_has_no_acceptance_write(self):
        """Scoped to the acceptance function: the panel has controls elsewhere."""
        source = acceptance_source()
        for forbidden in ("acceptance_override", "mark_met", "approve", "override",
                          "dismiss", "rerun", "/acceptance", "fetch", "button"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_the_panel_makes_no_write_request_at_all_for_this_section(self):
        code = tasks_code()
        for forbidden in ('method: "POST"', 'method: "PUT"', 'method: "PATCH"',
                          'method: "DELETE"'):
            self.assertNotIn(forbidden, code, forbidden)

    def test_no_aggregate_helper_is_declared_in_the_panel(self):
        """The fold lives on the workstation; the panel renders what it is sent."""
        code = tasks_code()
        for forbidden in ("function aggregate", "function fold", "countMet",
                          "function outcomeFor", "allMet"):
            self.assertNotIn(forbidden, code, forbidden)

    def test_the_fingerprints_are_tucked_behind_a_disclosure(self):
        html = panel("acceptance-met")["html"]
        self.assertIn("Acceptance identifiers", html)
        self.assertIn("<details", html)


class NoGlobalVerdict(unittest.TestCase):
    def test_no_scenario_renders_a_task_level_word(self):
        for scenario in ("acceptance-met", "acceptance-not-met",
                         "acceptance-incomplete-needs-human",
                         "acceptance-no-structured-criteria",
                         "acceptance-unknown-population"):
            with self.subTest(scenario=scenario):
                section = acceptance_section(panel(scenario)["html"])
                for forbidden in ("task passed", "task failed", "PASS", "FAIL",
                                  "Succeeded", "Overall", "Verdict", "Score",
                                  "Confidence", "Risk", "%"):
                    self.assertNotIn(forbidden, section, forbidden)

    def test_the_heading_is_turn_scoped(self):
        self.assertIn("Acceptance at this turn", panel("acceptance-met")["html"])

    def test_the_source_declares_no_task_level_vocabulary(self):
        code = tasks_code()
        for forbidden in ("taskVerdict", "taskPassed", "overallResult",
                          "acceptanceForTask", "latestAcceptance"):
            self.assertNotIn(forbidden, code, forbidden)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
