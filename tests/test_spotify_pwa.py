"""The Spotify player panel and the track result cards (M2D 51–56).

Two kinds of test, following the pattern the audio milestone established:

* **Behavioural**, run through ``tests/spotify_harness.js`` against the shipped
  ``web/spotify.js`` and through ``tests/pwa_harness.js`` against the shipped
  ``web/app.js``. Double submission, false success, a request that never answers
  and polling that must stop are all control-flow properties, and a scan of the
  files cannot see any of them.
* **Structural**, scanning the shipped files, for what the code *cannot* do —
  no optimistic write, no console call, no fabricated restore volume.

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
SPOTIFY_HARNESS = REPO_ROOT / "tests" / "spotify_harness.js"
PWA_HARNESS = REPO_ROOT / "tests" / "pwa_harness.js"


def spotify_code() -> str:
    return code_only((WEB_DIR / "spotify.js").read_text(encoding="utf-8"))


def app_code() -> str:
    return code_only((WEB_DIR / "app.js").read_text(encoding="utf-8"))


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


def player(name: str) -> dict:
    return _run(SPOTIFY_HARNESS, name)


def page(name: str) -> dict:
    return _run(PWA_HARNESS, name)


class AccountStateTests(unittest.TestCase):
    """Every connection state gets its own words and its own next step."""

    def test_disconnected_offers_authorization_on_the_workstation(self) -> None:
        html = player("disconnected")["html"]
        self.assertIn("Spotify account not connected", html)
        self.assertIn('id="spotifyAuthorize"', html)
        self.assertIn("Authorize on workstation", html)
        # The reason the phone cannot finish it, said before it is tried.
        self.assertIn("Opera on the workstation", html)
        self.assertIn("loopback", html)

    def test_disconnected_says_premium_is_needed_for_playback_and_not_for_search(self) -> None:
        html = player("disconnected")["html"]
        self.assertIn("Premium", html)
        self.assertIn("Searching the catalogue does not", html)

    def test_premium_required_is_its_own_state_with_no_authorize_button(self) -> None:
        """Reconnecting cannot fix a subscription tier, so it is not offered."""
        html = player("premium-required")["html"]
        self.assertIn("requires a Premium account", html)
        self.assertNotIn('id="spotifyAuthorize"', html)
        self.assertIn("Open in Spotify still works", html)

    def test_missing_scopes_names_the_missing_permission(self) -> None:
        html = player("missing-scopes")["html"]
        self.assertIn("missing permissions", html)
        self.assertIn("user-modify-playback-state", html)
        self.assertIn('id="spotifyAuthorize"', html)

    def test_connected_shows_the_player_and_a_disconnect_control(self) -> None:
        html = player("connected")["html"]
        self.assertIn('id="spotifyDisconnect"', html)
        self.assertIn('id="spotifyPlayPause"', html)
        self.assertIn('id="spotifyPrevious"', html)
        self.assertIn('id="spotifyNext"', html)
        self.assertIn('id="spotifyVolume"', html)
        self.assertIn('id="spotifyMute"', html)
        self.assertIn('id="spotifyDevice"', html)

    def test_connected_shows_the_track_artist_and_progress(self) -> None:
        html = player("connected")["html"]
        self.assertIn("Gönül Dağı", html)
        self.assertIn("Neşet Ertaş", html)
        self.assertIn("1:01", html)
        self.assertIn("4:00", html)

    def test_no_active_device_is_truthful_and_claims_nothing_started(self) -> None:
        """Check 22 in the UI."""
        html = player("no-active-device")["html"]
        self.assertIn("no active device", html)
        self.assertIn("Nothing has been started", html)
        # Transport controls are not offered for a player that does not exist.
        self.assertNotIn('id="spotifyPlayPause"', html)
        # But the devices that do exist are, so the user can choose one.
        self.assertIn('id="spotifyDevice"', html)

    def test_a_restricted_device_disables_the_controls_it_cannot_serve(self) -> None:
        """Check 26 in the UI, from the documented device fields."""
        html = player("restricted-device")["html"]
        self.assertIn('id="spotifyPlayPause" class="sp-btn primary" disabled', html)
        self.assertIn("does not report volume control", html)
        self.assertNotIn('id="spotifyVolume"', html)
        self.assertIn("remote control not allowed", html)


class AuthorizationUxTests(unittest.TestCase):
    """Checks 55 and the authorization flow the milestone specifies."""

    def test_starting_authorization_shows_the_workstation_instruction(self) -> None:
        result = player("authorize-start")
        self.assertIn("Authorize on workstation", result["beforeHtml"])
        self.assertTrue(result["started"])
        self.assertIn("Complete authorization in Opera on the workstation", result["html"])
        self.assertIn('id="spotifyCancelAuth"', result["html"])

    def test_starting_authorization_posts_and_sends_no_fields(self) -> None:
        result = player("authorize-start")
        writes = [r for r in result["requests"] if r["method"] != "GET"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["path"], "/api/spotify/authorize")
        self.assertEqual(writes[0]["method"], "POST")
        self.assertEqual(writes[0]["body"], {})

    def test_a_pending_attempt_expires_instead_of_hanging(self) -> None:
        """Check 55. The panel must come back to a state with a button."""
        result = player("authorize-expires")
        self.assertIn("Complete authorization in Opera", result["pendingHtml"])
        self.assertIn("Authorize on workstation", result["html"])
        self.assertNotIn('id="spotifyCancelAuth"', result["html"])
        # And it says what happened, rather than silently reverting.
        self.assertIn("not completed in time", result["html"])
        self.assertIn("Nothing was changed", result["html"])

    def test_cancelling_uses_delete_and_returns_to_the_authorize_button(self) -> None:
        result = player("authorize-cancel")
        writes = [r for r in result["requests"] if r["method"] != "GET"]
        self.assertEqual(writes[0]["method"], "DELETE")
        self.assertEqual(writes[0]["path"], "/api/spotify/authorize")
        self.assertIn("Authorize on workstation", result["html"])

    def test_the_panel_never_renders_a_token_or_an_authorization_url(self) -> None:
        for scenario in ("disconnected", "authorize-start", "authorize-cancel", "connected"):
            with self.subTest(scenario=scenario):
                blob = json.dumps(player(scenario), ensure_ascii=False)
                for leak in ("accounts.spotify.com", "code_verifier", "code_challenge",
                             "client_secret", "Bearer ", "refresh_token"):
                    self.assertNotIn(leak, blob)


class ObservedStateTests(unittest.TestCase):
    """Nothing on this panel is drawn from what the user asked for."""

    def test_pause_renders_the_observed_state(self) -> None:
        result = player("pause-observed")
        self.assertIn(">Play</button>", result["html"])
        writes = [r for r in result["requests"] if r["method"] != "GET"]
        self.assertEqual(writes[0]["path"], "/api/spotify/player/pause")

    def test_a_pause_spotify_ignored_does_not_render_as_paused(self) -> None:
        """Check 38: the button stays on Pause because it is still playing."""
        html = player("pause-not-observed")["html"]
        self.assertIn(">Pause</button>", html)
        self.assertIn("still playing", html)

    def test_the_slider_shows_the_observed_volume_after_a_change(self) -> None:
        result = player("volume-observed")
        self.assertIn('id="spotifyVolume" min="0" max="100" step="1" value="25"', result["html"])
        writes = [r for r in result["requests"] if r["method"] != "GET"]
        self.assertEqual(writes[0]["method"], "PUT")
        self.assertEqual(writes[0]["body"], {"volume_percent": 25})

    def test_a_refused_volume_change_shows_the_reason_and_the_real_value(self) -> None:
        html = player("volume-refused")["html"]
        self.assertIn("does not support volume control", html)
        self.assertIn('value="60"', html)
        self.assertNotIn('value="70"', html)

    def test_mute_renders_as_muted_by_cofferdam_not_as_a_spotify_mute(self) -> None:
        """Check 45 in the UI."""
        result = player("mute")
        self.assertIn("Mute Spotify", result["beforeHtml"])
        self.assertIn("Unmute Spotify", result["html"])
        self.assertIn("Muted by Cofferdam", result["html"])
        self.assertIn("volume to zero", result["html"])
        self.assertIn("Spotify has no mute of its own", result["html"])

    def test_the_panel_says_mute_does_not_touch_the_computer_volume(self) -> None:
        """Check 50 in the UI: the two controls are named apart."""
        html = player("connected")["html"]
        self.assertIn("does not touch this computer's own volume", html)
        self.assertIn("Audio panel", html)

    def test_unmute_with_no_known_level_shows_the_refusal(self) -> None:
        """Check 46/47 in the UI: no level is invented, and it says why."""
        html = player("unmute-unknown")["html"]
        self.assertIn("does not know what volume to restore", html)
        self.assertIn("set a volume directly", html)

    def test_transfer_reports_the_observed_device_and_no_pipewire_claim(self) -> None:
        """Check 49 in the UI."""
        result = player("transfer")
        writes = [r for r in result["requests"] if r["method"] != "GET"]
        self.assertEqual(writes[0]["path"], "/api/spotify/player/device")
        self.assertEqual(writes[0]["body"], {"device_resource_id": "spdev-bbb", "play": False})
        self.assertIn("now playing through Kitchen", result["html"])
        lowered = result["html"].lower()
        for claim in ("pipewire", "output changed", "system volume"):
            self.assertNotIn(claim, lowered)

    def test_a_stale_device_handle_shows_the_refusal_and_refreshes(self) -> None:
        """Check 24 in the UI."""
        result = player("stale-device")
        self.assertIn("not available right now", result["html"])
        self.assertIn("refresh and retry", result["html"])
        # A refusal re-reads, because the commonest cause is a stale page.
        self.assertTrue(any(r["path"].endswith("refresh=true") for r in result["requests"]))


class PendingStateTests(unittest.TestCase):
    """Check 53, and the bound that gives the panel back."""

    def test_a_second_tap_sends_no_second_request(self) -> None:
        result = player("double-submit")
        self.assertEqual(result["writeCount"], 1)
        self.assertIn("disabled", result["duringHtml"])

    def test_a_request_that_never_answers_still_returns_the_panel(self) -> None:
        result = player("pending-bound")
        self.assertIn("disabled", result["stuckHtml"])
        self.assertNotIn('id="spotifyPlayPause" class="sp-btn primary" disabled', result["html"])
        self.assertIn("did not finish in time", result["html"])
        self.assertIn("cannot say whether it worked", result["html"])


class PollingTests(unittest.TestCase):
    """Check 54: a hidden tab and a signed-out device make no requests."""

    def test_polling_stops_while_the_document_is_hidden(self) -> None:
        result = player("poll-hidden")
        self.assertEqual(result["whileHidden"], result["afterMount"])
        self.assertGreater(result["afterVisible"], result["whileHidden"])

    def test_stopping_clears_the_timer_and_the_state(self) -> None:
        result = player("poll-stops-on-stop")
        self.assertEqual(result["intervalsWhileMounted"], 1)
        self.assertEqual(result["intervalsAfterStop"], 0)
        self.assertEqual(result["afterStop"], result["mounted"])
        self.assertFalse(result["connected"])
        # And nothing about the account survives the sign-out.
        self.assertNotIn("Gönül", result["html"])
        self.assertNotIn("Efe", result["html"])

    def test_the_poll_interval_is_conservative(self) -> None:
        code = spotify_code()
        interval = int(re.search(r"var POLL_MS = (\d+);", code).group(1))
        self.assertGreaterEqual(interval, 10000, "polling an account faster than this is rude")

    def test_the_panel_never_writes_to_the_console(self) -> None:
        """Check 29 in the browser: what is playing must not enter a console log."""
        code = spotify_code()
        self.assertNotIn("console.", code)
        for scenario in ("connected", "pause-observed", "play-result", "volume-observed"):
            with self.subTest(scenario=scenario):
                self.assertEqual(player(scenario)["consoleOutput"], [])


class ResultActionTests(unittest.TestCase):
    """Checks 51 and 52, driven through the real search-and-render path."""

    def test_a_track_card_offers_play_now_add_to_queue_and_open(self) -> None:
        html = page("media_results_spotify_connected")["mediaCardsHtml"]
        card = re.search(r'<li class="mr-item">(?:(?!</li>).)*mres-track.*?</li>', html, re.S)
        self.assertIsNotNone(card, "no track card was rendered")
        card = card.group(0)
        self.assertIn("Play now", card)
        self.assertIn("Add to queue", card)
        self.assertIn("Open in Spotify", card)

    def test_a_non_track_spotify_card_offers_only_open(self) -> None:
        html = page("media_results_spotify_connected")["mediaCardsHtml"]
        for result_id in ("mres-album", "mres-artist"):
            with self.subTest(result_id=result_id):
                card = re.search(
                    r'<li class="mr-item">(?:(?!</li>).)*' + result_id + r'.*?</li>', html, re.S
                ).group(0)
                self.assertIn("Open in Spotify", card)
                self.assertNotIn("Play now", card)
                self.assertNotIn("Add to queue", card)
                self.assertNotIn("data-spotify-play", card)

    def test_a_youtube_card_offers_no_spotify_playback(self) -> None:
        html = page("media_results_youtube")["mediaCardsHtml"]
        self.assertIn("mres-video", html)
        self.assertNotIn("data-spotify-play", html)
        self.assertNotIn("data-spotify-queue", html)
        self.assertNotIn("Play now", html)

    def test_without_a_connected_account_the_buttons_are_disabled_and_explained(self) -> None:
        payload = page("media_results_spotify_disconnected")
        html = payload["mediaCardsHtml"]
        self.assertIn('data-spotify-play="spotify" data-result-id="mres-track" disabled', html)
        self.assertIn('data-spotify-queue="spotify" data-result-id="mres-track" disabled', html)
        self.assertIn("Connect your Spotify account", html)
        # Open still works, because it never needed an authorization.
        self.assertIn("Open in Spotify", html)

    def test_play_now_sends_only_a_search_id_and_a_result_id(self) -> None:
        """Checks 31 and 32: no URI, no track id, no device — there is no field."""
        payload = page("spotify_play_result_click")
        self.assertEqual(
            payload["spotifyCalls"],
            [{"call": "playResult", "searchId": "msrch-spotify", "resultId": "mres-track"}],
        )

    def test_add_to_queue_routes_to_the_queue_call(self) -> None:
        payload = page("spotify_queue_result_click")
        self.assertEqual(
            payload["spotifyCalls"],
            [{"call": "queueResult", "searchId": "msrch-spotify", "resultId": "mres-track"}],
        )

    def test_a_double_tap_on_play_now_sends_one_request(self) -> None:
        """Check 53 on the card, not only on the panel."""
        payload = page("spotify_play_result_double_click")
        self.assertEqual(len(payload["spotifyCalls"]), 1)

    def test_the_page_never_constructs_a_spotify_uri(self) -> None:
        code = app_code() + spotify_code()
        self.assertNotIn("spotify:track:", code)
        self.assertNotIn("spotify:album:", code)
        self.assertNotIn("open.spotify.com", code)
        self.assertNotIn("api.spotify.com", code)

    def test_the_page_never_sends_a_device_id_field(self) -> None:
        code = app_code() + spotify_code()
        self.assertNotIn("device_id:", code)
        self.assertNotIn('"device_id"', code)
        self.assertIn("device_resource_id", code)

    def test_the_page_has_no_vocabulary_for_a_token_or_a_code(self) -> None:
        """Check 19 from the client side: it cannot ask, so it cannot send."""
        code = spotify_code()
        for forbidden in ("access_token", "refresh_token", "code_verifier", "authorization_code",
                          "redirect_uri", "client_secret"):
            self.assertNotIn(forbidden, code)


class PanelSeparationTests(unittest.TestCase):
    """Check 50: two panels, two headings, and no shared control."""

    def setUp(self) -> None:
        self.html = (WEB_DIR / "index.html").read_text(encoding="utf-8")

    def test_the_spotify_panel_exists_and_is_labelled(self) -> None:
        self.assertIn('id="spotifyPanel"', self.html)
        self.assertIn("<h2>Spotify player</h2>", self.html)
        self.assertIn('id="spotifySections"', self.html)
        self.assertIn('id="spotifyRefresh"', self.html)

    def test_the_audio_panel_is_still_there_and_still_separate(self) -> None:
        self.assertIn('id="audioPanel"', self.html)
        self.assertIn("<h2>Audio</h2>", self.html)

    def test_the_page_says_which_volume_is_which(self) -> None:
        self.assertIn("This is not the Audio panel", self.html)
        self.assertIn("this computer's speaker volume", self.html)

    def test_the_script_is_loaded_before_app_js(self) -> None:
        """app.js mounts it, so it has to already exist when app.js runs."""
        self.assertLess(
            self.html.index('src="/spotify.js"'), self.html.index('src="/app.js"')
        )

    def test_the_two_panels_share_no_element_id(self) -> None:
        spotify_ids = set(re.findall(r'id="(spotify[^"]*)"', self.html))
        audio_ids = set(re.findall(r'id="(audio[^"]*)"', self.html))
        self.assertTrue(spotify_ids)
        self.assertTrue(audio_ids)
        self.assertEqual(spotify_ids & audio_ids, set())

    def test_the_spotify_module_touches_no_audio_route(self) -> None:
        code = spotify_code()
        self.assertNotIn("/api/audio", code)

    def test_the_audio_module_touches_no_spotify_route(self) -> None:
        code = code_only((WEB_DIR / "audio.js").read_text(encoding="utf-8"))
        self.assertNotIn("/api/spotify", code)


class LayoutTests(unittest.TestCase):
    """Check 56: nothing on this panel can push the page sideways."""

    def setUp(self) -> None:
        self.css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
        # The base rules only. A selector also appears inside the narrow-screen
        # media query, and matching that override instead of the rule it
        # overrides would test the wrong declaration block.
        self.base_css = re.sub(r"@media[^{]*\{.*?\}\s*\}", "", self.css, flags=re.S)

    def _rule_for(self, selector: str) -> str:
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", self.base_css)
        return match.group(1) if match else ""

    def test_every_spotify_flex_row_can_shrink(self) -> None:
        """`min-width: 0` is what actually stops a flex child overflowing."""
        for selector in (".sp-account", ".sp-now", ".sp-now-title", ".sp-transport",
                         ".sp-volume", ".sp-mute-row", ".sp-device-picker", ".sp-device",
                         ".mr-actions"):
            with self.subTest(selector=selector):
                body = self._rule_for(selector)
                self.assertTrue(body, f"{selector} should be styled")
                self.assertIn("min-width: 0", body)

    def test_provider_text_is_allowed_to_wrap(self) -> None:
        """A track title and a device name are both text somebody else wrote."""
        for selector in (".sp-headline", ".sp-now-title strong", ".sp-now-meta",
                         ".sp-device-name", ".sp-device-sub"):
            with self.subTest(selector=selector):
                self.assertIn("overflow-wrap: anywhere", self._rule_for(selector))

    def test_the_action_row_on_a_card_wraps_rather_than_scrolling(self) -> None:
        body = self._rule_for(".mr-actions")
        self.assertIn("flex-wrap: wrap", body)

    def test_the_slider_can_shrink_below_its_intrinsic_width(self) -> None:
        rule = re.search(r'\.sp-volume input\[type="range"\]\s*\{([^}]*)\}', self.base_css)
        self.assertIsNotNone(rule)
        self.assertIn("min-width: 0", rule.group(1))

    def test_a_narrow_phone_gets_a_stacking_rule(self) -> None:
        narrow = self.css[self.css.rfind("@media (max-width: 420px)"):]
        self.assertIn(".sp-volume", narrow)
        self.assertIn(".sp-device-picker", narrow)

    def test_no_fixed_pixel_width_is_set_on_a_spotify_block(self) -> None:
        for match in re.finditer(r"\.sp-[\w-]*[^{]*\{([^}]*)\}", self.css):
            body = match.group(1)
            self.assertNotRegex(body, r"(?<!min-)(?<!max-)width:\s*\d+px")

    def test_the_touch_targets_are_thumb_sized(self) -> None:
        for selector in (".sp-transport .sp-btn", ".mr-actions button"):
            with self.subTest(selector=selector):
                body = self._rule_for(selector)
                match = re.search(r"min-height:\s*(\d+)px", body)
                self.assertIsNotNone(match, f"{selector} needs a min-height")
                self.assertGreaterEqual(int(match.group(1)), 40)


class StructuralTests(unittest.TestCase):
    """What the shipped module cannot do, whichever path it takes."""

    def setUp(self) -> None:
        self.code = spotify_code()

    def test_no_restore_volume_is_invented_in_the_client(self) -> None:
        """Check 46/47: the client has no fallback level, so it cannot pick one."""
        self.assertNotIn("restore_volume_percent", self.code)
        # No bare "reasonable default" volume anywhere in the module.
        self.assertNotRegex(self.code, r"volume_percent:\s*\d+")

    def test_the_mute_flag_is_read_only_under_its_truthful_name(self) -> None:
        self.assertIn("muted_by_cofferdam", self.code)
        self.assertNotRegex(self.code, r"snapshot\.muted\b")

    def test_the_module_reads_state_only_from_the_server(self) -> None:
        """No control writes its own display from the value the user chose."""
        # `draftVolume` is the one local value, and it is only ever rendered as a
        # target with the observed level shown beside it.
        self.assertIn("Release to set", self.code)
        self.assertIn("currently ", self.code)

    def test_a_get_is_never_used_to_change_anything(self) -> None:
        reads = re.findall(r'deps\.api\("([^"]+)"[^)]*\)', self.code)
        for path in reads:
            if "playback" in path:
                self.assertNotIn("player/", path)

    def test_the_module_exports_only_what_app_js_needs(self) -> None:
        match = re.search(r"global\.CofferdamSpotify = \{(.*?)\};", self.code, re.S)
        self.assertIsNotNone(match)
        exported = set(re.findall(r"(\w+):", match.group(1)))
        self.assertEqual(
            exported, {"mount", "refresh", "stop", "connected", "playResult", "queueResult"}
        )

    def test_app_js_stops_the_panel_when_the_token_is_dropped(self) -> None:
        code = app_code()
        self.assertIn("global.CofferdamSpotify.stop()", code)
        self.assertIn("global.CofferdamSpotify.mount(", code)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
