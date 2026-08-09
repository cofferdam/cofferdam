"""The Claude Agent SDK adapter: a Task Core adapter, not a second task system.

What this file does is small on purpose. It owns one dictionary of live sessions
keyed by task id, and it translates in two directions: a Task Core call becomes a
:class:`~.session.DelegatedSession` operation, and a normalized delegated event
becomes an :class:`~..protocol.AdapterOutcome` the core is free to refuse.

It owns no lifecycle state. There is no state field on this class, no second
database, and no place a task's status is recorded — Task Core's store is the
only one, its transition graph is the only authority, and this adapter *asks*.
That separation is the whole reason M2F existed before any agent, and adding a
second provider is the moment it earns its keep: two adapters, one lifecycle, and
neither of them able to move a task somewhere the graph forbids.

What it takes, and what it refuses to take
------------------------------------------

The constructor takes a session factory and a concurrency limit, and both are
passed only by :func:`~..build_registry`. It takes no executable path, no
argument list, no permission mode, no model, no environment mapping — the entire
provider configuration lives in :mod:`.options`, decided in source.

Every operation starts from a :class:`~..protocol.TaskContext` the core built,
whose ``project_root`` was resolved from the host registry and re-verified on
disk moments earlier. Nothing in this file reads a path from anywhere else.

The two waits, and why only one of them is one
----------------------------------------------

A delegated session can report two kinds of "somebody is needed": a
**clarification**, where the agent wants information, and a **tool approval**,
where it wants permission. They are separate types with separate storage all the
way down — see :mod:`....delegated` — and this adapter keeps the distinction
intact by treating them completely differently.

A **clarification is a wait**, and as of M2I PR2 it is reported as one. The
adapter asks the core to move the task to ``waiting_for_user`` with the reason
``clarification``, hands over the normalized question and its own routing token,
and the core does the rest. When an accepted answer comes back down through
:meth:`ClaudeAgentSdkAdapter.deliver_clarification_answer` the same session
resumes — same helper, same client, same provider session id — because nothing
was torn down while it waited.

A **tool approval is not a wait**, and it must never be reported as one.
Cofferdam's permission handler denies it, the agent is told no, and it carries on
or gives up. Reporting "NEEDS YOU" about a request nobody can act on would be the
same false claim the Claude Code adapter had to unlearn when a finished turn was
reported as waiting for an answer. It is recorded in the history and the task
keeps running.

That asymmetry is the safety property, stated as behaviour rather than as a
comment: this adapter has one code path that can produce
``waiting_for_user(clarification)`` and **no** path at all that can produce
``waiting_for_user(approval)``, and there is no method here that could grant a
tool.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from ....runtime.identity import now_iso
from ...delegated import (
    KIND_CANCELLED,
    KIND_CLARIFICATION_REQUESTED,
    KIND_PROVIDER_FAILED,
    KIND_SUCCEEDED,
    TERMINAL_STATE_FOR_KIND,
    DelegatedEvent,
    DelegatedEventLog,
    DelegatedResult,
    projection,
    result_from_event,
)
from ...models import (
    EVENT_PROGRESS,
    MAX_RESULT_CHARS,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_WAITING_FOR_USER,
    WAITING_CLARIFICATION,
)
from ..protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    AdapterRefusal,
    TaskAdapter,
    TaskContext,
)
from . import options as option_policy, question as question_reader, sdk as sdk_boundary
from .hostclient import HostSession
from .normalize import PROVIDER
from .session import DelegatedSession, SessionRefused

ADAPTER_ID = "claude-agent-sdk"
DISPLAY_NAME = "Claude (Agent SDK)"
DESCRIPTION = (
    "Runs Claude through the official Agent SDK inside one approved project. It "
    "can read and edit files there; it has no shell, and it cannot leave the "
    "project folder."
)

#: One at a time, and the number is the same as the Claude Code adapter's for the
#: same reason: each task holds a live subprocess and a subscription session for
#: its whole lifetime, and a personal workstation running several at once would
#: be spending somebody's quota on work they have lost track of. A host may raise
#: it in source; a client cannot see it, let alone set it.
DEFAULT_MAX_CONCURRENT_TASKS = 1

#: What the PWA is told about this adapter's boundaries. Bounded sentences a
#: person can act on, not a configuration dump.
LIMITATIONS = (
    "Claude has no shell here. It can read, search, create and edit files "
    "inside the chosen project, and nothing else.",
    "It cannot leave the project folder.",
    "Cofferdam never approves a tool from a phone. If Claude asks for "
    "permission, it is refused and the request is recorded.",
    "Claude can ask you a question, and you can answer it. Answering a "
    "question grants nothing — it is information, not permission.",
    "A task is not resumed if Cofferdam restarts — it is reported as "
    "interrupted, and any question it was waiting on is closed with it.",
    "This adapter does not take follow-up messages yet; a task runs one turn, "
    "asks what it needs to, and reports its result.",
    "Never type a password, one-time code or token into a prompt or an answer.",
)


class ClaudeAgentSdkAdapter(TaskAdapter):
    """One instance per process, holding every live delegated session.

    Registered only when the host explicitly enabled it — see
    :func:`~..build_registry`. There is no client input anywhere in this
    constructor.
    """

    adapter_id = ADAPTER_ID
    display_name = DISPLAY_NAME
    description = DESCRIPTION

    def __init__(
        self,
        *,
        session_factory: Optional[Callable[..., DelegatedSession]] = None,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT_TASKS,
        cli_path: Optional[Path] = None,
        availability: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._session_factory = session_factory or _default_session_factory
        self._max_concurrent = max(1, int(max_concurrent))
        #: The CLI the SDK is pointed at, or ``None`` to let the SDK choose its
        #: own bundled copy.
        #:
        #: **Passed in by the registry, not found here**, and the direction
        #: matters. The value comes from the CLI adapter's fixed search — one
        #: program name, a fixed directory order, no caller-supplied component —
        #: so both adapters drive the same installed binary. Resolving it *in
        #: this file* would mean importing the other adapter's package, which
        #: would make this one un-shippable the day the CLI adapter is retired
        #: and would cross a boundary Task Core asserts. The registry is the
        #: layer allowed to know both adapters exist, so the wiring lives there.
        self._cli_path = cli_path
        self._availability = availability or sdk_boundary.available
        self._sessions: Dict[str, DelegatedSession] = {}
        self._logs: Dict[str, DelegatedEventLog] = {}
        self._results: Dict[str, DelegatedResult] = {}
        self._lock = threading.RLock()

    # -- what this adapter is ------------------------------------------------

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            start=True,
            cancel=True,
            structured_progress=True,
            final_result=True,
            # False, and honestly so. The session ends at its first terminal
            # event in this foundation, so there is nothing alive for a
            # follow-up to reach. Declaring the capability would make Task Core
            # offer a button whose only outcome is a refusal.
            followup=False,
            # True: the session can ask a structured question and take an answer
            # back into the same conversation. This is what M2I PR2 added and it
            # is the one capability the CLI transport cannot have.
            clarifications=True,
            # False, and it is the entry above that makes this one worth reading
            # twice. This adapter cannot grant a permission request — it denies
            # and records, which is a different and truthful thing — and no
            # amount of answering questions changes that.
            approvals=False,
            # False: no authentication probe exists on this transport yet.
            authentication_waits=False,
            # False until a complete safe reattachment design exists and has
            # been proven. "Possible later" is not a capability.
            recover_after_restart=False,
        )

    def available(self) -> bool:
        return bool(self._availability())

    def unavailable_reason(self) -> Optional[str]:
        if self.available():
            return None
        return sdk_boundary.unavailable_reason() or sdk_boundary.MISSING_MESSAGE

    def describe(self) -> Dict[str, object]:
        described = super().describe()
        described["limitations"] = list(LIMITATIONS)
        described["max_concurrent_tasks"] = self._max_concurrent
        described["tools"] = list(option_policy.PROFILE_TOOLS)
        described["provider"] = PROVIDER
        # The installed version, not the verified one: a capability report should
        # say what is actually there. ``None`` when the SDK is absent, which is
        # already visible through ``available``.
        described["sdk_version"] = sdk_boundary.installed_version()
        described["verified_sdk_version"] = sdk_boundary.VERIFIED_SDK_VERSION
        # Published rather than kept in a docstring, because a client that
        # renders a question deserves to know whether the shape it is rendering
        # came from a schema anybody has actually seen. ``False`` here is the
        # honest state of this build until a supervised live spike says
        # otherwise.
        described["question_schema_verified"] = question_reader.SCHEMA_VERIFIED
        return described

    # -- lifecycle -----------------------------------------------------------

    def start(self, context: TaskContext) -> AdapterOutcome:
        """Open one session for this task and return once it is real.

        Does not wait for the work. The core turns the task ``running`` after
        this returns, and everything afterwards arrives through :meth:`inspect`.
        """
        if not self.available():
            raise AdapterRefusal(self.unavailable_reason() or sdk_boundary.MISSING_MESSAGE)

        with self._lock:
            self._forget_finished()
            if context.task_id in self._sessions:
                # A second start for one task would be a second session on one
                # conversation. Task Core's idempotency key normally prevents a
                # double create; this is the structural backstop for the case it
                # does not.
                raise AdapterRefusal("this task is already running")
            active = sum(1 for session in self._sessions.values() if not session.finished)
            if active >= self._max_concurrent:
                raise AdapterRefusal(
                    "another Claude task is already running. Cofferdam runs one "
                    "at a time — wait for it or cancel it."
                )
            session = self._session_factory(
                task_id=context.task_id,
                project_root=context.project_root,
                cli_path=self._cli_path,
            )
            self._sessions[context.task_id] = session
            self._logs[context.task_id] = DelegatedEventLog()

        try:
            session.start(context.prompt)
        except SessionRefused as refusal:
            self._retire(context.task_id)
            raise AdapterRefusal(str(refusal))
        except Exception:
            # An unexpected failure inside the session must not leave the
            # concurrency slot consumed forever.
            self._retire(context.task_id)
            raise AdapterRefusal("the Claude Agent SDK session could not be started")

        # One event, about the launch, and nothing drained here. Whatever the
        # session has already produced arrives through the next `inspect`, which
        # Task Core calls on every read — draining it here as well would put the
        # same events behind two different code paths for no gain.
        return AdapterOutcome(
            events=(
                AdapterEvent(
                    event_type=EVENT_PROGRESS,
                    text="Claude started in " + context.project_id + ".",
                    detail="agent sdk session ready",
                ),
            ),
            requested_state=None,
        )

    def inspect(self, context: TaskContext) -> AdapterOutcome:
        """What has happened since last time, as a report the core may refuse.

        Called by Task Core on every read of a task detail. Everything the
        session produced since the previous call is drained, recorded through the
        provider-neutral log — which is where duplicate suppression, ordering and
        finality live — and projected onto Task Core's own event vocabulary.

        Draining is what makes this cheap to call repeatedly: an event is
        returned once, by construction, rather than filtered out afterwards.
        """
        with self._lock:
            session = self._sessions.get(context.task_id)
            log = self._logs.get(context.task_id)
        if session is None or log is None:
            return AdapterOutcome()

        events, asked = self._collect(context.task_id, session)
        terminal = log.terminal_event

        if terminal is None:
            if asked is not None and asked.clarification is not None:
                # A question, reported as a wait. The core decides whether the
                # task may enter that state and mints the id a client will use;
                # this only says what was asked and how to reach the session that
                # asked it.
                return AdapterOutcome(
                    events=tuple(events),
                    requested_state=STATE_WAITING_FOR_USER,
                    waiting_reason=WAITING_CLARIFICATION,
                    clarification=asked.clarification,
                    clarification_token=asked.provider_event_id,
                    # Taken from the event rather than from the session object,
                    # so the id stored against the question is the one that was
                    # true when the question was asked.
                    clarification_session_id=(
                        asked.provider_session_id or session.provider_session_id
                    ),
                    clarification_sequence=asked.provider_sequence,
                )
            if session.finished:
                # The session ended without ever producing a result. Exit is not
                # a result: something ran and reported nothing, and calling that
                # a completed task would be inventing an outcome.
                self._retire(context.task_id)
                return AdapterOutcome(
                    events=tuple(events),
                    requested_state=STATE_FAILED,
                    failure_code="agent_sdk_no_result",
                    failure_message="The Claude session ended without producing a result.",
                )
            return AdapterOutcome(events=tuple(events))

        return self._settle(context.task_id, terminal, events)

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        """Stop this task's session, and nothing else.

        **Precedence, for the race this method is most likely to lose.** A cancel
        can arrive in the gap between a session finishing and Cofferdam noticing
        it. Reporting ``cancelled`` there would be a false claim in the direction
        that matters most to an audit — the work *was* done, and the record would
        say it was stopped. So the buffer is drained first, and a terminal event
        that had already arrived wins.

        The request is not lost either way: Task Core has already written
        ``cancellation_requested`` into the history before calling this, so what
        a person sees is that they asked and that it had already finished.
        """
        with self._lock:
            session = self._sessions.get(context.task_id)
            log = self._logs.get(context.task_id)
        if session is None or log is None:
            raise AdapterRefusal("there is no running Claude session for this task")

        events, _ = self._collect(context.task_id, session)
        already = log.terminal_event
        if already is not None:
            return self._settle(context.task_id, already, events)

        delivered = session.request_cancel()
        events.extend(self._collect(context.task_id, session)[0])
        if not delivered:
            # Not promoted to `cancelled`. Saying a task stopped because
            # stopping it was requested is the false success this design refuses
            # to produce; the core leaves it `cancelling`.
            raise AdapterRefusal("the Claude session did not stop")

        # Give the session its own moment to record what stopping meant, then
        # take whatever terminal event exists. A cancel that raced a result
        # still resolves to the result, because the log accepted whichever
        # arrived first and refuses the second.
        session.close()
        note = getattr(session, "note_cancelled", None)
        if callable(note):
            # The out-of-process session cannot record its own cancellation the
            # way the in-process one does: the helper is gone by now. It is the
            # object that knows a cancel was actually delivered, so it is the
            # object that gets to say so.
            note()
        events.extend(self._collect(context.task_id, session)[0])
        terminal = log.terminal_event
        if terminal is not None:
            return self._settle(context.task_id, terminal, events)

        self._retire(context.task_id)
        return AdapterOutcome(
            events=tuple(events),
            requested_state=STATE_CANCELLED,
        )

    # -- results -------------------------------------------------------------

    def result_for(self, task_id: str) -> Optional[DelegatedResult]:
        """The durable terminal result for a finished task, if this process has one.

        The provider-neutral shape behind the ``get_result`` action M2I.5 will
        expose. **No route serves it in this build**, and this method is not a
        substitute for one: it is in-process memory, lost on restart, and the
        authoritative record of a finished task remains Task Core's own row.
        What it gives is the boundary — provenance, terminal state, bounded
        result or bounded failure — already produced rather than invented later.
        """
        with self._lock:
            return self._results.get(task_id)

    # -- turning events into a report ---------------------------------------

    def _collect(
        self, task_id: str, session: DelegatedSession
    ) -> Tuple[List[AdapterEvent], Optional[DelegatedEvent]]:
        """Drain the session, record through the log, project what survived.

        The log is the gate: an event it refuses — a duplicate, or anything after
        a terminal one — never becomes an adapter event, so a late provider
        result cannot resurrect a task that has already ended, and a repeated
        question event cannot open a second question.

        The second return value is the clarification this drain produced, if it
        produced one. **The last, not the first**: if a session somehow reported
        two before anybody looked, the newest is the one the provider is actually
        blocked on, and the older is history rather than a thing to wait for.
        """
        with self._lock:
            log = self._logs.get(task_id)
        if log is None:
            return [], None
        projected: List[AdapterEvent] = []
        asked: Optional[DelegatedEvent] = None
        for event in session.drain():
            accepted = log.record(event)
            if accepted is None:
                continue
            if accepted.kind == KIND_CLARIFICATION_REQUESTED:
                asked = accepted
            event_type, text, detail = projection(accepted)
            if text is None and detail is None:
                continue
            projected.append(
                AdapterEvent(event_type=event_type, text=text, detail=detail)
            )
        return projected, asked

    # -- answering -----------------------------------------------------------

    def deliver_clarification_answer(
        self, context: TaskContext, token: str, answer: str
    ) -> bool:
        """Hand one answer to this task's own session. Nothing broader happens.

        The session is found by **this task's id** in this adapter's own
        dictionary, and the token is checked against the question that session is
        actually holding. There is no argument here that could name another
        task's session and no registry to look one up in, which is what makes
        "an answer cannot reach another task" structural rather than validated.

        Returns whether the session took it. A session that has moved on, been
        cancelled, or timed the question out returns ``False``, and the core
        turns that into a truthful refusal rather than a recorded answer nobody
        received.
        """
        with self._lock:
            session = self._sessions.get(context.task_id)
        if session is None:
            raise AdapterRefusal("there is no running Claude session for this task")
        try:
            return bool(session.submit_answer(token, answer))
        except SessionRefused:
            return False

    def _settle(
        self, task_id: str, terminal: DelegatedEvent, events: List[AdapterEvent]
    ) -> AdapterOutcome:
        """Turn a terminal delegated event into a requested Task Core state."""
        state = TERMINAL_STATE_FOR_KIND.get(terminal.kind, STATE_FAILED)
        self._remember_result(task_id, terminal)
        self._retire(task_id)

        if terminal.kind == KIND_SUCCEEDED:
            return AdapterOutcome(
                events=tuple(events),
                requested_state=STATE_COMPLETED,
                final_result=(terminal.result or "")[:MAX_RESULT_CHARS] or None,
            )
        if terminal.kind == KIND_PROVIDER_FAILED:
            return AdapterOutcome(
                events=tuple(events),
                requested_state=STATE_FAILED,
                failure_code=terminal.failure_code or "provider_error",
                failure_message=terminal.text
                or "Claude reported an error without explaining it.",
            )
        if terminal.kind == KIND_CANCELLED:
            return AdapterOutcome(events=tuple(events), requested_state=STATE_CANCELLED)
        return AdapterOutcome(events=tuple(events), requested_state=state)

    def _remember_result(self, task_id: str, terminal: DelegatedEvent) -> None:
        with self._lock:
            if task_id in self._results:
                # First terminal event wins, exactly as the log decided. A second
                # one cannot rewrite the result of a task that already has one.
                return
            self._results[task_id] = result_from_event(
                task_id=task_id, event=terminal, completed_at=now_iso()
            )

    # -- bookkeeping ---------------------------------------------------------

    def _retire(self, task_id: str) -> None:
        """Close the session for a task that has reached its end.

        Called on every terminal path and on every failed start. The bug this
        shape exists to prevent is the one the Claude Code adapter had in live
        validation: a finished task that kept its subprocess, and with it the
        single concurrency slot, so every task created afterwards was refused
        until the daemon was restarted.
        """
        with self._lock:
            session = self._sessions.pop(task_id, None)
            self._logs.pop(task_id, None)
        if session is None:
            return
        try:
            session.close()
        except Exception:  # pragma: no cover - tidying must never fail a task
            pass

    def _forget_finished(self) -> None:
        """Drop sessions that have already stopped. The caller holds the lock."""
        for task_id in [
            key for key, session in self._sessions.items() if session.finished
        ]:
            self._sessions.pop(task_id, None)
            self._logs.pop(task_id, None)

    def active_task_ids(self):
        with self._lock:
            return tuple(
                task_id
                for task_id, session in self._sessions.items()
                if not session.finished
            )

    def shutdown(self) -> None:
        """Close every session this adapter owns. Called when the service stops."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._logs.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:  # pragma: no cover
                continue


def _default_session_factory(
    *, task_id: str, project_root: Path, cli_path: Optional[Path]
) -> DelegatedSession:
    """One helper process per task.

    :class:`~.hostclient.HostSession`, not the in-process
    :class:`~.session.SdkSession`. The SDK runs in a child Cofferdam launches
    with a complete code-owned environment, because ``ClaudeAgentOptions.env``
    cannot replace an inherited one — see :mod:`.hostenv` for the evidence and
    the reasoning. From this adapter's point of view the two are the same
    object, which is what lets every behaviour above be tested with a double.
    """
    return HostSession(task_id=task_id, project_root=project_root, cli_path=cli_path)


__all__ = [
    "ADAPTER_ID",
    "DEFAULT_MAX_CONCURRENT_TASKS",
    "DESCRIPTION",
    "DISPLAY_NAME",
    "LIMITATIONS",
    "ClaudeAgentSdkAdapter",
]
