"""Ubuntu Desktop adapter (X11 first).

Uses **semantic system commands**, never synthetic mouse/keyboard coordinates:

* screenshot — the first available of ``gnome-screenshot``, ``maim``,
  ``spectacle``, ``scrot``, or ImageMagick ``import``;
* launch — a fixed argv per allowlisted application key;
* open URL — ``xdg-open`` with the service-validated URL as a single argv item;
* displays — ``xrandr --listmonitors`` (count only in M1; targeting is M2).

**Not validated on a real Ubuntu host yet** — see
``docs/checklists/m1-ubuntu-validation.md``. Wayland: screenshots and window
control are restricted under GNOME's Wayland session, so the host runbook
selects an Xorg session; ``host_status`` reports ``session_type`` so the UI can
say so out loud.
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
    spawn_fixed,
)

# Logical key -> candidate executables, in preference order. Fixed table: a
# caller can pick a key, never a command.
_APPLICATION_COMMANDS = {
    "firefox": ("firefox", "firefox-esr"),
    "chromium": ("chromium", "chromium-browser"),
    "google-chrome": ("google-chrome", "google-chrome-stable"),
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


class LinuxX11Adapter(HostAdapter):
    name = "linux-x11"
    stub = False

    # -- status --------------------------------------------------------------

    def host_status(self) -> HostStatus:
        metrics = psutil_metrics()
        metrics.update(disk_metrics(str(Path.home())))
        notes: List[str] = []
        session_type = os.environ.get("XDG_SESSION_TYPE")
        if session_type == "wayland":
            notes.append(
                "Wayland session detected: screenshots and window control are restricted. "
                "Log in with 'Ubuntu on Xorg' for full support."
            )
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            notes.append("No DISPLAY/WAYLAND_DISPLAY: the service is not attached to a graphical session.")

        screenshot_tool = self._screenshot_tool()
        capabilities = {
            "screenshot": screenshot_tool is not None,
            "open_application": any(first_available(c) for c in _APPLICATION_COMMANDS.values()),
            "open_url": first_available(("xdg-open",)) is not None,
        }
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

    def _screenshot_tool(self):
        wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland"
        for executable, build_argv in _SCREENSHOT_TOOLS:
            if wayland and executable in _X11_ROOT_CAPTURE_TOOLS:
                continue
            found = first_available((executable,))
            if found:
                return found, build_argv, executable
        return None

    def take_screenshot(self) -> Screenshot:
        selected = self._screenshot_tool()
        if selected is None:
            if os.environ.get("XDG_SESSION_TYPE") == "wayland":
                raise AdapterUnsupported(
                    "no Wayland-safe screenshot tool found",
                    "scrot/maim/import capture a black frame under Wayland; install gnome-screenshot "
                    "or log in with 'Ubuntu on Xorg'",
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
            completed = run_fixed(build_argv(executable, str(tmp_path)))
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
        executable = first_available(_APPLICATION_COMMANDS[application])
        if not executable:
            raise AdapterUnsupported(f"application not installed: {application}")
        pid = spawn_fixed([executable])
        return ApplicationLaunch(application=application, pid=pid, detail=Path(executable).name)

    def open_url(self, url: str) -> ApplicationLaunch:
        opener = first_available(("xdg-open",))
        if not opener:
            raise AdapterUnsupported("xdg-open is not installed")
        # ``url`` has already been scheme-validated (http/https) by the action
        # schema; it is passed as one argv element, never through a shell.
        pid = spawn_fixed([opener, url])
        return ApplicationLaunch(application="default-browser", pid=pid, detail="xdg-open")

    def available_applications(self) -> List[str]:
        return [key for key, candidates in _APPLICATION_COMMANDS.items() if first_available(candidates)]
