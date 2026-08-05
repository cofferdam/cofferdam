"""Cold start, eventual consistency, and response ordering (M2D.1 regressions).

Every test here corresponds to something that actually went wrong on a phone,
against a real Spotify account, on 2026-08-05. The diagnosis is in
``docs/SPOTIFY_PLAYBACK.md``; these are the regressions.

Three verified causes, none of which the original tests could have caught,
because the original fake was perfectly consistent and the original harness had
no clock skew:

1. **Spotify closed.** No device existed, so Play now refused. Nothing launched
   Spotify, though Cofferdam has had an allowlisted launcher since M2B3A.
2. **Spotify open but idle.** The device existed with ``is_active`` false, and
   the old code refused that too — which is exactly why "Open in Spotify, then
   Play now" was a working workaround. The workaround was the diagnosis.
3. **Eventual consistency.** Spotify's player endpoints do not serve a write
   back immediately, so the single read taken microseconds after a write
   frequently still described the previous world. A successful volume change
   reported "the device reports 50%", and a successful play reported "playing
   something other than the track you chose".

The waits below are bounded-but-instant: attempt *counts* are real and asserted
on, and only the sleeping is removed. A test that actually slept would be
measuring ``time.sleep``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cofferdam.workstation.config import load_config
from cofferdam.workstation.mediasearch.credentials import CredentialStore
from cofferdam.workstation.mediasearch.results import (
    MediaResult,
    MediaSearchOutcome,
    ProviderItem,
)
from cofferdam.workstation.mediasearch.sessions import SearchSessionStore
from cofferdam.workstation.spotifyplayer.actions import (
    OUTCOME_APPLIED,
    OUTCOME_NOT_APPLIED,
    OUTCOME_PARTIAL,
    SpotifyActionExecutor,
)
from cofferdam.workstation.spotifyplayer.client import SpotifyPlayerClient
from cofferdam.workstation.spotifyplayer.coldstart import (
    SPOTIFY_APPLICATION_KEY,
    DeviceRecovery,
    SpotifyLauncher,
    eligible_devices,
)
from cofferdam.workstation.spotifyplayer.confirm import (
    DEVICE_APPEARANCE,
    PLAYBACK_CONFIRM,
    ConfirmWindow,
    confirm,
)
from cofferdam.workstation.spotifyplayer.errors import (
    CODE_DEVICE_AMBIGUOUS,
    CODE_LAUNCH_FAILED,
    CODE_NO_DEVICE_AFTER_LAUNCH,
    SpotifyPlayerError,
)
from cofferdam.workstation.spotifyplayer.progress import (
    PHASE_ACTIVATING,
    PHASE_LAUNCHING,
    PHASE_STARTING,
    PHASE_VERIFYING,
    PHASE_WAITING_FOR_DEVICE,
)
from cofferdam.workstation.spotifyplayer.service import SpotifyPlayerService
from cofferdam.workstation.spotifyplayer.tokens import TokenStore

from ._mediasearch_doubles import write_credentials
from ._spotifyplayer_doubles import (
    ALL_FAKE_OAUTH_SECRETS,
    OTHER_TRACK_ID,
    TRACK_ID,
    FakeApplicationAdapter,
    FakeSpotify,
    device,
    instant_recovery,
    track_item,
    write_user_tokens,
)


class ColdStartTestCase(unittest.TestCase):
    """A connected account whose Spotify desktop application is closed."""

    devices = ()
    playback_available = False

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = load_config(home=Path(self._tmp.name))
        self.config.ensure_dirs()
        write_credentials(self.config, youtube=False)
        write_user_tokens(self.config)

        # Copied, not shared. These are declared at class level and the fake
        # mutates them in place — a transfer flips `is_active`, a volume write
        # changes `volume_percent` — so passing the originals would let one test
        # method's writes leak into the next one's fixture.
        self.spotify = FakeSpotify(
            devices=[dict(entry) for entry in self.devices],
            playback_available=self.playback_available,
            is_playing=self.playback_available,
        )
        self.tokens = TokenStore(self.config)
        self.service = SpotifyPlayerService(
            self.config,
            CredentialStore(self.config),
            token_store=self.tokens,
            client=SpotifyPlayerClient(lambda: "test-client-id", self.tokens, request=self.spotify),
            cache_seconds=0.0,
        )
        self.sessions = SearchSessionStore()
        self.adapter = FakeApplicationAdapter(self.spotify)
        self.actions = SpotifyActionExecutor(
            self.service,
            self.sessions,
            recovery=instant_recovery(self.service, self.adapter),
            sleeper=lambda seconds: None,
        )
        self.session = self._session()

    def _session(self):
        result = MediaResult(
            provider_id="spotify", result_id="mres-one", result_type="track",
            title="Gönül Dağı", creators=("Neşet Ertaş",),
        )
        return self.sessions.create(
            MediaSearchOutcome(
                provider_id="spotify", query="Gönül Dağı", results=(result,),
                items=(ProviderItem(provider_id="spotify", item_type="track", item_id=TRACK_ID),),
            )
        )

    def play(self, device_resource_id=None):
        return self.actions.play_search_result(
            self.session.search_id, "mres-one", device_resource_id
        )

    def phases(self, result) -> list:
        return [step["phase"] for step in result.get("progress", [])]


class LaunchTests(ColdStartTestCase):
    """(1) Spotify closed + Play now launches Spotify."""

    def test_play_now_launches_spotify_and_starts_the_track(self) -> None:
        result = self.play()
        self.assertEqual(self.adapter.launches, [SPOTIFY_APPLICATION_KEY])
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["observed"]["track_id"], TRACK_ID)

    def test_the_launch_uses_the_allowlisted_application_key(self) -> None:
        """A logical key, not a program name. The adapter owns the mapping."""
        self.play()
        self.assertEqual(self.adapter.launches, ["spotify"])

    def test_no_shell_and_no_search_page_is_used_as_a_substitute(self) -> None:
        import inspect

        from cofferdam.workstation.spotifyplayer import coldstart

        import re

        from ._runtime_doubles import code_only

        # Docstrings *and* comments stripped: the module explains at length what
        # it refuses to do, and a bare substring scan would trip on its own
        # reasoning rather than on any code.
        source = re.sub(r'""".*?"""', "", inspect.getsource(coldstart), flags=re.S)
        source = code_only(source)
        for forbidden in ("subprocess", "os.system", "shell=True", "open_url",
                          "open.spotify.com", "Popen"):
            self.assertNotIn(forbidden, source)

    def test_the_phases_are_reported_in_order(self) -> None:
        result = self.play()
        phases = self.phases(result)
        self.assertEqual(
            phases[:2], [PHASE_LAUNCHING, PHASE_WAITING_FOR_DEVICE]
        )
        self.assertIn(PHASE_STARTING, phases)
        self.assertIn(PHASE_VERIFYING, phases)
        self.assertLess(phases.index(PHASE_STARTING), phases.index(PHASE_VERIFYING))

    def test_the_response_carries_a_correlation_id(self) -> None:
        result = self.play()
        self.assertTrue(result["correlation_id"].startswith("spop-"))

    def test_spotify_is_launched_at_most_once_per_recovery(self) -> None:
        """(15) One bounded launch attempt. Never a loop."""
        self.spotify.launch_delay_reads = 3
        self.play()
        self.assertEqual(len(self.adapter.launches), 1)

    def test_a_failed_launch_is_a_truthful_refusal(self) -> None:
        adapter = FakeApplicationAdapter(self.spotify, fail=True)
        actions = SpotifyActionExecutor(
            self.service, self.sessions,
            recovery=instant_recovery(self.service, adapter), sleeper=lambda s: None,
        )
        with self.assertRaises(SpotifyPlayerError) as raised:
            actions.play_search_result(self.session.search_id, "mres-one")
        self.assertEqual(raised.exception.code, CODE_LAUNCH_FAILED)
        self.assertEqual(len(adapter.launches), 1)


class DelayedDeviceTests(ColdStartTestCase):
    """(2) The device appears after a delayed poll, and the track still starts."""

    def test_a_device_that_registers_after_several_polls_still_plays(self) -> None:
        self.spotify.launch_delay_reads = 4
        result = self.play()
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["observed"]["track_id"], TRACK_ID)

    def test_the_device_list_is_polled_until_it_appears(self) -> None:
        self.spotify.launch_delay_reads = 4
        self.spotify.calls.clear()
        self.play()
        reads = [c for c in self.spotify.calls if c["path"] == "/v1/me/player/devices"]
        self.assertGreaterEqual(len(reads), 4)

    def test_a_device_that_never_appears_times_out_truthfully(self) -> None:
        """(3) Truthful, and it does not claim playback started."""
        self.spotify.launch_delay_reads = 999  # never within the window
        with self.assertRaises(SpotifyPlayerError) as raised:
            self.play()
        self.assertEqual(raised.exception.code, CODE_NO_DEVICE_AFTER_LAUNCH)
        # It does not tell the user to open Spotify — Cofferdam already did.
        self.assertNotIn("open Spotify on this computer", raised.exception.detail or "")
        self.assertEqual(
            [c for c in self.spotify.calls if c["path"] == "/v1/me/player/play"], []
        )

    def test_the_appearance_wait_is_bounded(self) -> None:
        """(16) Recovery never creates an infinite retry loop."""
        self.spotify.launch_delay_reads = 999
        recovery = instant_recovery(self.service, self.adapter, appearance_attempts=6)
        actions = SpotifyActionExecutor(
            self.service, self.sessions, recovery=recovery, sleeper=lambda s: None
        )
        self.spotify.calls.clear()
        with self.assertRaises(SpotifyPlayerError):
            actions.play_search_result(self.session.search_id, "mres-one")
        reads = [c for c in self.spotify.calls if c["path"] == "/v1/me/player/devices"]
        # One pre-action snapshot read, then exactly the window's attempts.
        self.assertLessEqual(len(reads), 8)

    def test_the_shipped_appearance_window_is_bounded_and_sane(self) -> None:
        self.assertLessEqual(DEVICE_APPEARANCE.timeout_seconds, 60)
        self.assertGreaterEqual(DEVICE_APPEARANCE.timeout_seconds, 5)


class AmbiguousDeviceTests(ColdStartTestCase):
    """(4) Several eligible devices and none active: ask, never guess."""

    devices = (
        device(device_id="dev-kitchen", name="Kitchen", is_active=False),
        device(device_id="dev-phone", name="Phone", is_active=False),
    )

    def test_two_idle_devices_are_not_guessed_between(self) -> None:
        with self.assertRaises(SpotifyPlayerError) as raised:
            self.play()
        self.assertEqual(raised.exception.code, CODE_DEVICE_AMBIGUOUS)
        self.assertEqual(
            [c for c in self.spotify.calls if c["path"] == "/v1/me/player/play"], []
        )

    def test_the_refusal_names_the_choices(self) -> None:
        with self.assertRaises(SpotifyPlayerError) as raised:
            self.play()
        self.assertIn("Kitchen", raised.exception.detail)
        self.assertIn("Phone", raised.exception.detail)

    def test_nothing_is_launched_when_devices_already_exist(self) -> None:
        with self.assertRaises(SpotifyPlayerError):
            self.play()
        self.assertEqual(self.adapter.launches, [])

    def test_naming_one_of_them_resolves_the_ambiguity(self) -> None:
        snapshot = self.service.snapshot(refresh=True)
        kitchen = [d for d in snapshot.devices if d.name == "Kitchen"][0]
        result = self.play(kitchen.resource_id)
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)

    def test_an_active_device_beats_any_number_of_idle_ones(self) -> None:
        self.spotify.devices.append(device(device_id="dev-ws", name="Workstation", is_active=True))
        result = self.play()
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)

    def test_a_restricted_device_is_not_an_eligible_candidate(self) -> None:
        self.spotify.devices = [
            device(device_id="dev-kitchen", name="Kitchen", is_active=False),
            device(device_id="dev-car", name="Car", is_active=False, is_restricted=True),
        ]
        # One eligible device, so no ambiguity: the restricted one is not a
        # candidate for anything, including for being counted.
        result = self.play()
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)

    def test_eligibility_excludes_restricted_devices(self) -> None:
        self.spotify.devices = [device(is_restricted=True, is_active=False)]
        snapshot = self.service.snapshot(refresh=True)
        self.assertEqual(eligible_devices(snapshot), [])


class ActivationTests(ColdStartTestCase):
    """(5) An inactive device is transferred to before the first playback."""

    devices = (device(device_id="dev-workstation", name="Workstation", is_active=False),)
    playback_available = True

    def test_the_single_idle_device_is_activated_then_played(self) -> None:
        result = self.play()
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertIn(PHASE_ACTIVATING, self.phases(result))

    def test_the_transfer_happens_before_the_play(self) -> None:
        self.spotify.calls.clear()
        self.play()
        paths = [(c["method"], c["path"]) for c in self.spotify.calls]
        transfer = paths.index(("PUT", "/v1/me/player"))
        play = paths.index(("PUT", "/v1/me/player/play"))
        self.assertLess(transfer, play)

    def test_the_transfer_does_not_start_whatever_was_loaded(self) -> None:
        """``play=False``: this is "put Spotify here", not "start something"."""
        self.play()
        call = [c for c in self.spotify.calls
                if c["path"] == "/v1/me/player" and c["method"] == "PUT"][0]
        self.assertIs(json.loads(call["body"].decode("utf-8"))["play"], False)

    def test_an_already_active_device_is_not_transferred(self) -> None:
        self.spotify.devices = [device(is_active=True)]
        result = self.play()
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertNotIn(PHASE_ACTIVATING, self.phases(result))
        self.assertEqual(
            [c for c in self.spotify.calls
             if c["path"] == "/v1/me/player" and c["method"] == "PUT"],
            [],
        )

    def test_first_play_now_needs_no_prior_open_in_spotify(self) -> None:
        """(6) The workaround that used to be required is required no longer."""
        result = self.play()
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        # No launch, no second attempt: one deliberate press did it.
        self.assertEqual(self.adapter.launches, [])
        plays = [c for c in self.spotify.calls if c["path"] == "/v1/me/player/play"]
        self.assertEqual(len(plays), 1)


class PlaybackConfirmationTests(ColdStartTestCase):
    """(7) and (8): a delayed observation confirms; a wrong one never does."""

    devices = (device(is_active=True),)
    playback_available = True

    def test_a_playback_state_that_lags_several_reads_eventually_confirms(self) -> None:
        self.spotify.lag_playback_reads = 3
        self.spotify.item = track_item(OTHER_TRACK_ID, name="Previous")
        result = self.play()
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["observed"]["track_id"], TRACK_ID)
        self.assertTrue(result["observed"]["confirmed"])

    def test_playback_is_re_read_more_than_once(self) -> None:
        self.spotify.lag_playback_reads = 3
        # Something *else* is playing, so the very first read cannot match and
        # the loop has to actually loop.
        self.spotify.item = track_item(OTHER_TRACK_ID, name="Previous")
        self.spotify.calls.clear()
        self.play()
        reads = [c for c in self.spotify.calls
                 if c["path"] == "/v1/me/player" and c["method"] == "GET"]
        self.assertGreaterEqual(len(reads), 3)

    def test_a_track_that_never_matches_is_not_a_full_success(self) -> None:
        """(8). Spotify accepted it and something else is playing."""
        self.spotify.ignore_writes = True
        self.spotify.item = track_item(OTHER_TRACK_ID, name="Something Else")
        result = self.play()
        self.assertEqual(result["outcome"], OUTCOME_NOT_APPLIED)
        self.assertFalse(result["observed"]["confirmed"])
        self.assertIn("try again", result["message"])

    def test_a_track_that_never_appears_is_partial_with_a_retry(self) -> None:
        self.spotify.ignore_writes = True
        self.spotify.playback_available = False
        result = self.play()
        self.assertEqual(result["outcome"], OUTCOME_PARTIAL)
        self.assertIn("try again", result["message"])

    def test_the_confirmation_window_is_bounded(self) -> None:
        self.assertLessEqual(PLAYBACK_CONFIRM.timeout_seconds, 10)
        self.assertGreater(PLAYBACK_CONFIRM.attempts, 1)

    def test_repeated_play_requests_are_never_used_as_the_fix(self) -> None:
        """The brief's explicit anti-pattern: do not just send it again."""
        self.spotify.ignore_writes = True
        self.spotify.item = track_item(OTHER_TRACK_ID)
        self.play()
        plays = [c for c in self.spotify.calls if c["path"] == "/v1/me/player/play"]
        self.assertEqual(len(plays), 1, "playback must be re-read, never re-sent")


class VolumeConfirmationTests(ColdStartTestCase):
    """(9) and (10): a lagging volume confirms; a stuck one does not lie."""

    devices = (device(is_active=True, volume_percent=50),)
    playback_available = True

    def test_a_volume_that_lags_one_read_confirms(self) -> None:
        self.spotify.lag_device_reads = 1
        result = self.actions.set_volume(80)
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["observed"]["volume_percent"], 80)

    def test_a_volume_that_lags_several_reads_confirms(self) -> None:
        self.spotify.lag_device_reads = 3
        result = self.actions.set_volume(70)
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["observed"]["volume_percent"], 70)

    def test_a_volume_that_never_arrives_does_not_report_success(self) -> None:
        """(10). Timeout is partial, and never `applied`."""
        self.spotify.ignore_writes = True
        result = self.actions.set_volume(70)
        self.assertNotEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["observed"]["volume_percent"], 50)
        self.assertFalse(result["observed"]["confirmed"])

    def test_the_observed_value_is_never_the_requested_one_by_construction(self) -> None:
        self.spotify.ignore_writes = True
        for wanted in (0, 25, 100):
            with self.subTest(volume=wanted):
                result = self.actions.set_volume(wanted)
                self.assertEqual(result["requested"]["volume_percent"], wanted)
                self.assertEqual(result["observed"]["volume_percent"], 50)

    def test_the_sequence_from_validation_ends_on_the_last_value(self) -> None:
        """(13) 50 → 80 → 70, each confirmed, ending verified at 70."""
        self.spotify.lag_device_reads = 2
        seen = []
        for wanted in (50, 80, 70):
            result = self.actions.set_volume(wanted)
            self.assertEqual(result["outcome"], OUTCOME_APPLIED)
            seen.append(result["observed"]["volume_percent"])
        self.assertEqual(seen, [50, 80, 70])
        self.assertEqual(
            self.service.snapshot(refresh=True).devices[0].volume_percent, 70
        )

    def test_mute_confirmation_is_bounded_the_same_way(self) -> None:
        """And the restore level survives a lagging mute.

        The first version of the confirmation loop lost it: each read inside the
        window built a snapshot, a snapshot still showing the old non-zero volume
        looked exactly like "the user turned it back up in the Spotify app", and
        the level to restore was dropped mid-mute. Unmute then refused as though
        Cofferdam had never muted anything.
        """
        self.spotify.lag_device_reads = 2
        muted = self.actions.set_muted(True)
        self.assertTrue(muted["playback"]["restore_volume_known"])
        self.assertTrue(muted["playback"]["muted_by_cofferdam"])
        self.assertEqual(muted["outcome"], OUTCOME_APPLIED)
        self.assertEqual(muted["observed"]["volume_percent"], 0)
        restored = self.actions.set_muted(False)
        self.assertEqual(restored["outcome"], OUTCOME_APPLIED)
        self.assertEqual(restored["observed"]["volume_percent"], 50)


class TransportConfirmationTests(ColdStartTestCase):
    """Pause, resume and skip get the same treatment, for the same reason."""

    devices = (device(is_active=True),)
    playback_available = True

    def test_pause_is_confirmed_rather_than_read_once(self) -> None:
        self.spotify.calls.clear()
        result = self.actions.pause()
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)

    def test_a_lagging_skip_still_confirms(self) -> None:
        self.spotify.lag_playback_reads = 2
        result = self.actions.skip(True)
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)

    def test_queue_behaviour_is_unchanged(self) -> None:
        """(18) Queueing still claims acceptance and nothing about playback."""
        result = self.actions.queue_search_result(self.session.search_id, "mres-one")
        self.assertEqual(result["outcome"], "accepted_by_provider")
        self.assertEqual(self.spotify.queued_uris, ["spotify:track:" + TRACK_ID])
        self.assertIn("has not changed", result["message"])


class ProgressPrivacyTests(ColdStartTestCase):
    """(17) No track title, artist or token anywhere near the diagnostics."""

    devices = (device(is_active=True),)
    playback_available = True

    def test_the_phase_log_carries_no_personal_data(self) -> None:
        result = self.play()
        blob = json.dumps(result.get("progress", []), ensure_ascii=False)
        for personal in ("Gönül", "Ertaş", "Test Listener", TRACK_ID, "dev-workstation"):
            self.assertNotIn(personal, blob)
        for secret in ALL_FAKE_OAUTH_SECRETS:
            self.assertNotIn(secret, blob)

    def test_the_activity_view_carries_no_personal_data(self) -> None:
        self.play()
        blob = json.dumps(self.actions.activity.snapshot(), ensure_ascii=False)
        for personal in ("Gönül", "Ertaş", "Test Listener", TRACK_ID, "dev-workstation"):
            self.assertNotIn(personal, blob)

    def test_the_activity_view_has_a_closed_shape(self) -> None:
        self.play()
        self.assertEqual(
            set(self.actions.activity.snapshot()),
            {"active", "operation", "phase", "label", "correlation_id", "elapsed_ms",
             "started_at"},
        )

    def test_the_phase_vocabulary_is_closed(self) -> None:
        from cofferdam.workstation.spotifyplayer.progress import PHASES, OperationProgress

        progress = OperationProgress()
        progress.enter("something_invented")
        self.assertEqual(progress.steps, [])
        progress.enter(PHASES[0])
        self.assertEqual(len(progress.steps), 1)

    def test_the_phase_log_is_bounded(self) -> None:
        from cofferdam.workstation.spotifyplayer.progress import (
            MAX_STEPS,
            PHASE_VERIFYING,
            OperationProgress,
        )

        progress = OperationProgress()
        for _ in range(MAX_STEPS * 3):
            progress.enter(PHASE_VERIFYING)
        self.assertEqual(len(progress.steps), MAX_STEPS)

    def test_the_correlation_id_reveals_nothing(self) -> None:
        result = self.play()
        correlation = result["correlation_id"]
        self.assertRegex(correlation, r"^spop-[0-9a-f]{12}$")
        second = self.play()["correlation_id"]
        self.assertNotEqual(correlation, second)

    def test_nothing_in_the_new_modules_logs(self) -> None:
        import inspect
        import re

        from cofferdam.workstation.spotifyplayer import coldstart, confirm, progress

        for module in (coldstart, confirm, progress):
            source = inspect.getsource(module)
            with self.subTest(module=module.__name__):
                self.assertIsNone(re.search(r"^\s*print\(", source, re.MULTILINE))
                self.assertNotIn("import logging", source)


class ConfirmHelperTests(unittest.TestCase):
    """The primitive, on its own: immediate first read, bounded, honest."""

    def test_the_first_read_is_immediate_and_costs_no_sleep(self) -> None:
        slept = []
        value, matched = confirm(
            lambda: 42, lambda v: v == 42, ConfirmWindow(5, 1.0), slept.append
        )
        self.assertTrue(matched)
        self.assertEqual(value, 42)
        self.assertEqual(slept, [], "a value that is already right must not wait")

    def test_it_stops_after_exactly_the_attempt_count(self) -> None:
        reads = []
        _value, matched = confirm(
            lambda: reads.append(1), lambda v: False, ConfirmWindow(4, 0.0), lambda s: None
        )
        self.assertFalse(matched)
        self.assertEqual(len(reads), 4)

    def test_it_returns_the_last_value_it_actually_read(self) -> None:
        values = iter([1, 2, 3, 4])
        value, matched = confirm(
            lambda: next(values), lambda v: v == 99, ConfirmWindow(4, 0.0), lambda s: None
        )
        self.assertFalse(matched)
        self.assertEqual(value, 4, "never a requested value, always an observed one")

    def test_a_single_attempt_window_never_sleeps(self) -> None:
        slept = []
        confirm(lambda: 0, lambda v: False, ConfirmWindow(1, 5.0), slept.append)
        self.assertEqual(slept, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
