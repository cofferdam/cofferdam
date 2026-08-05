"""M2B3A.1: official-provider search, result selection, and exact opening.

Four properties are under test here, and most assertions serve one of them.

**The client never names a destination.** No request schema has a field for a
URL, a URI, or a video id; the server resolves a chosen result from its own
bounded session and rebuilds the launch target from validated identifiers. The
tests assert the *absence* of that vocabulary, and that smuggling it in is
rejected rather than ignored.

**Credentials do not leave the host.** They are not in any response, any action
record, any error, any log line, or any argv. The leak tests search whole
serialized payloads for the fake credential strings rather than checking named
fields, so a future field that accidentally carried one would fail them.

**Nothing claims playback.** Opening the exact track opens it. Every success
says so.

**Absence is truthful.** No credentials means "structured results not
configured" plus the untouched M2B3A open and search-page actions — never a
broken control, and never a fabricated result.
"""

from __future__ import annotations

import ast
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cofferdam.workstation import media
from cofferdam.workstation.mediasearch import credentials as credentials_module
from cofferdam.workstation.mediasearch import spotify as spotify_module
from cofferdam.workstation.mediasearch import transport as transport_module
from cofferdam.workstation.mediasearch import youtube as youtube_module
from cofferdam.workstation.mediasearch.credentials import (
    PROVIDER_CREDENTIAL_STATUSES,
    STATUS_CONFIGURED,
    STATUS_INVALID,
    STATUS_MISSING,
    CredentialStore,
)
from cofferdam.workstation.mediasearch.errors import (
    ProviderMalformedResponse,
    ProviderRateLimited,
    ProviderRejected,
    ProviderTemporarilyUnavailable,
    ProviderUnconfigured,
    ResultNotFound,
    SearchSessionExpired,
    SearchSessionNotFound,
)
from cofferdam.workstation.mediasearch.results import (
    MAX_CREATORS,
    MAX_RESULTS,
    MAX_TITLE_LENGTH,
    MEDIA_RESULT_MODEL_VERSION,
    RESULT_TYPES,
    MediaResult,
    MediaSearchOutcome,
    ProviderItem,
    bounded_text,
)
from cofferdam.workstation.mediasearch.sessions import (
    MAX_SEARCH_SESSIONS,
    SEARCH_SESSION_TTL_SECONDS,
    SearchSessionStore,
)
from cofferdam.workstation.mediasearch.spotify import SpotifySearchAdapter, build_uri
from cofferdam.workstation.mediasearch.transport import (
    ALLOWED_HOSTS,
    MAX_RESPONSE_BYTES,
    READ_TIMEOUT_SECONDS,
    TransportError,
)
from cofferdam.workstation.mediasearch.youtube import YouTubeSearchAdapter, build_watch_url

from tests._mediasearch_doubles import (
    ALL_FAKE_CREDENTIALS,
    FAKE_SPOTIFY_CLIENT_SECRET,
    FAKE_YOUTUBE_API_KEY,
    FakeTransport,
    json_response,
    spotify_search_payload,
    spotify_track,
    write_credentials,
    youtube_search_payload,
    youtube_video,
)
from tests._workstation_doubles import WorkstationTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "cofferdam" / "workstation" / "mediasearch"

TURKISH_QUERY = "Neşet Ertaş Gönül Dağı"

# The action schemas are pydantic; everything else in this file — the adapters,
# the transport, the normalization and the session store — is stdlib-only and
# runs on a bare interpreter. That is what keeps the stdlib-only CI path a real
# check on the component that talks to the internet.
try:  # pragma: no cover - import guard
    import pydantic  # noqa: F401

    PYDANTIC_AVAILABLE = True
except Exception:  # pragma: no cover
    PYDANTIC_AVAILABLE = False

requires_schemas = unittest.skipUnless(
    PYDANTIC_AVAILABLE, "pydantic not installed: pip install -e '.[workstation]'"
)



# ---------------------------------------------------------------------------
# (12, 13) credentials never escape
# ---------------------------------------------------------------------------


class CredentialContainmentTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        from cofferdam.workstation.config import load_config

        self._tmp = tempfile.TemporaryDirectory()
        self.config = load_config(home=Path(self._tmp.name))
        self.config.ensure_dirs()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_status_is_one_word_from_the_closed_vocabulary(self) -> None:
        store = CredentialStore(self.config)
        self.assertEqual(store.status("spotify"), STATUS_MISSING)
        write_credentials(self.config)
        self.assertEqual(store.status("spotify"), STATUS_CONFIGURED)
        for provider_id in ("spotify", "youtube"):
            with self.subTest(provider=provider_id):
                self.assertIn(store.status(provider_id), PROVIDER_CREDENTIAL_STATUSES)

    def test_a_malformed_file_reports_invalid_and_never_its_contents(self) -> None:
        """(13) The JSON decoder's message quotes the file; ours must not."""
        path = self.config.secrets_dir / "media_providers.json"
        path.write_text('{"spotify": {"client_secret": "' + FAKE_SPOTIFY_CLIENT_SECRET + '"',
                        encoding="utf-8")
        store = CredentialStore(self.config)
        self.assertEqual(store.status("spotify"), STATUS_INVALID)
        # And the failure carries nothing from the file.
        try:
            store._document()
        except ValueError as exc:
            self.assertNotIn(FAKE_SPOTIFY_CLIENT_SECRET, str(exc))
        else:  # pragma: no cover
            self.fail("expected a ValueError")

    def test_credential_objects_do_not_print_their_values(self) -> None:
        """(13) A repr is exactly how a secret reaches a log."""
        write_credentials(self.config)
        store = CredentialStore(self.config)
        for provider_id in ("spotify", "youtube"):
            with self.subTest(provider=provider_id):
                loaded = store.load(provider_id)
                for rendering in (repr(loaded), str(loaded), "{}".format(loaded)):
                    for secret in ALL_FAKE_CREDENTIALS:
                        self.assertNotIn(secret, rendering)
                self.assertIn("redacted", repr(loaded))

    def test_describe_returns_status_words_only(self) -> None:
        write_credentials(self.config)
        described = CredentialStore(self.config).describe(("spotify", "youtube"))
        for value in described.values():
            self.assertIn(value, PROVIDER_CREDENTIAL_STATUSES)
        blob = json.dumps(described)
        for secret in ALL_FAKE_CREDENTIALS:
            self.assertNotIn(secret, blob)

    def test_an_oversized_or_control_bearing_credential_is_invalid(self) -> None:
        path = self.config.secrets_dir / "media_providers.json"
        for bad in ("x" * 5000, "abc\ndef", "", "   "):
            with self.subTest(value=repr(bad[:12])):
                path.write_text(
                    json.dumps({"youtube": {"api_key": bad}}), encoding="utf-8"
                )
                self.assertEqual(CredentialStore(self.config).status("youtube"), STATUS_INVALID)

    def test_a_world_readable_file_is_reported_without_naming_it(self) -> None:
        write_credentials(self.config)
        path = self.config.secrets_dir / "media_providers.json"
        path.chmod(0o644)
        note = CredentialStore(self.config).permissions_note()
        self.assertIsNotNone(note)
        self.assertNotIn(str(path), note)
        for secret in ALL_FAKE_CREDENTIALS:
            self.assertNotIn(secret, note)

    def test_the_package_never_logs(self) -> None:
        """(13) Structural: no logging call can exist to carry a credential."""
        for path in sorted(PACKAGE.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                for forbidden in ("logging.", "print(", "sys.stdout", "sys.stderr"):
                    self.assertNotIn(forbidden, source)

    def test_the_package_never_starts_a_subprocess(self) -> None:
        """(13) A credential in an argv is visible in the process table."""
        for path in sorted(PACKAGE.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                for forbidden in ("subprocess", "os.system", "os.popen", "shell=True"):
                    self.assertNotIn(forbidden, source)

    def test_the_package_imports_only_the_standard_library(self) -> None:
        """(43) So the stdlib-only CI path really exercises the network layer."""
        import sys

        for path in sorted(PACKAGE.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported.add(node.module.split(".")[0])
            with self.subTest(module=path.name):
                self.assertEqual(imported - set(sys.stdlib_module_names), set())


# ---------------------------------------------------------------------------
# (10, 11) transport bounds
# ---------------------------------------------------------------------------


class TransportBoundsTests(unittest.TestCase):
    def test_only_official_hosts_are_reachable(self) -> None:
        """(11)"""
        self.assertEqual(
            set(ALLOWED_HOSTS),
            {"accounts.spotify.com", "api.spotify.com", "www.googleapis.com"},
        )
        for host in ("evil.example", "localhost", "127.0.0.1", "169.254.169.254", ""):
            with self.subTest(host=host):
                with self.assertRaises(TransportError):
                    transport_module.request(host, "/x")

    def test_a_relative_path_is_refused(self) -> None:
        with self.assertRaises(TransportError):
            transport_module.request("api.spotify.com", "v1/search")

    def test_redirects_are_never_followed(self) -> None:
        """(11) A 3xx is a failure, not a hop to somewhere else."""
        source = (PACKAGE / "transport.py").read_text(encoding="utf-8")
        self.assertIn("300 <= status < 400", source)
        # And structurally: nothing in the package enables redirect following.
        for path in sorted(PACKAGE.rglob("*.py")):
            with self.subTest(module=path.name):
                self.assertNotIn("HTTPRedirectHandler", path.read_text(encoding="utf-8"))
                self.assertNotIn("allow_redirects", path.read_text(encoding="utf-8"))

    def test_timeouts_and_sizes_are_bounded(self) -> None:
        """(10)"""
        self.assertLessEqual(transport_module.CONNECT_TIMEOUT_SECONDS, 15)
        self.assertLessEqual(READ_TIMEOUT_SECONDS, 15)
        self.assertLessEqual(MAX_RESPONSE_BYTES, 2 * 1024 * 1024)
        self.assertEqual(transport_module.MAX_ATTEMPTS, 1)

    def test_the_transport_uses_verified_tls_only(self) -> None:
        source = (PACKAGE / "transport.py").read_text(encoding="utf-8")
        self.assertIn("HTTPSConnection", source)
        self.assertIn("CERT_REQUIRED", source)
        self.assertIn("check_hostname = True", source)
        # No plaintext connection class anywhere.
        self.assertNotIn("HTTPConnection(", source)
        self.assertNotIn("CERT_NONE", source)


# ---------------------------------------------------------------------------
# (1, 3) capability gating
# ---------------------------------------------------------------------------


class CapabilityTests(unittest.TestCase):
    def test_structured_search_requires_configuration(self) -> None:
        """(1)"""
        for provider_id in ("spotify", "youtube"):
            provider = media.get_provider(provider_id)
            with self.subTest(provider=provider_id):
                self.assertTrue(provider.offers_structured_search)
                self.assertFalse(
                    provider.capabilities()[media.CAPABILITY_STRUCTURED_SEARCH]
                )
                self.assertTrue(
                    provider.capabilities(structured_search_configured=True)[
                        media.CAPABILITY_STRUCTURED_SEARCH
                    ]
                )

    def test_streaming_providers_gain_no_structured_search(self) -> None:
        """(3) Not even when told the credentials are configured."""
        for provider_id in ("netflix", "prime-video", "tv-plus"):
            provider = media.get_provider(provider_id)
            with self.subTest(provider=provider_id):
                self.assertFalse(provider.offers_structured_search)
                self.assertIsNone(provider.structured_search_key)
                capabilities = provider.capabilities(structured_search_configured=True)
                self.assertFalse(capabilities[media.CAPABILITY_STRUCTURED_SEARCH])
                self.assertFalse(capabilities[media.CAPABILITY_OPEN_SELECTED_RESULT])
                self.assertFalse(capabilities[media.CAPABILITY_OPEN_FIRST_RESULT])

    def test_playback_control_is_false_for_every_provider(self) -> None:
        """(29, 32) Explicitly false, not merely absent."""
        for provider in media.MEDIA_PROVIDERS:
            with self.subTest(provider=provider.id):
                capabilities = provider.capabilities(structured_search_configured=True)
                self.assertIn(media.CAPABILITY_PLAYBACK_CONTROL, capabilities)
                self.assertFalse(capabilities[media.CAPABILITY_PLAYBACK_CONTROL])

    def test_auto_open_first_is_disabled_by_default(self) -> None:
        """(35)"""
        for provider in media.MEDIA_PROVIDERS:
            with self.subTest(provider=provider.id):
                self.assertFalse(
                    provider.capabilities(structured_search_configured=True)[
                        media.CAPABILITY_AUTO_OPEN_FIRST
                    ]
                )

    def test_the_m2b3a_open_and_search_page_capabilities_are_unchanged(self) -> None:
        """(2) They do not depend on credentials at all."""
        for provider in media.MEDIA_PROVIDERS:
            with self.subTest(provider=provider.id):
                capabilities = provider.capabilities()
                self.assertTrue(capabilities[media.CAPABILITY_OPEN_HOME])
                self.assertEqual(
                    capabilities[media.CAPABILITY_OPEN_SEARCH_PAGE],
                    provider.supports(media.MEDIA_ACTION_SEARCH),
                )


# ---------------------------------------------------------------------------
# (14, 15, 16, 17, 18) normalization
# ---------------------------------------------------------------------------


class NormalizationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        from cofferdam.workstation.config import load_config

        self._tmp = tempfile.TemporaryDirectory()
        self.config = load_config(home=Path(self._tmp.name))
        self.config.ensure_dirs()
        write_credentials(self.config)
        self.store = CredentialStore(self.config)
        self.transport = FakeTransport()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def spotify(self) -> SpotifySearchAdapter:
        return SpotifySearchAdapter(self.store)

    def youtube(self) -> YouTubeSearchAdapter:
        return YouTubeSearchAdapter(self.store)


class SpotifyNormalizationTests(NormalizationTestCase):
    def search(self, payload, **kwargs):
        self.transport.queue_spotify_token().queue(json_response(200, payload))
        with patch.object(spotify_module, "request", self.transport):
            return self.spotify().search(TURKISH_QUERY, **kwargs)

    def test_a_track_normalizes_to_bounded_fields(self) -> None:
        """(14)"""
        outcome = self.search(spotify_search_payload([spotify_track()]))
        self.assertEqual(len(outcome.results), 1)
        result = outcome.results[0]
        self.assertEqual(result.provider_id, "spotify")
        self.assertEqual(result.result_type, "track")
        self.assertEqual(result.title, "Gönül Dağı")
        self.assertEqual(result.creators, ("Neşet Ertaş",))
        self.assertEqual(result.subtitle, "Gönül Dağı")
        self.assertEqual(result.duration_seconds, 245)
        self.assertIs(result.explicit, False)

    def test_the_query_reaches_spotify_as_a_value(self) -> None:
        """(4) Turkish characters survive; the transport percent-encodes them."""
        self.search(spotify_search_payload([spotify_track()]))
        call = self.transport.last()
        self.assertEqual(call["host"], "api.spotify.com")
        self.assertEqual(call["path"], "/v1/search")
        self.assertEqual(call["query"]["q"], TURKISH_QUERY)
        self.assertEqual(call["query"]["limit"], str(MAX_RESULTS))

    def test_oversized_provider_fields_are_truncated_not_rejected(self) -> None:
        """(18) A long title should still be pickable, and marked as cut."""
        outcome = self.search(
            spotify_search_payload([spotify_track(name="A" * 5000)])
        )
        title = outcome.results[0].title
        self.assertLessEqual(len(title), MAX_TITLE_LENGTH)
        self.assertTrue(title.endswith("…"))

    def test_too_many_creators_are_capped(self) -> None:
        """(18)"""
        track = spotify_track()
        track["artists"] = [{"name": f"Artist {index}"} for index in range(50)]
        outcome = self.search(spotify_search_payload([track]))
        self.assertLessEqual(len(outcome.results[0].creators), MAX_CREATORS)

    def test_the_result_count_is_capped(self) -> None:
        """(17) Even when the provider ignores our limit."""
        tracks = [
            spotify_track(name=f"Track {index}", track_id=f"{index:022d}".replace("-", "0"))
            for index in range(40)
        ]
        outcome = self.search(spotify_search_payload(tracks))
        self.assertLessEqual(len(outcome.results), MAX_RESULTS)
        self.assertEqual(len(outcome.results), len(outcome.items))

    def test_an_item_without_a_valid_id_is_dropped(self) -> None:
        """An unopenable card can only disappoint."""
        good = spotify_track()
        bad = spotify_track(track_id="not-a-valid-id")
        outcome = self.search(spotify_search_payload([bad, good]))
        self.assertEqual(len(outcome.results), 1)
        self.assertEqual(outcome.items[0].item_id, good["id"])

    def test_a_result_carries_no_uri_or_url(self) -> None:
        """(19, 20) The client is never handed something openable."""
        outcome = self.search(spotify_search_payload([spotify_track()]))
        blob = json.dumps(outcome.results[0].to_dict())
        self.assertNotIn("spotify:", blob)
        self.assertNotIn("http", blob)
        self.assertNotIn("uri", blob)
        # The id Spotify gave us is not in the client payload either.
        self.assertNotIn("1a2b3c4d5e6f7g8h9i0j1k", blob)

    def test_the_uri_is_rebuilt_from_validated_parts(self) -> None:
        """(28)"""
        self.assertEqual(
            build_uri("track", "1a2b3c4d5e6f7g8h9i0j1k"),
            "spotify:track:1a2b3c4d5e6f7g8h9i0j1k",
        )
        for bad_id in ("../etc", "short", "x" * 23, "has space", ""):
            with self.subTest(item_id=bad_id):
                with self.assertRaises(ValueError):
                    build_uri("track", bad_id)
        with self.assertRaises(ValueError):
            build_uri("not-a-type", "1a2b3c4d5e6f7g8h9i0j1k")

    def test_no_credential_reaches_the_search_query_string(self) -> None:
        """(12) The bearer token belongs in a header, and the secret nowhere."""
        self.search(spotify_search_payload([spotify_track()]))
        for call in self.transport.calls:
            blob = json.dumps(call["query"])
            for secret in ALL_FAKE_CREDENTIALS:
                self.assertNotIn(secret, blob)


class SpotifyFailureTests(NormalizationTestCase):
    def run_with(self, replies):
        for reply in replies:
            self.transport.queue(reply)
        with patch.object(spotify_module, "request", self.transport):
            return self.spotify().search(TURKISH_QUERY)

    def test_bad_credentials_report_rejected_not_unconfigured(self) -> None:
        with self.assertRaises(ProviderRejected):
            self.run_with([json_response(400, {"error": "invalid_client"})])

    def test_missing_credentials_report_unconfigured(self) -> None:
        (self.config.secrets_dir / "media_providers.json").unlink()
        with self.assertRaises(ProviderUnconfigured):
            self.run_with([])

    def test_rate_limiting_carries_retry_after_without_sleeping(self) -> None:
        started = time.monotonic()
        with self.assertRaises(ProviderRateLimited) as caught:
            self.run_with([
                json_response(200, {"access_token": "t", "expires_in": 3600}),
                json_response(429, {}, {"retry-after": "30"}),
            ])
        self.assertEqual(caught.exception.retry_after_seconds, 30)
        # The daemon must not have blocked for the provider's benefit.
        self.assertLess(time.monotonic() - started, 2.0)

    def test_a_timeout_is_temporarily_unavailable(self) -> None:
        with self.assertRaises(ProviderTemporarilyUnavailable):
            self.run_with([
                json_response(200, {"access_token": "t", "expires_in": 3600}),
                TransportError("timed out", timeout=True),
            ])

    def test_a_malformed_body_fails_closed(self) -> None:
        from cofferdam.workstation.mediasearch.transport import Response

        with self.assertRaises(ProviderMalformedResponse):
            self.run_with([
                json_response(200, {"access_token": "t", "expires_in": 3600}),
                Response(status=200, body=b"not json at all", headers={}),
            ])

    def test_an_expired_token_is_retried_exactly_once(self) -> None:
        self.transport.queue_spotify_token()
        self.transport.queue(json_response(401, {}))
        self.transport.queue_spotify_token()
        self.transport.queue(json_response(200, spotify_search_payload([spotify_track()])))
        with patch.object(spotify_module, "request", self.transport):
            outcome = self.spotify().search(TURKISH_QUERY)
        self.assertEqual(len(outcome.results), 1)
        # token, search(401), token, search(200) — and no further attempts.
        self.assertEqual(len(self.transport.calls), 4)

    def test_no_error_message_carries_a_credential(self) -> None:
        """(12, 13)"""
        for replies in (
            [json_response(400, {"error": "invalid_client"})],
            [json_response(200, {"access_token": "t", "expires_in": 3600}),
             json_response(500, {})],
        ):
            with self.subTest(replies=len(replies)):
                self.transport = FakeTransport()
                try:
                    self.run_with(replies)
                except Exception as exc:
                    rendered = repr(exc) + str(exc) + str(getattr(exc, "detail", ""))
                    for secret in ALL_FAKE_CREDENTIALS:
                        self.assertNotIn(secret, rendered)


class YouTubeNormalizationTests(NormalizationTestCase):
    def search(self, payload, status=200, headers=None):
        self.transport.queue(json_response(status, payload, headers))
        with patch.object(youtube_module, "request", self.transport):
            return self.youtube().search("Cofferdam")

    def test_a_video_normalizes_to_bounded_fields(self) -> None:
        """(15)"""
        outcome = self.search(youtube_search_payload([youtube_video()]))
        self.assertEqual(len(outcome.results), 1)
        result = outcome.results[0]
        self.assertEqual(result.result_type, "video")
        self.assertEqual(result.title, "Cofferdam demo")
        self.assertEqual(result.subtitle, "Cofferdam")
        self.assertEqual(result.published, "2026-01-15")
        # search.list carries no duration and none is invented.
        self.assertIsNone(result.duration_seconds)

    def test_only_video_results_appear(self) -> None:
        """(16) The kind is verified, not merely requested."""
        channel = {
            "id": {"kind": "youtube#channel", "channelId": "UC123"},
            "snippet": {"title": "A channel", "channelTitle": "A channel"},
        }
        playlist = {
            "id": {"kind": "youtube#playlist", "playlistId": "PL123"},
            "snippet": {"title": "A playlist", "channelTitle": "Someone"},
        }
        outcome = self.search(
            youtube_search_payload([channel, playlist, youtube_video()])
        )
        self.assertEqual(len(outcome.results), 1)
        self.assertEqual(outcome.results[0].result_type, "video")
        self.assertTrue(all(item.item_type == "video" for item in outcome.items))

    def test_the_request_asks_for_videos_only(self) -> None:
        """(16)"""
        self.search(youtube_search_payload([youtube_video()]))
        call = self.transport.last()
        self.assertEqual(call["host"], "www.googleapis.com")
        self.assertEqual(call["query"]["type"], "video")
        self.assertEqual(call["query"]["part"], "snippet")
        self.assertEqual(call["query"]["maxResults"], str(MAX_RESULTS))

    def test_a_malformed_video_id_is_dropped(self) -> None:
        bad = youtube_video(video_id="tooshort")
        outcome = self.search(youtube_search_payload([bad, youtube_video()]))
        self.assertEqual(len(outcome.results), 1)

    def test_video_ids_are_validated_before_a_url_is_built(self) -> None:
        """(30)"""
        self.assertEqual(
            build_watch_url("dQw4w9WgXcQ"), "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        for bad in ("../../evil", "short", "x" * 12, "has space", "", "a&b=c"):
            with self.subTest(video_id=bad):
                with self.assertRaises(ValueError):
                    build_watch_url(bad)

    def test_html_entities_in_a_title_stay_text(self) -> None:
        """Nothing unescapes provider text into markup."""
        outcome = self.search(
            youtube_search_payload([youtube_video(title="Rock &amp; Roll &#39;76")])
        )
        self.assertEqual(outcome.results[0].title, "Rock &amp; Roll &#39;76")

    def test_the_result_count_is_capped(self) -> None:
        """(17)"""
        videos = [youtube_video(video_id="abcdefghij" + str(index)) for index in range(9)]
        outcome = self.search(youtube_search_payload(videos))
        self.assertLessEqual(len(outcome.results), MAX_RESULTS)

    def test_quota_exhaustion_is_distinct_from_key_rejection(self) -> None:
        quota = {"error": {"errors": [{"reason": "quotaExceeded"}]}}
        self.transport = FakeTransport()
        with self.assertRaises(ProviderRateLimited):
            self.search(quota, status=403)

        self.transport = FakeTransport()
        restricted = {"error": {"errors": [{"reason": "keyInvalid"}]}}
        with self.assertRaises(ProviderRejected):
            self.search(restricted, status=403)

    def test_the_api_key_never_appears_in_an_error(self) -> None:
        """(12, 13)"""
        self.transport = FakeTransport()
        try:
            self.search({"error": {"errors": [{"reason": "keyInvalid"}]}}, status=403)
        except Exception as exc:
            rendered = repr(exc) + str(exc) + str(getattr(exc, "detail", ""))
            self.assertNotIn(FAKE_YOUTUBE_API_KEY, rendered)

    def test_a_provider_message_is_never_forwarded(self) -> None:
        """Provider prose is outside text and must not reach a phone screen."""
        payload = {
            "error": {
                "errors": [{"reason": "keyInvalid", "message": "PROVIDER-SUPPLIED-TEXT"}],
                "message": "PROVIDER-SUPPLIED-TEXT",
            }
        }
        self.transport = FakeTransport()
        try:
            self.search(payload, status=403)
        except Exception as exc:
            rendered = repr(exc) + str(exc) + str(getattr(exc, "detail", ""))
            self.assertNotIn("PROVIDER-SUPPLIED-TEXT", rendered)


# ---------------------------------------------------------------------------
# (22-27) search sessions
# ---------------------------------------------------------------------------


def _outcome(provider_id="spotify", count=2) -> MediaSearchOutcome:
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
            item_id=("1a2b3c4d5e6f7g8h9i0j1k" if provider_id == "spotify" else "dQw4w9WgXcQ"),
        )
        for _ in range(count)
    )
    return MediaSearchOutcome(
        provider_id=provider_id, query="q", results=results, items=items
    )


class SearchSessionTests(unittest.TestCase):
    def test_a_session_never_serializes_its_provider_items(self) -> None:
        """(19, 20, 21) The launch targets stay server-side."""
        store = SearchSessionStore()
        session = store.create(_outcome())
        payload = session.to_dict()
        self.assertNotIn("items", payload)
        blob = json.dumps(payload)
        self.assertNotIn("spotify:", blob)
        self.assertNotIn("1a2b3c4d5e6f7g8h9i0j1k", blob)

    def test_an_unknown_session_is_rejected(self) -> None:
        """(23)"""
        store = SearchSessionStore()
        for candidate in ("nope", "", None, 7):
            with self.subTest(search_id=repr(candidate)):
                with self.assertRaises(SearchSessionNotFound):
                    store.get(candidate)

    def test_an_expired_session_is_rejected_and_forgotten(self) -> None:
        """(22, 27)"""
        store = SearchSessionStore(ttl_seconds=60)
        session = store.create(_outcome(), now=1000.0)
        store.get(session.search_id, now=1030.0)
        with self.assertRaises(SearchSessionExpired):
            store.get(session.search_id, now=1061.0)
        # Gone, so the same id cannot come back later.
        with self.assertRaises(SearchSessionNotFound):
            store.get(session.search_id, now=1062.0)

    def test_an_unknown_result_is_rejected(self) -> None:
        """(24)"""
        store = SearchSessionStore()
        session = store.create(_outcome())
        for candidate in ("r99", "", None, "../r0"):
            with self.subTest(result_id=repr(candidate)):
                with self.assertRaises(ResultNotFound):
                    store.resolve(session.search_id, candidate)

    def test_a_result_cannot_be_opened_through_another_provider(self) -> None:
        """(25) The check that keeps a video id out of the Spotify URI adapter."""
        store = SearchSessionStore()
        session = store.create(_outcome(provider_id="youtube"))
        with self.assertRaises(ResultNotFound):
            store.resolve(session.search_id, "r0", provider_id="spotify")
        # And the honest path still works.
        _, result, item = store.resolve(session.search_id, "r0", provider_id="youtube")
        self.assertEqual(item.provider_id, "youtube")

    def test_session_count_is_bounded_with_oldest_first_eviction(self) -> None:
        """(26)"""
        store = SearchSessionStore(max_sessions=4)
        created = [store.create(_outcome(), now=1000.0 + index) for index in range(10)]
        self.assertLessEqual(store.count(now=1010.0), 4)
        # The oldest went first.
        with self.assertRaises(SearchSessionNotFound):
            store.get(created[0].search_id, now=1010.0)
        store.get(created[-1].search_id, now=1010.0)

    def test_first_requires_a_non_empty_result_list(self) -> None:
        """(34) Zero results cannot trigger Open first result."""
        store = SearchSessionStore()
        empty = store.create(_outcome(count=0))
        with self.assertRaises(ResultNotFound):
            store.first(empty.search_id)

    def test_first_is_index_zero_of_the_verified_session(self) -> None:
        """(33)"""
        store = SearchSessionStore()
        session = store.create(_outcome(count=3))
        _, result, _ = store.first(session.search_id)
        self.assertEqual(result.result_id, "r0")
        self.assertEqual(result.title, "Result 0")

    def test_ids_are_unguessable(self) -> None:
        store = SearchSessionStore()
        ids = {store.create(_outcome()).search_id for _ in range(8)}
        self.assertEqual(len(ids), 8)
        for search_id in ids:
            self.assertGreaterEqual(len(search_id), 16)

    def test_the_default_ttl_is_short(self) -> None:
        self.assertLessEqual(SEARCH_SESSION_TTL_SECONDS, 3600)
        self.assertLessEqual(MAX_SEARCH_SESSIONS, 256)

    def test_sessions_do_not_persist(self) -> None:
        """(27) Nothing in the package writes a session anywhere."""
        source = (PACKAGE / "sessions.py").read_text(encoding="utf-8")
        for forbidden in ("open(", "write_text", "Path(", "json.dump", "sqlite"):
            with self.subTest(marker=forbidden):
                self.assertNotIn(forbidden, source)


# ---------------------------------------------------------------------------
# (5-9, 19-21) schema boundaries
# ---------------------------------------------------------------------------


@requires_schemas
class ActionSchemaTests(unittest.TestCase):
    """The absence of a destination field, asserted directly."""

    def schemas(self):
        from cofferdam.workstation.actions import (
            FindMediaResultsParams,
            OpenMediaResultParams,
        )

        return FindMediaResultsParams, OpenMediaResultParams

    def test_the_schemas_accept_only_handles_and_a_phrase(self) -> None:
        """(19, 20, 21)"""
        find, open_result = self.schemas()
        self.assertEqual(set(find.model_fields), {"provider_id", "query", "types"})
        self.assertEqual(
            set(open_result.model_fields),
            {"provider_id", "search_id", "result_id", "open_first"},
        )

    def test_a_client_cannot_smuggle_a_destination(self) -> None:
        """(19, 20, 21) Refused, not ignored."""
        find, open_result = self.schemas()
        for field in ("url", "uri", "spotify_uri", "watch_url", "video_id", "target",
                      "endpoint", "template", "command", "api_key"):
            with self.subTest(field=field):
                with self.assertRaises(Exception):
                    open_result(
                        provider_id="spotify", search_id="abc", result_id="r0",
                        **{field: "https://evil.example"}
                    )
                with self.assertRaises(Exception):
                    find(provider_id="spotify", query="x", **{field: "https://evil.example"})

    def test_a_handle_cannot_contain_a_url_or_a_path(self) -> None:
        """(19) The character class alone forbids it."""
        _, open_result = self.schemas()
        for bad in (
            "https://evil.example",
            "spotify:track:1a2b3c4d5e6f7g8h9i0j1k",
            "../../etc/passwd",
            "/usr/bin/sh",
            "a b",
            "x" * 100,
            "",
        ):
            with self.subTest(handle=bad[:24]):
                with self.assertRaises(Exception):
                    open_result(provider_id="spotify", search_id=bad, result_id="r0")

    def test_exactly_one_selection_method_is_required(self) -> None:
        """(33) Opening index 0 is always something someone asked for."""
        _, open_result = self.schemas()
        with self.assertRaises(Exception):
            open_result(provider_id="spotify", search_id="abc")
        with self.assertRaises(Exception):
            open_result(
                provider_id="spotify", search_id="abc", result_id="r0", open_first=True
            )
        self.assertTrue(
            open_result(provider_id="spotify", search_id="abc", open_first=True).open_first
        )

    def test_provider_ids_are_allowlisted(self) -> None:
        """(8)"""
        find, _ = self.schemas()
        for bad in ("hbo-max", "Spotify", "spotify ", "", "../spotify"):
            with self.subTest(provider=bad):
                with self.assertRaises(Exception):
                    find(provider_id=bad, query="x")

    def test_result_types_are_allowlisted(self) -> None:
        """(9)"""
        find, _ = self.schemas()
        for bad in (["audiobook"], ["track", "nonsense"], ["../track"], ["a", "b", "c", "d"]):
            with self.subTest(types=bad):
                with self.assertRaises(Exception):
                    find(provider_id="spotify", query="x", types=bad)
        self.assertEqual(find(provider_id="spotify", query="x", types=["track"]).types, ["track"])

    def test_query_rules_match_the_m2b3a_boundary(self) -> None:
        """(4, 5, 6, 7)"""
        find, _ = self.schemas()
        # Turkish survives, and surrounding whitespace is trimmed.
        self.assertEqual(
            find(provider_id="spotify", query="  " + TURKISH_QUERY + "  ").query,
            TURKISH_QUERY,
        )
        for bad in ("", "   ", "a\nb", "a\x00b", "a" * (media.MAX_QUERY_LENGTH + 1)):
            with self.subTest(query=repr(bad[:12])):
                with self.assertRaises(Exception):
                    find(provider_id="spotify", query=bad)


# ---------------------------------------------------------------------------
# through the API
# ---------------------------------------------------------------------------


class MediaResultsApiTestCase(WorkstationTestCase):
    """Drives the real service with a fake transport behind the adapters."""

    configure_credentials = True

    def setUp(self) -> None:
        super().setUp()
        if self.configure_credentials:
            write_credentials(self.config)
        self.transport = FakeTransport()

    def providers(self) -> dict:
        response = self.client.get("/api/media/providers", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        return {entry["id"]: entry for entry in response.json()["providers"]}

    def find(self, provider_id: str, query: str, payload=None, status=200, **kwargs):
        module = spotify_module if provider_id == "spotify" else youtube_module
        if provider_id == "spotify":
            self.transport.queue_spotify_token()
        if payload is not None:
            self.transport.queue(json_response(status, payload))
        with patch.object(module, "request", self.transport):
            return self.client.post(
                f"/api/media/providers/{provider_id}/results/search",
                json=dict({"query": query}, **kwargs),
                headers=self.auth,
            )

    def open_result(self, search_id: str, result_id: str, provider_id: str):
        return self.client.post(
            f"/api/media/searches/{search_id}/results/{result_id}/open",
            json={"provider_id": provider_id},
            headers=self.auth,
        )


class ConfiguredSearchApiTests(MediaResultsApiTestCase):
    def test_the_catalogue_reports_structured_search_as_configured(self) -> None:
        """(1)"""
        providers = self.providers()
        self.assertTrue(providers["spotify"]["capabilities"]["structured_search"])
        self.assertTrue(providers["youtube"]["capabilities"]["structured_search"])
        for provider_id in ("netflix", "prime-video", "tv-plus"):
            with self.subTest(provider=provider_id):
                self.assertFalse(
                    providers[provider_id]["capabilities"]["structured_search"]
                )

    def test_a_spotify_search_returns_cards_and_opens_nothing(self) -> None:
        response = self.find(
            "spotify", TURKISH_QUERY, spotify_search_payload([spotify_track()])
        )
        self.assertEqual(response.status_code, 200)
        record = response.json()
        self.assertEqual(record["status"], "succeeded")
        result = record["result"]
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["title"], "Gönül Dağı")
        self.assertEqual(result["result_model_version"], MEDIA_RESULT_MODEL_VERSION)
        self.assertFalse(result["playback_started"])
        # Nothing launched.
        self.assertEqual(self.adapter.opened_uris, [])
        self.assertEqual(self.adapter.opened_urls, [])

    def test_the_search_response_carries_no_credential_and_no_target(self) -> None:
        """(12, 19, 20)"""
        response = self.find(
            "spotify", TURKISH_QUERY, spotify_search_payload([spotify_track()])
        )
        blob = json.dumps(response.json())
        for secret in ALL_FAKE_CREDENTIALS:
            self.assertNotIn(secret, blob)
        self.assertNotIn("spotify:", blob)
        self.assertNotIn("api.spotify.com", blob)
        self.assertNotIn("Bearer", blob)

    def test_a_selected_spotify_result_opens_through_the_native_uri_adapter(self) -> None:
        """(28, 29)"""
        search = self.find(
            "spotify", TURKISH_QUERY, spotify_search_payload([spotify_track()])
        ).json()["result"]
        response = self.open_result(search["search_id"], "r0", "spotify")
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(result["opened_in"], "application")
        self.assertEqual(
            self.adapter.opened_uris, [("spotify", "spotify:track:1a2b3c4d5e6f7g8h9i0j1k")]
        )
        self.assertFalse(result["playback_started"])
        self.assertEqual(result["playback"], media.PLAYBACK_NOT_STARTED)
        self.assertIn("nothing is playing", result["note"].lower())

    def test_a_selected_youtube_result_opens_in_opera(self) -> None:
        """(30, 32)"""
        search = self.find(
            "youtube", "Cofferdam", youtube_search_payload([youtube_video()])
        ).json()["result"]
        response = self.open_result(search["search_id"], "r0", "youtube")
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(result["opened_in"], "browser")
        self.assertEqual(
            self.adapter.opened_urls, ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]
        )
        self.assertEqual(self.adapter.opened_with, ["opera"])
        self.assertFalse(result["playback_started"])

    def test_open_first_result_uses_index_zero(self) -> None:
        """(33)"""
        tracks = [
            spotify_track(name="First", track_id="aaaaaaaaaaaaaaaaaaaaaa"),
            spotify_track(name="Second", track_id="bbbbbbbbbbbbbbbbbbbbbb"),
        ]
        search = self.find("spotify", TURKISH_QUERY, spotify_search_payload(tracks)).json()[
            "result"
        ]
        response = self.open_result(search["search_id"], "first", "spotify")
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(result["title"], "First")
        self.assertEqual(result["selected_by"], "first_result")
        self.assertEqual(
            self.adapter.opened_uris, [("spotify", "spotify:track:aaaaaaaaaaaaaaaaaaaaaa")]
        )

    def test_zero_results_cannot_open_a_first_result(self) -> None:
        """(34)"""
        search = self.find("spotify", "zzzz", spotify_search_payload([])).json()["result"]
        self.assertEqual(search["results"], [])
        response = self.open_result(search["search_id"], "first", "spotify")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "media_result_not_found")
        self.assertEqual(self.adapter.opened_uris, [])

    def test_a_result_cannot_be_opened_through_the_wrong_provider(self) -> None:
        """(25)"""
        search = self.find(
            "youtube", "Cofferdam", youtube_search_payload([youtube_video()])
        ).json()["result"]
        response = self.open_result(search["search_id"], "r0", "spotify")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "media_result_not_found")
        self.assertEqual(self.adapter.opened_uris, [])
        self.assertEqual(self.adapter.opened_urls, [])

    def test_an_unknown_search_or_result_is_rejected(self) -> None:
        """(23, 24)"""
        response = self.open_result("nosuchsearch", "r0", "spotify")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "media_search_not_found")

        search = self.find(
            "spotify", TURKISH_QUERY, spotify_search_payload([spotify_track()])
        ).json()["result"]
        response = self.open_result(search["search_id"], "r42", "spotify")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "media_result_not_found")

    def test_an_expired_search_is_rejected(self) -> None:
        """(22)"""
        search = self.find(
            "spotify", TURKISH_QUERY, spotify_search_payload([spotify_track()])
        ).json()["result"]
        store = self.app.state.media_search.sessions
        # Age the session past its TTL rather than sleeping through it.
        with patch.object(
            store, "_sessions", {
                sid: session.__class__(
                    **dict(
                        session.__dict__,
                        expires_at=time.time() - 1,
                    )
                )
                for sid, session in store._sessions.items()
            }
        ):
            response = self.open_result(search["search_id"], "r0", "spotify")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "media_search_expired")
        self.assertEqual(self.adapter.opened_uris, [])

    def test_a_client_cannot_post_a_url_to_the_open_route(self) -> None:
        """(19, 20, 21)"""
        search = self.find(
            "spotify", TURKISH_QUERY, spotify_search_payload([spotify_track()])
        ).json()["result"]
        for smuggled in (
            {"url": "https://evil.example"},
            {"uri": "spotify:track:0000000000000000000000"},
            {"watch_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"},
            {"video_id": "aaaaaaaaaaa"},
        ):
            with self.subTest(field=sorted(smuggled)[0]):
                response = self.client.post(
                    f"/api/media/searches/{search['search_id']}/results/r0/open",
                    json=dict({"provider_id": "spotify"}, **smuggled),
                    headers=self.auth,
                )
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.adapter.opened_uris, [])

    def test_selecting_a_later_result_opens_that_one(self) -> None:
        """The whole point of the milestone: the *intended* item, not the first.

        Regression guard for a real defect found by driving the PWA: the open
        route requires ``provider_id`` in the body, and the phone was sending an
        empty body, so every selection failed with "invalid parameters" while
        every unit test passed. This exercises the exact payload the client
        sends, which is what the earlier tests were not doing.
        """
        tracks = [
            spotify_track(name="First", track_id="aaaaaaaaaaaaaaaaaaaaaa", artist="Artist A"),
            spotify_track(name="Second", track_id="bbbbbbbbbbbbbbbbbbbbbb", artist="Artist B"),
            spotify_track(name="Third", track_id="cccccccccccccccccccccc", artist="Artist C"),
        ]
        search = self.find("spotify", TURKISH_QUERY, spotify_search_payload(tracks)).json()[
            "result"
        ]
        response = self.client.post(
            f"/api/media/searches/{search['search_id']}/results/r1/open",
            json={"provider_id": "spotify"},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(result["result_id"], "r1")
        self.assertEqual(result["title"], "Second")
        self.assertEqual(result["selected_by"], "user")
        self.assertEqual(
            self.adapter.opened_uris, [("spotify", "spotify:track:bbbbbbbbbbbbbbbbbbbbbb")]
        )

    def test_the_open_route_requires_the_provider_id(self) -> None:
        """The client's assertion is what the cross-provider check compares to."""
        search = self.find(
            "spotify", TURKISH_QUERY, spotify_search_payload([spotify_track()])
        ).json()["result"]
        response = self.client.post(
            f"/api/media/searches/{search['search_id']}/results/r0/open",
            json={},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.adapter.opened_uris, [])

    def test_a_provider_failure_does_not_break_ordinary_open(self) -> None:
        """(36)"""
        self.transport = FakeTransport()
        self.transport.queue_spotify_token()
        self.transport.queue(json_response(500, {}))
        with patch.object(spotify_module, "request", self.transport):
            failed = self.client.post(
                "/api/media/providers/spotify/results/search",
                json={"query": TURKISH_QUERY},
                headers=self.auth,
            )
        self.assertEqual(failed.status_code, 502)

        # The M2B3A actions are untouched.
        opened = self.post_action("open_media_provider", {"provider_id": "spotify"})
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(self.adapter.launched, ["spotify"])
        searched = self.post_action(
            "search_media_provider", {"provider_id": "youtube", "query": "x"}
        )
        self.assertEqual(searched.status_code, 200)

    def test_recent_actions_never_carry_a_credential(self) -> None:
        """(12, 13)"""
        self.find("spotify", TURKISH_QUERY, spotify_search_payload([spotify_track()]))
        blob = json.dumps(self.client.get("/api/actions", headers=self.auth).json())
        for secret in ALL_FAKE_CREDENTIALS:
            self.assertNotIn(secret, blob)

    def test_diagnostics_report_status_words_only(self) -> None:
        response = self.client.get("/api/media/diagnostics", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["providers"]["spotify"], STATUS_CONFIGURED)
        blob = json.dumps(payload)
        for secret in ALL_FAKE_CREDENTIALS:
            self.assertNotIn(secret, blob)
        self.assertNotIn("media_providers.json", blob)
        self.assertNotIn(str(self.home), blob)

    def test_diagnostics_require_a_token(self) -> None:
        self.assertEqual(self.client.get("/api/media/diagnostics").status_code, 401)

    def test_structured_search_requires_a_token(self) -> None:
        response = self.client.post(
            "/api/media/providers/spotify/results/search", json={"query": "x"}
        )
        self.assertEqual(response.status_code, 401)


class UnconfiguredSearchApiTests(MediaResultsApiTestCase):
    """(2) The normal state of a machine that has set nothing up."""

    configure_credentials = False

    def test_structured_search_is_reported_unconfigured(self) -> None:
        providers = self.providers()
        for provider_id in ("spotify", "youtube"):
            with self.subTest(provider=provider_id):
                entry = providers[provider_id]
                self.assertFalse(entry["capabilities"]["structured_search"])
                self.assertFalse(entry["structured_search_configured"])
                # Still perfectly launchable.
                self.assertTrue(entry["available"])

    def test_the_m2b3a_actions_still_work(self) -> None:
        """(2, 36)"""
        opened = self.post_action("open_media_provider", {"provider_id": "spotify"})
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(self.adapter.launched, ["spotify"])

        searched = self.post_action(
            "search_media_provider", {"provider_id": "youtube", "query": TURKISH_QUERY}
        )
        self.assertEqual(searched.status_code, 200)
        self.assertIn("youtube.com/results", self.adapter.opened_urls[-1])

    def test_a_structured_search_refuses_truthfully(self) -> None:
        response = self.client.post(
            "/api/media/providers/spotify/results/search",
            json={"query": TURKISH_QUERY},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["error"]["code"], "media_provider_unconfigured"
        )

    def test_diagnostics_report_missing(self) -> None:
        payload = self.client.get("/api/media/diagnostics", headers=self.auth).json()
        self.assertEqual(payload["providers"]["spotify"], STATUS_MISSING)
        self.assertEqual(payload["providers"]["youtube"], STATUS_MISSING)


class StreamingProvidersUnchangedTests(MediaResultsApiTestCase):
    """(3) Netflix, Prime Video and TV+ gain nothing here."""

    def test_they_cannot_run_a_structured_search(self) -> None:
        for provider_id in ("netflix", "prime-video", "tv-plus"):
            with self.subTest(provider=provider_id):
                response = self.client.post(
                    f"/api/media/providers/{provider_id}/results/search",
                    json={"query": "anything"},
                    headers=self.auth,
                )
                self.assertEqual(response.status_code, 502)
                self.assertEqual(
                    response.json()["error"]["code"], "media_provider_unconfigured"
                )

    def test_their_m2b3a_behaviour_is_unchanged(self) -> None:
        for provider_id in ("netflix", "prime-video", "tv-plus"):
            with self.subTest(provider=provider_id):
                response = self.post_action(
                    "open_media_provider", {"provider_id": provider_id}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.adapter.opened_with[-1], "opera")

    def test_tv_plus_still_has_no_search_page(self) -> None:
        response = self.post_action(
            "search_media_provider", {"provider_id": "tv-plus", "query": "x"}
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["error"]["code"], "media_search_unsupported"
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
