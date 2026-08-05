"""Typed audio actions (M2C checks 5, 8-18).

The theme of this file is that a command exiting zero proves nothing. Each
action is driven against a fake host that can be told to accept commands and
ignore them, or to clamp them, and the assertion is always about what the code
*reports* rather than about what it attempted.
"""

from __future__ import annotations

import unittest

from cofferdam.workstation.audio.actions import (
    OUTCOME_APPLIED,
    OUTCOME_NOT_APPLIED,
    OUTCOME_PARTIAL,
    REJECT_GRAPH_CHANGED,
    REJECT_INVALID_MUTE,
    REJECT_INVALID_VOLUME,
    REJECT_RESOURCE_CHANGED,
    REJECT_UNKNOWN_RESOURCE,
    REJECT_UNSUPPORTED,
    AudioActionExecutor,
    AudioActionRejected,
    clean_volume_percent,
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


def executor_for(host: FakeAudioHost, **kwargs):
    backend = WirePlumberBackend(runner=host.run, which=host.which)
    service = AudioInventoryService(backend=backend, cache_seconds=0.0, **kwargs)
    return AudioActionExecutor(service), service


def first_output_id(service) -> str:
    return service.snapshot(refresh=True).outputs()[0]["resource_id"]


class VolumeValidationTests(unittest.TestCase):
    """(8)(9)(10)(11)(12)(13) The range, and what falls outside it."""

    def test_zero_is_accepted(self) -> None:
        self.assertEqual(clean_volume_percent(0), 0)

    def test_one_hundred_is_accepted(self) -> None:
        self.assertEqual(clean_volume_percent(100), 100)

    def test_a_negative_volume_is_rejected(self) -> None:
        with self.assertRaises(AudioActionRejected) as caught:
            clean_volume_percent(-1)
        self.assertEqual(caught.exception.code, REJECT_INVALID_VOLUME)

    def test_a_volume_above_one_hundred_is_rejected_not_clamped(self) -> None:
        """(11)(13) 150 is refused. It must never quietly become 100."""
        with self.assertRaises(AudioActionRejected) as caught:
            clean_volume_percent(150)
        self.assertEqual(caught.exception.code, REJECT_INVALID_VOLUME)

    def test_non_numeric_and_nan_volumes_are_rejected(self) -> None:
        """(12) Strings, None, NaN and infinity are all refused."""
        for value in ("50", "", None, [], {}, float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=repr(value)):
                with self.assertRaises(AudioActionRejected):
                    clean_volume_percent(value)

    def test_a_boolean_is_not_a_volume(self) -> None:
        """`True` is an int in Python; 100% by accident is exactly the bug."""
        for value in (True, False):
            with self.subTest(value=value):
                with self.assertRaises(AudioActionRejected):
                    clean_volume_percent(value)

    def test_a_fractional_volume_is_refused_rather_than_rounded(self) -> None:
        with self.assertRaises(AudioActionRejected):
            clean_volume_percent(25.5)
        # A float that is exactly a whole number is a JSON client being a JSON
        # client, and is accepted.
        self.assertEqual(clean_volume_percent(25.0), 25)


class VolumeExecutionTests(unittest.TestCase):
    """(15) The observed value is the answer, never the requested one."""

    def test_setting_the_volume_reports_the_value_read_back(self) -> None:
        host = FakeAudioHost(volumes={58: 50})
        executor, service = executor_for(host)
        result = executor.set_output_volume(first_output_id(service), 25)

        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["observed"]["volume_percent"], 25)
        self.assertEqual(result["requested"]["volume_percent"], 25)
        self.assertEqual(host.volumes[58], 25)

    def test_a_host_that_accepts_and_ignores_is_reported_as_not_applied(self) -> None:
        """(15) The false-success guard: exit code 0, nothing changed."""
        host = FakeAudioHost(volumes={58: 50}, ignore_writes=True)
        executor, service = executor_for(host)
        result = executor.set_output_volume(first_output_id(service), 25)

        self.assertEqual(result["outcome"], OUTCOME_NOT_APPLIED)
        self.assertEqual(result["observed"]["volume_percent"], 50)
        self.assertEqual(result["requested"]["volume_percent"], 25)
        self.assertIn("25", result["message"])
        self.assertIn("50", result["message"])

    def test_a_clamping_route_is_reported_at_the_value_it_reached(self) -> None:
        host = FakeAudioHost(volumes={58: 10}, volume_ceiling=60)
        executor, service = executor_for(host)
        result = executor.set_output_volume(first_output_id(service), 90)

        self.assertEqual(result["outcome"], OUTCOME_NOT_APPLIED)
        self.assertEqual(result["observed"]["volume_percent"], 60)

    def test_the_backend_is_never_asked_for_amplification(self) -> None:
        """(13) No argv this code produces can carry a value above 100%."""
        host = FakeAudioHost(volumes={58: 50})
        executor, service = executor_for(host)
        resource = first_output_id(service)
        with self.assertRaises(AudioActionRejected):
            executor.set_output_volume(resource, 150)

        for argv in host.calls:
            if len(argv) > 3 and argv[1] == "set-volume":
                self.fail("a refused volume must never reach the backend")

    def test_zero_and_one_hundred_reach_the_host(self) -> None:
        host = FakeAudioHost(volumes={58: 50})
        executor, service = executor_for(host)
        resource = first_output_id(service)

        self.assertEqual(
            executor.set_output_volume(resource, 0)["observed"]["volume_percent"], 0
        )
        self.assertEqual(
            executor.set_output_volume(resource, 100)["observed"]["volume_percent"], 100
        )


class MuteExecutionTests(unittest.TestCase):
    """(14) Mute and unmute are each verified on their own."""

    def test_mute_and_unmute_are_independently_verified(self) -> None:
        host = FakeAudioHost(mutes={58: False})
        executor, service = executor_for(host)
        resource = first_output_id(service)

        muted = executor.set_output_mute(resource, True)
        self.assertEqual(muted["outcome"], OUTCOME_APPLIED)
        self.assertTrue(muted["observed"]["muted"])
        self.assertTrue(host.mutes[58])

        unmuted = executor.set_output_mute(resource, False)
        self.assertEqual(unmuted["outcome"], OUTCOME_APPLIED)
        self.assertFalse(unmuted["observed"]["muted"])
        self.assertFalse(host.mutes[58])

    def test_an_unmute_that_did_not_take_effect_is_reported_as_failure(self) -> None:
        host = FakeAudioHost(mutes={58: True}, ignore_writes=True)
        executor, service = executor_for(host)
        result = executor.set_output_mute(first_output_id(service), False)

        self.assertEqual(result["outcome"], OUTCOME_NOT_APPLIED)
        self.assertTrue(result["observed"]["muted"])
        self.assertFalse(result["requested"]["muted"])

    def test_a_non_boolean_mute_is_rejected(self) -> None:
        host = FakeAudioHost()
        executor, service = executor_for(host)
        resource = first_output_id(service)
        for value in (1, 0, "true", None, "yes"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(AudioActionRejected) as caught:
                    executor.set_output_mute(resource, value)
                self.assertEqual(caught.exception.code, REJECT_INVALID_MUTE)


class DefaultOutputTests(unittest.TestCase):
    """(16)(17) Re-read after selecting, and never claim a stream moved."""

    def _with_streams(self, link_target=58, **kwargs):
        from cofferdam.workstation.audio.discovery import AudioStreamDiscovery

        host = FakeAudioHost(
            objects=simple_graph(
                with_hdmi=True,
                streams=[stream(104, 103, "Spotify")],
                clients=[client(103, "Spotify", pid=4242)],
                links=[link(104, link_target)],
            ),
            volumes={58: 50, 70: 30},
            mutes={58: False, 70: False},
            **kwargs,
        )
        discovery = AudioStreamDiscovery(
            known_executables={"spotify": ("spotify",)},
            process_reader=process_reader({4242: FakeProcess(4242, "/usr/bin/spotify")}),
        )
        backend = WirePlumberBackend(runner=host.run, which=host.which)
        service = AudioInventoryService(
            backend=backend, cache_seconds=0.0, stream_discovery=discovery
        )
        return AudioActionExecutor(service), service, host

    def test_the_default_is_re_read_after_selection(self) -> None:
        """(16) The result reports observed default state, not the request."""
        host = FakeAudioHost(objects=simple_graph(with_hdmi=True), volumes={58: 50, 70: 30},
                             mutes={58: False, 70: False})
        executor, service = executor_for(host)
        snapshot = service.snapshot(refresh=True)
        hdmi = [o for o in snapshot.outputs() if o["node_name"] == HDMI_NAME][0]

        result = executor.set_default_output(hdmi["resource_id"])
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertTrue(result["observed"]["is_default"])
        self.assertEqual(host.default_sink, HDMI_NAME)

    def test_a_selection_that_did_not_take_is_reported_as_not_applied(self) -> None:
        host = FakeAudioHost(objects=simple_graph(with_hdmi=True), volumes={58: 50, 70: 30},
                             mutes={58: False, 70: False}, ignore_writes=True)
        executor, service = executor_for(host)
        snapshot = service.snapshot(refresh=True)
        hdmi = [o for o in snapshot.outputs() if o["node_name"] == HDMI_NAME][0]

        result = executor.set_default_output(hdmi["resource_id"])
        self.assertEqual(result["outcome"], OUTCOME_NOT_APPLIED)
        self.assertFalse(result["observed"]["is_default"])

    def test_a_stream_that_did_not_follow_is_reported_as_partial(self) -> None:
        """(17) The honest partial: the default moved, the music did not."""
        executor, service, host = self._with_streams(link_target=58)
        snapshot = service.snapshot(refresh=True)
        hdmi = [o for o in snapshot.outputs() if o["node_name"] == HDMI_NAME][0]

        # The fake host switches the default but leaves the link where it was,
        # which is what WirePlumber does for a stream pinned to a target.
        result = executor.set_default_output(hdmi["resource_id"])

        self.assertEqual(result["outcome"], OUTCOME_PARTIAL)
        self.assertTrue(result["streams"]["already_playing"])
        self.assertEqual(result["streams"]["moved"], [])
        self.assertEqual(len(result["streams"]["stayed"]), 1)
        self.assertIn("already playing", result["message"])

    def test_a_stream_that_followed_is_reported_as_moved(self) -> None:
        executor, service, host = self._with_streams(link_target=58)
        snapshot = service.snapshot(refresh=True)
        hdmi = [o for o in snapshot.outputs() if o["node_name"] == HDMI_NAME][0]

        # This host moves existing streams with the default, as WirePlumber does
        # for a stream that connected to "the default" rather than to a device.
        def follow(fake):
            if fake.default_sink == HDMI_NAME:
                for entry in fake.objects:
                    if entry.get("type") == "PipeWire:Interface:Link":
                        entry["info"]["input-node-id"] = 70

        host.graph_mutator = follow
        result = executor.set_default_output(hdmi["resource_id"])

        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(len(result["streams"]["moved"]), 1)
        self.assertEqual(result["streams"]["moved"][0]["application"], "spotify")
        self.assertEqual(result["streams"]["stayed"], [])

    def test_stream_movement_is_observed_and_says_so(self) -> None:
        """The claim carries its own provenance rather than being asserted."""
        executor, service, host = self._with_streams()
        snapshot = service.snapshot(refresh=True)
        hdmi = [o for o in snapshot.outputs() if o["node_name"] == HDMI_NAME][0]
        result = executor.set_default_output(hdmi["resource_id"])
        self.assertTrue(result["streams"]["verified"])


class ResolutionTests(unittest.TestCase):
    """(5)(6) Stale, changed, and reused references are all refused."""

    def test_a_stale_resource_id_is_rejected(self) -> None:
        """(5) An id from a previous graph resolves to nothing."""
        host = FakeAudioHost()
        executor, service = executor_for(host)
        stale = "aout-" + "0" * 16

        with self.assertRaises(AudioActionRejected) as caught:
            executor.set_output_volume(stale, 30)
        self.assertEqual(caught.exception.code, REJECT_UNKNOWN_RESOURCE)
        # And nothing was attempted against the host.
        self.assertFalse([c for c in host.calls if c[1:2] == ["set-volume"]])

    def test_an_id_from_before_a_server_restart_is_rejected(self) -> None:
        host = FakeAudioHost()
        executor, service = executor_for(host)
        old_id = first_output_id(service)

        host.objects = simple_graph(cookie=555444333)
        with self.assertRaises(AudioActionRejected) as caught:
            executor.set_output_volume(old_id, 30)
        self.assertEqual(caught.exception.code, REJECT_UNKNOWN_RESOURCE)

    def test_a_node_id_reused_by_different_hardware_is_refused_mid_action(self) -> None:
        """(6) The re-verification step, exercised as the race it exists for.

        The snapshot resolves the id, then the graph changes so node 58 is a
        different device before the command would run. Acting anyway would
        change the volume of the wrong speaker.
        """
        host = FakeAudioHost()
        executor, service = executor_for(host)
        resource = first_output_id(service)

        state = {"dumps": 0}

        def swap(fake):
            state["dumps"] += 1
            # The first dump is the snapshot; the second is the pre-action
            # re-verification, and by then the slot has been reassigned.
            if state["dumps"] == 2:
                fake.objects = [
                    core(),
                    device(90, "bluez_card.x", "Some Speaker", api="bluez5", bus="bluetooth"),
                    sink(58, BLUETOOTH_NAME, "Some Speaker", device_id=90),
                    default_metadata(BLUETOOTH_NAME),
                ]

        host.graph_mutator = swap
        with self.assertRaises(AudioActionRejected) as caught:
            executor.set_output_volume(resource, 30)
        self.assertEqual(caught.exception.code, REJECT_RESOURCE_CHANGED)
        self.assertFalse([c for c in host.calls if c[1:2] == ["set-volume"]])

    def test_a_replaced_object_at_the_same_name_is_refused(self) -> None:
        """A new object serial at the same node name means a new device."""
        host = FakeAudioHost()
        executor, service = executor_for(host)
        resource = first_output_id(service)

        state = {"dumps": 0}

        def replace(fake):
            state["dumps"] += 1
            if state["dumps"] == 2:
                fake.objects = simple_graph(speaker_serial=99999)

        host.graph_mutator = replace
        with self.assertRaises(AudioActionRejected) as caught:
            executor.set_output_volume(resource, 30)
        self.assertEqual(caught.exception.code, REJECT_RESOURCE_CHANGED)

    def test_an_audio_server_restart_mid_action_is_refused(self) -> None:
        host = FakeAudioHost()
        executor, service = executor_for(host)
        resource = first_output_id(service)

        state = {"dumps": 0}

        def restart(fake):
            state["dumps"] += 1
            if state["dumps"] == 2:
                fake.objects = simple_graph(cookie=777666555)

        host.graph_mutator = restart
        with self.assertRaises(AudioActionRejected) as caught:
            executor.set_output_volume(resource, 30)
        self.assertEqual(caught.exception.code, REJECT_GRAPH_CHANGED)

    def test_a_display_name_is_never_accepted_as_a_reference(self) -> None:
        """No fallback to matching by name, however exact the name is."""
        host = FakeAudioHost()
        executor, service = executor_for(host)
        for candidate in ("Raptor Lake-P/U/H cAVS Speaker", SPEAKER_NAME, "58", 58, None, ""):
            with self.subTest(candidate=repr(candidate)):
                with self.assertRaises(AudioActionRejected):
                    executor.set_output_volume(candidate, 30)

    def test_a_disappeared_output_is_refused_before_acting(self) -> None:
        host = FakeAudioHost()
        executor, service = executor_for(host)
        resource = first_output_id(service)

        state = {"dumps": 0}

        def unplug(fake):
            state["dumps"] += 1
            if state["dumps"] == 2:
                fake.objects = [core(), default_metadata(None)]

        host.graph_mutator = unplug
        with self.assertRaises(AudioActionRejected) as caught:
            executor.set_output_mute(resource, True)
        self.assertEqual(caught.exception.code, REJECT_RESOURCE_CHANGED)


class StreamMoveTests(unittest.TestCase):
    """(18) Unsupported means refused, never a silent no-op."""

    def test_moving_a_stream_is_refused_with_a_reason(self) -> None:
        host = FakeAudioHost()
        executor, _ = executor_for(host)
        with self.assertRaises(AudioActionRejected) as caught:
            executor.move_stream("astream-anything", "aout-anything")
        self.assertEqual(caught.exception.code, REJECT_UNSUPPORTED)
        self.assertTrue(caught.exception.detail)

    def test_the_capability_is_published_as_unavailable(self) -> None:
        """Not absent, and not an empty success: named, with the reason."""
        host = FakeAudioHost()
        _, service = executor_for(host)
        snapshot = service.snapshot(refresh=True)
        capability = snapshot.capability("move_audio_stream")

        self.assertIsNotNone(capability)
        self.assertEqual(capability.state, "unavailable")
        self.assertTrue(capability.reason)

    def test_the_supported_capabilities_are_published_as_supported(self) -> None:
        host = FakeAudioHost()
        _, service = executor_for(host)
        snapshot = service.snapshot(refresh=True)
        for name in ("set_default_audio_output", "set_output_volume", "set_output_mute"):
            with self.subTest(name=name):
                self.assertEqual(snapshot.capability(name).state, "supported")


class NoShellTests(unittest.TestCase):
    """(21) Every argument the backend builds is a fixed token or a number."""

    def test_no_argument_ever_carries_caller_text(self) -> None:
        host = FakeAudioHost(volumes={58: 50})
        executor, service = executor_for(host)
        resource = first_output_id(service)
        executor.set_output_volume(resource, 40)
        executor.set_output_mute(resource, True)
        executor.set_default_output(resource)

        allowed_programs = {"pw-dump", "wpctl"}
        allowed_commands = {"get-volume", "set-volume", "set-mute", "set-default"}
        for argv in host.calls:
            self.assertIn(argv[0], allowed_programs)
            # A resource id must never appear in an argument vector: the backend
            # is addressed by node id, which it derived itself.
            self.assertNotIn(resource, argv)
            for token in argv[1:]:
                self.assertNotRegex(token, r"[;&|`$><\n]")
            if argv[0] == "wpctl":
                self.assertIn(argv[1], allowed_commands)
                self.assertTrue(argv[2].isdigit())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
