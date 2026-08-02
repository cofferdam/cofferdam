"""LinuxX11Adapter screenshot-tool selection under Wayland.

M1 Ubuntu validation (real GNOME/Wayland host) found that X11 root-window
capture tools (``scrot``, ``maim``, ``import``) exit 0 and write a non-empty
PNG under a Wayland session, but the image is solid black — XWayland exposes
an empty placeholder root window, not the compositor's real framebuffer. That
is a false success: the adapter must not offer these tools as a valid
screenshot capability when the session is Wayland, and must fail closed
(``AdapterUnsupported``) rather than serve a black image as if it were real.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from cofferdam.workstation.adapters.linux_x11 import LinuxX11Adapter
from cofferdam.workstation.errors import AdapterUnsupported


def _adapter() -> LinuxX11Adapter:
    return LinuxX11Adapter(config=None)


class WaylandScreenshotSafetyTests(unittest.TestCase):
    def test_x11_root_capture_tools_are_rejected_under_wayland(self) -> None:
        """Only scrot is on PATH, but the session is Wayland: no tool is usable."""
        adapter = _adapter()
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=False), patch(
            "cofferdam.workstation.adapters.linux_x11.first_available",
            side_effect=lambda candidates: "/usr/bin/scrot" if "scrot" in candidates else None,
        ):
            self.assertIsNone(adapter._screenshot_tool())
            self.assertFalse(adapter.host_status().capabilities["screenshot"])
            with self.assertRaises(AdapterUnsupported) as ctx:
                adapter.take_screenshot()
            self.assertIn("Wayland", ctx.exception.message)

    def test_gnome_screenshot_is_still_usable_under_wayland(self) -> None:
        """A Wayland-safe tool (gnome-screenshot) is not filtered out."""
        adapter = _adapter()
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=False), patch(
            "cofferdam.workstation.adapters.linux_x11.first_available",
            side_effect=lambda candidates: (
                "/usr/bin/gnome-screenshot" if "gnome-screenshot" in candidates else None
            ),
        ):
            selected = adapter._screenshot_tool()
            self.assertIsNotNone(selected)
            self.assertEqual(selected[2], "gnome-screenshot")

    def test_scrot_is_still_usable_outside_wayland(self) -> None:
        """The X11 runbook (session_type=x11) keeps working: no regression there."""
        adapter = _adapter()
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11"}, clear=False), patch(
            "cofferdam.workstation.adapters.linux_x11.first_available",
            side_effect=lambda candidates: "/usr/bin/scrot" if "scrot" in candidates else None,
        ):
            selected = adapter._screenshot_tool()
            self.assertIsNotNone(selected)
            self.assertEqual(selected[2], "scrot")
            self.assertTrue(adapter.host_status().capabilities["screenshot"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
