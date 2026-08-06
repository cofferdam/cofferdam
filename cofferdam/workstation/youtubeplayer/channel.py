"""The bounded control channel between Cofferdam and one player document.

This module is the trust boundary. Everything it accepts from the player page is
untrusted input from a browser tab, and everything it sends is a command from a
closed vocabulary. Neither side can widen the other's surface:

* **Cofferdam cannot ask the player to do anything but play a video.** The
  command set below is a Python tuple. There is no "eval", no "navigate", no
  "open", no field carrying a URL, and no passthrough. A player page that
  received an unknown command name would find nothing to run.
* **The player cannot ask Cofferdam to do anything at all.** Its entire
  vocabulary is *register*, *report your state*, and *acknowledge a command*.
  There is no message that starts an action, opens an application, reads a file
  or reaches any other part of the workstation API. A compromised player page
  gets to lie about what is playing; it does not get a foothold.

Identity, and why a process id would be wrong
---------------------------------------------

A player is identified by an ``instance_id`` this module mints at registration,
not by Opera's process id and not by "a browser is running". Opera is one
process with many tabs, it survives the tab closing, and it is frequently
already open for something else entirely. A player instance is a *document*, and
the only honest evidence a document exists is that it keeps saying so.

So the connection state is derived from a **heartbeat**: the player posts its
observed state on a fixed interval, and a player that has not reported inside
:data:`STALE_AFTER_SECONDS` is disconnected. Closing the tab stops the posts, so
tab closure is detected by the same mechanism, with no extra machinery and no
way for "Opera is running" to be mistaken for "a player is connected".

One player, and superseding
---------------------------

At most one instance is current. A second registration — a user who opened the
page twice, or a tab that reloaded — becomes current and the older instance is
superseded: its long poll is released and its posts are refused. That is what
keeps "one persistent player" true without needing to find and close a tab.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..runtime.identity import fingerprint, now_iso
from .errors import describe_player_error
from .models import (
    PLAYBACK_AUTOPLAY_BLOCKED,
    PLAYBACK_BUFFERING,
    PLAYBACK_CUEING,
    PLAYBACK_ENDED,
    PLAYBACK_ERROR,
    PLAYBACK_IDLE,
    PLAYBACK_PAUSED,
    PLAYBACK_PLAYING,
    PlayerObservation,
    bounded_percent,
    bounded_seconds,
)

# -- what Cofferdam may send -------------------------------------------------
#
# The complete vocabulary. A closed tuple rather than a convention, so "can the
# backend tell the player to do X" is answerable by reading five lines.

COMMAND_LOAD = "load_video"
COMMAND_PLAY = "play"
COMMAND_PAUSE = "pause"
COMMAND_SET_VOLUME = "set_volume"
COMMAND_SET_MUTED = "set_muted"

COMMANDS: Tuple[str, ...] = (
    COMMAND_LOAD,
    COMMAND_PLAY,
    COMMAND_PAUSE,
    COMMAND_SET_VOLUME,
    COMMAND_SET_MUTED,
)

# -- what the player may say -------------------------------------------------

MESSAGE_REGISTER = "register"
MESSAGE_STATE = "state"
MESSAGE_ACK = "ack"

MESSAGES: Tuple[str, ...] = (MESSAGE_REGISTER, MESSAGE_STATE, MESSAGE_ACK)

# -- the documented IFrame Player API state numbers --------------------------
#
# Verified against the official reference on 2026-08-06. Mapped rather than
# passed through: a client branching on ``-1`` would be coupled to YouTube's
# constants instead of Cofferdam's contract.

_PLAYER_STATES: Dict[int, str] = {
    -1: PLAYBACK_IDLE,      # unstarted
    0: PLAYBACK_ENDED,      # ended
    1: PLAYBACK_PLAYING,    # playing
    2: PLAYBACK_PAUSED,     # paused
    3: PLAYBACK_BUFFERING,  # buffering
    5: PLAYBACK_CUEING,     # video cued
}

#: How often the player page reports in. Low enough that a closed tab is noticed
#: while someone is still looking at the phone, high enough that it is nothing.
HEARTBEAT_SECONDS = 2.0

#: A player that has not reported within this window is gone. Four missed
#: heartbeats: generous enough to survive a stalled frame, short enough that a
#: closed tab does not linger as "connected".
STALE_AFTER_SECONDS = 8.0

#: How long a command long-poll may block before answering empty. Bounded so a
#: connection is never held indefinitely, and short enough that a released
#: instance notices promptly.
POLL_WAIT_SECONDS = 20.0

#: A command nobody collected within this window is dropped rather than
#: delivered late to a player that has since loaded something else.
COMMAND_TTL_SECONDS = 15.0

#: Commands are held for redelivery only while this many are outstanding; the
#: player acknowledges each one, so the list is normally empty or one long.
MAX_PENDING_COMMANDS = 8

_INSTANCE_BYTES = 24


@dataclass(frozen=True)
class Command:
    """One typed instruction, addressed to the current player instance.

    ``sequence`` is monotonic per channel. It is what lets the player ignore a
    command it has already run after a reconnect, and what lets the backend say
    "the player acknowledged *this* command" rather than "some command".
    """

    sequence: int
    name: str
    payload: Dict[str, Any]
    issued_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {"sequence": self.sequence, "command": self.name, **self.payload}


class PlayerChannel:
    """The registry of the one current player, and the queue of its commands.

    Thread-safe throughout: loopback handler threads post state and long-poll for
    commands while action threads enqueue them.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        # Reentrant, and that is load-bearing rather than defensive. `wait_for`
        # holds this lock while evaluating a caller's predicate, and those
        # predicates are naturally written against the public readers here —
        # `observation()`, `reported_video_id()`, `acknowledged()` — each of
        # which takes the lock itself. With a plain Lock the first such
        # predicate deadlocks the service on its own state.
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)

        self._instance_id: Optional[str] = None
        self._registered_at: Optional[float] = None
        self._last_seen: Optional[float] = None
        self._observation: Optional[PlayerObservation] = None
        self._sequence = 0
        self._pending: List[Command] = []
        self._acknowledged = 0
        #: Set when the current video was loaded by us, so a state report can be
        #: checked against what was actually asked for.
        self._expected_video_id: Optional[str] = None
        self._reported_video_id: Optional[str] = None

    # -- registration --------------------------------------------------------

    def register(self) -> str:
        """Mint a new instance, superseding any previous one. Returns its id.

        The id is a secret shared with exactly one browser tab: it is how a
        subsequent state post proves it comes from the player Cofferdam is
        talking to rather than from a stale tab. It is never published — the
        snapshot carries :meth:`player_resource_id`, a digest of it.
        """
        with self._lock:
            self._instance_id = secrets.token_urlsafe(_INSTANCE_BYTES)
            now = self._clock()
            self._registered_at = now
            self._last_seen = now
            self._observation = PlayerObservation()
            self._pending = []
            self._expected_video_id = None
            self._reported_video_id = None
            # Release the previous instance's long poll, if one is parked.
            self._wake.notify_all()
            return self._instance_id

    def release(self, instance_id: object) -> bool:
        """Forget an instance, if it is the current one. Used on tab unload."""
        with self._lock:
            if not self._is_current(instance_id):
                return False
            self._instance_id = None
            self._registered_at = None
            self._last_seen = None
            self._observation = None
            self._pending = []
            self._expected_video_id = None
            self._reported_video_id = None
            self._wake.notify_all()
            return True

    def _is_current(self, instance_id: object) -> bool:
        return (
            isinstance(instance_id, str)
            and self._instance_id is not None
            and secrets.compare_digest(instance_id, self._instance_id)
        )

    # -- connection ----------------------------------------------------------

    def connected(self) -> bool:
        """Whether a player has reported in recently enough to be believed."""
        with self._lock:
            return self._connected_locked()

    def _connected_locked(self) -> bool:
        if self._instance_id is None or self._last_seen is None:
            return False
        return (self._clock() - self._last_seen) <= STALE_AFTER_SECONDS

    def player_resource_id(self) -> Optional[str]:
        """The opaque, publishable handle for the current player.

        A digest of the instance id, so the snapshot can say "this is a
        different player than the one you saw before" without ever publishing
        the value that authenticates the player's posts.
        """
        with self._lock:
            if self._instance_id is None:
                return None
            return "ytp-" + fingerprint("cofferdam.youtube.player", self._instance_id)

    def observation(self) -> Optional[PlayerObservation]:
        with self._lock:
            return self._observation if self._connected_locked() else None

    def reported_video_id(self) -> Optional[str]:
        """The video the player last said it had loaded. Never published."""
        with self._lock:
            return self._reported_video_id if self._connected_locked() else None

    # -- the player reporting in --------------------------------------------

    def submit_state(self, instance_id: object, payload: object) -> bool:
        """Accept one bounded state report from the current player.

        Returns ``False`` for anything that is not the current instance, which
        the endpoint turns into a refusal telling a superseded tab to stop. Every
        field is normalized through the bounded coercions in
        :mod:`.models`; nothing is copied from the payload wholesale, and a field
        that fails to coerce becomes ``None`` rather than a rejection — a player
        reporting a nonsense duration should still be able to report that it is
        playing.
        """
        if not isinstance(payload, dict):
            return False
        with self._lock:
            if not self._is_current(instance_id):
                return False
            self._last_seen = self._clock()

            raw_state = payload.get("player_state")
            state = _PLAYER_STATES.get(raw_state) if not isinstance(raw_state, bool) else None
            if state is None:
                state = PLAYBACK_IDLE

            error = describe_player_error(payload.get("error_code"))
            if error is not None:
                state = PLAYBACK_ERROR
            elif payload.get("autoplay_blocked") is True and state != PLAYBACK_PLAYING:
                # Only while it is not playing: a blocked autoplay followed by a
                # successful manual start must not keep reporting as blocked.
                state = PLAYBACK_AUTOPLAY_BLOCKED

            video_id = payload.get("video_id")
            self._reported_video_id = video_id if _plausible_video_id(video_id) else None

            muted = payload.get("muted")
            self._observation = PlayerObservation(
                observed_at=now_iso(),
                playback_state=state,
                video_handle=None,  # filled in by the service, which owns the mapping
                current_time_seconds=bounded_seconds(payload.get("current_time")),
                duration_seconds=bounded_seconds(payload.get("duration")),
                volume_percent=bounded_percent(payload.get("volume")),
                muted=muted if isinstance(muted, bool) else None,
                error=error,
            )
            self._wake.notify_all()
            return True

    def acknowledge(self, instance_id: object, sequence: object) -> bool:
        """Record that the player ran the command with this sequence number."""
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            return False
        with self._lock:
            if not self._is_current(instance_id):
                return False
            self._last_seen = self._clock()
            if sequence > self._acknowledged:
                self._acknowledged = sequence
            self._pending = [c for c in self._pending if c.sequence > sequence]
            self._wake.notify_all()
            return True

    def acknowledged(self) -> int:
        with self._lock:
            return self._acknowledged

    # -- Cofferdam issuing commands -----------------------------------------

    def send(self, name: str, **payload: Any) -> Command:
        """Enqueue one typed command for the current player.

        ``name`` must be in :data:`COMMANDS`. That check is not defensive
        politeness: it is the assertion that this channel has no passthrough, and
        it raises rather than dropping so a programming mistake cannot become a
        silently ignored command.
        """
        if name not in COMMANDS:  # pragma: no cover - construction invariant
            raise ValueError("unknown player command")
        with self._lock:
            self._sequence += 1
            command = Command(
                sequence=self._sequence,
                name=name,
                payload=dict(payload),
                issued_at=self._clock(),
            )
            self._pending.append(command)
            if len(self._pending) > MAX_PENDING_COMMANDS:
                self._pending = self._pending[-MAX_PENDING_COMMANDS:]
            self._wake.notify_all()
            return command

    def collect(
        self, instance_id: object, after: int, timeout: float = POLL_WAIT_SECONDS
    ) -> Optional[List[Command]]:
        """Block until there is a command after ``after``, or the window closes.

        Returns ``None`` when the caller is not (or is no longer) the current
        instance — the signal a superseded tab uses to stop and close itself —
        and a possibly-empty list otherwise. The wait is bounded by ``timeout``
        in every path, so a parked connection cannot be held open indefinitely.
        """
        deadline = self._clock() + max(0.0, timeout)
        with self._lock:
            while True:
                if not self._is_current(instance_id):
                    return None
                fresh = [
                    command
                    for command in self._pending
                    if command.sequence > after
                    and (self._clock() - command.issued_at) <= COMMAND_TTL_SECONDS
                ]
                if fresh:
                    return fresh
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return []
                self._wake.wait(min(remaining, 1.0))

    # -- what the backend asked for -----------------------------------------

    def expect_video(self, video_id: Optional[str]) -> None:
        """Record which video the next state reports should be describing."""
        with self._lock:
            self._expected_video_id = video_id

    def expected_video_id(self) -> Optional[str]:
        with self._lock:
            return self._expected_video_id

    # -- waiting on the player ----------------------------------------------

    def wait_for(
        self, predicate: Callable[[], bool], timeout: float, tick: float = 0.1
    ) -> bool:
        """Bounded wait for a condition over channel state.

        One place, so no caller writes its own polling loop. The wait is woken by
        every state post, so a player that answers quickly costs nothing; the
        timeout is a hard ceiling and never a retry count.
        """
        deadline = self._clock() + max(0.0, timeout)
        with self._lock:
            while True:
                if predicate():
                    return True
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return False
                self._wake.wait(min(remaining, tick))

    def wait_for_registration(self, timeout: float) -> bool:
        """Bounded wait for *any* player to register and report in."""
        return self.wait_for(self._connected_locked, timeout)


def _plausible_video_id(value: object) -> bool:
    """The same shape check the search adapter applies, applied again here.

    The player is echoing back an id Cofferdam gave it, so this should always
    pass. It is checked anyway because "should always" is the assumption that
    turns a compromised tab into a stored value nothing else re-validates.
    """
    if not isinstance(value, str) or len(value) != 11:
        return False
    return all(character.isalnum() or character in "_-" for character in value)


__all__ = [
    "COMMANDS",
    "COMMAND_LOAD",
    "COMMAND_PAUSE",
    "COMMAND_PLAY",
    "COMMAND_SET_MUTED",
    "COMMAND_SET_VOLUME",
    "COMMAND_TTL_SECONDS",
    "HEARTBEAT_SECONDS",
    "MESSAGES",
    "MESSAGE_ACK",
    "MESSAGE_REGISTER",
    "MESSAGE_STATE",
    "POLL_WAIT_SECONDS",
    "STALE_AFTER_SECONDS",
    "Command",
    "PlayerChannel",
]
