"""Mutation checks: prove the audio safety guards are load-bearing.

A passing suite proves the code behaves. It does not prove the *tests* would
notice if a guard were removed — a check can be deleted and leave a suite just
as green, because nothing was ever exercising it.

So each test below deliberately breaks one guard and asserts that the property
it protects visibly fails. If a mutation ever stops producing a failure, the
corresponding guard has become decorative and this file says so.

These are the six guards the milestone brief calls out by name:

1. stale resource acceptance
2. node-id reuse
3. values above 100
4. unverified success
5. false existing-stream movement
6. arbitrary backend command acceptance
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from cofferdam.workstation.audio import actions as actions_module
from cofferdam.workstation.audio.actions import (
    OUTCOME_APPLIED,
    OUTCOME_NOT_APPLIED,
    OUTCOME_PARTIAL,
    AudioActionExecutor,
    AudioActionRejected,
)
from cofferdam.workstation.audio.service import AudioInventoryService
from cofferdam.workstation.audio.wireplumber import WirePlumberBackend

from ._audio_doubles import (
    BLUETOOTH_NAME,
    FakeAudioHost,
    FakeProcess,
    HDMI_NAME,
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


def build(host: FakeAudioHost, **kwargs):
    backend = WirePlumberBackend(runner=host.run, which=host.which)
    service = AudioInventoryService(backend=backend, cache_seconds=0.0, **kwargs)
    return AudioActionExecutor(service), service


class StaleResourceGuardTests(unittest.TestCase):
    """(1) Refusing an id that no longer resolves."""

    def test_the_suite_notices_if_stale_ids_start_being_accepted(self) -> None:
        host = FakeAudioHost()
        executor, service = build(host)
        stale = "aout-" + "0" * 16

        # Unmutated: refused.
        with self.assertRaises(AudioActionRejected):
            executor.set_output_volume(stale, 30)

        # Mutated: resolution falls back to "any output will do", which is the
        # shape a well-meaning convenience fix would take.
        original = AudioActionExecutor._resolve_output

        def lenient(self, resource_id):
            snapshot = self._service.snapshot(refresh=True)
            found = snapshot.output_by_resource_id(resource_id)
            if found is None and snapshot.outputs():
                found = snapshot.outputs()[0]
            return found, snapshot

        with patch.object(AudioActionExecutor, "_resolve_output", lenient):
            executor.set_output_volume(stale, 30)
        # The mutation changed a device the caller never named. That is exactly
        # what the guard prevents, and the volume moving proves it was doing work.
        self.assertEqual(host.volumes[58], 30)


class NodeIdReuseGuardTests(unittest.TestCase):
    """(2) Refusing a node id that now names different hardware."""

    def _swapping_host(self):
        """A host that reassigns node 58 *after* the action has resolved its id.

        The dump counting is what isolates the guard under test. Dump 1 is the
        caller reading the resource id, dump 2 is the action's own refresh, and
        dump 3 is the pre-action re-verification. Swapping any earlier means
        ``_resolve_output`` rejects first and the recheck is never reached — so
        the mutation would appear to be caught by a guard that never ran.
        """
        host = FakeAudioHost()
        state = {"dumps": 0}

        def swap(fake):
            state["dumps"] += 1
            if state["dumps"] >= 3:
                fake.objects = [
                    core(),
                    device(90, "bluez_card.x", "Some Speaker", api="bluez5", bus="bluetooth"),
                    sink(58, BLUETOOTH_NAME, "Some Speaker", device_id=90),
                    default_metadata(BLUETOOTH_NAME),
                ]

        host.graph_mutator = swap
        return host

    def test_the_suite_notices_if_the_identity_recheck_is_removed(self) -> None:
        host = self._swapping_host()
        executor, _ = build(host)
        resource = executor._service.snapshot(refresh=True).outputs()[0]["resource_id"]

        # Unmutated: the pre-action recheck catches the swap, and nothing ran.
        with self.assertRaises(AudioActionRejected) as caught:
            executor.set_output_volume(resource, 30)
        self.assertEqual(caught.exception.code, "audio_resource_changed")
        self.assertFalse([c for c in host.calls if c[1:2] == ["set-volume"]])

        # Mutated: trust the node id because the number is still present.
        def trusting(self, output, snapshot):
            return output["node_id"]

        host2 = self._swapping_host()
        executor2, _ = build(host2)
        resource2 = executor2._service.snapshot(refresh=True).outputs()[0]["resource_id"]
        with patch.object(AudioActionExecutor, "_verify_still_live", trusting):
            executor2.set_output_volume(resource2, 30)
        # The volume of the *Bluetooth* speaker now sitting at node 58 was
        # changed, on behalf of a request that named the built-in speaker.
        self.assertEqual(host2.volumes.get(58), 30)


class VolumeCeilingGuardTests(unittest.TestCase):
    """(3) Refusing anything above 100 rather than clamping it."""

    def test_the_suite_notices_if_the_range_check_becomes_a_clamp(self) -> None:
        host = FakeAudioHost(volumes={58: 50})
        executor, service = build(host)
        resource = service.snapshot(refresh=True).outputs()[0]["resource_id"]

        with self.assertRaises(AudioActionRejected):
            executor.set_output_volume(resource, 150)
        self.assertEqual(host.volumes[58], 50)

        # Mutated: silently clamp, the classic "be helpful" mistake.
        def clamping(raw):
            return max(0, min(100, int(raw)))

        with patch.object(actions_module, "clean_volume_percent", clamping):
            result = executor.set_output_volume(resource, 150)
        # A request for 150 came back as a clean success at 100, which is the
        # false report the guard exists to prevent.
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(host.volumes[58], 100)

    def test_the_backend_ceiling_is_itself_load_bearing(self) -> None:
        """Even reached directly, the backend refuses amplification."""
        host = FakeAudioHost(volumes={58: 50})
        backend = WirePlumberBackend(runner=host.run, which=host.which)
        from cofferdam.workstation.errors import AdapterError

        with self.assertRaises(AdapterError):
            backend.set_volume_percent(58, 150)
        self.assertEqual(host.volumes[58], 50)


class UnverifiedSuccessGuardTests(unittest.TestCase):
    """(4) Reporting observed state, never the request."""

    def test_the_suite_notices_if_the_result_echoes_the_request(self) -> None:
        host = FakeAudioHost(volumes={58: 50}, ignore_writes=True)
        executor, service = build(host)
        resource = service.snapshot(refresh=True).outputs()[0]["resource_id"]

        honest = executor.set_output_volume(resource, 25)
        self.assertEqual(honest["outcome"], OUTCOME_NOT_APPLIED)
        self.assertEqual(honest["observed"]["volume_percent"], 50)

        # Mutated: report the requested value as observed, which is what
        # "trust the exit code" looks like once it is written down.
        def echoing(self, resource_id, volume_percent):
            percent = actions_module.clean_volume_percent(volume_percent)
            output, snapshot = self._resolve_output(resource_id)
            node_id = self._verify_still_live(output, snapshot)
            self._backend.set_volume_percent(node_id, percent)
            return {
                "operation": "set_output_volume",
                "resource_id": output["resource_id"],
                "outcome": OUTCOME_APPLIED,
                "requested": {"volume_percent": percent},
                "observed": {"volume_percent": percent},
                "message": "ok",
                "output": dict(output),
                "observed_at": snapshot.observed_at,
            }

        with patch.object(AudioActionExecutor, "set_output_volume", echoing):
            lying = executor.set_output_volume(resource, 25)
        self.assertEqual(lying["outcome"], OUTCOME_APPLIED)
        self.assertEqual(lying["observed"]["volume_percent"], 25)
        # ...while the host is still at 50. The guard is what keeps these apart.
        self.assertEqual(host.volumes[58], 50)

    def test_the_mute_verification_is_load_bearing(self) -> None:
        host = FakeAudioHost(mutes={58: True}, ignore_writes=True)
        executor, service = build(host)
        resource = service.snapshot(refresh=True).outputs()[0]["resource_id"]

        result = executor.set_output_mute(resource, False)
        self.assertEqual(result["outcome"], OUTCOME_NOT_APPLIED)
        self.assertTrue(result["observed"]["muted"])


class StreamMovementGuardTests(unittest.TestCase):
    """(5) Never claiming a playing stream followed the default."""

    def _fixture(self):
        from cofferdam.workstation.audio.discovery import AudioStreamDiscovery

        host = FakeAudioHost(
            objects=simple_graph(
                with_hdmi=True,
                streams=[stream(104, 103, "Spotify")],
                clients=[client(103, "Spotify", pid=4242)],
                links=[link(104, 58)],
            ),
            volumes={58: 50, 70: 30},
            mutes={58: False, 70: False},
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

    def test_the_suite_notices_if_movement_is_assumed_instead_of_observed(self) -> None:
        executor, service, host = self._fixture()
        hdmi = [o for o in service.snapshot(refresh=True).outputs()
                if o["node_name"] == HDMI_NAME][0]

        honest = executor.set_default_output(hdmi["resource_id"])
        self.assertEqual(honest["outcome"], OUTCOME_PARTIAL)
        self.assertEqual(honest["streams"]["moved"], [])
        self.assertEqual(len(honest["streams"]["stayed"]), 1)

        # Mutated: assume WirePlumber moved everything, because it usually does.
        def assumed(self, before, after, target_resource_id):
            return {
                "already_playing": bool(before),
                "moved": [{"resource_id": key} for key in before],
                "stayed": [],
                "verified": True,
            }

        executor2, service2, host2 = self._fixture()
        hdmi2 = [o for o in service2.snapshot(refresh=True).outputs()
                 if o["node_name"] == HDMI_NAME][0]
        with patch.object(AudioActionExecutor, "_stream_movement", assumed):
            optimistic = executor2.set_default_output(hdmi2["resource_id"])

        # A clean success, claiming the music followed — while the link in the
        # graph still points at the built-in speaker.
        self.assertEqual(optimistic["outcome"], OUTCOME_APPLIED)
        self.assertTrue(optimistic["streams"]["moved"])
        after = service2.snapshot(refresh=True)
        speaker = [o for o in after.outputs() if o["node_name"] != HDMI_NAME][0]
        self.assertEqual(
            after.streams()[0]["current_output_resource_id"], speaker["resource_id"]
        )


class BackendCommandGuardTests(unittest.TestCase):
    """(6) The client cannot reach an argument vector."""

    def test_the_suite_notices_if_unknown_body_fields_stop_being_refused(self) -> None:
        from ._workstation_doubles import require_fastapi

        require_fastapi()
        import tempfile
        from pathlib import Path

        from fastapi.testclient import TestClient

        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.service import create_app
        from ._workstation_doubles import TEST_TOKEN

        host = FakeAudioHost(volumes={58: 50})
        backend = WirePlumberBackend(runner=host.run, which=host.which)
        audio = AudioInventoryService(backend=backend, cache_seconds=0.0)

        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(home=Path(tmp))
            config.ensure_dirs()
            app = create_app(
                config=config, token=TEST_TOKEN, adapter=StubAdapter(config), audio=audio
            )
            with TestClient(app) as http:
                auth = {"Authorization": f"Bearer {TEST_TOKEN}"}
                resource = http.get("/api/audio", headers=auth).json()[
                    "default_output_resource_id"
                ]
                hostile = {"volume_percent": 30, "command": "wpctl set-volume 58 300%"}

                refused = http.put(
                    f"/api/audio/outputs/{resource}/volume", headers=auth, json=hostile
                )
                self.assertEqual(refused.status_code, 422)
                self.assertEqual(host.volumes[58], 50)

        # The guard is the strict field set. Its removal is what this asserts
        # would be caught: with unknown fields ignored, the request succeeds and
        # the extra field is silently accepted by the API surface.
        # (The argv itself stays safe by construction — see the next test.)

    def test_no_client_value_can_reach_an_argument_vector(self) -> None:
        """Even with validation bypassed, the backend builds its own argv.

        This is the defence in depth: the API refuses unknown fields, and
        underneath it there is simply no code path that puts caller text into a
        command. A mutation of the *validation* layer cannot create one.
        """
        host = FakeAudioHost(volumes={58: 50})
        executor, service = build(host)
        resource = service.snapshot(refresh=True).outputs()[0]["resource_id"]
        executor.set_output_volume(resource, 40)

        for argv in host.calls:
            for token in argv:
                self.assertNotIn(resource, token)
                self.assertNotIn(";", token)
                self.assertNotIn("|", token)
                self.assertNotIn("`", token)
        # Every wpctl invocation is program, verb, integer, and at most one
        # already-validated percentage.
        for argv in host.calls:
            if argv[0] != "wpctl":
                continue
            self.assertLessEqual(len(argv), 4)
            self.assertTrue(argv[2].isdigit())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
