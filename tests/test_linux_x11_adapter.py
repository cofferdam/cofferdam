"""LinuxX11Adapter: capability reporting, session gating, and false-success rules.

Two real-run findings from the M1 Ubuntu validation are pinned here.

**Black screenshots.** X11 root-window capture tools (``scrot``, ``maim``,
``import``) exit 0 and write a non-empty PNG under a Wayland session, but the
image is solid black — XWayland exposes an empty placeholder root window, not
the compositor's framebuffer. The adapter must not offer them as a screenshot
capability under Wayland, and must fail closed rather than serve a black image.

**Applications that "succeeded" but never opened.** Launches were reported as
successes on the strength of a PID alone. The adapter must now confirm a launch
before reporting it, must gate every graphical action on a desktop session that
actually exists, and must describe this host truthfully — without directing the
user to a session type we have not verified for them.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from cofferdam.workstation.adapters import linux_x11
from cofferdam.workstation.adapters.linux_session import GraphicalSession, SessionLaunch
from cofferdam.workstation.adapters.linux_x11 import LinuxX11Adapter
from cofferdam.workstation.errors import AdapterError, AdapterUnsupported

WAYLAND_SESSION = GraphicalSession(
    available=True,
    session_type="wayland",
    session_id="wayland-generation-1",
    environment={"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"},
)
X11_SESSION = GraphicalSession(
    available=True,
    session_type="x11",
    session_id="x11-generation-1",
    environment={"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"},
)
NO_SESSION = GraphicalSession(
    available=False,
    session_type=None,
    reason="no graphical session is active on this host yet",
)

# A service started at boot by lingering: its own environment predates every
# session, so it names no display at all. Patched over ``os.environ`` to make
# "the daemon's environment is not authoritative" testable rather than asserted.
LINGERING_DAEMON_ENVIRONMENT = {"PATH": "/usr/bin:/bin", "HOME": "/home/tester"}


def _adapter() -> LinuxX11Adapter:
    return LinuxX11Adapter(config=None)


def _only(*names):
    """A ``first_available`` stub that finds exactly ``names`` on PATH."""

    def resolve(candidates):
        for candidate in candidates:
            if candidate in names:
                return "/usr/bin/" + candidate
        return None

    return resolve


def _session(session: GraphicalSession):
    return patch.object(linux_x11, "detect_graphical_session", lambda: session)


class WaylandScreenshotSafetyTests(unittest.TestCase):
    def test_x11_root_capture_tools_are_rejected_under_wayland(self) -> None:
        """Only scrot is on PATH, but the session is Wayland: no tool is usable."""
        adapter = _adapter()
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=False), patch.object(
            linux_x11, "first_available", _only("scrot")
        ), _session(WAYLAND_SESSION):
            self.assertIsNone(adapter._screenshot_tool(WAYLAND_SESSION))
            self.assertFalse(adapter.host_status().capabilities["screenshot"])
            with self.assertRaises(AdapterUnsupported) as ctx:
                adapter.take_screenshot()
            self.assertIn("Wayland", ctx.exception.message)

    def test_gnome_screenshot_is_still_usable_under_wayland(self) -> None:
        """A Wayland-safe tool (gnome-screenshot) is not filtered out."""
        adapter = _adapter()
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=False), patch.object(
            linux_x11, "first_available", _only("gnome-screenshot")
        ):
            selected = adapter._screenshot_tool(WAYLAND_SESSION)
            self.assertIsNotNone(selected)
            self.assertEqual(selected[2], "gnome-screenshot")

    def test_scrot_is_still_usable_outside_wayland(self) -> None:
        """The X11 path keeps working: no regression there."""
        adapter = _adapter()
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11"}, clear=False), patch.object(
            linux_x11, "first_available", _only("scrot")
        ), _session(X11_SESSION):
            selected = adapter._screenshot_tool(X11_SESSION)
            self.assertIsNotNone(selected)
            self.assertEqual(selected[2], "scrot")
            self.assertTrue(adapter.host_status().capabilities["screenshot"])


class CapabilityReportingTests(unittest.TestCase):
    def test_no_graphical_session_disables_every_gui_capability(self) -> None:
        """Linger before login: the API is up, but nothing GUI is offered."""
        adapter = _adapter()
        with patch.object(linux_x11, "first_available", _only("firefox", "gnome-screenshot")), _session(
            NO_SESSION
        ):
            status = adapter.host_status()
        self.assertEqual(
            status.capabilities,
            {"screenshot": False, "open_application": False, "open_url": False},
        )
        self.assertTrue(status.notes)
        self.assertIn("GUI actions are unavailable", status.notes[0])

    def test_wayland_session_enables_launching_but_not_capture(self) -> None:
        adapter = _adapter()
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=False), patch.object(
            linux_x11, "first_available", _only("firefox", "scrot")
        ), _session(WAYLAND_SESSION):
            status = adapter.host_status()
        self.assertFalse(status.capabilities["screenshot"])
        self.assertTrue(status.capabilities["open_application"])
        self.assertTrue(status.capabilities["open_url"])
        self.assertEqual(status.session_type, "wayland")

    def test_session_type_comes_from_live_detection_not_our_own_environment(self) -> None:
        """A service started before login has a stale environment; ignore it."""
        adapter = _adapter()
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11"}, clear=False), patch.object(
            linux_x11, "first_available", _only("firefox")
        ), _session(WAYLAND_SESSION):
            self.assertEqual(adapter.host_status().session_type, "wayland")

    def test_notes_never_direct_the_user_to_xorg(self) -> None:
        """Corrected M1 text: no promise about a session type we cannot verify."""
        adapter = _adapter()
        for session in (WAYLAND_SESSION, NO_SESSION):
            with self.subTest(session=session.session_type):
                with patch.object(
                    linux_x11, "first_available", _only("firefox", "scrot")
                ), _session(session):
                    notes = " ".join(adapter.host_status().notes).lower()
                self.assertNotIn("xorg", notes)
                self.assertNotIn("x11", notes)

    def test_wayland_note_names_capture_and_clears_launching(self) -> None:
        adapter = _adapter()
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=False), patch.object(
            linux_x11, "first_available", _only("firefox", "scrot")
        ), _session(WAYLAND_SESSION):
            notes = " ".join(adapter.host_status().notes)
        self.assertIn("Screen capture is unavailable", notes)
        self.assertIn("Wayland", notes)
        self.assertIn("unaffected", notes)


class ScreenshotCapabilityAccuracyTests(unittest.TestCase):
    """M1.2: the screenshot flag must describe the *session*, not our PATH.

    Observed on the real Ubuntu GNOME/Wayland host: a daemon started at boot by
    lingering reported ``screenshot: true`` because ``scrot`` was installed, and
    the action then failed with ``scrot: Can't open X display``. It failed
    closed — no black image, no false success — so this was an
    advertisement-accuracy defect, and these tests pin the corrected rule.

    Every test here patches ``os.environ`` to the lingering daemon's variable-free
    environment, so any implementation that consults it instead of the verified
    session fails rather than passes by coincidence.
    """

    def _lingering(self):
        return patch.dict("os.environ", LINGERING_DAEMON_ENVIRONMENT, clear=True)

    def test_boot_started_daemon_does_not_advertise_scrot_in_a_wayland_session(self) -> None:
        """The exact reported defect: empty daemon env, Wayland session, scrot."""
        adapter = _adapter()
        with self._lingering(), patch.object(
            linux_x11, "first_available", _only("scrot")
        ), _session(WAYLAND_SESSION):
            status = adapter.host_status()
        self.assertFalse(status.capabilities["screenshot"])

    def test_wayland_session_with_x11_only_tool_is_false(self) -> None:
        adapter = _adapter()
        with self._lingering(), patch.object(
            linux_x11, "first_available", _only("scrot", "maim", "import")
        ), _session(WAYLAND_SESSION):
            self.assertFalse(adapter.host_status().capabilities["screenshot"])

    def test_x11_session_with_scrot_may_be_true(self) -> None:
        """The supported existing path still advertises, from session state."""
        adapter = _adapter()
        with self._lingering(), patch.object(
            linux_x11, "first_available", _only("scrot")
        ), _session(X11_SESSION):
            self.assertTrue(adapter.host_status().capabilities["screenshot"])

    def test_no_verified_session_is_false_even_with_every_tool_installed(self) -> None:
        adapter = _adapter()
        with self._lingering(), patch.object(
            linux_x11, "first_available", _only("gnome-screenshot", "scrot", "maim")
        ), _session(NO_SESSION):
            self.assertFalse(adapter.host_status().capabilities["screenshot"])

    def test_tool_presence_alone_cannot_make_the_capability_true(self) -> None:
        """Same PATH, three different sessions, three different answers."""
        adapter = _adapter()
        answers = {}
        for name, session in (
            ("none", NO_SESSION),
            ("wayland", WAYLAND_SESSION),
            ("x11", X11_SESSION),
        ):
            with self._lingering(), patch.object(
                linux_x11, "first_available", _only("scrot")
            ), _session(session):
                answers[name] = adapter.host_status().capabilities["screenshot"]
        self.assertEqual(answers, {"none": False, "wayland": False, "x11": True})

    def test_a_session_publishing_wayland_display_is_wayland_even_without_session_type(
        self,
    ) -> None:
        """A missing XDG_SESSION_TYPE must not be read as 'probably X11'."""
        adapter = _adapter()
        untyped_wayland = GraphicalSession(
            available=True,
            session_type=None,
            session_id="g1",
            environment={"WAYLAND_DISPLAY": "wayland-0"},
        )
        with self._lingering(), patch.object(
            linux_x11, "first_available", _only("scrot")
        ), _session(untyped_wayland):
            self.assertFalse(adapter.host_status().capabilities["screenshot"])

    def test_session_publishing_no_display_endpoint_is_false(self) -> None:
        adapter = _adapter()
        no_display = GraphicalSession(
            available=True, session_type=None, session_id="g1", environment={}
        )
        with self._lingering(), patch.object(
            linux_x11, "first_available", _only("scrot")
        ), _session(no_display):
            self.assertFalse(adapter.host_status().capabilities["screenshot"])

    def test_stale_daemon_display_is_not_used_for_the_capture(self) -> None:
        """A DISPLAY left over from an ended session must not be handed on."""
        stale = dict(LINGERING_DAEMON_ENVIRONMENT, DISPLAY=":99", WAYLAND_DISPLAY="wayland-9")
        with patch.dict("os.environ", stale, clear=True):
            environment = linux_x11._capture_environment(X11_SESSION)
        self.assertEqual(environment["DISPLAY"], ":0")
        self.assertNotIn("WAYLAND_DISPLAY", environment)
        self.assertEqual(environment["PATH"], "/usr/bin:/bin")

    def test_capture_runs_with_the_sessions_display_not_ours(self) -> None:
        adapter = _adapter()
        seen = {}

        def fake_run(argv, **kwargs):
            seen["env"] = kwargs.get("env") or {}
            raise AdapterError("stop here")

        with self._lingering(), patch.object(
            linux_x11, "first_available", _only("scrot")
        ), patch.object(linux_x11, "run_fixed", fake_run), _session(X11_SESSION):
            with self.assertRaises(AdapterError):
                adapter.take_screenshot()
        self.assertEqual(seen["env"].get("DISPLAY"), ":0")

    def test_capability_is_recalculated_after_logout_and_a_new_session(self) -> None:
        """Nothing is cached from an earlier login."""
        adapter = _adapter()
        sequence = [X11_SESSION, NO_SESSION, WAYLAND_SESSION]
        seen = []
        for session in sequence:
            with self._lingering(), patch.object(
                linux_x11, "first_available", _only("scrot")
            ), _session(session):
                seen.append(adapter.host_status().capabilities["screenshot"])
        self.assertEqual(seen, [True, False, False])

    def test_replaced_session_identity_does_not_carry_capability_forward(self) -> None:
        """A new generation of the same type is judged on its own environment."""
        adapter = _adapter()
        replaced = GraphicalSession(
            available=True,
            session_type="wayland",
            session_id="wayland-generation-2",
            environment={"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-1"},
        )
        with self._lingering(), patch.object(
            linux_x11, "first_available", _only("scrot")
        ), _session(X11_SESSION):
            self.assertTrue(adapter.host_status().capabilities["screenshot"])
        with self._lingering(), patch.object(
            linux_x11, "first_available", _only("scrot")
        ), _session(replaced):
            self.assertFalse(adapter.host_status().capabilities["screenshot"])


class ScreenshotFailsClosedTests(unittest.TestCase):
    """The action must stay refused whenever the capability is not offered."""

    def _lingering(self):
        return patch.dict("os.environ", LINGERING_DAEMON_ENVIRONMENT, clear=True)

    def test_direct_request_while_unavailable_fails_closed_with_no_image(self) -> None:
        """A caller bypassing the disabled button gets a typed refusal."""
        adapter = _adapter()
        ran = []

        with self._lingering(), patch.object(
            linux_x11, "first_available", _only("scrot")
        ), patch.object(linux_x11, "run_fixed", lambda *a, **k: ran.append(a)), _session(
            WAYLAND_SESSION
        ):
            self.assertFalse(adapter.host_status().capabilities["screenshot"])
            with self.assertRaises(AdapterUnsupported) as ctx:
                adapter.take_screenshot()

        self.assertIn("Wayland", ctx.exception.message)
        self.assertEqual(ran, [], "no capture process may be started when unavailable")

    def test_refusal_is_deterministic_across_repeated_requests(self) -> None:
        adapter = _adapter()
        for _ in range(3):
            with self._lingering(), patch.object(
                linux_x11, "first_available", _only("scrot")
            ), _session(WAYLAND_SESSION):
                with self.assertRaises(AdapterUnsupported):
                    adapter.take_screenshot()

    def test_session_without_a_display_endpoint_names_that_reason(self) -> None:
        adapter = _adapter()
        no_display = GraphicalSession(
            available=True, session_type=None, session_id="g1", environment={}
        )
        with self._lingering(), patch.object(
            linux_x11, "first_available", _only("scrot")
        ), _session(no_display):
            with self.assertRaises(AdapterUnsupported) as ctx:
                adapter.take_screenshot()
        self.assertIn("display endpoint", ctx.exception.detail or "")

    def test_a_failing_capture_tool_is_still_a_bounded_adapter_error(self) -> None:
        """Preserved behaviour: the real host's scrot failure stays typed."""
        adapter = _adapter()

        class _Completed:
            returncode = 1
            stdout = b""
            stderr = b"scrot: Can't open X display. It *is* running, yeah?"

        with self._lingering(), patch.object(
            linux_x11, "first_available", _only("scrot")
        ), patch.object(linux_x11, "run_fixed", lambda *a, **k: _Completed()), _session(
            X11_SESSION
        ):
            with self.assertRaises(AdapterError) as ctx:
                adapter.take_screenshot()
        self.assertIn("screenshot tool failed", ctx.exception.message)
        self.assertNotIsInstance(ctx.exception, AdapterUnsupported)

    def test_an_empty_capture_file_is_never_reported_as_success(self) -> None:
        """Preserved behaviour: a 0-byte PNG is a failure, not a screenshot."""
        adapter = _adapter()

        class _Completed:
            returncode = 0
            stdout = b""
            stderr = b""

        with self._lingering(), patch.object(
            linux_x11, "first_available", _only("scrot")
        ), patch.object(linux_x11, "run_fixed", lambda *a, **k: _Completed()), _session(
            X11_SESSION
        ):
            with self.assertRaises(AdapterError) as ctx:
                adapter.take_screenshot()
        self.assertIn("no image", ctx.exception.message)


class SessionGatingTests(unittest.TestCase):
    def test_open_application_fails_closed_without_a_session(self) -> None:
        adapter = _adapter()
        with patch.object(linux_x11, "first_available", _only("firefox")), _session(NO_SESSION):
            with self.assertRaises(AdapterUnsupported) as ctx:
                adapter.open_application("firefox")
        self.assertIn("no active graphical session", ctx.exception.message)
        self.assertIn("graphical session", ctx.exception.detail or "")

    def test_open_url_fails_closed_without_a_session(self) -> None:
        adapter = _adapter()
        with patch.object(linux_x11, "first_available", _only("firefox")), _session(NO_SESSION):
            with self.assertRaises(AdapterUnsupported):
                adapter.open_url("https://example.com")

    def test_screenshot_fails_closed_without_a_session(self) -> None:
        adapter = _adapter()
        with patch.object(linux_x11, "first_available", _only("gnome-screenshot")), _session(
            NO_SESSION
        ):
            with self.assertRaises(AdapterUnsupported):
                adapter.take_screenshot()

    def test_no_session_check_happens_before_the_allowlist_check(self) -> None:
        """A non-allowlisted key is still rejected as such, session or not."""
        adapter = _adapter()
        with _session(NO_SESSION):
            with self.assertRaises(AdapterUnsupported) as ctx:
                adapter.open_application("notepad")
        self.assertIn("not allowlisted", ctx.exception.message)


class FalseSuccessPreventionTests(unittest.TestCase):
    def test_running_process_is_reported_with_its_pid(self) -> None:
        adapter = _adapter()
        launch = SessionLaunch(unit="u", pid=321, state="running", exit_status=None)
        with patch.object(linux_x11, "first_available", _only("firefox")), patch.object(
            linux_x11, "launch_in_session", lambda argv, description, **kwargs: launch
        ), _session(WAYLAND_SESSION):
            result = adapter.open_application("firefox")
        self.assertEqual(result.pid, 321)
        self.assertEqual(result.application, "firefox")

    def test_fast_exit_with_no_visible_instance_is_a_failure(self) -> None:
        """The defect this replaced: exit 0, nothing on screen, 'succeeded'."""
        adapter = _adapter()
        launch = SessionLaunch(unit="u", pid=None, state="exited", exit_status=0)
        with patch.object(linux_x11, "first_available", _only("firefox")), patch.object(
            linux_x11, "launch_in_session", lambda argv, description, **kwargs: launch
        ), patch.object(linux_x11, "process_running", lambda names: False), _session(
            WAYLAND_SESSION
        ):
            with self.assertRaises(AdapterError) as ctx:
                adapter.open_application("firefox")
        self.assertIn("without opening anything", ctx.exception.message)

    def test_fast_exit_with_a_visible_instance_is_accepted(self) -> None:
        """Launching a browser that is already up hands over and returns at once."""
        adapter = _adapter()
        launch = SessionLaunch(unit="u", pid=None, state="exited", exit_status=0)
        with patch.object(linux_x11, "first_available", _only("firefox")), patch.object(
            linux_x11, "launch_in_session", lambda argv, description, **kwargs: launch
        ), patch.object(linux_x11, "process_running", lambda names: True), _session(
            WAYLAND_SESSION
        ):
            result = adapter.open_application("firefox")
        self.assertEqual(result.application, "firefox")
        self.assertIsNone(result.pid)


class OpenUrlTests(unittest.TestCase):
    def test_url_is_opened_by_an_allowlisted_browser_not_xdg_open(self) -> None:
        """xdg-open exits 0 whether or not a browser starts, so it is not used."""
        adapter = _adapter()
        seen = {}

        def capture(argv, description, **kwargs):
            seen["argv"] = list(argv)
            return SessionLaunch(unit="u", pid=99, state="running", exit_status=None)

        with patch.object(linux_x11, "first_available", _only("firefox", "xdg-open")), patch.object(
            linux_x11, "launch_in_session", capture
        ), _session(WAYLAND_SESSION):
            result = adapter.open_url("https://example.com")

        self.assertEqual(seen["argv"], ["/usr/bin/firefox", "https://example.com"])
        self.assertEqual(result.application, "firefox")
        self.assertEqual(result.pid, 99)

    def test_url_fails_closed_when_no_allowlisted_browser_exists(self) -> None:
        adapter = _adapter()
        with patch.object(linux_x11, "first_available", _only("xdg-open")), _session(
            WAYLAND_SESSION
        ):
            with self.assertRaises(AdapterUnsupported) as ctx:
                adapter.open_url("https://example.com")
        self.assertIn("browser", ctx.exception.message)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
