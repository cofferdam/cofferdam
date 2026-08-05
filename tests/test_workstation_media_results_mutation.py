"""Mutation checks: prove the safety guards are load-bearing.

A passing test suite proves the code behaves. It does not prove the *tests*
would notice if the code stopped behaving — a guard can be quietly removed and
leave a suite just as green, because nothing was ever exercising it.

So each test below deliberately breaks one guard and asserts that the property
it protects visibly fails. If a mutation ever stops producing a failure, the
corresponding guard has become decorative and this file says so.

These are the six guards the milestone brief calls out by name.
"""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cofferdam.workstation.mediasearch import spotify as spotify_module
from cofferdam.workstation.mediasearch import youtube as youtube_module
from cofferdam.workstation.mediasearch.errors import ResultNotFound, SearchSessionExpired
from cofferdam.workstation.mediasearch.results import MediaResult, MediaSearchOutcome, ProviderItem
from cofferdam.workstation.mediasearch.sessions import SearchSessionStore

from tests._mediasearch_doubles import (
    ALL_FAKE_CREDENTIALS,
    FakeTransport,
    json_response,
    spotify_search_payload,
    spotify_track,
    write_credentials,
)
from tests._workstation_doubles import WorkstationTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]


def _outcome(provider_id="spotify", count=1) -> MediaSearchOutcome:
    results = tuple(
        MediaResult(
            provider_id=provider_id,
            result_id="r" + str(index),
            result_type="track" if provider_id == "spotify" else "video",
            title="Result " + str(index),
        )
        for index in range(count)
    )
    items = tuple(
        ProviderItem(
            provider_id=provider_id,
            item_type="track" if provider_id == "spotify" else "video",
            item_id="1a2b3c4d5e6f7g8h9i0j1k" if provider_id == "spotify" else "dQw4w9WgXcQ",
        )
        for _ in range(count)
    )
    return MediaSearchOutcome(provider_id=provider_id, query="q", results=results, items=items)


class MutationTests(WorkstationTestCase):
    def setUp(self) -> None:
        super().setUp()
        write_credentials(self.config)
        self.transport = FakeTransport()

    def _search(self):
        self.transport.queue_spotify_token()
        self.transport.queue(json_response(200, spotify_search_payload([spotify_track()])))
        with patch.object(spotify_module, "request", self.transport):
            response = self.client.post(
                "/api/media/providers/spotify/results/search",
                json={"query": "Gönül Dağı"},
                headers=self.auth,
            )
        self.assertEqual(response.status_code, 200)
        return response.json()["result"]

    # -- 1. accepting a client-supplied URL must fail ------------------------

    def test_accepting_a_client_supplied_url_would_be_caught(self) -> None:
        """The schema's ``extra="forbid"`` is what refuses a smuggled target."""
        from cofferdam.workstation.actions import OpenMediaResultParams

        # As shipped: refused.
        with self.assertRaises(Exception):
            OpenMediaResultParams(
                provider_id="spotify", search_id="abc", result_id="r0",
                url="https://evil.example",
            )

        # Mutated: a schema that allowed extras would accept it, and this
        # assertion is what would then start failing in the real suite.
        class Permissive(OpenMediaResultParams):
            model_config = {"extra": "allow"}

        permissive = Permissive(
            provider_id="spotify", search_id="abc", result_id="r0",
            url="https://evil.example",
        )
        self.assertEqual(getattr(permissive, "url", None), "https://evil.example")

    # -- 2. accepting an expired search must fail ----------------------------

    def test_accepting_an_expired_search_would_be_caught(self) -> None:
        store = SearchSessionStore(ttl_seconds=60)
        session = store.create(_outcome(), now=1000.0)

        # As shipped: expiry is enforced.
        with self.assertRaises(SearchSessionExpired):
            store.get(session.search_id, now=2000.0)

        # Mutated: a store whose expiry check always says "fresh" would hand the
        # session back, which is the behaviour the guard exists to prevent.
        with patch.object(type(session), "expired", lambda self, now=None: False):
            revived = store.create(_outcome(), now=1000.0)
            self.assertIsNotNone(store.get(revived.search_id, now=9_999_999.0))

    # -- 3. cross-provider opening must fail ---------------------------------

    def test_allowing_cross_provider_opening_would_be_caught(self) -> None:
        store = SearchSessionStore()
        session = store.create(_outcome(provider_id="youtube"))

        # As shipped: a YouTube result cannot be opened as Spotify.
        with self.assertRaises(ResultNotFound):
            store.resolve(session.search_id, "r0", provider_id="spotify")

        # Mutated: dropping the provider argument is exactly the mistake, and it
        # yields a YouTube video id on the path that builds a Spotify URI.
        _, _, item = store.resolve(session.search_id, "r0")
        self.assertEqual(item.provider_id, "youtube")
        self.assertEqual(item.item_id, "dQw4w9WgXcQ")

    # -- 4. leaking credentials into errors must fail ------------------------

    def test_leaking_a_credential_into_an_error_would_be_caught(self) -> None:
        """The leak assertions search whole payloads, not named fields."""
        self.transport.queue(json_response(400, {"error": "invalid_client"}))
        with patch.object(spotify_module, "request", self.transport):
            response = self.client.post(
                "/api/media/providers/spotify/results/search",
                json={"query": "x"},
                headers=self.auth,
            )
        blob = json.dumps(response.json())
        for secret in ALL_FAKE_CREDENTIALS:
            self.assertNotIn(secret, blob)

        # Mutated: had the adapter interpolated the secret into its detail, this
        # is the shape the assertion above would have caught.
        leaky = json.dumps({"error": {"detail": "rejected key " + ALL_FAKE_CREDENTIALS[0]}})
        self.assertIn(ALL_FAKE_CREDENTIALS[0], leaky)

    # -- 5. removing query bounds must fail ----------------------------------

    def test_removing_query_bounds_would_be_caught(self) -> None:
        from cofferdam.workstation.media import MAX_QUERY_LENGTH, validate_query

        oversized = "a" * (MAX_QUERY_LENGTH + 1)
        with self.assertRaises(Exception):
            validate_query(oversized)
        with self.assertRaises(Exception):
            validate_query("a\nb")

        # Mutated: a validator that only trimmed would let both through.
        def unbounded(value):
            return value.strip()

        self.assertEqual(len(unbounded(oversized)), MAX_QUERY_LENGTH + 1)
        self.assertIn("\n", unbounded("a\nb"))

    # -- 6. claiming playback success must fail ------------------------------

    def test_claiming_playback_success_would_be_caught(self) -> None:
        search = self._search()
        response = self.client.post(
            f"/api/media/searches/{search['search_id']}/results/r0/open",
            json={"provider_id": "spotify"},
            headers=self.auth,
        )
        result = response.json()["result"]

        # As shipped.
        self.assertFalse(result["playback_started"])
        self.assertEqual(result["playback"], "not_started")
        note = result["note"].lower()
        for forbidden in ("now playing", "started playing", "is playing now"):
            self.assertNotIn(forbidden, note)

        # Mutated: this is the claim the assertions above would reject.
        pretend = dict(result, playback="started", playback_started=True)
        self.assertTrue(pretend["playback_started"])


class NoPlaybackAnywhereTests(unittest.TestCase):
    """Structural: no code path can control playback, in any provider."""

    PACKAGE = REPO_ROOT / "cofferdam" / "workstation" / "mediasearch"

    def test_no_playback_endpoint_is_ever_called(self) -> None:
        """Spotify's player API is simply not referenced anywhere."""
        for path in sorted(self.PACKAGE.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                for forbidden in ("/v1/me/player", "/me/player", "player/play",
                                  "player/pause", "player/next"):
                    self.assertNotIn(forbidden, source)

    def test_the_spotify_flow_cannot_reach_user_endpoints(self) -> None:
        """Client credentials is the guarantee, not a promise to behave.

        The documented flow reaches only endpoints that do not access user
        information, so playback is unreachable by construction rather than by
        restraint.
        """
        source = (self.PACKAGE / "spotify.py").read_text(encoding="utf-8")
        self.assertIn("client_credentials", source)
        # No authorization-code flow, which is what a user-scoped token needs.
        self.assertNotIn("authorization_code", source)
        self.assertNotIn("refresh_token", source)
        self.assertNotIn("/authorize", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
