"""Audio discovery and identity (M2C checks 1-9, 27-29).

These tests drive the real backend, discovery and service code against a
pw-dump-shaped graph. Nothing here mocks the layer under test: the parser reads
the structure ``pw-dump`` really emits, and the volume reader parses the text
``wpctl`` really prints.
"""

from __future__ import annotations

import json
import unittest

from cofferdam.workstation.audio.discovery import (
    ASSOCIATION_IDENTIFIED,
    ASSOCIATION_UNCLASSIFIED,
    output_resource_id,
)
from cofferdam.workstation.audio.models import (
    DEVICE_BLUETOOTH,
    DEVICE_BUILTIN_SPEAKER,
    DEVICE_HDMI,
    KIND_OUTPUTS,
    KIND_STREAMS,
)
from cofferdam.workstation.audio.service import AudioInventoryService
from cofferdam.workstation.audio.wireplumber import WirePlumberBackend

from ._audio_doubles import (
    BLUETOOTH_NAME,
    FakeAudioHost,
    FakeProcess,
    HDMI_NAME,
    SPEAKER_NAME,
    client,
    core,
    default_metadata,
    device,
    link,
    process_reader,
    simple_graph,
    sink,
    stream,
)


def build_service(host: FakeAudioHost, **kwargs) -> AudioInventoryService:
    backend = WirePlumberBackend(runner=host.run, which=host.which)
    return AudioInventoryService(backend=backend, cache_seconds=0.0, **kwargs)


class OutputDiscoveryTests(unittest.TestCase):
    """(1)(2)(3)(7) What the host has, reported as the host reports it."""

    def test_the_current_default_output_is_identified_from_evidence(self) -> None:
        """(1) The default comes from the graph's metadata, not from a guess."""
        host = FakeAudioHost()
        snapshot = build_service(host).snapshot(refresh=True)

        outputs = snapshot.collection(KIND_OUTPUTS)
        self.assertEqual(len(outputs.items), 1)
        default = snapshot.output_by_resource_id(snapshot.default_output_resource_id)
        self.assertIsNotNone(default)
        self.assertEqual(default["node_name"], SPEAKER_NAME)
        self.assertTrue(default["is_default"])

    def test_a_graph_with_no_default_reports_none_rather_than_choosing(self) -> None:
        """A host with outputs and no default must not have one invented for it."""
        host = FakeAudioHost(objects=simple_graph(default_sink=None), default_sink=None)
        snapshot = build_service(host).snapshot(refresh=True)

        self.assertIsNone(snapshot.default_output_resource_id)
        self.assertTrue(snapshot.outputs())
        self.assertFalse(any(item["is_default"] for item in snapshot.outputs()))
        self.assertTrue(any("no default" in w for w in snapshot.warnings))

    def test_two_connected_outputs_stay_distinct(self) -> None:
        """(2) Two outputs get two identities, two names, and two categories."""
        host = FakeAudioHost(objects=simple_graph(with_hdmi=True), volumes={58: 50, 70: 30})
        snapshot = build_service(host).snapshot(refresh=True)

        items = snapshot.outputs()
        self.assertEqual(len(items), 2)
        self.assertEqual(len({item["resource_id"] for item in items}), 2)
        self.assertEqual(len({item["stable_id"] for item in items}), 2)
        self.assertEqual(len({item["node_name"] for item in items}), 2)

        by_name = {item["node_name"]: item for item in items}
        self.assertEqual(by_name[SPEAKER_NAME]["device_type"], DEVICE_BUILTIN_SPEAKER)
        self.assertEqual(by_name[HDMI_NAME]["device_type"], DEVICE_HDMI)
        # Exactly one is the default; the other must not inherit the flag.
        self.assertEqual(sum(1 for item in items if item["is_default"]), 1)

    def test_missing_hardware_metadata_is_not_invented(self) -> None:
        """(3) A sink whose card says nothing yields nulls, never placeholders."""
        bare = sink(80, "alsa_output.bare", "", device_id=None)
        # No description, no nick, no device: everything the UI would like is absent.
        del bare["info"]["props"]["node.description"]
        objects = [core(), bare, default_metadata(None)]
        host = FakeAudioHost(objects=objects, volumes={80: 10}, mutes={80: False}, default_sink=None)

        item = build_service(host).snapshot(refresh=True).outputs()[0]
        self.assertIsNone(item["display_name"])
        self.assertIsNone(item["description"])
        self.assertIsNone(item["route"])
        self.assertIsNone(item["profile"])
        # `device_type` is exempt and is the one field that must *not* be null:
        # it is a closed vocabulary whose `unknown` member is a real answer —
        # "nothing here identifies this device" — carried with the evidence that
        # led to it. Every other descriptive field is absent rather than filled.
        self.assertEqual(item["device_type"], "unknown")
        self.assertTrue(item["device_type_evidence"])
        descriptive = {k: v for k, v in item.items()
                       if k not in ("device_type", "device_type_evidence")}
        for value in descriptive.values():
            self.assertNotIn(value, ("unknown", "Unknown", "n/a", "N/A", "—"))
        # Identity is still formed: a nameless card does not cost the device its
        # resource_id, it only costs it a *hardware-grade* stability claim.
        self.assertTrue(item["resource_id"])
        self.assertEqual(item["identity_stability"], "weak")

    def test_a_bluetooth_device_is_classified_from_its_api_not_its_name(self) -> None:
        """Classification reads structured properties, never a display name."""
        bt_device = device(
            90, "bluez_card.00_11_22", "Some Speaker", api="bluez5", bus="bluetooth"
        )
        node = sink(91, BLUETOOTH_NAME, "Some Speaker", device_id=90)
        host = FakeAudioHost(
            objects=[core(), bt_device, node, default_metadata(BLUETOOTH_NAME)],
            volumes={91: 40},
            mutes={91: False},
            default_sink=BLUETOOTH_NAME,
        )
        item = build_service(host).snapshot(refresh=True).outputs()[0]
        self.assertEqual(item["device_type"], DEVICE_BLUETOOTH)

    def test_a_disappearing_output_does_not_crash_the_snapshot(self) -> None:
        """(7) A sink that vanishes between the dump and the volume read."""
        host = FakeAudioHost(objects=simple_graph(with_hdmi=True), volumes={58: 50})
        # Node 70 is in the graph but has no volume entry, so `wpctl get-volume`
        # fails for it exactly as it would for a device just unplugged.
        snapshot = build_service(host).snapshot(refresh=True)

        items = {item["node_name"]: item for item in snapshot.outputs()}
        self.assertEqual(len(items), 2)
        self.assertIsNone(items[HDMI_NAME]["volume_percent"])
        self.assertEqual(snapshot.collection(KIND_OUTPUTS).status, "partial")
        self.assertTrue(any("volume could not be read" in w for w in snapshot.warnings))

    def test_a_card_with_no_live_sink_is_explained_rather_than_omitted_silently(self) -> None:
        """An HDMI card with its profile off produces a warning a person can act on."""
        objects = simple_graph()
        objects.insert(1, device(51, "alsa_card.pci-0000_01_00.1", "AD107 HDMI Controller",
                                 profile={"index": 0, "name": "off", "description": "Off"}))
        host = FakeAudioHost(objects=objects)
        snapshot = build_service(host).snapshot(refresh=True)

        self.assertEqual(len(snapshot.outputs()), 1)
        self.assertTrue(
            any("AD107" in w and "profile is off" in w for w in snapshot.warnings),
            snapshot.warnings,
        )


class IdentityStabilityTests(unittest.TestCase):
    """(4)(6) A node id is not an identity, and the code must depend on that."""

    def test_a_runtime_node_id_is_not_the_identity(self) -> None:
        """(4) The same device at a different node id keeps its resource_id."""
        first = build_service(FakeAudioHost()).snapshot(refresh=True)

        moved = simple_graph()
        for entry in moved:
            if entry.get("id") == 58:
                entry["id"] = 137  # the daemon handed this device a different id
        host = FakeAudioHost(objects=moved, volumes={137: 50}, mutes={137: False})
        second = build_service(host).snapshot(refresh=True)

        self.assertEqual(
            first.outputs()[0]["resource_id"], second.outputs()[0]["resource_id"]
        )
        self.assertNotEqual(first.outputs()[0]["node_id"], second.outputs()[0]["node_id"])
        # And the transient value is published as transient.
        self.assertTrue(second.outputs()[0]["node_id_is_transient"])

    def test_a_reused_node_id_carrying_different_hardware_gets_a_different_identity(self) -> None:
        """(6) Node 58 becoming a different device must not resolve to the old id."""
        original = build_service(FakeAudioHost()).snapshot(refresh=True)

        # Same numeric id, entirely different device.
        reused = [
            core(),
            device(90, "bluez_card.00_11_22", "Some Speaker", api="bluez5", bus="bluetooth"),
            sink(58, BLUETOOTH_NAME, "Some Speaker", device_id=90),
            default_metadata(BLUETOOTH_NAME),
        ]
        host = FakeAudioHost(objects=reused, volumes={58: 20}, mutes={58: False},
                             default_sink=BLUETOOTH_NAME)
        after = build_service(host).snapshot(refresh=True)

        self.assertEqual(original.outputs()[0]["node_id"], after.outputs()[0]["node_id"])
        self.assertNotEqual(
            original.outputs()[0]["resource_id"], after.outputs()[0]["resource_id"]
        )
        # The old id does not resolve against the new graph.
        self.assertIsNone(after.output_by_resource_id(original.outputs()[0]["resource_id"]))

    def test_a_restarted_audio_server_invalidates_every_resource_id(self) -> None:
        """A new cookie is a new graph, so nothing a client held still resolves."""
        before = build_service(FakeAudioHost()).snapshot(refresh=True)
        restarted = FakeAudioHost(objects=simple_graph(cookie=999888777))
        after = build_service(restarted).snapshot(refresh=True)

        self.assertNotEqual(before.graph["graph_id"], after.graph["graph_id"])
        self.assertIsNone(after.output_by_resource_id(before.outputs()[0]["resource_id"]))
        # The durable identity, which a preference would key off, is unchanged.
        self.assertEqual(before.outputs()[0]["stable_id"], after.outputs()[0]["stable_id"])

    def test_a_graph_without_a_cookie_publishes_no_resources_at_all(self) -> None:
        """No graph identity means no safe way to address anything in it."""
        objects = simple_graph()
        objects[0] = {"id": 0, "type": "PipeWire:Interface:Core", "info": {"version": "1.6.2"}}
        snapshot = build_service(FakeAudioHost(objects=objects)).snapshot(refresh=True)

        self.assertFalse(snapshot.graph["available"])
        self.assertEqual(snapshot.collection(KIND_OUTPUTS).status, "unavailable")
        self.assertEqual(snapshot.collection(KIND_OUTPUTS).items, ())


class VolumeReadingTests(unittest.TestCase):
    """The scale question, settled by parsing what wpctl actually prints."""

    def test_volume_is_read_on_the_scale_wpctl_uses(self) -> None:
        host = FakeAudioHost(volumes={58: 95})
        item = build_service(host).snapshot(refresh=True).outputs()[0]
        self.assertEqual(item["volume_percent"], 95)

    def test_a_muted_marker_in_the_wpctl_output_is_understood(self) -> None:
        parse = WirePlumberBackend._parse_volume
        self.assertEqual(parse("Volume: 0.25 [MUTED]\n"), (25, True))
        self.assertEqual(parse("Volume: 0.25\n"), (25, False))

    def test_an_unreadable_volume_line_yields_no_number(self) -> None:
        """A number that cannot be parsed is absent, never defaulted."""
        parse = WirePlumberBackend._parse_volume
        self.assertEqual(parse("")[0], None)
        self.assertEqual(parse("something else entirely")[0], None)
        self.assertEqual(parse("Volume: not-a-number")[0], None)

    def test_a_host_left_above_unity_is_reported_at_the_ceiling(self) -> None:
        """(13) Nothing in the published range ever exceeds 100."""
        self.assertEqual(WirePlumberBackend._parse_volume("Volume: 1.50")[0], 100)

    def test_mute_state_comes_from_the_graph(self) -> None:
        host = FakeAudioHost(mutes={58: True})
        item = build_service(host).snapshot(refresh=True).outputs()[0]
        self.assertTrue(item["muted"])


class StreamAssociationTests(unittest.TestCase):
    """(28)(29) Positive evidence only, and uncertainty stays uncertain."""

    def _service(self, host: FakeAudioHost, processes, table=None):
        from cofferdam.workstation.audio.discovery import AudioStreamDiscovery

        discovery = AudioStreamDiscovery(
            known_executables=table if table is not None else {"spotify": ("spotify",)},
            process_reader=process_reader(processes),
        )
        backend = WirePlumberBackend(runner=host.run, which=host.which)
        return AudioInventoryService(backend=backend, cache_seconds=0.0, stream_discovery=discovery)

    def test_a_stream_is_associated_through_verified_process_evidence(self) -> None:
        """(28) pid -> /proc -> known executable is what makes an association."""
        host = FakeAudioHost(
            objects=simple_graph(
                streams=[stream(104, 103, "Spotify")],
                clients=[client(103, "Spotify", pid=4242, binary="spotify")],
                links=[link(104, 58)],
            )
        )
        service = self._service(host, {4242: FakeProcess(4242, "/usr/bin/spotify")})
        item = service.snapshot(refresh=True).streams()[0]

        self.assertEqual(item["association"]["status"], ASSOCIATION_IDENTIFIED)
        self.assertEqual(item["association"]["application"], "spotify")
        self.assertIsNotNone(item["association"]["process_resource_id"])

    def test_a_stream_claiming_a_name_without_a_matching_process_stays_unclassified(self) -> None:
        """(29) Calling yourself Spotify is not evidence of being Spotify."""
        host = FakeAudioHost(
            objects=simple_graph(
                streams=[stream(104, 103, "Spotify")],
                clients=[client(103, "Spotify", pid=4242, binary="spotify")],
                links=[link(104, 58)],
            )
        )
        # The verified pid resolves to something else entirely.
        service = self._service(host, {4242: FakeProcess(4242, "/usr/bin/some-other-program")})
        item = service.snapshot(refresh=True).streams()[0]

        self.assertEqual(item["association"]["status"], ASSOCIATION_UNCLASSIFIED)
        self.assertIsNone(item["association"]["application"])
        self.assertTrue(item["association"]["reason"])
        # The declared name is still shown, and still labelled as declared.
        self.assertEqual(item["declared_application_name"], "Spotify")

    def test_a_stream_with_no_verified_pid_stays_unclassified(self) -> None:
        """(29) No peer credential means no association, whatever it declares."""
        host = FakeAudioHost(
            objects=simple_graph(
                streams=[stream(104, 103, "Spotify")],
                clients=[client(103, "Spotify", pid=None, binary="spotify")],
                links=[link(104, 58)],
            )
        )
        service = self._service(host, {})
        item = service.snapshot(refresh=True).streams()[0]
        self.assertEqual(item["association"]["status"], ASSOCIATION_UNCLASSIFIED)

    def test_a_substring_match_does_not_associate(self) -> None:
        """An executable named `spotifyd` is not the `spotify` in the table."""
        host = FakeAudioHost(
            objects=simple_graph(
                streams=[stream(104, 103, "Spotify")],
                clients=[client(103, "Spotify", pid=4242)],
                links=[link(104, 58)],
            )
        )
        service = self._service(host, {4242: FakeProcess(4242, "/usr/bin/spotifyd")})
        item = service.snapshot(refresh=True).streams()[0]
        self.assertEqual(item["association"]["status"], ASSOCIATION_UNCLASSIFIED)

    def test_a_stream_reports_the_output_it_is_actually_linked_to(self) -> None:
        host = FakeAudioHost(
            objects=simple_graph(
                with_hdmi=True,
                streams=[stream(104, 103, "Spotify")],
                clients=[client(103, "Spotify", pid=4242)],
                links=[link(104, 70)],
            ),
            volumes={58: 50, 70: 30},
        )
        service = self._service(host, {4242: FakeProcess(4242, "/usr/bin/spotify")})
        snapshot = service.snapshot(refresh=True)
        item = snapshot.streams()[0]

        hdmi = [o for o in snapshot.outputs() if o["node_name"] == HDMI_NAME][0]
        self.assertEqual(item["current_output_resource_id"], hdmi["resource_id"])
        self.assertTrue(item["current_output_is_known"])


class StreamPrivacyTests(unittest.TestCase):
    """(27) What is playing is never read into a payload."""

    def _snapshot(self):
        from cofferdam.workstation.audio.discovery import AudioStreamDiscovery

        host = FakeAudioHost(
            objects=simple_graph(
                streams=[stream(104, 103, "Spotify", media_name="Secret Song — Secret Artist")],
                clients=[client(103, "Spotify", pid=4242,
                                media_name="Secret Song — Secret Artist")],
                links=[link(104, 58)],
            )
        )
        discovery = AudioStreamDiscovery(
            known_executables={"spotify": ("spotify",)},
            process_reader=process_reader({4242: FakeProcess(4242, "/usr/bin/spotify")}),
        )
        backend = WirePlumberBackend(runner=host.run, which=host.which)
        service = AudioInventoryService(
            backend=backend, cache_seconds=0.0, stream_discovery=discovery
        )
        return service.snapshot(refresh=True)

    def test_no_media_title_appears_anywhere_in_the_snapshot(self) -> None:
        payload = json.dumps(self._snapshot().to_dict())
        self.assertNotIn("Secret Song", payload)
        self.assertNotIn("Secret Artist", payload)
        self.assertNotIn("media.name", payload)

    def test_no_raw_property_dictionary_is_published(self) -> None:
        """The allowlist rule, asserted on the published shape."""
        snapshot = self._snapshot()
        item = snapshot.streams()[0]
        allowed = {
            "resource_id", "node_id", "node_id_is_transient", "object_serial",
            "declared_application_name", "media_role", "state",
            "current_output_resource_id", "current_output_is_known",
            "volume_percent", "muted", "association",
        }
        self.assertEqual(set(item), allowed)

        output = snapshot.outputs()[0]
        self.assertNotIn("props", output)
        for key in output:
            # No published key is a raw PipeWire property name.
            self.assertNotRegex(key, r"^(alsa|api|device|node|object|card|factory)\.")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
