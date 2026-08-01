"""Deterministic stub adapter for tests and unsupported platforms.

**The stub never pretends to be a real host.** ``stub=True`` appears in host
status and in every action result it produces, and the PWA shows a persistent
banner while it is active, so an Ubuntu acceptance run cannot be passed by
stubbed behaviour (``docs/checklists/m1-ubuntu-validation.md`` requires the
adapter reported by ``/api/status`` to be ``linux-x11``).
"""

from __future__ import annotations

import socket
import struct
import zlib
from typing import List, Optional

from ..errors import AdapterUnsupported
from .base import APPLICATION_KEYS, ApplicationLaunch, HostAdapter, HostStatus, Screenshot


def _placeholder_png(width: int = 64, height: int = 64) -> bytes:
    """A tiny valid grey PNG, built with zlib only (no image dependency)."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit truecolour
    raw = b"".join(b"\x00" + bytes([0x40, 0x44, 0x4C] * width) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


class StubAdapter(HostAdapter):
    name = "stub"
    stub = True

    def __init__(self, config, *, fail: Optional[str] = None) -> None:
        super().__init__(config)
        self._fail = fail
        self.launched: List[str] = []
        self.opened_urls: List[str] = []

    def host_status(self) -> HostStatus:
        return HostStatus(
            hostname=socket.gethostname(),
            platform="stub",
            session_type=None,
            adapter=self.name,
            stub=True,
            uptime_seconds=1234.0,
            cpu_percent=1.5,
            memory_total_bytes=16 * 1024**3,
            memory_used_bytes=4 * 1024**3,
            disk_total_bytes=512 * 1024**3,
            disk_used_bytes=128 * 1024**3,
            display_count=1,
            capabilities={"screenshot": True, "open_application": True, "open_url": True},
            notes=["Stub adapter: no real host is being controlled."],
        )

    def take_screenshot(self) -> Screenshot:
        if self._fail == "screenshot":
            raise AdapterUnsupported("stub failure: screenshot")
        return Screenshot(png_bytes=_placeholder_png(), width=64, height=64, tool="stub")

    def open_application(self, application: str) -> ApplicationLaunch:
        if application not in APPLICATION_KEYS:
            raise AdapterUnsupported(f"application not allowlisted: {application}")
        if self._fail == "open_application":
            raise AdapterUnsupported("stub failure: open_application")
        self.launched.append(application)
        return ApplicationLaunch(application=application, pid=4242, detail="stub")

    def open_url(self, url: str) -> ApplicationLaunch:
        if self._fail == "open_url":
            raise AdapterUnsupported("stub failure: open_url")
        self.opened_urls.append(url)
        return ApplicationLaunch(application="default-browser", pid=4243, detail="stub")
