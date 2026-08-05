"""What a long operation is doing right now, said truthfully and cheaply.

Cold-start recovery can take twenty seconds — launch Spotify, wait for its
Connect device to register, transfer to it, start the track, confirm it started.
A phone that shows a spinner for that long is indistinguishable from a phone that
has hung, and the honest fix is to say which of those five things is happening.

Two rules shaped this module.

**A phase is recorded when it begins, not predicted.** Every entry here is
written by the code that is about to do the thing, so the sequence a user reads
is a log of what happened rather than an optimistic script. If recovery stops at
``waiting_for_spotify_device``, that is exactly where it stopped.

**Reading the phase costs nothing.** The PWA polls this while a write is in
flight, and it must not add provider calls to an account that is already being
rate-limited by the operation it is watching. :class:`ActivityRecorder` is one
in-memory record with a lock; the route that serves it touches no network at all.

Correlation ids exist so a phase sequence, an audit record and a response can be
lined up during diagnosis. They are random hex, carry no account information, and
are safe to log — which is the point, since nothing else about a playback
operation is.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..runtime.identity import now_iso

# -- phases ------------------------------------------------------------------
#
# The vocabulary the brief names, plus the two terminal ones. Closed set: a
# client renders these, and an unrecognised phase would render as nothing.

PHASE_LAUNCHING = "launching_spotify"
PHASE_WAITING_FOR_DEVICE = "waiting_for_spotify_device"
PHASE_ACTIVATING = "activating_device"
PHASE_STARTING = "starting_playback"
PHASE_VERIFYING = "verifying_playback"
PHASE_CONFIRMING_VOLUME = "confirming_volume"
PHASE_DONE = "done"

PHASES: Tuple[str, ...] = (
    PHASE_LAUNCHING,
    PHASE_WAITING_FOR_DEVICE,
    PHASE_ACTIVATING,
    PHASE_STARTING,
    PHASE_VERIFYING,
    PHASE_CONFIRMING_VOLUME,
    PHASE_DONE,
)

# What each phase is called on a phone. Kept here rather than in the PWA so the
# server owns the vocabulary and its wording together; a client that renders an
# unknown phase falls back to the phase name.
PHASE_LABELS: Dict[str, str] = {
    PHASE_LAUNCHING: "Opening Spotify…",
    PHASE_WAITING_FOR_DEVICE: "Waiting for Spotify device…",
    PHASE_ACTIVATING: "Switching Spotify to that device…",
    PHASE_STARTING: "Starting selected track…",
    PHASE_VERIFYING: "Checking Spotify actually started it…",
    PHASE_CONFIRMING_VOLUME: "Confirming the new volume…",
    PHASE_DONE: "Done",
}

# One operation cannot produce more than this many entries. Bounded because the
# list is returned to a client and, in a loop that misbehaved, would otherwise
# grow without limit.
MAX_STEPS = 12


def new_correlation_id() -> str:
    """A short, random, account-free handle for one operation."""
    return "spop-" + uuid.uuid4().hex[:12]


@dataclass
class OperationProgress:
    """The phase log for one operation.

    Carries no track, artist, album, device id, account or volume — only which
    phase, when, and how long in. That is enough to diagnose "it hung waiting for
    the device" without recording what somebody was listening to.
    """

    correlation_id: str = field(default_factory=new_correlation_id)
    operation: str = ""
    started_at: str = field(default_factory=now_iso)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    _clock: Any = field(default=time.monotonic, repr=False)
    _start: float = field(default=0.0, repr=False)
    _recorder: Optional["ActivityRecorder"] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._start = self._clock()

    def enter(self, phase: str) -> None:
        """Record that a phase is beginning. Ignores an unknown phase name."""
        if phase not in PHASES or len(self.steps) >= MAX_STEPS:
            return
        self.steps.append(
            {
                "phase": phase,
                "label": PHASE_LABELS.get(phase, phase),
                "at": now_iso(),
                "elapsed_ms": int((self._clock() - self._start) * 1000),
            }
        )
        if self._recorder is not None:
            self._recorder.update(self, phase)

    @property
    def phase(self) -> Optional[str]:
        return self.steps[-1]["phase"] if self.steps else None

    def elapsed_ms(self) -> int:
        return int((self._clock() - self._start) * 1000)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "started_at": self.started_at,
            "elapsed_ms": self.elapsed_ms(),
            "steps": list(self.steps),
        }


class ActivityRecorder:
    """The one operation currently in flight, readable without a provider call.

    Deliberately holds a single record rather than a history. A history of
    playback operations, kept in memory and served over a route, is a listening
    log by another name — and the phase vocabulary is closed, so this cannot grow
    into one by accident.
    """

    def __init__(self, clock=time.monotonic) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._current: Optional[Dict[str, Any]] = None

    def begin(self, progress: OperationProgress, operation: str) -> None:
        progress.operation = operation
        progress._recorder = self
        with self._lock:
            self._current = {
                "correlation_id": progress.correlation_id,
                "operation": operation,
                "phase": None,
                "label": None,
                "started_at": progress.started_at,
                "elapsed_ms": 0,
                "active": True,
            }

    def update(self, progress: OperationProgress, phase: str) -> None:
        with self._lock:
            if not self._current or self._current["correlation_id"] != progress.correlation_id:
                return
            self._current["phase"] = phase
            self._current["label"] = PHASE_LABELS.get(phase, phase)
            self._current["elapsed_ms"] = progress.elapsed_ms()

    def finish(self, progress: OperationProgress) -> None:
        with self._lock:
            if not self._current or self._current["correlation_id"] != progress.correlation_id:
                return
            self._current["active"] = False
            self._current["phase"] = PHASE_DONE
            self._current["label"] = PHASE_LABELS[PHASE_DONE]
            self._current["elapsed_ms"] = progress.elapsed_ms()

    def snapshot(self) -> Dict[str, Any]:
        """The bounded public view. No network, no lock held on the way out."""
        with self._lock:
            if self._current is None:
                return {"active": False, "operation": None, "phase": None, "label": None,
                        "correlation_id": None, "elapsed_ms": 0}
            return dict(self._current)


__all__ = [
    "ActivityRecorder",
    "MAX_STEPS",
    "OperationProgress",
    "PHASES",
    "PHASE_ACTIVATING",
    "PHASE_CONFIRMING_VOLUME",
    "PHASE_DONE",
    "PHASE_LABELS",
    "PHASE_LAUNCHING",
    "PHASE_STARTING",
    "PHASE_VERIFYING",
    "PHASE_WAITING_FOR_DEVICE",
    "new_correlation_id",
]
