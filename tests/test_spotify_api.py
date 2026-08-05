"""The Spotify HTTP boundary (M2D checks 18–20, 23, 29–34).

The claim under test is the one in ``service.py``'s docstring: a client may send
an opaque device handle, a search id, a result id, an integer and a boolean, and
there is no sixth thing. In particular there is no field for a Spotify URI, a
track id, a device id, an access token, an authorization code or a redirect URI
— they are *absent from the schema*, not validated and rejected, and these tests
prove the difference by sending them.

Everything here goes through the real ASGI app.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from ._mediasearch_doubles import ALL_FAKE_CREDENTIALS, write_credentials
from ._spotifyplayer_doubles import (
    ALL_FAKE_OAUTH_SECRETS,
    FAKE_AUTHORIZATION_CODE,
    FAKE_REFRESH_TOKEN,
    TRACK_ID,
    FakeSpotify,
    device,
    write_user_tokens,
)
from ._workstation_doubles import TEST_TOKEN, require_fastapi


class SpotifyApiTestCase(unittest.TestCase):
    """An app whose Spotify player is backed by the fake transport."""

    connected = True

    def setUp(self) -> None:
        require_fastapi()
        from fastapi.testclient import TestClient

        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.mediasearch.credentials import CredentialStore
        from cofferdam.workstation.service import create_app
        from cofferdam.workstation.spotifyplayer.client import SpotifyPlayerClient
        from cofferdam.workstation.spotifyplayer.service import SpotifyPlayerService
        from cofferdam.workstation.spotifyplayer.tokens import TokenStore

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.config = load_config(home=self.home)
        self.config.ensure_dirs()
        write_credentials(self.config, youtube=False)
        if self.connected:
            write_user_tokens(self.config)

        self.spotify = FakeSpotify()
        tokens = TokenStore(self.config)
        service = SpotifyPlayerService(
            self.config,
            CredentialStore(self.config),
            token_store=tokens,
            client=SpotifyPlayerClient(lambda: "test-client-id", tokens, request=self.spotify),
            cache_seconds=0.0,
        )
        self.app = create_app(
            config=self.config,
            token=TEST_TOKEN,
            adapter=StubAdapter(self.config),
            spotify=service,
        )
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

    # -- helpers -----------------------------------------------------------

    @property
    def auth(self):
        return {"Authorization": "Bearer " + TEST_TOKEN}

    def get(self, path):
        return self.client.get(path, headers=self.auth)

    def post(self, path, body=None):
        return self.client.post(path, headers=self.auth, json=body if body is not None else {})

    def put(self, path, body=None):
        return self.client.put(path, headers=self.auth, json=body if body is not None else {})

    def seed_search(self, *, provider_id="spotify", item_type="track", item_id=TRACK_ID):
        """Put a verified search session into the app's own session store."""
        from cofferdam.workstation.mediasearch.results import (
            MediaResult,
            MediaSearchOutcome,
            ProviderItem,
        )

        result = MediaResult(
            provider_id=provider_id, result_id="mres-one", result_type=item_type,
            title="Gönül Dağı", creators=("Neşet Ertaş",),
        )
        outcome = MediaSearchOutcome(
            provider_id=provider_id, query="Gönül Dağı", results=(result,),
            items=(ProviderItem(provider_id=provider_id, item_type=item_type, item_id=item_id),),
        )
        return self.app.state.media_search.sessions.create(outcome)


class AuthenticationTests(SpotifyApiTestCase):
    """Check 20: every route needs the device token."""

    ROUTES = (
        ("GET", "/api/spotify/playback"),
        ("POST", "/api/spotify/authorize"),
        ("DELETE", "/api/spotify/authorize"),
        ("POST", "/api/spotify/disconnect"),
        ("POST", "/api/spotify/player/pause"),
        ("POST", "/api/spotify/player/resume"),
        ("POST", "/api/spotify/player/next"),
        ("POST", "/api/spotify/player/previous"),
        ("PUT", "/api/spotify/player/volume"),
        ("PUT", "/api/spotify/player/mute"),
        ("PUT", "/api/spotify/player/device"),
        ("POST", "/api/media/searches/s/results/r/spotify/play"),
        ("POST", "/api/media/searches/s/results/r/spotify/queue"),
    )

    def test_no_route_answers_without_a_token(self) -> None:
        for method, path in self.ROUTES:
            with self.subTest(route=f"{method} {path}"):
                response = self.client.request(method, path, json={})
                self.assertEqual(response.status_code, 401)

    def test_no_route_answers_with_the_wrong_token(self) -> None:
        for method, path in self.ROUTES:
            with self.subTest(route=f"{method} {path}"):
                response = self.client.request(
                    method, path, headers={"Authorization": "Bearer wrong"}, json={}
                )
                self.assertEqual(response.status_code, 401)

    def test_an_unauthenticated_request_reaches_no_provider(self) -> None:
        for method, path in self.ROUTES:
            self.client.request(method, path, json={})
        self.assertEqual(self.spotify.calls, [])


class ReadOnlyGetTests(SpotifyApiTestCase):
    """Check 18: no GET in this group changes anything."""

    def test_every_spotify_get_is_a_read_and_nothing_else(self) -> None:
        """Two GETs exist, and both are named as reads.

        ``/api/spotify/activity`` (M2D.1) joined ``/playback`` when cold-start
        recovery made an operation long enough that a phone needs to know which
        step it is on. It touches neither Spotify nor the filesystem.
        """
        gets = sorted(
            route.path
            for route in self.app.routes
            if getattr(route, "path", "").startswith("/api/spotify")
            and "GET" in (getattr(route, "methods", None) or ())
        )
        self.assertEqual(gets, ["/api/spotify/activity", "/api/spotify/playback"])

    def test_reading_the_activity_route_makes_no_provider_call(self) -> None:
        response = self.get("/api/spotify/activity")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.spotify.calls, [])
        self.assertEqual(
            set(response.json()),
            {"active", "operation", "phase", "label", "correlation_id", "elapsed_ms"},
        )

    def test_the_activity_route_carries_no_track_or_device(self) -> None:
        session = self.seed_search()
        self.post(f"/api/media/searches/{session.search_id}/results/mres-one/spotify/play")
        body = self.get("/api/spotify/activity").text
        for personal in ("Gönül", "Ertaş", TRACK_ID, "dev-workstation", "Test Listener"):
            self.assertNotIn(personal, body)

    def test_reading_playback_sends_no_write_to_the_provider(self) -> None:
        self.assertEqual(self.get("/api/spotify/playback").status_code, 200)
        for call in self.spotify.calls:
            if call["host"] == "api.spotify.com":
                self.assertEqual(call["method"], "GET", f"{call['method']} {call['path']}")

    def test_reading_playback_twice_leaves_the_provider_state_alone(self) -> None:
        first = self.get("/api/spotify/playback").json()
        second = self.get("/api/spotify/playback").json()
        self.assertEqual(first["is_playing"], second["is_playing"])
        self.assertEqual(
            first["active_device_resource_id"], second["active_device_resource_id"]
        )

    def test_no_spotify_get_route_takes_a_body(self) -> None:
        """A GET with a body is how a mutating read sneaks in."""
        response = self.client.request(
            "GET", "/api/spotify/playback", headers=self.auth, json={"muted": True}
        )
        # Accepted or refused, it must not have muted anything.
        self.assertNotIn(
            "/v1/me/player/volume", [c["path"] for c in self.spotify.calls]
        )


class BodySchemaTests(SpotifyApiTestCase):
    """Check 19: the PWA cannot submit a token, a code, a URI or a device id."""

    FORBIDDEN_FIELDS = (
        {"access_token": "anything"},
        {"refresh_token": FAKE_REFRESH_TOKEN},
        {"code": FAKE_AUTHORIZATION_CODE},
        {"authorization_code": FAKE_AUTHORIZATION_CODE},
        {"code_verifier": "a" * 64},
        {"redirect_uri": "http://evil.example/callback"},
        {"uri": "spotify:track:" + TRACK_ID},
        {"spotify_uri": "spotify:track:" + TRACK_ID},
        {"track_id": TRACK_ID},
        {"device_id": "dev-workstation"},
        {"url": "https://api.spotify.com/v1/me/player"},
        {"host": "evil.example"},
        {"proxy": "http://127.0.0.1:9"},
        {"client_secret": "anything"},
    )

    def test_a_transport_route_accepts_no_fields_at_all(self) -> None:
        for body in self.FORBIDDEN_FIELDS:
            with self.subTest(field=sorted(body)[0]):
                response = self.post("/api/spotify/player/pause", body)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "invalid_params")

    def test_a_forbidden_field_never_reaches_the_provider(self) -> None:
        for body in self.FORBIDDEN_FIELDS:
            self.post("/api/spotify/player/pause", body)
            self.put("/api/spotify/player/volume", dict(body, volume_percent=30))
            self.put("/api/spotify/player/device", dict(body, device_resource_id="spdev-x"))
        self.assertEqual(self.spotify.calls, [])

    def test_the_volume_route_takes_only_two_named_fields(self) -> None:
        response = self.put(
            "/api/spotify/player/volume", {"volume_percent": 30, "device_id": "dev-workstation"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("device_id", response.json()["error"]["message"])

    def test_a_missing_required_field_is_refused(self) -> None:
        self.assertEqual(self.put("/api/spotify/player/volume", {}).status_code, 422)
        self.assertEqual(self.put("/api/spotify/player/mute", {}).status_code, 422)
        self.assertEqual(self.put("/api/spotify/player/device", {}).status_code, 422)

    def test_a_non_json_content_type_is_refused(self) -> None:
        response = self.client.post(
            "/api/spotify/player/pause",
            headers=dict(self.auth, **{"Content-Type": "text/plain"}),
            content=b"pause",
        )
        self.assertEqual(response.status_code, 415)

    def test_a_form_encoded_body_is_refused(self) -> None:
        response = self.client.put(
            "/api/spotify/player/volume",
            headers=self.auth,
            data={"volume_percent": "30"},
        )
        self.assertEqual(response.status_code, 415)

    def test_an_oversized_body_is_refused(self) -> None:
        response = self.client.post(
            "/api/spotify/player/pause",
            headers=dict(self.auth, **{"Content-Type": "application/json"}),
            content=json.dumps({"padding": "x" * 8192}).encode("utf-8"),
        )
        self.assertEqual(response.status_code, 413)

    def test_a_non_object_body_is_refused(self) -> None:
        for payload in ("[]", '"a string"', "42", "null"):
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/api/spotify/player/pause",
                    headers=dict(self.auth, **{"Content-Type": "application/json"}),
                    content=payload.encode("utf-8"),
                )
                self.assertEqual(response.status_code, 400)

    def test_an_unknown_player_operation_is_a_404_and_not_an_action(self) -> None:
        response = self.post("/api/spotify/player/shuffle")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.spotify.calls, [])

    def test_a_play_flag_must_be_a_boolean(self) -> None:
        response = self.put(
            "/api/spotify/player/device", {"device_resource_id": "spdev-x", "play": "yes"}
        )
        self.assertEqual(response.status_code, 422)

    def test_a_volume_out_of_range_is_refused_with_422(self) -> None:
        for value in (-1, 101, "50", None, [30]):
            with self.subTest(value=repr(value)):
                response = self.put("/api/spotify/player/volume", {"volume_percent": value})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.json()["error"]["code"], "spotify_volume_invalid"
                )


class DeviceAuthorityTests(SpotifyApiTestCase):
    """Checks 23, 24, 25: the client's only device vocabulary is the handle."""

    def test_the_response_carries_no_provider_device_id(self) -> None:
        payload = self.get("/api/spotify/playback").json()
        blob = json.dumps(payload)
        self.assertNotIn("dev-workstation", blob)
        for entry in payload["devices"]:
            self.assertNotIn("id", entry)
            self.assertNotIn("provider_device_id", entry)

    def test_a_raw_provider_device_id_is_not_accepted_as_a_handle(self) -> None:
        response = self.put(
            "/api/spotify/player/device", {"device_resource_id": "dev-workstation"}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "spotify_device_unknown")

    def test_a_stale_handle_is_refused_after_the_device_list_changes(self) -> None:
        handle = self.get("/api/spotify/playback").json()["devices"][0]["resource_id"]
        self.spotify.devices = [device(device_id="dev-other", name="Phone")]
        response = self.put("/api/spotify/player/device", {"device_resource_id": handle})
        self.assertEqual(response.status_code, 404)

    def test_a_restricted_device_refuses_with_its_own_code(self) -> None:
        self.spotify.devices = [device(is_restricted=True, supports_volume=False)]
        handle = self.get("/api/spotify/playback").json()["devices"][0]["resource_id"]
        response = self.put(
            "/api/spotify/player/volume", {"volume_percent": 30, "device_resource_id": handle}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "spotify_device_restricted")


class ResultPlaybackRouteTests(SpotifyApiTestCase):
    """Checks 30–34: only a verified Spotify track, from the server's session."""

    def test_playing_a_verified_track_result_works(self) -> None:
        session = self.seed_search()
        response = self.post(
            f"/api/media/searches/{session.search_id}/results/mres-one/spotify/play"
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["outcome"], "applied")
        self.assertEqual(payload["observed"]["track_id"], TRACK_ID)

    def test_the_client_never_sends_a_uri_or_a_track_id(self) -> None:
        session = self.seed_search()
        path = f"/api/media/searches/{session.search_id}/results/mres-one/spotify/play"
        for body in ({"uri": "spotify:track:" + TRACK_ID}, {"track_id": TRACK_ID}):
            with self.subTest(field=sorted(body)[0]):
                self.assertEqual(self.post(path, body).status_code, 422)
        self.assertEqual(
            [c for c in self.spotify.calls if c["path"] == "/v1/me/player/play"], []
        )

    def test_a_client_supplied_uri_cannot_reach_the_provider(self) -> None:
        """Check 31: not even one that names a real track."""
        session = self.seed_search()
        path = f"/api/media/searches/{session.search_id}/results/mres-one/spotify/play"
        self.post(path, {"uris": ["spotify:track:0000000000000000000000"]})
        self.assertEqual(
            [c for c in self.spotify.calls if c["path"] == "/v1/me/player/play"], []
        )
        # The server rebuilds the URI itself, from the item it remembered.
        self.assertEqual(self.post(path).status_code, 200)
        call = [c for c in self.spotify.calls if c["path"] == "/v1/me/player/play"][-1]
        self.assertEqual(
            json.loads(call["body"].decode("utf-8"))["uris"], ["spotify:track:" + TRACK_ID]
        )

    def test_an_expired_search_session_cannot_play_or_queue(self) -> None:
        """Check 33, and it must not surface as an internal error."""
        for verb in ("play", "queue"):
            with self.subTest(verb=verb):
                response = self.post(
                    f"/api/media/searches/msrch-gone/results/mres-one/spotify/{verb}"
                )
                self.assertIn(response.status_code, (404, 409))
                self.assertNotEqual(response.status_code, 500)
                self.assertIn(
                    response.json()["error"]["code"],
                    ("media_search_not_found", "media_search_expired"),
                )
        self.assertEqual(
            [c for c in self.spotify.calls if c["path"].startswith("/v1/me/player/play")], []
        )

    def test_a_youtube_result_cannot_be_played_through_spotify(self) -> None:
        """Check 34."""
        session = self.seed_search(provider_id="youtube", item_type="video")
        response = self.post(
            f"/api/media/searches/{session.search_id}/results/mres-one/spotify/play"
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "media_result_not_found")
        self.assertEqual(self.spotify.calls, [])

    def test_a_non_track_spotify_result_is_refused(self) -> None:
        session = self.seed_search(item_type="album")
        response = self.post(
            f"/api/media/searches/{session.search_id}/results/mres-one/spotify/play"
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "spotify_result_not_playable")

    def test_queueing_reports_acceptance_and_not_playback(self) -> None:
        session = self.seed_search()
        payload = self.post(
            f"/api/media/searches/{session.search_id}/results/mres-one/spotify/queue"
        ).json()
        self.assertEqual(payload["outcome"], "accepted_by_provider")
        self.assertIn("has not changed", payload["message"])


class ResponseBoundsTests(SpotifyApiTestCase):
    """Checks 27, 28, 29: bounded, never raw, and never in a log."""

    def test_the_playback_response_is_the_versioned_bounded_shape(self) -> None:
        payload = self.get("/api/spotify/playback").json()
        self.assertEqual(payload["version"], 1)
        self.assertIn("authorization", payload)
        self.assertEqual(
            set(payload["authorization"]), {"pending", "expires_in_seconds", "last_outcome"}
        )

    def test_no_response_carries_a_token_or_a_secret(self) -> None:
        session = self.seed_search()
        bodies = [
            self.get("/api/spotify/playback").text,
            self.post("/api/spotify/player/pause").text,
            self.put("/api/spotify/player/volume", {"volume_percent": 30}).text,
            self.post(
                f"/api/media/searches/{session.search_id}/results/mres-one/spotify/play"
            ).text,
            self.post("/api/spotify/disconnect").text,
        ]
        for body in bodies:
            for secret in ALL_FAKE_OAUTH_SECRETS + ALL_FAKE_CREDENTIALS:
                self.assertNotIn(secret, body)

    def test_the_client_secret_never_reaches_the_browser(self) -> None:
        """Check 10: PKCE needs no secret, so none is anywhere near this path."""
        from ._mediasearch_doubles import FAKE_SPOTIFY_CLIENT_SECRET

        for path in ("/api/spotify/playback", "/api/media/providers", "/api/status"):
            with self.subTest(path=path):
                self.assertNotIn(FAKE_SPOTIFY_CLIENT_SECRET, self.get(path).text)

    def test_the_provider_object_is_never_forwarded(self) -> None:
        body = self.get("/api/spotify/playback").text
        for leaked in ("external_urls", "available_markets", "preview_url", "open.spotify.com"):
            self.assertNotIn(leaked, body)

    def test_the_audit_record_carries_no_track_title(self) -> None:
        """Check 29: playback is personal activity, so the audit is operational."""
        self.post("/api/spotify/player/pause")
        session = self.seed_search()
        self.post(f"/api/media/searches/{session.search_id}/results/mres-one/spotify/play")

        actions = self.get("/api/actions").json()["actions"]
        recorded = [a for a in actions if a["action"].startswith("spotify_")]
        self.assertTrue(recorded, "the write path should be audited")
        blob = json.dumps(actions, ensure_ascii=False)
        for personal in ("Gönül", "Ertaş", "Test Listener", TRACK_ID, "dev-workstation",
                         "spotify:track:"):
            self.assertNotIn(personal, blob)
        for record in recorded:
            self.assertEqual(record["params"], {})

    def test_the_track_title_does_not_reach_the_daemon_log(self) -> None:
        """It is allowed in the authenticated PWA, and nowhere else."""
        import contextlib

        captured_out, captured_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
            self.get("/api/spotify/playback")
            self.post("/api/spotify/player/next")
        written = captured_out.getvalue() + captured_err.getvalue()
        for personal in ("Gönül", "Ertaş", "Test Listener"):
            self.assertNotIn(personal, written)

    def test_the_title_is_present_for_the_authenticated_client(self) -> None:
        payload = self.get("/api/spotify/playback").json()
        self.assertEqual(payload["now_playing"]["title"], "Gönül Dağı")


class DisconnectedRouteTests(SpotifyApiTestCase):
    connected = False

    def test_reading_playback_reports_disconnected_without_calling_spotify(self) -> None:
        payload = self.get("/api/spotify/playback").json()
        self.assertEqual(payload["connection"]["status"], "disconnected")
        self.assertEqual(self.spotify.calls, [])

    def test_every_action_is_refused_with_409(self) -> None:
        for method, path, body in (
            ("POST", "/api/spotify/player/pause", {}),
            ("PUT", "/api/spotify/player/volume", {"volume_percent": 30}),
            ("PUT", "/api/spotify/player/mute", {"muted": True}),
        ):
            with self.subTest(path=path):
                response = self.client.request(method, path, headers=self.auth, json=body)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["error"]["code"], "spotify_not_connected")

    def test_disconnect_on_a_disconnected_host_is_honest(self) -> None:
        payload = self.post("/api/spotify/disconnect").json()
        self.assertEqual(payload["outcome"], "not_applied")
        self.assertIn("no Spotify account was connected", payload["message"])


class AuthorizationRouteTests(SpotifyApiTestCase):
    connected = False

    def test_the_start_route_returns_no_authorization_url(self) -> None:
        """The phone cannot complete a loopback callback, so it is not offered one."""
        response = self.post("/api/spotify/authorize")
        self.addCleanup(lambda: self.client.delete("/api/spotify/authorize", headers=self.auth))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("authorize_url", payload)
        self.assertNotIn("url", payload)
        self.assertNotIn("state", payload)
        self.assertNotIn("code_verifier", payload)
        self.assertIn("Opera", payload["message"])
        self.assertIn("workstation", payload["message"])

    def test_a_pending_attempt_can_be_cancelled(self) -> None:
        self.post("/api/spotify/authorize")
        response = self.client.delete("/api/spotify/authorize", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["authorization"]["pending"])

    def test_the_status_carries_no_secret(self) -> None:
        self.post("/api/spotify/authorize")
        self.addCleanup(lambda: self.client.delete("/api/spotify/authorize", headers=self.auth))
        body = self.get("/api/spotify/playback").text
        self.assertNotIn("code_challenge", body)
        self.assertNotIn("code_verifier", body)
        self.assertNotIn("accounts.spotify.com", body)

    def test_the_start_route_accepts_no_fields(self) -> None:
        response = self.post("/api/spotify/authorize", {"redirect_uri": "http://evil/cb"})
        self.assertEqual(response.status_code, 422)


class NoShellTests(unittest.TestCase):
    """The Spotify package runs no process and interpolates nothing."""

    def test_the_package_uses_no_subprocess_and_no_shell(self) -> None:
        import pathlib

        import cofferdam.workstation.spotifyplayer as package

        root = pathlib.Path(package.__file__).parent
        for path in sorted(root.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn("import subprocess", source)
                self.assertNotIn("os.system", source)
                self.assertNotIn("shell=True", source)
                self.assertNotIn("os.popen", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
