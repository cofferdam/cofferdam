"""The provider-neutral adapter interface, and what an adapter may say.

Task Core owns identity, lifecycle, persistence, events, policy and the API. An
**adapter** owns exactly one thing: making something happen for a task, and
reporting what happened. The line between them is the reason this milestone
exists before any real agent is integrated — it is much easier to draw now than
after Claude Code's process handling has grown roots through the middle of it.

Three properties the interface is built to force.

**An adapter reports; it does not decide.** Every method returns
:class:`AdapterOutcome` — structured events and, optionally, a requested state.
Nothing here writes a database row or a state. The core validates the requested
state against the transition graph and may refuse it, which means a buggy or
hostile adapter cannot move a task somewhere the graph forbids.

**Capabilities are declared, not assumed.** :class:`AdapterCapabilities` is a
closed set of booleans an adapter answers honestly. The core asks before
offering an operation, and the PWA renders buttons from the same answer, so
"this adapter cannot be cancelled" is one fact with one source rather than a
guess made separately in three places.

**An unsupported operation is a truthful refusal.** Not a silent success, not a
no-op that looks like it worked. :class:`AdapterRefusal` exists so an adapter
that cannot do something says so in a way the core can turn into a sentence.

What an adapter never receives
------------------------------

A request body. A token. A client-supplied path. :class:`TaskContext` is built
by the core from the durable task row and the *server-resolved* project root, so
an adapter's view of a task is Cofferdam's view of it — not the phone's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from ..models import (
    ACTOR_ADAPTER,
    EVENT_PROGRESS,
    MAX_OUTPUT_CHARS,
    SOURCE_ADAPTER,
    EvidenceReference,
    bounded_text,
)


@dataclass(frozen=True)
class AdapterCapabilities:
    """What one adapter can actually do.

    Every field defaults to ``False``. An adapter gains a capability by claiming
    it, never by omission — the opposite default would mean a new capability
    added here was silently claimed by every existing adapter.
    """

    start: bool = False
    followup: bool = False
    cancel: bool = False
    recover_after_restart: bool = False
    structured_progress: bool = False
    final_result: bool = False
    approvals: bool = False
    authentication_waits: bool = False

    def to_dict(self) -> Dict[str, bool]:
        return {
            "start": self.start,
            "followup": self.followup,
            "cancel": self.cancel,
            "recover_after_restart": self.recover_after_restart,
            "structured_progress": self.structured_progress,
            "final_result": self.final_result,
            "approvals": self.approvals,
            "authentication_waits": self.authentication_waits,
        }


@dataclass(frozen=True)
class TaskContext:
    """Everything an adapter is given about a task. Assembled by the core.

    ``project_root`` is a real, verified directory — resolved from the project
    registry moments before use, never from a request. It is passed as a
    :class:`~pathlib.Path` rather than a string as a small piece of friction
    against an adapter concatenating it into a command line.
    """

    task_id: str
    correlation_id: str
    project_id: str
    project_root: Path
    adapter_id: str
    prompt: str
    state: str
    lifecycle_revision: int
    followup: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterEvent:
    """One thing an adapter wants recorded.

    Bounded on construction, so an adapter cannot decide how much of the
    database it uses. ``evidence`` is normalized to *adapter-reported* by the
    core unless the core observed the thing itself — see :mod:`..service`.
    """

    event_type: str = EVENT_PROGRESS
    text: Optional[str] = None
    detail: Optional[str] = None
    evidence: Tuple[EvidenceReference, ...] = ()

    def bounded(self) -> "AdapterEvent":
        return AdapterEvent(
            event_type=self.event_type,
            text=bounded_text(self.text, MAX_OUTPUT_CHARS),
            detail=bounded_text(self.detail, 500),
            evidence=tuple(self.evidence),
        )


@dataclass(frozen=True)
class AdapterOutcome:
    """What one adapter call produced.

    ``requested_state`` is a *request*. The core checks it against the
    transition graph and refuses it if the graph does not contain that arrow,
    which is what keeps an adapter from declaring a cancelled task complete.
    """

    events: Sequence[AdapterEvent] = ()
    requested_state: Optional[str] = None
    waiting_reason: Optional[str] = None
    final_result: Optional[str] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    accepted: bool = True

    @property
    def actor(self) -> str:
        return ACTOR_ADAPTER

    @property
    def source(self) -> str:
        return SOURCE_ADAPTER


class AdapterRefusal(Exception):
    """The adapter will not or cannot do this, and says why.

    Carries a short message the core may show. It is *the core* that decides how
    that reaches a client, and the core never renders an adapter's exception
    text as its own — see :class:`..errors.AdapterFailed`.
    """

    def __init__(self, message: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class TaskAdapter:
    """The interface every agent integration implements.

    Subclasses are registered in a **code-owned** table (see
    :mod:`.__init__`). There is no path by which a client names a module, a
    class, or an import — an adapter id is looked up in a dictionary that exists
    in source, and an unknown id is a refusal rather than an import attempt.

    The default implementations refuse. An adapter that does not override
    ``send_followup`` cannot receive follow-ups even if it mistakenly declared
    the capability, which is the safer direction for the mistake to fall.
    """

    #: Stable, lowercase, code-owned. Appears in URLs and audit records.
    adapter_id = "abstract"
    #: What a person sees. Must not imply the adapter is something it is not.
    display_name = "Abstract adapter"
    #: One sentence about what this adapter actually does.
    description = ""

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities()

    def available(self) -> bool:
        """Whether this adapter can be used on this host, right now.

        Separate from registration on purpose: an adapter can exist in the table
        and be unavailable — not installed, not enabled, not configured — and
        the PWA shows the difference rather than offering a selector entry whose
        only outcome is a refusal.
        """
        return False

    def unavailable_reason(self) -> Optional[str]:
        return None

    def start(self, context: TaskContext) -> AdapterOutcome:
        raise AdapterRefusal("this adapter cannot start tasks")

    def send_followup(self, context: TaskContext, followup: str) -> AdapterOutcome:
        raise AdapterRefusal("this adapter does not accept follow-up messages")

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        raise AdapterRefusal("this adapter cannot cancel a running task")

    def inspect(self, context: TaskContext) -> AdapterOutcome:
        """What the adapter believes is happening, asked fresh.

        Default is an empty outcome rather than a refusal: "I have nothing new
        to report" is a legitimate answer and should not read as a failure.
        """
        return AdapterOutcome()

    def recover(self, context: TaskContext) -> AdapterOutcome:
        """Reattach to a task that survived a restart. Not used in this milestone.

        Declared so the shape is settled before an adapter needs it, and refused
        by default so nothing can quietly claim to have recovered something.
        """
        raise AdapterRefusal("this adapter cannot recover a task after a restart")

    def describe(self) -> Dict[str, Any]:
        """The client-facing description of this adapter."""
        return {
            "adapter_id": self.adapter_id,
            "display_name": self.display_name,
            "description": self.description,
            "available": self.available(),
            "unavailable_reason": self.unavailable_reason(),
            "capabilities": self.capabilities().to_dict(),
        }


__all__ = [
    "AdapterCapabilities",
    "AdapterEvent",
    "AdapterOutcome",
    "AdapterRefusal",
    "TaskAdapter",
    "TaskContext",
]
