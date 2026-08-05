"""Snapshot assembly: session scope, cache invalidation, and the overlay boundary.

The properties here are about *time* and *layering* rather than about any one
backend:

* a snapshot describes one instant, and a cached one that no longer describes
  the current session is thrown away rather than served;
* GUI-scoped resources do not exist before a graphical login, and saying "none"
  would be a claim nobody can make about a machine nobody has logged into;
* a user's label is layered onto a discovered resource and can never replace,
  rename, or invent one.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cofferdam.workstation.runtime.models import (
    KIND_APPLICATIONS,
    KIND_DISPLAYS,
    KIND_PROCESSES,
    KIND_WINDOWS,
    RESOURCE_KINDS,
    STATUS_OK,
    STATUS_UNAVAILABLE,
)
from cofferdam.workstation.runtime.overlays import (
    SKIP_AMBIGUOUS,
    OverlayResolver,
)
from cofferdam.workstation.runtime.service import RuntimeInventoryService

from ._runtime_doubles import FakeOverlay, FakeProc, FakeSession


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.proc = FakeProc(Path(self._tmp.name) / "proc")
        self.clock = FakeClock()
        self.session = FakeSession()
        self.scans = 0

    def build(self, **kwargs):
        import os

        from cofferdam.workstation.runtime.displays import DisplayDiscovery
        from cofferdam.workstation.runtime.processes import ProcessDiscovery

        outer = self

        class CountingProcessDiscovery(ProcessDiscovery):
            def read_all(self):
                outer.scans += 1
                return super().read_all()

        drm = Path(self._tmp.name) / "drm"
        drm.mkdir(exist_ok=True)

        defaults = dict(
            session_detector=lambda: self.session,
            display_discovery=DisplayDiscovery(drm_root=str(drm)),
            process_discovery=CountingProcessDiscovery(
                proc_root=str(self.proc.root), uid=os.getuid()
            ),
            clock=self.clock,
        )
        defaults.update(kwargs)
        return RuntimeInventoryService(**defaults)


class PreLoginTests(ServiceTestCase):
    """(13) Pre-login GUI/session-scoped inventory is unavailable."""

    def test_before_login_displays_and_windows_are_unavailable(self) -> None:
        self.session = FakeSession(
            available=False,
            session_id=None,
            reason="no graphical session is active on this host yet",
        )
        snapshot = self.build().snapshot()

        for kind in (KIND_DISPLAYS, KIND_WINDOWS):
            with self.subTest(kind=kind):
                collection = snapshot.collection(kind)
                self.assertEqual(collection.status, STATUS_UNAVAILABLE)
                self.assertEqual(collection.items, ())
                self.assertTrue(collection.reason)

    def test_before_login_the_headless_daemon_still_reports_processes(self) -> None:
        """The service runs before anyone logs in, and must stay useful.

        Processes are not session-scoped: a lingering daemon has them, and
        reporting them is correct. Only the GUI-scoped collections go dark.
        """
        self.proc.add(4242, comm="worker", start_ticks=100)
        self.session = FakeSession(available=False, session_id=None, reason="no session yet")
        snapshot = self.build().snapshot()

        processes = snapshot.collection(KIND_PROCESSES)
        self.assertEqual(processes.status, STATUS_OK)
        self.assertEqual([item["pid"] for item in processes.items], [4242])

    def test_the_snapshot_records_that_there_is_no_session(self) -> None:
        self.session = FakeSession(available=False, session_id=None, reason="no session yet")
        snapshot = self.build().snapshot()

        self.assertIs(snapshot.session["available"], False)
        self.assertIsNone(snapshot.session["session_id"])


class SessionInvalidationTests(ServiceTestCase):
    """(14) Logout/session replacement invalidates session-scoped inventory."""

    def test_a_cached_snapshot_is_reused_within_the_cache_window(self) -> None:
        service = self.build()
        service.snapshot()
        service.snapshot()
        self.assertEqual(self.scans, 1, "the cache exists to avoid a second process scan")

    def test_the_cache_expires_with_time(self) -> None:
        service = self.build(cache_seconds=5.0)
        service.snapshot()
        self.clock.now += 6.0
        service.snapshot()
        self.assertEqual(self.scans, 2)

    def test_a_replaced_session_invalidates_the_cache_immediately(self) -> None:
        """A fresh login is a different session, however recent the cache is.

        Serving the old snapshot would present the previous session's displays
        as currently connected — the exact staleness this rule exists for.
        """
        service = self.build(cache_seconds=3600.0)
        first = service.snapshot()

        self.session = FakeSession(session_id="stamp-2")
        second = service.snapshot()

        self.assertEqual(self.scans, 2, "the cache survived a session change")
        self.assertNotEqual(first.session["session_id"], second.session["session_id"])

    def test_a_logout_invalidates_the_cache_immediately(self) -> None:
        service = self.build(cache_seconds=3600.0)
        service.snapshot()

        self.session = FakeSession(available=False, session_id=None, reason="logged out")
        snapshot = service.snapshot()

        self.assertEqual(self.scans, 2)
        self.assertEqual(snapshot.collection(KIND_DISPLAYS).status, STATUS_UNAVAILABLE)

    def test_a_login_after_a_logout_produces_a_fresh_session_identity(self) -> None:
        service = self.build(cache_seconds=3600.0)
        before = service.snapshot().session["session_id"]

        self.session = FakeSession(available=False, session_id=None, reason="logged out")
        service.snapshot()

        self.session = FakeSession(session_id="stamp-after-login")
        after = service.snapshot().session["session_id"]

        self.assertIsNotNone(after)
        self.assertNotEqual(before, after)

    def test_refresh_bypasses_the_cache(self) -> None:
        service = self.build(cache_seconds=3600.0)
        service.snapshot()
        service.snapshot(refresh=True)
        self.assertEqual(self.scans, 2)

    def test_one_scan_feeds_both_the_process_and_application_collections(self) -> None:
        """They must describe the same instant, not two adjacent ones."""
        self.proc.add(4242, comm="worker", start_ticks=100)
        service = self.build()
        service.snapshot()
        self.assertEqual(self.scans, 1)


class StubAdapterTests(ServiceTestCase):
    """A simulated host is not observed, and must not borrow this one's data."""

    def test_the_stub_adapter_reports_every_collection_unavailable(self) -> None:
        class StubAdapter:
            stub = True

            def application_executables(self):  # pragma: no cover - never reached
                return {}

        self.proc.add(4242, comm="worker", start_ticks=100)
        snapshot = self.build(adapter=StubAdapter()).snapshot()

        for kind in RESOURCE_KINDS:
            with self.subTest(kind=kind):
                collection = snapshot.collection(kind)
                self.assertEqual(collection.status, STATUS_UNAVAILABLE)
                self.assertEqual(collection.items, ())
                self.assertIn("stub adapter", collection.reason)


class OverlayBoundaryTests(unittest.TestCase):
    """(16) Existing overlay data cannot replace core identity."""

    def display(self, **overrides):
        item = {
            "resource_id": "display-abc123",
            "identity": {"source": "edid", "stability": "hardware", "edid_sha256": "a" * 64},
            "connector": "HDMI-1",
            "manufacturer": "VSC",
            "model": "VA1650-FHD",
            "serial": "Y39252000375",
            "overlay": None,
        }
        item.update(overrides)
        return item

    def test_an_overlay_adds_a_label_without_touching_the_identity(self) -> None:
        items = [self.display()]
        before = dict(items[0])
        overlay = FakeOverlay("example-display", "Example label", edid_sha256="A" * 64)

        OverlayResolver().resolve_displays(items, [overlay])

        self.assertEqual(items[0]["overlay"]["label"], "Example label")
        for field in ("resource_id", "connector", "manufacturer", "model", "serial"):
            with self.subTest(field=field):
                self.assertEqual(items[0][field], before[field])
        self.assertEqual(items[0]["identity"], before["identity"])

    def test_an_overlay_cannot_supply_an_identity_the_hardware_did_not(self) -> None:
        """Mutation check: the payload has no field that could overwrite one."""
        items = [self.display()]
        overlay = FakeOverlay("example-display", "Example label", edid_sha256="A" * 64)
        OverlayResolver().resolve_displays(items, [overlay])

        payload = items[0]["overlay"]
        for banned in ("resource_id", "connector", "manufacturer", "model", "serial", "identity"):
            with self.subTest(field=banned):
                self.assertNotIn(banned, payload)

    def test_a_connector_hint_alone_never_matches(self) -> None:
        """The rule the word "hint" is supposed to mean.

        Unplug one monitor, plug in another: the socket name is unchanged and
        the panel is not. Matching on it would move a user's label onto a
        different monitor without anybody noticing.
        """
        items = [self.display()]
        overlay = FakeOverlay("example-display", "Example label", connector_hint="HDMI-1")

        OverlayResolver().resolve_displays(items, [overlay])
        self.assertIsNone(items[0]["overlay"])

    def test_a_full_hardware_triple_matches(self) -> None:
        items = [self.display()]
        overlay = FakeOverlay(
            "example-display",
            "Example label",
            manufacturer="VSC",
            model="VA1650-FHD",
            serial="Y39252000375",
        )
        OverlayResolver().resolve_displays(items, [overlay])
        self.assertEqual(items[0]["overlay"]["matched_by"], "manufacturer-model-serial")

    def test_a_partial_triple_does_not_match(self) -> None:
        """Two panels of one model differ only by serial. Two thirds is not a match."""
        items = [self.display()]
        overlay = FakeOverlay(
            "example-display", "Example label", manufacturer="VSC", model="VA1650-FHD"
        )
        OverlayResolver().resolve_displays(items, [overlay])
        self.assertIsNone(items[0]["overlay"])

    def test_two_overlays_matching_one_display_apply_neither(self) -> None:
        items = [self.display()]
        overlays = [
            FakeOverlay("example-one", "First", edid_sha256="a" * 64),
            FakeOverlay(
                "example-two", "Second", manufacturer="VSC", model="VA1650-FHD", serial="Y39252000375"
            ),
        ]
        warnings = OverlayResolver().resolve_displays(items, overlays)

        self.assertIsNone(items[0]["overlay"])
        self.assertEqual(items[0]["overlay_skipped"], SKIP_AMBIGUOUS)
        self.assertTrue(warnings)

    def test_a_display_with_no_overlay_stays_fully_identified(self) -> None:
        """Removing the label must leave the resource complete."""
        items = [self.display()]
        OverlayResolver().resolve_displays(items, [])

        self.assertIsNone(items[0]["overlay"])
        self.assertEqual(items[0]["resource_id"], "display-abc123")
        self.assertEqual(items[0]["model"], "VA1650-FHD")

    def test_a_disabled_overlay_is_not_offered_by_the_service(self) -> None:
        """The service filters to enabled entries before the resolver sees them."""

        class Registry:
            items = (FakeOverlay("example-off", "Off", enabled=False, edid_sha256="a" * 64),)

        class Load:
            ok = True
            registry = Registry()

        class Registries:
            def load(self, name):
                return Load()

        service = RuntimeInventoryService(registry_loader=lambda: Registries())
        self.assertEqual(list(service._display_overlays()), [])


class RegistryExamplesAreNeverLiveTests(unittest.TestCase):
    """(15) Registry examples are never used as live resources."""

    def test_a_broken_registry_leaves_discovery_working_and_unlabelled(self) -> None:
        def exploding_loader():
            raise RuntimeError("registry is corrupt")

        service = RuntimeInventoryService(registry_loader=exploding_loader)
        self.assertEqual(list(service._display_overlays()), [])

    def test_no_overlay_source_means_no_overlays_and_no_items(self) -> None:
        """A registry can never *add* a resource, only annotate one."""
        service = RuntimeInventoryService(registry_loader=None)
        self.assertEqual(list(service._display_overlays()), [])

    def test_an_overlay_for_a_display_that_is_not_connected_produces_nothing(self) -> None:
        """The decisive case: a configured display that discovery did not find."""
        items = []
        warnings = OverlayResolver().resolve_displays(
            items, [FakeOverlay("example-display", "Example label", edid_sha256="b" * 64)]
        )
        self.assertEqual(items, [], "an overlay must never create a runtime resource")
        self.assertEqual(warnings, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
