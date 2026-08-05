"""Bounded confirmation: look again, a few times, and then stop.

Written after real validation found the same bug twice. Setting the volume to 80%
reported *"set to 80% but the device reports 50%"*, and the first Play now
reported *"playing something other than the track you chose"* — both while
Spotify was doing exactly what was asked. The cause in both cases was a single
**immediate** read: Spotify's player endpoints are eventually consistent, so the
read that happens microseconds after a write frequently still describes the world
before it.

The old code was not wrong to check. It was wrong to check **once**. So this
module re-reads on a bounded schedule until the observation matches, or until it
gives up and says so.

Three properties, all load-bearing:

**The first read is immediate.** When Spotify is already consistent — the common
case — confirmation costs exactly what it cost before and adds no latency.

**The number of reads is a constant, not a condition.** Every window below is a
fixed attempt count times a fixed interval. There is no "until it works", because
Spotify rate-limits over a rolling 30-second window and a loop that kept trying
would turn one slow device into a burst of requests against an account that is
already struggling.

**Giving up is an outcome, not an exception.** :func:`confirm` returns whether it
matched, and the caller decides what that means. A volume that never confirmed is
`partially_applied`; a track that never appeared is a truthful failure with a
retry. Neither is ever reported as success.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Tuple


@dataclass(frozen=True)
class ConfirmWindow:
    """A fixed number of reads at a fixed spacing. Both halves are bounded."""

    attempts: int
    interval_seconds: float

    @property
    def timeout_seconds(self) -> float:
        """Worst-case wall time, which is what a UI has to survive."""
        return max(0, self.attempts - 1) * self.interval_seconds


# Volume settles quickly when it settles at all — the device has already been
# told. Five reads over two seconds covers the lag seen in validation without
# making a rate limit worse.
VOLUME_CONFIRM = ConfirmWindow(attempts=5, interval_seconds=0.5)

# Starting a track is the slowest of these: the device has to pick it up, load it
# and report it. Six reads over three seconds.
PLAYBACK_CONFIRM = ConfirmWindow(attempts=6, interval_seconds=0.6)

# Pause, resume, next and previous act on something already loaded, so they
# settle fastest.
TRANSPORT_CONFIRM = ConfirmWindow(attempts=3, interval_seconds=0.4)

# After a transfer, the device has to become active. Two seconds is generous for
# a device that was already registered.
ACTIVATION_CONFIRM = ConfirmWindow(attempts=5, interval_seconds=0.4)

# The long one, and the only one that waits on another *process*: a desktop
# application that was just launched has to start, sign in from its stored
# session, and register with Spotify Connect. Twenty seconds, one read a second,
# and then a truthful failure.
DEVICE_APPEARANCE = ConfirmWindow(attempts=20, interval_seconds=1.0)


def confirm(
    read: Callable[[], Any],
    matches: Callable[[Any], bool],
    window: ConfirmWindow,
    sleeper: Callable[[float], None] = time.sleep,
) -> Tuple[Any, bool]:
    """Read until ``matches``, or until the window is spent.

    Returns the **last value read** and whether it matched — never a value that
    was assumed, and never the value that was requested. The caller reports what
    came back from here; nothing in this module knows what was asked for, which
    is what makes it impossible for it to echo a request as an observation.
    """
    value = read()
    if matches(value):
        return value, True
    for _ in range(max(0, window.attempts - 1)):
        sleeper(window.interval_seconds)
        value = read()
        if matches(value):
            return value, True
    return value, False


__all__ = [
    "ACTIVATION_CONFIRM",
    "ConfirmWindow",
    "DEVICE_APPEARANCE",
    "PLAYBACK_CONFIRM",
    "TRANSPORT_CONFIRM",
    "VOLUME_CONFIRM",
    "confirm",
]
