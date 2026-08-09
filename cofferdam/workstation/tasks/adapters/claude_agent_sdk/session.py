"""One delegated task, one Agent SDK session, held behind a testable boundary.

The SDK is asynchronous — ``anyio``, an async context manager, an async
iterator — and Task Core is not. Task Core is a synchronous service that a route
calls, that holds a lock, and that asks an adapter what has happened whenever
somebody looks at a task. Something has to sit between those two shapes.

This module is that something, and it is deliberately two things rather than one:

:class:`DelegatedSession`
    The boundary. A small synchronous surface — start, drain, answer, cancel,
    close — with no SDK anywhere in it. Everything above is written against
    *this*, so every behaviour worth testing (ordering, cancellation precedence,
    terminal finality, answer routing, resource release) can be tested with a
    double on a machine where the SDK is not installed.

:class:`SdkSession`
    The real implementation. It owns one thread, that thread owns one event
    loop, and the loop owns one ``ClaudeSDKClient``. **It runs inside the helper
    process**, never in the daemon — see :mod:`.hostenv` for why — so nothing in
    this file is imported by an ordinary Cofferdam start-up.

The rules the implementation is written to
------------------------------------------

**One session per task, and no way to reach another.** The client is held on the
instance. There is no registry to look one up in, no identifier a caller passes,
and therefore no argument that could point a cancel or an answer at somebody
else's session.

**Bounded everything.** One thread, one loop, one bounded event buffer that drops
its oldest entries rather than growing, one pending question at a time.

**Cancellation reaches the SDK, and cannot be faked.** ``request_cancel`` wakes
any blocked question, calls the SDK's own ``interrupt`` on the loop that owns the
client, then closes the connection. If it does not work, this class says so.

**A terminal event ends the iteration.** Once the session has produced a result,
the reader stops, the client disconnects and the thread exits.

How a question is asked, and how it is answered
-----------------------------------------------

This is the part M2I PR2 adds, and the mechanism was chosen from what the SDK
source actually proves rather than from what would be convenient.

A question arrives through ``can_use_tool``. Two facts from the published 0.2.134
source make that the right place. Control requests are dispatched with
``spawn_detached``, so a callback that takes its time **does not block the read
loop** — messages keep arriving and ``interrupt`` still lands. And the callback is
handed the tool's ``input`` and may still refuse, so the question can be *read*
without the tool being *run*.

The answer is delivered as the ``message`` of a ``PermissionResultDeny``. That
choice is deliberate and it is the conservative one:

* it uses only typed, documented API — the SDK turns it into
  ``{"behavior": "deny", "message": …}``, which is verified in ``query.py``;
* it grants nothing. The question tool never executes, so there is no
  unverified interactive path being relied on inside a headless session;
* the session is unchanged — same client, same process, same provider session id
  — so continuation is a property of not having torn anything down, rather than
  a resume this build would have to prove.

What is *not* claimed: that allowing the tool with an updated input would also
work. It might; it is unverified, it is unused, and there is no code here that
does it.

Two things the permission handler will never do
-----------------------------------------------

It never allows. Every branch returns a deny — with a person's answer when there
is one, with a refusal sentence when there is not.

It never puts tool input into a durable event. For an ordinary tool the handler
reads the *name* and nothing else. For a question tool it reads the input through
:mod:`.question`, which returns bounded sanitized question text and Cofferdam's
own option identifiers — and, when it cannot read the shape, returns names and
type names and no values at all.
"""

from __future__ import annotations

import asyncio
import secrets
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, List, Optional, Tuple

from ....runtime.identity import now_iso
from ...delegated import (
    KIND_ACTIVITY,
    KIND_CANCELLATION_REQUESTED,
    KIND_CANCELLED,
    KIND_PROVIDER_FAILED,
    DelegatedEvent,
    build_event,
    safe_line,
    safe_text,
)
from . import normalize, options as option_policy, question as question_reader
from . import sdk as sdk_boundary

#: How long a start waits for the SDK to connect and accept the prompt. Generous,
#: because a first connect starts a CLI subprocess; bounded, because a start that
#: has not happened in this long is a start that should report rather than hang a
#: request somebody made from a phone.
START_TIMEOUT_SECONDS = 90.0

#: How long a cancel waits for the interrupt to be delivered.
CANCEL_TIMEOUT_SECONDS = 15.0

#: How long a close waits for the reader thread to finish and the client to
#: disconnect before this class stops waiting and says so.
CLOSE_TIMEOUT_SECONDS = 20.0

#: How long the permission callback holds a question open waiting for a person.
#:
#: Twenty minutes, and the number is a judgement rather than a limit somebody
#: measured. A question asked from a workstation and answered from a phone has to
#: survive somebody putting the phone down; a question nobody has answered in
#: twenty minutes is one where an agent holding a subscription session open is
#: costing more than the answer is worth. When it expires the question is
#: declined truthfully — the agent is told nobody answered — rather than answered
#: with a guess.
QUESTION_TIMEOUT_SECONDS = 20 * 60.0

#: The most normalized events one session buffers between drains. Oldest are
#: dropped, because the newest activity is what somebody looking at a phone
#: wants and an unbounded buffer is a memory leak with a nice name.
MAX_BUFFERED_EVENTS = 500

#: How many questions one session will ask before it stops being read as a
#: conversation and starts being read as a loop. A turn that has asked this many
#: has not earned another.
MAX_QUESTIONS_PER_SESSION = 8

#: The prefix of the token this session gives a question so that an answer can be
#: routed back to it. Distinct from Task Core's own ``q_`` question id: the two
#: namespaces are separate on purpose, so a value from one can never be used as a
#: value in the other.
QUESTION_TOKEN_PREFIX = "ask_"


class SessionRefused(RuntimeError):
    """The session could not do what was asked, with a sentence saying why.

    Distinct from the SDK's own errors, which never leave this module: an SDK
    exception's text can name a path, a version or an account, and Task Core's
    contract is that failure messages are Cofferdam's words. What crosses this
    boundary is a short sentence and, at most, an exception *type name*.
    """


def new_question_token() -> str:
    """A token this session uses to recognise the answer to its own question."""
    return QUESTION_TOKEN_PREFIX + secrets.token_hex(8)


class DelegatedSession:
    """The synchronous surface an adapter is written against.

    Every method here is a refusal by default. A subclass gains a behaviour by
    implementing it, never by inheriting a permissive stub — the same direction
    the Task Core adapter protocol chose, and for the same reason: the safer way
    for a missing implementation to fail is loudly.
    """

    #: The provider's own session identifier, once it has reported one. Preserved
    #: so an answer can be verified against the session that asked, and so a
    #: durable result can say which session produced it.
    provider_session_id: Optional[str] = None

    def start(self, prompt: str) -> None:
        raise SessionRefused("this session cannot be started")

    def drain(self) -> List[DelegatedEvent]:
        """Every normalized event since the last drain. Never blocks."""
        return []

    @property
    def pending_question_token(self) -> Optional[str]:
        """The token of the question this session is waiting on, if any."""
        return None

    def submit_answer(self, token: str, answer: str) -> bool:
        """Deliver one answer to the question identified by ``token``.

        ``True`` only when this session was actually waiting on that token. A
        mismatched token is ``False`` rather than an exception, because the
        caller's correct response is the same either way — refuse the answer —
        and because an answer arriving a moment after a question was superseded
        is an ordinary race rather than a fault.
        """
        raise SessionRefused("this session cannot take an answer")

    def send_followup(self, followup: str) -> None:
        """Deliver another user turn to the same session.

        Distinct from :meth:`submit_answer`: a follow-up is a new instruction to
        a session that is waiting for nothing, while an answer resolves a
        question the agent is blocked on. They are separate methods because they
        reach the provider through entirely different channels, and a single
        method would have to decide which — from state, at the worst possible
        moment.
        """
        raise SessionRefused(
            "same-session follow-up is not implemented by this adapter yet"
        )

    def request_cancel(self) -> bool:
        """Ask the provider to stop. ``True`` only if the request was delivered."""
        raise SessionRefused("this session cannot be cancelled")

    def close(self) -> bool:
        """Release everything this session holds. ``True`` if it let go."""
        return True

    @property
    def finished(self) -> bool:
        return True

    @property
    def running(self) -> bool:
        return not self.finished


class _PendingQuestion:
    """One question the session is holding open, and the answer slot for it.

    The event is an :class:`asyncio.Event` rather than a threading one, and that
    is not a style choice: the callback awaiting it runs on the SDK's event loop,
    and a thread primitive awaited there would block the loop that has to deliver
    the interrupt this design relies on. Everything from another thread reaches it
    through ``call_soon_threadsafe``.
    """

    __slots__ = ("token", "event", "answer", "cancelled")

    def __init__(self, token: str, event: "asyncio.Event") -> None:
        self.token = token
        self.event = event
        self.answer: Optional[str] = None
        self.cancelled = False


class SdkSession(DelegatedSession):
    """One Claude Agent SDK session, driven from a dedicated thread.

    Constructed by the helper process with values it was launched with: a task id
    Task Core minted, a project root the server verified, and an executable found
    by the fixed search in the Claude Code adapter's ``cli`` module. Nothing here
    takes a parameter that could carry a request value.
    """

    def __init__(
        self,
        *,
        task_id: str,
        project_root: Path,
        cli_path: Optional[Path] = None,
        loader: Callable[[], Any] = sdk_boundary.load,
    ) -> None:
        self.task_id = task_id
        self._project_root = project_root
        self._cli_path = cli_path
        self._loader = loader
        self._session_id = option_policy.new_session_id()

        self._lock = threading.RLock()
        self._buffer: Deque[DelegatedEvent] = deque(maxlen=MAX_BUFFERED_EVENTS)
        self._normalizer = normalize.MessageNormalizer()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Any = None
        self._connected = threading.Event()
        self._stopped = threading.Event()
        self._cancel_requested = False
        self._start_error: Optional[str] = None
        self._pending: Optional[_PendingQuestion] = None
        self._questions_asked = 0
        #: Sequence numbers for events this class mints itself — a cancellation
        #: notice, a transport failure, a clarification. Counted separately from
        #: the normalizer's so a Cofferdam-authored event can never be mistaken
        #: for one the provider sent.
        self._local_sequence = 0

    # -- reading -------------------------------------------------------------

    @property
    def provider_session_id(self) -> Optional[str]:  # type: ignore[override]
        return self._normalizer.provider_session_id or self._session_id

    @property
    def finished(self) -> bool:
        return self._stopped.is_set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    @property
    def pending_question_token(self) -> Optional[str]:  # type: ignore[override]
        with self._lock:
            return self._pending.token if self._pending is not None else None

    def drain(self) -> List[DelegatedEvent]:
        with self._lock:
            events = list(self._buffer)
            self._buffer.clear()
        return events

    # -- lifecycle -----------------------------------------------------------

    def start(self, prompt: str) -> None:
        """Connect, send the prompt, and return as soon as the session is real.

        Does not wait for the work — that arrives through :meth:`drain`. What it
        does wait for is *connection*, because a start that reported success
        before the subprocess existed would let Task Core mark a task running
        with nothing behind it.

        The prompt is delivered with ``query`` after an argument-free
        ``connect``, and the ordering is not stylistic: the SDK refuses a string
        prompt passed to ``connect`` when a permission callback is installed —
        verified in the published source — and the permission callback is not
        optional here. This is the shape that gets both a bounded permission
        policy and a working interrupt.
        """
        if self._thread is not None:
            raise SessionRefused("this session has already been started")
        self._thread = threading.Thread(
            target=self._run,
            args=(prompt,),
            name="cofferdam-agent-sdk-" + self.task_id[:24],
            daemon=True,
        )
        self._thread.start()
        if not self._connected.wait(START_TIMEOUT_SECONDS):
            # Never left running in the background: a start that did not connect
            # inside the bound is closed here, so a slow or wedged subprocess
            # cannot outlive the request that created it.
            self.close()
            raise SessionRefused("Claude did not start within the time Cofferdam waits")
        if self._start_error is not None:
            self.close()
            raise SessionRefused(self._start_error)

    def submit_answer(self, token: str, answer: str) -> bool:
        """Wake the blocked permission callback with one person's answer.

        Checked against the token the session itself minted, so an answer can
        only resolve the question it was written for. Two answers to one question
        cannot both land: the first clears the pending slot, and the second finds
        nothing to match.
        """
        if not isinstance(token, str) or not isinstance(answer, str) or not answer:
            return False
        loop = self._loop
        with self._lock:
            pending = self._pending
            if pending is None or pending.token != token or pending.cancelled:
                return False
            if pending.answer is not None:
                return False
            pending.answer = answer
        if loop is None:  # pragma: no cover - the loop is gone with the session
            return False
        try:
            loop.call_soon_threadsafe(pending.event.set)
        except RuntimeError:  # pragma: no cover - loop already closed
            return False
        return True

    def request_cancel(self) -> bool:
        """Interrupt this session, and only this one.

        Delivered by scheduling the SDK's own ``interrupt`` on the loop that owns
        the client. There is no signal, no pid and no process lookup anywhere in
        this method — which is what makes "cancel cannot reach another task"
        structural rather than checked.

        A question being held open is released first. A callback still awaiting
        an answer would otherwise sit there until its timeout while the session
        around it was being torn down, and the agent would be told nothing at the
        one moment it most needs to stop.
        """
        with self._lock:
            first = not self._cancel_requested
            self._cancel_requested = True
        self._release_pending_question()
        if first:
            self._emit(
                KIND_CANCELLATION_REQUESTED,
                text="Cofferdam asked Claude to stop this task.",
            )
        if self._stopped.is_set():
            return True

        loop = self._loop
        client = self._client
        if loop is None or client is None:
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(client.interrupt(), loop)
            future.result(timeout=CANCEL_TIMEOUT_SECONDS)
        except Exception:
            # The interrupt did not land. Reported as a failure to cancel rather
            # than escalated to something broader: this class stops one session
            # through its own client, and there is no wider hammer it is allowed
            # to reach for.
            return False
        return True

    def close(self) -> bool:
        """Disconnect and let the thread finish. ``True`` when it actually did.

        Called on every exit path — a start that failed, a cancel, a terminal
        result, an adapter shutdown — because the failure mode this guards is a
        subprocess that outlives the task that owns it, and that failure is
        invisible until a workstation has a dozen of them.
        """
        self._release_pending_question()
        loop = self._loop
        if loop is not None and not self._stopped.is_set():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:  # pragma: no cover - loop already closed
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(CLOSE_TIMEOUT_SECONDS)
            if thread.is_alive():
                return False
        return True

    def _release_pending_question(self) -> None:
        """Wake a blocked callback without answering it."""
        loop = self._loop
        with self._lock:
            pending = self._pending
            if pending is None or pending.cancelled:
                return
            pending.cancelled = True
        if loop is None:  # pragma: no cover
            return
        try:
            loop.call_soon_threadsafe(pending.event.set)
        except RuntimeError:  # pragma: no cover
            return

    # -- the thread ----------------------------------------------------------

    def _run(self, prompt: str) -> None:
        """The whole asynchronous half, on its own loop, in its own thread."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._drive(prompt))
        except Exception as exc:  # pragma: no cover - defensive
            self._record_transport_failure(exc)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # pragma: no cover - best effort
                pass
            loop.close()
            self._loop = None
            self._client = None
            self._connected.set()
            self._stopped.set()

    async def _drive(self, prompt: str) -> None:
        try:
            module = self._loader()
        except sdk_boundary.AgentSdkUnavailable as exc:
            self._start_error = exc.message
            return
        try:
            values = option_policy.build_option_values(
                project_root=self._project_root,
                session_id=self._session_id,
                cli_path=self._cli_path,
            )
            client = module.ClaudeSDKClient(
                option_policy.build_options(
                    module, values, can_use_tool=self._make_permission_handler(module)
                )
            )
        except Exception as exc:
            self._start_error = "Claude could not be configured (" + type(exc).__name__ + ")"
            return

        self._client = client
        try:
            # No prompt argument: the SDK refuses a string here when a permission
            # callback is installed. See :meth:`start`.
            await client.connect()
            await client.query(prompt)
        except Exception as exc:
            self._start_error = _transport_message(exc)
            self._client = None
            await _disconnect_quietly(client)
            return
        finally:
            self._connected.set()

        try:
            async for message in client.receive_messages():
                terminal = False
                for event in self._normalizer.normalize(message):
                    self._append(event)
                    if event.terminal:
                        terminal = True
                if terminal:
                    # The turn produced its result. Stop reading rather than
                    # holding a live subprocess for a follow-up this adapter
                    # does not deliver.
                    break
                if self._cancel_requested and self._stopped.is_set():  # pragma: no cover
                    break
        except asyncio.CancelledError:  # pragma: no cover - loop stopped under us
            raise
        except Exception as exc:
            self._record_transport_failure(exc)
        finally:
            if self._cancel_requested:
                self._emit(KIND_CANCELLED, text="This task was stopped.")
            await _disconnect_quietly(client)
            self._client = None

    # -- the permission channel ----------------------------------------------

    def _make_permission_handler(self, module: Any) -> Any:
        """Build the ``can_use_tool`` callback. It denies, and it records.

        Written as a closure over ``module`` so the SDK's ``PermissionResultDeny``
        is reached through the loaded handle rather than an import, keeping this
        file importable without the SDK.
        """

        def deny(message: str) -> Any:
            return module.PermissionResultDeny(
                behavior="deny", message=message, interrupt=False
            )

        async def can_use_tool(tool_name: Any, tool_input: Any, context: Any) -> Any:
            # `context` is accepted because the SDK's callback signature requires
            # it, and is deliberately not read: it carries a prompt sentence, a
            # blocked path and permission suggestions, none of which belongs in a
            # durable event a phone renders.
            if question_reader.is_question_tool(tool_name):
                return await self._handle_question(tool_input, deny)

            # `tool_input` is the command or path the agent wanted. For an
            # ordinary tool it is not read at all — the tool's name is enough for
            # a person to decide at the workstation, and that material is exactly
            # what makes approvals worth keeping on a trusted surface.
            request = normalize.approval_request(tool_name)
            if request is not None:
                self._append(
                    normalize.approval_event(
                        request=request,
                        provider_sequence=self._next_local_sequence(),
                        provider_session_id=self.provider_session_id,
                    )
                )
            return deny(
                "Cofferdam does not approve tools from a phone. Decide this at "
                "the workstation."
            )

        return can_use_tool

    async def _handle_question(self, tool_input: Any, deny: Callable[[str], Any]) -> Any:
        """Read one question, publish it, wait for an answer, and reply.

        Every exit from here is a deny. The question tool never runs: what the
        agent receives is either the person's answer or a sentence saying why
        there is not one.
        """
        observation = question_reader.observe(
            question_reader.QUESTION_TOOL_NAMES[0], tool_input
        )
        parsed = question_reader.read_question(tool_input)

        if parsed is None:
            # Conservative branch. The shape is not one this build can defend, so
            # no clarification is fabricated — a bounded observation goes into the
            # history instead, carrying key names and counts and no values.
            self._append(
                self._build_event(
                    KIND_ACTIVITY,
                    text=observation.summary(),
                    detail=safe_line(
                        "keys=" + str(len(observation.key_names))
                        + " questions=" + str(observation.question_count)
                        + " options=" + str(observation.option_count),
                        60,
                    ),
                )
            )
            return deny(
                "Cofferdam could not read that question, so nobody was asked. "
                "Continue with what you have, or state the choice in your reply."
            )

        with self._lock:
            if self._pending is not None:
                # One question at a time. A second while the first is open would
                # give a task two things to be waiting for and one state to say
                # so in.
                return deny("Cofferdam is already waiting on an earlier question.")
            if self._questions_asked >= MAX_QUESTIONS_PER_SESSION:
                return deny("This task has asked as many questions as it may.")
            self._questions_asked += 1
            pending = _PendingQuestion(new_question_token(), asyncio.Event())
            self._pending = pending

        self._append(
            self._build_event(
                normalize.KIND_CLARIFICATION_REQUESTED,
                text=parsed.question,
                clarification=normalize.clarification_from_observed(parsed),
                provider_event_id=pending.token,
            )
        )

        try:
            await asyncio.wait_for(pending.event.wait(), QUESTION_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            pending.cancelled = True
        except asyncio.CancelledError:  # pragma: no cover - loop torn down
            pending.cancelled = True
            raise
        finally:
            with self._lock:
                if self._pending is pending:
                    self._pending = None

        answer = None if pending.cancelled else pending.answer
        if answer is None:
            self._append(
                self._build_event(
                    KIND_ACTIVITY,
                    text="Nobody answered that question, so Claude was told so.",
                    detail="clarification_unanswered",
                )
            )
            return deny(
                "Nobody answered that question. Continue with what you have, or "
                "stop and say what you needed."
            )

        self._append(
            self._build_event(
                KIND_ACTIVITY,
                text="An answer was delivered to Claude.",
                detail="clarification_answered",
            )
        )
        # The answer, and nothing Cofferdam wrapped around it beyond one framing
        # sentence composed here. There is no template read from anywhere and no
        # client-supplied structure in this string — see
        # ``clarifications.encode_answer``, which produced the answer itself.
        return deny(
            "The question was not asked through this tool. The person answered: "
            + answer
        )

    # -- bookkeeping ---------------------------------------------------------

    def _next_local_sequence(self) -> int:
        with self._lock:
            self._local_sequence += 1
            # Offset far above the normalizer's own counter so a Cofferdam event
            # and a provider event can never collide on a sequence number and be
            # read as the same point in the stream.
            return 1_000_000 + self._local_sequence

    def _append(self, event: DelegatedEvent) -> None:
        with self._lock:
            self._buffer.append(event)

    def _build_event(self, kind: str, **fields: Any) -> DelegatedEvent:
        return build_event(
            kind=kind,
            provider=normalize.PROVIDER,
            provider_sequence=self._next_local_sequence(),
            observed_at=now_iso(),
            provider_session_id=self.provider_session_id,
            **fields,
        )

    def _emit(self, kind: str, *, text: str, **fields: Any) -> None:
        self._append(self._build_event(kind, text=text, **fields))

    def _record_transport_failure(self, exc: BaseException) -> None:
        """Record a provider failure in Cofferdam's words.

        The exception's own text is discarded. An SDK error message can carry a
        path, an executable location, a version string or a fragment of the
        stream, and Task Core's contract is that a failure a person reads is
        Cofferdam's sentence — with the exception *type name* as the only thing
        borrowed, because that much is a category rather than content.
        """
        self._emit(
            KIND_PROVIDER_FAILED,
            text="The Claude session ended unexpectedly.",
            detail=safe_line(type(exc).__name__, 60),
            failure_code="transport_error",
        )


async def _disconnect_quietly(client: Any) -> None:
    """Close a client without letting the close itself fail a task.

    A disconnect that raises after the work is done would turn a completed task
    into a failure over cleanup, which is the wrong trade: the result is already
    known, and the process is going away either way.
    """
    try:
        await client.disconnect()
    except Exception:  # pragma: no cover - best effort
        pass


def _transport_message(exc: BaseException) -> str:
    """A start failure, categorised, in Cofferdam's words.

    Three categories because three different things go wrong at connect time and
    each sends somebody somewhere different: the CLI is missing, the CLI would
    not start, or something else entirely. The SDK's own message is never
    included.
    """
    name = type(exc).__name__
    if name == "CLINotFoundError":
        return "Claude Code is not installed where the Agent SDK looked for it"
    if name in ("CLIConnectionError", "ProcessError"):
        return "Claude Code could not be started by the Agent SDK"
    return "the Claude Agent SDK could not start this task (" + name + ")"


__all__ = [
    "CANCEL_TIMEOUT_SECONDS",
    "CLOSE_TIMEOUT_SECONDS",
    "MAX_BUFFERED_EVENTS",
    "MAX_QUESTIONS_PER_SESSION",
    "QUESTION_TIMEOUT_SECONDS",
    "QUESTION_TOKEN_PREFIX",
    "START_TIMEOUT_SECONDS",
    "DelegatedSession",
    "SdkSession",
    "SessionRefused",
    "new_question_token",
]
