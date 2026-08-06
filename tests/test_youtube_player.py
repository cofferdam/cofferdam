"""The dedicated player: lifecycle, authority, queue, controls (M2E 1–46).

The properties under test here are the ones the old behaviour could not have
had, because it opened a tab per video and treated the tab appearing as proof:

* exactly **one** launch per bounded recovery, and none at all when a player is
  already connected;
* a video reaches the player only from a **verified search result** or from the
  **Cofferdam queue** — never from anything a client sent;
* every answer is derived from what the *player reported*, so the fake here can
  disagree with the request. Half the tests below are about what happens then.

The fake player is a small stateful browser tab, driven through the same channel
methods the shipped loopback endpoint uses. See ``tests/_youtubeplayer_doubles``.
"""

from __future__ import annotations

import threading
import unittest

from cofferdam.workstation.mediasearch.sessions import SearchSessionStore
from cofferdam.workstation.youtubeplayer.actions import (
    OUTCOME_APPLIED,
    OUTCOME_AUTOPLAY_BLOCKED,
    OUTCOME_PARTIALLY_APPLIED,
    OUTCOME_QUEUED,
    YouTubeActionExecutor,
    _whole_percent,
)
from cofferdam.workstation.youtubeplayer.channel import COMMANDS, PlayerChannel
from cofferdam.workstation.youtubeplayer.errors import (
    CODE_BUSY,
    CODE_EMBED_IDENTITY_REJECTED,
    CODE_INVALID_MUTE,
    CODE_INVALID_VOLUME,
    CODE_NO_NEXT_ITEM,
    CODE_NO_PLAYER,
    CODE_NO_PREVIOUS_ITEM,
    CODE_QUEUE_FULL,
    CODE_QUEUE_ITEM_UNKNOWN,
    CODE_REGISTRATION_TIMEOUT,
    CODE_RESULT_NOT_PLAYABLE,
    CODE_VIDEO_NOT_OBSERVED,
    CODE_VIDEO_UNAVAILABLE,
    CODE_VOLUME_NOT_OBSERVED,
    ERROR_EMBED_IDENTITY,
    YouTubePlayerError,
    describe_player_error,
)
from cofferdam.workstation.youtubeplayer.models import (
    CONNECTION_DISCONNECTED,
    CONNECTION_READY,
    MAX_QUEUE_ITEMS,
    PLAYBACK_AUTOPLAY_BLOCKED,
    PLAYBACK_ERROR,
    PLAYBACK_PAUSED,
    PLAYBACK_PLAYING,
)
from cofferdam.workstation.youtubeplayer.queue import PlayQueue
from cofferdam.workstation.youtubeplayer.service import REGISTRATION_WINDOW

from ._youtubeplayer_doubles import (
    OTHER_VIDEO_ID,
    THIRD_VIDEO_ID,
    VIDEO_ID,
    DelayedLauncher,
    FakePlayer,
    ImmediateLauncher,
    SilentLauncher,
    build_service,
    non_video_session,
    spotify_session,
    wait_until,
    youtube_session,
)


class PlayerTestCase(unittest.TestCase):
    """Shared wiring: a session store, a service on fakes, and an executor."""

    launcher_factory = ImmediateLauncher

    def setUp(self) -> None:
        self.store = SearchSessionStore()
        self.service = build_service(self.launcher_factory)
        self.actions = YouTubeActionExecutor(self.service, self.store)
        self.launcher = self.service.launcher
        self.player = self.launcher.player
        self.addCleanup(self.player.stop)

    def session(self, video_ids=(VIDEO_ID,)):
        return youtube_session(self.store, video_ids)

    def play(self, session, result_id="r0"):
        return self.actions.play_search_result(session.search_id, result_id)


# -- 1-6: launch lifecycle and the single tab --------------------------------


class LaunchLifecycle(PlayerTestCase):
    def test_one_launch_during_one_recovery(self):
        """1. A Play now with no player open launches exactly one tab."""
        self.play(self.session())
        self.assertEqual(self.launcher.calls, 1)

    def test_connected_player_prevents_another_launch(self):
        """2. A second Play now goes to the player that is already open."""
        session = self.session((VIDEO_ID, OTHER_VIDEO_ID))
        self.play(session, "r0")
        self.play(session, "r1")
        self.assertEqual(self.launcher.calls, 1, "a second tab was opened")

    def test_concurrent_play_now_launches_once(self):
        """1. Two presses that genuinely overlap still produce one launch.

        Made deterministic rather than raced: the second press is issued while
        the first is provably still inside the launch, by holding the second
        thread until the launcher has been entered. Without that, a fast first
        press would finish before the second began and the test would assert
        nothing.
        """
        session = self.session((VIDEO_ID, OTHER_VIDEO_ID))
        entered = threading.Event()
        outcomes = []

        real_launch = self.launcher.launch

        def slow_launch(url):
            entered.set()
            import time

            time.sleep(0.3)
            return real_launch(url)

        self.launcher.launch = slow_launch

        def press(result_id):
            try:
                outcomes.append(self.play(session, result_id)["outcome"])
            except YouTubePlayerError as error:
                outcomes.append(error.code)

        first = threading.Thread(target=press, args=("r0",))
        first.start()
        self.assertTrue(entered.wait(timeout=5), "the launch was never entered")

        second = threading.Thread(target=press, args=("r1",))
        second.start()
        for thread in (first, second):
            thread.join(timeout=20)

        self.assertEqual(self.launcher.calls, 1, "two tabs were opened")
        # The overlapping press is refused rather than queued behind the first:
        # two Play nows that both ran would load two videos and confirm neither.
        self.assertIn(CODE_BUSY, outcomes)
        self.assertIn(OUTCOME_APPLIED, outcomes)

    def test_player_launch_uses_the_loopback_player_url(self):
        """The launcher is only ever given Cofferdam's own player address."""
        self.play(self.session())
        self.assertEqual(len(self.launcher.urls), 1)
        url = self.launcher.urls[0]
        self.assertTrue(url.startswith("http://127.0.0.1:"), url)
        self.assertTrue(url.endswith("/player"), url)
        # No token, no query string, no fragment — nothing to leak into history.
        self.assertNotIn("?", url)
        self.assertNotIn("#", url)

    def test_closed_player_is_detected(self):
        """5. A tab that stops reporting becomes disconnected."""
        self.play(self.session())
        self.assertEqual(self.service.connection_state(), CONNECTION_READY)

        self.player.stop()
        self.service.channel.release(self.player.instance_id)
        self.assertEqual(self.service.connection_state(), CONNECTION_DISCONNECTED)
        self.assertFalse(self.service.snapshot().to_dict()["connection"]["connected"])

    def test_closed_player_relaunches_once_on_the_next_play(self):
        """6. A later explicit Play now may reopen a closed player, once."""
        session = self.session((VIDEO_ID, OTHER_VIDEO_ID))
        self.play(session, "r0")
        self.assertEqual(self.launcher.calls, 1)

        self.player.stop()
        self.service.channel.release(self.player.instance_id)
        self.launcher.player = FakePlayer(self.service.channel)
        self.addCleanup(self.launcher.player.stop)

        self.play(session, "r1")
        self.assertEqual(self.launcher.calls, 2, "the closed player was not reopened")

    def test_disconnected_player_reports_no_current_video(self):
        """A player that is gone has no 'currently playing' to show."""
        self.play(self.session())
        self.player.stop()
        self.service.channel.release(self.player.instance_id)
        current = self.service.snapshot().to_dict()["current"]
        self.assertIsNone(current["video"])
        self.assertIsNone(current["result_handle"])

    def test_running_browser_is_never_reported_as_a_connected_player(self):
        """A launch that produced no player leaves the state disconnected.

        The adapter was called and Opera may well be running; that is explicitly
        not evidence of a player.
        """
        service = build_service(SilentLauncher)
        service.launcher.available_flag = True
        self.assertFalse(service.channel.connected())
        self.assertEqual(service.connection_state(), CONNECTION_DISCONNECTED)


class DelayedRegistration(PlayerTestCase):
    launcher_factory = DelayedLauncher

    def test_delayed_registration_continues_the_original_play(self):
        """3. A slow tab does not turn one press into two."""
        result = self.play(self.session())
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(self.launcher.calls, 1)
        self.assertEqual(self.player.video_id, VIDEO_ID)


class RegistrationTimeout(PlayerTestCase):
    launcher_factory = SilentLauncher

    def setUp(self) -> None:
        super().setUp()
        # The real window is 24 seconds; a test must not wait that long, and
        # shortening it here still exercises the same code path.
        import cofferdam.workstation.youtubeplayer.service as service_module

        self._real_window = service_module.REGISTRATION_WINDOW
        service_module.REGISTRATION_WINDOW = type(self._real_window)(
            attempts=2, interval_seconds=0.05
        )
        self.addCleanup(
            setattr, service_module, "REGISTRATION_WINDOW", self._real_window
        )

    def test_registration_timeout_is_a_truthful_failure(self):
        """4. A player that never opens is reported as that, not as success."""
        with self.assertRaises(YouTubePlayerError) as raised:
            self.play(self.session())
        self.assertEqual(raised.exception.code, CODE_REGISTRATION_TIMEOUT)
        self.assertEqual(self.launcher.calls, 1, "the timeout was chased with a second tab")

    def test_registration_timeout_does_not_leave_a_stuck_phase(self):
        """A failed launch must not leave the phone showing 'opening…' forever."""
        with self.assertRaises(YouTubePlayerError):
            self.play(self.session())
        self.assertEqual(self.service.connection_state(), CONNECTION_DISCONNECTED)


# -- 7-12: what a client may and may not name --------------------------------


class ResultAuthority(PlayerTestCase):
    def test_client_cannot_submit_a_youtube_url(self):
        """7. There is no field for a URL anywhere in the action signature."""
        import inspect

        for name in ("play_search_result", "queue_search_result"):
            parameters = set(
                inspect.signature(getattr(self.actions, name)).parameters
            )
            self.assertEqual(parameters, {"search_id", "result_id"})

    def test_client_cannot_submit_a_video_id_as_authority(self):
        """8. A video id where a result id belongs resolves to nothing."""
        session = self.session()
        with self.assertRaises(Exception) as raised:
            self.actions.play_search_result(session.search_id, VIDEO_ID)
        # The search session has no result called "dQw4w9WgXcQ"; it has "r0".
        self.assertIn("result", str(raised.exception).lower())

    def test_client_cannot_submit_a_player_command_or_script(self):
        """9. Command-shaped and script-shaped result ids are just unknown ids."""
        session = self.session()
        for hostile in (
            "playVideo()",
            "<script>alert(1)</script>",
            "javascript:alert(1)",
            "https://www.youtube.com/watch?v=" + VIDEO_ID,
            "../../etc/passwd",
            {"command": "play"},
            ["r0"],
            None,
            7,
        ):
            with self.assertRaises(Exception):
                self.actions.play_search_result(session.search_id, hostile)
        self.assertEqual(self.launcher.calls, 0, "a hostile id reached a launch")

    def test_only_video_results_can_play(self):
        """10. A non-video YouTube result is refused rather than loaded."""
        session = non_video_session(self.store)
        with self.assertRaises(YouTubePlayerError) as raised:
            self.actions.play_search_result(session.search_id, "r0")
        self.assertEqual(raised.exception.code, CODE_RESULT_NOT_PLAYABLE)

    def test_spotify_results_cannot_enter_the_youtube_player(self):
        """11. The cross-provider check fails closed."""
        session = spotify_session(self.store)
        with self.assertRaises(Exception) as raised:
            self.actions.play_search_result(session.search_id, "r0")
        self.assertNotIsInstance(raised.exception, AssertionError)
        self.assertEqual(self.launcher.calls, 0)

    def test_expired_searches_cannot_play_or_queue(self):
        """12. An expired session is refused for both actions."""
        session = self.session()
        # Expire it the way the store does: by time.
        import time as _time

        self.store._sessions[session.search_id] = session.__class__(
            **{**session.__dict__, "expires_at": _time.time() - 1}
        )
        for call in (self.actions.play_search_result, self.actions.queue_search_result):
            with self.assertRaises(Exception):
                call(session.search_id, "r0")
        self.assertEqual(self.launcher.calls, 0)

    def test_unknown_search_is_refused(self):
        with self.assertRaises(Exception):
            self.actions.play_search_result("no-such-search", "r0")


# -- 13-18: play now, observation, autoplay ----------------------------------


class PlayNow(PlayerTestCase):
    def test_play_now_loads_the_selected_video(self):
        """13. The player ends up holding the id behind the chosen result."""
        session = self.session((VIDEO_ID, OTHER_VIDEO_ID))
        self.play(session, "r1")
        self.assertEqual(self.player.video_id, OTHER_VIDEO_ID)

    def test_play_now_does_not_create_a_second_player(self):
        """14. Three videos, one tab."""
        session = self.session((VIDEO_ID, OTHER_VIDEO_ID, THIRD_VIDEO_ID))
        for result_id in ("r0", "r1", "r2"):
            self.play(session, result_id)
        self.assertEqual(self.launcher.calls, 1)

    def test_play_now_confirms_the_observed_video(self):
        """15. Success requires the player to report *that* video."""
        result = self.play(self.session())
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(self.service.channel.reported_video_id(), VIDEO_ID)

    def test_observation_mismatch_is_not_a_success(self):
        """16. A player showing something else is never reported as playing."""
        self.player.load_video_id_override = OTHER_VIDEO_ID
        with self.assertRaises(YouTubePlayerError) as raised:
            self.play(self.session())
        self.assertEqual(raised.exception.code, CODE_VIDEO_NOT_OBSERVED)

    def test_autoplay_blocked_is_reported_truthfully(self):
        """17. Blocked playback is its own outcome, with the video still cued."""
        self.player.refuse_autoplay = True
        result = self.play(self.session())
        self.assertEqual(result["outcome"], OUTCOME_AUTOPLAY_BLOCKED)
        self.assertEqual(
            result["player"]["current"]["playback_state"], PLAYBACK_AUTOPLAY_BLOCKED
        )
        # The chosen video is loaded, so one click on the workstation is enough.
        self.assertEqual(self.player.video_id, VIDEO_ID)
        self.assertIn("click", result["note"].lower())

    def test_no_unbounded_play_retry(self):
        """18. A blocked player is asked to play a bounded number of times."""
        self.player.refuse_autoplay = True
        self.play(self.session())
        plays = [c for c in self.player.commands if c["command"] == "play"]
        # loadVideoById carries autoplay; the executor does not then hammer
        # playVideo hoping the browser changes its mind.
        self.assertLessEqual(len(plays), 1, "the player was asked to play repeatedly")

    def test_partial_state_is_not_reported_as_playing(self):
        """9 of the play-now contract: paused-but-loaded is partial, not success."""
        self.play(self.session())
        self.actions.pause()
        # A fresh load that the fake leaves paused rather than playing.
        self.player.refuse_autoplay = False
        session = self.session((OTHER_VIDEO_ID,))

        original_run = self.player.run

        def run_then_pause(command):
            original_run(command)
            if command.name == "load_video":
                self.player.player_state = 2  # paused

        self.player.run = run_then_pause
        result = self.actions.play_search_result(session.search_id, "r0")
        self.assertEqual(result["outcome"], OUTCOME_PARTIALLY_APPLIED)

    def test_play_now_never_opens_a_watch_tab(self):
        """10 of the play-now contract: no normal YouTube page is ever opened."""
        self.play(self.session())
        for url in self.launcher.urls:
            self.assertNotIn("youtube.com", url)
            self.assertNotIn("watch", url)


# -- error 153: the embed YouTube would not identify -------------------------


class EmbedIdentityRejection(PlayerTestCase):
    """What a player YouTube refused to load is allowed to be called.

    From real-host validation: the player page opened, connected to Cofferdam,
    and YouTube answered the embed with "Video player configuration error /
    Error 153" — the embed request carried no identification of the embedding
    page. The referrer fix is what stops it happening; this class is about what
    Cofferdam says on the day it happens anyway.

    Four sentences it must never be:

    * ``autoplay_blocked`` — there is no loaded player to click;
    * "that video is unavailable" — the video is fine on the normal page;
    * ``applied`` / playing;
    * a bare "loaded but not playing", which reads as a slow video.
    """

    def reject_embed(self):
        self.player.error_on_load = ERROR_EMBED_IDENTITY

    def test_error_153_is_its_own_refusal(self):
        self.reject_embed()
        with self.assertRaises(YouTubePlayerError) as raised:
            self.play(self.session())
        self.assertEqual(raised.exception.code, CODE_EMBED_IDENTITY_REJECTED)

    def test_error_153_is_not_reported_as_autoplay_blocked(self):
        """The wrong answer sends someone to click a window that cannot help."""
        self.reject_embed()
        with self.assertRaises(YouTubePlayerError) as raised:
            self.play(self.session())
        self.assertNotEqual(raised.exception.code, "autoplay_blocked")
        self.assertNotIn("click", raised.exception.message.lower())
        state = self.service.snapshot().to_dict()["current"]["playback_state"]
        self.assertNotEqual(state, PLAYBACK_AUTOPLAY_BLOCKED)
        self.assertEqual(state, PLAYBACK_ERROR)

    def test_error_153_is_never_a_success(self):
        """No outcome at all: it raises, so there is no envelope to misread."""
        self.reject_embed()
        with self.assertRaises(YouTubePlayerError):
            self.play(self.session())
        snapshot = self.service.snapshot().to_dict()
        self.assertNotEqual(snapshot["current"]["playback_state"], "playing")
        self.assertEqual(
            snapshot["last_error"]["code"], CODE_EMBED_IDENTITY_REJECTED
        )

    def test_error_153_is_not_reported_as_the_video_being_unavailable(self):
        """Two different problems, two different things to do about them."""
        self.reject_embed()
        with self.assertRaises(YouTubePlayerError) as raised:
            self.play(self.session())
        self.assertNotEqual(raised.exception.code, CODE_VIDEO_UNAVAILABLE)
        self.assertNotEqual(raised.exception.code, CODE_VIDEO_NOT_OBSERVED)

    def test_the_refusal_names_the_player_rather_than_the_video(self):
        """The sentence a person reads has to point at the right thing."""
        self.reject_embed()
        with self.assertRaises(YouTubePlayerError) as raised:
            self.play(self.session())
        self.assertIn("identify", raised.exception.message.lower())
        self.assertIn("youtube", (raised.exception.detail or "").lower())

    def test_a_video_that_is_genuinely_unavailable_still_says_so(self):
        """The new branch did not swallow the errors that were already mapped."""
        self.player.error_on_load = 100
        with self.assertRaises(YouTubePlayerError) as raised:
            self.play(self.session())
        self.assertEqual(raised.exception.code, CODE_VIDEO_NOT_OBSERVED)
        self.assertEqual(
            self.service.snapshot().to_dict()["last_error"]["code"],
            CODE_VIDEO_UNAVAILABLE,
        )

    def test_an_error_outranks_a_blocked_autoplay_in_the_state_report(self):
        """A player reporting both is a player YouTube refused, not one waiting."""
        channel = PlayerChannel()
        instance = channel.register()
        channel.submit_state(
            instance,
            {
                "player_state": 5,
                "video_id": VIDEO_ID,
                "error_code": ERROR_EMBED_IDENTITY,
                "autoplay_blocked": True,
            },
        )
        observation = channel.observation()
        self.assertEqual(observation.playback_state, PLAYBACK_ERROR)
        self.assertEqual(observation.error["code"], CODE_EMBED_IDENTITY_REJECTED)

    def test_resume_against_a_rejected_embed_is_not_autoplay_blocked(self):
        """Pressing play again must not turn a config error into a click prompt."""
        self.reject_embed()
        with self.assertRaises(YouTubePlayerError):
            self.play(self.session())
        self.player.refuse_autoplay = True   # the wrong answer, if it were taken
        with self.assertRaises(YouTubePlayerError) as raised:
            self.actions.resume()
        self.assertEqual(raised.exception.code, CODE_EMBED_IDENTITY_REJECTED)

    def test_153_maps_to_cofferdam_words(self):
        described = describe_player_error(ERROR_EMBED_IDENTITY)
        self.assertEqual(described["code"], CODE_EMBED_IDENTITY_REJECTED)
        # Cofferdam's own sentence, not YouTube's, and no bare number on a phone.
        self.assertNotIn("153", described["message"])
        self.assertNotIn("153", described["detail"])

    def test_the_next_load_clears_the_rejection(self):
        """A stale error must not make the following video look broken too."""
        self.reject_embed()
        with self.assertRaises(YouTubePlayerError):
            self.play(self.session())
        self.player.error_on_load = None
        result = self.play(self.session((OTHER_VIDEO_ID,)))
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertIsNone(result["player"]["last_error"])


# -- 19-27: the Cofferdam queue ----------------------------------------------


class Queue(PlayerTestCase):
    def test_add_to_queue_does_not_interrupt_playback(self):
        """19. Queueing sends no command to the player at all."""
        session = self.session((VIDEO_ID, OTHER_VIDEO_ID))
        self.play(session, "r0")
        before = len(self.player.commands)

        result = self.actions.queue_search_result(session.search_id, "r1")
        self.assertEqual(result["outcome"], OUTCOME_QUEUED)
        self.assertEqual(len(self.player.commands), before, "queueing touched the player")
        self.assertEqual(self.player.player_state, 1, "playback stopped")
        self.assertEqual(self.player.video_id, VIDEO_ID)

    def test_queue_length_is_bounded(self):
        """20. Adding past the cap is refused rather than silently dropped."""
        queue = PlayQueue()
        from cofferdam.workstation.youtubeplayer.models import VideoMetadata

        metadata = VideoMetadata.build("host", VIDEO_ID, title="t")
        for _ in range(MAX_QUEUE_ITEMS):
            queue.add(VIDEO_ID, metadata)
        self.assertEqual(len(queue), MAX_QUEUE_ITEMS)
        with self.assertRaises(YouTubePlayerError) as raised:
            queue.add(VIDEO_ID, metadata)
        self.assertEqual(raised.exception.code, CODE_QUEUE_FULL)

    def test_queue_item_metadata_is_bounded(self):
        """21. A hostile title is truncated and never becomes markup."""
        from cofferdam.workstation.youtubeplayer.models import MAX_TITLE, VideoMetadata

        metadata = VideoMetadata.build(
            "host", VIDEO_ID, title="x" * 5000, channel="y" * 5000
        )
        payload = metadata.to_dict()
        self.assertLessEqual(len(payload["title"]), MAX_TITLE)
        self.assertLessEqual(len(payload["channel"]), 120)
        # And no video id anywhere in the published shape.
        self.assertNotIn(VIDEO_ID, str(payload))

    def test_next_loads_the_next_queued_item(self):
        """22. Next plays the Cofferdam queue's next entry."""
        session = self.session((VIDEO_ID, OTHER_VIDEO_ID))
        self.play(session, "r0")
        self.actions.queue_search_result(session.search_id, "r1")

        result = self.actions.skip(True)
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(self.player.video_id, OTHER_VIDEO_ID)

    def test_previous_returns_to_the_previous_queue_item(self):
        """23. Previous goes back through Cofferdam's own list."""
        session = self.session((VIDEO_ID, OTHER_VIDEO_ID))
        self.play(session, "r0")
        self.play(session, "r1")
        self.assertEqual(self.player.video_id, OTHER_VIDEO_ID)

        self.actions.skip(False)
        self.assertEqual(self.player.video_id, VIDEO_ID)

    def test_queue_never_selects_a_recommendation(self):
        """24. With nothing queued, Next refuses instead of playing something."""
        self.play(self.session())
        with self.assertRaises(YouTubePlayerError) as raised:
            self.actions.skip(True)
        self.assertEqual(raised.exception.code, CODE_NO_NEXT_ITEM)
        self.assertEqual(self.player.video_id, VIDEO_ID, "the video changed anyway")

    def test_previous_at_the_start_refuses(self):
        self.play(self.session())
        with self.assertRaises(YouTubePlayerError) as raised:
            self.actions.skip(False)
        self.assertEqual(raised.exception.code, CODE_NO_PREVIOUS_ITEM)

    def test_removing_a_queue_item_works_safely(self):
        """25. Removal is by issued handle, and does not stop playback."""
        session = self.session((VIDEO_ID, OTHER_VIDEO_ID))
        self.play(session, "r0")
        queued = self.actions.queue_search_result(session.search_id, "r1")
        handle = queued["queue_item_id"]

        result = self.actions.remove_queue_item(handle)
        self.assertEqual(result["player"]["queue"]["length"], 1)
        self.assertEqual(self.player.player_state, 1, "removal stopped playback")

        with self.assertRaises(YouTubePlayerError) as raised:
            self.actions.remove_queue_item(handle)
        self.assertEqual(raised.exception.code, CODE_QUEUE_ITEM_UNKNOWN)

    def test_removing_an_unknown_handle_is_refused(self):
        for hostile in ("", None, 7, "ytq-nope", {"id": 1}):
            with self.assertRaises(YouTubePlayerError):
                self.actions.remove_queue_item(hostile)

    def test_clearing_the_queue_works_safely(self):
        """26. Clearing empties the list and leaves the video playing."""
        session = self.session((VIDEO_ID, OTHER_VIDEO_ID))
        self.play(session, "r0")
        self.actions.queue_search_result(session.search_id, "r1")

        result = self.actions.clear_queue()
        self.assertEqual(result["player"]["queue"]["length"], 0)
        self.assertIsNone(result["player"]["queue"]["index"])
        self.assertEqual(self.player.player_state, 1, "clearing stopped playback")

    def test_queue_is_empty_after_a_restart_without_false_persistence(self):
        """27. A new process has an empty queue and does not pretend otherwise."""
        session = self.session((VIDEO_ID, OTHER_VIDEO_ID))
        self.play(session, "r0")
        self.actions.queue_search_result(session.search_id, "r1")
        self.assertEqual(len(self.service.queue), 2)

        # A restart is a new service object; nothing is read from disk because
        # nothing was written to it.
        restarted = build_service()
        self.addCleanup(restarted.launcher.player.stop)
        self.assertEqual(len(restarted.queue), 0)
        snapshot = restarted.snapshot().to_dict()
        self.assertEqual(snapshot["queue"]["length"], 0)
        self.assertIsNone(snapshot["queue"]["index"])

    def test_play_now_at_capacity_reclaims_played_history_only(self):
        """The bound holds without Play now failing for an unrelated reason."""
        from cofferdam.workstation.youtubeplayer.models import VideoMetadata

        queue = PlayQueue(limit=3)
        metadata = VideoMetadata.build("host", VIDEO_ID, title="t")
        first = queue.play_now(VIDEO_ID, metadata)
        second = queue.play_now(OTHER_VIDEO_ID, metadata)
        third = queue.play_now(THIRD_VIDEO_ID, metadata)
        self.assertEqual(len(queue), 3)

        fourth = queue.play_now(VIDEO_ID, metadata)
        items, index = queue.snapshot()
        self.assertEqual(len(items), 3, "the bound was exceeded")
        self.assertEqual(items[index].queue_item_id, fourth.queue_item_id)
        # The oldest *played* entry went; nothing upcoming was discarded.
        self.assertNotIn(first.queue_item_id, [item.queue_item_id for item in items])


# -- 28-37: transport, volume, mute ------------------------------------------


class Transport(PlayerTestCase):
    def test_pause_is_observed(self):
        """28."""
        self.play(self.session())
        result = self.actions.pause()
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["player"]["current"]["playback_state"], PLAYBACK_PAUSED)

    def test_resume_is_observed(self):
        """29."""
        self.play(self.session())
        self.actions.pause()
        result = self.actions.resume()
        self.assertEqual(result["outcome"], OUTCOME_APPLIED)
        self.assertEqual(result["player"]["current"]["playback_state"], PLAYBACK_PLAYING)

    def test_transport_requires_a_player(self):
        for call in (self.actions.pause, self.actions.resume):
            with self.assertRaises(YouTubePlayerError) as raised:
                call()
            self.assertEqual(raised.exception.code, CODE_NO_PLAYER)


class Volume(PlayerTestCase):
    def test_volume_accepts_0_and_100(self):
        """30."""
        self.play(self.session())
        for level in (0, 100):
            result = self.actions.set_volume(level)
            self.assertEqual(result["player"]["volume"]["volume_percent"], level)

    def test_volume_rejects_out_of_range(self):
        """31. Refused, never clamped."""
        self.play(self.session())
        for level in (-1, -100, 101, 1000):
            with self.assertRaises(YouTubePlayerError) as raised:
                self.actions.set_volume(level)
            self.assertEqual(raised.exception.code, CODE_INVALID_VOLUME)
        self.assertEqual(self.player.volume, 100, "an invalid value was applied anyway")

    def test_volume_rejects_nan_and_malformed(self):
        """32."""
        for value in (
            float("nan"),
            float("inf"),
            float("-inf"),
            "80",
            "eighty",
            None,
            True,
            False,
            [80],
            {"volume_percent": 80},
            61.5,
        ):
            with self.assertRaises(YouTubePlayerError):
                _whole_percent(value)

    def test_requested_volume_is_not_echoed_as_observed(self):
        """33. A player that ignores the command produces a refusal."""
        self.play(self.session())
        self.player.ignore_commands = True
        with self.assertRaises(YouTubePlayerError) as raised:
            self.actions.set_volume(40)
        self.assertEqual(raised.exception.code, CODE_VOLUME_NOT_OBSERVED)
        # And the message carries what the player actually reports.
        self.assertIn("100", raised.exception.detail or "")

    def test_delayed_volume_is_confirmed_with_bounded_reads(self):
        """34. A player that answers late still confirms, without a retry loop."""
        self.play(self.session())
        original_run = self.player.run
        pending = {"applied": False}

        def slow(command):
            if command.name == "set_volume" and not pending["applied"]:
                # First delivery: acknowledge but do not move yet, then apply
                # from another thread a moment later.
                pending["applied"] = True

                def later():
                    import time

                    time.sleep(0.2)
                    original_run(command)
                    self.player.beat()

                threading.Thread(target=later, daemon=True).start()
                return
            original_run(command)

        self.player.run = slow
        result = self.actions.set_volume(35)
        self.assertEqual(result["player"]["volume"]["volume_percent"], 35)

    def test_volume_scope_is_published_as_player_only(self):
        """36. The payload says which volume this is, every time."""
        self.play(self.session())
        volume = self.actions.set_volume(55)["player"]["volume"]
        self.assertEqual(volume["scope"], "youtube_player_only")


class Mute(PlayerTestCase):
    def test_mute_and_unmute_use_the_player_api(self):
        """35. Mute is a player command, and the observed flag follows it."""
        self.play(self.session())

        result = self.actions.set_muted(True)
        self.assertEqual(result["player"]["volume"]["muted"], True)
        self.assertTrue(self.player.muted)
        self.assertIn("set_muted", [c["command"] for c in self.player.commands])

        result = self.actions.set_muted(False)
        self.assertEqual(result["player"]["volume"]["muted"], False)
        self.assertFalse(self.player.muted)

    def test_mute_preserves_the_player_volume(self):
        """The official API keeps the level across mute; nothing is invented."""
        self.play(self.session())
        self.actions.set_volume(42)
        self.actions.set_muted(True)
        self.assertEqual(self.player.volume, 42)
        result = self.actions.set_muted(False)
        self.assertEqual(result["player"]["volume"]["volume_percent"], 42)

    def test_mute_rejects_non_boolean(self):
        self.play(self.session())
        for value in ("true", 1, 0, None, [], {}):
            with self.assertRaises(YouTubePlayerError) as raised:
                self.actions.set_muted(value)
            self.assertEqual(raised.exception.code, CODE_INVALID_MUTE)

    def test_mute_never_touches_computer_audio(self):
        """35/36. No audio module is reachable from this package.

        A structural check rather than a behavioural one: the guarantee is that
        the code *cannot* change system volume, and that is a property of what it
        imports.
        """
        import pathlib

        package = pathlib.Path(
            "cofferdam/workstation/youtubeplayer"
        )
        for path in package.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("from ..audio", source, path.name)
            self.assertNotIn("import audio", source, path.name)
            self.assertNotIn("wireplumber", source.lower(), path.name)
            self.assertNotIn("pw-dump", source, path.name)


class Separation(PlayerTestCase):
    def test_youtube_volume_does_not_change_computer_audio(self):
        """36. Setting the player volume issues one player command and no more."""
        self.play(self.session())
        self.player.commands.clear()
        self.actions.set_volume(20)
        names = {command["command"] for command in self.player.commands}
        self.assertEqual(names, {"set_volume"})

    def test_computer_audio_changes_do_not_overwrite_player_volume(self):
        """37. The player snapshot is built only from what the player reported.

        There is no path from the audio inventory into this snapshot — the
        service holds no audio reference at all — so a system volume change
        cannot rewrite the reported player volume.
        """
        self.play(self.session())
        self.actions.set_volume(30)
        self.assertFalse(hasattr(self.service, "audio"))
        # Simulate the machine's own volume changing: nothing here reads it, so
        # the player's reported level is unchanged.
        snapshot = self.service.snapshot().to_dict()
        self.assertEqual(snapshot["volume"]["volume_percent"], 30)


# -- 42: duplicate submissions ------------------------------------------------


class DuplicateSubmission(PlayerTestCase):
    def test_a_second_write_while_one_is_in_flight_is_refused(self):
        """42. One player, one command at a time."""
        self.play(self.session())
        lock = self.service.write_lock
        lock.acquire()
        self.addCleanup(lock.release)
        with self.assertRaises(YouTubePlayerError) as raised:
            self.actions.pause()
        self.assertEqual(raised.exception.code, CODE_BUSY)


# -- 44-45: the channel vocabulary is closed ---------------------------------


class ChannelVocabulary(unittest.TestCase):
    def test_player_control_vocabulary_is_closed(self):
        """44. Five commands, and no way to send a sixth."""
        self.assertEqual(
            set(COMMANDS),
            {"load_video", "play", "pause", "set_volume", "set_muted"},
        )
        channel = PlayerChannel()
        for hostile in ("eval", "open_url", "navigate", "screenshot", "run", ""):
            with self.assertRaises(ValueError):
                channel.send(hostile)

    def test_player_cannot_invoke_arbitrary_cofferdam_actions(self):
        """45. The player's whole vocabulary is three inbound messages."""
        channel = PlayerChannel()
        instance = channel.register()

        # Everything a player may say. None of these is an action.
        self.assertTrue(channel.submit_state(instance, {"player_state": 1}))
        self.assertTrue(channel.acknowledge(instance, 1))
        self.assertTrue(channel.release(instance))

        # And a state payload carrying action-shaped fields changes nothing:
        # they are simply not read.
        instance = channel.register()
        channel.submit_state(
            instance,
            {
                "player_state": 1,
                "action": "take_screenshot",
                "command": "open_application",
                "url": "https://example.com",
                "exec": "/bin/sh",
            },
        )
        observation = channel.observation()
        self.assertEqual(
            set(observation.to_dict()),
            {
                "observed_at",
                "playback_state",
                "video_handle",
                "current_time_seconds",
                "duration_seconds",
                "volume_percent",
                "muted",
                "error",
            },
        )

    def test_a_superseded_instance_is_refused(self):
        """One player. A second registration retires the first."""
        channel = PlayerChannel()
        first = channel.register()
        second = channel.register()
        self.assertFalse(channel.submit_state(first, {"player_state": 1}))
        self.assertTrue(channel.submit_state(second, {"player_state": 1}))
        self.assertIsNone(channel.collect(first, 0, timeout=0.01))

    def test_a_malformed_video_id_from_the_player_is_dropped(self):
        """The page echoes an id back; it is re-validated rather than trusted."""
        channel = PlayerChannel()
        instance = channel.register()
        for hostile in ("../../etc", "<script>", "short", "a" * 200, 7, None):
            channel.submit_state(instance, {"player_state": 1, "video_id": hostile})
            self.assertIsNone(channel.reported_video_id())

    def test_documented_error_codes_map_to_cofferdam_words(self):
        """Only the documented onError codes produce a state."""
        self.assertEqual(describe_player_error(100)["code"], "youtube_video_unavailable")
        self.assertEqual(describe_player_error(101)["code"], "youtube_embedding_refused")
        self.assertEqual(describe_player_error(150)["code"], "youtube_embedding_refused")
        for undocumented in (0, 1, 3, 4, 99, 999, "100", None, True):
            self.assertIsNone(describe_player_error(undocumented))


# -- 46: what reaches a broad log --------------------------------------------


class Privacy(PlayerTestCase):
    def test_no_titles_or_ids_enter_broad_logs(self):
        """46. The audit record carries an operation and an outcome, no content."""
        from cofferdam.workstation.store import ActionStore

        class _Config:
            max_action_records = 10

            def __init__(self, path):
                self.actions_path = path

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            store = ActionStore(_Config(Path(directory) / "actions.json"))
            store.record_youtube_event("youtube_play_search_result", "ok", "ytop-abc")
            record = store.recent()[0]
            blob = str(record)
            for forbidden in (VIDEO_ID, "A video called something", "A channel", "youtube.com"):
                self.assertNotIn(forbidden, blob)
            self.assertEqual(record["params"], {})

    def test_progress_carries_no_content(self):
        """A phase log is phases and timings, never what was playing."""
        result = self.play(self.session())
        blob = str(result["progress"])
        self.assertNotIn(VIDEO_ID, blob)
        self.assertNotIn("A video called something", blob)

    def test_snapshot_never_publishes_a_video_id(self):
        """The published shape carries handles, not launch targets."""
        session = self.session((VIDEO_ID, OTHER_VIDEO_ID))
        self.play(session, "r0")
        self.actions.queue_search_result(session.search_id, "r1")
        blob = str(self.service.snapshot().to_dict())
        self.assertNotIn(VIDEO_ID, blob)
        self.assertNotIn(OTHER_VIDEO_ID, blob)
        self.assertNotIn("youtube.com", blob)

    def test_snapshot_never_publishes_the_player_instance_secret(self):
        """The instance id authenticates the tab; the handle is a digest of it."""
        self.play(self.session())
        instance = self.player.instance_id
        self.assertTrue(instance)
        self.assertNotIn(instance, str(self.service.snapshot().to_dict()))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
