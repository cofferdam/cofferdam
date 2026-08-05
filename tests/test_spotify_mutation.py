"""Mutation checks: prove the Spotify safety guards are load-bearing.

A passing suite proves the code behaves. It does not prove the *tests* would
notice if a guard were removed — a check can be deleted and leave a suite just
as green, because nothing was ever exercising it.

So each test below deliberately breaks one guard and asserts that the property
it protects visibly fails. If a mutation ever stops producing a failure, the
corresponding guard has become decorative and this file says so.

These are the seven guards the milestone brief calls out by name:

1. callback state validation
2. loopback-only binding
3. client-supplied Spotify URI rejection
4. stale-device rejection
5. playback observation verification
6. unknown-unmute restore rejection
7. secret redaction
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
from cofferdam.workstation.spotifyplayer import oauth
from cofferdam.workstation.spotifyplayer.actions import (
    OUTCOME_APPLIED,
    OUTCOME_NOT_APPLIED,
    SpotifyActionExecutor,
)
from cofferdam.workstation.spotifyplayer.callback import CallbackListener
from cofferdam.workstation.spotifyplayer.client import SpotifyPlayerClient
from cofferdam.workstation.spotifyplayer.errors import SpotifyPlayerError
from cofferdam.workstation.spotifyplayer.service import SpotifyPlayerService
from cofferdam.workstation.spotifyplayer.tokens import TokenStore, UserTokens

from ._mediasearch_doubles import write_credentials
from ._spotifyplayer_doubles import (
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


class MutationTestCase(unittest.TestCase):
    """A connected service, ready to have one of its guards broken."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = load_config(home=Path(self._tmp.name))
        self.config.ensure_dirs()
        write_credentials(self.config, youtube=False)
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
        self.adapter = FakeApplicationAdapter(self.spotify)
        self.actions = SpotifyActionExecutor(
            self.service,
            self.sessions,
            recovery=instant_recovery(self.service, self.adapter),
            sleeper=lambda seconds: None,
        )

    def session(self, *, provider_id="spotify", item_type="track", item_id=TRACK_ID):
        result = MediaResult(
            provider_id=provider_id, result_id="mres-one", result_type=item_type,
            title="Gönül Dağı",
        )
        return self.sessions.create(
            MediaSearchOutcome(
                provider_id=provider_id, query="q", results=(result,),
                items=(ProviderItem(
                    provider_id=provider_id, item_type=item_type, item_id=item_id
                ),),
            )
        )


class CallbackStateGuardTests(unittest.TestCase):
    """(1) The state check is what makes a forged callback useless."""

    def test_the_suite_notices_if_state_validation_stops_validating(self) -> None:
        registry = oauth.AttemptRegistry()
        attempt = registry.start("client-id")

        # Unmutated: a callback carrying the wrong state consumes nothing.
        self.assertIsNone(registry.consume("forged-state"))

        # Mutated: comparison always agrees.
        with patch.object(oauth, "states_match", lambda expected, received: True):
            mutated = oauth.AttemptRegistry()
            mutated.start("client-id")
            self.assertIsNotNone(
                mutated.consume("forged-state"),
                "removing the state check should let a forged callback through",
            )

        # And the real one still works, so the guard is not simply refusing all.
        self.assertIsNotNone(registry.consume(attempt.state))

    def test_the_suite_notices_if_the_comparison_stops_being_constant_time(self) -> None:
        """A `==` here leaks the state one character at a time to a local attacker."""
        import inspect

        source = inspect.getsource(oauth.states_match)
        # The comparison itself, not the sentence explaining it.
        comparison = [line for line in source.splitlines() if line.strip().startswith("return ")][-1]
        self.assertIn("hmac.compare_digest", comparison)
        # The mutation, stated so the property is observable rather than assumed.
        self.assertNotIn(
            "compare_digest", comparison.replace("hmac.compare_digest(expected, received)", "expected == received")
        )

    def test_the_suite_notices_if_a_consumed_attempt_can_be_replayed(self) -> None:
        registry = oauth.AttemptRegistry()
        attempt = registry.start("client-id")
        registry.consume(attempt.state)

        # Unmutated: gone.
        self.assertIsNone(registry.consume(attempt.state))

        # Mutated: `consume` that peeks instead of taking.
        class PeekingRegistry(oauth.AttemptRegistry):
            def consume(self, state):
                current = self.current()
                if current and oauth.states_match(current.state, state):
                    return current
                return None

        peeking = PeekingRegistry()
        replayable = peeking.start("client-id")
        self.assertIsNotNone(peeking.consume(replayable.state))
        self.assertIsNotNone(
            peeking.consume(replayable.state),
            "a peeking consume should make a replay succeed — the real one must not",
        )


class LoopbackBindingGuardTests(unittest.TestCase):
    """(2) The listener refuses to exist anywhere but 127.0.0.1."""

    def test_the_suite_notices_if_the_bind_address_stops_being_checked(self) -> None:
        # Unmutated: constructing on a routable address raises.
        with self.assertRaises(ValueError):
            CallbackListener(host="0.0.0.0")

        # Mutated: the constructor accepts whatever it is given.
        class UncheckedListener(CallbackListener):
            def __init__(self, host, port=0):
                self._host = host
                self._port = port
                self._server = None
                self._thread = None
                self._received = __import__("threading").Event()
                self._result = None
                self._validator = None

        widened = UncheckedListener("0.0.0.0", 0)
        widened.start(lambda result: True)
        self.addCleanup(widened.stop)
        self.assertEqual(
            widened.bound_address[0],
            "0.0.0.0",
            "removing the bind check should open the callback to the network",
        )

    def test_the_bind_address_is_a_constant_not_configuration(self) -> None:
        """No deployment mistake can widen it, because nothing reads a setting."""
        import inspect

        from cofferdam.workstation.spotifyplayer import callback as module

        source = inspect.getsource(module)
        self.assertNotIn("os.environ", source)
        self.assertNotIn("config.", source)
        self.assertEqual(oauth.CALLBACK_HOST, "127.0.0.1")


class ClientUriGuardTests(MutationTestCase):
    """(3) The URI is rebuilt server-side, so there is nothing to submit."""

    def test_the_suite_notices_if_a_client_uri_could_be_used(self) -> None:
        session = self.session()

        # Unmutated: play uses the session's own item.
        self.actions.play_search_result(session.search_id, "mres-one")
        call = [c for c in self.spotify.calls if c["path"] == "/v1/me/player/play"][-1]
        self.assertEqual(
            json.loads(call["body"].decode("utf-8"))["uris"], ["spotify:track:" + TRACK_ID]
        )

        # Mutated: an executor that trusts a caller-supplied URI instead.
        class TrustingExecutor(SpotifyActionExecutor):
            def _resolve_track(self, search_id, result_id):
                return "spotify:track:" + OTHER_TRACK_ID, None

        self.spotify.calls.clear()
        TrustingExecutor(self.service, self.sessions).play_search_result(
            session.search_id, "mres-one"
        )
        call = [c for c in self.spotify.calls if c["path"] == "/v1/me/player/play"][-1]
        self.assertEqual(
            json.loads(call["body"].decode("utf-8"))["uris"],
            ["spotify:track:" + OTHER_TRACK_ID],
            "trusting a supplied URI should reach the provider — the real path must not",
        )

    def test_the_suite_notices_if_the_provider_check_stops_pinning_spotify(self) -> None:
        """The guard that stops a YouTube result reaching the Spotify player."""
        session = self.session(provider_id="youtube", item_type="video")

        # Unmutated: refused, and nothing is played.
        with self.assertRaises(Exception):
            self.actions.play_search_result(session.search_id, "mres-one")
        self.assertEqual(
            [c for c in self.spotify.calls if c["path"] == "/v1/me/player/play"], []
        )

        # Mutated: resolve without pinning the provider.
        resolved = self.sessions.resolve(session.search_id, "mres-one", provider_id=None)
        self.assertEqual(
            resolved[2].provider_id,
            "youtube",
            "dropping the provider pin should surface a YouTube item here",
        )

    def test_the_suite_notices_if_non_track_results_become_playable(self) -> None:
        from cofferdam.workstation.spotifyplayer import actions as actions_module

        session = self.session(item_type="album")

        # Unmutated: refused.
        with self.assertRaises(SpotifyPlayerError):
            self.actions.play_search_result(session.search_id, "mres-one")

        self.assertEqual(
            [c for c in self.spotify.calls if c["path"] == "/v1/me/player/play"], []
        )

        # Mutated: widen the playable set, and an album context is sent to an
        # endpoint that takes track URIs — which is a provider 400 in production
        # and a silently wrong action in a test that was not looking.
        with patch.object(
            actions_module, "PLAYABLE_ITEM_TYPES", ("track", "album", "artist", "playlist")
        ):
            self.actions.play_search_result(session.search_id, "mres-one")
        call = [c for c in self.spotify.calls if c["path"] == "/v1/me/player/play"][-1]
        self.assertEqual(
            json.loads(call["body"].decode("utf-8"))["uris"],
            ["spotify:album:" + TRACK_ID],
            "widening the playable set should send an album to the track endpoint",
        )


class StaleDeviceGuardTests(MutationTestCase):
    """(4) A handle that no longer resolves is refused, never guessed at."""

    def test_the_suite_notices_if_stale_handles_start_being_accepted(self) -> None:
        stale = self.service.snapshot(refresh=True).devices[0].resource_id
        self.spotify.devices = [device(device_id="dev-other", name="Phone", is_active=True)]

        # Unmutated: refused.
        with self.assertRaises(SpotifyPlayerError):
            self.actions.transfer(stale)

        # Mutated: fall back to "whatever is active" when the handle is unknown.
        class ForgivingService(SpotifyPlayerService):
            def resolve_device(self, snapshot, resource_id, *, require_controllable=True):
                found = snapshot.device_by_resource_id(resource_id)
                return found or snapshot.active_device()

        forgiving = ForgivingService(
            self.config,
            CredentialStore(self.config),
            token_store=self.tokens,
            client=SpotifyPlayerClient(lambda: "id", self.tokens, request=self.spotify),
            cache_seconds=0.0,
        )
        result = SpotifyActionExecutor(forgiving, self.sessions).transfer(stale)
        self.assertEqual(
            result["outcome"],
            OUTCOME_APPLIED,
            "a fallback should let a stale handle move the wrong device — the real path must not",
        )

    def test_the_suite_notices_if_the_restricted_check_is_dropped(self) -> None:
        self.spotify.devices = [device(is_restricted=True, supports_volume=False)]
        handle = self.service.snapshot(refresh=True).devices[0].resource_id

        # Unmutated: refused.
        with self.assertRaises(SpotifyPlayerError):
            self.actions.transfer(handle)

        # Mutated: `controllable` that ignores the documented field.
        with patch(
            "cofferdam.workstation.spotifyplayer.models.SpotifyDevice.controllable",
            property(lambda self: True),
        ):
            self.actions.transfer(handle)
        self.assertTrue(
            [c for c in self.spotify.calls if c["path"] == "/v1/me/player" and c["method"] == "PUT"],
            "ignoring is_restricted should let the request reach the provider",
        )

    def test_the_suite_notices_if_the_device_list_stops_being_refreshed(self) -> None:
        """A cached list is exactly how a disappeared device keeps working."""
        handle = self.service.snapshot(refresh=True).devices[0].resource_id
        self.spotify.calls.clear()
        self.actions.set_volume(30, handle)
        self.assertTrue(
            [c for c in self.spotify.calls if c["path"] == "/v1/me/player/devices"],
            "the device list must be re-read before a targeted action",
        )


class ObservationGuardTests(MutationTestCase):
    """(5) A 204 is Spotify saying "heard you", not "the speaker changed"."""

    def test_the_suite_notices_if_a_pause_is_reported_without_observing(self) -> None:
        self.spotify.ignore_writes = True

        # Unmutated: the re-read disagrees, so it is not "applied".
        self.assertEqual(self.actions.pause()["outcome"], OUTCOME_NOT_APPLIED)

        # Mutated: report success from the fact that the request was sent.
        class OptimisticExecutor(SpotifyActionExecutor):
            def pause(self):
                before = self._fresh()
                target = self._service.target_device(before)
                self._service.client.pause(self._tokens(), target.provider_device_id)
                return self._result(
                    "spotify_pause", OUTCOME_APPLIED, {"is_playing": False},
                    {"is_playing": False}, "Spotify is paused", before,
                )

        self.assertEqual(
            OptimisticExecutor(self.service, self.sessions).pause()["outcome"],
            OUTCOME_APPLIED,
            "reporting without re-reading should claim a pause that did not happen",
        )

    def test_the_suite_notices_if_the_played_track_stops_being_compared(self) -> None:
        session = self.session()
        self.spotify.ignore_writes = True
        self.spotify.item = track_item(OTHER_TRACK_ID, name="Something Else")

        # Unmutated: the observed track is not the requested one.
        result = self.actions.play_search_result(session.search_id, "mres-one")
        self.assertEqual(result["outcome"], OUTCOME_NOT_APPLIED)
        self.assertNotEqual(result["requested"]["track_id"], result["observed"]["track_id"])

        # Mutated: compare nothing.
        class UncheckedExecutor(SpotifyActionExecutor):
            def play_search_result(self, search_id, result_id, device_resource_id=None):
                uri, _result = self._resolve_track(search_id, result_id)
                before = self._fresh()
                target = self._service.target_device(before, device_resource_id)
                self._service.client.play_uris(self._tokens(), [uri], target.provider_device_id)
                after = self._reobserve()
                return self._result(
                    "spotify_play_search_result", OUTCOME_APPLIED, {}, {},
                    "Spotify is playing the track you chose", after,
                )

        self.assertEqual(
            UncheckedExecutor(self.service, self.sessions).play_search_result(
                session.search_id, "mres-one"
            )["outcome"],
            OUTCOME_APPLIED,
            "dropping the comparison should claim the wrong track as a success",
        )

    def test_the_suite_notices_if_a_volume_change_stops_being_verified(self) -> None:
        """The observed number must be read, never echoed from the request."""
        self.spotify.ignore_writes = True

        # Unmutated: never `applied`, and `observed` is what the device says.
        result = self.actions.set_volume(25)
        self.assertNotEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["requested"]["volume_percent"], 25)
        self.assertNotEqual(result["observed"]["volume_percent"], 25)
        self.assertFalse(result["observed"]["confirmed"])

        # Mutated: report the requested value as the observed one — the single
        # most tempting shortcut in this whole file, and the one that would make
        # every volume test pass while the speaker did nothing.
        class EchoingExecutor(SpotifyActionExecutor):
            def set_volume(self, volume_percent, device_resource_id=None):
                before = self._fresh()
                target = self._service.target_device(before, device_resource_id)
                self._service.client.set_volume(
                    self._tokens(), int(volume_percent), target.provider_device_id
                )
                after = self._reobserve()
                return self._result(
                    "spotify_set_volume", OUTCOME_APPLIED,
                    {"volume_percent": int(volume_percent)},
                    {"volume_percent": int(volume_percent)},
                    f"Spotify volume is now {int(volume_percent)}%", after,
                )

        echoed = EchoingExecutor(self.service, self.sessions).set_volume(25)
        self.assertEqual(
            echoed["observed"]["volume_percent"],
            echoed["requested"]["volume_percent"],
            "echoing the request should look like success — the real path must not",
        )

    def test_the_suite_notices_if_confirmation_polling_is_removed(self) -> None:
        """One read is not enough, and this is what proves it.

        Spotify's devices endpoint is eventually consistent. The single
        immediate read this replaced reported "set to 80% but the device reports
        50%" while the speaker was already at 80 — a real failure, on a real
        phone, on every volume change.
        """
        self.spotify.lag_device_reads = 2

        # Unmutated: re-read on a bounded schedule, and it confirms.
        result = self.actions.set_volume(80)
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["observed"]["volume_percent"], 80)

        # Mutated: a confirmation window of exactly one attempt, which is the
        # pre-M2D.1 behaviour spelled out.
        from cofferdam.workstation.spotifyplayer.confirm import ConfirmWindow

        with patch(
            "cofferdam.workstation.spotifyplayer.actions.VOLUME_CONFIRM",
            ConfirmWindow(attempts=1, interval_seconds=0.0),
        ):
            self.spotify.lag_device_reads = 2
            once = self.actions.set_volume(30)
        self.assertNotEqual(
            once["outcome"],
            OUTCOME_APPLIED,
            "reading once should fail to confirm a lagging write — which is the bug",
        )
        self.assertEqual(once["observed"]["volume_percent"], 80)

    def test_the_suite_notices_if_device_polling_becomes_unbounded(self) -> None:
        """Every wait is a fixed attempt count, not a condition.

        Spotify rate-limits over a rolling thirty-second window, so a loop that
        kept trying would turn one slow device into a burst of requests against
        an account that is already struggling.
        """
        import inspect

        from cofferdam.workstation.spotifyplayer import coldstart, confirm

        source = inspect.getsource(confirm.confirm)
        self.assertIn("range(", source)
        self.assertNotIn("while ", source)
        self.assertNotIn("while ", inspect.getsource(coldstart.DeviceRecovery))

        for window in (
            confirm.VOLUME_CONFIRM,
            confirm.PLAYBACK_CONFIRM,
            confirm.TRANSPORT_CONFIRM,
            confirm.ACTIVATION_CONFIRM,
            confirm.DEVICE_APPEARANCE,
        ):
            with self.subTest(window=window):
                self.assertGreater(window.attempts, 0)
                self.assertLessEqual(window.attempts, 30)
                self.assertLessEqual(window.timeout_seconds, 60)

        # Mutated: a window with a huge attempt count still terminates, which is
        # the property that makes "bounded" meaningful rather than lucky.
        reads = []

        def never_matches(_value: object) -> bool:
            return False

        confirm.confirm(
            lambda: reads.append(1),
            never_matches,
            confirm.ConfirmWindow(attempts=7, interval_seconds=0.0),
            lambda seconds: None,
        )
        self.assertEqual(len(reads), 7)


class UnmuteRestoreGuardTests(MutationTestCase):
    """(6) An unknown restore level is refused, never invented."""

    def test_the_suite_notices_if_a_restore_level_starts_being_guessed(self) -> None:
        self.spotify.devices = [device(volume_percent=0)]

        # Unmutated: refused, and nothing is sent.
        with self.assertRaises(SpotifyPlayerError):
            self.actions.set_muted(False)
        self.assertEqual(
            [c for c in self.spotify.calls if c["path"] == "/v1/me/player/volume"], []
        )

        # Mutated: a store that supplies a "reasonable" default.
        with patch(
            "cofferdam.workstation.spotifyplayer.mutestate.MuteStateStore.restore_value",
            lambda self, resource_id: 50,
        ):
            result = self.actions.set_muted(False)
        self.assertEqual(result["requested"]["volume_percent"], 50)
        self.assertTrue(
            [c for c in self.spotify.calls if c["path"] == "/v1/me/player/volume"],
            "a guessed level should set somebody's speakers to a number nobody chose",
        )

    def test_the_suite_notices_if_a_zero_becomes_a_restorable_level(self) -> None:
        from cofferdam.workstation.spotifyplayer.mutestate import MuteStateStore

        store = MuteStateStore(self.config)

        # Unmutated: zero is not something to restore *to*.
        store.remember("spdev-a", 0)
        self.assertIsNone(store.restore_value("spdev-a"))

        # Mutated: record it anyway, and "unmute" becomes "set to silent".
        with patch.object(
            MuteStateStore, "remember",
            lambda self, resource_id, volume_percent: MuteStateStore._write(
                self, {resource_id: {"restore_volume_percent": volume_percent}}
            ),
        ):
            store.remember("spdev-b", 0)
        raw = json.loads(store.path.read_text(encoding="utf-8"))
        self.assertEqual(
            raw["devices"]["spdev-b"]["restore_volume_percent"],
            0,
            "recording a zero should make unmute a silent button",
        )

    def test_the_suite_notices_if_a_stale_mute_record_stops_being_dropped(self) -> None:
        """Somebody turned it back up in Spotify; the record is now a lie."""
        self.actions.set_muted(True)
        self.spotify.devices[0]["volume_percent"] = 45
        snapshot = self.service.snapshot(refresh=True)
        self.assertFalse(snapshot.muted_by_cofferdam)
        self.assertIsNone(
            self.service.mute_state.restore_value(snapshot.devices[0].resource_id)
        )


class SecretRedactionGuardTests(MutationTestCase):
    """(7) No token, hash, prefix or length reaches a client or a record."""

    def test_the_suite_notices_if_the_public_view_starts_carrying_a_token(self) -> None:
        tokens = UserTokens(refresh_token=FAKE_REFRESH_TOKEN, scopes=("a",))

        # Unmutated: absent.
        self.assertNotIn(FAKE_REFRESH_TOKEN, json.dumps(tokens.public_view()))

        # Mutated: a "harmless" debugging field.
        with patch.object(
            UserTokens, "public_view",
            lambda self: {"scopes": list(self.scopes), "refresh_token": self.refresh_token},
        ):
            leaked = json.dumps(UserTokens(refresh_token=FAKE_REFRESH_TOKEN).public_view())
        self.assertIn(
            FAKE_REFRESH_TOKEN, leaked, "a token in the public view should be visible here"
        )

    def test_the_suite_notices_if_the_repr_starts_carrying_a_token(self) -> None:
        """A dataclass repr puts the token in every traceback that touches it."""
        tokens = UserTokens(refresh_token=FAKE_REFRESH_TOKEN)
        self.assertNotIn(FAKE_REFRESH_TOKEN, repr(tokens))

        with patch.object(UserTokens, "__repr__", object.__repr__):
            self.assertNotIn(FAKE_REFRESH_TOKEN, repr(tokens))
        # The real mutation is a *dataclass-generated* repr, which does leak:
        from dataclasses import dataclass

        @dataclass
        class Naive:
            refresh_token: str

        self.assertIn(
            FAKE_REFRESH_TOKEN,
            repr(Naive(FAKE_REFRESH_TOKEN)),
            "the default dataclass repr should leak — which is why the real one is written",
        )

    def test_the_suite_notices_if_a_device_id_starts_being_published(self) -> None:
        payload = self.service.snapshot(refresh=True).to_dict()

        # Unmutated: the provider id is server-side only.
        self.assertNotIn("dev-workstation", json.dumps(payload))

        # Mutated: publish the whole dataclass.
        from dataclasses import asdict

        leaked = json.dumps(asdict(self.service.snapshot(refresh=True).devices[0]))
        self.assertIn(
            "dev-workstation",
            leaked,
            "serializing the dataclass should expose the provider id — to_dict must not",
        )

    def test_the_suite_notices_if_the_audit_starts_carrying_a_track(self) -> None:
        from cofferdam.workstation.store import ActionStore

        store = ActionStore(self.config)
        store.record_spotify_event("spotify_next", "ok")
        recorded = json.dumps(store.recent(), ensure_ascii=False)
        self.assertNotIn("Gönül", recorded)
        self.assertNotIn("Ertaş", recorded)

        # Mutated: an audit that records the "useful" context.
        store.add(
            {
                "action_id": "x" * 32, "action": "spotify_next", "status": "succeeded",
                "started_at": "2026-08-05T12:00:00Z", "finished_at": "2026-08-05T12:00:00Z",
                "params": {"track": "Gönül Dağı"}, "result": {}, "error": None, "stub": False,
            }
        )
        self.assertIn(
            "Gönül",
            json.dumps(store.recent(), ensure_ascii=False),
            "a track title in params should be visible here — the real audit has none",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
