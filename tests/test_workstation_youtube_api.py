"""The authenticated YouTube player routes (M2E API and security).

What these assert is the *shape of the surface*, not the player logic — that is
covered against the real service in ``tests/test_youtube_player.py``. Here the
questions are the ones only a client can ask:

* is every route authenticated, including the read;
* does any ``GET`` change anything;
* is the accepted vocabulary genuinely closed — a body carrying ``video_id``,
  ``url``, ``command`` or ``script`` must be *refused*, not filtered;
* does an unsupported content type, an oversized body, or a malformed one fail
  before anything runs.
"""

from __future__ import annotations

import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the extras are absent
    TestClient = None

from cofferdam.workstation.mediasearch.sessions import SearchSessionStore
from cofferdam.workstation.youtubeplayer.models import MAX_QUEUE_ITEMS

from ._youtubeplayer_doubles import (
    OTHER_VIDEO_ID,
    VIDEO_ID,
    FakeAdapter,
    ImmediateLauncher,
    build_service,
    spotify_session,
    youtube_session,
)

TOKEN = "test-device-token-not-a-real-credential"


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class YouTubeApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.mediasearch.service import MediaSearchService
        from cofferdam.workstation.service import create_app

        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        config = load_config(Path(self._home.name))

        self.service = build_service(ImmediateLauncher)
        self.addCleanup(self.service.launcher.player.stop)

        media_search = MediaSearchService(config)
        self.store = media_search.sessions

        self.app = create_app(
            config=config,
            token=TOKEN,
            adapter=FakeAdapter(),
            media_search=media_search,
            youtube_player=self.service,
        )
        self.client = TestClient(self.app)
        self.auth = {"Authorization": "Bearer " + TOKEN}

    def session(self, video_ids=(VIDEO_ID,)):
        return youtube_session(self.store, video_ids)

    def play_path(self, session, result_id="r0", verb="play"):
        return (
            "/api/media/searches/"
            + session.search_id
            + "/results/"
            + result_id
            + "/youtube/"
            + verb
        )


class Authentication(YouTubeApiTestCase):
    ROUTES = (
        ("GET", "/api/youtube/player", None),
        ("GET", "/api/youtube/activity", None),
        ("POST", "/api/youtube/player/open", {}),
        ("POST", "/api/youtube/player/pause", {}),
        ("PUT", "/api/youtube/player/volume", {"volume_percent": 50}),
        ("PUT", "/api/youtube/player/mute", {"muted": True}),
        ("DELETE", "/api/youtube/player/queue", None),
        ("DELETE", "/api/youtube/player/queue/ytq-x", None),
    )

    def test_every_route_requires_the_device_token(self):
        for method, path, body in self.ROUTES:
            response = self.client.request(method, path, json=body)
            self.assertEqual(response.status_code, 401, method + " " + path)

    def test_result_routes_require_the_device_token(self):
        session = self.session()
        for verb in ("play", "queue"):
            response = self.client.post(self.play_path(session, verb=verb), json={})
            self.assertEqual(response.status_code, 401, verb)

    def test_a_wrong_token_is_refused(self):
        response = self.client.get(
            "/api/youtube/player", headers={"Authorization": "Bearer wrong"}
        )
        self.assertEqual(response.status_code, 401)


class ReadsDoNotMutate(YouTubeApiTestCase):
    def test_reading_state_opens_no_player(self):
        response = self.client.get("/api/youtube/player", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.launcher.calls, 0, "a GET opened a player")

    def test_reading_state_is_authenticated_and_bounded(self):
        payload = self.client.get("/api/youtube/player", headers=self.auth).json()
        self.assertEqual(payload["version"], 1)
        self.assertIn("connection", payload)
        self.assertIn("queue", payload)
        self.assertEqual(payload["queue"]["max_length"], MAX_QUEUE_ITEMS)
        self.assertEqual(payload["volume"]["scope"], "youtube_player_only")

    def test_activity_is_free_and_carries_no_content(self):
        payload = self.client.get("/api/youtube/activity", headers=self.auth).json()
        self.assertEqual(set(payload) <= {
            "active", "operation", "phase", "label", "correlation_id", "elapsed_ms"
        }, True)


class ClosedVocabulary(YouTubeApiTestCase):
    #: Everything a client might try to smuggle a destination through. Each one
    #: must be refused by name rather than silently ignored, so that a client
    #: attempting it is told the attempt was wrong.
    HOSTILE_FIELDS = (
        {"video_id": VIDEO_ID},
        {"url": "https://www.youtube.com/watch?v=" + VIDEO_ID},
        {"watch_url": "https://youtu.be/" + VIDEO_ID},
        {"iframe_src": "https://www.youtube.com/embed/" + VIDEO_ID},
        {"command": "playVideo"},
        {"script": "alert(1)"},
        {"javascript": "alert(1)"},
        {"tab_id": 7},
        {"executable": "/bin/sh"},
        {"token": "anything"},
        {"callback_url": "http://example.com"},
        {"instance_id": "guessed"},
    )

    def test_play_refuses_every_extra_field(self):
        session = self.session()
        for body in self.HOSTILE_FIELDS:
            response = self.client.post(self.play_path(session), json=body, headers=self.auth)
            self.assertEqual(response.status_code, 422, str(body))
            self.assertIn("unexpected field", response.json()["error"]["message"])
        self.assertEqual(self.service.launcher.calls, 0)

    def test_queue_refuses_every_extra_field(self):
        session = self.session()
        for body in self.HOSTILE_FIELDS:
            response = self.client.post(
                self.play_path(session, verb="queue"), json=body, headers=self.auth
            )
            self.assertEqual(response.status_code, 422, str(body))

    def test_transport_refuses_every_extra_field(self):
        for body in self.HOSTILE_FIELDS:
            response = self.client.post(
                "/api/youtube/player/pause", json=body, headers=self.auth
            )
            self.assertEqual(response.status_code, 422, str(body))

    def test_volume_accepts_only_volume_percent(self):
        for body in self.HOSTILE_FIELDS:
            response = self.client.put(
                "/api/youtube/player/volume",
                json=dict(body, volume_percent=50),
                headers=self.auth,
            )
            self.assertEqual(response.status_code, 422, str(body))

    def test_mute_accepts_only_muted(self):
        for body in self.HOSTILE_FIELDS:
            response = self.client.put(
                "/api/youtube/player/mute", json=dict(body, muted=True), headers=self.auth
            )
            self.assertEqual(response.status_code, 422, str(body))

    def test_an_unknown_transport_operation_is_404(self):
        """``open`` is deliberately absent here — it is a real route of its own."""
        for operation in ("stop", "seek", "eval", "screenshot", "load", "queue"):
            response = self.client.post(
                "/api/youtube/player/" + operation, json={}, headers=self.auth
            )
            self.assertEqual(response.status_code, 404, operation)

    def test_open_is_not_shadowed_by_the_transport_route(self):
        """``/open`` is its own route and must not be read as an operation name."""
        response = self.client.post("/api/youtube/player/open", json={}, headers=self.auth)
        self.assertNotEqual(response.status_code, 404)


class BodyDiscipline(YouTubeApiTestCase):
    def test_a_non_json_content_type_is_refused(self):
        response = self.client.put(
            "/api/youtube/player/volume",
            content="volume_percent=50",
            headers={**self.auth, "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(response.status_code, 415)

    def test_an_oversized_body_is_refused(self):
        response = self.client.put(
            "/api/youtube/player/volume",
            content=b'{"volume_percent":' + b"0" * 5000 + b"}",
            headers={**self.auth, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_malformed_json_is_refused(self):
        response = self.client.put(
            "/api/youtube/player/volume",
            content=b"not json",
            headers={**self.auth, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)

    def test_a_missing_required_field_is_refused(self):
        for path in ("/api/youtube/player/volume", "/api/youtube/player/mute"):
            response = self.client.put(path, json={}, headers=self.auth)
            self.assertEqual(response.status_code, 422, path)


class Behaviour(YouTubeApiTestCase):
    def test_play_now_through_the_route_opens_one_player(self):
        session = self.session((VIDEO_ID, OTHER_VIDEO_ID))
        first = self.client.post(self.play_path(session, "r0"), json={}, headers=self.auth)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["outcome"], "applied")

        second = self.client.post(self.play_path(session, "r1"), json={}, headers=self.auth)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.service.launcher.calls, 1, "a second tab was opened")

    def test_volume_out_of_range_is_422_and_not_clamped(self):
        session = self.session()
        self.client.post(self.play_path(session), json={}, headers=self.auth)
        for level in (-1, 101, 1000):
            response = self.client.put(
                "/api/youtube/player/volume",
                json={"volume_percent": level},
                headers=self.auth,
            )
            self.assertEqual(response.status_code, 422, str(level))
            self.assertEqual(response.json()["error"]["code"], "youtube_volume_invalid")

    def test_a_cross_provider_result_fails_closed(self):
        session = spotify_session(self.store)
        response = self.client.post(
            "/api/media/searches/" + session.search_id + "/results/r0/youtube/play",
            json={},
            headers=self.auth,
        )
        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(self.service.launcher.calls, 0)

    def test_an_expired_search_is_a_recognisable_refusal(self):
        session = self.session()
        import time

        self.store._sessions[session.search_id] = session.__class__(
            **{**session.__dict__, "expires_at": time.time() - 1}
        )
        response = self.client.post(self.play_path(session), json={}, headers=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "media_search_expired")

    def test_transport_without_a_player_is_a_clean_409(self):
        response = self.client.post("/api/youtube/player/pause", json={}, headers=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "youtube_player_not_connected")

    def test_queueing_does_not_open_a_player(self):
        session = self.session()
        response = self.client.post(
            self.play_path(session, verb="queue"), json={}, headers=self.auth
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["outcome"], "queued")
        self.assertEqual(self.service.launcher.calls, 0, "queueing opened a tab")

    def test_the_response_never_carries_a_video_id_or_url(self):
        session = self.session()
        response = self.client.post(self.play_path(session), json={}, headers=self.auth)
        body = response.text
        self.assertNotIn(VIDEO_ID, body)
        self.assertNotIn("youtube.com", body)
        self.assertNotIn("127.0.0.1", body)

    def test_clearing_and_removing_queue_items(self):
        session = self.session((VIDEO_ID, OTHER_VIDEO_ID))
        queued = self.client.post(
            self.play_path(session, "r1", verb="queue"), json={}, headers=self.auth
        ).json()
        handle = queued["queue_item_id"]

        removed = self.client.delete(
            "/api/youtube/player/queue/" + handle, headers=self.auth
        )
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.json()["player"]["queue"]["length"], 0)

        again = self.client.delete("/api/youtube/player/queue/" + handle, headers=self.auth)
        self.assertEqual(again.status_code, 404)

        cleared = self.client.delete("/api/youtube/player/queue", headers=self.auth)
        self.assertEqual(cleared.status_code, 200)

    def test_the_audit_record_carries_no_content(self):
        session = self.session()
        self.client.post(self.play_path(session), json={}, headers=self.auth)
        actions = self.client.get("/api/actions", headers=self.auth).json()["actions"]
        youtube = [a for a in actions if a["action"].startswith("youtube_")]
        self.assertTrue(youtube, "the action was not audited")
        blob = str(youtube)
        for forbidden in (VIDEO_ID, "A video called something", "A channel", "youtube.com"):
            self.assertNotIn(forbidden, blob)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
