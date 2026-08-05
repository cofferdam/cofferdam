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
from typing import List, Optional, Sequence

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

# Browser key -> candidate executables, in preference order. Fixed table: a
# caller can pick a key, never a command.
#
# **Declaration order is behaviour**, not style: ``_browser_key`` walks this
# mapping for the legacy no-profile URL path and takes the first browser found
# on PATH. Opera stays appended last so that path keeps choosing exactly what it
# chose before M2A. Cofferdam's *product* preference for Opera is expressed
# separately, in ``browser_selection``, where it can be seen and overridden.
_BROWSER_COMMANDS = {
    "firefox": ("firefox", "firefox-esr"),
    "chromium": ("chromium", "chromium-browser"),
    "google-chrome": ("google-chrome", "google-chrome-stable"),
    "opera": ("opera", "opera-stable"),
}

# Everything launchable, browsers included. M2B3A adds the first non-browser
# entry, so the two tables stop being the same thing: a browser can open a URL,
# an application can only be started (or handed a URI on its own scheme).
# Spotify is the real snap-packaged desktop application on this host — no
# wrapper, no repackaged web page.
_APPLICATION_COMMANDS = dict(_BROWSER_COMMANDS)
_APPLICATION_COMMANDS["spotify"] = ("spotify",)

# Which applications may be handed a URI, and on which scheme. Verified against
# the installed application before being listed: Spotify's desktop entry on this
# host declares ``MimeType=x-scheme-handler/spotify`` and ``Exec=… %U``, so a
# ``spotify:`` URI is its own documented entry point rather than a guess.
_APPLICATION_URI_SCHEMES = {"spotify": ("spotify",)}

# Non-zero exit codes that mean "an instance was already running, I handed the
# request to it, and I am done" — *not* "I failed to start".
#
# 24 is Chromium's ``CHROME_RESULT_CODE_NORMAL_EXIT_PROCESS_NOTIFIED``.
# Observed directly on this host (M2A validation, snap-packaged Opera 133):
# launching `opera <url>` while Opera is running prints "Opening in existing
# browser session.", opens the URL as a new tab, and exits **24**. systemd marks
# any non-zero exit as ``failed``, so without this the adapter reported a tab
# that visibly opened as an error.
#
# This does not relax the M1 rule that a launcher's exit code is never evidence
# on its own. The code is accepted only for this application, only for this
# exact value, and the launch still has to be corroborated by ``_confirm``
# finding a live instance of the same application — which is precisely the
# situation this code describes. Every other exit status still fails closed.
#
# Other Chromium-based browsers share the constant; they are deliberately not
# listed until the behaviour has been observed for them here.
_DELEGATION_EXIT_STATUS = {"opera": (24,)}

# Desktop-entry basenames, used **only** to report that an application exists
# when its executable is not on PATH — a Flatpak or an unusual snap layout.
# Nothing is ever launched through a desktop file, and no path from a registry
# or an API request is ever consulted: this table and the directory list below
# are code-owned and closed. ``opera_opera.desktop`` is Ubuntu's snap naming for
# Opera; it is listed because that is the real name on this platform, not
# because desktop-file names are configurable.
_APPLICATION_DESKTOP_ENTRIES = {
    "firefox": ("firefox.desktop", "firefox-esr.desktop", "firefox_firefox.desktop"),
    "chromium": ("chromium.desktop", "chromium-browser.desktop", "chromium_chromium.desktop"),
    "google-chrome": ("google-chrome.desktop",),
    "opera": ("opera.desktop", "opera-stable.desktop", "opera_opera.desktop"),
    "spotify": ("spotify.desktop", "spotify_spotify.desktop"),
}

_DESKTOP_ENTRY_DIRECTORIES = (
    "/usr/share/applications",
    "/usr/local/share/applications",
    "/var/lib/snapd/desktop/applications",
    "/var/lib/flatpak/exports/share/applications",
)

_USER_DESKTOP_ENTRY_DIRECTORIES = (
    ".local/share/applications",
    ".local/share/flatpak/exports/share/applications",
)

# Logical key -> process names an already-running instance may present. Used
# only to corroborate a fast clean exit; a browser handed a request by an
# existing instance returns immediately. Snap and deb builds differ, hence the
# aliases.
_APPLICATION_PROCESS_NAMES = {
    "firefox": ("firefox", "firefox-esr", "firefox-bin"),
    "chromium": ("chromium", "chromium-browser", "chrome"),
    "google-chrome": ("chrome", "google-chrome", "google-chrome-stable"),
    "opera": ("opera", "opera-stable"),
    "spotify": ("spotify",),
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


def _desktop_entry_present(names: Sequence[str]) -> bool:
    """Is one of these bounded desktop-entry basenames installed?

    Searches a fixed list of XDG application directories for exact basenames
    from the code-owned table. No pattern from configuration, no recursion, and
    no reading of the entries themselves — presence is the only question asked.
    """
    if not names:
        return False
    directories = [Path(directory) for directory in _DESKTOP_ENTRY_DIRECTORIES]
    try:
        home = Path.home()
    except (OSError, RuntimeError):  # pragma: no cover - no home directory
        home = None
    if home is not None:
        directories.extend(home / relative for relative in _USER_DESKTOP_ENTRY_DIRECTORIES)

    for directory in directories:
        for name in names:
            try:
                if (directory / name).is_file():
                    return True
            except OSError:  # pragma: no cover - unreadable mount point
                continue
    return False


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
        """First allowlisted application that is actually installed.

        This is the pre-M2A URL path and must stay exactly as it was: it walks
        ``_BROWSER_COMMANDS`` in declaration order and takes the first
        executable found on ``PATH``. Desktop-entry detection is deliberately
        *not* consulted here — an application we can see but cannot launch is
        not a browser this path may choose.

        It walks the *browser* table, never the application table. M2B3A added
        the first non-browser application, and iterating the wider table here
        would eventually let something that cannot render a web page be elected
        this host's browser.
        """
        for key, candidates in _BROWSER_COMMANDS.items():
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
            accept_exit_status=_DELEGATION_EXIT_STATUS.get(application, ()),
            expect_session=session.session_id,
        )
        pid = self._confirm(application, launch)
        return ApplicationLaunch(application=application, pid=pid, detail=Path(executable).name)

    def open_url(self, url: str, application: Optional[str] = None) -> ApplicationLaunch:
        """Open a validated URL in an allowlisted browser.

        M1 used ``xdg-open``. It was dropped because it reports nothing usable:
        it exits 0 after delegating, whether or not the browser it delegates to
        ever starts, so a URL open could not be distinguished from a silent
        failure. Launching an allowlisted browser directly is verifiable, and
        keeps the same fixed-argv boundary.

        ``application`` (M2A) is the logical key a browser profile resolved to,
        or ``None`` for the legacy "first installed browser" behaviour. Passing
        the URL to an already-running Opera hands it to that existing instance,
        which opens a new tab in the user's session — this never starts a second,
        isolated browser and never touches a profile directory.

        Only a browser key is accepted. Since M2B3A the application allowlist is
        wider than the browser table, and "open this web page in Spotify" has to
        be a refusal rather than something the adapter tries.
        """
        session = self._require_session()
        if application is None:
            application = self._browser_key()
            if application is None:
                raise AdapterUnsupported(
                    "no allowlisted browser is installed",
                    "install one of: " + ", ".join(_BROWSER_COMMANDS),
                )
        elif application not in _BROWSER_COMMANDS:
            raise AdapterUnsupported(
                f"not an allowlisted browser: {application}",
                "URLs may only be opened in: " + ", ".join(_BROWSER_COMMANDS),
            )

        executable = first_available(_BROWSER_COMMANDS[application])
        if not executable:
            raise AdapterUnsupported(f"application not installed: {application}")
        # ``url`` has already been scheme-validated (http/https) by the action
        # schema; it is passed as one argv element, never through a shell.
        launch = launch_in_session(
            [executable, url],
            description="Cofferdam: open URL",
            accept_exit_status=_DELEGATION_EXIT_STATUS.get(application, ()),
            expect_session=session.session_id,
        )
        pid = self._confirm(application, launch)
        return ApplicationLaunch(application=application, pid=pid, detail=Path(executable).name)

    def open_application_uri(self, application: str, uri: str) -> ApplicationLaunch:
        """Start an allowlisted application with a URI on its own scheme.

        The URI reaches the program the same way a URL reaches a browser: as one
        argv element of a fixed vector, through the systemd user manager, never
        a shell and never a concatenated string. Two independent gates stand in
        front of it — the application must appear in
        ``_APPLICATION_URI_SCHEMES``, and the URI's scheme must be one that
        entry permits — so an accepted key can never be handed a scheme it did
        not register for.

        ``xdg-open`` is deliberately not used, for the reason recorded in
        :meth:`open_url`: it exits 0 after delegating whether or not anything
        started, which is unreportable. Launching the application directly keeps
        the outcome observable, and ``_confirm`` still has to corroborate it.
        """
        allowed_schemes = _APPLICATION_URI_SCHEMES.get(application)
        if allowed_schemes is None:
            raise AdapterUnsupported(
                f"application does not accept URIs: {application}",
                "URI launches are available for: " + ", ".join(_APPLICATION_URI_SCHEMES),
            )
        scheme = uri.split(":", 1)[0].lower() if ":" in uri else ""
        if scheme not in allowed_schemes:
            raise AdapterUnsupported(
                "that URI scheme is not allowed for this application",
                application + " accepts: " + ", ".join(s + ":" for s in allowed_schemes),
            )

        session = self._require_session()
        executable = first_available(_APPLICATION_COMMANDS[application])
        if not executable:
            raise AdapterUnsupported(f"application not installed: {application}")
        launch = launch_in_session(
            [executable, uri],
            description="Cofferdam: open " + application + " URI",
            accept_exit_status=_DELEGATION_EXIT_STATUS.get(application, ()),
            expect_session=session.session_id,
        )
        pid = self._confirm(application, launch)
        return ApplicationLaunch(application=application, pid=pid, detail=Path(executable).name)

    def available_applications(self) -> List[str]:
        """Applications this host can actually launch — executable on ``PATH``.

        Deliberately *not* widened by desktop-entry detection. Everything listed
        here is offered as a control on the phone, so it must be launchable, not
        merely present. Desktop metadata is consulted only to explain an absence
        (:meth:`unavailable_detail`), where "installed but not on PATH" and "not
        installed" call for completely different fixes.
        """
        return [key for key, candidates in _APPLICATION_COMMANDS.items() if first_available(candidates)]

    def application_executables(self) -> dict:
        """The launch table, inverted for runtime discovery.

        The candidate names plus the basename of whatever was found on ``PATH``
        — and deliberately **not** the basename of the symlink's final target.
        On Ubuntu ``/snap/bin/opera`` resolves to ``/usr/bin/snap``, the generic
        snap multiplexer, so following the link would put ``snap`` in Opera's
        name set and classify every unrelated snap helper as Opera. The real
        program keeps its own name where it matters: the process that actually
        runs is ``/snap/opera/<rev>/usr/lib/.../opera``, whose basename is
        already in the table.
        """
        table: dict = {}
        for key, candidates in _APPLICATION_COMMANDS.items():
            names = set(candidates)
            found = first_available(candidates)
            if found:
                names.add(Path(found).name)
            table[key] = tuple(sorted(names))
        return table

    def unavailable_detail(self, application: str) -> Optional[str]:
        if _desktop_entry_present(_APPLICATION_DESKTOP_ENTRIES.get(application, ())):
            return (
                "a desktop entry for this application exists on the host, but no launchable "
                "executable was found on PATH"
            )
        return None
