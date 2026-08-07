"""The Tasks panel (M2F 57-65).

Two kinds of test, following the pattern the audio, Spotify and YouTube
milestones established:

* **Behavioural**, run through ``tests/tasks_harness.js`` against the shipped
  ``web/tasks.js``. Double submission, stale-response ordering, polling that
  must stop, and a refusal that must not render as success are control-flow
  properties, and a scan of the file cannot see any of them.
* **Structural**, scanning the shipped files, for what the panel *cannot* do —
  no console call, no command or working-directory field, no horizontal
  overflow, no raw terminal by default.

Comments are stripped before scanning, so a guard never trips on the sentence
explaining why the rule exists.
"""

from __future__ import annotations

import json
import re
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


def app_code() -> str:
    return code_only((WEB_DIR / "app.js").read_text(encoding="utf-8"))


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
        raise AssertionError(f"harness failed: {completed.stderr[:800]}")
    payload = json.loads(completed.stdout)
    assert not payload.get("uncaught"), payload.get("uncaught")
    assert not payload.get("timerErrors"), payload.get("timerErrors")
    return payload


# -- the states a phone has to be able to show -------------------------------


class PanelStates(unittest.TestCase):
    def test_an_empty_panel_is_honest(self):
        """Empty-state honesty: it says what will appear, not "no data"."""
        html = panel("empty")["html"]
        self.assertIn("No tasks yet", html)
        self.assertIn("New task", html)

    def test_the_list_groups_active_waiting_and_finished(self):
        """46 at the UI: three buckets, because three is what a person asks."""
        html = panel("list-groups")["html"]
        self.assertIn("Active", html)
        self.assertIn("Waiting for you", html)
        self.assertIn("Finished", html)
        self.assertIn("Çalışan görev", html)
        self.assertIn("Bekleyen görev", html)

    def test_waiting_tasks_are_visibly_actionable(self):
        """60. A task that needs a person must not look like one that does not."""
        html = panel("list-groups")["html"]
        self.assertIn("needs-you", html)
        self.assertIn("needs you", html)

    def test_interrupted_and_failed_read_differently_in_the_list(self):
        """62."""
        html = panel("list-groups")["html"]
        self.assertIn(">interrupted<", html)
        self.assertIn(">failed<", html)

    def test_no_projects_or_adapters_says_which_is_missing(self):
        """Two different causes, two different sentences, both actionable."""
        html = panel("no-projects-or-adapters")["html"]
        self.assertIn("No projects are configured", html)
        self.assertIn("No task adapters are registered", html)
        # And neither is dressed up as a fault.
        self.assertNotIn("error", html.lower())

    def test_the_validation_adapter_is_labelled_in_the_composer(self):
        """A test adapter that read as a real one would be the worst outcome."""
        html = panel("composer-labels-the-validation-adapter")["html"]
        self.assertIn("This is a validation adapter", html)
        self.assertIn("runs no program, calls no model", html)


class TaskDetail(unittest.TestCase):
    def test_a_waiting_task_exposes_the_followup_box(self):
        """60."""
        html = panel("detail-waiting-offers-followup")["html"]
        self.assertIn("taskFollowupText", html)
        self.assertIn("Send answer", html)
        self.assertIn("paused until you answer", html)

    def test_a_terminal_task_hides_invalid_actions(self):
        """61. No cancel, no follow-up box on something already finished."""
        html = panel("detail-terminal-hides-invalid-actions")["html"]
        self.assertNotIn("taskCancel", html)
        self.assertNotIn("taskFollowupText", html)
        # What it does offer is the thing that still makes sense.
        self.assertIn("Copy result", html)

    def test_interrupted_is_explained_as_a_restart_not_a_failure(self):
        """62."""
        html = panel("detail-interrupted-is-distinct")["html"]
        self.assertIn("Cofferdam restarted while this task was running", html)
        self.assertIn("did not fail", html)
        self.assertIn("was not resumed", html)

    def test_failed_shows_the_failure_rather_than_a_restart_notice(self):
        """62. The other half of the same distinction."""
        html = panel("detail-failed-is-distinct")["html"]
        self.assertIn("It failed on purpose.", html)
        self.assertNotIn("Cofferdam restarted", html)

    def test_the_default_view_is_not_a_raw_log(self):
        """63. The summary is the page; the stream is behind a disclosure."""
        result = panel("detail-is-not-a-terminal-log")
        html = result["html"]
        # The event stream is present but disclosed, not the default reading.
        self.assertIn("<details", html)
        self.assertIn("Advanced — event history", html)
        details_index = html.index("<details")
        # The result and the prompt come before the raw events.
        self.assertIn("Result", html[:details_index])
        self.assertLess(html.index("Result"), details_index)

    def test_evidence_distinguishes_a_claim_from_an_observation(self):
        """55 at the UI: an adapter's word is labelled as an adapter's word."""
        html = panel("detail-is-not-a-terminal-log")["html"]
        self.assertIn("(adapter says)", html)
        self.assertIn("(observed)", html)


# -- 57: one action at a time ------------------------------------------------


class DuplicateSubmission(unittest.TestCase):
    def test_three_taps_send_one_request(self):
        """57."""
        result = panel("double-tap-creates-one-task")
        self.assertEqual(result["before"], 0)
        self.assertEqual(result["after"], 1, result["writes"])

    def test_the_request_carries_a_client_request_id(self):
        """57. The backend stays authoritative; this is what it recognises."""
        result = panel("double-tap-creates-one-task")
        body = result["writes"][0]["body"]
        self.assertIn("client_request_id", body)
        self.assertTrue(body["client_request_id"])

    def test_a_request_that_never_answers_gives_the_panel_back(self):
        """A bounded pending state, so nobody has to reload the page."""
        result = panel("hung-request-gives-the-panel-back")
        self.assertIn("disabled", result["during"])
        self.assertIn("did not finish in time", result["after"])
        self.assertIn("cannot say whether it worked", result["after"])

    def test_a_refused_creation_is_shown_as_the_refusal(self):
        """No optimistic false success."""
        result = panel("refused-create-is-not-success")
        html = result["html"]
        self.assertIn("that project is not configured", html)
        self.assertEqual(len(result["writes"]), 1)
        self.assertNotIn("Task started.", html)


# -- 58: response ordering ---------------------------------------------------


class ResponseOrdering(unittest.TestCase):
    def test_an_older_list_cannot_overwrite_a_newer_task(self):
        """58. The failure the Spotify milestone found, prevented here."""
        result = panel("stale-list-cannot-overwrite-a-newer-task")
        self.assertIn("The new title", result["afterWrite"])
        self.assertIn(
            "The new title",
            result["afterStalePoll"],
            "a list response issued earlier overwrote the newly created task",
        )
        self.assertNotIn("The old title", result["afterStalePoll"])


# -- 59: polling -------------------------------------------------------------


class Polling(unittest.TestCase):
    def test_polling_stops_while_the_tab_is_hidden(self):
        """59. A phone in a pocket is not asking for anything."""
        result = panel("poll-stops-while-hidden")
        self.assertEqual(
            result["whileHidden"], result["afterMount"], "the panel polled while hidden"
        )
        self.assertGreater(result["afterVisible"], result["whileHidden"])

    def test_polling_stops_after_sign_out(self):
        """59. And the panel forgets what was asked."""
        result = panel("poll-stops-on-stop")
        self.assertEqual(result["intervalsWhileMounted"], 1)
        self.assertEqual(result["intervalsAfterStop"], 0)
        self.assertEqual(result["afterStop"], result["mounted"])
        self.assertNotIn("Gizli görev", result["html"])


# -- follow-up and cancel ----------------------------------------------------


class Actions(unittest.TestCase):
    def test_a_followup_sends_only_its_text_and_a_request_id(self):
        result = panel("followup-sends-the-answer")
        writes = result["writes"]
        self.assertEqual(len(writes), 1)
        self.assertTrue(writes[0]["path"].endswith("/followups"))
        self.assertEqual(
            set(writes[0]["body"]), {"followup", "client_request_id"}
        )
        self.assertIn("Answer sent.", result["html"])

    def test_cancel_repeats_the_observed_state_rather_than_upgrading_it(self):
        """A task that is still `cancelling` is not cancelled, and says so."""
        result = panel("cancel-repeats-the-observed-state")
        self.assertIn("Cancellation requested", result["html"])
        self.assertNotIn("Task cancelled.", result["html"])
        self.assertEqual(result["writes"][0]["body"], {})


# -- 64: what the client may name --------------------------------------------


class ClientVocabulary(unittest.TestCase):
    def test_the_panel_has_no_working_directory_or_command_field(self):
        """64. Absent from the source, not merely unrendered.

        Matched as a *field or identifier*, not as a substring. The panel's own
        copy contains the sentence "It is never run as a command", which is the
        reassurance this guard exists to protect — a scan that failed on it
        would be repaired by deleting the sentence, which is exactly backwards.
        """
        source = tasks_code()
        for forbidden in (
            "working_directory",
            "workingDirectory",
            "executable",
            "argv",
            "command",
            "shell",
            "cwd",
            "env",
            "webhook",
            "callback_url",
            "return_url",
        ):
            for shape in (
                forbidden + r"\s*:",          # an object key
                r'"' + forbidden + r'"',      # a quoted field name
                r"\b" + forbidden + r"Field\b",
                r"id=\\?\"[^\"]*" + forbidden,  # an input element
            ):
                self.assertIsNone(
                    re.search(shape, source),
                    forbidden + " appears as a field in tasks.js",
                )

    def test_the_panel_never_sends_an_origin(self):
        """4 at the UI."""
        source = tasks_code()
        self.assertNotIn("origin:", source)
        self.assertNotIn('"origin"', source)

    def test_every_write_body_is_from_the_closed_set(self):
        for name, allowed in (
            ("double-tap-creates-one-task",
             {"project_id", "adapter_id", "prompt", "client_request_id"}),
            ("followup-sends-the-answer", {"followup", "client_request_id"}),
            ("cancel-repeats-the-observed-state", set()),
        ):
            for write in panel(name)["writes"]:
                body = write["body"] or {}
                self.assertTrue(
                    set(body) <= allowed,
                    name + " sent an unexpected field: " + str(sorted(set(body) - allowed)),
                )

    def test_the_composer_offers_only_a_project_an_adapter_and_a_prompt(self):
        html = panel("composer-labels-the-validation-adapter")["html"]
        self.assertIn("taskProject", html)
        self.assertIn("taskAdapter", html)
        self.assertIn("taskPrompt", html)
        # And says what the prompt is, so nobody types a command into it.
        self.assertIn("never run as a command", html)


# -- privacy -----------------------------------------------------------------


class Privacy(unittest.TestCase):
    def test_the_panel_never_logs(self):
        """A task's prompt and result are somebody's private thinking."""
        self.assertNotIn("console.", tasks_code())

    def test_the_panel_stores_nothing(self):
        source = tasks_code()
        for forbidden in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
            self.assertNotIn(forbidden, source, forbidden + " is used")

    def test_task_content_never_reaches_a_url(self):
        """Content travels in a body, never in a path or a query string."""
        source = tasks_code()
        # Every task path is built from an encoded task id and a fixed suffix.
        for match in re.findall(r'"/api/tasks[^"]*"', source):
            self.assertNotIn("prompt", match)
            self.assertNotIn("followup", match)

    def test_no_scenario_leaked_content_into_the_console(self):
        for name in ("list-groups", "detail-is-not-a-terminal-log", "followup-sends-the-answer"):
            self.assertEqual(panel(name)["consoleOutput"], [], name)


# -- 65: phone and tablet layout ---------------------------------------------


class Layout(unittest.TestCase):
    def setUp(self) -> None:
        self.css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
        self.rules = [
            block for block in self.css.split("}") if ".task-" in block.split("{")[0]
        ]

    def test_every_flex_or_grid_row_can_shrink(self):
        """65. `min-width: 0` is what actually prevents sideways overflow."""
        for block in self.rules:
            if "display: flex" in block or "display: grid" in block:
                selector = block.split("{")[0].strip()
                self.assertIn("min-width: 0", block, selector + " cannot shrink")

    def test_long_text_wraps_rather_than_overflowing(self):
        for selector in (".task-title", ".task-meta", ".task-activity", ".task-event-text"):
            block = next(b for b in self.rules if selector in b.split("{")[0])
            self.assertIn("overflow-wrap: anywhere", block, selector)

    def test_the_result_block_scrolls_inside_itself(self):
        """A long result must not widen the page."""
        block = next(b for b in self.rules if ".task-text" in b.split("{")[0])
        self.assertIn("overflow-x: auto", block)
        self.assertIn("white-space: pre-wrap", block)

    def test_touch_targets_are_large_enough(self):
        for block in self.rules:
            if "min-height" in block:
                match = re.search(r"min-height:\s*(\d+)px", block)
                if match:
                    self.assertGreaterEqual(int(match.group(1)), 40, block.split("{")[0])

    def test_the_panel_has_a_narrow_screen_layout(self):
        narrow = self.css.split("@media (max-width: 480px)")[-1]
        self.assertIn(".task-facts", narrow)
        self.assertIn(".task-actions button", narrow)


class RealAdapterBoundaries(unittest.TestCase):
    """What the panel does once an adapter runs an actual program.

    Behavioural, through the shipped ``web/tasks.js``. The harness's adapter is
    called "Runner" and is deliberately not named after any product: the panel
    must render a real adapter's boundaries out of the fields the API sends,
    without knowing what it is.
    """

    def test_an_authentication_wait_offers_no_text_field(self):
        """A textarea under "waiting for sign-in" invites a password.

        Task Core named ``SECRET_BEARING_WAITING_REASONS`` and the panel did not
        read the list, because the only adapter that shipped never produced one.
        The first adapter that authenticates does.
        """
        result = panel("authentication-wait-offers-no-text-field")
        self.assertFalse(result["hasFollowupBox"])
        self.assertFalse(result["hasSendButton"])
        html = result["html"]
        self.assertIn("task-secret-wait", html)
        self.assertIn("Never type a password", html)
        self.assertIn("on the workstation itself", html)
        # No input of any kind appears in that state.
        self.assertNotIn("<textarea", html)
        self.assertNotIn("<input", html)

    def test_cancel_is_still_offered_while_waiting_for_sign_in(self):
        """Withholding the box must not withhold the way out of the task."""
        result = panel("authentication-wait-offers-no-text-field")
        self.assertTrue(result["hasCancelButton"])

    def test_an_ordinary_question_still_gets_a_field(self):
        """The control. Without this, "no box" could be satisfied by removing
        follow-up altogether rather than by protecting one case of it."""
        result = panel("clarification-wait-still-offers-the-box")
        self.assertTrue(result["hasFollowupBox"])
        self.assertNotIn("task-secret-wait", result["html"])

    def test_the_composer_shows_what_a_real_adapter_will_refuse(self):
        """Before somebody writes a prompt asking for the thing it cannot do."""
        html = panel("composer-shows-a-real-adapter-limitations")["html"]
        self.assertIn("task-limitations", html)
        self.assertIn("It has no shell.", html)
        self.assertIn("cannot leave the project folder", html)
        self.assertIn("Never type a password", html)
        # And it is still not a terminal: no command, path or flag field.
        for forbidden in ("taskCommand", "taskCwd", "taskEnv", "taskFlags"):
            self.assertNotIn(forbidden, html)

    def test_the_panel_prints_nothing_to_the_console(self):
        for name in (
            "authentication-wait-offers-no-text-field",
            "composer-shows-a-real-adapter-limitations",
        ):
            self.assertEqual(panel(name)["consoleOutput"], [], name)


class DetailFollowsTheBackend(unittest.TestCase):
    """Defect 1: the open task detail did not update on its own.

    The phone sat on `running` while the backend had moved on, and only a manual
    browser reload showed the truth.
    """

    def test_the_detail_updates_without_a_page_reload(self):
        """The regression test for the reported defect.

        Detail polling was never absent — the requests went out on every tick
        and their responses were thrown away. The list and the detail shared one
        response-ordering counter, the poll issues the detail request first and
        the list second, so the list held the higher generation; whenever the
        list response landed first, the detail was discarded as stale. Reading a
        task detail asks the adapter what it saw, so it is reliably the slower
        of the two, which turned a race into a certainty.
        """
        result = panel("detail-updates-without-a-page-reload")
        self.assertIn("running", result["whileRunning"])
        self.assertGreaterEqual(result["detailRequests"], 2, "detail was not polled")
        self.assertNotIn("running", result["html"])
        self.assertIn("turn complete", result["html"].lower())

    def test_polling_does_not_storm(self):
        """Conservative: one detail read and one list read per tick, not more."""
        result = panel("detail-updates-without-a-page-reload")
        self.assertLessEqual(result["detailRequests"], result["listRequests"] + 1)
        self.assertLessEqual(result["detailRequests"], 6)


class FinishedTurnWording(unittest.TestCase):
    """Defect 2: a completed turn was labelled as waiting for an answer."""

    def test_a_finished_turn_does_not_say_needs_you(self):
        result = panel("turn-complete-does-not-say-needs-you")
        html = result["html"]
        self.assertNotIn("needs you", html)
        self.assertNotIn("waiting for an answer", html)
        self.assertIn("Turn complete", html)
        self.assertIn("optional follow-up", html)

    def test_the_field_is_not_called_your_answer(self):
        """Nothing was asked, so nothing is an answer."""
        html = panel("turn-complete-does-not-say-needs-you")["html"]
        self.assertNotIn("Your answer", html)
        self.assertIn("Follow-up (optional)", html)

    def test_a_follow_up_is_still_offered(self):
        self.assertTrue(panel("turn-complete-does-not-say-needs-you")["hasFollowupBox"])

    def test_finishing_is_offered_so_cancel_is_not_the_only_way_out(self):
        result = panel("turn-complete-does-not-say-needs-you")
        self.assertTrue(result["hasFinishButton"])
        self.assertTrue(result["hasCancelButton"])

    def test_finishing_posts_to_the_finish_route(self):
        result = panel("finish-closes-the-session")
        writes = [(w["method"], w["path"]) for w in result["writes"]]
        self.assertIn(("POST", "/api/tasks/task_done/finish"), writes)
        self.assertNotIn(("POST", "/api/tasks/task_done/cancel"), writes)

    def test_a_real_question_still_says_your_answer(self):
        """The control: the distinction is only worth anything if both sides
        still work."""
        html = panel("clarification-wait-still-offers-the-box")["html"]
        self.assertIn("Your answer", html)
        self.assertNotIn("Turn complete", html)


class UnauthorizedIsNotAGenericError(unittest.TestCase):
    """7. A 401 means sign in again, and the panel has to say so.

    The shell already clears the token and shows "token rejected". The panel
    used to swallow the error and leave the last good detail on screen looking
    current, which is a different way of being wrong.
    """

    def test_it_says_to_sign_in_again(self):
        html = panel("unauthorized-is-surfaced-as-sign-in")["html"]
        self.assertIn("Sign in again", html)

    def test_it_does_not_report_a_generic_reachability_failure(self):
        html = panel("unauthorized-is-surfaced-as-sign-in")["html"]
        self.assertNotIn("could not reach the workstation", html)

    def test_polling_stops_once_the_token_is_rejected(self):
        """Otherwise a signed-out phone hammers a route that will keep saying no."""
        result = panel("unauthorized-is-surfaced-as-sign-in")
        self.assertEqual(result["requestsAfterUnauthorized"], 0)


class PanelSeparation(unittest.TestCase):
    def test_the_shell_carries_the_tasks_panel(self):
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="tasksPanel"', html)
        self.assertIn("<h2>Tasks</h2>", html)
        self.assertIn('<script src="/tasks.js"></script>', html)

    def test_the_panel_is_mounted_independently_of_the_others(self):
        """A task store that cannot be read must not take the page down."""
        source = app_code()
        block = source.split("global.CofferdamTasks.mount")[1][:300]
        self.assertIn(".catch(", block)

    def test_signing_out_stops_the_panel(self):
        self.assertIn("global.CofferdamTasks.stop()", app_code())

    def test_the_shell_explains_what_a_task_is_not(self):
        """The panel's own copy has to rule out the two wrong readings."""
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        block = html.split('id="tasksPanel"')[1][:1200]
        self.assertIn("interrupted", block)
        self.assertIn("You choose the project, the adapter and the words", block)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
