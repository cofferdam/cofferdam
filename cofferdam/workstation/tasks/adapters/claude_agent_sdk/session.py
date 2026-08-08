"""One delegated task, one Agent SDK session, held behind a testable boundary.

The SDK is asynchronous — ``anyio``, an async context manager, an async
iterator — and Task Core is not. Task Core is a synchronous service that a route
calls, that holds a lock, and that asks an adapter what has happened whenever
somebody looks at a task. Something has to sit between those two shapes.

This module is that something, and it is deliberately two things rather than one:

:class:`DelegatedSession`
    The boundary. A small synchronous surface — start, drain, cancel, close —
    with no SDK anywhere in it. The adapter is written against *this*, so every
    behaviour worth testing (ordering, cancellation precedence, terminal
    finality, resource release) can be tested with a double on a machine where
    the SDK is not installed.

:class:`SdkSession`
    The real implementation. It owns one thread, that thread owns one event
    loop, and the loop owns one ``ClaudeSDKClient``. Nothing else in Cofferdam
    creates a task, a loop or a thread for this.

The rules the implementation is written to
------------------------------------------

**One session per task, and no way to reach another.** The client is held on the
instance, found by the task that owns it. There is no registry to look one up
in, no identifier a caller passes, and therefore no argument that could point a
cancel at somebody else's session.

**Bounded everything.** One thread, one loop, one bounded event buffer that drops
its oldest entries rather than growing. A session that produced ten thousand
events uses the same memory as one that produced two hundred.

**Cancellation reaches the SDK, and cannot be faked.** ``request_cancel`` calls
the SDK's own ``interrupt`` on the loop that owns the client, then closes the
connection. If it does not work, this class says so — nothing here reports a
session as stopped because stopping it was requested.

**A terminal event ends the iteration.** Once the session has produced a result,
the reader stops, the client disconnects and the thread exits. That is what keeps
"no unbounded background task" true rather than intended, and it is why this
foundation does not offer same-session follow-up: keeping the session alive for
another turn is a different lifetime with different failure modes, and it belongs
to the PR that also builds the answer channel. The *seam* is here — the provider
session id is preserved and :meth:`DelegatedSession.send_followup` exists and
refuses truthfully — so that PR changes this file rather than the adapter above
it.

Two things the permission handler will never do
-----------------------------------------------

It never allows. The callback Cofferdam installs returns a deny, always, and
records that the agent asked — which turns a permission request into a task
waiting for a person at the workstation rather than an automatic yes from a
phone.

It never reads the tool input. The callback is handed the command, the path and
the arguments the agent wanted to use, and the handler takes the tool's *name*
and nothing else. Those arguments are exactly the material that makes approvals
worth keeping on a trusted surface; copying them into an event a phone renders
would give away the thing the arrangement protects.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, List, Optional

from ....runtime.identity import now_iso
from ...delegated import (
    KIND_CANCELLATION_REQUESTED,
    KIND_CANCELLED,
    KIND_PROVIDER_FAILED,
    DelegatedEvent,
    build_event,
    safe_line,
)
from . import normalize, options as option_policy, sdk as sdk_boundary

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

#: The most normalized events one session buffers between drains. Oldest are
#: dropped, because the newest activity is what somebody looking at a phone
#: wants and an unbounded buffer is a memory leak with a nice name.
MAX_BUFFERED_EVENTS = 500


class SessionRefused(RuntimeError):
    """The session could not do what was asked, with a sentence saying why.

    Distinct from the SDK's own errors, which never leave this module: an SDK
    exception's text can name a path, a version or an account, and Task Core's
    contract is that failure messages are Cofferdam's words. What crosses this
    boundary is a short sentence and, at most, an exception *type name*.
    """


class DelegatedSession:
    """The synchronous surface an adapter is written against.

    Every method here is a refusal by default. A subclass gains a behaviour by
    implementing it, never by inheriting a permissive stub — the same direction
    the Task Core adapter protocol chose, and for the same reason: the safer way
    for a missing implementation to fail is loudly.
    """

    #: The provider's own session identifier, once it has reported one. Preserved
    #: so a later PR can resume the same conversation, and so a durable result
    #: can say which session produced it.
    provider_session_id: Optional[str] = None

    def start(self, prompt: str) -> None:
        raise SessionRefused("this session cannot be started")

    def drain(self) -> List[DelegatedEvent]:
        """Every normalized event since the last drain. Never blocks."""
        return []

    def send_followup(self, followup: str) -> None:
        """Deliver another user turn to the same session.

        Refused in this foundation, and refused rather than silently accepted:
        the session ends at its first terminal event here, so a follow-up would
        have nowhere to go and a client told "ok" would be told something false.
        The seam exists so M2I PR2 changes this method rather than the shape of
        everything above it.
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


class SdkSession(DelegatedSession):
    """One Claude Agent SDK session, driven from a dedicated thread.

    Constructed by the adapter with values the adapter resolved: a task id it
    minted, a project root the server verified, and an executable found by the
    fixed search in the Claude Code adapter's ``cli`` module. Nothing here takes
    a parameter that could carry a request value.
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
        #: Sequence numbers for events this class mints itself — a cancellation
        #: notice, a transport failure. Counted separately from the normalizer's
        #: so a Cofferdam-authored event can never be mistaken for one the
        #: provider sent.
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

    def request_cancel(self) -> bool:
        """Interrupt this session, and only this one.

        Delivered by scheduling the SDK's own ``interrupt`` on the loop that owns
        the client. There is no signal, no pid and no process lookup anywhere in
        this method — which is what makes "cancel cannot reach another task"
        structural rather than checked.

        Repeated cancellation is truthful: the second call records nothing new
        and returns whether the session is actually stopping, so a caller cannot
        be told a fresh cancellation happened when it did not.
        """
        with self._lock:
            first = not self._cancel_requested
            self._cancel_requested = True
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
                    # holding a live subprocess for a follow-up this foundation
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

        async def can_use_tool(tool_name: Any, tool_input: Any, context: Any) -> Any:
            # `tool_input` and `context` are accepted because the SDK's callback
            # signature requires them, and are deliberately not read. The input
            # is the command or path the agent wanted; the context carries a
            # prompt sentence and a blocked path. None of it belongs in a durable
            # event a phone renders, and the tool's name is enough for a person
            # to decide at the workstation.
            request = normalize.approval_request(tool_name)
            if request is not None:
                self._append(
                    normalize.approval_event(
                        request=request,
                        provider_sequence=self._next_local_sequence(),
                        provider_session_id=self.provider_session_id,
                    )
                )
            return module.PermissionResultDeny(
                behavior="deny",
                message=(
                    "Cofferdam does not approve tools from a phone. Decide this "
                    "at the workstation."
                ),
                interrupt=False,
            )

        return can_use_tool

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

    def _emit(self, kind: str, *, text: str, **fields: Any) -> None:
        self._append(
            build_event(
                kind=kind,
                provider=normalize.PROVIDER,
                provider_sequence=self._next_local_sequence(),
                observed_at=now_iso(),
                provider_session_id=self.provider_session_id,
                text=text,
                **fields,
            )
        )

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
    "START_TIMEOUT_SECONDS",
    "DelegatedSession",
    "SdkSession",
    "SessionRefused",
]
