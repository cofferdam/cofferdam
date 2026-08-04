"""Graphical-session detection and verified launching (Linux + systemd).

These cover the M1 Ubuntu finding that `open_application` and `open_url` were
recorded as *succeeded* while nothing appeared on screen. Two independent
defects produced that: the launcher was never waited on, and the service's
`NoNewPrivileges=yes` made every child unable to run Ubuntu's snap-packaged
Firefox. The tests here pin the resulting rules:

* a launch is only a success with evidence — surviving a settle window, or a
  visible existing instance;
* a process that dies *after* briefly looking active is a failure, not a
  success (the exact shape of the original bug);
* "is there a desktop session" is answered live, so a service started by
  lingering before graphical login reports the truth rather than its own stale
  startup environment.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cofferdam.workstation.adapters import linux_session
from cofferdam.workstation.adapters.linux_session import (
    detect_graphical_session,
    launch_in_session,
)
from cofferdam.workstation.errors import AdapterError


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["stub"],
        returncode=returncode,
        stdout=stdout.encode("utf-8"),
        stderr=stderr.encode("utf-8"),
    )


def _show(active_state: str, sub_state: str = "", exit_status: str = "", pid: str = "0") -> str:
    return "\n".join(
        [
            "ActiveState=" + active_state,
            "SubState=" + sub_state,
            "ExecMainStatus=" + exit_status,
            "ExecMainPID=" + pid,
            "Result=" + ("exit-code" if active_state == "failed" else "success"),
        ]
    )


class _FakeSystemd:
    """Scripts the control commands the module issues.

    ``target_generation`` stands in for systemd's
    ``ActiveEnterTimestampMonotonic``: the marker that changes when the
    graphical session is torn down and a new one starts. Tests mutate it to
    simulate a logout/login happening mid-request.
    """

    def __init__(
        self,
        *,
        target: str = "active",
        environment: str = "",
        show=(),
        target_generation: str = "111222333",
    ):
        self.target = target
        self.environment = environment
        self.show_sequence = list(show)
        self.target_generation = target_generation
        self.launched = None
        self.target_queries = 0

    def __call__(self, argv, timeout=None):
        argv = list(argv)
        if argv[0] == "systemd-run":
            self.launched = argv
            return _completed()
        if "show-environment" in argv:
            return _completed(self.environment)
        if "show" in argv and "graphical-session.target" in argv:
            # Read-only state query. The module must never ask systemd to
            # *start* this target — see the login-loop regression.
            self.target_queries += 1
            for forbidden in ("start", "stop", "restart", "isolate"):
                self.assert_not_in(forbidden, argv)
            return _completed(
                "ActiveState="
                + self.target
                + "\nActiveEnterTimestampMonotonic="
                + (self.target_generation if self.target == "active" else "0")
            )
        if "show" in argv:
            if not self.show_sequence:
                raise AssertionError("ran out of scripted 'systemctl show' results")
            value = self.show_sequence[0]
            if len(self.show_sequence) > 1:
                self.show_sequence.pop(0)
            return _completed(value)
        if "reset-failed" in argv:
            return _completed()
        raise AssertionError("unexpected command: " + " ".join(argv))

    @staticmethod
    def assert_not_in(word, argv) -> None:
        if word in argv:
            raise AssertionError("module tried to " + word + " the graphical session target")


class GraphicalSessionDetectionTests(unittest.TestCase):
    def test_no_graphical_target_means_no_session(self) -> None:
        """A lingering service before graphical login must say so, not guess."""
        fake = _FakeSystemd(target="inactive", environment="XDG_SESSION_TYPE=wayland")
        with patch.object(linux_session, "run_fixed", fake):
            session = detect_graphical_session()
        self.assertFalse(session.available)
        self.assertIn("graphical session", (session.reason or ""))

    def test_session_env_absent_means_no_session(self) -> None:
        """Target up but the manager publishes no display: still not usable.

        This is the linger case specifically: our own os.environ may be stale or
        empty, and the manager's live block is what a launch would inherit.
        """
        fake = _FakeSystemd(target="active", environment="HOME=/home/someone")
        with patch.object(linux_session, "run_fixed", fake), patch.dict(
            "os.environ", {}, clear=True
        ):
            session = detect_graphical_session()
        self.assertFalse(session.available)
        self.assertIn("WAYLAND_DISPLAY", (session.reason or ""))

    def test_wayland_socket_must_actually_exist(self) -> None:
        with tempfile.TemporaryDirectory() as runtime_dir:
            fake = _FakeSystemd(
                target="active",
                environment=(
                    "XDG_SESSION_TYPE=wayland\n"
                    "WAYLAND_DISPLAY=wayland-0\n"
                    "XDG_RUNTIME_DIR=" + runtime_dir
                ),
            )
            with patch.object(linux_session, "run_fixed", fake):
                session = detect_graphical_session()
        self.assertFalse(session.available)
        self.assertIn("socket", (session.reason or ""))

    def test_active_wayland_session_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as runtime_dir:
            (Path(runtime_dir) / "wayland-0").write_text("", encoding="utf-8")
            fake = _FakeSystemd(
                target="active",
                environment=(
                    "XDG_SESSION_TYPE=wayland\n"
                    "WAYLAND_DISPLAY=wayland-0\n"
                    "XDG_RUNTIME_DIR=" + runtime_dir
                ),
            )
            with patch.object(linux_session, "run_fixed", fake):
                session = detect_graphical_session()
        self.assertTrue(session.available)
        self.assertEqual(session.session_type, "wayland")
        self.assertIsNone(session.reason)

    def test_detection_never_raises_when_systemctl_is_missing(self) -> None:
        """Status must stay serviceable on a host with no systemd user manager."""

        def explode(argv, timeout=None):
            raise AdapterError("required program not found: systemctl")

        with patch.object(linux_session, "run_fixed", explode), patch.dict(
            "os.environ", {}, clear=True
        ):
            session = detect_graphical_session()
        self.assertFalse(session.available)

    def test_show_environment_values_are_unquoted(self) -> None:
        fake = _FakeSystemd(target="active", environment="XDG_SESSION_TYPE=$'wayland'")
        with patch.object(linux_session, "run_fixed", fake), patch.dict(
            "os.environ", {}, clear=True
        ):
            session = detect_graphical_session()
        self.assertEqual(session.session_type, "wayland")


class VerifiedLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.object(linux_session, "LAUNCH_POLL_SECONDS", 0.01)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_process_that_dies_after_looking_active_is_a_failure(self) -> None:
        """The original bug's exact shape: briefly active, then failed.

        Returning on the first 'active' sighting would call this a success.
        """
        fake = _FakeSystemd(
            show=[_show("active", "running", "0", "4242"), _show("failed", "failed", "1")]
        )
        with patch.object(linux_session, "run_fixed", fake):
            with self.assertRaises(AdapterError) as ctx:
                launch_in_session(["/usr/bin/firefox"], description="test", settle_seconds=0.2)
        self.assertIn("exited immediately", ctx.exception.message)
        self.assertIn("status 1", ctx.exception.detail or "")

    def test_surviving_the_settle_window_is_a_success(self) -> None:
        fake = _FakeSystemd(show=[_show("active", "running", "0", "4242")])
        with patch.object(linux_session, "run_fixed", fake):
            launch = launch_in_session(
                ["/usr/bin/firefox"], description="test", settle_seconds=0.05
            )
        self.assertEqual(launch.state, "running")
        self.assertEqual(launch.pid, 4242)

    def test_fast_clean_exit_is_reported_as_exited_not_running(self) -> None:
        """Exit 0 is *not* upgraded to success here — the caller must corroborate."""
        fake = _FakeSystemd(show=[_show("inactive", "dead", "0")])
        with patch.object(linux_session, "run_fixed", fake):
            launch = launch_in_session(
                ["/usr/bin/firefox"], description="test", settle_seconds=0.2
            )
        self.assertEqual(launch.state, "exited")
        self.assertEqual(launch.exit_status, 0)
        self.assertIsNone(launch.pid)

    def test_fast_nonzero_exit_is_a_failure(self) -> None:
        fake = _FakeSystemd(show=[_show("inactive", "dead", "1")])
        with patch.object(linux_session, "run_fixed", fake):
            with self.assertRaises(AdapterError):
                launch_in_session(["/usr/bin/firefox"], description="test", settle_seconds=0.2)

    def test_launch_goes_through_the_user_manager(self) -> None:
        """The whole point of the fix: the app is not a child of this service."""
        fake = _FakeSystemd(show=[_show("active", "running", "0", "77")])
        with patch.object(linux_session, "run_fixed", fake):
            launch_in_session(
                ["/usr/bin/firefox", "https://example.com"],
                description="test",
                settle_seconds=0.05,
            )
        self.assertIsNotNone(fake.launched)
        self.assertEqual(fake.launched[0], "systemd-run")
        self.assertIn("--user", fake.launched)
        # The URL stays a separate argv element, after the -- separator.
        self.assertEqual(fake.launched[-2:], ["/usr/bin/firefox", "https://example.com"])
        self.assertIn("--", fake.launched)

    def test_manager_refusal_is_reported(self) -> None:
        def refuse(argv, timeout=None):
            if list(argv)[0] == "systemd-run":
                return _completed(returncode=1, stderr="Failed to connect to bus")
            return _completed()

        with patch.object(linux_session, "run_fixed", refuse):
            with self.assertRaises(AdapterError) as ctx:
                launch_in_session(["/usr/bin/firefox"], description="test")
        self.assertIn("Failed to connect to bus", ctx.exception.detail or "")


class SessionIdentityTests(unittest.TestCase):
    """A GUI action must land in the session it was authorised against.

    The daemon is long-lived and survives logout, so "a graphical session is
    active" is not enough on its own: the session that is active when the
    application is finally launched has to be the *same* one that was checked
    when the request was accepted.
    """

    def setUp(self) -> None:
        patcher = patch.object(linux_session, "LAUNCH_POLL_SECONDS", 0.01)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _live_session(self, runtime_dir: str, generation: str) -> _FakeSystemd:
        (Path(runtime_dir) / "wayland-0").write_text("", encoding="utf-8")
        return _FakeSystemd(
            target="active",
            target_generation=generation,
            environment=(
                "XDG_SESSION_TYPE=wayland\nWAYLAND_DISPLAY=wayland-0\nXDG_RUNTIME_DIR=" + runtime_dir
            ),
            show=[_show("active", "running", "0", "4242")],
        )

    def test_detection_reports_the_current_session_generation(self) -> None:
        with tempfile.TemporaryDirectory() as runtime_dir:
            fake = self._live_session(runtime_dir, "555000")
            with patch.object(linux_session, "run_fixed", fake):
                session = detect_graphical_session()
        self.assertTrue(session.available)
        self.assertEqual(session.session_id, "555000")

    def test_an_inactive_target_reports_no_session_id(self) -> None:
        fake = _FakeSystemd(target="inactive", environment="XDG_SESSION_TYPE=wayland")
        with patch.object(linux_session, "run_fixed", fake):
            session = detect_graphical_session()
        self.assertFalse(session.available)
        self.assertIsNone(session.session_id)

    def test_stale_session_env_after_logout_is_not_trusted(self) -> None:
        """The manager keeps DISPLAY/WAYLAND_DISPLAY after logout under linger.

        The socket may even still exist. The target being inactive is what
        decides, so capabilities go false rather than a launch going nowhere.
        """
        with tempfile.TemporaryDirectory() as runtime_dir:
            (Path(runtime_dir) / "wayland-0").write_text("", encoding="utf-8")
            fake = _FakeSystemd(
                target="inactive",
                environment=(
                    "XDG_SESSION_TYPE=wayland\n"
                    "WAYLAND_DISPLAY=wayland-0\n"
                    "XDG_RUNTIME_DIR=" + runtime_dir
                ),
            )
            with patch.object(linux_session, "run_fixed", fake):
                session = detect_graphical_session()
        self.assertFalse(session.available)
        self.assertIsNone(session.session_id)

    def test_launch_into_the_same_session_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as runtime_dir:
            fake = self._live_session(runtime_dir, "555000")
            with patch.object(linux_session, "run_fixed", fake):
                launch = launch_in_session(
                    ["/usr/bin/firefox"],
                    description="test",
                    settle_seconds=0.05,
                    expect_session="555000",
                )
        self.assertEqual(launch.state, "running")

    def test_launch_is_refused_when_the_session_changed(self) -> None:
        """Logout + login between accepting the request and launching it."""
        with tempfile.TemporaryDirectory() as runtime_dir:
            fake = self._live_session(runtime_dir, "999999")
            with patch.object(linux_session, "run_fixed", fake):
                with self.assertRaises(AdapterError) as ctx:
                    launch_in_session(
                        ["/usr/bin/firefox"],
                        description="test",
                        settle_seconds=0.05,
                        expect_session="555000",
                    )
        self.assertIn("session changed", ctx.exception.message)
        self.assertIsNone(fake.launched, "nothing may be launched into a different session")

    def test_launch_is_refused_when_the_session_ended(self) -> None:
        fake = _FakeSystemd(target="inactive")
        with patch.object(linux_session, "run_fixed", fake):
            with self.assertRaises(AdapterError) as ctx:
                launch_in_session(
                    ["/usr/bin/firefox"],
                    description="test",
                    settle_seconds=0.05,
                    expect_session="555000",
                )
        self.assertIn("session ended", ctx.exception.message)
        self.assertIsNone(fake.launched)

    def test_detection_never_asks_systemd_to_start_the_target(self) -> None:
        """The regression guard, at runtime: detection activates nothing."""
        with tempfile.TemporaryDirectory() as runtime_dir:
            fake = self._live_session(runtime_dir, "555000")
            with patch.object(linux_session, "run_fixed", fake):
                detect_graphical_session()
        # _FakeSystemd asserts internally that no start/stop/restart/isolate verb
        # reaches the target; this pins that the query happened at all.
        self.assertEqual(fake.target_queries, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
