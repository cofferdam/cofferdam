"""Ubuntu Desktop adapter (Wayland and X11).

Uses **semantic system commands**, never synthetic mouse/keyboard coordinates:

* screenshot — the first available of ``gnome-screenshot``, ``maim``,
  ``spectacle``, ``scrot``, or ImageMagick ``import``;
* launch — a fixed argv per allowlisted application key;
* open URL — the same fixed argv plus the service-validated URL;
* displays — ``xrandr --listmonitors`` (count only in M1; targeting is M2).

Graphical actions do not run as children of this service. They are handed to
the systemd user manager as transient units by
:mod:`~cofferdam.workstation.adapters.linux_session`, which also supplies the
live "is there a desktop session at all" answer. The reasoning, and the real
Ubuntu failure that forced it, are documented in that module.

**Reporting rule:** an exit code of zero from a launcher is not evidence that
anything reached the screen. Every launch here is confirmed — the process is
still alive after a settle window, or an existing instance of the same
application can be seen — and otherwise fails closed.
"""

from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path
from typing import List, Optional

from ..errors import AdapterError, AdapterUnsupported
from .base import (
    APPLICATION_KEYS,
    ApplicationLaunch,
    HostAdapter,
    HostStatus,
    Screenshot,
    disk_metrics,
    first_available,
    psutil_metrics,
    run_fixed,
)
from .linux_session import (
    GraphicalSession,
    SessionLaunch,
    detect_graphical_session,
    launch_in_session,
    process_running,
)

# Logical key -> candidate executables, in preference order. Fixed table: a
# caller can pick a key, never a command.
_APPLICATION_COMMANDS = {
    "firefox": ("firefox", "firefox-esr"),
    "chromium": ("chromium", "chromium-browser"),
    "google-chrome": ("google-chrome", "google-chrome-stable"),
}

# Logical key -> process names an already-running instance may present. Used
# only to corroborate a fast clean exit; a browser handed a request by an
# existing instance returns immediately. Snap and deb builds differ, hence the
# aliases.
_APPLICATION_PROCESS_NAMES = {
    "firefox": ("firefox", "firefox-esr", "firefox-bin"),
    "chromium": ("chromium", "chromium-browser", "chrome"),
    "google-chrome": ("chrome", "google-chrome", "google-chrome-stable"),
}

# tool -> argv template completed with the output path the service generates.
_SCREENSHOT_TOOLS = (
    ("gnome-screenshot", lambda exe, out: [exe, "--file", out]),
    ("maim", lambda exe, out: [exe, out]),
    ("scrot", lambda exe, out: [exe, "--overwrite", out]),
    ("spectacle", lambda exe, out: [exe, "--background", "--nonotify", "--output", out]),
    ("import", lambda exe, out: [exe, "-window", "root", out]),
)

# These tools grab the X11 root window directly. Under a Wayland session that
# root window is XWayland's empty placeholder, not the compositor's real
# framebuffer — the capture "succeeds" (exit 0, non-empty file) but is solid
# black. Confirmed on a real GNOME/Wayland host (M1 Ubuntu validation):
# ``scrot`` returned a 0-byte-variance black PNG rather than failing. Treat
# them as unavailable under Wayland so the adapter fails closed instead of
# reporting a false success.
_X11_ROOT_CAPTURE_TOOLS = frozenset({"scrot", "maim", "import"})

# Variables that describe *which display a program should draw on or read from*.
# They belong to the graphical session, so they are taken from the verified
# session's own environment block and never from this process's. A boot-started
# service has none of them; one restarted mid-session has whichever set was
# current when it started, which may since have been replaced.
_SESSION_DISPLAY_KEYS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "XDG_CURRENT_DESKTOP",
    "DBUS_SESSION_BUS_ADDRESS",
)


def _session_display(session: GraphicalSession) -> Optional[str]:
    """Which display protocol a program handed to *this* session would meet.

    ``"wayland"`` and ``"x11"`` are answers about the session, not about this
    process. ``None`` means the session published no display endpoint at all,
    in which case nothing graphical can be offered honestly.

    A session that publishes ``WAYLAND_DISPLAY`` counts as Wayland even if it
    did not publish ``XDG_SESSION_TYPE``: the X11 root-capture tools would
    return a black frame there, and guessing "x11" from a missing variable is
    how a false capability gets advertised.
    """
    environment = session.environment or {}
    if environment.get("WAYLAND_DISPLAY") or session.session_type == "wayland":
        return "wayland"
    if environment.get("DISPLAY"):
        return "x11"
    return None


def _capture_environment(session: GraphicalSession) -> dict:
    """Environment for a capture subprocess: our PATH, the session's display.

    ``PATH``/``HOME`` and the rest of our own environment are ordinary process
    setup and are kept. Every variable that names a *display* is replaced by the
    verified session's value, and dropped entirely when the session does not
    publish it — otherwise a ``DISPLAY`` inherited from a session that has since
    ended would send the capture at a dead X server.
    """
    environment = dict(os.environ)
    session_environment = session.environment or {}
    for key in _SESSION_DISPLAY_KEYS:
        value = session_environment.get(key)
        if value:
            environment[key] = value
        else:
            environment.pop(key, None)
    return environment


class LinuxX11Adapter(HostAdapter):
    name = "linux-x11"
    stub = False

    # -- status --------------------------------------------------------------

    def host_status(self) -> HostStatus:
        metrics = psutil_metrics()
        metrics.update(disk_metrics(str(Path.home())))

        # Asked live, every time: the API stays up through lingering before
        # anyone logs in, so "is there a desktop" is not answerable once at
        # startup. Capabilities follow from it, so the phone only offers a
        # control when that control can currently do something.
        session = detect_graphical_session()
        session_type = session.session_type

        screenshot_tool = self._screenshot_tool(session)
        browser = self._browser_key()
        capabilities = {
            "screenshot": session.available and screenshot_tool is not None,
            "open_application": session.available and browser is not None,
            "open_url": session.available and browser is not None,
        }
        notes = self._notes(session, capabilities)

        return HostStatus(
            hostname=socket.gethostname(),
            platform="linux",
            session_type=session_type,
            adapter=self.name,
            stub=False,
            uptime_seconds=metrics.get("uptime_seconds"),
            cpu_percent=metrics.get("cpu_percent"),
            memory_total_bytes=metrics.get("memory_total_bytes"),
            memory_used_bytes=metrics.get("memory_used_bytes"),
            disk_total_bytes=metrics.get("disk_total_bytes"),
            disk_used_bytes=metrics.get("disk_used_bytes"),
            display_count=self._display_count(),
            capabilities=capabilities,
            notes=notes,
        )

    def _notes(self, session: GraphicalSession, capabilities: dict) -> List[str]:
        """Say what is true about *this* session — never advertise a fix we
        have not verified, and never promise a session type the host may not
        offer."""
        notes: List[str] = []

        if not session.available:
            reason = session.reason or "no graphical session is active"
            notes.append(
                "GUI actions are unavailable: " + reason + ". The API stays up; "
                "opening applications and URLs resumes once a desktop session is logged in."
            )
            return notes

        if not capabilities["screenshot"]:
            if _session_display(session) == "wayland":
                notes.append(
                    "Screen capture is unavailable in this Wayland session: no capture tool "
                    "that works under Wayland is installed. Opening applications and URLs is "
                    "unaffected."
                )
            else:
                notes.append(
                    "Screen capture is unavailable: no screenshot tool is installed. "
                    "Opening applications and URLs is unaffected."
                )
        return notes

    def _browser_key(self) -> Optional[str]:
        """First allowlisted application that is actually installed."""
        for key, candidates in _APPLICATION_COMMANDS.items():
            if first_available(candidates):
                return key
        return None

    def _require_session(self) -> GraphicalSession:
        session = detect_graphical_session()
        if not session.available:
            raise AdapterUnsupported("no active graphical session", session.reason)
        return session

    def _confirm(self, application: str, launch: SessionLaunch) -> Optional[int]:
        """Turn an observed launch into a PID, or fail closed.

        ``running`` is direct evidence. ``exited`` is only acceptable when an
        instance of the same application is visibly alive to have taken the
        request over — otherwise nothing opened and saying "succeeded" would be
        exactly the false success this replaced.
        """
        if launch.state == "running":
            return launch.pid
        if process_running(_APPLICATION_PROCESS_NAMES.get(application, (application,))):
            return None
        raise AdapterError(
            "the application exited without opening anything",
            "the launcher exited with status "
            + str(launch.exit_status)
            + " and no running "
            + application
            + " process could be found",
        )

    def _display_count(self) -> Optional[int]:
        xrandr = first_available(("xrandr",))
        if not xrandr:
            return None
        try:
            completed = run_fixed([xrandr, "--listmonitors"], timeout=5)
        except AdapterError:
            return None
        if completed.returncode != 0:
            return None
        text = completed.stdout.decode("utf-8", "replace")
        for line in text.splitlines():
            if line.lower().startswith("monitors:"):
                try:
                    return int(line.split(":", 1)[1].strip())
                except ValueError:
                    return None
        return None

    # -- capabilities --------------------------------------------------------

    def _screenshot_tool(self, session: GraphicalSession):
        """The capture tool expected to work *in this session*, or ``None``.

        Presence on PATH is not the question. The question is whether a tool we
        have can reach the display of the session that exists right now, so the
        answer is derived entirely from ``session`` — never from
        :data:`os.environ`. Reading our own environment here was the M1.2
        defect: a service started at boot by lingering has no
        ``XDG_SESSION_TYPE``, so the Wayland guard below silently did not apply
        and ``scrot`` was offered on a Wayland host, where it fails with
        "Can't open X display".
        """
        if not session.available:
            return None
        display = _session_display(session)
        if display is None:
            return None
        for executable, build_argv in _SCREENSHOT_TOOLS:
            if display == "wayland" and executable in _X11_ROOT_CAPTURE_TOOLS:
                continue
            found = first_available((executable,))
            if found:
                return found, build_argv, executable
        return None

    def take_screenshot(self) -> Screenshot:
        session = self._require_session()
        selected = self._screenshot_tool(session)
        if selected is None:
            display = _session_display(session)
            if display == "wayland":
                raise AdapterUnsupported(
                    "no Wayland-safe screenshot tool found",
                    "scrot/maim/import capture a black frame under Wayland; no capture tool that "
                    "works under Wayland is installed",
                )
            if display is None:
                raise AdapterUnsupported(
                    "no screenshot tool found",
                    "the graphical session publishes no display endpoint to capture from",
                )
            raise AdapterUnsupported(
                "no screenshot tool found",
                "install one of: gnome-screenshot, maim, scrot, spectacle, imagemagick",
            )
        executable, build_argv, tool_name = selected

        handle, tmp_name = tempfile.mkstemp(prefix="cofferdam-shot-", suffix=".png")
        os.close(handle)
        tmp_path = Path(tmp_name)
        try:
            completed = run_fixed(
                build_argv(executable, str(tmp_path)),
                env=_capture_environment(session),
            )
            if completed.returncode != 0:
                raise AdapterError(
                    f"screenshot tool failed ({tool_name})",
                    completed.stderr.decode("utf-8", "replace"),
                )
            data = tmp_path.read_bytes()
            if not data:
                raise AdapterError(f"screenshot tool produced no image ({tool_name})")
            return Screenshot(png_bytes=data, tool=tool_name)
        except OSError as exc:
            raise AdapterError("could not read captured screenshot", exc) from exc
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    def open_application(self, application: str) -> ApplicationLaunch:
        if application not in APPLICATION_KEYS:
            raise AdapterUnsupported(f"application not allowlisted: {application}")
        session = self._require_session()
        executable = first_available(_APPLICATION_COMMANDS[application])
        if not executable:
            raise AdapterUnsupported(f"application not installed: {application}")
        launch = launch_in_session(
            [executable],
            description="Cofferdam: open " + application,
            expect_session=session.session_id,
        )
        pid = self._confirm(application, launch)
        return ApplicationLaunch(application=application, pid=pid, detail=Path(executable).name)

    def open_url(self, url: str) -> ApplicationLaunch:
        """Open a validated URL in an allowlisted browser.

        M1 used ``xdg-open``. It was dropped because it reports nothing usable:
        it exits 0 after delegating, whether or not the browser it delegates to
        ever starts, so a URL open could not be distinguished from a silent
        failure. Launching an allowlisted browser directly is verifiable, and
        keeps the same fixed-argv boundary.
        """
        session = self._require_session()
        application = self._browser_key()
        if application is None:
            raise AdapterUnsupported(
                "no allowlisted browser is installed",
                "install one of: " + ", ".join(_APPLICATION_COMMANDS),
            )
        executable = first_available(_APPLICATION_COMMANDS[application])
        # ``url`` has already been scheme-validated (http/https) by the action
        # schema; it is passed as one argv element, never through a shell.
        launch = launch_in_session(
            [executable, url],
            description="Cofferdam: open URL",
            expect_session=session.session_id,
        )
        pid = self._confirm(application, launch)
        return ApplicationLaunch(application=application, pid=pid, detail=Path(executable).name)

    def available_applications(self) -> List[str]:
        return [key for key, candidates in _APPLICATION_COMMANDS.items() if first_available(candidates)]
