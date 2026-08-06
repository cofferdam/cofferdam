"""The dedicated player's lifecycle: make one exist, and say truthfully whether it does.

This module owns the answer to two questions the old behaviour never had to ask,
because it opened a new tab every time and called that success.

**"Is there a player?"** is answered by the heartbeat in :mod:`.channel` and by
nothing else. Not by Opera's process list, not by a pid this module launched, not
by the fact that a launch call returned without raising. A player is a *document
that is currently saying so*.

**"Can I make one?"** is answered by :meth:`PlayerService.ensure_player`, which
is the single place a launch can happen. Its contract is the part of this
milestone most worth reading:

* it launches **at most once per call**, guarded by a lock so two Play now
  presses arriving together cannot each decide to launch;
* it waits a **bounded** time for the player to register, and a player that
  registers late still satisfies the wait that is already parked on it — which
  is what lets the original Play now continue rather than failing and asking the
  user to press again;
* it never launches when a player is already connected, so a second video goes
  to the existing tab;
* a launch that produces no player is a **truthful timeout**, not a second
  launch. The next explicit Play now may launch again — one bounded attempt per
  deliberate press, which is what "allow one relaunch after the tab was closed"
  means in practice.

The snapshot
------------

:meth:`PlayerService.snapshot` assembles three independent things: the connection
state this module tracks, the last observation the *player* reported, and the
queue Cofferdam owns. It never merges in what was requested. If a command asked
for volume 80 and the player has reported 50, the snapshot says 50.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Optional, Tuple

from ..runtime.identity import detect_host_identity, now_iso
from ..spotifyplayer.confirm import ConfirmWindow
from .channel import (
    COMMAND_LOAD,
    COMMAND_PAUSE,
    COMMAND_PLAY,
    COMMAND_SET_MUTED,
    COMMAND_SET_VOLUME,
    PlayerChannel,
)
from .endpoint import PlayerEndpoint
from .errors import (
    CommandNotAcknowledged,
    NoPlayerConnected,
    PlayerGone,
    RegistrationTimeout,
)
from .launcher import PlayerLauncher
from .models import (
    CONNECTION_DISCONNECTED,
    CONNECTION_LAUNCHING,
    CONNECTION_READY,
    CONNECTION_UNAVAILABLE,
    CONNECTION_WAITING,
    PlayerSnapshot,
    QueueItem,
    VideoMetadata,
)
from .progress import PHASE_LAUNCHING, PHASE_WAITING_FOR_PLAYER
from .queue import PlayQueue

#: How long to wait for a freshly launched player to register and report in.
#: Opera has to start (or focus), open a tab, fetch the official IFrame API
#: script, and construct a player. Twenty-five seconds is generous for that on a
#: cold browser and short enough that a phone is not left guessing.
REGISTRATION_WINDOW = ConfirmWindow(attempts=25, interval_seconds=1.0)

#: How long to wait for the player to acknowledge one command. Local, so this is
#: a round trip over loopback plus one iframe API call.
ACK_WINDOW = ConfirmWindow(attempts=10, interval_seconds=0.3)

#: How long to wait for the player to *report the state* a command asked for.
#: Loading a video is the slow one — the iframe fetches it — so it gets its own
#: window, separate from a volume change that is immediate.
LOAD_WINDOW = ConfirmWindow(attempts=20, interval_seconds=0.4)
TRANSPORT_WINDOW = ConfirmWindow(attempts=12, interval_seconds=0.25)
VOLUME_WINDOW = ConfirmWindow(attempts=10, interval_seconds=0.25)


class PlayerService:
    """One dedicated player, its queue, and the lifecycle that keeps it single."""

    def __init__(
        self,
        adapter,
        channel: Optional[PlayerChannel] = None,
        endpoint: Optional[PlayerEndpoint] = None,
        launcher: Optional[PlayerLauncher] = None,
        play_queue: Optional[PlayQueue] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._adapter = adapter
        self.channel = channel or PlayerChannel(clock=clock)
        self.endpoint = endpoint or PlayerEndpoint(self.channel)
        self.launcher = launcher or PlayerLauncher(adapter)
        self.queue = play_queue or PlayQueue()
        self._clock = clock
        self._host_id = detect_host_identity().host_id

        #: Serialises the launch decision. Without it, two Play now presses
        #: arriving together would each see "no player" and each launch a tab —
        #: the exact duplicate this milestone exists to prevent.
        self._launch_lock = threading.Lock()
        #: Serialises *writes* to the player. One player, one command at a time.
        #: Public because the action executor is its only user and acquiring it
        #: non-blocking is how duplicate submissions become a refusal.
        self.write_lock = threading.Lock()

        #: Set only while a launch is in flight, so the phone can render
        #: "launching" / "waiting for player" rather than "disconnected" for the
        #: twenty seconds a cold Opera takes. It is never a substitute for the
        #: heartbeat: the moment a player is connected, the heartbeat wins, and
        #: the moment a launch ends this goes back to ``None``.
        self._transient_state: Optional[str] = None
        self._current_video_id: Optional[str] = None
        self._current_result_handle: Optional[str] = None
        self._current_metadata: Optional[VideoMetadata] = None
        self._last_error: Optional[Dict[str, str]] = None

    # -- identity ------------------------------------------------------------

    @property
    def host_id(self) -> str:
        return self._host_id

    def describe(self, video_id: str, result) -> VideoMetadata:
        """Bounded display metadata for one video, built from the search result.

        The *result* is the source, never the player: the search layer has
        already normalized and bounded these fields once, and building them here
        from a second source would mean two bounding paths for the same text.
        """
        return VideoMetadata.build(
            self._host_id,
            video_id,
            title=getattr(result, "title", None),
            channel=getattr(result, "subtitle", None),
            published=getattr(result, "published", None),
        )

    # -- observation ---------------------------------------------------------

    def available(self) -> bool:
        return self.launcher.available()

    def connection_state(self) -> str:
        """The published connection state, recomputed from live evidence.

        Order matters. A live heartbeat outranks everything, including a launch
        this service believes is still running — a player that reported in *is*
        connected regardless of what the launcher thinks. Only when there is no
        heartbeat does the in-flight launch phase get to speak.
        """
        if self.channel.connected():
            return CONNECTION_READY
        transient = self._transient_state
        if transient is not None:
            return transient
        if not self.available():
            return CONNECTION_UNAVAILABLE
        return CONNECTION_DISCONNECTED

    def snapshot(self) -> PlayerSnapshot:
        """One consistent view of the player, the current video, and the queue."""
        state = self.connection_state()
        observation = self.channel.observation()
        items, index = self.queue.snapshot()

        if state != CONNECTION_READY:
            # A disconnected player has no current video. Keeping the last one
            # would leave the phone showing something as "current" when nothing
            # is loaded anywhere — a small false claim, and exactly the kind this
            # milestone is about.
            current_video = None
            result_handle = None
        else:
            current_video = self._current_metadata
            result_handle = self._current_result_handle
            if observation is not None and current_video is not None:
                observation = _with_handle(observation, current_video.handle)

        return PlayerSnapshot(
            connection_state=state,
            observed_at=now_iso(),
            player_resource_id=self.channel.player_resource_id() if state == CONNECTION_READY else None,
            current_result_handle=result_handle,
            current_video=current_video,
            observation=observation,
            queue=items,
            queue_index=index,
            last_error=self._last_error,
        )

    # -- lifecycle -----------------------------------------------------------

    def ensure_player(self, progress=None, allow_launch: bool = True) -> None:
        """Make a connected player exist, launching **at most once**.

        Raises :class:`~.errors.RegistrationTimeout` when a launch happened and
        no player reported in, and :class:`~.errors.NoPlayerConnected` when
        launching was not permitted. Both are truthful outcomes; neither is
        retried here.
        """
        if self.channel.connected():
            return
        if not allow_launch:
            raise NoPlayerConnected()

        with self._launch_lock:
            # Re-checked inside the lock. A player that registered while this
            # call was waiting for the lock — typically the *other* Play now
            # that just launched it — means there is nothing to do, and this is
            # what stops the second press opening a second tab.
            if self.channel.connected():
                return

            try:
                self._enter(CONNECTION_LAUNCHING, PHASE_LAUNCHING, progress)
                url = self.endpoint.player_url()
                self.launcher.launch(url)

                self._enter(CONNECTION_WAITING, PHASE_WAITING_FOR_PLAYER, progress)
                registered = self.channel.wait_for_registration(
                    REGISTRATION_WINDOW.timeout_seconds
                )
            finally:
                # Cleared whatever happened, including on a launcher refusal.
                # A transient phase that outlived its operation would leave the
                # phone showing "opening…" forever.
                self._transient_state = None

        if not registered:
            raise RegistrationTimeout(int(REGISTRATION_WINDOW.timeout_seconds))

    def _enter(self, connection_state: str, phase: str, progress) -> None:
        self._transient_state = connection_state
        if progress is not None:
            progress.enter(phase)

    # -- sending commands ----------------------------------------------------

    def _send_and_acknowledge(self, name: str, **payload) -> None:
        """Deliver one command and wait, bounded, for the player to run it.

        Acknowledgement is not evidence that the *state* changed — that is
        confirmed separately against what the player reports. It is evidence the
        command was received and executed, which is what distinguishes "the tab
        is wedged" from "the tab did it and YouTube refused".
        """
        if not self.channel.connected():
            raise PlayerGone()
        command = self.channel.send(name, **payload)
        acknowledged = self.channel.wait_for(
            lambda: self.channel.acknowledged() >= command.sequence,
            ACK_WINDOW.timeout_seconds,
        )
        if not acknowledged:
            if not self.channel.connected():
                raise PlayerGone()
            raise CommandNotAcknowledged(int(ACK_WINDOW.timeout_seconds))

    def load_video(self, item: QueueItem, autoplay: bool = True) -> None:
        """Load one verified video into the existing player.

        The only thing that crosses the channel is the eleven-character id the
        search session verified, plus a boolean. No URL, no title, no embed
        parameters: the player document owns how a video id becomes an embed,
        and it applies the same rules every time.
        """
        self.channel.expect_video(item.video_id)
        self._send_and_acknowledge(
            COMMAND_LOAD, video_id=item.video_id, autoplay=bool(autoplay)
        )
        self._current_video_id = item.video_id
        self._current_metadata = item.metadata
        self._last_error = None

    def request_play(self) -> None:
        self._send_and_acknowledge(COMMAND_PLAY)

    def request_pause(self) -> None:
        self._send_and_acknowledge(COMMAND_PAUSE)

    def request_volume(self, volume_percent: int) -> None:
        self._send_and_acknowledge(COMMAND_SET_VOLUME, volume_percent=int(volume_percent))

    def request_mute(self, muted: bool) -> None:
        self._send_and_acknowledge(COMMAND_SET_MUTED, muted=bool(muted))

    # -- confirming ----------------------------------------------------------

    def confirm(self, predicate: Callable[[], bool], window: ConfirmWindow) -> bool:
        """Bounded wait for something the *player* reports to become true.

        Wraps :meth:`PlayerChannel.wait_for` so every confirmation in this
        package shares one bound and one wake-up path. The predicate is always
        written against reported state; nothing here knows what was requested,
        which is what makes it impossible for this to echo a request as an
        observation.
        """
        return self.channel.wait_for(predicate, window.timeout_seconds)

    def observed_video_matches(self, video_id: str) -> bool:
        return self.channel.reported_video_id() == video_id

    def set_result_handle(self, handle: Optional[str]) -> None:
        self._current_result_handle = handle

    def note_error(self, error: Optional[Dict[str, str]]) -> None:
        self._last_error = dict(error) if error else None

    # -- shutdown ------------------------------------------------------------

    def stop(self) -> None:
        """Release the loopback listener. The queue dies with the process."""
        self.endpoint.stop()


def _with_handle(observation, handle: str):
    """Attach the current video's opaque handle to a player observation.

    The player reports a video *id*; the snapshot publishes a *handle*. The
    mapping lives here rather than in the channel because the channel has no
    business knowing how identities are published.
    """
    from dataclasses import replace

    return replace(observation, video_handle=handle)


__all__ = [
    "ACK_WINDOW",
    "LOAD_WINDOW",
    "REGISTRATION_WINDOW",
    "TRANSPORT_WINDOW",
    "VOLUME_WINDOW",
    "PlayerService",
]
