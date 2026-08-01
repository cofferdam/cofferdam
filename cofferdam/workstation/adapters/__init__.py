"""Host adapters — the only place platform-specific behaviour may live.

Selection order for ``adapter="auto"``: native Linux → :class:`LinuxX11Adapter`,
native Windows → :class:`WindowsAdapter` (development convenience), anything
else → :class:`StubAdapter`.

The stub is **always identifiable** (``stub=True`` in status and in every action
result) so an Ubuntu acceptance run cannot be passed accidentally by a stub.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from .base import ApplicationLaunch, HostAdapter, HostStatus, Screenshot, APPLICATION_KEYS
from .stub import StubAdapter

if TYPE_CHECKING:  # pragma: no cover
    from ..config import Config

__all__ = [
    "APPLICATION_KEYS",
    "ApplicationLaunch",
    "HostAdapter",
    "HostStatus",
    "Screenshot",
    "StubAdapter",
    "select_adapter",
]


def select_adapter(name: str, config: "Config") -> HostAdapter:
    """Instantiate the adapter named by config (``auto`` resolves per platform)."""
    requested = (name or "auto").strip().lower()

    if requested == "stub":
        return StubAdapter(config)
    if requested in ("linux", "linux-x11", "linux_x11"):
        from .linux_x11 import LinuxX11Adapter

        return LinuxX11Adapter(config)
    if requested == "windows":
        from .windows import WindowsAdapter

        return WindowsAdapter(config)
    if requested != "auto":
        raise ValueError(f"unknown adapter: {requested!r}")

    if sys.platform.startswith("linux"):
        from .linux_x11 import LinuxX11Adapter

        return LinuxX11Adapter(config)
    if sys.platform == "win32":
        from .windows import WindowsAdapter

        return WindowsAdapter(config)
    return StubAdapter(config)
