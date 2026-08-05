"""Current windows — and why this host cannot report them.

The interface exists and is wired into the snapshot. On this build it returns
``unavailable`` with a precise reason. That is the finding, not a stub.

What was investigated on the real host (GNOME Shell 50.1, Wayland)
------------------------------------------------------------------

``org.gnome.Shell.Eval``
    Present on the bus and **returns ``(false, '')`` for every expression**:
    GNOME disables it outside unsafe-mode in release builds. It is also barred
    on our own side — evaluating JavaScript inside the compositor is arbitrary
    code execution in the user's shell, which is exactly the class of
    "unsupported shell-eval trick" the milestone rules out. It would not be used
    even if it answered.

``org.gnome.Mutter.DisplayConfig``
    Answers fully — this is what display discovery uses — but its interface is
    monitors and layout. It has no window vocabulary at all.

Portals (``org.freedesktop.portal.*``)
    The portal surface is about granting a *user-approved* capability for a
    specific interaction (open a file, cast a screen). There is no portal that
    enumerates the window list, and the screenshot portal was already found on
    this host to never emit a ``Response`` even non-interactively (recorded in
    ``ROADMAP.md`` under Wayland capture).

Accessibility (AT-SPI)
    ``org.a11y.Bus`` is running and hands out a bus address, so the mechanism
    exists. But ``org.gnome.desktop.interface toolkit-accessibility`` is
    **false** on this host, which means GTK applications do not export their
    trees; the bus would answer with almost nothing. Turning that setting on is
    a change to the user's desktop configuration, and it carries a real cost —
    accessibility bridges are a documented way for one application to read
    another's contents. Enabling it silently to make an inventory look complete
    is not a trade this milestone gets to make on the user's behalf.

Deliberately not used
    Pixel matching, OCR, screenshots, fixed coordinates, blind clicking, and
    installing a GNOME extension without the user asking for one. Each could
    produce a window list. None produces *evidence*, and an extension is a
    persistent change to the desktop.

Conclusion: there is no safe, read-only, semantic window enumeration available
to this build on GNOME Wayland. The honest report is ``unavailable`` with the
reason above — never an empty ``ok`` list, which would tell a user their
applications have no windows open while they are looking at those windows.

The seam for later
------------------
:class:`WindowDiscovery` is the interface a future backend implements. The
expected shape is a **GNOME companion extension** the user installs knowingly
(``docs/DESKTOP_APP.md`` records the companion direction), exposing a narrow
read-only D-Bus method that lists windows with their owning application's PID.
Adding it means adding a backend here and returning ``ok``; no other module
changes, and the snapshot shape already carries the field.

If window titles ever are exposed, they are treated as sensitive: not written to
logs, not persisted, and served only through the authenticated API. A window
title is routinely a document name, a message subject, or a customer's name.
"""

from __future__ import annotations

from typing import Optional

from .models import KIND_WINDOWS, Evidence, ResourceCollection, unavailable

BACKEND_NONE = "none-available"

UNAVAILABLE_REASON = (
    "this desktop exposes no safe, read-only interface for listing windows: GNOME's shell "
    "evaluation endpoint is disabled (and would be arbitrary code execution in the compositor), "
    "no portal enumerates windows, and the accessibility bridge is switched off on this host. "
    "Cofferdam will not guess from screenshots or install a shell extension to answer this."
)

LIMITATIONS = (
    "window discovery has no backend on GNOME Wayland in this build",
    "an unavailable window list is not an empty one: applications may well have windows open",
    "window titles would be treated as sensitive and are never logged or persisted",
)


class WindowDiscovery:
    """Resources owned: the user's current windows.

    Evidence: none available on this host — see the module docstring for what
    was tried.

    Limitations: returns ``unavailable`` with a reason on every current
    platform. It never returns a successful empty list, because "no windows"
    and "cannot see windows" are different facts and only one of them is true
    here.
    """

    kind = KIND_WINDOWS

    def collect(self, session) -> ResourceCollection:
        evidence = Evidence(backend=BACKEND_NONE, limitations=LIMITATIONS)

        if not getattr(session, "available", False):
            # Before login there are no windows *and* no way to look. Say the
            # first, since it is the more specific and more useful answer.
            return unavailable(
                self.kind,
                getattr(session, "reason", None)
                or "no graphical session is active, so no windows exist yet",
                evidence,
            )
        return unavailable(self.kind, UNAVAILABLE_REASON, evidence)

    def window_count_for(self, application_instance_id: str) -> Optional[int]:
        """How many windows an instance has, or ``None`` when unknowable.

        ``None``, never ``0``. A future backend replaces this; until then the
        application cards show no window count rather than a zero that reads as
        "this application has no windows open".
        """
        return None


__all__ = ["BACKEND_NONE", "LIMITATIONS", "UNAVAILABLE_REASON", "WindowDiscovery"]
