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

from ..delegated import ClarificationRequest
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
    #: This adapter can ask a person a **structured question** and take an answer
    #: back into the same provider session.
    #:
    #: Emphatically **not** ``approvals``, and the two are next to each other in
    #: this list so that anybody adding a third is forced to notice the
    #: difference. ``approvals`` means "this adapter can grant permission to act",
    #: which no adapter in Cofferdam claims and none is intended to;
    #: ``clarifications`` means "this adapter can carry information back", which
    #: grants nothing. Task Core reads this one before it offers the answer
    #: route, and reads ``approvals`` before nothing at all, because there is no
    #: route to offer.
    clarifications: bool = False

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
            "clarifications": self.clarifications,
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
    #: Evidence the adapter **observed for itself**, as opposed to relayed.
    #:
    #: This is the "unless the core observed the thing itself" case named in the
    #: docstring of :class:`AdapterEvent`, which the foundation described and
    #: left unimplemented because its only adapter observed nothing. It is a
    #: separate field rather than a flag on an event for one reason: the
    #: distinction it carries is not *is this true*, it is **who looked**.
    #:
    #: Anything in ``events[].evidence`` came out of the thing being adapted —
    #: an agent's own account of what it did — and the core stamps all of it
    #: ``adapter_reported`` regardless of the source it arrived with. That rule
    #: does not bend, and an agent saying "I made a commit" is a claim forever.
    #:
    #: What belongs here is the result of an operation *Cofferdam* ran: a fixed
    #: Git observation inside an approved root, or a pid its own ``Popen``
    #: returned. The core still checks each ``source`` against
    #: :data:`~..models.VERIFIED_EVIDENCE_SOURCES` and demotes anything else, so
    #: an adapter cannot launder a claim by moving it into this field — it can
    #: only fail to be believed.
    observations: Sequence[EvidenceReference] = ()
    #: A question the delegated session is asking, normalized and bounded.
    #:
    #: Reported here rather than written anywhere by the adapter, because the
    #: question and the task's move to ``waiting_for_user`` have to land in one
    #: transaction and only the core can open one. The adapter says "this was
    #: asked"; the core decides whether the task may enter that state, mints the
    #: question id a client will use, and writes both or neither.
    #:
    #: There is no counterpart field for a tool approval, and its absence is the
    #: design: an approval reaches the history as an ordinary event and has
    #: nowhere durable to become answerable from.
    clarification: Optional["ClarificationRequest"] = None
    #: The **adapter's own** routing token for that question — the handle it will
    #: recognise when the answer comes back. Opaque to the core, which stores it
    #: as the question's provider event id and hands it back untouched.
    #:
    #: Deliberately not the question id: the core mints that, and a single
    #: identifier serving both namespaces would let a value a client has seen be
    #: used to address a provider session.
    clarification_token: Optional[str] = None
    #: The provider session the question was asked in, for the durable record.
    #:
    #: Reported rather than inferred, and stored rather than looked up, because
    #: it is the evidence that an answer resumed *the same* conversation. A
    #: question whose session id is missing is a question whose continuation
    #: nobody can check afterwards — which the M2I PR2 live spike found the hard
    #: way, by producing exactly that.
    #:
    #: It is never published to a client: a handle to a live agent conversation
    #: has no business in a response a bridge might one day relay.
    clarification_session_id: Optional[str] = None
    #: The provider's own ordering for that question, kept so a reader can tell
    #: whether two questions arrived in the order they happened.
    clarification_sequence: int = 0
    #: Which provider session produced this report, and where in it.
    #:
    #: Reported on every outcome that ends a turn, and the reason it is reported
    #: rather than looked up is the same reason ``clarification_session_id`` is:
    #: it is the evidence that a second turn happened in the *same* conversation
    #: as the first. A turn whose session id is missing is a turn whose
    #: continuity nobody can check afterwards.
    #:
    #: Never published to a client. A handle to a live agent conversation has no
    #: business in a response, and the result boundary publishes the id of a
    #: *finished* turn — a name for something that happened, not a way to reach
    #: something that is still running.
    provider_session_id: Optional[str] = None
    #: The provider's own sequence for the event that ended the turn.
    provider_turn_sequence: int = 0
    #: Whether the adapter still holds a usable session for this task **now**.
    #:
    #: The adapter is the only layer that can answer this, and it answers it
    #: fresh rather than from a flag set earlier: a helper can die between one
    #: read and the next. The core turns ``True`` into ``ready_for_followup``
    #: and ``False`` into a completed task, which is the difference between
    #: offering somebody a follow-up box that works and one that cannot.
    session_retained: bool = False

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
        """Deliver one more user turn to the session **this task already owns**.

        The session is never named, supplied or looked up by identifier: an
        adapter finds it by the task's own id, in its own table of live
        sessions. There is no parameter here — and no field on any request
        above — that could point this at another task's conversation.

        ``followup`` is validated user text. It is delivered as its own provider
        turn, not concatenated onto a prompt and not encoded as an answer: a
        follow-up and a clarification answer reach the provider through
        different code paths on purpose, because a single path would have to
        decide which at the worst possible moment.

        Refused by default, so an adapter that declared ``followup`` without
        implementing it fails loudly rather than silently swallowing somebody's
        message.
        """
        raise AdapterRefusal("this adapter does not accept follow-up messages")

    def session_available(self, task_id: str) -> bool:
        """Whether this adapter can still see a session it could continue.

        The half of the follow-up contract only an adapter can answer. Task Core
        knows the state says ``ready_for_followup``; only the thing holding the
        process knows whether the process is still there. Asked fresh on every
        follow-up, because a helper can die between one read and the next.

        **This is an early refusal, not the guarantee.** The guarantee is
        :meth:`send_followup`, which refuses when the session is gone and is the
        only place a message is actually handed over. That is why the default is
        ``True`` rather than fail-closed: the Claude Code adapter has enforced
        liveness inside its own ``send_followup`` since M2G, and defaulting to
        ``False`` here would have broken a shipped adapter to satisfy a check it
        already performs one layer down. What this method buys is a truthful
        refusal *before* the task moves, so a dead session produces
        ``task_session_unavailable`` rather than a task briefly reported running.
        """
        return True

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        raise AdapterRefusal("this adapter cannot cancel a running task")

    def inspect(self, context: TaskContext) -> AdapterOutcome:
        """What the adapter believes is happening, asked fresh.

        Default is an empty outcome rather than a refusal: "I have nothing new
        to report" is a legitimate answer and should not read as a failure.
        """
        return AdapterOutcome()

    def deliver_clarification_answer(
        self, context: TaskContext, token: str, answer: str
    ) -> bool:
        """Hand one answer to the delegated session that asked for it.

        ``token`` is the adapter's own routing handle from
        :attr:`AdapterOutcome.clarification_token`; ``answer`` is text a
        code-owned encoder produced from a validated record. Neither is a value a
        client sent.

        Returns whether the session actually took it. ``False`` is a real answer
        and the core treats it as one — the question stays open and the person is
        told it was not delivered, which is truthful in a way that recording the
        answer anyway would not be.

        Refused by default. An adapter that declared ``clarifications`` without
        implementing this cannot deliver anything, which is the safer direction
        for the mistake to fall.
        """
        raise AdapterRefusal("this adapter cannot take an answer to a question")

    def recover(self, context: TaskContext) -> AdapterOutcome:
        """Reattach to a task that survived a restart. Not used in this milestone.

        Declared so the shape is settled before an adapter needs it, and refused
        by default so nothing can quietly claim to have recovered something.
        """
        raise AdapterRefusal("this adapter cannot recover a task after a restart")

    def shutdown(self) -> None:
        """Release everything this adapter owns, because the daemon is stopping.

        Declared on the base class rather than only on the adapters that have
        something to release, so the registry can call it on every adapter
        without asking what kind each one is — and so an adapter that acquires a
        process later inherits the call site rather than having to be wired into
        one.

        Does nothing by default, which is the correct behaviour for an adapter
        that holds no process. It must never raise: a shutdown path that can fail
        is a shutdown path that leaves the *next* adapter's children running.
        """
        return None

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
