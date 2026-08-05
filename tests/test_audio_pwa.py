"""The Audio panel in the PWA (M2C checks 30-35).

Two kinds of test:

* **Behavioural**, run through ``tests/audio_harness.js`` against the shipped
  ``web/audio.js``. Double submission, false success, and a request that never
  answers are control-flow properties; a scan of the file cannot see any of
  them.
* **Structural**, scanning the shipped files. These assert what the code
  *cannot* do — no sample device, no optimistic write of a requested value —
  which a rendering test can only ever demonstrate for the paths it happens to
  take.

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
HARNESS = REPO_ROOT / "tests" / "audio_harness.js"


def audio_code() -> str:
    return code_only((WEB_DIR / "audio.js").read_text(encoding="utf-8"))


def run_scenario(name: str) -> dict:
    node = shutil.which("node")
    if not node:  # pragma: no cover - depends on the runner
        raise unittest.SkipTest("node is not installed")
    completed = subprocess.run(
        [node, str(HARNESS), name], capture_output=True, text=True, timeout=60, check=False
    )
    if completed.returncode != 0:  # pragma: no cover - harness failure
        raise AssertionError(f"harness failed: {completed.stderr[:500]}")
    payload = json.loads(completed.stdout)
    assert not payload.get("uncaught"), payload.get("uncaught")
    assert not payload.get("timerErrors"), payload.get("timerErrors")
    return payload


class SliderBoundsTests(unittest.TestCase):
    """(30) The control cannot express a value outside the product range."""

    def test_the_slider_is_bounded_zero_to_one_hundred(self) -> None:
        html = run_scenario("renders")["html"]
        self.assertIn('type="range"', html)
        self.assertIn('min="0"', html)
        self.assertIn('max="100"', html)
        self.assertIn('step="1"', html)

    def test_the_slider_is_labelled_for_assistive_technology(self) -> None:
        html = run_scenario("renders")["html"]
        self.assertIn('aria-label="output volume percent"', html)
        self.assertIn('aria-valuemin="0"', html)
        self.assertIn('aria-valuemax="100"', html)
        # And the number is shown, not only implied by the thumb position.
        self.assertRegex(html, r'audio-volume-value[^>]*>\d+%<')

    def test_no_amplification_control_is_offered(self) -> None:
        """The ceiling is 100, and no vocabulary for going past it exists.

        Asserting on the rendered ``max`` rather than on stray digits: a bare
        substring ban flags ``POLL_MS = 20000`` and teaches the next author to
        rename a timing constant to appease a test.
        """
        html = run_scenario("renders")["html"]
        maxima = re.findall(r'max="(\d+)"', html)
        self.assertTrue(maxima)
        for value in maxima:
            self.assertLessEqual(int(value), 100)

        code = audio_code().lower()
        for banned in ("boost", "amplif", "overdrive", "over100", "above100"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, code)
        # And the clamp the view applies to its own slider stops at 100.
        self.assertIn("math.min(100, parsed)", code)


class DoubleSubmissionTests(unittest.TestCase):
    """(31) A second tap while busy is not a second command."""

    def test_three_taps_send_one_request(self) -> None:
        result = run_scenario("double-submit")
        self.assertEqual(result["putCount"], 1)

    def test_controls_are_disabled_while_a_change_is_in_flight(self) -> None:
        result = run_scenario("double-submit")
        self.assertIn("disabled", result["duringHtml"])
        # ...and released once it settles.
        self.assertNotIn("disabled", result["html"])

    def test_a_request_that_never_answers_still_releases_the_panel(self) -> None:
        """The bounded pending state, exercised as the hang it exists for."""
        result = run_scenario("pending-bound")
        self.assertIn("disabled", result["stuckHtml"])
        self.assertNotIn("disabled", result["html"])
        self.assertIn("did not finish in time", result["html"])
        # And it does not claim the change worked.
        self.assertNotIn("audio-note ok", result["html"])


class NoOptimisticSuccessTests(unittest.TestCase):
    """(32) The panel shows what the server observed, never what was asked."""

    def test_an_ignored_change_renders_the_observed_value(self) -> None:
        html = run_scenario("no-optimistic")["html"]
        # The server observed 50 after a request for 25.
        self.assertRegex(html, r'audio-volume-value[^>]*>50%<')
        self.assertIn("but this output reports 50%", html)
        self.assertNotIn("audio-note ok", html)

    def test_a_refused_change_shows_the_server_message(self) -> None:
        html = run_scenario("refused")["html"]
        self.assertIn("audio-error", html)
        self.assertIn("must be between 0 and 100 percent", html)
        self.assertNotIn("audio-note ok", html)

    def test_the_view_never_writes_a_requested_value_into_its_own_state(self) -> None:
        """Structural: no assignment of a request back onto the rendered model.

        The negative lookahead matters: ``=== "number"`` contains ``=`` and a
        plain substring check flags the perfectly good type guard beside it.
        What is being banned is *assignment* to server-owned state.
        """
        code = audio_code()
        # The only place a percentage is written to state is the drag draft,
        # which is rendered as a target and labelled as one.
        for target in (
            r"output\.volume_percent",
            r"output\.muted",
            r"snapshot\.default_output_resource_id",
            r"item\.volume_percent",
        ):
            with self.subTest(target=target):
                self.assertNotRegex(code, target + r"\s*=(?!=)")

    def test_every_action_re_reads_before_reporting(self) -> None:
        code = audio_code()
        # Both the success and the refusal path refresh from the server.
        self.assertGreaterEqual(code.count("load(true)"), 2)


class ObservedOutputTests(unittest.TestCase):
    """(33) After selecting an output, the panel shows the observed result."""

    def test_selecting_an_output_renders_the_new_current_output(self) -> None:
        result = run_scenario("observed-output")
        self.assertIn("Monitor Audio", result["html"])
        # The partial outcome is shown as a partial, not as a clean success.
        self.assertIn("already playing stayed where it was", result["html"])
        self.assertIn("audio-note warn", result["html"])

    def test_the_user_is_warned_before_switching(self) -> None:
        html = run_scenario("renders")["html"]
        self.assertIn("Audio that is already", html)

    def test_a_host_with_no_default_is_told_rather_than_shown_a_guess(self) -> None:
        html = run_scenario("no-default")["html"]
        self.assertIn("no default one", html)
        self.assertNotIn("audio-volume", html)


class DegradationTests(unittest.TestCase):
    """(34) The panel works when stream discovery does not."""

    def test_volume_and_mute_still_work_without_stream_discovery(self) -> None:
        result = run_scenario("streams-unavailable")
        self.assertIn("audio-volume", result["html"])
        self.assertIn("audioMute", result["html"])
        self.assertEqual(result["putCount"], 1)

    def test_unavailable_streams_are_not_rendered_as_nothing_playing(self) -> None:
        html = run_scenario("streams-unavailable")["html"]
        self.assertIn("cannot read what is currently playing", html)
        self.assertNotIn("Nothing is playing right now", html)


class NoSampleDataTests(unittest.TestCase):
    """The rule the live view already follows, applied to audio."""

    def setUp(self) -> None:
        self.code = audio_code()

    def test_the_audio_view_ships_no_placeholder_device(self) -> None:
        banned = (
            "alsa_output", "bluez_output", "pci-0000", "sample", "demo",
            "placeholder", "lorem", "hdmi-stereo", "Speaker Name",
        )
        lowered = self.code.lower()
        for text in banned:
            with self.subTest(text=text):
                self.assertNotIn(text.lower(), lowered)

    def test_the_audio_view_holds_no_hardcoded_resource_array(self) -> None:
        self.assertNotIn("resource_id:", self.code)
        self.assertNotIn('"resource_id"', self.code)

    def test_no_media_title_vocabulary_appears_in_the_view(self) -> None:
        """(27, client half) The panel reads no field that could hold a title.

        Field *access* is what is banned, not the English word: the streams
        section carries the sentence "Titles of what is playing are never read
        or shown", which is the promise being kept rather than a breach of it.
        """
        for banned in (
            "media.name", "media_name", "track_title", "video_title", "tab_title",
            ".title", "song", "artist", "now_playing",
        ):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, self.code.lower())

    def test_the_view_never_sends_a_backend_identifier(self) -> None:
        """(20, client half) node ids are never put in a request."""
        self.assertNotIn("node_id", self.code)
        self.assertNotIn("object_serial", self.code)


class LayoutTests(unittest.TestCase):
    """(35) Nothing in this panel can push the page sideways."""

    def setUp(self) -> None:
        raw = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
        # Comments out first. A `/* … */` block sitting above a rule otherwise
        # lands inside the captured selector list and stops it matching.
        self.css = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)

    def test_every_audio_text_block_may_wrap(self) -> None:
        for selector in (
            ".audio-current-name", ".audio-output-name", ".audio-stream-name",
            ".audio-output-sub", ".audio-stream-sub", ".audio-error", ".audio-unavailable",
        ):
            with self.subTest(selector=selector):
                rule = re.search(re.escape(selector) + r"[^{]*\{([^}]*)\}", self.css)
                self.assertIsNotNone(rule, f"{selector} should be styled")
                self.assertIn("overflow-wrap: anywhere", rule.group(1))

    def _rule_for(self, selector: str) -> str:
        """The declaration block for a selector, including grouped rules.

        ``.audio-output, .audio-stream { … }`` is one rule serving two
        selectors, so anchoring on ``selector {`` finds neither.
        """
        pattern = re.compile(
            r"(^|[},])\s*([^{}]*?)\{([^}]*)\}", re.MULTILINE | re.DOTALL
        )
        for match in pattern.finditer(self.css):
            selectors = [part.strip() for part in match.group(2).split(",")]
            if selector in selectors:
                return match.group(3)
        return ""

    def test_the_flex_rows_can_shrink(self) -> None:
        """`min-width: 0` is what actually stops a flex child overflowing."""
        for selector in (".audio-volume", ".audio-actions", ".audio-output", ".audio-stream"):
            with self.subTest(selector=selector):
                body = self._rule_for(selector)
                self.assertTrue(body, f"{selector} should be styled")
                self.assertIn("min-width: 0", body)

    def test_the_slider_can_shrink_below_its_intrinsic_width(self) -> None:
        rule = re.search(r'\.audio-volume input\[type="range"\]\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(rule)
        self.assertIn("min-width: 0", rule.group(1))

    def test_a_narrow_phone_gets_a_stacking_rule(self) -> None:
        narrow = self.css[self.css.rfind("@media (max-width: 420px)"):]
        self.assertIn(".audio-volume", narrow)
        self.assertIn(".audio-select", narrow)

    def test_no_fixed_pixel_width_is_set_on_an_audio_block(self) -> None:
        for match in re.finditer(r"\.audio-[\w-]*[^{]*\{([^}]*)\}", self.css):
            body = match.group(1)
            self.assertNotRegex(body, r"(?<!min-)(?<!max-)width:\s*\d+px")


class WiringTests(unittest.TestCase):
    """The panel is actually reachable, and stops when the token is dropped."""

    def setUp(self) -> None:
        self.index = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.app = code_only((WEB_DIR / "app.js").read_text(encoding="utf-8"))

    def test_the_audio_script_and_panel_are_served(self) -> None:
        self.assertIn('<script src="/audio.js"></script>', self.index)
        self.assertIn('id="audioPanel"', self.index)
        self.assertIn('id="audioSections"', self.index)

    def test_the_panel_is_mounted_and_stopped_with_the_token(self) -> None:
        self.assertIn("CofferdamAudio.mount", self.app)
        self.assertIn("CofferdamAudio.stop", self.app)

    def test_the_panel_sits_above_the_media_panel(self) -> None:
        """Turning the volume down should not be below a list of services."""
        self.assertLess(self.index.index('id="audioPanel"'), self.index.index('id="mediaPanel"'))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
