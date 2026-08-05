"""Display discovery: two panels stay two panels, and nothing is invented.

These pin the properties that make a display card trustworthy. Each one
corresponds to a way the implementation could look right while being wrong:

* collapsing two monitors into one because their connector names or their
  vendor strings collided;
* deciding "internal" from a name that looked internal rather than from what
  the system said;
* filling an absent model or serial with a placeholder that later reads as a
  measurement;
* handing out an identity that changes between two reads of the same machine.

Several tests are paired with a **mutation check**: the same assertion run
against a deliberately broken input, proving the test can actually fail. A guard
that passes no matter what is worse than no guard, because it is trusted.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cofferdam.workstation.runtime import displays as displays_module
from cofferdam.workstation.runtime.displays import DisplayDiscovery, normalize_connector
from cofferdam.workstation.runtime.models import (
    STABILITY_HARDWARE,
    STABILITY_WEAK,
    STATUS_OK,
    STATUS_UNAVAILABLE,
)

from ._runtime_doubles import (
    HOST_ID,
    FakeSession,
    build_edid,
    current_state,
    logical_monitor,
    monitor,
    write_drm_tree,
)

INTERNAL_EDID = build_edid(
    manufacturer="AUO", product_code=0x53AB, serial_number=0, width_mm=382, height_mm=215
)
EXTERNAL_EDID = build_edid(
    manufacturer="VSC",
    product_code=0x6943,
    model_name="VA1650-FHD",
    serial_text="Y39252000375",
    width_mm=344,
    height_mm=194,
)


class DisplayDiscoveryTestCase(unittest.TestCase):
    """Base: a fake DRM tree plus a scripted compositor reply."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._reply = None
        original = displays_module.call_method
        displays_module.call_method = self._call
        self.addCleanup(lambda: setattr(displays_module, "call_method", original))

    def _call(self, *args, **kwargs):
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply

    def collect(self, reply, connectors, session=None):
        self._reply = reply
        drm = write_drm_tree(self.root / "drm", connectors)
        discovery = DisplayDiscovery(drm_root=str(drm))
        return discovery.collect(HOST_ID, session or FakeSession())

    def two_display_host(self):
        internal = monitor(
            "eDP-1", "AUO", "0x53ab", "0x00000000", refresh=144.003,
            is_builtin=True, display_name="Built-in display",
        )
        external = monitor(
            "HDMI-1", "VSC", "VA1650-FHD", "Y39252000375",
            is_builtin=False, display_name='ViewSonic Corporation 15"',
        )
        reply = current_state(
            [internal, external],
            [
                logical_monitor([internal], x=0, y=0, primary=True),
                logical_monitor([external], x=1920, y=0, primary=False),
            ],
        )
        connectors = [
            {"name": "eDP-1", "edid": INTERNAL_EDID},
            {"name": "HDMI-A-1", "edid": EXTERNAL_EDID},
            {"name": "DP-1", "status": "disconnected", "enabled": "disabled", "edid": None},
        ]
        return reply, connectors


class TwoDisplaysStayTwoTests(DisplayDiscoveryTestCase):
    """(1) Two discovered displays remain two distinct resources."""

    def test_two_connected_panels_are_two_resources_with_distinct_ids(self) -> None:
        reply, connectors = self.two_display_host()
        collection = self.collect(reply, connectors)

        self.assertEqual(collection.status, STATUS_OK)
        self.assertEqual(len(collection.items), 2)
        identifiers = {item["resource_id"] for item in collection.items}
        self.assertEqual(len(identifiers), 2, "two panels collapsed into one resource")

    def test_a_disconnected_connector_is_not_reported_as_a_display(self) -> None:
        """DP-1 is present in the kernel tree and unplugged. It is not a display."""
        reply, connectors = self.two_display_host()
        collection = self.collect(reply, connectors)
        self.assertNotIn("DP-1", {item["connector"] for item in collection.items})

    def test_identical_panels_stay_distinct_and_are_marked_weak(self) -> None:
        """Mutation of the happy path: two panels with byte-identical EDIDs.

        Some models ship with no serial at all, so this is a real configuration
        and not a contrived one. The hardware digest cannot separate them, and
        the honest outcome is two resources that both say their identity is
        weak — not one resource, and not two that claim hardware identity.
        """
        twin = build_edid(manufacturer="XXX", product_code=0x0001, width_mm=300, height_mm=200)
        first = monitor("DP-1", "XXX", "0x0001", "0x00000000", is_builtin=False)
        second = monitor("DP-2", "XXX", "0x0001", "0x00000000", is_builtin=False)
        reply = current_state(
            [first, second],
            [logical_monitor([first]), logical_monitor([second], x=1920)],
        )
        collection = self.collect(
            reply,
            [{"name": "DP-1", "edid": twin}, {"name": "DP-2", "edid": twin}],
        )

        self.assertEqual(len(collection.items), 2)
        self.assertEqual(len({item["resource_id"] for item in collection.items}), 2)
        for item in collection.items:
            self.assertEqual(item["identity"]["stability"], STABILITY_WEAK)
        self.assertTrue(collection.warnings, "identical hardware identity must be reported")


class ClassificationComesFromEvidenceTests(DisplayDiscoveryTestCase):
    """(2) Internal and external are classified only from evidence."""

    def test_builtin_flag_from_the_compositor_decides(self) -> None:
        reply, connectors = self.two_display_host()
        collection = self.collect(reply, connectors)
        by_connector = {item["connector"]: item for item in collection.items}

        self.assertIs(by_connector["eDP-1"]["internal"], True)
        self.assertIs(by_connector["HDMI-1"]["internal"], False)
        for item in collection.items:
            self.assertEqual(item["internal_source"], "compositor-is-builtin")

    def test_a_compositor_that_says_nothing_falls_back_to_connector_type(self) -> None:
        """The fallback is a kernel convention, and it labels itself as such."""
        panel = monitor("eDP-1", "AUO", "0x53ab", "0x0", is_builtin=None)
        collection = self.collect(
            current_state([panel], [logical_monitor([panel], primary=True)]),
            [{"name": "eDP-1", "edid": INTERNAL_EDID}],
        )
        item = collection.items[0]
        self.assertIs(item["internal"], True)
        self.assertEqual(item["internal_source"], "connector-type")

    def test_classification_is_not_taken_from_the_compositors_display_name(self) -> None:
        """Mutation check: a name that *says* built-in must not classify it.

        ``display-name`` is a human string the compositor composes; treating it
        as evidence would let a monitor called "Built-in display" be classified
        internal on the strength of its own label.
        """
        panel = monitor(
            "HDMI-1", "VSC", "VA1650-FHD", "Y1", is_builtin=False, display_name="Built-in display"
        )
        collection = self.collect(
            current_state([panel], [logical_monitor([panel])]),
            [{"name": "HDMI-A-1", "edid": EXTERNAL_EDID}],
        )
        self.assertIs(collection.items[0]["internal"], False)

    def test_an_unknown_connector_type_is_left_unclassified(self) -> None:
        """No evidence, no answer. ``None`` is the honest classification."""
        panel = monitor("Unknown-9", "ZZZ", "0x0", "0x0", is_builtin=None)
        collection = self.collect(
            current_state([panel], [logical_monitor([panel])]),
            [{"name": "Unknown-9", "edid": None}],
        )
        item = collection.items[0]
        self.assertIsNone(item["internal"])
        self.assertIsNone(item["internal_source"])


class AbsentValuesStayAbsentTests(DisplayDiscoveryTestCase):
    """(3) Missing model/serial/EDID values are not invented."""

    def test_a_panel_with_no_readable_edid_reports_no_fingerprint_or_size(self) -> None:
        panel = monitor("HDMI-1", "VSC", "VA1650-FHD", "Y1", is_builtin=False)
        collection = self.collect(
            current_state([panel], [logical_monitor([panel])]),
            [{"name": "HDMI-A-1", "edid": None}],
        )
        item = collection.items[0]

        self.assertIsNone(item["identity"]["edid_sha256"])
        self.assertIsNone(item["physical_size_mm"])
        self.assertIsNone(item["model_source"])
        # The connector still joins by name, and the evidence says so — that
        # is a weaker join and is labelled rather than hidden.
        self.assertEqual(item["drm_connector"], "HDMI-A-1")
        self.assertEqual(item["match_method"], "connector-name")

    def test_no_placeholder_string_is_substituted_for_a_missing_value(self) -> None:
        """Mutation check: assert on the actual banned spellings.

        "Unknown" and "N/A" read as data. If a future change fills a gap with
        one of them it will pass every "is not None" assertion ever written, so
        the strings themselves are named here.
        """
        panel = monitor("HDMI-1", "", "", "", is_builtin=False)
        collection = self.collect(
            current_state([panel], [logical_monitor([panel])]),
            [{"name": "HDMI-A-1", "edid": None}],
        )
        item = collection.items[0]

        for field in ("manufacturer", "model", "serial", "display_name"):
            with self.subTest(field=field):
                self.assertIsNone(
                    item[field],
                    "an empty value from the compositor must stay absent, not become a string",
                )
        banned = {"unknown", "n/a", "none", "-", "unnamed", "default", "generic"}
        for key, found in item.items():
            if isinstance(found, str):
                with self.subTest(key=key):
                    self.assertNotIn(found.strip().lower(), banned)

    def test_a_numeric_product_code_is_reported_as_what_it_is(self) -> None:
        """A panel with no model descriptor really is described by a number.

        The number is kept — discarding the only model information the hardware
        gave would be its own kind of dishonesty — and ``model_source`` says it
        is a product code so a UI does not present it as a name.
        """
        reply, connectors = self.two_display_host()
        collection = self.collect(reply, connectors)
        by_connector = {item["connector"]: item for item in collection.items}

        self.assertEqual(by_connector["eDP-1"]["model"], "0x53ab")
        self.assertEqual(by_connector["eDP-1"]["model_source"], "edid-product-code")
        self.assertEqual(by_connector["HDMI-1"]["model_source"], "edid-descriptor")


class IdentityStabilityTests(DisplayDiscoveryTestCase):
    """(4) Display identity is stable within a boot, and honest about strength."""

    def test_two_reads_of_the_same_machine_yield_the_same_identities(self) -> None:
        reply, connectors = self.two_display_host()
        first = self.collect(reply, connectors)
        second = self.collect(reply, connectors)

        self.assertEqual(
            [item["resource_id"] for item in first.items],
            [item["resource_id"] for item in second.items],
        )

    def test_an_edid_backed_identity_is_marked_hardware_grade(self) -> None:
        reply, connectors = self.two_display_host()
        collection = self.collect(reply, connectors)
        for item in collection.items:
            with self.subTest(connector=item["connector"]):
                self.assertEqual(item["identity"]["source"], "edid")
                self.assertEqual(item["identity"]["stability"], STABILITY_HARDWARE)

    def test_identity_survives_the_connector_being_renamed(self) -> None:
        """The point of a hardware fingerprint: move the cable, keep the panel.

        The same monitor reported on ``HDMI-2`` instead of ``HDMI-1`` must keep
        its identity, or every user label would detach the first time somebody
        used a different port.
        """
        first = monitor("HDMI-1", "VSC", "VA1650-FHD", "Y39252000375", is_builtin=False)
        moved = monitor("HDMI-2", "VSC", "VA1650-FHD", "Y39252000375", is_builtin=False)

        before = self.collect(
            current_state([first], [logical_monitor([first])]),
            [{"name": "HDMI-A-1", "edid": EXTERNAL_EDID}],
        )
        after = self.collect(
            current_state([moved], [logical_monitor([moved])]),
            [{"name": "HDMI-A-2", "edid": EXTERNAL_EDID}],
        )
        self.assertEqual(before.items[0]["resource_id"], after.items[0]["resource_id"])

    def test_a_connector_only_identity_says_it_is_weak(self) -> None:
        panel = monitor("HDMI-1", "VSC", "VA1650-FHD", "Y1", is_builtin=False)
        collection = self.collect(
            current_state([panel], [logical_monitor([panel])]),
            [{"name": "HDMI-A-1", "edid": None}],
        )
        identity = collection.items[0]["identity"]
        self.assertEqual(identity["source"], "connector")
        self.assertEqual(identity["stability"], STABILITY_WEAK)


class SessionScopeTests(DisplayDiscoveryTestCase):
    """Displays belong to a session, and say so when there is not one."""

    def test_no_graphical_session_yields_unavailable_not_empty(self) -> None:
        collection = self.collect(
            current_state([], []),
            [{"name": "eDP-1", "edid": INTERNAL_EDID}],
            session=FakeSession(available=False, reason="no graphical session is active yet"),
        )
        self.assertEqual(collection.status, STATUS_UNAVAILABLE)
        self.assertEqual(collection.items, ())
        self.assertIn("graphical session", collection.reason)

    def test_an_unreachable_compositor_is_unavailable_with_a_reason(self) -> None:
        from cofferdam.workstation.runtime.dbus import DbusUnavailable

        self._reply = DbusUnavailable("the session bus could not be queried")
        drm = write_drm_tree(self.root / "drm", [{"name": "eDP-1", "edid": INTERNAL_EDID}])
        collection = DisplayDiscovery(drm_root=str(drm)).collect(HOST_ID, FakeSession())

        self.assertEqual(collection.status, STATUS_UNAVAILABLE)
        self.assertEqual(collection.items, ())
        self.assertIn("session bus", collection.reason)


class ConnectorNormalizationTests(unittest.TestCase):
    """The kernel and the compositor spell connectors differently."""

    def test_hdmi_type_suffixes_fold_to_the_userspace_spelling(self) -> None:
        self.assertEqual(normalize_connector("HDMI-A-1"), normalize_connector("HDMI-1"))
        self.assertEqual(normalize_connector("DVI-D-2"), normalize_connector("DVI-2"))

    def test_distinct_connectors_do_not_fold_together(self) -> None:
        """Mutation check: folding must not become "everything matches"."""
        self.assertNotEqual(normalize_connector("HDMI-A-1"), normalize_connector("HDMI-A-2"))
        self.assertNotEqual(normalize_connector("eDP-1"), normalize_connector("DP-1"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
