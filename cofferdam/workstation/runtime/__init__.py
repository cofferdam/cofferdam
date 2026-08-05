"""Runtime inventory (M2B) — what actually exists on this machine right now.

This is the second of the three layers in D-2026-08-04-6, and the one M2A
deliberately did not have:

1. **definitions** — code-owned: which applications exist as a concept and which
   launch adapters are permitted. ``cofferdam/workstation/adapters/``.
2. **runtime resources** — *this package*: connected displays, running
   processes, application instances, windows. Everything here comes from an
   observation of the machine made at a stated instant.
3. **user overlays** — optional labels and aliases.
   ``cofferdam/workstation/registries/``.

A definition being available never means something is running. A browser profile
existing never means a browser is open. A registry entry never conjures a
display. Those three sentences are why the layers are separate packages rather
than fields on one model.

Module map
----------

==========================  ==================================================
module                      responsibility
==========================  ==================================================
``models``                  the versioned snapshot shape and the closed
                            ``ok``/``partial``/``unavailable``/``error``
                            vocabulary every collection reports in
``identity``                host, boot and graphical-session identity, and the
                            fingerprints derived from them
``dbus``                    bounded read-only calls to the session bus
``edid``                    the minimum EDID parsing needed for a hardware
                            fingerprint and physical size
``displays``                ``DisplayDiscovery`` — Mutter's display
                            configuration joined to the kernel's DRM connectors
``processes``               ``ProcessDiscovery`` — ``/proc``, never a command
                            line and never an environment block
``applications``            ``ApplicationInstanceDiscovery`` — systemd cgroup
                            scopes as the instance boundary
``windows``                 ``WindowDiscovery`` — the interface, and why it
                            reports ``unavailable`` on GNOME Wayland
``overlays``                ``OverlayResolver`` — attaches a user's label to a
                            discovered resource without becoming its identity
``service``                 ``RuntimeInventoryService`` — one scan per
                            snapshot, short-lived cache, session-aware
                            invalidation
==========================  ==================================================

Every backend states, in its own docstring, the resources it owns, the evidence
it uses, its limitations, and its status semantics. Those are not notes: a
backend that cannot answer must say ``unavailable`` with a reason, and an empty
``ok`` collection is a positive claim that the machine has none of that
resource.

This package is **read-only**. It observes; it does not launch, stop, move,
configure, or terminate anything. It runs no shell, evaluates nothing inside the
compositor, and takes no screenshots.
"""

from __future__ import annotations

from .applications import ApplicationInstanceDiscovery
from .displays import DisplayDiscovery
from .identity import (
    BootIdentity,
    HostIdentity,
    SessionIdentity,
    detect_boot_identity,
    detect_host_identity,
    session_identity_from,
)
from .models import (
    KIND_APPLICATIONS,
    KIND_DISPLAYS,
    KIND_PROCESSES,
    KIND_WINDOWS,
    RESOURCE_KINDS,
    RUNTIME_SNAPSHOT_VERSION,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_UNAVAILABLE,
    Evidence,
    ResourceCollection,
    RuntimeSnapshot,
)
from .overlays import OverlayResolver
from .processes import ProcessDiscovery
from .service import RuntimeInventoryService
from .windows import WindowDiscovery

__all__ = [
    "ApplicationInstanceDiscovery",
    "BootIdentity",
    "DisplayDiscovery",
    "Evidence",
    "HostIdentity",
    "KIND_APPLICATIONS",
    "KIND_DISPLAYS",
    "KIND_PROCESSES",
    "KIND_WINDOWS",
    "OverlayResolver",
    "ProcessDiscovery",
    "RESOURCE_KINDS",
    "RUNTIME_SNAPSHOT_VERSION",
    "ResourceCollection",
    "RuntimeInventoryService",
    "RuntimeSnapshot",
    "STATUS_ERROR",
    "STATUS_OK",
    "STATUS_PARTIAL",
    "STATUS_UNAVAILABLE",
    "SessionIdentity",
    "WindowDiscovery",
    "detect_boot_identity",
    "detect_host_identity",
    "session_identity_from",
]
