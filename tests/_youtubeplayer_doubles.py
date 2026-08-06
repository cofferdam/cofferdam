"""Test doubles for the YouTube dedicated player.

Same choice the Spotify doubles made, for the same reason: the fake sits at the
**edge**, not inside the logic. Two things are faked here and nothing else —

* the **adapter**, so no browser is launched during a test run;
* the **player document**, so no Opera and no YouTube are involved.

Everything between them is the code that ships: the channel, its heartbeat and
its command vocabulary, the queue, the lifecycle, the confirmations, the
snapshot assembly and the action executor.

:class:`FakePlayer` is *stateful* on purpose, and models a small browser tab
rather than a scripted queue of replies. It really holds a video id, a playback
state, a volume and a mute flag; a pause really stops it and the service's
re-read really observes that. A scripted double would have to be written in the
exact order the implementation happens to call things, and would then pass or
fail on call ordering rather than on behaviour — which is precisely the bug
class this milestone is about.

It talks to the channel through the **same public methods the loopback endpoint
uses**, so a test exercises the real registration, the real command delivery and
the real acknowledgement path. The tests that need to prove the *HTTP* boundary
— loopback binding, the Host check, the content-type rule — drive a real socket
instead; see ``tests/test_youtube_endpoint.py``.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from cofferdam.workstation.mediasearch.results import (
    RESULT_TYPE_TRACK,
    RESULT_TYPE_VIDEO,
    MediaResult,
    MediaSearchOutcome,
    ProviderItem,
)
from cofferdam.workstation.mediasearch.sessions import SearchSessionStore
from cofferdam.workstation.youtubeplayer.channel import (
    COMMAND_LOAD,
    COMMAND_PAUSE,
    COMMAND_PLAY,
    COMMAND_SET_MUTED,
    COMMAND_SET_VOLUME,
    PlayerChannel,
)
from cofferdam.workstation.youtubeplayer.service import PlayerService

# Real-looking YouTube video ids: eleven characters of the URL-safe base-64
# alphabet, which is what the shipped validator accepts.
VIDEO_ID = "dQw4w9WgXcQ"
OTHER_VIDEO_ID = "9bZkp7q19f0"
THIRD_VIDEO_ID = "kJQP7kiw5Fk"

# The documented IFrame Player API state numbers.
STATE_UNSTARTED = -1
STATE_ENDED = 0
STATE_PLAYING = 1
STATE_PAUSED = 2
STATE_BUFFERING = 3
STATE_CUED = 5


class FakeAdapter:
    """The narrowest adapter a player launch touches.

    Records every ``open_url`` call, which is how the single-launch tests count
    tabs. ``name``/``stub`` exist because the service constructor reads them.
    """

    name = "fake"
    stub = False

    def __init__(self, applications=("opera",), fail_launch: bool = False) -> None:
        self._applications = tuple(applications)
        self.fail_launch = fail_launch
        self.opened_urls: List[str] = []
        self.opened_applications: List[Optional[str]] = []
        self._lock = threading.Lock()

    def available_applications(self):
        return self._applications

    def open_url(self, url: str, application: Optional[str] = None):
        with self._lock:
            self.opened_urls.append(url)
            self.opened_applications.append(application)
        if self.fail_launch:
            raise RuntimeError("launch refused by this fake host")

        class _Launch:
            pid = 4321

        return _Launch()

    @property
    def launch_count(self) -> int:
        with self._lock:
            return len(self.opened_urls)


class FakePlayer:
    """A browser tab that is not a browser.

    Drives the shipped :class:`~cofferdam.workstation.youtubeplayer.channel.PlayerChannel`
    exactly as ``web/player.js`` drives it over loopback: register, then poll for
    commands, run them, acknowledge, and post state on a heartbeat.

    Every knob a test needs to make the browser misbehave is here and is
    explicit — refuse autoplay, report a YouTube error code, load a different
    video than the one asked for, go silent as though the tab were closed. None
    of them is a special case in the shipped code; they are all just states a
    real tab can be in.
    """

    def __init__(self, channel: PlayerChannel) -> None:
        self.channel = channel
        self.instance_id: Optional[str] = None

        # What this "tab" currently is.
        self.video_id: Optional[str] = None
        self.player_state: int = STATE_UNSTARTED
        self.volume: int = 100
        self.muted: bool = False
        self.error_code: Optional[int] = None
        self.autoplay_blocked: bool = False

        # Behaviour switches for the unhappy paths.
        self.refuse_autoplay = False
        self.ignore_commands = False
        self.acknowledge = True
        self.load_video_id_override: Optional[str] = None
        self.error_on_load: Optional[int] = None

        self.commands: List[Dict[str, Any]] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle -----------------------------------------------------------

    def register(self) -> str:
        self.instance_id = self.channel.register()
        self.beat()
        return self.instance_id

    def start(self, poll_interval: float = 0.01) -> "FakePlayer":
        """Register and run the poll/heartbeat loop in a background thread.

        Mirrors the real page: one poll at a time, re-armed after the previous
        one finished, plus a state post on every tick.
        """
        self.register()
        self._running = True

        def loop() -> None:
            while self._running:
                commands = self.channel.collect(
                    self.instance_id, self._last_sequence, timeout=poll_interval
                )
                if commands is None:  # superseded
                    return
                for command in commands:
                    self._last_sequence = max(self._last_sequence, command.sequence)
                    self.run(command)
                    if self.acknowledge:
                        self.channel.acknowledge(self.instance_id, command.sequence)
                self.beat()

        self._last_sequence = 0
        self._thread = threading.Thread(target=loop, name="fake-player", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop reporting in. This is what closing the tab looks like."""
        self._running = False
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        self._thread = None

    _last_sequence = 0

    # -- the channel ---------------------------------------------------------

    def beat(self) -> bool:
        """One state post, exactly as the page sends it."""
        return self.channel.submit_state(self.instance_id, self.state())

    def state(self) -> Dict[str, Any]:
        return {
            "player_state": self.player_state,
            "video_id": self.video_id,
            "current_time": 12,
            "duration": 213,
            "volume": self.volume,
            "muted": self.muted,
            "error_code": self.error_code,
            "autoplay_blocked": self.autoplay_blocked,
        }

    def pump(self, timeout: float = 0.2) -> List[Any]:
        """Collect and run whatever is waiting, synchronously. Returns commands.

        The deterministic alternative to :meth:`start` — used where a test wants
        to control exactly when the "browser" reacts.
        """
        commands = self.channel.collect(self.instance_id, self._last_sequence, timeout=timeout)
        if not commands:
            return []
        for command in commands:
            self._last_sequence = max(self._last_sequence, command.sequence)
            self.run(command)
            if self.acknowledge:
                self.channel.acknowledge(self.instance_id, command.sequence)
        self.beat()
        return commands

    def run(self, command) -> None:
        """Execute one command, the way web/player.js does."""
        self.commands.append(command.to_dict())
        if self.ignore_commands:
            return
        name = command.name
        payload = command.payload

        if name == COMMAND_LOAD:
            # ``load_video_id_override`` models the one thing this milestone
            # must never report as success: a player showing something other
            # than what was asked for.
            self.video_id = self.load_video_id_override or payload.get("video_id")
            self.error_code = self.error_on_load
            if self.error_on_load is not None:
                self.player_state = STATE_UNSTARTED
                return
            if payload.get("autoplay") and not self.refuse_autoplay:
                self.player_state = STATE_PLAYING
                self.autoplay_blocked = False
            elif payload.get("autoplay") and self.refuse_autoplay:
                # What the documented onAutoplayBlocked event looks like: the
                # video is cued and the browser will not start it.
                self.player_state = STATE_CUED
                self.autoplay_blocked = True
            else:
                self.player_state = STATE_CUED
            return

        if name == COMMAND_PLAY:
            if self.refuse_autoplay:
                self.autoplay_blocked = True
                self.player_state = STATE_CUED
                return
            self.player_state = STATE_PLAYING
            self.autoplay_blocked = False
            return

        if name == COMMAND_PAUSE:
            self.player_state = STATE_PAUSED
            return

        if name == COMMAND_SET_VOLUME:
            level = payload.get("volume_percent")
            if isinstance(level, int) and 0 <= level <= 100:
                self.volume = level
                if level > 0 and self.muted:
                    self.muted = False
            return

        if name == COMMAND_SET_MUTED:
            wanted = payload.get("muted")
            if isinstance(wanted, bool):
                self.muted = wanted
            return
        # Anything else is ignored, exactly as the shipped page ignores it.


def youtube_result(index: int = 0, video_id: str = VIDEO_ID) -> tuple:
    """One normalized YouTube video result and its private provider item."""
    result = MediaResult(
        provider_id="youtube",
        result_id="r" + str(index),
        result_type=RESULT_TYPE_VIDEO,
        title="A video called something",
        subtitle="A channel",
        creators=("A channel",),
        published="2024-01-02",
    )
    item = ProviderItem(provider_id="youtube", item_type=RESULT_TYPE_VIDEO, item_id=video_id)
    return result, item


def youtube_session(store: SearchSessionStore, video_ids=(VIDEO_ID,)):
    """A live YouTube search session holding ``video_ids``."""
    results = []
    items = []
    for index, video_id in enumerate(video_ids):
        result, item = youtube_result(index, video_id)
        results.append(result)
        items.append(item)
    return store.create(
        MediaSearchOutcome(
            provider_id="youtube",
            query="something",
            results=tuple(results),
            items=tuple(items),
        )
    )


def spotify_session(store: SearchSessionStore):
    """A live *Spotify* session — the cross-provider case that must fail closed."""
    result = MediaResult(
        provider_id="spotify",
        result_id="r0",
        result_type=RESULT_TYPE_TRACK,
        title="A track",
        subtitle="An artist",
    )
    item = ProviderItem(
        provider_id="spotify", item_type=RESULT_TYPE_TRACK, item_id="3n3Ppam7vgaVa1iaRUc9Lp"
    )
    return store.create(
        MediaSearchOutcome(
            provider_id="spotify", query="a track", results=(result,), items=(item,)
        )
    )


def non_video_session(store: SearchSessionStore):
    """A YouTube session whose result is not a video.

    The search adapter cannot currently produce one — it re-checks
    ``id.kind`` — but the player must still refuse it rather than rely on that,
    which is what this exists to prove.
    """
    result = MediaResult(
        provider_id="youtube",
        result_id="r0",
        result_type="playlist",
        title="A playlist",
    )
    item = ProviderItem(provider_id="youtube", item_type="playlist", item_id="PL1234567890")
    return store.create(
        MediaSearchOutcome(
            provider_id="youtube", query="a playlist", results=(result,), items=(item,)
        )
    )


class ImmediateLauncher:
    """A launcher whose "browser" registers a player the moment it is called.

    The happy path: Opera opens, the tab loads, the player reports in. Counting
    :attr:`calls` is how the tests assert that exactly one tab was opened.
    """

    def __init__(self, channel: PlayerChannel, player: Optional[FakePlayer] = None) -> None:
        self.channel = channel
        self.player = player or FakePlayer(channel)
        self.calls = 0
        self.urls: List[str] = []
        self.available_flag = True

    def available(self) -> bool:
        return self.available_flag

    def launch(self, player_url: str):
        self.calls += 1
        self.urls.append(player_url)
        self.player.start()
        return 1234


class DelayedLauncher(ImmediateLauncher):
    """A launcher whose player registers after ``delay`` seconds.

    Models a cold Opera. The point of the tests that use it is that the *first*
    Play now keeps waiting and then continues, rather than failing and asking
    the user to press again.
    """

    def __init__(self, channel: PlayerChannel, delay: float = 0.4, player=None) -> None:
        super().__init__(channel, player)
        self.delay = delay

    def launch(self, player_url: str):
        self.calls += 1
        self.urls.append(player_url)

        def later() -> None:
            time.sleep(self.delay)
            self.player.start()

        threading.Thread(target=later, daemon=True).start()
        return 1234


class SilentLauncher(ImmediateLauncher):
    """A launcher whose player never registers. Models a tab that never opened."""

    def launch(self, player_url: str):
        self.calls += 1
        self.urls.append(player_url)
        return 1234


def build_service(launcher_factory=ImmediateLauncher, adapter=None, **kwargs) -> PlayerService:
    """A :class:`PlayerService` wired to fakes, with no socket and no browser.

    The endpoint is replaced by a stub whose ``player_url`` is a constant, so
    nothing in these tests binds a port. The tests that must prove the real
    listener's properties bind one deliberately.
    """
    channel = PlayerChannel()
    launcher = launcher_factory(channel, **kwargs)
    service = PlayerService(
        adapter=adapter or FakeAdapter(),
        channel=channel,
        endpoint=_StubEndpoint(),
        launcher=launcher,
    )
    return service


class _StubEndpoint:
    """Stands in for the loopback listener without binding anything.

    Carries ``player_origin`` as well as ``player_url`` because the real endpoint
    does, and the two must agree: the origin the page declares to YouTube and the
    address Opera is pointed at are the same origin or the embed is refused.
    """

    PORT = 45999

    def __init__(self) -> None:
        self.stopped = False

    def player_origin(self) -> str:
        return "http://127.0.0.1:" + str(self.PORT)

    def player_url(self) -> str:
        return self.player_origin() + "/player"

    def stop(self) -> None:
        self.stopped = True


def wait_until(predicate, timeout: float = 3.0, interval: float = 0.01) -> bool:
    """Poll ``predicate`` until true or the window closes. Bounded, like the code."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


__all__ = [
    "OTHER_VIDEO_ID",
    "STATE_BUFFERING",
    "STATE_CUED",
    "STATE_ENDED",
    "STATE_PAUSED",
    "STATE_PLAYING",
    "STATE_UNSTARTED",
    "THIRD_VIDEO_ID",
    "VIDEO_ID",
    "DelayedLauncher",
    "FakeAdapter",
    "FakePlayer",
    "ImmediateLauncher",
    "SilentLauncher",
    "build_service",
    "non_video_session",
    "spotify_session",
    "wait_until",
    "youtube_result",
    "youtube_session",
]
