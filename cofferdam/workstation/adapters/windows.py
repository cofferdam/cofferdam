"""Windows adapter — development convenience only.

Ubuntu Desktop is the supported host (``DECISIONS.md`` D-2026-08-01-1). This
adapter exists so the service can be built and exercised for real on the
maintainer's Windows machine before the Ubuntu host is available; it is not a
supported deployment target and is never used to satisfy Ubuntu acceptance.

Screenshot uses a **fixed** PowerShell script (no caller input reaches it) that
copies the virtual screen into a PNG at a service-generated path.
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

_APPLICATION_COMMANDS = {
    "firefox": (
        "firefox",
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    ),
    "chromium": ("chromium", "chrome"),
    "google-chrome": (
        "chrome",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ),
    # M2A: the browser-profile registry can select Opera. Ubuntu is the
    # supported host; this entry only keeps the development adapter able to
    # honour the same logical key. Opera's Windows entry point is
    # ``launcher.exe``, given as an absolute path rather than a bare name —
    # "launcher" is generic enough that anything by that name on PATH could be
    # started instead.
    "opera": (
        "opera",
        r"C:\Program Files\Opera\launcher.exe",
        r"C:\Program Files (x86)\Opera\launcher.exe",
    ),
}

# Fixed capture script. The only substituted value is a path this service
# generated with tempfile; no caller text is ever interpolated.
_CAPTURE_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
    "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen; "
    "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
    "$g = [System.Drawing.Graphics]::FromImage($bmp); "
    "$g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size); "
    "$bmp.Save('{path}', [System.Drawing.Imaging.ImageFormat]::Png); "
    "$g.Dispose(); $bmp.Dispose();"
)


def _resolve(candidates) -> Optional[str]:
    found = first_available(candidates)
    if found:
        return found
    for candidate in candidates:
        if os.path.isabs(candidate) and Path(candidate).is_file():
            return candidate
    return None


class WindowsAdapter(HostAdapter):
    name = "windows"
    stub = False

    def host_status(self) -> HostStatus:
        metrics = psutil_metrics()
        metrics.update(disk_metrics(str(Path.home())))
        capabilities = {
            "screenshot": first_available(("powershell", "pwsh")) is not None,
            "open_application": any(_resolve(c) for c in _APPLICATION_COMMANDS.values()),
            "open_url": True,
        }
        return HostStatus(
            hostname=socket.gethostname(),
            platform="windows",
            session_type="windows-desktop",
            adapter=self.name,
            stub=False,
            uptime_seconds=metrics.get("uptime_seconds"),
            cpu_percent=metrics.get("cpu_percent"),
            memory_total_bytes=metrics.get("memory_total_bytes"),
            memory_used_bytes=metrics.get("memory_used_bytes"),
            disk_total_bytes=metrics.get("disk_total_bytes"),
            disk_used_bytes=metrics.get("disk_used_bytes"),
            display_count=None,
            capabilities=capabilities,
            notes=["Windows is a development host only; Ubuntu Desktop is the supported platform."],
        )

    def take_screenshot(self) -> Screenshot:
        shell = first_available(("powershell", "pwsh"))
        if not shell:
            raise AdapterUnsupported("powershell not found")

        handle, tmp_name = tempfile.mkstemp(prefix="cofferdam-shot-", suffix=".png")
        os.close(handle)
        tmp_path = Path(tmp_name)
        try:
            script = _CAPTURE_SCRIPT.format(path=str(tmp_path).replace("'", "''"))
            completed = run_fixed([shell, "-NoProfile", "-NonInteractive", "-Command", script])
            if completed.returncode != 0:
                raise AdapterError("screen capture failed", completed.stderr.decode("utf-8", "replace"))
            data = tmp_path.read_bytes()
            if not data:
                raise AdapterError("screen capture produced no image")
            return Screenshot(png_bytes=data, tool="powershell-gdi")
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
        executable = _resolve(_APPLICATION_COMMANDS[application])
        if not executable:
            raise AdapterUnsupported(f"application not installed: {application}")
        pid = spawn_fixed([executable])
        return ApplicationLaunch(application=application, pid=pid, detail=Path(executable).name)

    def open_url(self, url: str, application: Optional[str] = None) -> ApplicationLaunch:
        # Prefer launching an allowlisted browser with the URL as a single argv
        # element. ``url`` was scheme-validated (http/https) by the action schema.
        if application is not None:
            candidates = _APPLICATION_COMMANDS.get(application)
            if candidates is None:
                raise AdapterUnsupported(f"application not allowlisted: {application}")
            executable = _resolve(candidates)
            if not executable:
                raise AdapterUnsupported(f"application not installed: {application}")
            pid = spawn_fixed([executable, url])
            return ApplicationLaunch(
                application=application, pid=pid, detail=Path(executable).name
            )
        for key in ("firefox", "google-chrome", "chromium"):
            executable = _resolve(_APPLICATION_COMMANDS[key])
            if executable:
                pid = spawn_fixed([executable, url])
                return ApplicationLaunch(application=key, pid=pid, detail=Path(executable).name)
        try:
            import webbrowser

            if not webbrowser.open(url):
                raise AdapterError("no browser available to open the URL")
        except OSError as exc:
            raise AdapterError("could not open the URL", exc) from exc
        return ApplicationLaunch(application="default-browser", pid=None, detail="webbrowser")

    def available_applications(self) -> List[str]:
        return [key for key, candidates in _APPLICATION_COMMANDS.items() if _resolve(candidates)]
