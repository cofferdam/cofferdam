"""The YouTube player panel and the video result cards (M2E 38–42, 47–50).

Two kinds of test, following the pattern the audio and Spotify milestones
established:

* **Behavioural**, run through ``tests/youtube_harness.js`` against the shipped
  ``web/youtube.js`` and through ``tests/pwa_harness.js`` against the shipped
  ``web/app.js``. Double submission, false success, a request that never
  answers, polling that must stop, and — the one this milestone cares most about
  — an older poll response losing to a newer verified one are all control-flow
  properties, and a scan of the files cannot see any of them.
* **Structural**, scanning the shipped files, for what the code *cannot* do —
  no optimistic write, no console call, no URL or video id in the client's
  vocabulary, no horizontal overflow.

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
YOUTUBE_HARNESS = REPO_ROOT / "tests" / "youtube_harness.js"


def youtube_code() -> str:
    return code_only((WEB_DIR / "youtube.js").read_text(encoding="utf-8"))


def app_code() -> str:
    return code_only((WEB_DIR / "app.js").read_text(encoding="utf-8"))


def player_page_code() -> str:
    return code_only((WEB_DIR / "player.js").read_text(encoding="utf-8"))


def _run(harness: Path, name: str) -> dict:
    node = shutil.which("node")
    if not node:  # pragma: no cover - depends on the runner
        raise unittest.SkipTest("node is not installed")
    completed = subprocess.run(
        [node, str(harness), name], capture_output=True, text=True, timeout=90, check=False
    )
    if completed.returncode != 0:  # pragma: no cover - harness failure
        raise AssertionError(f"harness failed: {completed.stderr[:800]}")
    payload = json.loads(completed.stdout)
    assert not payload.get("uncaught"), payload.get("uncaught")
    assert not payload.get("timerErrors"), payload.get("timerErrors")
    return payload


def panel(name: str) -> dict:
    return _run(YOUTUBE_HARNESS, name)


# -- 49: the states a phone has to be able to show ---------------------------


class PanelStates(unittest.TestCase):
    def test_a_connected_player_shows_what_is_playing(self):
        result = panel("connected")
        html = result["html"]
        self.assertIn("player open", html)
        self.assertIn("Gönül Dağı", html)
        self.assertIn("Neşet Ertaş", html)
        self.assertIn("1:01 / 4:00", html)
        # And the reading is dated rather than presented as live.
        self.assertIn("at last check", html)

    def test_a_closed_player_says_so_and_offers_to_open_one(self):
        """49. Player-closed is a clear state, not an empty panel."""
        html = panel("player-closed")["html"]
        self.assertIn("player closed", html)
        self.assertIn("No Cofferdam YouTube player is open", html)
        self.assertIn("Open player on workstation", html)

    def test_controls_are_disabled_when_no_player_is_connected(self):
        """Every transport control is unavailable rather than misleading."""
        html = panel("player-closed")["html"]
        for control in ("youtubePrevious", "youtubePlayPause", "youtubeNext",
                        "youtubeVolume", "youtubeMute"):
            pattern = re.compile(re.escape(control) + r'"[^>]*disabled')
            self.assertRegex(html, pattern, control + " was not disabled")

    def test_a_host_without_a_browser_says_that_instead(self):
        """"Unavailable" and "closed" are different facts and read differently."""
        html = panel("unavailable")["html"]
        self.assertIn("unavailable on this host", html)
        self.assertIn("no browser Cofferdam can open a player in", html)
        # And it does not offer a button that could only fail.
        self.assertNotIn("Open player on workstation", html)

    def test_autoplay_blocked_explains_the_one_click(self):
        """49. Blocked playback is explained, and blamed on the right thing."""
        html = panel("autoplay-blocked")["html"]
        self.assertIn("will not start sound until the player window is clicked once", html)
        self.assertIn("Enable playback", html)
        self.assertIn("browser rule, not a Cofferdam setting", html)

    def test_the_queue_expands_and_names_the_current_item(self):
        result = panel("queue-expanded")
        self.assertNotIn("First", result["collapsed"])
        self.assertIn("Queue (2 of 25)", result["collapsed"])
        expanded = result["expanded"]
        self.assertIn("First", expanded)
        self.assertIn("Second", expanded)
        self.assertIn("playing now", expanded)
        self.assertIn("Remove", expanded)

    def test_an_empty_queue_explains_what_next_will_do(self):
        html = panel("connected")["html"]
        self.assertIn("Queue (0 of 25)", html)


# -- no false success --------------------------------------------------------


class NoFalseSuccess(unittest.TestCase):
    def test_a_refused_action_is_shown_as_the_refusal(self):
        result = panel("refused-action-is-not-success")
        html = result["html"]
        self.assertIn("the YouTube player tab closed", html)
        self.assertIn("press Play now again", html)
        # Exactly one write was attempted, and the panel did not claim it worked.
        self.assertEqual(len(result["writes"]), 1)

    def test_an_unobserved_outcome_is_not_upgraded(self):
        """16/17. The server said blocked; the panel says blocked."""
        html = panel("partial-outcome-is-not-success")["html"]
        self.assertIn("will not start sound", html)
        self.assertNotIn(">Pause<", html)   # it is not rendered as playing

    def test_the_panel_never_writes_state_before_the_server_answers(self):
        """Structural: no assignment to the snapshot outside `adopt`."""
        source = youtube_code()
        # `\s*=(?!=)` so a comparison (`snapshot === null`) is not counted as an
        # assignment — the point is where state is *written*, not where it is read.
        assignments = re.findall(r"\bsnapshot\s*=(?!=)", source)
        # The declaration, the two `= null` resets in load's failure paths, the
        # one in stop, and the single write in adopt. Five, and no optimistic
        # write anywhere: nothing sets it from a request that has not answered.
        self.assertEqual(len(assignments), 5, assignments)
        adopt_body = source.split("function adopt")[1][:400]
        self.assertIn("appliedGeneration", adopt_body)


# -- 42: one action at a time ------------------------------------------------


class DuplicateSubmission(unittest.TestCase):
    def test_three_taps_send_one_request(self):
        """42."""
        result = panel("double-submission")
        self.assertEqual(result["before"], 0)
        self.assertEqual(result["after"], 1, result["writes"])

    def test_a_request_that_never_answers_gives_the_panel_back(self):
        """A bounded pending state, so nobody has to reload the page."""
        result = panel("hung-request-gives-the-panel-back")
        self.assertIn("disabled", result["during"])
        self.assertIn("did not finish in time", result["after"])
        self.assertIn("cannot say whether it worked", result["after"])


# -- 38-41: response ordering ------------------------------------------------


class ResponseOrdering(unittest.TestCase):
    def test_an_older_poll_cannot_overwrite_a_newer_video(self):
        """38. The failure the Spotify milestone found, prevented here."""
        result = panel("stale-poll-cannot-overwrite-a-newer-video")
        self.assertIn("The new video", result["afterWrite"])
        self.assertIn(
            "The new video",
            result["afterStalePoll"],
            "a poll issued earlier overwrote the newly verified video",
        )
        self.assertNotIn("The old video", result["afterStalePoll"])

    def test_an_older_poll_cannot_overwrite_a_newer_volume(self):
        """39."""
        result = panel("stale-poll-cannot-overwrite-a-newer-volume")
        self.assertIn("80%", result["afterWrite"])
        self.assertIn(
            "80%",
            result["afterStalePoll"],
            "a poll issued earlier overwrote the newly verified volume",
        )

    def test_polling_pauses_while_a_write_is_confirmed(self):
        """40."""
        result = panel("polling-pauses-during-a-write")
        self.assertEqual(
            result["readsBefore"],
            result["readsAfter"],
            "a state poll ran while a write was being confirmed",
        )

    def test_polling_stops_while_the_tab_is_hidden(self):
        """41. A phone in a pocket is not asking for anything."""
        result = panel("poll-stops-while-hidden")
        self.assertEqual(
            result["whileHidden"],
            result["afterMount"],
            "the panel kept polling while hidden",
        )
        self.assertGreater(result["afterVisible"], result["whileHidden"])

    def test_polling_stops_after_sign_out(self):
        """41. Signing out ends every request and forgets what was playing."""
        result = panel("poll-stops-on-stop")
        self.assertEqual(result["intervalsWhileMounted"], 1)
        self.assertEqual(result["intervalsAfterStop"], 0)
        self.assertEqual(result["afterStop"], result["mounted"])
        self.assertFalse(result["connected"])
        # And the panel is emptied rather than left showing the last video.
        self.assertNotIn("Gönül Dağı", result["html"])


# -- what the client may name ------------------------------------------------


class ClientVocabulary(unittest.TestCase):
    def test_every_request_carries_only_server_issued_handles(self):
        result = panel("requests-carry-only-handles")
        for write in result["writes"]:
            body = write["body"] or {}
            self.assertEqual(
                set(body) <= {"volume_percent", "muted"},
                True,
                "an unexpected field was sent: " + str(body),
            )
            blob = json.dumps(write)
            for forbidden in ("youtube.com", "watch?v=", "videoId", "video_id",
                              "playVideo", "<script", "javascript:"):
                self.assertNotIn(forbidden, blob, forbidden + " reached a request")

    def test_the_play_and_queue_paths_are_built_from_handles(self):
        result = panel("requests-carry-only-handles")
        paths = [write["path"] for write in result["writes"]]
        self.assertIn(
            "/api/media/searches/search-abc/results/r2/youtube/play", paths
        )
        self.assertIn(
            "/api/media/searches/search-abc/results/r3/youtube/queue", paths
        )

    def test_queue_removal_sends_the_issued_handle(self):
        result = panel("queue-removal-sends-the-handle")
        self.assertEqual(
            [write["path"] for write in result["writes"]],
            ["/api/youtube/player/queue/ytq-a"],
        )

    def test_dragging_the_slider_sends_nothing_until_it_is_released(self):
        result = panel("volume-drag-does-not-send-per-pixel")
        self.assertEqual(result["duringDrag"], 0)
        self.assertEqual(result["afterCommit"], 1)
        self.assertEqual(result["writes"][0]["body"], {"volume_percent": 70})

    def test_no_url_or_video_id_appears_in_the_panel_source(self):
        source = youtube_code()
        for forbidden in ("youtube.com", "watch?v=", "youtu.be", "/embed/",
                          "videoId", "playVideo", "loadVideoById"):
            self.assertNotIn(forbidden, source, forbidden + " is in youtube.js")


# -- 46: nothing is logged ---------------------------------------------------


class Privacy(unittest.TestCase):
    def test_the_panel_never_logs(self):
        """46. A browser console is a surface neither of us controls."""
        self.assertNotIn("console.", youtube_code())

    def test_the_panel_stores_nothing(self):
        source = youtube_code()
        for forbidden in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
            self.assertNotIn(forbidden, source, forbidden + " is used")

    def test_the_player_page_never_logs_or_stores(self):
        source = player_page_code()
        for forbidden in ("console.", "localStorage", "sessionStorage",
                          "document.cookie", "indexedDB"):
            self.assertNotIn(forbidden, source, forbidden + " is used in player.js")


# -- 47-48: the result cards -------------------------------------------------


class ResultCards(unittest.TestCase):
    def test_video_results_expose_play_queue_and_open(self):
        """47."""
        source = app_code()
        self.assertIn("data-youtube-play", source)
        self.assertIn("data-youtube-queue", source)
        self.assertIn("Play now", source)
        self.assertIn("Add to queue", source)
        self.assertIn("Open in YouTube", source)

    def test_non_video_results_do_not_expose_player_actions(self):
        """48. The guard is a result-type check, not a hope."""
        source = app_code()
        block = source.split("function youtubeResultActions")[1][:700]
        self.assertIn('result.result_type !== "video"', block)
        self.assertIn("return \"\"", block)

    def test_spotify_results_do_not_expose_youtube_actions(self):
        """11 at the UI layer: the provider check comes first."""
        source = app_code()
        block = source.split("function youtubeResultActions")[1][:700]
        self.assertIn("providerId !== YOUTUBE_PROVIDER_ID", block)

    def test_the_result_actions_are_visible_buttons_not_a_menu(self):
        """Play now is the thing someone came to press."""
        source = app_code()
        block = source.split("function youtubeResultActions")[1][:900]
        self.assertIn('class="mr-play primary"', block)
        self.assertNotIn("<details", block)
        self.assertNotIn("<select", block)

    def test_the_card_sends_only_handles(self):
        source = app_code()
        block = source.split("function youtubeResultAction(")[1][:1400]
        self.assertIn("state.searchId", block)
        self.assertIn("resultId", block)
        for forbidden in ("youtube.com", "video_id", "watch?v="):
            self.assertNotIn(forbidden, block)

    def test_the_card_never_upgrades_the_outcome(self):
        """A queued video must not toast as though it were playing."""
        source = app_code()
        block = source.split("function youtubeResultAction(")[1][:1400]
        self.assertIn('outcome.outcome === "applied"', block)
        self.assertIn('outcome.outcome === "queued"', block)

    def test_a_second_tap_on_a_card_sends_nothing(self):
        source = app_code()
        block = source.split("function youtubeResultAction(")[1][:800]
        self.assertIn("if (mediaPending[providerId]) { return; }", block)


# -- 50: phone and tablet layout ---------------------------------------------


class Layout(unittest.TestCase):
    def setUp(self) -> None:
        self.css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
        self.rules = [
            block for block in self.css.split("}")
            if ".yt-" in block.split("{")[0]
        ]

    def test_every_flex_row_can_shrink(self):
        """50. `min-width: 0` is what actually prevents sideways overflow.

        A flex item's default `min-width: auto` refuses to shrink below its
        content, so one unbroken video title pushes the whole page sideways. This
        asserts the property rather than eyeballing it.
        """
        for block in self.rules:
            if "display: flex" in block or "display: grid" in block:
                selector = block.split("{")[0].strip()
                self.assertIn("min-width: 0", block, selector + " cannot shrink")

    def test_long_text_wraps_rather_than_overflowing(self):
        for selector in (".yt-title", ".yt-meta", ".yt-queue-title"):
            block = next(b for b in self.rules if selector in b.split("{")[0])
            self.assertIn("overflow-wrap: anywhere", block, selector)

    def test_touch_targets_are_large_enough(self):
        """A phone control smaller than a fingertip is a control that misfires."""
        for block in self.rules:
            if "button" in block.split("{")[0] and "min-height" in block:
                match = re.search(r"min-height:\s*(\d+)px", block)
                if match:
                    self.assertGreaterEqual(int(match.group(1)), 40, block.split("{")[0])

    def test_the_panel_has_a_narrow_screen_layout(self):
        self.assertIn("@media (max-width: 480px)", self.css)
        narrow = self.css.split("@media (max-width: 480px)")[-1]
        self.assertIn(".yt-volume", narrow)

    def test_the_separation_from_computer_audio_is_stated_in_the_panel(self):
        """Keeping the three volumes visually and verbally distinct."""
        html = panel("connected")["html"]
        self.assertIn("does not change this computer", html)
        self.assertIn("Audio", html)
        self.assertIn("does not change Spotify", html)


class PanelSeparation(unittest.TestCase):
    def test_the_shell_keeps_three_distinct_panels(self):
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        for panel_id in ("audioPanel", "spotifyPanel", "youtubePanel"):
            self.assertIn('id="' + panel_id + '"', html)
        self.assertIn("<h2>YouTube player</h2>", html)

    def test_the_youtube_panel_is_loaded_by_the_shell(self):
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('<script src="/youtube.js"></script>', html)

    def test_signing_out_stops_the_youtube_panel(self):
        source = app_code()
        self.assertIn("global.CofferdamYouTube.stop()", source)

    def test_the_panel_is_mounted_independently_of_the_others(self):
        """A player that cannot be reached must not take the page down."""
        source = app_code()
        block = source.split("global.CofferdamYouTube.mount")[1][:300]
        self.assertIn(".catch(", block)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
