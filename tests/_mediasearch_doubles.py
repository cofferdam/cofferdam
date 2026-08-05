"""Test doubles for official-provider search.

The adapters are exercised against a **fake transport** rather than a fake
adapter. That choice matters: patching ``transport.request`` leaves the real
normalization, the real validation, the real error classification and the real
URI/URL construction under test, and only replaces the socket. A double at the
adapter level would have tested nothing that ships.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from cofferdam.workstation.mediasearch.transport import Response

# A credential that is obviously fake, obviously not a real key, and distinctive
# enough that a leak test can grep the whole world for it.
FAKE_SPOTIFY_CLIENT_ID = "test-client-id-not-a-real-credential"
FAKE_SPOTIFY_CLIENT_SECRET = "test-client-secret-not-a-real-credential"
FAKE_YOUTUBE_API_KEY = "test-api-key-not-a-real-credential"

# Every fake credential value, for the "nothing leaked" assertions.
ALL_FAKE_CREDENTIALS = (
    FAKE_SPOTIFY_CLIENT_ID,
    FAKE_SPOTIFY_CLIENT_SECRET,
    FAKE_YOUTUBE_API_KEY,
)


def write_credentials(config, *, spotify: bool = True, youtube: bool = True) -> None:
    """Write a credential file with fake values, the way a user would."""
    document: Dict[str, Dict[str, str]] = {}
    if spotify:
        document["spotify"] = {
            "client_id": FAKE_SPOTIFY_CLIENT_ID,
            "client_secret": FAKE_SPOTIFY_CLIENT_SECRET,
        }
    if youtube:
        document["youtube"] = {"api_key": FAKE_YOUTUBE_API_KEY}
    path = config.secrets_dir / "media_providers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o600)


def json_response(status: int, payload, headers: Optional[Dict[str, str]] = None) -> Response:
    return Response(
        status=status,
        body=json.dumps(payload).encode("utf-8"),
        headers=headers or {},
    )


class FakeTransport:
    """Scripted replies, plus a record of every request that was made.

    ``calls`` is what the request-shape assertions read: it captures the host,
    path, query and headers actually sent, so a test can prove that (for
    example) the query the user typed reached the provider as a *value* and that
    no credential reached a place it should not.
    """

    def __init__(self) -> None:
        self.calls: List[dict] = []
        self._replies: List[object] = []

    def queue(self, reply) -> "FakeTransport":
        """Queue a :class:`Response` or an exception to raise."""
        self._replies.append(reply)
        return self

    def queue_spotify_token(self, *, status: int = 200) -> "FakeTransport":
        return self.queue(
            json_response(status, {"access_token": "fake-bearer-token", "expires_in": 3600})
        )

    def __call__(self, host, path, *, method="GET", query=None, headers=None, body=None):
        self.calls.append(
            {
                "host": host,
                "path": path,
                "method": method,
                "query": dict(query or {}),
                "headers": dict(headers or {}),
                "body": body,
            }
        )
        if not self._replies:
            raise AssertionError(f"unexpected provider call: {method} {host}{path}")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    # -- convenience --------------------------------------------------------

    @property
    def hosts(self):
        return [call["host"] for call in self.calls]

    def last(self) -> dict:
        return self.calls[-1]


def spotify_track(
    name: str = "Gönül Dağı",
    track_id: str = "1a2b3c4d5e6f7g8h9i0j1k",
    artist: str = "Neşet Ertaş",
    album: str = "Gönül Dağı",
    duration_ms: int = 245000,
    explicit: bool = False,
) -> dict:
    return {
        "id": track_id,
        "name": name,
        "duration_ms": duration_ms,
        "explicit": explicit,
        "uri": "spotify:track:" + track_id,
        "artists": [{"name": artist}],
        "album": {"name": album, "release_date": "1998-01-01"},
    }


def spotify_search_payload(tracks) -> dict:
    return {"tracks": {"items": list(tracks), "total": len(tracks)}}


def youtube_video(
    title: str = "Cofferdam demo",
    video_id: str = "dQw4w9WgXcQ",
    channel: str = "Cofferdam",
    published: str = "2026-01-15T10:00:00Z",
    live: str = "none",
) -> dict:
    return {
        "id": {"kind": "youtube#video", "videoId": video_id},
        "snippet": {
            "title": title,
            "channelTitle": channel,
            "publishedAt": published,
            "liveBroadcastContent": live,
        },
    }


def youtube_search_payload(items) -> dict:
    return {"kind": "youtube#searchListResponse", "items": list(items)}


__all__ = [
    "ALL_FAKE_CREDENTIALS",
    "FAKE_SPOTIFY_CLIENT_ID",
    "FAKE_SPOTIFY_CLIENT_SECRET",
    "FAKE_YOUTUBE_API_KEY",
    "FakeTransport",
    "json_response",
    "spotify_search_payload",
    "spotify_track",
    "write_credentials",
    "youtube_search_payload",
    "youtube_video",
]
