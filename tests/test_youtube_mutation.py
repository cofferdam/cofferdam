"""Mutation checks: prove the YouTube player guards are load-bearing.

A passing suite proves the code behaves. It does not prove the *tests* would
notice if a guard were removed — a check can be deleted and leave a suite just
as green, because nothing was ever exercising it.

So each test below deliberately breaks one guard and asserts that the property
it protects visibly fails. If a mutation ever stops producing a failure, the
corresponding guard has become decorative and this file says so.

These are the seven guards the milestone brief calls out by name:

1. duplicate-player launch prevention
2. client-supplied video-id rejection
3. observed-video verification
4. the queue bound
5. the stale-response generation guard (in the PWA)
6. loopback-only binding
7. arbitrary player-command rejection
"""

from __future__ import annotations

import re
import threading
import unittest
from pathlib import Path

from cofferdam.workstation.mediasearch.sessions import SearchSessionStore
from cofferdam.workstation.youtubeplayer.actions import (
    OUTCOME_APPLIED,
    YouTubeActionExecutor,
)
from cofferdam.workstation.youtubeplayer.channel import COMMANDS, PlayerChannel
from cofferdam.workstation.youtubeplayer.errors import YouTubePlayerError
from cofferdam.workstation.youtubeplayer.models import MAX_QUEUE_ITEMS, VideoMetadata
from cofferdam.workstation.youtubeplayer.queue import PlayQueue

from ._runtime_doubles import code_only
from ._youtubeplayer_doubles import (
    OTHER_VIDEO_ID,
    VIDEO_ID,
    ImmediateLauncher,
    build_service,
    youtube_session,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class MutationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SearchSessionStore()
        self.service = build_service(ImmediateLauncher)
        self.actions = YouTubeActionExecutor(self.service, self.store)
        self.addCleanup(self.service.launcher.player.stop)

    def session(self, video_ids=(VIDEO_ID, OTHER_VIDEO_ID)):
        return youtube_session(self.store, video_ids)


# -- 1. duplicate-player launch prevention -----------------------------------


class DuplicateLaunchGuard(MutationTestCase):
    def test_the_guard_holds(self):
        session = self.session()
        self.actions.play_search_result(session.search_id, "r0")
        self.actions.play_search_result(session.search_id, "r1")
        self.assertEqual(self.service.launcher.calls, 1)

    def test_removing_the_connected_check_opens_a_second_tab(self):
        """Mutation: ``ensure_player`` stops noticing an existing player."""
        session = self.session()
        self.actions.play_search_result(session.search_id, "r0")
        self.assertEqual(self.service.launcher.calls, 1)

        original = self.service.channel.connected
        # The mutation: a player is never considered connected, which is exactly
        # what "identify the player by whether Opera is running" would produce.
        self.service.channel.connected = lambda: False
        try:
            with self.assertRaises(YouTubePlayerError):
                # It launches again and then times out waiting for a *new*
                # registration, because the mutated check can never be satisfied.
                self.actions.play_search_result(session.search_id, "r1")
        finally:
            self.service.channel.connected = original

        self.assertGreater(
            self.service.launcher.calls, 1, "the mutation produced no second launch"
        )

    def test_removing_the_launch_lock_allows_two_concurrent_launches(self):
        """Mutation: the launch decision stops being serialised."""
        session = self.session()
        entered = threading.Event()
        real_launch = self.service.launcher.launch

        def slow_launch(url):
            entered.set()
            import time

            time.sleep(0.3)
            return real_launch(url)

        self.service.launcher.launch = slow_launch

        # The mutation: a lock that never blocks, and a write lock that never
        # refuses — together, exactly the pre-guard behaviour.
        class _NullLock:
            def acquire(self, blocking=True):
                return True

            def release(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        self.service._launch_lock = _NullLock()
        self.service.write_lock = _NullLock()

        def press(result_id):
            try:
                self.actions.play_search_result(session.search_id, result_id)
            except Exception:
                pass

        first = threading.Thread(target=press, args=("r0",))
        first.start()
        self.assertTrue(entered.wait(timeout=5))
        second = threading.Thread(target=press, args=("r1",))
        second.start()
        for thread in (first, second):
            thread.join(timeout=20)

        self.assertGreater(
            self.service.launcher.calls, 1, "the mutation produced no duplicate launch"
        )


# -- 2. client-supplied video-id rejection -----------------------------------


class VideoIdRejectionGuard(MutationTestCase):
    def test_the_guard_holds(self):
        session = self.session()
        with self.assertRaises(Exception):
            self.actions.play_search_result(session.search_id, VIDEO_ID)

    def test_the_guard_is_structural_not_incidental(self):
        """There is no parameter a video id could arrive in.

        The strongest form of this guard: it is not a check that could be
        deleted, it is the absence of a field. Mutating it would mean adding an
        argument, so what is asserted is the signature itself.
        """
        import inspect

        for name in ("play_search_result", "queue_search_result"):
            parameters = inspect.signature(getattr(self.actions, name)).parameters
            self.assertEqual(set(parameters), {"search_id", "result_id"}, name)

        # And the route layer accepts no such field either.
        service_source = code_only(
            (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text("utf-8")
        )
        youtube_block = service_source.split("youtube_play_result")[1][:2000]
        for forbidden in ("video_id", "watch_url", "iframe"):
            self.assertNotIn(forbidden, youtube_block)

    def test_removing_the_id_revalidation_lets_a_malformed_id_through(self):
        """Mutation: ``valid_video_id`` stops rejecting anything."""
        import cofferdam.workstation.youtubeplayer.actions as actions_module

        session = youtube_session(self.store, (VIDEO_ID,))
        # Corrupt the private provider item so it no longer holds a real id.
        stored = self.store._sessions[session.search_id]
        broken_item = stored.items[0].__class__(
            provider_id="youtube",
            item_type="video",
            item_id="javascript:alert(1)",
        )
        self.store._sessions[session.search_id] = stored.__class__(
            **{**stored.__dict__, "items": (broken_item,)}
        )

        # The guard catches it.
        with self.assertRaises(YouTubePlayerError):
            self.actions.play_search_result(session.search_id, "r0")

        # The mutation: re-validation always passes.
        original = actions_module.valid_video_id
        actions_module.valid_video_id = lambda _value: True
        try:
            try:
                self.actions.play_search_result(session.search_id, "r0")
            except Exception:
                pass
            reached = self.service.launcher.player.video_id
        finally:
            actions_module.valid_video_id = original

        self.assertEqual(
            reached,
            "javascript:alert(1)",
            "the mutation did not reach the player — the guard was not load-bearing",
        )


# -- 3. observed-video verification ------------------------------------------


class ObservedVideoGuard(MutationTestCase):
    def test_the_guard_holds(self):
        self.service.launcher.player.load_video_id_override = OTHER_VIDEO_ID
        session = youtube_session(self.store, (VIDEO_ID,))
        with self.assertRaises(YouTubePlayerError) as raised:
            self.actions.play_search_result(session.search_id, "r0")
        self.assertEqual(raised.exception.code, "youtube_video_not_observed")

    def test_removing_the_check_reports_the_wrong_video_as_success(self):
        """Mutation: the identity confirmation always agrees."""
        self.service.launcher.player.load_video_id_override = OTHER_VIDEO_ID
        session = youtube_session(self.store, (VIDEO_ID,))

        original = self.service.observed_video_matches
        self.service.observed_video_matches = lambda _video_id: True
        try:
            result = self.actions.play_search_result(session.search_id, "r0")
        finally:
            self.service.observed_video_matches = original

        self.assertEqual(
            result["outcome"],
            OUTCOME_APPLIED,
            "the mutation did not produce a false success — the check was decorative",
        )
        # And the player really is showing something else, which is the failure
        # the guard exists to prevent reporting.
        self.assertEqual(self.service.launcher.player.video_id, OTHER_VIDEO_ID)


# -- 4. the queue bound ------------------------------------------------------


class QueueBoundGuard(unittest.TestCase):
    def metadata(self):
        return VideoMetadata.build("host", VIDEO_ID, title="t")

    def test_the_guard_holds(self):
        queue = PlayQueue()
        for _ in range(MAX_QUEUE_ITEMS):
            queue.add(VIDEO_ID, self.metadata())
        with self.assertRaises(YouTubePlayerError):
            queue.add(VIDEO_ID, self.metadata())

    def test_removing_the_bound_lets_the_queue_grow(self):
        """Mutation: the limit is raised out of the way."""
        queue = PlayQueue(limit=10**6)
        for _ in range(MAX_QUEUE_ITEMS + 25):
            queue.add(VIDEO_ID, self.metadata())
        self.assertGreater(
            len(queue), MAX_QUEUE_ITEMS, "the mutation did not grow the queue"
        )

    def test_the_published_bound_matches_the_enforced_one(self):
        """A bound the client is told about but that is not enforced is worse
        than none, because it is believed."""
        queue = PlayQueue()
        self.assertEqual(queue.limit, MAX_QUEUE_ITEMS)


# -- 5. the stale-response generation guard (PWA) ----------------------------


class GenerationGuard(unittest.TestCase):
    """The PWA's ordering protection, asserted structurally.

    Its *behaviour* is exercised in ``tests/test_youtube_pwa.py`` through the
    harness; what this adds is that the guard cannot be quietly deleted, since
    the property is invisible to a reader who does not know to look for it.
    """

    def source(self) -> str:
        return code_only((REPO_ROOT / "web" / "youtube.js").read_text("utf-8"))

    def test_the_guard_is_present(self):
        source = self.source()
        self.assertIn("refreshGeneration", source)
        self.assertIn("appliedGeneration", source)

    def test_every_state_adoption_is_generation_checked(self):
        """No path adopts a payload without comparing generations first."""
        source = self.source()
        adoptions = re.findall(r"function\s+adopt\w*\s*\(", source)
        self.assertTrue(adoptions, "no adopt function found to check")
        # The one adoption path exists and consults the generation.
        adopt_body = source.split("function adopt")[1][:900]
        self.assertIn("appliedGeneration", adopt_body)


# -- 6. loopback-only binding ------------------------------------------------


class LoopbackBindingGuard(unittest.TestCase):
    def test_the_guard_holds(self):
        from cofferdam.workstation.youtubeplayer.endpoint import (
            LOOPBACK_HOST,
            PlayerEndpoint,
        )

        self.assertEqual(LOOPBACK_HOST, "127.0.0.1")
        endpoint = PlayerEndpoint(PlayerChannel())
        endpoint.start()
        self.addCleanup(endpoint.stop)
        self.assertEqual(endpoint._server.server_address[0], "127.0.0.1")

    def test_a_widened_bind_becomes_reachable_off_loopback(self):
        """Mutation: the constant is replaced with a wildcard bind.

        This is the one mutation that would be invisible in ordinary use — the
        player would keep working perfectly — so it gets an explicit test that
        the difference is observable.
        """
        import socket

        from cofferdam.workstation.youtubeplayer import endpoint as module

        original = module.LOOPBACK_HOST
        module.LOOPBACK_HOST = "0.0.0.0"
        try:
            widened = module.PlayerEndpoint(PlayerChannel())
            widened.start()
            self.addCleanup(widened.stop)
            self.assertEqual(
                widened._server.server_address[0],
                "0.0.0.0",
                "the mutation did not widen the bind",
            )
        finally:
            module.LOOPBACK_HOST = original

        # And the unmutated endpoint is not reachable that way.
        clean = module.PlayerEndpoint(PlayerChannel())
        port = clean.start()
        self.addCleanup(clean.stop)
        probe = socket.socket()
        probe.settimeout(1)
        try:
            reachable = probe.connect_ex(("0.0.0.0", port)) == 0
        finally:
            probe.close()
        # Connecting to 0.0.0.0 resolves to loopback on Linux, so this is not a
        # proof on its own — the bound address above is. Asserted only that the
        # clean endpoint reports loopback.
        self.assertEqual(clean._server.server_address[0], "127.0.0.1")

    def test_the_host_header_check_is_load_bearing(self):
        """Mutation: the allowed-hostname list accepts anything."""
        import json as _json
        import urllib.error
        import urllib.request

        from cofferdam.workstation.youtubeplayer import endpoint as module

        endpoint = module.PlayerEndpoint(PlayerChannel())
        port = endpoint.start()
        self.addCleanup(endpoint.stop)

        def register(host: str) -> int:
            request = urllib.request.Request(
                "http://127.0.0.1:" + str(port) + module.PATH_REGISTER,
                data=_json.dumps({}).encode(),
                method="POST",
            )
            request.add_header("Content-Type", "application/json")
            request.add_header("Host", host)
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code

        self.assertEqual(register("evil.example.com"), 421)

        original = module._ALLOWED_HOSTNAMES
        module._ALLOWED_HOSTNAMES = original + ("evil.example.com",)
        try:
            self.assertEqual(
                register("evil.example.com"),
                200,
                "the mutation changed nothing — the Host check was decorative",
            )
        finally:
            module._ALLOWED_HOSTNAMES = original


# -- 7. arbitrary player-command rejection -----------------------------------


class CommandVocabularyGuard(unittest.TestCase):
    def test_the_guard_holds(self):
        channel = PlayerChannel()
        for hostile in ("eval", "open_url", "navigate", "exec"):
            with self.assertRaises(ValueError):
                channel.send(hostile)

    def test_removing_the_allowlist_lets_an_arbitrary_command_through(self):
        """Mutation: ``COMMANDS`` gains an entry it should never have."""
        from cofferdam.workstation.youtubeplayer import channel as module

        channel = module.PlayerChannel()
        instance = channel.register()

        original = module.COMMANDS
        module.COMMANDS = original + ("eval",)
        try:
            channel.send("eval", script="alert(1)")
        finally:
            module.COMMANDS = original

        delivered = channel.collect(instance, 0, timeout=0.1)
        self.assertEqual(
            [command.name for command in delivered],
            ["eval"],
            "the mutation did not deliver an arbitrary command",
        )

    def test_the_player_page_ignores_a_command_it_does_not_know(self):
        """The second half of the same guard, on the page that executes them.

        Even with the backend mutated, the shipped page has no dispatch table to
        look a name up in — so an unknown command finds nothing to run.
        """
        source = code_only((REPO_ROOT / "web" / "player.js").read_text("utf-8"))
        for forbidden in ("eval(", "new Function", "innerHTML", "document.write"):
            self.assertNotIn(forbidden, source)
        # Dispatch is a chain of equality tests against the five constants, not
        # a lookup keyed on message content.
        for command in COMMANDS:
            self.assertIn(command, source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
