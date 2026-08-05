"""One scan, one snapshot: the orchestration the API and the PWA both read.

The service exists so the four backends are called **once, together**, from one
walk of ``/proc`` and one query to the compositor. Letting each endpoint collect
independently would let a client assemble a picture whose displays came from one
instant and whose processes came from another — a snapshot that never existed.
The sub-endpoints therefore serve slices of the same snapshot, not separate
scans.

Caching
-------
Very short-lived, and it is a *rate limit* more than a cache. A phone polling
the live view every few seconds must not make the workstation walk its whole
process table every few seconds, but an inventory that describes anything other
than roughly-now is not an inventory. Hence a few seconds' reuse, always
published as ``observed_at`` so a client can see the age it is being served, and
a caller-driven bypass for the refresh button.

The cache is invalidated by **identity**, not only by time. If the graphical
session changed — a logout and a fresh login — the cached snapshot describes a
session that no longer exists and is dropped, even if it is a millisecond old.
The same holds for a boot identity change, which cannot happen without a reboot
but is checked because a stale process identity is the dangerous one. This is
what stops a disconnected display or an ended session's windows being served as
current.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from ..adapters.linux_session import detect_graphical_session
from .applications import ApplicationInstanceDiscovery
from .displays import DisplayDiscovery
from .identity import (
    detect_boot_identity,
    detect_host_identity,
    now_iso,
    session_identity_from,
)
from .models import (
    KIND_APPLICATIONS,
    KIND_DISPLAYS,
    KIND_PROCESSES,
    KIND_WINDOWS,
    RESOURCE_KINDS,
    RuntimeSnapshot,
    unavailable,
)
from .overlays import OverlayResolver
from .processes import ProcessDiscovery
from .windows import WindowDiscovery

# Long enough that a polling phone does not drive a continuous process scan,
# short enough that "what is on my desk right now" stays a true description.
DEFAULT_CACHE_SECONDS = 5.0


def _monotonic() -> float:
    return time.monotonic()


class RuntimeInventoryService:
    """Collects one :class:`RuntimeSnapshot` per scan, with a short cache.

    ``registries`` and ``adapter`` are injected rather than imported so tests
    can drive the whole assembly with doubles, and so a non-Linux adapter simply
    contributes no application definitions instead of special-casing platforms
    here.
    """

    def __init__(
        self,
        adapter=None,
        registry_loader=None,
        cache_seconds: float = DEFAULT_CACHE_SECONDS,
        session_detector=detect_graphical_session,
        display_discovery: Optional[DisplayDiscovery] = None,
        process_discovery: Optional[ProcessDiscovery] = None,
        window_discovery: Optional[WindowDiscovery] = None,
        clock=_monotonic,
    ) -> None:
        self._adapter = adapter
        self._registry_loader = registry_loader
        self._cache_seconds = max(0.0, cache_seconds)
        self._session_detector = session_detector
        self._displays = display_discovery or DisplayDiscovery()
        self._processes = process_discovery or ProcessDiscovery()
        self._windows = window_discovery or WindowDiscovery()
        self._overlays = OverlayResolver()
        self._clock = clock

        self._lock = threading.Lock()
        self._cached: Optional[RuntimeSnapshot] = None
        self._cached_at: float = 0.0

    # -- public API ----------------------------------------------------------

    def snapshot(self, refresh: bool = False) -> RuntimeSnapshot:
        """The current snapshot, collected or reused.

        Holds the lock across collection deliberately: two concurrent requests
        should produce one scan, not two competing walks of ``/proc``.
        """
        with self._lock:
            if not refresh:
                cached = self._usable_cached()
                if cached is not None:
                    return cached
            snapshot = self._collect()
            self._cached = snapshot
            self._cached_at = self._clock()
            return snapshot

    def collection(self, kind: str, refresh: bool = False) -> Tuple[RuntimeSnapshot, Any]:
        """One collection plus the snapshot header it belongs to.

        Both are returned because a collection without its ``observed_at`` and
        its identities is not interpretable: a list of processes means nothing
        without the boot it was read in.
        """
        if kind not in RESOURCE_KINDS:  # pragma: no cover - callers validate first
            raise KeyError(kind)
        snapshot = self.snapshot(refresh=refresh)
        return snapshot, snapshot.collection(kind)

    # -- collection ----------------------------------------------------------

    def _usable_cached(self) -> Optional[RuntimeSnapshot]:
        """A cached snapshot, if it is both fresh enough and still about *now*."""
        if self._cached is None:
            return None
        if self._clock() - self._cached_at >= self._cache_seconds:
            return None

        # Age is not the only way a snapshot goes stale. A session that has been
        # replaced makes every session-scoped resource in it wrong, however
        # recent it is.
        session = session_identity_from(self._session_detector())
        cached_session = self._cached.session
        if (
            cached_session.get("available") != session.available
            or cached_session.get("session_id") != session.session_id
        ):
            return None
        return self._cached

    def _collect(self) -> RuntimeSnapshot:
        host = detect_host_identity()
        boot = detect_boot_identity()
        raw_session = self._session_detector()
        session = session_identity_from(raw_session)

        if getattr(self._adapter, "stub", False):
            # The stub adapter means no real host is being controlled. Reporting
            # this machine's actual displays and processes underneath it would
            # be the worst of both worlds: real data presented as belonging to a
            # host the rest of the UI is telling the user is simulated.
            return self._stubbed(host, boot, session)

        warnings = []
        if not boot.available:
            warnings.append(
                "this host publishes no boot identity, so process and application identities "
                "cannot be formed"
            )

        # One walk of /proc feeds both the process and the application
        # collections, so the two always describe the same instant.
        facts, scan_warnings = self._processes.read_all()

        applications, instance_by_pid = ApplicationInstanceDiscovery(
            self._application_definitions()
        ).collect(host.host_id, boot, facts, scan_warnings)

        processes = self._processes.collect(
            host.host_id, boot, facts, list(scan_warnings), instance_by_pid
        )

        displays = self._displays.collect(host.host_id, raw_session)
        displays, overlay_warnings = self._with_overlays(displays)
        warnings.extend(overlay_warnings)

        windows = self._windows.collect(raw_session)

        return RuntimeSnapshot(
            observed_at=now_iso(),
            host=host.to_dict(),
            boot=boot.to_dict(),
            session=session.to_dict(),
            collections={
                KIND_DISPLAYS: displays,
                KIND_APPLICATIONS: applications,
                KIND_PROCESSES: processes,
                KIND_WINDOWS: windows,
            },
            warnings=tuple(warnings),
        )

    def _stubbed(self, host, boot, session) -> RuntimeSnapshot:
        """Every collection unavailable, saying plainly that nothing was observed.

        Not empty ``ok`` collections. A stub host has not been looked at, which
        is a different statement from "looked at, found nothing", and the whole
        status vocabulary exists to keep those apart.
        """
        reason = (
            "the stub adapter is active: no real host is being observed, so nothing can be "
            "reported about this machine"
        )
        return RuntimeSnapshot(
            observed_at=now_iso(),
            host=host.to_dict(),
            boot=boot.to_dict(),
            session=session.to_dict(),
            collections={kind: unavailable(kind, reason) for kind in RESOURCE_KINDS},
            warnings=(reason,),
        )

    def _with_overlays(self, displays):
        """Layer user labels onto discovered displays, on a copy of the items.

        Copying is not ceremony: discovery's output is the system's answer, and
        the overlay pass must not be able to reach back and edit it. The items
        that go out are new dictionaries; the discovered identity fields in them
        are carried through untouched.
        """
        if not displays.items:
            return displays, []
        items: List[Dict[str, Any]] = [dict(item) for item in displays.items]
        overlay_warnings = self._overlays.resolve_displays(items, self._display_overlays())
        return dataclasses.replace(displays, items=tuple(items)), overlay_warnings

    # -- injected knowledge --------------------------------------------------

    def _application_definitions(self) -> Dict[str, tuple]:
        """The adapter's launch table, or nothing.

        An adapter that cannot say leaves every instance unmapped, which is the
        honest degradation: instances are still discovered, they are simply not
        claimed to be a known application.
        """
        if self._adapter is None:
            return {}
        try:
            table = self._adapter.application_executables()
        except Exception:  # an adapter fault must not lose the whole snapshot
            return {}
        return dict(table or {})

    def _display_overlays(self):
        """Enabled entries of the displays overlay registry, or nothing.

        A registry that is missing or invalid yields no overlays and no error:
        an unlabelled display is complete, and a broken label file must never
        take the inventory down with it.
        """
        if self._registry_loader is None:
            return ()
        try:
            registries = self._registry_loader()
            load = registries.load("displays")
            if not load.ok or load.registry is None:
                return ()
            return [item for item in load.registry.items if item.enabled]
        except Exception:
            return ()


__all__ = ["DEFAULT_CACHE_SECONDS", "RuntimeInventoryService"]
