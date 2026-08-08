"""The Remote Control panel (M2H PR3).

Two kinds of test, following the pattern the audio, Spotify, YouTube and Tasks
milestones established:

* **Behavioural**, run through ``tests/remote_harness.js`` against the shipped
  ``web/remote.js``. That the link endpoint is never polled, that a double tap
  sends one mutation, that a failed status poll does not invent a stopped host,
  that the new tab is opened inside the click gesture and closed again when
  retrieval fails — none of these is visible to a scan of the file.
* **Structural**, scanning the shipped files, for what the panel *cannot* do:
  no console call, no browser storage, no URL in an ``href``, no second caller
  of the link route, no unit name or path vocabulary.

Comments are stripped before scanning, so a guard never trips on the sentence
explaining why the rule exists.

Every URL in this file is fabricated. The confirmed *structure* is real; the
capability value is typed by hand and grants nothing.
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
REMOTE_HARNESS = REPO_ROOT / "tests" / "remote_harness.js"

FAKE_TOKEN = "FAKEfake0123456789-_TESTtok0"
FAKE_URL = "https://claude.ai/code?environment=" + FAKE_TOKEN


def remote_code() -> str:
    return code_only((WEB_DIR / "remote.js").read_text(encoding="utf-8"))


def panel(name: str) -> dict:
    node = shutil.which("node")
    if not node:  # pragma: no cover - depends on the runner
        raise unittest.SkipTest("node is not installed")
    completed = subprocess.run(
        [node, str(REMOTE_HARNESS), name],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if completed.returncode != 0:  # pragma: no cover - harness failure
        raise AssertionError(f"harness failed: {completed.stderr[:800]}")
    return json.loads(completed.stdout)


# ---------------------------------------------------------------------------
# Rendering and state
# ---------------------------------------------------------------------------


class RenderingTests(unittest.TestCase):
    def test_the_card_renders_for_a_registered_project(self) -> None:
        result = panel("stopped")
        self.assertIn("Claude adapter sandbox", result["html"])
        self.assertIn("rc-card", result["html"])

    def test_a_capability_disabled_project_cannot_start(self) -> None:
        result = panel("capability_disabled")
        self.assertTrue(result["startDisabled"])
        self.assertIn("not enabled", result["html"])

    def test_the_stopped_state_offers_start_only(self) -> None:
        result = panel("stopped")
        self.assertFalse(result["startDisabled"])
        self.assertTrue(result["openDisabled"])

    def test_the_starting_state_disables_start_and_allows_stop(self) -> None:
        result = panel("starting")
        self.assertTrue(result["startDisabled"])
        self.assertFalse(result["stopDisabled"])

    def test_awaiting_consent_is_shown_and_blocks_open(self) -> None:
        result = panel("awaiting_consent")
        self.assertIn("needs Remote Control enabled", result["html"])
        self.assertTrue(result["openDisabled"])

    def test_running_without_a_link_does_not_offer_open(self) -> None:
        result = panel("running_without_link")
        self.assertTrue(result["openDisabled"])
        self.assertIn("not published yet", result["html"])

    def test_running_with_a_link_offers_open(self) -> None:
        result = panel("running_with_link")
        self.assertFalse(result["openDisabled"])
        self.assertFalse(result["stopDisabled"])

    def test_a_failed_host_can_be_started_again_and_stopped(self) -> None:
        result = panel("failed")
        self.assertFalse(result["startDisabled"])
        self.assertFalse(result["stopDisabled"])

    def test_an_unreachable_backend_says_so(self) -> None:
        result = panel("unknown_backend")
        self.assertIn("Could not reach the workstation", result["html"])

    def test_no_raw_url_appears_in_any_rendered_state(self) -> None:
        for scenario in (
            "stopped",
            "running_with_link",
            "open_link_success",
            "url_not_retained_after_open",
        ):
            with self.subTest(scenario=scenario):
                result = panel(scenario)
                self.assertFalse(result["leak"]["urlInHtml"])
                self.assertNotIn(FAKE_TOKEN, result["html"])


# ---------------------------------------------------------------------------
# Control matrix
# ---------------------------------------------------------------------------


class ControlMatrixTests(unittest.TestCase):
    def test_a_revoked_capability_still_shows_status_and_can_stop(self) -> None:
        """Refusing to stop something is a worse permission than refusing to start it."""
        result = panel("revoked_capability_can_still_stop")
        self.assertTrue(result["startDisabled"])
        self.assertFalse(result["stopDisabled"])
        self.assertEqual(result["stopPosts"], 1)

    def test_a_double_start_tap_sends_one_request(self) -> None:
        self.assertEqual(panel("double_start")["startPosts"], 1)

    def test_a_double_stop_tap_sends_one_request(self) -> None:
        self.assertEqual(panel("double_stop")["stopPosts"], 1)

    def test_a_double_open_tap_opens_one_tab(self) -> None:
        result = panel("double_open_sends_one_request")
        self.assertEqual(result["tabsOpened"], 1)
        self.assertEqual(result["linkRequests"], 1)

    def test_open_does_nothing_when_no_link_is_available(self) -> None:
        result = panel("open_ignored_without_link")
        self.assertTrue(result["openDisabled"])
        self.assertEqual(result["linkRequests"], 0)


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


class PollingTests(unittest.TestCase):
    def test_polling_never_touches_the_link_endpoint(self) -> None:
        """The property the whole capability boundary rests on."""
        result = panel("polling_never_touches_link")
        self.assertEqual(result["linkRequests"], 0)
        self.assertGreater(result["statusRequests"], 1)

    def test_polling_pauses_while_the_page_is_hidden(self) -> None:
        result = panel("polling_stops_when_hidden")
        self.assertEqual(result["beforeHidden"], result["whileHidden"])
        self.assertGreater(result["afterVisible"], result["whileHidden"])

    def test_polling_is_cleaned_up_when_the_panel_stops(self) -> None:
        result = panel("polling_cleaned_up_on_stop")
        self.assertEqual(result["intervalsBefore"], 1)
        self.assertEqual(result["intervalsAfter"], 0)
        self.assertEqual(result["requestsGrew"], 0)

    def test_status_refreshes_promptly_after_a_mutation(self) -> None:
        result = panel("refresh_after_mutation")
        self.assertGreater(result["statusAfter"], result["statusBefore"])

    def test_a_failed_status_poll_does_not_invent_a_stopped_host(self) -> None:
        """Network failure is reported; it does not rewrite the lifecycle."""
        result = panel("status_failure_keeps_last_state")
        self.assertIn("running", result["after"])
        self.assertFalse(result["openDisabled"])
        self.assertIn("last confirmed state", result["after"])

    def test_no_timer_raised(self) -> None:
        for scenario in ("polling_never_touches_link", "polling_stops_when_hidden"):
            with self.subTest(scenario=scenario):
                self.assertEqual(panel(scenario)["timerErrors"], [])


# ---------------------------------------------------------------------------
# The explicit link-open flow
# ---------------------------------------------------------------------------


class LinkOpenTests(unittest.TestCase):
    def test_the_link_is_fetched_only_after_an_explicit_click(self) -> None:
        result = panel("open_link_success")
        self.assertEqual(result["linksBeforeClick"], 0)
        self.assertEqual(result["linkRequests"], 1)

    def test_the_tab_is_opened_inside_the_click_gesture(self) -> None:
        """Opened after the fetch resolves, a mobile browser blocks it."""
        self.assertTrue(panel("open_link_success")["tabOpenedSynchronously"])

    def test_a_successful_retrieval_navigates_only_that_tab(self) -> None:
        result = panel("open_link_success")
        self.assertEqual(result["navigatedTo"], FAKE_URL)
        self.assertFalse(result["closed"])

    def test_opener_isolation_is_applied(self) -> None:
        self.assertTrue(panel("open_link_success")["openerSevered"])

    def test_a_refused_link_closes_the_tab_and_navigates_nothing(self) -> None:
        result = panel("open_link_refused")
        self.assertTrue(result["closed"])
        self.assertIsNone(result["navigatedTo"])

    def test_a_refused_link_does_not_imply_the_host_stopped(self) -> None:
        self.assertTrue(panel("open_link_refused")["stillRunning"])

    def test_a_malformed_url_is_never_navigated_to(self) -> None:
        """Even if the server sent it. The frontend check is not the authority,
        but it is the last thing between a bad value and a navigation."""
        result = panel("open_link_rejects_malformed")
        self.assertIsNone(result["navigatedTo"])
        self.assertTrue(result["closed"])

    def test_a_network_failure_closes_the_tab(self) -> None:
        self.assertTrue(panel("open_link_network_error")["closed"])

    def test_a_blocked_popup_is_reported_and_nothing_is_navigated(self) -> None:
        result = panel("open_link_popup_blocked")
        self.assertFalse(result["granted"])
        self.assertIn("blocked the new tab", result["html"])

    def test_the_url_reaches_no_storage_no_log_and_no_markup(self) -> None:
        for scenario in ("open_link_success", "url_not_retained_after_open"):
            with self.subTest(scenario=scenario):
                leak = panel(scenario)["leak"]
                self.assertFalse(leak["urlInHtml"])
                self.assertFalse(leak["urlInStorage"])
                self.assertFalse(leak["urlInConsole"])
                self.assertEqual(leak["storageWriteCount"], 0)
                self.assertEqual(leak["consoleCount"], 0)

    def test_the_url_is_not_retained_after_the_navigation(self) -> None:
        self.assertFalse(panel("url_not_retained_after_open")["htmlAfter"])


# ---------------------------------------------------------------------------
# Structural guards on the shipped files
# ---------------------------------------------------------------------------


class StructureTests(unittest.TestCase):
    def test_the_panel_never_logs(self) -> None:
        self.assertNotIn("console", remote_code())

    def test_the_panel_never_touches_browser_storage(self) -> None:
        code = remote_code()
        for forbidden in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
            with self.subTest(api=forbidden):
                self.assertNotIn(forbidden, code)

    def test_there_is_exactly_one_caller_of_the_link_endpoint(self) -> None:
        """A second one would be a second way to fetch a capability."""
        self.assertEqual(len(re.findall(r'"/link"', remote_code())), 1)

    def test_no_url_is_ever_placed_in_an_anchor(self) -> None:
        code = remote_code()
        self.assertNotIn("href=", code)
        self.assertNotIn("<a ", code)

    def test_the_panel_cannot_name_a_unit_path_or_argv(self) -> None:
        code = remote_code()
        for forbidden in ("systemctl", "cofferdam-rc@", "/home/", "--spawn", "execut", "argv"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, code)

    def test_every_request_is_built_from_a_project_id(self) -> None:
        code = remote_code()
        self.assertIn("encodeURIComponent(", code)
        self.assertNotIn("/api/remote-control/\" + selectedId", code)

    def test_no_prefetch_or_prerender_hint_exists(self) -> None:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        for forbidden in ('rel="prefetch"', 'rel="prerender"', 'rel="preload"', "dns-prefetch"):
            with self.subTest(hint=forbidden):
                self.assertNotIn(forbidden, html)

    def test_the_page_suppresses_the_referrer(self) -> None:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('<meta name="referrer" content="no-referrer">', html)

    def test_the_panel_is_registered_and_torn_down_by_the_app(self) -> None:
        app = code_only((WEB_DIR / "app.js").read_text(encoding="utf-8"))
        self.assertIn("CofferdamRemote.mount", app)
        self.assertIn("CofferdamRemote.stop", app)

    def test_the_frontend_link_contract_matches_the_backend(self) -> None:
        """Mirrored, never loosened. The backend stays the authority."""
        from cofferdam.workstation.sessions import links

        code = remote_code()
        self.assertIn('"' + links.LINK_PATH + '"', code)
        self.assertIn('"' + links.LINK_QUERY_KEY + '"', code)
        for host in links.ALLOWED_LINK_HOSTS:
            with self.subTest(host=host):
                self.assertIn("https://" + host, code)
        self.assertIn(
            "{%d,%d}" % (links.LINK_TOKEN_MIN_CHARS, links.LINK_TOKEN_MAX_CHARS), code
        )

    def test_the_service_worker_cannot_cache_the_link_response(self) -> None:
        """``no-store`` is the server's half; this is the client's.

        A service worker that answered from a cache could keep a capability URL
        on the phone after the session it opened was gone. Cofferdam's worker is
        network-only, and this asserts it stays that way.
        """
        worker = code_only((WEB_DIR / "sw.js").read_text(encoding="utf-8"))
        self.assertNotIn("respondWith", worker)
        self.assertNotIn("caches", worker)

    def test_the_card_does_not_scroll_sideways_on_a_phone(self) -> None:
        css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".rc-actions { display: flex; flex-wrap: wrap;", css)
        self.assertIn("overflow-wrap: anywhere", css)

    def test_the_controls_are_reachable_and_labelled(self) -> None:
        result = panel("running_with_link")
        html = result["html"]
        self.assertIn('aria-disabled="true"', panel("stopped")["html"])
        self.assertIn('role="status"', panel("unknown_backend")["html"])
        self.assertIn("Open Remote Control", html)

    def test_the_security_boundary_is_stated_without_overclaiming(self) -> None:
        result = panel("stopped")
        html = result["html"]
        self.assertIn("does not read, store or mirror the conversation", html)
        self.assertIn("does not revoke", html)
        for overclaim in ("revoked", "rotated", "invalidated"):
            with self.subTest(word=overclaim):
                self.assertNotIn("link " + overclaim, html.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
