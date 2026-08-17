"""M2K PR8 — what the assessment panel says, and the one word it must never say.

Behavioural, run through ``tests/tasks_harness.js`` against the shipped
``web/tasks.js`` — so these assert what a person actually sees, not what the
source appears to intend.

The load-bearing requirement is one distinction:

    ``unverified`` must look materially different from ``not_met``.

``not_met`` is a finding about the work: the machine looked completely and the
required change is not there. ``unverified`` is a statement about Cofferdam: the
evidence could not decide. Rendered alike — same colour, same words, same shape
of sentence — every limit of the observer becomes an accusation about the worker,
which is the whole reason the vocabulary has three values instead of two.

The rest is absence: no PASS, no FAIL, no aggregate, no score, no confidence, no
risk, no re-run control and no check-runner control anywhere on the screen.
"""

from __future__ import annotations

import json
import re
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
    return payload


def tasks_code() -> str:
    return code_only((WEB_DIR / "tasks.js").read_text(encoding="utf-8"))


class ResultWords(unittest.TestCase):
    def setUp(self):
        self.html = panel("assessment-shows-all-three-results")["html"]

    def test_met_is_rendered_as_met(self):
        self.assertIn(">Met<", self.html)

    def test_not_met_is_rendered_as_not_met(self):
        self.assertIn(">Not met<", self.html)

    def test_unverified_is_rendered_as_could_not_verify(self):
        self.assertIn(">Could not verify<", self.html)

    def test_all_three_appear_on_one_screen(self):
        for word in (">Met<", ">Not met<", ">Could not verify<"):
            self.assertIn(word, self.html, word)

    def test_unverified_does_not_share_a_class_with_not_met(self):
        """The load-bearing one. Different tone, so they cannot read alike."""
        import re

        badges = re.findall(r'<span class="(badge[^"]*)">([^<]*)</span>', self.html)
        classes = {label: cls for cls, label in badges}
        self.assertIn("Not met", classes)
        self.assertIn("Could not verify", classes)
        self.assertNotEqual(
            classes["Not met"],
            classes["Could not verify"],
            "unverified and not_met render with the same class",
        )

    def test_unverified_is_not_styled_as_an_error(self):
        import re

        badges = re.findall(r'<span class="(badge[^"]*)">([^<]*)</span>', self.html)
        classes = {label: cls for cls, label in badges}
        self.assertNotIn("err", classes["Could not verify"])

    def test_the_expected_criterion_is_shown_beside_its_result(self):
        self.assertIn("src/app.py", self.html)
        self.assertIn("src/gone.py", self.html)
        self.assertIn("src/old.py", self.html)
        self.assertIn("src/new.py", self.html)

    def test_reason_codes_are_rendered_as_sentences(self):
        self.assertIn("A resulting change for this path was observed.", self.html)
        self.assertIn("already had uncommitted changes", self.html)

    def test_results_appear_in_ordinal_order(self):
        order = [
            self.html.index(">Met<"),
            self.html.index(">Not met<"),
            self.html.index(">Could not verify<"),
        ]
        self.assertEqual(order, sorted(order))


class ForbiddenVocabulary(unittest.TestCase):
    def setUp(self):
        self.html = panel("assessment-shows-all-three-results")["html"]

    def test_no_pass_or_fail_language(self):
        for forbidden in ("Passed", "Failed", "Success", "Successful", "Error:"):
            self.assertNotIn(forbidden, self.html, forbidden)

    def test_no_aggregate_is_shown(self):
        for forbidden in ("2/4", "0/4", "4/4", "50%", "All criteria", "Overall"):
            self.assertNotIn(forbidden, self.html, forbidden)

    def test_no_confidence_or_risk(self):
        for forbidden in ("Confidence", "confidence", "Risk", "risk level", "Score"):
            self.assertNotIn(forbidden, self.html, forbidden)

    def test_there_is_no_rerun_or_check_runner_control(self):
        for forbidden in ("Re-run", "Rerun", "Run checks", "Run tests", "Evaluate again"):
            self.assertNotIn(forbidden, self.html, forbidden)
        self.assertNotIn("taskRerunEvaluation", self.html)

    def test_the_source_declares_no_aggregate_helper(self):
        code = tasks_code()
        for forbidden in (
            "allMet", "metCount", "percentMet", "overallResult", "aggregate",
            "passRate", "score",
        ):
            self.assertNotIn(forbidden, code, forbidden)

    def test_the_source_has_no_rerun_request(self):
        code = tasks_code()
        self.assertNotIn("rerun", code.lower())
        # Every assessment request the panel can make is a GET of the view.
        #
        # This counted the occurrences until M2K PR24, which gave the panel a
        # second, different reason to read the route: an `extend` or `replace`
        # declaration names the predecessor's criteria snapshot id, and reading
        # it is what stops a person being asked to type one. Both are reads of
        # the same immutable view. So the guard moves from "there is one call"
        # to the property it was standing in for — no call anywhere passes an
        # options object, which is what `deps.api` turns into a write.
        self.assertGreaterEqual(code.count("/assessment"), 1)
        self.assertNotIn('assessment", {', code)
        self.assertNotIn('/assessment", {', code)
        for match in re.finditer(r'"/assessment"[^;]{0,40}', code):
            self.assertNotIn("body", match.group(0))


class ManualCriterion(unittest.TestCase):
    def setUp(self):
        self.html = panel("assessment-shows-all-three-results")["html"]

    def test_a_manual_criterion_shows_its_description_as_the_expectation(self):
        self.assertIn("a person confirms the page renders", self.html)

    def test_it_is_could_not_verify_with_a_manual_reason(self):
        self.assertIn("A person has to check this one. Cofferdam cannot.", self.html)

    def test_there_is_no_control_to_mark_it_done(self):
        for forbidden in ("Mark as met", "Mark met", "Confirm", "I checked"):
            self.assertNotIn(forbidden, self.html, forbidden)


class NotProvided(unittest.TestCase):
    def setUp(self):
        self.html = panel("assessment-not-provided")["html"]

    def test_it_says_no_criteria_were_supplied(self):
        self.assertIn("No structured acceptance criteria were supplied", self.html)

    def test_it_never_reads_as_a_pass(self):
        for forbidden in ("0/0", "All criteria met", "Success", "Passed", "Nothing to fail"):
            self.assertNotIn(forbidden, self.html, forbidden)

    def test_it_shows_no_result_rows(self):
        for forbidden in (">Met<", ">Not met<"):
            self.assertNotIn(forbidden, self.html, forbidden)


class LegacyUnknown(unittest.TestCase):
    def setUp(self):
        self.html = panel("assessment-legacy-unknown")["html"]

    def test_it_says_criteria_were_not_recorded_for_a_historical_turn(self):
        self.assertIn("not recorded for this historical turn", self.html)

    def test_it_does_not_say_there_were_no_criteria(self):
        for forbidden in ("No criteria", "0 criteria", "Success", "Passed"):
            self.assertNotIn(forbidden, self.html, forbidden)

    def test_it_is_not_confused_with_not_provided(self):
        self.assertNotIn("No structured acceptance criteria were supplied", self.html)


class MissingEvaluation(unittest.TestCase):
    def setUp(self):
        self.html = panel("assessment-evaluation-not-recorded")["html"]

    def test_it_says_evaluation_not_recorded(self):
        self.assertIn("Evaluation not recorded", self.html)

    def test_it_is_not_called_an_unverified_criterion_result(self):
        """There is no result record at all, which is a different statement."""
        self.assertNotIn(">Could not verify<", self.html)

    def test_it_offers_no_way_to_run_one(self):
        for forbidden in ("Re-run", "Evaluate", "Retry evaluation"):
            self.assertNotIn(forbidden, self.html, forbidden)

    def test_the_criteria_are_still_shown(self):
        self.assertIn("src/app.py", self.html)

    def test_a_still_running_turn_reads_differently(self):
        html = panel("assessment-turn-not-closed")["html"]
        self.assertIn("still running", html)
        self.assertNotIn("Evaluation not recorded", html)


class TurnQualification(unittest.TestCase):
    def test_the_panel_names_the_turn_it_is_showing(self):
        html = panel("assessment-shows-all-three-results")["html"]
        self.assertIn("Assessment — turn 1", html)

    def test_a_second_turn_is_requested_and_labelled(self):
        result = panel("assessment-second-turn")
        self.assertIn("Assessment — turn 2", result["html"])
        asked = [r for r in result["requests"] if "/assessment" in r.get("path", "")]
        self.assertTrue(asked)
        self.assertTrue(
            any(r["path"].endswith("/turns/2/assessment") for r in asked),
            [r["path"] for r in asked],
        )

    def test_the_request_is_always_turn_qualified(self):
        result = panel("assessment-shows-all-three-results")
        asked = [r for r in result["requests"] if "/assessment" in r.get("path", "")]
        self.assertTrue(asked)
        for request in asked:
            self.assertIn("/turns/", request["path"])
            self.assertEqual(request.get("method", "GET"), "GET")


class AuditHandles(unittest.TestCase):
    def setUp(self):
        self.html = panel("assessment-shows-all-three-results")["html"]

    def test_the_identifiers_are_available_but_tucked_away(self):
        self.assertIn("Audit identifiers", self.html)
        self.assertIn("<details", self.html)

    def test_they_are_not_presented_as_proof_or_a_score(self):
        for forbidden in ("Verified by", "Trust", "Proof", "Confidence", "Signature"):
            self.assertNotIn(forbidden, self.html, forbidden)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
