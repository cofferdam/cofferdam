"""The audio HTTP boundary (M2C checks 19-26).

These are the tests for the claim in ``service.py``'s docstring: a client may
send a resource id, an integer, and a boolean, and there is no fourth thing.
Everything here goes through the real ASGI app.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ._audio_doubles import (
    FakeAudioHost,
    FakeProcess,
    HDMI_NAME,
    client as fake_client,
    link,
    process_reader,
    simple_graph,
    stream,
)
from ._workstation_doubles import TEST_TOKEN, require_fastapi


class AudioApiTestCase(unittest.TestCase):
    """An app whose audio service is backed by the fake host."""

    ignore_writes = False

    def setUp(self) -> None:
        require_fastapi()
        from fastapi.testclient import TestClient

        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.audio.discovery import AudioStreamDiscovery
        from cofferdam.workstation.audio.service import AudioInventoryService
        from cofferdam.workstation.audio.wireplumber import WirePlumberBackend
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.service import create_app

        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.config = load_config(home=self.home)
        self.config.ensure_dirs()

        self.host = FakeAudioHost(
            objects=simple_graph(
                with_hdmi=True,
                streams=[stream(104, 103, "Spotify")],
                clients=[fake_client(103, "Spotify", pid=4242)],
                links=[link(104, 58)],
            ),
            volumes={58: 50, 70: 30},
            mutes={58: False, 70: False},
            ignore_writes=self.ignore_writes,
        )
        backend = WirePlumberBackend(runner=self.host.run, which=self.host.which)
        discovery = AudioStreamDiscovery(
            known_executables={"spotify": ("spotify",)},
            process_reader=process_reader({4242: FakeProcess(4242, "/usr/bin/spotify")}),
        )
        audio = AudioInventoryService(
            backend=backend, cache_seconds=0.0, stream_discovery=discovery
        )
        self.app = create_app(
            config=self.config,
            token=TEST_TOKEN,
            adapter=StubAdapter(self.config),
            audio=audio,
        )
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self._tmp.cleanup()

    @property
    def auth(self) -> dict:
        return {"Authorization": f"Bearer {TEST_TOKEN}"}

    def snapshot(self) -> dict:
        return self.client.get("/api/audio", headers=self.auth).json()

    def default_output_id(self) -> str:
        return self.snapshot()["default_output_resource_id"]

    def other_output_id(self) -> str:
        payload = self.snapshot()
        for item in payload["collections"]["outputs"]["items"]:
            if item["node_name"] == HDMI_NAME:
                return item["resource_id"]
        raise AssertionError("the fixture should expose a second output")


class AuthenticationTests(AudioApiTestCase):
    """(22) Every audio route requires the token."""

    def test_every_audio_route_requires_authentication(self) -> None:
        resource = self.default_output_id()
        cases = [
            ("get", "/api/audio", None),
            ("get", "/api/audio/outputs", None),
            ("get", "/api/audio/streams", None),
            ("put", f"/api/audio/outputs/{resource}/default", {}),
            ("put", f"/api/audio/outputs/{resource}/volume", {"volume_percent": 20}),
            ("put", f"/api/audio/outputs/{resource}/mute", {"muted": True}),
            ("put", f"/api/audio/streams/x/output", {"output_resource_id": resource}),
        ]
        for method, path, body in cases:
            with self.subTest(path=path, method=method):
                call = getattr(self.client, method)
                response = call(path) if body is None else call(path, json=body)
                self.assertEqual(response.status_code, 401)

    def test_a_wrong_token_is_refused(self) -> None:
        response = self.client.get(
            "/api/audio", headers={"Authorization": "Bearer not-the-token"}
        )
        self.assertEqual(response.status_code, 401)


class ReadOnlyTests(AudioApiTestCase):
    """(23) A GET never changes anything."""

    def test_reading_the_snapshot_changes_no_host_state(self) -> None:
        before = (dict(self.host.volumes), dict(self.host.mutes), self.host.default_sink)
        for path in ("/api/audio", "/api/audio/outputs", "/api/audio/streams"):
            self.client.get(path, headers=self.auth)
            self.client.get(path + "?refresh=true", headers=self.auth)
        after = (dict(self.host.volumes), dict(self.host.mutes), self.host.default_sink)

        self.assertEqual(before, after)
        # And nothing that writes was ever executed.
        for argv in self.host.calls:
            self.assertNotIn(argv[1:2], (["set-volume"], ["set-mute"], ["set-default"]))

    def test_the_mutating_routes_cannot_be_driven_by_get(self) -> None:
        """No GET reaches a mutating handler, and none changes anything.

        The status is 404 rather than 405 because the PWA's static mount at
        ``/`` full-matches any GET before the router can offer a
        method-not-allowed for the PUT-only path. Which code comes back matters
        far less than the property being asserted: the handler is not reached
        and the host does not move.
        """
        resource = self.default_output_id()
        before = (dict(self.host.volumes), dict(self.host.mutes), self.host.default_sink)
        for path in (
            f"/api/audio/outputs/{resource}/volume",
            f"/api/audio/outputs/{resource}/mute",
            f"/api/audio/outputs/{resource}/default",
            f"/api/audio/outputs/{resource}/volume?volume_percent=100",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, headers=self.auth)
                self.assertIn(response.status_code, (404, 405))
        after = (dict(self.host.volumes), dict(self.host.mutes), self.host.default_sink)
        self.assertEqual(before, after)

    def test_an_unknown_collection_is_a_404_that_does_not_echo_the_request(self) -> None:
        response = self.client.get("/api/audio/../etc/passwd", headers=self.auth)
        self.assertIn(response.status_code, (404, 400))
        self.assertNotIn("passwd", response.text)


class RequestShapeTests(AudioApiTestCase):
    """(19)(20)(24)(25) What a client is allowed to send, and nothing else."""

    def test_a_non_json_content_type_is_rejected(self) -> None:
        """(24)"""
        resource = self.default_output_id()
        response = self.client.put(
            f"/api/audio/outputs/{resource}/volume",
            headers={**self.auth, "Content-Type": "text/plain"},
            content="30",
        )
        self.assertEqual(response.status_code, 415)

    def test_an_oversized_payload_is_rejected(self) -> None:
        """(25)"""
        resource = self.default_output_id()
        response = self.client.put(
            f"/api/audio/outputs/{resource}/volume",
            headers=self.auth,
            json={"volume_percent": 30, "padding": "x" * 5000},
        )
        self.assertIn(response.status_code, (413, 422))
        self.assertEqual(self.host.volumes[58], 50)

    def test_a_client_cannot_submit_a_raw_backend_command(self) -> None:
        """(19) There is no field for a command, and unknown fields are refused."""
        resource = self.default_output_id()
        for body in (
            {"command": "wpctl set-volume 58 200%"},
            {"argv": ["wpctl", "set-volume", "58", "200%"]},
            {"shell": "true"},
            {"exec": "/bin/sh"},
            {"volume_percent": 30, "command": "rm -rf /"},
            {"properties": {"node.name": "x"}},
            {"volume_percent": 30, "node_id": 58},
        ):
            with self.subTest(body=body):
                response = self.client.put(
                    f"/api/audio/outputs/{resource}/volume", headers=self.auth, json=body
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.host.volumes[58], 50)

    def test_a_client_cannot_address_an_output_by_backend_node_id(self) -> None:
        """(20) A numeric node id is not authority for anything."""
        for candidate in ("58", "70", "node-58", "0"):
            with self.subTest(candidate=candidate):
                response = self.client.put(
                    f"/api/audio/outputs/{candidate}/volume",
                    headers=self.auth,
                    json={"volume_percent": 10},
                )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(self.host.volumes[58], 50)

    def test_a_missing_required_field_is_refused(self) -> None:
        resource = self.default_output_id()
        self.assertEqual(
            self.client.put(
                f"/api/audio/outputs/{resource}/volume", headers=self.auth, json={}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.put(
                f"/api/audio/outputs/{resource}/mute", headers=self.auth, json={}
            ).status_code,
            422,
        )

    def test_a_malformed_body_is_refused(self) -> None:
        resource = self.default_output_id()
        response = self.client.put(
            f"/api/audio/outputs/{resource}/volume",
            headers={**self.auth, "Content-Type": "application/json"},
            content="{not json",
        )
        self.assertEqual(response.status_code, 400)

    def test_out_of_range_volumes_are_refused_over_http(self) -> None:
        resource = self.default_output_id()
        for value in (-1, 101, 150, 1000, "50", True, None, 25.5):
            with self.subTest(value=repr(value)):
                response = self.client.put(
                    f"/api/audio/outputs/{resource}/volume",
                    headers=self.auth,
                    json={"volume_percent": value},
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "audio_volume_invalid")
                self.assertEqual(self.host.volumes[58], 50)

    def test_the_range_bounds_are_accepted_over_http(self) -> None:
        resource = self.default_output_id()
        for value in (0, 100):
            with self.subTest(value=value):
                response = self.client.put(
                    f"/api/audio/outputs/{resource}/volume",
                    headers=self.auth,
                    json={"volume_percent": value},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["observed"]["volume_percent"], value)


class OutcomeTests(AudioApiTestCase):
    """The response reports observed state, and the audit follows it."""

    def test_a_successful_change_reports_the_observed_value(self) -> None:
        resource = self.default_output_id()
        response = self.client.put(
            f"/api/audio/outputs/{resource}/volume", headers=self.auth,
            json={"volume_percent": 25},
        )
        payload = response.json()
        self.assertEqual(payload["outcome"], "applied")
        self.assertEqual(payload["observed"]["volume_percent"], 25)
        self.assertEqual(payload["requested"]["volume_percent"], 25)

    def test_selecting_an_output_returns_the_observed_result(self) -> None:
        """(33 server half) The response says where things actually ended up."""
        target = self.other_output_id()
        payload = self.client.put(
            f"/api/audio/outputs/{target}/default", headers=self.auth, json={}
        ).json()

        self.assertIn(payload["outcome"], ("applied", "partially_applied"))
        self.assertTrue(payload["observed"]["is_default"])
        self.assertIn("streams", payload)
        self.assertTrue(payload["streams"]["verified"])

    def test_moving_a_stream_is_a_documented_501(self) -> None:
        payload_response = self.client.put(
            "/api/audio/streams/astream-whatever/output",
            headers=self.auth,
            json={"output_resource_id": self.default_output_id()},
        )
        self.assertEqual(payload_response.status_code, 501)
        body = payload_response.json()
        self.assertEqual(body["error"]["code"], "audio_action_unsupported")
        self.assertTrue(body["error"]["detail"])

    def test_actions_are_audited_without_content(self) -> None:
        """(26 part) The audit records the operation, not what was playing."""
        resource = self.default_output_id()
        self.client.put(
            f"/api/audio/outputs/{resource}/volume", headers=self.auth,
            json={"volume_percent": 35},
        )
        records = self.client.get("/api/actions", headers=self.auth).json()["actions"]
        audio_records = [r for r in records if r["action"] == "set_output_volume"]
        self.assertTrue(audio_records)

        blob = json.dumps(records)
        self.assertNotIn("A Track Title", blob)
        self.assertNotIn("35", json.dumps(audio_records[0]["params"]))

    def test_a_refused_action_is_also_audited(self) -> None:
        self.client.put(
            "/api/audio/outputs/aout-000000/volume", headers=self.auth,
            json={"volume_percent": 35},
        )
        records = self.client.get("/api/actions", headers=self.auth).json()["actions"]
        refused = [r for r in records if r["action"] == "set_output_volume"]
        self.assertTrue(refused)
        self.assertEqual(refused[0]["status"], "failed")


class FalseSuccessOverHttpTests(AudioApiTestCase):
    """A host that accepts and ignores must not produce a 200 'applied'."""

    ignore_writes = True

    def test_an_ignored_command_is_reported_truthfully(self) -> None:
        resource = self.default_output_id()
        payload = self.client.put(
            f"/api/audio/outputs/{resource}/volume", headers=self.auth,
            json={"volume_percent": 25},
        ).json()

        self.assertEqual(payload["outcome"], "not_applied")
        self.assertEqual(payload["observed"]["volume_percent"], 50)

    def test_the_audit_records_the_failure_not_the_attempt(self) -> None:
        resource = self.default_output_id()
        self.client.put(
            f"/api/audio/outputs/{resource}/mute", headers=self.auth, json={"muted": True}
        )
        records = self.client.get("/api/actions", headers=self.auth).json()["actions"]
        mutes = [r for r in records if r["action"] == "set_output_mute"]
        self.assertTrue(mutes)
        self.assertEqual(mutes[0]["status"], "failed")


class SecretsTests(AudioApiTestCase):
    """(26) No credential or token ever appears in an audio payload."""

    def test_no_token_or_credential_appears_in_any_audio_response(self) -> None:
        resource = self.default_output_id()
        bodies = [
            self.client.get("/api/audio", headers=self.auth).text,
            self.client.get("/api/audio/outputs", headers=self.auth).text,
            self.client.get("/api/audio/streams", headers=self.auth).text,
            self.client.put(
                f"/api/audio/outputs/{resource}/volume", headers=self.auth,
                json={"volume_percent": 30},
            ).text,
        ]
        for body in bodies:
            self.assertNotIn(TEST_TOKEN, body)
            for word in ("client_secret", "api_key", "Authorization", "Bearer ",
                         "spotify_client", "youtube_api"):
                self.assertNotIn(word, body)

    def test_no_media_title_appears_in_any_audio_response(self) -> None:
        """(27 over HTTP)"""
        for path in ("/api/audio", "/api/audio/streams"):
            self.assertNotIn("A Track Title", self.client.get(path, headers=self.auth).text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
