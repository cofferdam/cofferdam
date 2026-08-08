"""What Cofferdam is willing to say about a native Remote Control host.

The shape of this module is an argument about evidence. A supervised native
session has exactly one source of truth in this PR — the systemd user manager —
and systemd knows whether a *process* is running. It does not know whether
Claude authenticated, whether a phone is attached, whether a person is waiting,
or whether a conversation is in progress. So none of those are fields here.

That restraint is the feature. The failure mode this model exists to prevent is
a PWA card that says "connected" because a unit is active, while the host has
been sitting at an expired login for two hours. A state nobody can produce
evidence for is a state that will eventually be wrong, and a status screen that
is confidently wrong is worse than one that admits it does not know.

What is deliberately absent
---------------------------

No transcript, prompt, message, answer, turn count or token count. Lane A's
boundary (D-2026-08-08-3) is that Cofferdam supervises the *lifecycle* of a
native session and never reads its content, and the cheapest way to keep a
boundary is to have nowhere to put the thing you must not collect.

:attr:`NativeSessionStatus.session_url` is the one forward-looking field, and it
is always ``None`` here. It is a named seam for the URL-capture PR, not a
promise that a URL exists — see the module docstring of :mod:`.supervisor` for
what that PR has to prove before it may be filled in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

#: The only session kind this package knows. Present as a constant rather than a
#: free string so that a second provider is a visible addition rather than a
#: value someone passes in.
KIND_CLAUDE_REMOTE_CONTROL = "claude_remote_control"

# -- lifecycle ---------------------------------------------------------------
#
# Six states, each of which some observation in *this* PR can actually produce.

#: The unit is loaded and not running. Also what a never-started unit reports.
STATE_STOPPED = "stopped"
#: systemd is bringing the unit up and it has not reached running yet.
STATE_STARTING = "starting"
#: The unit is active. The **process** is up; nothing beyond that is claimed.
STATE_RUNNING = "running"
#: systemd is taking the unit down.
STATE_STOPPING = "stopping"
#: The unit failed, or exited non-zero and systemd gave up on it.
STATE_FAILED = "failed"
#: The honest answer. systemd was unreachable, answered with something this
#: build does not recognise, or answered nothing at all.
STATE_UNKNOWN = "unknown"

STATES: Tuple[str, ...] = (
    STATE_STOPPED,
    STATE_STARTING,
    STATE_RUNNING,
    STATE_STOPPING,
    STATE_FAILED,
    STATE_UNKNOWN,
)

#: States in which a start request has nothing to do. Used for idempotency: a
#: second start against a host that is already up must not produce a second
#: host, and "already running" is a truthful success rather than an error.
LIVE_STATES: Tuple[str, ...] = (STATE_STARTING, STATE_RUNNING)

#: The systemd ``ActiveState`` values this build maps, and to what.
#:
#: Conservative by construction: anything absent from this table becomes
#: :data:`STATE_UNKNOWN`. ``reloading`` is deliberately *not* mapped to running
#: even though systemd considers it a live state — this package has never seen a
#: Remote Control host report it, and inventing a mapping for a state we cannot
#: produce is exactly the guessing this model is built to avoid.
ACTIVE_STATE_MAP: Dict[str, str] = {
    "active": STATE_RUNNING,
    "activating": STATE_STARTING,
    "deactivating": STATE_STOPPING,
    "inactive": STATE_STOPPED,
    "failed": STATE_FAILED,
}


def map_active_state(active_state: Optional[str]) -> str:
    """Turn a systemd ``ActiveState`` into a Cofferdam lifecycle state.

    Unknown, malformed, empty and missing all become :data:`STATE_UNKNOWN`.
    Never ``running`` — the one direction in which a wrong answer is dangerous,
    because it is the answer that makes a person stop investigating.
    """
    if not isinstance(active_state, str):
        return STATE_UNKNOWN
    return ACTIVE_STATE_MAP.get(active_state.strip(), STATE_UNKNOWN)


@dataclass(frozen=True)
class NativeSessionStatus:
    """One native Remote Control host, as far as this workstation can tell."""

    project_id: str
    unit: str
    state: str
    kind: str = KIND_CLAUDE_REMOTE_CONTROL

    #: The raw systemd values behind :attr:`state`, kept because "running" with
    #: ``SubState=auto-restart`` underneath is a different situation from
    #: ``SubState=running``, and somebody debugging at 1am wants the real words.
    active_state: Optional[str] = None
    sub_state: Optional[str] = None

    #: When systemd says the unit went active, verbatim. Not parsed into a
    #: datetime: systemd's format is locale- and timezone-dependent, and a
    #: mis-parse would produce a confident wrong timestamp rather than a blank.
    started_at: Optional[str] = None

    #: When Cofferdam last successfully asked. Set by the supervisor from its
    #: injected clock; ``None`` when the question could not be answered.
    last_seen_at: Optional[str] = None

    #: The launch this status is about. ``None`` when nothing has been started
    #: or the state was cleared. A link minted by a different generation is not
    #: returned for this one, which is what makes a restart invalidate the old
    #: URL structurally rather than by remembering to delete a file.
    generation: Optional[str] = None

    #: **Whether** a session link has been captured for this generation — never
    #: the link itself. This is the field a status screen renders; the URL is a
    #: separate authenticated retrieval, because a status payload is cached,
    #: rendered, screenshotted and logged, and a capability URL must be in none
    #: of those.
    url_available: bool = False

    #: Set only from an explicit, observed signal in the child's own output.
    #: Never inferred from a unit being inactive or from a missing link — see
    #: :data:`..wrapper.AUTH_FORMAT_CONFIRMED`, which is why this is always
    #: ``False`` in this build.
    auth_required: bool = False

    #: The host is up and waiting for Remote Control to be enabled on this
    #: machine, which is a question only a person at the keyboard can answer.
    #:
    #: Unlike :attr:`auth_required` this one *is* reachable in this build,
    #: because the marker behind it was observed in real output during the M2H
    #: PR2 PTY spike. It is the difference between "the process is running" and
    #: "your phone can reach a session": with the prompt unanswered, systemd
    #: reports a perfectly healthy unit that will never publish anything.
    awaiting_consent: bool = False

    #: **Never populated in a status payload.** The field stays on the dataclass
    #: so the retrieval path has a typed place to put a link, and
    #: :meth:`to_dict` drops it unconditionally.
    session_url: Optional[str] = None

    #: A short, bounded, already-redacted sentence. Never a journal dump.
    error: Optional[str] = None

    def is_live(self) -> bool:
        return self.state in LIVE_STATES

    def to_dict(self) -> Dict[str, Any]:
        """The client-facing shape. **The session URL is not in it.**

        Dropped unconditionally rather than conditionally: a status payload that
        carries the link "only when the caller is allowed" is one refactor away
        from carrying it always, and this response is cached, rendered and
        screenshotted. ``url_available`` says whether a link exists;
        ``GET …/link`` is the only thing that returns one.

        The project *root* is absent for the same reason it is absent from
        :meth:`..tasks.projects.TaskProject.to_dict`: a client chooses a project
        by id and never learns where it lives on disk.
        """
        return {
            "project_id": self.project_id,
            "kind": self.kind,
            "unit": self.unit,
            "state": self.state,
            "active_state": self.active_state,
            "sub_state": self.sub_state,
            "generation": self.generation,
            "url_available": self.url_available,
            "auth_required": self.auth_required,
            "awaiting_consent": self.awaiting_consent,
            "started_at": self.started_at,
            "last_seen_at": self.last_seen_at,
            "error": self.error,
        }


__all__ = [
    "ACTIVE_STATE_MAP",
    "KIND_CLAUDE_REMOTE_CONTROL",
    "LIVE_STATES",
    "STATES",
    "STATE_FAILED",
    "STATE_RUNNING",
    "STATE_STARTING",
    "STATE_STOPPED",
    "STATE_STOPPING",
    "STATE_UNKNOWN",
    "NativeSessionStatus",
    "map_active_state",
]
