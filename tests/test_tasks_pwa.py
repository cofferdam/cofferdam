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
        # "Answer sent." was wrong for a follow-up that answers nothing; the
        # panel says "Sent." now. See FinishedTurnWording.
        self.assertIn("Sent.", result["html"])

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

    def test_the_panel_stores_only_namespaced_drafts(self):
        """M2I PR4 replaced "stores nothing" with "stores exactly one thing".

        The old rule was a blanket ban on browser storage, and it was the right
        rule while the panel had nothing worth keeping. It stopped being right
        when the thing not being kept was somebody's half-written instruction to
        an agent: iOS discards a backgrounded tab whenever it likes, and a rule
        that guaranteed the draft was lost was protecting nothing.

        So the ban became specific. `localStorage` is allowed; `sessionStorage`,
        cookies and IndexedDB are not, because one storage mechanism is enough
        and three are three things to audit. Every key is namespaced, every
        access goes through the four draft helpers, and what may be under a key
        is asserted separately below.
        """
        source = tasks_code()
        for forbidden in ("sessionStorage", "document.cookie", "indexedDB"):
            self.assertNotIn(forbidden, source, forbidden + " is used")
        self.assertIn("cofferdam.taskdraft.", source, "drafts are not namespaced")

    def test_every_storage_access_is_guarded(self):
        """A bare `localStorage` access throws on iOS Safari and kills the module.

        app.js learned this from a real device: under Private Browsing and some
        MDM configurations the *property access itself* raises SecurityError, and
        an unguarded one at boot took the whole page down. This panel inherits
        the rule rather than the bug.
        """
        for line in tasks_code().splitlines():
            stripped = line.strip()
            if "localStorage" in stripped:
                self.assertIn(
                    "global.localStorage",
                    stripped,
                    "bare localStorage access can throw on iOS Safari: " + stripped,
                )

    def test_only_the_draft_writer_touches_storage(self):
        """One writer, and it takes a task, an operation and a string.

        This is what makes "no token, no session id, no provider payload in
        browser storage" structural rather than reviewed: there is no second
        `setItem` call site through which anything else could be added.
        """
        source = tasks_code()
        self.assertEqual(
            source.count("global.localStorage.setItem"),
            2,
            "storage is written from somewhere other than writeDraft and its probe",
        )

    def test_no_provider_or_credential_word_reaches_storage(self):
        """The keys are drafts. Nothing here names anything else.

        Scanned as *identifiers* the panel could store, not as prose: the file's
        own comments say the words "token" and "session" while explaining why
        neither is stored, and a guard that failed on the explanation would be
        repaired by deleting the explanation.
        """
        source = tasks_code()
        block = source[source.index("function writeDraft") :]
        block = block[: block.index("function clearAllDrafts")]
        for forbidden in (
            "token",
            "provider_session_id",
            "session_id",
            "approve",
            "approval",
            "tool_input",
        ):
            self.assertNotIn(forbidden, block, "writeDraft can store " + forbidden)

    def test_the_panel_never_reads_or_writes_the_device_token_key(self):
        """The token lives in storage; it is not this panel's to touch.

        ``app.js`` keeps the device token under ``cofferdam.token`` — validated
        against the API before it is written, and that is deliberate on a phone.
        What matters here is that the tasks panel has no business with it: it
        holds the token nowhere, reads it from no key, and its own namespace
        cannot collide with it.

        The two prefixes are checked for disjointness rather than merely being
        different strings, because ``clearAllDrafts`` iterates the whole store
        and removes by prefix. A namespace that were a prefix of the token key
        would sign somebody out every time a draft was dropped.
        """
        source = tasks_code()
        self.assertNotIn("cofferdam.token", source)
        self.assertNotIn("Authorization", source, "the panel builds its own auth header")
        self.assertNotIn("Bearer", source)

        draft_prefix = "cofferdam.taskdraft."
        token_key = "cofferdam.token"
        self.assertFalse(token_key.startswith(draft_prefix))
        self.assertFalse(draft_prefix.startswith(token_key))

    def test_no_raw_provider_payload_can_be_stored_or_rendered(self):
        """Section 5, at the client: what the server never sends, the panel
        never names.

        Hidden reasoning, tool input and the raw SDK message are absent from
        every API response by construction. This asserts the other half — that
        the panel has no field, no key and no branch that would read one if a
        future response carried it, so adding the field server-side could not
        silently surface it on a phone.
        """
        source = tasks_code()
        for forbidden in (
            "provider_session_id",
            "raw_payload",
            "raw_message",
            "thinking",
            "reasoning",
            "tool_input",
            "tool_use",
            "permission_mode",
            "acceptEdits",
        ):
            self.assertNotIn(forbidden, source, forbidden + " is named by tasks.js")

    def test_the_service_worker_caches_no_task_route(self):
        """A cached task response would outlive both `no-store` and a sign-out."""
        worker = (WEB_DIR / "sw.js").read_text(encoding="utf-8")
        for forbidden in (
            "/api/tasks",
            "clarification",
            "followup",
            "taskdraft",
            "/result",
        ):
            self.assertNotIn(forbidden, worker.lower(), forbidden + " is in the worker")

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


class FollowUpDraftSurvives(unittest.TestCase):
    """The reported data-loss defect: typed follow-up text disappeared.

    Task-detail polling rebuilt the whole detail with `innerHTML`, which in a
    browser destroys the textarea and everything in it. The auto-refresh added
    for the previous defect made it happen every few seconds, and pasting
    happened to coincide with a poll, which is why the page looked like it was
    refreshing itself mid-paste.

    These run against the shipped `web/tasks.js` through a harness that now
    models the destruction honestly — an earlier version memoised its stub
    nodes forever, so a textarea could never lose its value and this class of
    bug was invisible to it.
    """

    def test_a_typed_draft_survives_several_polls(self):
        """1, 2, 3."""
        result = panel("draft-survives-polling")
        self.assertEqual(result["draftAfterPolling"], "ikinci bir Türkçe cümle ekle")

    def test_the_backend_still_updates_while_the_draft_is_held(self):
        """4. Preserving the draft must not mean freezing the view."""
        result = panel("draft-survives-polling")
        self.assertIn("turn complete", result["html"].lower())

    def test_pasted_text_survives_and_identical_polls_do_not_rebuild(self):
        """5. An unchanged snapshot renders nothing, so nothing is destroyed."""
        result = panel("draft-survives-paste-and-identical-snapshots")
        self.assertEqual(result["draft"], "Birinci cümle. İkinci cümle. Üçüncü cümle.")
        self.assertEqual(
            result["renders"], 0, "identical polls rebuilt the form anyway"
        )

    def test_focus_is_returned_after_a_render_that_had_to_happen(self):
        """6. A real change still rebuilds; the caret goes back afterwards."""
        result = panel("draft-survives-polling")
        self.assertEqual(result["focusedBefore"], "taskFollowupText")
        self.assertEqual(result["focusedAfter"], "taskFollowupText")

    def test_a_failed_submission_keeps_every_character(self):
        """8. The moment most likely to fail is the one where the text matters."""
        result = panel("failed-submit-preserves-the-draft")
        self.assertEqual(result["draft"], "bu metin kaybolmamalı")

    def test_an_accepted_submission_clears_it_exactly_once(self):
        """9, 10. And a double tap is still one delivery."""
        result = panel("accepted-submit-clears-once")
        self.assertIn(result["draft"], ("", None))
        self.assertEqual(result["followupPosts"], 1)
        self.assertEqual(len(set(result["requestIds"])), 1)

    def test_text_typed_while_sending_is_not_swallowed(self):
        """9. The clear must correspond to *this* submission.

        Somebody sends one message and starts the next before the server
        answers. Clearing unconditionally on acceptance deletes a message nobody
        sent and nobody asked to lose.
        """
        result = panel("text-typed-while-sending-is-not-swallowed")
        self.assertEqual(result["draft"], "ikinci mesaj")

    def test_a_draft_never_leaks_between_tasks(self):
        """11."""
        result = panel("drafts-are-per-task")
        self.assertEqual(result["draftInB"], "")

    def test_returning_to_a_task_restores_its_draft(self):
        """12. Session policy: drafts are kept per task while the panel lives."""
        result = panel("drafts-are-per-task")
        self.assertEqual(result["draftBackInA"], "A için taslak")

    def test_finishing_cannot_accidentally_submit_the_draft(self):
        """13."""
        result = panel("finishing-does-not-submit-the-draft")
        self.assertIn("/api/tasks/task_x/finish", result["writes"])
        self.assertFalse(
            [path for path in result["writes"] if "followups" in path],
            "finishing sent the unsent draft",
        )

    def test_no_draft_reaches_the_console_or_a_cache(self):
        """14.

        `localStorage` is no longer in this list — see
        ``Privacy.test_the_panel_stores_only_namespaced_drafts`` for what
        replaced the blanket ban and why. The service worker is still forbidden
        from knowing anything about a follow-up: a cached response carrying one
        would outlive the sign-out that is supposed to remove it.
        """
        for name in ("draft-survives-polling", "failed-submit-preserves-the-draft"):
            self.assertEqual(panel(name)["consoleOutput"], [], name)
        source = (WEB_DIR / "tasks.js").read_text(encoding="utf-8")
        self.assertNotIn("console.", source)
        self.assertNotIn("caches.", source)
        worker = (WEB_DIR / "sw.js").read_text(encoding="utf-8")
        self.assertNotIn("followup", worker.lower())
        self.assertNotIn("taskdraft", worker.lower())

    def test_drafts_are_keyed_by_task_rather_than_held_in_one_slot(self):
        """A single field would carry one task's words into another's box."""
        source = (WEB_DIR / "tasks.js").read_text(encoding="utf-8")
        self.assertIn("draftKey(", source)
        self.assertIn("draftFor(", source)

    def test_the_draft_is_not_written_to_the_server_while_typing(self):
        """A draft is not a follow-up, and a half-typed sentence is not history."""
        result = panel("draft-survives-polling")
        self.assertNotIn("followups", json.dumps(result.get("html", "")))


class StructuredQuestions(unittest.TestCase):
    """M2I PR4: the phone can answer a structured question.

    The gap this closes was the largest in the milestone and was entirely on the
    client. The backend had shipped `/clarifications` and
    `/clarifications/{id}/answer` in M2I PR2 and **nothing called them**: the
    panel rendered a generic "Your answer" box for any `waiting_for_user` and
    posted it to `/followups`, a route the server refuses outright for as long as
    a question is open. From a phone, the headline feature of M2I could not be
    used at all.
    """

    def test_the_question_is_rendered_with_its_options(self):
        result = panel("sdk-question-is-rendered-with-its-options")
        self.assertTrue(result["hasQuestion"])
        self.assertTrue(result["hasSendButton"])
        html = result["html"]
        self.assertIn("Hangi dosyayı düzenleyeyim?", html)
        self.assertIn("README.md", html)
        self.assertIn("STATUS.md", html)

    def test_a_fixed_choice_question_gets_no_free_text_box(self):
        """A field whose contents the server would refuse is not a field."""
        self.assertFalse(panel("sdk-question-is-rendered-with-its-options")["hasAnswerBox"])

    def test_free_text_is_offered_when_the_question_allows_it(self):
        self.assertTrue(
            panel("sdk-question-free-text-is-offered-only-when-allowed")["hasAnswerBox"]
        )

    def test_no_followup_box_is_offered_while_a_question_is_open(self):
        """The two routes stay apart on the screen as well as on the wire."""
        self.assertFalse(panel("sdk-question-is-rendered-with-its-options")["hasFollowupBox"])

    def test_the_answer_goes_to_the_answer_route(self):
        writes = panel("sdk-answer-goes-to-the-answer-route")["writes"]
        self.assertEqual(len(writes), 1, writes)
        self.assertIn("/clarifications/", writes[0]["path"])
        self.assertTrue(writes[0]["path"].endswith("/answer"))
        self.assertNotIn("followups", writes[0]["path"])

    def test_the_answer_body_is_the_closed_set(self):
        """Two fields, and no third one an approval could travel in."""
        writes = panel("sdk-answer-goes-to-the-answer-route")["writes"]
        self.assertTrue(set(writes[0]["body"]) <= {"answer", "option_ids"}, writes[0])
        self.assertEqual(writes[0]["body"]["option_ids"], ["opt2"])

    def test_a_followup_while_a_question_is_open_sends_nothing(self):
        result = panel("sdk-followup-is-refused-while-a-question-is-open")
        self.assertEqual(result["writes"], [])
        self.assertIn("waiting for an answer to a question", result["html"])

    def test_answering_is_labelled_as_information_not_permission(self):
        """Said on the screen where somebody is about to type it."""
        html = panel("sdk-question-is-rendered-with-its-options")["html"]
        self.assertIn("information, not permission", html)
        self.assertIn("cannot approve a tool", html)

    def test_an_unverified_question_shape_says_so(self):
        """Section 9: unverified variants are marked, never presented as verified."""
        html = panel("sdk-unverified-question-shape-is-labelled")["html"]
        self.assertIn("has not verified this question's shape", html)

    def test_the_question_form_carries_no_provider_field(self):
        """No session id, no tool input, no raw payload — rendered or stored."""
        result = panel("sdk-question-renders-no-provider-field")
        html = result["html"]
        for forbidden in (
            "provider_session_id",
            "tool_input",
            "permission_mode",
            "acceptEdits",
            "claude-agent-sdk",
        ):
            self.assertNotIn(forbidden, html, forbidden + " is rendered")
        self.assertEqual(result["storage"], {}, "a question was written to storage")

    def test_the_panel_has_no_approval_control_at_all(self):
        """Not a disabled one, not a hidden one — none.

        The API has no approval route, and this is the client half of the same
        property: there is no path from a tap to a permission decision because
        there is no control that would produce one.

        Matched as a *route, request field or control*, not as a substring — the
        same rule ``ClientVocabulary`` follows and for the same reason. Two
        legitimate uses of the word survive it deliberately: the panel's own copy
        says "It cannot approve a tool", which is the sentence this guard exists
        to protect, and ``waitingLabel`` renders `approval` as words because a
        waiting reason Cofferdam can report is a waiting reason a person should
        be able to read. Neither is a control, and a scan that failed on either
        would be repaired by deleting the honesty.
        """
        source = tasks_code()
        for forbidden in ("approve", "approval", "deny", "allow_tool", "permission_mode"):
            for shape in (
                r"\b" + forbidden + r"\s*:",       # a request-body key
                r"id=\\?\"[^\"]*" + forbidden,     # a control's id
                r"/" + forbidden,                  # a route segment
            ):
                self.assertIsNone(
                    re.search(shape, source, re.IGNORECASE),
                    forbidden + " appears as a field, control or route in tasks.js",
                )
        # Every write path this panel knows, listed: none of them approves.
        for match in re.findall(r'"/api/tasks[^"]*"', source):
            for forbidden in ("approve", "approval", "deny", "permission"):
                self.assertNotIn(forbidden, match, match)

    def test_an_approval_wait_is_still_described_in_words(self):
        """The control for the guard above.

        "No approval control" must not be satisfied by pretending the waiting
        reason does not exist. If an adapter ever reported one, the panel says so
        and offers nothing — which is the truthful pair.
        """
        self.assertIn('case "approval"', tasks_code())


class DraftsSurviveAReload(unittest.TestCase):
    """M2I PR4: a draft outlives the page, not just the poll.

    The previous milestone made a draft survive a re-render, which is what a
    polling tick does to it. It could not survive the page being discarded — and
    on a phone that is the common case, not the rare one: iOS reclaims a
    backgrounded tab and the person comes back to an empty box with no
    indication anything was ever there.

    The harness models this honestly. Its `localStorage` double lives outside the
    sandbox that runs `tasks.js`, so a "reload" builds entirely fresh module
    state and only what was genuinely written down crosses over.
    """

    def test_a_followup_draft_survives_a_reload(self):
        result = panel("followup-draft-survives-a-reload")
        self.assertEqual(result["draftAfterReload"], "yarım kalmış bir cümle")
        self.assertEqual(result["storedBefore"], 1)

    def test_a_clarification_draft_survives_a_reload(self):
        result = panel("clarification-draft-survives-a-reload")
        self.assertEqual(result["draftAfterReload"], "üçüncü seçenek olsun")

    def test_the_two_kinds_of_draft_do_not_share_a_key(self):
        """An answer to a question must not reappear as a follow-up.

        They are separate acts with separate routes, and a shared key would put
        the words somebody wrote for a question into the box that sends a new
        instruction — after the question is closed and nobody is checking.
        """
        result = panel("drafts-are-separate-by-operation")
        self.assertEqual(result["followupBox"], "")
        self.assertEqual(result["keys"], ["cofferdam.taskdraft.clarification.task_sdk"])

    def test_one_task_draft_never_appears_on_another(self):
        result = panel("drafts-do-not-cross-tasks-in-storage")
        self.assertEqual(result["draftInB"], "")
        self.assertEqual(result["keys"], ["cofferdam.taskdraft.followup.task_a"])

    def test_a_cancelled_task_drops_its_draft(self):
        """Leaving it would show text on a screen with nowhere to send it."""
        result = panel("a-terminal-task-drops-its-draft")
        self.assertEqual(result["keysWhileOpen"], 1)
        self.assertEqual(result["keysAfterCancel"], 0)

    def test_signing_out_removes_every_stored_draft(self):
        """The most personal content in the product does not survive a sign-out."""
        result = panel("signing-out-removes-stored-drafts")
        self.assertEqual(result["before"], 1)
        self.assertEqual(result["after"], 0)

    def test_storage_that_throws_leaves_the_panel_working(self):
        """iOS Private Browsing raises on the property access itself."""
        result = panel("a-storage-refusal-does-not-break-the-panel")
        self.assertEqual(result["draft"], "hafızada kalsın")
        self.assertIn("task-detail", result["html"])

    def test_a_restored_draft_is_never_submitted_by_itself(self):
        """Coming back to the app sends nothing. It is text, not a message."""
        result = panel("no-draft-is-submitted-on-its-own")
        self.assertEqual(result["draft"], "kendiliğinden gitmesin")
        self.assertEqual(
            [path for path in result["writes"] if "followups" in path], []
        )

    def test_the_draft_bound_is_stated_in_source(self):
        """A `maxlength` is a hint a paste can exceed, so the writer bounds it."""
        source = tasks_code()
        self.assertIn("MAX_DRAFT_CHARS", source)
        self.assertIn("slice(0, MAX_DRAFT_CHARS)", source)


class AcceptedDraftIsCleared(unittest.TestCase):
    """Found on a real phone during M2I PR4 validation, and worse than it looked.

    **What was reported:** after an accepted follow-up produced its result, the
    sent text was still sitting in the follow-up box.

    **What it actually was:** the draft is deliberately not part of the markup —
    keeping it out is what stops the form being rebuilt under somebody on every
    poll. So ``clearDraft`` emptied memory and ``localStorage`` while the live
    textarea kept holding the accepted words, and the next ``render`` called
    ``captureDraft``, which read that node and wrote them straight back.

    The draft came back, the request id had been released with it, and the next
    tap on Send therefore submitted the same sentence under a **new** key. The
    server did exactly the right thing with a new key and different-looking
    message: it opened another turn. The validation run produced **three**
    follow-up turns from one intended message, with three distinct request ids,
    consuming real model usage — and nothing on the screen said so.

    So this is not a cosmetic class. It is the duplicate-turn class, and the
    scenarios below assert the consequence as well as the symptom.
    """

    def test_the_box_is_empty_after_an_accepted_followup(self):
        self.assertEqual(panel("an-accepted-followup-clears-everything")["box"], "")

    def test_the_stored_draft_is_gone_after_an_accepted_followup(self):
        result = panel("an-accepted-followup-clears-everything")
        self.assertEqual(result["storedBefore"], 1, "nothing was stored to begin with")
        self.assertEqual(result["keys"], [])

    def test_a_reload_does_not_bring_the_accepted_text_back(self):
        """The store is what survives a reload, so this is the durable half."""
        result = panel("an-accepted-followup-does-not-return-after-a-reload")
        self.assertEqual(result["box"], "")
        self.assertEqual(result["keys"], [])

    def test_tapping_send_again_creates_no_second_turn(self):
        """The defect's actual cost, asserted directly.

        Three taps, one message. Before the fix this produced three posts under
        three different keys, which is what the phone run recorded in
        ``task_turns``.
        """
        result = panel("a-second-tap-after-acceptance-sends-nothing")
        self.assertEqual(result["posts"], 1, result["requestIds"])
        self.assertEqual(len(set(result["requestIds"])), 1)
        self.assertEqual(result["box"], "")

    def test_text_typed_while_the_request_was_open_is_kept(self):
        """The clear must not eat somebody's next message.

        What is in the box after the server answers is not necessarily what the
        server accepted. Anything newer is a different message and survives.
        """
        result = panel("text-typed-while-in-flight-survives-acceptance")
        self.assertEqual(result["box"], "sonraki mesaj")
        self.assertEqual(result["keys"], ["cofferdam.taskdraft.followup.task_acc"])

    def test_a_refused_followup_keeps_its_box_and_its_key(self):
        """Cleared on acceptance, never on merely having received a response.

        A 409 is the case most likely to be retried, and both the words and the
        key the server would recognise the retry by have to survive it.
        """
        result = panel("a-refused-followup-still-keeps-the-box")
        self.assertEqual(result["box"], "reddedildi ama duruyor")
        self.assertEqual(result["keys"], ["cofferdam.taskdraft.followup.task_acc"])
        self.assertEqual(len(result["requestIds"]), 1)

    def test_accepting_on_one_task_leaves_another_tasks_draft(self):
        result = panel("accepting-one-task-leaves-another-tasks-draft")
        self.assertEqual(result["keys"], ["cofferdam.taskdraft.followup.task_b"])
        self.assertIn(result["boxAfterAcceptOnA"], ("", None))

    def test_an_accepted_answer_leaves_the_followup_draft_alone(self):
        """The two operations stay separate through an acceptance too."""
        result = panel("an-accepted-answer-leaves-the-followup-draft-alone")
        self.assertEqual(result["keys"], ["cofferdam.taskdraft.followup.task_sdk"])

    def test_the_clear_goes_through_one_helper_that_touches_the_node(self):
        """Structural, because the store-only clear looked correct for a week.

        A future clear that empties the store and forgets the node reproduces
        the defect exactly, and reads as fine in review. So the helper exists,
        both accepted paths go through it, and it is the thing that writes the
        node.
        """
        source = tasks_code()
        self.assertIn("function clearAcceptedDraft", source)
        self.assertEqual(source.count("clearAcceptedDraft("), 3, "one helper, two callers")
        block = source[source.index("function clearAcceptedDraft") :]
        block = block[: block.index("function applyDraft")]
        self.assertIn('box.value = ""', block, "the helper does not clear the node")
        self.assertIn("clearDraft(", block, "the helper does not clear the store")


class RequestIdentity(unittest.TestCase):
    """M2I PR4: a retry is recognisable as one.

    The defect was small and its consequence was not. One module-level slot held
    the key for every write, and it was cleared on *any* response — including a
    refusal. A refusal is exactly the moment somebody presses the button again,
    so the retry carried a fresh key and arrived at the server as a second,
    unrelated message.
    """

    def test_a_refused_followup_is_retried_under_the_same_key(self):
        result = panel("a-refused-followup-keeps-its-request-id")
        self.assertEqual(result["posts"], 2)
        self.assertEqual(
            len(set(result["requestIds"])),
            1,
            "the retry carried a new key, so the server could not recognise it",
        )

    def test_a_refused_followup_keeps_the_text_then_gives_it_up_once_accepted(self):
        """Two moments, and this test used to check only the wrong one.

        It asserted the draft was still there at the *end* of the scenario — by
        which point the retry had been **accepted**. So it passed while the panel
        held on to text the server had taken, which is the M2I PR4 phone defect,
        and would have kept passing after it. The refusal is what the draft must
        survive; the acceptance is what it must not.
        """
        result = panel("a-refused-followup-keeps-its-request-id")
        self.assertEqual(result["draftAfterRefusal"], "aynı mesaj")
        self.assertEqual(result["draftAfterAccept"], "")
        self.assertEqual(result["keysAfterAccept"], [])

    def test_edited_words_get_a_new_key(self):
        """The server binds a key to a payload hash; different words must differ."""
        ids = panel("an-edited-followup-gets-a-new-request-id")["requestIds"]
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(ids[0], ids[1])

    def test_keys_are_scoped_by_operation_and_task(self):
        source = tasks_code()
        self.assertIn("requestScope(", source)
        self.assertIn("requestIdFor(", source)
        self.assertIn("releaseRequestId(", source)


class ForegroundRefresh(unittest.TestCase):
    """M2I PR4: unlocking the phone shows the truth, not a ten-second-old copy."""

    def test_returning_to_the_app_refreshes_at_once(self):
        result = panel("foregrounding-refreshes-without-waiting")
        self.assertEqual(
            result["whileHidden"], result["afterMount"], "the panel polled while hidden"
        )
        self.assertGreater(
            result["afterForeground"],
            result["whileHidden"],
            "foregrounding did not refresh",
        )

    def test_the_refresh_is_one_read_and_not_a_new_timer(self):
        """`reschedule` stays the only thing that creates an interval."""
        result = panel("foregrounding-refreshes-without-waiting")
        self.assertLessEqual(result["afterForeground"] - result["whileHidden"], 2)


class ResultRetrieval(unittest.TestCase):
    """M2I PR4: the phone can ask what the latest completed turn produced."""

    def test_the_result_route_is_rendered_with_its_turn_facts(self):
        html = panel("the-result-route-reports-the-latest-turn")["html"]
        self.assertIn("Latest result", html)
        self.assertIn("İkinci turun sonucu.", html)
        self.assertIn("2 turns", html)
        self.assertIn("turn 2", html)

    def test_the_result_meaning_comes_from_the_server(self):
        """A second implementation of the distinction would be one too many."""
        html = panel("the-result-route-reports-the-latest-turn")["html"]
        self.assertIn("The latest completed turn's result.", html)
        self.assertIn("task still open", html)

    def test_the_provider_session_id_is_dropped_rather_than_hidden(self):
        """The route carries one; the panel must not hold it.

        Dropped at adoption rather than merely left unrendered — a field that
        never enters this panel's state is one no future render can put on a
        screen.
        """
        result = panel("the-result-route-reports-the-latest-turn")
        self.assertNotIn("3f5a6b7c", result["html"])
        self.assertEqual(result["storage"], {})
        source = tasks_code()
        self.assertNotIn("provider_session_id", source)


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
