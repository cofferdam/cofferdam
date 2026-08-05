"""Playback snapshots, device handles, and the typed actions (M2D 21–50).

Every action in this milestone acts, re-reads Spotify's state, and reports what
it *observed*. That is the whole design, and it is only meaningful if the
re-read can disagree with the request — so the fake here has an ``ignore_writes``
mode where Spotify accepts a command with ``204`` and nothing moves. Half the
tests below are about what happens then.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.config import load_config
from cofferdam.workstation.mediasearch.credentials import CredentialStore
from cofferdam.workstation.mediasearch.results import (
    MediaResult,
    MediaSearchOutcome,
    ProviderItem,
)
from cofferdam.workstation.mediasearch.sessions import SearchSessionStore
from cofferdam.workstation.spotifyplayer.actions import (
    OUTCOME_ACCEPTED,
    OUTCOME_APPLIED,
    OUTCOME_NOT_APPLIED,
    OUTCOME_PARTIAL,
    SpotifyActionExecutor,
    clean_volume_percent,
)
from cofferdam.workstation.spotifyplayer.client import SpotifyPlayerClient
from cofferdam.workstation.spotifyplayer.errors import (
    CODE_DEVICE_RESTRICTED,
    CODE_DEVICE_UNKNOWN,
    CODE_INVALID_VOLUME,
    CODE_NO_ACTIVE_DEVICE,
    CODE_PREMIUM_REQUIRED,
    CODE_RATE_LIMITED,
    CODE_RESULT_NOT_PLAYABLE,
    CODE_UNMUTE_UNKNOWN,
    CODE_VOLUME_UNSUPPORTED,
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    STATUS_MISSING_SCOPES,
    STATUS_PREMIUM_REQUIRED,
    STATUS_REFRESH_FAILED,
    STATUS_TEMPORARILY_UNAVAILABLE,
    SpotifyPlayerError,
)
from cofferdam.workstation.spotifyplayer.service import SpotifyPlayerService
from cofferdam.workstation.spotifyplayer.tokens import TokenStore

from ._mediasearch_doubles import write_credentials
from ._spotifyplayer_doubles import (
    ALL_FAKE_OAUTH_SECRETS,
    FAKE_ACCESS_TOKEN,
    FAKE_REFRESH_TOKEN,
    OTHER_TRACK_ID,
    TRACK_ID,
    FakeApplicationAdapter,
    FakeSpotify,
    device,
    instant_recovery,
    track_item,
    write_user_tokens,
)


class PlaybackTestCase(unittest.TestCase):
    """A service wired to the fake Spotify, with a connected account."""

    connected = True

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = load_config(home=Path(self._tmp.name))
        self.config.ensure_dirs()
        write_credentials(self.config, youtube=False)
        if self.connected:
            write_user_tokens(self.config)

        self.spotify = FakeSpotify()
        self.tokens = TokenStore(self.config)
        self.service = SpotifyPlayerService(
            self.config,
            CredentialStore(self.config),
            token_store=self.tokens,
            client=SpotifyPlayerClient(lambda: "test-client-id", self.tokens, request=self.spotify),
            cache_seconds=0.0,
        )
        self.sessions = SearchSessionStore()
        # Cold-start recovery, with bounded-but-instant waits: the attempt counts
        # are real and asserted on, only the sleeping is removed.
        self.adapter = FakeApplicationAdapter(self.spotify)
        self.actions = SpotifyActionExecutor(
            self.service,
            self.sessions,
            recovery=instant_recovery(self.service, self.adapter),
            sleeper=lambda seconds: None,
        )

    # -- helpers -----------------------------------------------------------

    def search_session(self, *, provider_id: str = "spotify", item_type: str = "track",
                       item_id: str = TRACK_ID, now=None):
        result = MediaResult(
            provider_id=provider_id,
            result_id="mres-one",
            result_type=item_type,
            title="Gönül Dağı",
            creators=("Neşet Ertaş",),
        )
        outcome = MediaSearchOutcome(
            provider_id=provider_id,
            query="Gönül Dağı",
            results=(result,),
            items=(ProviderItem(provider_id=provider_id, item_type=item_type, item_id=item_id),),
        )
        return self.sessions.create(outcome, now=now)


class ConnectionStatusTests(PlaybackTestCase):
    """The status is derived from what Spotify does, not from a stored flag."""

    def test_a_working_account_reads_as_connected(self) -> None:
        self.assertEqual(self.service.connection_status()["status"], STATUS_CONNECTED)

    def test_the_status_never_contains_a_token(self) -> None:
        blob = json.dumps(self.service.snapshot(refresh=True).to_dict(), ensure_ascii=False)
        for secret in ALL_FAKE_OAUTH_SECRETS:
            self.assertNotIn(secret, blob)

    def test_a_short_authorization_reads_as_missing_scopes(self) -> None:
        write_user_tokens(self.config, scopes="user-read-playback-state")
        service = SpotifyPlayerService(
            self.config, CredentialStore(self.config), cache_seconds=0.0,
            client=SpotifyPlayerClient(lambda: "id", TokenStore(self.config), request=self.spotify),
        )
        status = service.connection_status()
        self.assertEqual(status["status"], STATUS_MISSING_SCOPES)
        self.assertIn("user-modify-playback-state", status["missing_scopes"])

    def test_a_rejected_refresh_token_reads_as_refresh_failed_not_connected(self) -> None:
        """Check 15 at the service: an invalid grant is never a connected state."""
        self.spotify.refresh_status = 400
        snapshot = self.service.snapshot(refresh=True)
        self.assertEqual(snapshot.connection["status"], STATUS_REFRESH_FAILED)
        self.assertFalse(snapshot.playback_available)

    def test_a_premium_refusal_is_reported_truthfully(self) -> None:
        """Check 21. Reported only when Spotify actually says so."""
        self.spotify.fail(
            "/v1/me/player/devices", 403, {"error": {"status": 403, "reason": "PREMIUM_REQUIRED"}}
        )
        snapshot = self.service.snapshot(refresh=True)
        self.assertEqual(snapshot.connection["status"], STATUS_PREMIUM_REQUIRED)

    def test_a_bare_403_names_both_documented_causes_rather_than_guessing(self) -> None:
        self.spotify.fail("/v1/me/player/devices", 403, {"error": {"status": 403}})
        snapshot = self.service.snapshot(refresh=True)
        self.assertNotEqual(snapshot.connection["status"], STATUS_PREMIUM_REQUIRED)
        detail = snapshot.connection["detail"] or ""
        self.assertIn("Premium", detail)
        self.assertIn("development mode", detail)

    def test_rate_limiting_is_temporary_not_disconnected(self) -> None:
        self.spotify.fail("/v1/me/player/devices", 429, {"error": {"status": 429}})
        snapshot = self.service.snapshot(refresh=True)
        self.assertEqual(snapshot.connection["status"], STATUS_TEMPORARILY_UNAVAILABLE)

    def test_an_action_taken_during_a_provider_problem_keeps_that_reason(self) -> None:
        """A read that failed for a specific reason must not become a generic one.

        Every one of these produces a snapshot with no devices in it, so an
        action that resolved a device first would report all three as "no active
        device" and hide what is actually wrong.
        """
        for status, payload, expected in (
            (429, {"error": {"status": 429}}, CODE_RATE_LIMITED),
            (403, {"error": {"status": 403, "reason": "PREMIUM_REQUIRED"}}, CODE_PREMIUM_REQUIRED),
        ):
            with self.subTest(status=status):
                self.spotify.failures.clear()
                self.spotify.fail("/v1/me/player/devices", status, payload)
                with self.assertRaises(SpotifyPlayerError) as raised:
                    self.actions.pause()
                self.assertEqual(raised.exception.code, expected)


class DisconnectedTests(PlaybackTestCase):
    connected = False

    def test_no_stored_authorization_reads_as_disconnected(self) -> None:
        self.assertEqual(self.service.connection_status()["status"], STATUS_DISCONNECTED)

    def test_a_disconnected_host_makes_no_provider_call(self) -> None:
        self.service.snapshot(refresh=True)
        self.assertEqual(self.spotify.calls, [])

    def test_every_action_is_refused_before_it_reaches_the_network(self) -> None:
        """And with the *right* refusal, which is the part that is easy to lose.

        A disconnected account has an empty device list, so an action that
        resolved a device before checking the connection would refuse with "no
        active device" — sending someone to switch on a speaker when what they
        need is to authorize an account.
        """
        from cofferdam.workstation.spotifyplayer.errors import CODE_NOT_CONNECTED

        for name, call in (
            ("pause", self.actions.pause),
            ("resume", self.actions.resume),
            ("next", lambda: self.actions.skip(True)),
            ("previous", lambda: self.actions.skip(False)),
            ("volume", lambda: self.actions.set_volume(30)),
            ("mute", lambda: self.actions.set_muted(True)),
            ("transfer", lambda: self.actions.transfer("spdev-anything")),
        ):
            with self.subTest(action=name):
                with self.assertRaises(SpotifyPlayerError) as raised:
                    call()
                self.assertEqual(raised.exception.code, CODE_NOT_CONNECTED)
        self.assertEqual(self.spotify.calls, [])


class SnapshotShapeTests(PlaybackTestCase):
    """Checks 27 and 28: bounded, and never the provider's own object."""

    def test_the_snapshot_has_a_closed_set_of_keys(self) -> None:
        payload = self.service.snapshot(refresh=True).to_dict()
        self.assertEqual(
            set(payload),
            {
                "version", "observed_at", "connection", "playback_available", "is_playing",
                "progress_ms", "repeat_state", "shuffle_state", "active_device_resource_id",
                "devices_available", "devices", "now_playing", "muted_by_cofferdam",
                "restore_volume_known", "capabilities", "limitations", "warnings",
            },
        )

    def test_the_current_track_is_reduced_to_what_a_person_reads(self) -> None:
        payload = self.service.snapshot(refresh=True).to_dict()
        self.assertEqual(
            set(payload["now_playing"]),
            {"item_type", "track_id", "title", "artists", "album", "duration_ms", "explicit"},
        )
        self.assertEqual(payload["now_playing"]["title"], "Gönül Dağı")

    def test_the_provider_object_does_not_travel(self) -> None:
        """The fake supplies external URLs, markets and a preview URL."""
        blob = json.dumps(self.service.snapshot(refresh=True).to_dict(), ensure_ascii=False)
        for leaked in ("external_urls", "available_markets", "preview_url",
                       "open.spotify.com", "p.scdn.co", "actions", "disallows", "timestamp"):
            self.assertNotIn(leaked, blob)

    def test_a_device_publishes_no_provider_id(self) -> None:
        """Check 23: a Spotify device id is never client authority."""
        payload = self.service.snapshot(refresh=True).to_dict()
        blob = json.dumps(payload)
        self.assertNotIn("dev-workstation", blob)
        for entry in payload["devices"]:
            self.assertNotIn("id", entry)
            self.assertTrue(entry["resource_id"].startswith("spdev-"))
            self.assertEqual(entry["identity_stability"], "provider_session")

    def test_the_same_device_gets_a_stable_handle_within_a_host(self) -> None:
        first = self.service.snapshot(refresh=True).devices[0].resource_id
        second = self.service.snapshot(refresh=True).devices[0].resource_id
        self.assertEqual(first, second)

    def test_a_device_with_no_id_is_dropped_rather_than_published(self) -> None:
        self.spotify.devices = [device(), {"id": None, "name": "Ghost", "is_active": False}]
        payload = self.service.snapshot(refresh=True).to_dict()
        self.assertEqual(len(payload["devices"]), 1)

    def test_nothing_playing_is_a_state_not_an_error(self) -> None:
        self.spotify.playback_available = False
        snapshot = self.service.snapshot(refresh=True)
        self.assertFalse(snapshot.playback_available)
        self.assertFalse(snapshot.is_playing)
        self.assertIsNone(snapshot.now_playing)
        self.assertEqual(snapshot.connection["status"], STATUS_CONNECTED)

    def test_the_limitations_say_mute_is_volume_zero(self) -> None:
        """Check 45: never described as a native Spotify mute."""
        text = " ".join(self.service.snapshot(refresh=True).to_dict()["limitations"]).lower()
        self.assertIn("no mute operation", text)
        self.assertIn("volume to zero", text)

    def test_the_flag_is_named_so_it_cannot_be_read_as_a_spotify_feature(self) -> None:
        payload = self.service.snapshot(refresh=True).to_dict()
        self.assertIn("muted_by_cofferdam", payload)
        self.assertNotIn("muted", payload)


class NoActiveDeviceTests(PlaybackTestCase):
    """Check 22: truthful, and never a claim that something started."""

    def setUp(self) -> None:
        super().setUp()
        self.spotify.devices = [device(is_active=False, name="Phone")]
        self.spotify.playback_available = False

    def test_the_snapshot_reports_no_active_device(self) -> None:
        snapshot = self.service.snapshot(refresh=True)
        self.assertIsNone(snapshot.active_device_resource_id)
        self.assertTrue(snapshot.devices_available)
        self.assertEqual(len(snapshot.devices), 1)

    def test_an_action_is_refused_with_the_no_active_device_code(self) -> None:
        with self.assertRaises(SpotifyPlayerError) as raised:
            self.actions.pause()
        self.assertEqual(raised.exception.code, CODE_NO_ACTIVE_DEVICE)

    def test_playing_a_result_adopts_the_one_idle_device_rather_than_refusing(self) -> None:
        """M2D.1 changed this deliberately, and real validation is why.

        A single eligible device that is merely *inactive* used to be refused
        with "no active device" — which is why "Open in Spotify, then Play now"
        was a working workaround, and the workaround was the diagnosis. There is
        no ambiguity here about where to play: there is one device, and the user
        pressed a button naming a track.
        """
        session = self.search_session()
        result = self.actions.play_search_result(session.search_id, "mres-one")
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["observed"]["track_id"], TRACK_ID)
        # Recovery, not a launch: Spotify was already running.
        self.assertEqual(self.adapter.launches, [])
        # And it was made active first, which is the documented operation.
        transfers = [
            call for call in self.spotify.calls
            if call["path"] == "/v1/me/player" and call["method"] == "PUT"
        ]
        self.assertEqual(len(transfers), 1)

    def test_queueing_still_refuses_when_there_is_nowhere_playing(self) -> None:
        """Recovery is scoped to Play now. Queueing is unchanged.

        Launching Spotify because somebody added a track to a queue would be a
        surprise; the milestone asks only that Play now recover.
        """
        session = self.search_session()
        with self.assertRaises(SpotifyPlayerError) as raised:
            self.actions.queue_search_result(session.search_id, "mres-one")
        self.assertEqual(raised.exception.code, CODE_NO_ACTIVE_DEVICE)
        self.assertEqual(self.spotify.queued_uris, [])
        self.assertEqual(self.adapter.launches, [])

    def test_transport_capabilities_are_reported_false(self) -> None:
        capabilities = self.service.snapshot(refresh=True).to_dict()["capabilities"]
        self.assertFalse(capabilities["transport"])
        self.assertFalse(capabilities["volume"])
        self.assertFalse(capabilities["mute"])


class DeviceResolutionTests(PlaybackTestCase):
    """Checks 24, 25, 26: stale, gone, and restricted are all refused."""

    def test_an_unknown_handle_is_refused(self) -> None:
        with self.assertRaises(SpotifyPlayerError) as raised:
            self.actions.set_volume(30, "spdev-does-not-exist")
        self.assertEqual(raised.exception.code, CODE_DEVICE_UNKNOWN)

    def test_a_handle_for_a_device_that_disappeared_is_refused(self) -> None:
        stale = self.service.snapshot(refresh=True).devices[0].resource_id
        self.spotify.devices = [device(device_id="dev-other", name="Phone")]
        with self.assertRaises(SpotifyPlayerError) as raised:
            self.actions.transfer(stale)
        self.assertEqual(raised.exception.code, CODE_DEVICE_UNKNOWN)

    def test_there_is_no_fallback_to_matching_a_device_name(self) -> None:
        """Two speakers can share a name; a name is not an identity."""
        import inspect

        source = inspect.getsource(SpotifyPlayerService.resolve_device)
        self.assertNotIn(".name", source.split('"""')[-1])

    def test_a_restricted_device_refuses_every_targeted_action(self) -> None:
        self.spotify.devices = [device(is_restricted=True, supports_volume=False)]
        handle = self.service.snapshot(refresh=True).devices[0].resource_id
        for name, call in (
            ("pause", self.actions.pause),
            ("volume", lambda: self.actions.set_volume(30, handle)),
            ("transfer", lambda: self.actions.transfer(handle)),
        ):
            with self.subTest(action=name):
                with self.assertRaises(SpotifyPlayerError) as raised:
                    call()
                self.assertEqual(raised.exception.code, CODE_DEVICE_RESTRICTED)

    def test_the_device_list_is_refreshed_before_a_targeted_action(self) -> None:
        handle = self.service.snapshot(refresh=True).devices[0].resource_id
        before = len([c for c in self.spotify.calls if c["path"] == "/v1/me/player/devices"])
        self.actions.set_volume(30, handle)
        after = len([c for c in self.spotify.calls if c["path"] == "/v1/me/player/devices"])
        self.assertGreater(after, before)


class TransportTests(PlaybackTestCase):
    """Checks 38, 39, 40: acted on, then looked at."""

    def test_pause_is_verified_against_a_re_read(self) -> None:
        result = self.actions.pause()
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertFalse(result["observed"]["is_playing"])
        self.assertFalse(result["playback"]["is_playing"])

    def test_a_pause_spotify_ignored_is_not_reported_as_applied(self) -> None:
        self.spotify.ignore_writes = True
        result = self.actions.pause()
        self.assertEqual(result["outcome"], OUTCOME_NOT_APPLIED)
        self.assertIn("still playing", result["message"])

    def test_resume_is_verified_against_a_re_read(self) -> None:
        self.spotify.is_playing = False
        result = self.actions.resume()
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertTrue(result["observed"]["is_playing"])

    def test_a_resume_that_did_not_start_anything_says_so(self) -> None:
        self.spotify.is_playing = False
        self.spotify.ignore_writes = True
        result = self.actions.resume()
        self.assertEqual(result["outcome"], OUTCOME_NOT_APPLIED)

    def test_next_reports_the_item_it_observed(self) -> None:
        result = self.actions.skip(True)
        self.assertEqual(result["operation"], "spotify_next")
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["observed"]["track_id"], OTHER_TRACK_ID)

    def test_previous_that_restarts_the_same_track_is_not_a_false_success(self) -> None:
        self.spotify.ignore_writes = True
        result = self.actions.skip(False)
        self.assertEqual(result["operation"], "spotify_previous")
        self.assertEqual(result["outcome"], OUTCOME_PARTIAL)
        self.assertIn("same track", result["message"])

    def test_every_transport_action_re_reads_playback(self) -> None:
        for name, call in (
            ("pause", self.actions.pause),
            ("resume", self.actions.resume),
            ("next", lambda: self.actions.skip(True)),
            ("previous", lambda: self.actions.skip(False)),
        ):
            with self.subTest(action=name):
                self.spotify.calls.clear()
                call()
                reads = [c for c in self.spotify.calls
                         if c["path"] == "/v1/me/player" and c["method"] == "GET"]
                self.assertGreaterEqual(len(reads), 2, "state must be read before and after")

    def test_a_rate_limited_action_is_not_retried(self) -> None:
        self.spotify.fail("/v1/me/player/pause", 429, {"error": {"status": 429}})
        with self.assertRaises(SpotifyPlayerError) as raised:
            self.actions.pause()
        self.assertEqual(raised.exception.code, CODE_RATE_LIMITED)
        self.assertEqual(raised.exception.retry_after_seconds, 7)
        writes = [c for c in self.spotify.calls if c["path"] == "/v1/me/player/pause"]
        self.assertEqual(len(writes), 1)


class VolumeTests(PlaybackTestCase):
    """Checks 41–44: the range is a product decision, and nothing is clamped."""

    def test_zero_and_one_hundred_are_accepted(self) -> None:
        for value in (0, 100):
            with self.subTest(value=value):
                self.assertEqual(clean_volume_percent(value), value)
                result = self.actions.set_volume(value)
                self.assertEqual(result["outcome"], OUTCOME_APPLIED)
                self.assertEqual(result["observed"]["volume_percent"], value)

    def test_out_of_range_values_are_refused_not_clamped(self) -> None:
        for value in (-1, -100, 101, 1000):
            with self.subTest(value=value):
                with self.assertRaises(SpotifyPlayerError) as raised:
                    self.actions.set_volume(value)
                self.assertEqual(raised.exception.code, CODE_INVALID_VOLUME)
        self.assertEqual(
            [c for c in self.spotify.calls if c["path"] == "/v1/me/player/volume"], []
        )

    def test_malformed_values_are_refused(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf"), "50", None, True, False,
                      [50], {"volume_percent": 50}, 12.5):
            with self.subTest(value=repr(value)):
                with self.assertRaises(SpotifyPlayerError) as raised:
                    clean_volume_percent(value)
                self.assertEqual(raised.exception.code, CODE_INVALID_VOLUME)

    def test_a_device_that_does_not_support_volume_refuses(self) -> None:
        """Check 44, from the documented ``supports_volume`` field."""
        self.spotify.devices = [device(supports_volume=False, volume_percent=None)]
        with self.assertRaises(SpotifyPlayerError) as raised:
            self.actions.set_volume(30)
        self.assertEqual(raised.exception.code, CODE_VOLUME_UNSUPPORTED)

    def test_a_volume_change_spotify_ignored_is_not_reported_as_applied(self) -> None:
        """Never `applied`, and the observed number is the one actually seen.

        After the confirmation window this is `partially_applied` rather than
        `not_applied`: Spotify accepted the write, and "it never took effect" is
        a stronger claim than a bounded wait can support. What must never happen
        — and is what this really guards — is the requested value being echoed
        back as though it had been observed.
        """
        self.spotify.ignore_writes = True
        result = self.actions.set_volume(25)
        self.assertNotEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["outcome"], OUTCOME_PARTIAL)
        self.assertEqual(result["requested"]["volume_percent"], 25)
        self.assertEqual(result["observed"]["volume_percent"], 60)
        self.assertFalse(result["observed"]["confirmed"])
        self.assertIn("60%", result["message"])

    def test_an_ignored_volume_change_is_re_read_the_whole_window(self) -> None:
        """The bounded window is spent before giving up, not skipped."""
        from cofferdam.workstation.spotifyplayer.confirm import VOLUME_CONFIRM

        self.spotify.ignore_writes = True
        self.spotify.calls.clear()
        self.actions.set_volume(25)
        reads = [c for c in self.spotify.calls if c["path"] == "/v1/me/player/devices"]
        # One pre-action read, then one per confirmation attempt.
        self.assertGreaterEqual(len(reads), VOLUME_CONFIRM.attempts)

    def test_a_volume_that_settles_after_a_lagging_read_is_applied(self) -> None:
        """The defect real validation found: 50 → 80 reported "device says 50".

        Spotify's devices endpoint is eventually consistent, so the read taken
        microseconds after the write still described the previous level. One read
        was the bug; the fix is to look again, a bounded number of times.
        """
        self.spotify.lag_device_reads = 2
        result = self.actions.set_volume(80)
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["observed"]["volume_percent"], 80)
        self.assertTrue(result["observed"]["confirmed"])

    def test_the_volume_reaches_the_provider_as_a_query_value(self) -> None:
        self.actions.set_volume(25)
        call = [c for c in self.spotify.calls if c["path"] == "/v1/me/player/volume"][-1]
        self.assertEqual(call["method"], "PUT")
        self.assertEqual(call["query"]["volume_percent"], "25")
        self.assertEqual(call["query"]["device_id"], "dev-workstation")


class MuteTests(PlaybackTestCase):
    """Checks 45, 46, 47: mute is volume zero, and unmute never invents."""

    def test_mute_sets_the_volume_to_zero_and_says_so(self) -> None:
        result = self.actions.set_muted(True)
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["observed"]["volume_percent"], 0)
        self.assertIn("volume to zero", result["message"])
        self.assertNotIn("Spotify muted itself", result["message"])

    def test_the_operation_is_not_named_as_a_spotify_mute(self) -> None:
        result = self.actions.set_muted(True)
        self.assertEqual(result["operation"], "spotify_set_mute")
        self.assertTrue(result["playback"]["muted_by_cofferdam"])
        self.assertNotIn("muted", result["playback"])

    def test_unmute_restores_only_the_level_cofferdam_recorded(self) -> None:
        self.actions.set_muted(True)
        result = self.actions.set_muted(False)
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["observed"]["volume_percent"], 60)

    def test_unmute_with_no_recorded_level_is_refused_rather_than_guessed(self) -> None:
        """Check 47: after a restart there is nothing to restore *to*."""
        self.spotify.devices = [device(volume_percent=0)]
        with self.assertRaises(SpotifyPlayerError) as raised:
            self.actions.set_muted(False)
        self.assertEqual(raised.exception.code, CODE_UNMUTE_UNKNOWN)
        self.assertEqual(
            [c for c in self.spotify.calls if c["path"] == "/v1/me/player/volume"], []
        )

    def test_a_mute_undone_from_the_spotify_app_drops_the_stale_record(self) -> None:
        self.actions.set_muted(True)
        # Somebody turned it back up in Spotify itself.
        self.spotify.devices[0]["volume_percent"] = 45
        snapshot = self.service.snapshot(refresh=True)
        self.assertFalse(snapshot.muted_by_cofferdam)
        with self.assertRaises(SpotifyPlayerError) as raised:
            self.actions.set_muted(False)
        self.assertEqual(raised.exception.code, CODE_UNMUTE_UNKNOWN)

    def test_setting_a_non_zero_volume_ends_the_mute_record(self) -> None:
        self.actions.set_muted(True)
        self.actions.set_volume(40)
        self.assertIsNone(
            self.service.mute_state.restore_value(
                self.service.snapshot(refresh=True).devices[0].resource_id
            )
        )

    def test_mute_is_refused_on_a_device_without_volume_control(self) -> None:
        self.spotify.devices = [device(supports_volume=False, volume_percent=None)]
        with self.assertRaises(SpotifyPlayerError) as raised:
            self.actions.set_muted(True)
        self.assertEqual(raised.exception.code, CODE_VOLUME_UNSUPPORTED)

    def test_muted_must_be_a_boolean(self) -> None:
        for value in ("true", 1, 0, None, []):
            with self.subTest(value=repr(value)):
                with self.assertRaises(SpotifyPlayerError):
                    self.actions.set_muted(value)


class TransferTests(PlaybackTestCase):
    """Checks 48, 49: revalidated, and never a claim about PipeWire."""

    def setUp(self) -> None:
        super().setUp()
        self.spotify.devices = [
            device(device_id="dev-workstation", name="Workstation", is_active=True),
            device(device_id="dev-kitchen", name="Kitchen", is_active=False, volume_percent=20),
        ]

    def _kitchen(self) -> str:
        snapshot = self.service.snapshot(refresh=True)
        return [d for d in snapshot.devices if d.name == "Kitchen"][0].resource_id

    def test_transfer_moves_the_active_device_and_observes_it(self) -> None:
        result = self.actions.transfer(self._kitchen())
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["observed"]["active_device_resource_id"], self._kitchen())

    def test_transfer_revalidates_the_device_before_acting(self) -> None:
        handle = self._kitchen()
        self.spotify.calls.clear()
        self.actions.transfer(handle)
        first = self.spotify.calls[0]
        self.assertEqual(first["path"], "/v1/me/player/devices")
        self.assertEqual(first["method"], "GET")

    def test_transfer_says_the_computer_audio_output_did_not_change(self) -> None:
        result = self.actions.transfer(self._kitchen())
        self.assertTrue(result["system_audio_unchanged"])
        # The message describes Spotify and only Spotify. The one place this
        # response mentions the computer's output is the *limitation* that says
        # it did not change — which is the honest half, not a claim.
        message = result["message"].lower()
        for claim in ("pipewire", "wireplumber", "system output", "audio output", "speaker"):
            self.assertNotIn(claim, message)
        limitations = " ".join(result["playback"]["limitations"]).lower()
        self.assertIn("does not change this computer", limitations)

    def test_the_play_flag_is_sent_explicitly(self) -> None:
        self.actions.transfer(self._kitchen(), play=False)
        call = [c for c in self.spotify.calls
                if c["path"] == "/v1/me/player" and c["method"] == "PUT"][-1]
        body = json.loads(call["body"].decode("utf-8"))
        self.assertEqual(body, {"device_ids": ["dev-kitchen"], "play": False})

    def test_a_transfer_spotify_ignored_is_not_reported_as_applied(self) -> None:
        handle = self._kitchen()
        self.spotify.ignore_writes = True
        result = self.actions.transfer(handle)
        self.assertNotEqual(result["outcome"], OUTCOME_APPLIED)


class SearchResultPlaybackTests(PlaybackTestCase):
    """Checks 30–37: only a verified track, and only from the server's session."""

    def test_playing_a_verified_track_plays_that_exact_track(self) -> None:
        session = self.search_session()
        result = self.actions.play_search_result(session.search_id, "mres-one")
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["requested"]["track_id"], TRACK_ID)
        self.assertEqual(result["observed"]["track_id"], TRACK_ID)

    def test_the_uri_is_rebuilt_by_the_server_from_its_own_session(self) -> None:
        session = self.search_session()
        self.actions.play_search_result(session.search_id, "mres-one")
        call = [c for c in self.spotify.calls if c["path"] == "/v1/me/player/play"][-1]
        body = json.loads(call["body"].decode("utf-8"))
        self.assertEqual(body["uris"], ["spotify:track:" + TRACK_ID])

    def test_a_non_track_result_is_refused(self) -> None:
        """Check 52's server half: an album is a context, not a track."""
        for item_type in ("album", "artist", "playlist", "show", "episode"):
            with self.subTest(item_type=item_type):
                session = self.search_session(item_type=item_type)
                with self.assertRaises(SpotifyPlayerError) as raised:
                    self.actions.play_search_result(session.search_id, "mres-one")
                self.assertEqual(raised.exception.code, CODE_RESULT_NOT_PLAYABLE)

    def test_a_youtube_result_cannot_be_played_through_spotify(self) -> None:
        """Check 34: the cross-provider guard the search layer already enforces."""
        session = self.search_session(provider_id="youtube", item_type="video")
        with self.assertRaises(Exception) as raised:
            self.actions.play_search_result(session.search_id, "mres-one")
        self.assertNotIsInstance(raised.exception, SpotifyPlayerError)
        self.assertEqual(
            [c for c in self.spotify.calls if c["path"] == "/v1/me/player/play"], []
        )

    def test_an_expired_search_session_cannot_play(self) -> None:
        """Check 33."""
        import time as _time

        session = self.search_session(now=_time.time() - 100000)
        with self.assertRaises(Exception):
            self.actions.play_search_result(session.search_id, "mres-one")
        self.assertEqual(
            [c for c in self.spotify.calls if c["path"] == "/v1/me/player/play"], []
        )

    def test_an_unknown_result_id_cannot_play(self) -> None:
        session = self.search_session()
        with self.assertRaises(Exception):
            self.actions.play_search_result(session.search_id, "mres-not-in-this-search")

    def test_a_mismatched_observation_is_not_reported_as_full_success(self) -> None:
        """Check 36. Spotify accepted the request and something else is playing."""
        session = self.search_session()
        self.spotify.ignore_writes = True
        self.spotify.item = track_item(OTHER_TRACK_ID, name="Something Else")
        result = self.actions.play_search_result(session.search_id, "mres-one")
        self.assertEqual(result["outcome"], OUTCOME_NOT_APPLIED)
        self.assertIn("something other than", result["message"])

    def test_an_unobservable_start_is_partial_rather_than_applied(self) -> None:
        session = self.search_session()
        self.spotify.ignore_writes = True
        self.spotify.playback_available = False
        result = self.actions.play_search_result(session.search_id, "mres-one")
        self.assertEqual(result["outcome"], OUTCOME_PARTIAL)

    def test_queueing_adds_the_track_and_claims_nothing_about_playback(self) -> None:
        """Check 37."""
        session = self.search_session()
        result = self.actions.queue_search_result(session.search_id, "mres-one")
        self.assertEqual(result["outcome"], OUTCOME_ACCEPTED)
        self.assertEqual(self.spotify.queued_uris, ["spotify:track:" + TRACK_ID])
        self.assertIn("has not changed", result["message"])
        for claim in ("now playing", "started playing", "is playing the track"):
            self.assertNotIn(claim, result["message"].lower())

    def test_queueing_does_not_change_what_is_playing(self) -> None:
        session = self.search_session()
        before = self.service.snapshot(refresh=True).now_playing.track_id
        result = self.actions.queue_search_result(session.search_id, "mres-one")
        self.assertEqual(result["observed"]["track_id"], before)

    def test_queueing_a_non_track_is_refused(self) -> None:
        session = self.search_session(item_type="album")
        with self.assertRaises(SpotifyPlayerError) as raised:
            self.actions.queue_search_result(session.search_id, "mres-one")
        self.assertEqual(raised.exception.code, CODE_RESULT_NOT_PLAYABLE)


class DisconnectActionTests(PlaybackTestCase):
    def test_disconnect_removes_the_local_authorization(self) -> None:
        result = self.actions.disconnect()
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["observed"]["status"], STATUS_DISCONNECTED)
        self.assertFalse(self.tokens.path.exists())

    def test_disconnect_does_not_claim_provider_revocation(self) -> None:
        result = self.actions.disconnect()
        self.assertFalse(result["revoked_at_provider"])
        self.assertIn("did not revoke", result["message"])

    def test_disconnect_drops_the_mute_restore_state_too(self) -> None:
        self.actions.set_muted(True)
        self.actions.disconnect()
        self.assertIsNone(self.service.mute_state.restore_value("spdev-anything"))

    def test_disconnect_returns_no_token(self) -> None:
        blob = json.dumps(self.actions.disconnect(), ensure_ascii=False)
        for secret in ALL_FAKE_OAUTH_SECRETS:
            self.assertNotIn(secret, blob)


class NetworkPolicyTests(PlaybackTestCase):
    """Fixed hosts, one refresh per operation, and no redirect to carry a token."""

    def test_only_the_two_official_hosts_are_ever_contacted(self) -> None:
        self.actions.pause()
        self.actions.set_volume(30)
        session = self.search_session()
        self.actions.queue_search_result(session.search_id, "mres-one")
        # `accounts.` for the token, `api.` for everything else. Both are module
        # constants, and there is no third.
        self.assertLessEqual(
            {call["host"] for call in self.spotify.calls},
            {"api.spotify.com", "accounts.spotify.com"},
        )

    def test_the_bearer_token_goes_only_to_the_api_host(self) -> None:
        self.actions.pause()
        for call in self.spotify.calls:
            if "Authorization" in call["headers"]:
                self.assertEqual(call["host"], "api.spotify.com")

    def test_the_transport_follows_no_redirects(self) -> None:
        """Structural: with no hop, an Authorization header cannot be forwarded."""
        import inspect

        from cofferdam.workstation.mediasearch import transport

        source = inspect.getsource(transport)
        self.assertIn("redirect", source.lower())
        self.assertNotIn("HTTPRedirectHandler()", source)

    def test_an_expired_access_token_is_refreshed_at_most_once(self) -> None:
        """A 401 that survives one refresh stands, rather than becoming a loop.

        A retry loop here would turn a rejected authorization into a burst of
        requests against an account that has already said no — against a
        provider that rate limits over a rolling 30-second window.
        """
        self.spotify.fail("/v1/me/player/devices", 401)
        snapshot = self.service.snapshot(refresh=True)
        self.assertNotEqual(snapshot.connection["status"], STATUS_CONNECTED)
        self.assertFalse(snapshot.playback_available)
        # One refresh for the missing access token, one for the 401 retry.
        self.assertLessEqual(self.spotify.token_calls, 2)
        attempts = [c for c in self.spotify.calls if c["path"] == "/v1/me/player/devices"]
        self.assertEqual(len(attempts), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
