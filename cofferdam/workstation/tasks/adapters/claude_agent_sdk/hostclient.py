"""Launching the Agent SDK helper, and talking to it. The daemon side.

:class:`HostSession` implements the same synchronous
:class:`~.session.DelegatedSession` surface the adapter is written against, so
from above it is indistinguishable from an in-process session. Below, it is a
subprocess with a pipe.

This is the file that owns the environment boundary
---------------------------------------------------

Everything in :mod:`.hostenv` is a list of names; **this** is where a process is
actually created with them. The launch is deliberately rigid:

* a fixed executable — ``sys.executable``, the interpreter already running;
* a fixed argument vector — ``-m`` and one module path constant, with no
  element that came from a caller, a request, a registry or a configuration file;
* ``shell=False``, so no element of that vector is ever interpreted as syntax;
* ``env=`` a complete mapping built by selection, which **replaces** the child's
  environment rather than merging with it;
* ``cwd=`` the project root the server resolved and verified moments earlier;
* ``stderr`` discarded, so nothing the SDK might print can corrupt a frame or
  reach a log;
* ``start_new_session=True``, so the child has its own process group and a
  Ctrl-C reaching the daemon cannot reach a task.

There is no parameter on any method in this file for an executable, an argument,
an environment, a shell or a path — the only values that vary are a task id
Cofferdam minted, a project root the server resolved, and a CLI path from the
registry's fixed host search.

Failure is reported, never guessed
-----------------------------------

A helper that will not start, will not say ``ready``, or dies mid-session
produces a refusal or a provider-failure event in Cofferdam's words. Nothing here
reports a session as running because a ``fork`` succeeded, and nothing reports it
as stopped because stopping it was requested.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
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
from . import hostenv, hostproto, normalize
from .session import DelegatedSession, SessionRefused

#: The module the helper runs. A constant, and the only thing after ``-m`` in the
#: argument vector. Written out rather than derived from ``__name__`` so that the
#: exact string a process is started with is greppable in this file.
HOST_MODULE = "cofferdam.workstation.tasks.adapters.claude_agent_sdk.host"

#: How long the parent waits for the helper to say it is ready. Covers an
#: interpreter start and an import of the SDK, which is not a small package.
READY_TIMEOUT_SECONDS = 60.0

#: How long the parent waits for the helper to report a started session. Covers
#: the SDK's own connect, which starts a CLI subprocess of its own.
START_TIMEOUT_SECONDS = 120.0

#: How long a close waits for the helper to exit before it is stopped, and then
#: how long after that before it is killed. Two steps, and the first one is
#: longer, for the same reason the Claude Code adapter's escalation is shaped
#: that way: an agent killed mid-write is one that did not finish writing the
#: file it was editing.
CLOSE_TIMEOUT_SECONDS = 20.0
TERMINATE_TIMEOUT_SECONDS = 5.0

#: How long the parent waits for the helper to acknowledge a follow-up by
#: starting the turn. Short: the helper is a local process that only has to
#: write one line to a pipe its client is already holding open, and a wait long
#: enough to cover a hung helper would be a wait long enough to hang a request
#: somebody made from a phone.
FOLLOWUP_ACK_TIMEOUT_SECONDS = 15.0

#: How often that wait re-reads the helper's reported state.
FOLLOWUP_POLL_SECONDS = 0.02

#: The most events one session buffers between drains.
MAX_BUFFERED_EVENTS = 500


class HostSession(DelegatedSession):
    """One helper process, holding one Agent SDK session.

    Constructed by the adapter with values the adapter resolved. The signature is
    identical to the in-process session's, so the adapter does not know which one
    it has — which is what lets every adapter behaviour be tested with a double.
    """

    def __init__(
        self,
        *,
        task_id: str,
        project_root: Path,
        cli_path: Optional[Path] = None,
        launcher: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.task_id = task_id
        self._project_root = project_root
        self._cli_path = cli_path
        #: Injected only by tests, which pass a double that speaks the protocol
        #: without a process. Never set from configuration and never from a
        #: request: there is no path from either to this constructor.
        self._launcher = launcher or _spawn_helper

        self._lock = threading.RLock()
        self._buffer: Deque[DelegatedEvent] = deque(maxlen=MAX_BUFFERED_EVENTS)
        self._process: Any = None
        #: Who the child was, read once at launch. See :class:`OwnedChild`: this
        #: is what every later signal is checked against, and recording it here
        #: rather than at stop time is what makes "the thing we signal is the
        #: thing we started" a fact about this object rather than an inference.
        self._owned: Optional[OwnedChild] = None
        self._reader: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._error: Optional[str] = None
        self._provider_session_id: Optional[str] = None
        self._pending_token: Optional[str] = None
        self._cancel_requested = False
        self._local_sequence = 0
        #: Mirrors the helper's own view, learned from ``turn_complete``. The
        #: parent never infers it from a terminal event: a terminal event says a
        #: turn produced a result, and whether the *session* survived it is the
        #: helper's fact to report, not the parent's to guess.
        self._turn_complete = False
        self._turn_number = 0
        #: Set when a follow-up has been written and the helper has not yet said
        #: the new turn ended. Cleared by the next ``turn_complete``.
        self._followup_in_flight = False
        self._followup_error: Optional[str] = None

    # -- reading -------------------------------------------------------------

    @property
    def provider_session_id(self) -> Optional[str]:  # type: ignore[override]
        return self._provider_session_id

    @property
    def finished(self) -> bool:
        return self._stopped.is_set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    @property
    def pending_question_token(self) -> Optional[str]:  # type: ignore[override]
        with self._lock:
            return self._pending_token

    @property
    def turn_complete(self) -> bool:  # type: ignore[override]
        with self._lock:
            return self._turn_complete and not self._stopped.is_set()

    @property
    def turn_number(self) -> int:  # type: ignore[override]
        with self._lock:
            return self._turn_number

    def drain(self) -> List[DelegatedEvent]:
        with self._lock:
            events = list(self._buffer)
            self._buffer.clear()
        return events

    # -- lifecycle -----------------------------------------------------------

    def start(self, prompt: str) -> None:
        """Launch the helper, deliver the prompt, and return once it is real.

        Waits for two acknowledgements rather than one. ``ready`` proves the
        helper booted with an environment it accepted; ``started`` proves the SDK
        connected and took the prompt. A start that reported success on the
        strength of a successful ``Popen`` would let Task Core mark a task
        running with nothing behind it — the exact failure the Claude Code
        adapter's launch evidence exists to prevent.
        """
        if self._process is not None:
            raise SessionRefused("this session has already been started")

        try:
            self._process = self._launcher(project_root=self._project_root)
        except (OSError, ValueError) as exc:
            self._stopped.set()
            raise SessionRefused(
                "Cofferdam could not start the Claude Agent SDK helper ("
                + type(exc).__name__
                + ")"
            )

        # Immediately, and before anything else can happen to the child: the
        # pid, its `/proc` start time and the group it leads, as they are right
        # now. Every signal this session ever sends is checked against these
        # three, and none is sent if any has changed.
        self._owned = OwnedChild(self._process)

        self._reader = threading.Thread(
            target=self._read,
            name="cofferdam-sdk-host-" + self.task_id[:24],
            daemon=True,
        )
        self._reader.start()

        if not self._ready.wait(READY_TIMEOUT_SECONDS):
            self.close()
            raise SessionRefused(self._error or "the Claude Agent SDK helper did not start")
        if self._error is not None:
            self.close()
            raise SessionRefused(self._error)

        if not self._send(
            hostproto.command(
                hostproto.COMMAND_START,
                task_id=self.task_id,
                prompt=prompt,
                cli_path=str(self._cli_path) if self._cli_path is not None else None,
            )
        ):
            self.close()
            raise SessionRefused("the Claude Agent SDK helper stopped before it began")

        if not self._started.wait(START_TIMEOUT_SECONDS):
            self.close()
            raise SessionRefused(
                self._error or "Claude did not start within the time Cofferdam waits"
            )
        if self._error is not None:
            self.close()
            raise SessionRefused(self._error)

    def submit_answer(self, token: str, answer: str) -> bool:
        """Send one answer down to the helper, for one question it is holding.

        The token is checked here as well as in the helper. Two checks because
        they answer different questions: this one prevents a stale answer from
        being written to the pipe at all, and the helper's prevents one that
        raced a supersession from reaching a callback that has moved on.
        """
        if not isinstance(token, str) or not isinstance(answer, str) or not answer:
            return False
        with self._lock:
            if self._pending_token != token:
                return False
            self._pending_token = None
        delivered = self._send(
            hostproto.command(hostproto.COMMAND_ANSWER, token=token, answer=answer)
        )
        if not delivered:
            with self._lock:
                # Put it back: the question is still open if the write failed,
                # and a token cleared by a failed delivery would make the
                # question unanswerable for the rest of the session.
                self._pending_token = token
        return delivered

    def send_followup(self, followup: str) -> None:  # type: ignore[override]
        """Write one follow-up down the pipe, and confirm the helper took it.

        Synchronous by design, and the wait is short. The alternative — write
        and return — would let Task Core move a task to ``running`` on the
        strength of a successful ``write()``, which is the same "a fork
        succeeded so it must be working" claim this whole file exists to refuse.
        What is waited for is the helper *starting* the turn, not finishing it.

        Every refusal raises :class:`SessionRefused` with Cofferdam's words, so
        a message that went nowhere is reported as one.
        """
        if not isinstance(followup, str) or not followup:
            raise SessionRefused("that follow-up is not usable")
        with self._lock:
            if self._stopped.is_set() or self._process is None:
                raise SessionRefused("this Claude session is no longer running")
            if self._cancel_requested:
                raise SessionRefused("this task is being cancelled")
            if self._pending_token is not None:
                raise SessionRefused("this session is waiting for an answer")
            if not self._turn_complete:
                raise SessionRefused("Claude is still working on the previous message")
            if self._followup_in_flight:
                raise SessionRefused("a follow-up is already being delivered")
            self._followup_error = None
            self._followup_in_flight = True
            expected = self._turn_number

        if not self._send(
            hostproto.command(hostproto.COMMAND_FOLLOWUP, followup=followup)
        ):
            with self._lock:
                self._followup_in_flight = False
            raise SessionRefused("the Claude Agent SDK helper stopped")

        # Wait for the helper either to begin the turn — which it signals by
        # ceasing to be turn-complete — or to refuse it. Bounded, because a
        # helper that answers neither is a helper that is gone.
        deadline = time.monotonic() + FOLLOWUP_ACK_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            with self._lock:
                problem = self._followup_error
                started = not self._turn_complete or self._turn_number > expected
                gone = self._stopped.is_set()
            if problem is not None:
                with self._lock:
                    self._followup_in_flight = False
                    self._followup_error = None
                raise SessionRefused(problem)
            if gone:
                with self._lock:
                    self._followup_in_flight = False
                raise SessionRefused("the Claude session ended before the follow-up")
            if started:
                return
            time.sleep(FOLLOWUP_POLL_SECONDS)

        with self._lock:
            self._followup_in_flight = False
        raise SessionRefused("Claude did not take the follow-up in time")

    def request_cancel(self) -> bool:
        """Ask the helper to interrupt its session, and only its own.

        The message goes down the pipe to one process. There is no signal here,
        no pid lookup and no process-name matching — which is what makes "cancel
        cannot reach another task" structural rather than checked. The escalation
        to a signal exists only in :meth:`close`, and only against the child this
        object launched.
        """
        with self._lock:
            first = not self._cancel_requested
            self._cancel_requested = True
            self._pending_token = None
        if first:
            self._emit(
                KIND_CANCELLATION_REQUESTED,
                text="Cofferdam asked Claude to stop this task.",
            )
        if self._stopped.is_set():
            return True
        return self._send(hostproto.command(hostproto.COMMAND_CANCEL))

    def close(self) -> bool:
        """Ask the helper to exit, then make sure it did. ``True`` if it let go.

        Escalation is bounded and targets only this object's own child: close
        stdin, wait, terminate the process group, wait, kill. Nothing here
        enumerates processes or matches one by name.
        """
        process = self._process
        if process is None:
            self._stopped.set()
            return True

        self._send(hostproto.command(hostproto.COMMAND_CLOSE))
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:  # pragma: no cover
            pass

        released = _stop_process(process, self._owned)
        thread = self._reader
        if thread is not None and thread.is_alive():
            thread.join(TERMINATE_TIMEOUT_SECONDS)
        self._stopped.set()
        return released

    # -- the pipe ------------------------------------------------------------

    def _send(self, payload: dict) -> bool:
        process = self._process
        if process is None or process.stdin is None:
            return False
        try:
            process.stdin.write(hostproto.encode_line(payload))
            process.stdin.flush()
        except (OSError, ValueError):
            return False
        return True

    def _read(self) -> None:
        """Consume the helper's stdout until it closes.

        Every line is decoded, bounded and rebuilt through the ordinary event
        constructors. A line this build cannot understand is dropped: a helper
        from a different build should cost an unread event, not a dead session.
        """
        process = self._process
        stream = process.stdout if process is not None else None
        if stream is None:  # pragma: no cover - defensive
            self._ready.set()
            self._started.set()
            self._stopped.set()
            return
        try:
            for raw in stream:
                if len(raw) > hostproto.MAX_LINE_BYTES:
                    continue
                parsed = hostproto.decode_line(raw)
                if parsed is not None:
                    self._absorb(parsed)
        except (OSError, ValueError):  # pragma: no cover - the pipe went away
            pass
        finally:
            if not self._started.is_set() and self._error is None:
                self._error = self._error or "the Claude Agent SDK helper stopped"
            self._ready.set()
            self._started.set()
            self._stopped.set()

    def _absorb(self, payload: dict) -> None:
        name = payload.get("message")
        if name == hostproto.MESSAGE_READY:
            self._ready.set()
            return
        if name == hostproto.MESSAGE_STARTED:
            candidate = payload.get("provider_session_id")
            if isinstance(candidate, str) and candidate:
                self._provider_session_id = candidate[:64]
            with self._lock:
                self._turn_number = max(self._turn_number, 1)
            self._started.set()
            return
        if name == hostproto.MESSAGE_TURN_COMPLETE:
            candidate = payload.get("provider_session_id")
            with self._lock:
                if isinstance(candidate, str) and candidate:
                    if not self._provider_session_id:
                        self._provider_session_id = candidate[:64]
                turn = payload.get("turn")
                if isinstance(turn, int) and turn > self._turn_number:
                    self._turn_number = turn
                self._turn_complete = True
                self._followup_in_flight = False
            return
        if name == hostproto.MESSAGE_ERROR:
            # Bounded, and already Cofferdam's words: the helper never puts an
            # SDK message into this field. See `host._verify_own_environment`
            # and `session._transport_message`.
            detail = safe_line(payload.get("detail"), 200) or "the helper refused"
            with self._lock:
                if self._followup_in_flight:
                    # A refusal that answers a follow-up in flight belongs to
                    # that request, not to the session as a whole. Routed to the
                    # waiting caller rather than latched into `_error`, which
                    # would make every later start check read as failed.
                    self._followup_error = detail
                    return
            self._error = detail
            self._ready.set()
            return
        if name == hostproto.MESSAGE_FINISHED:
            self._stopped.set()
            return
        if name != hostproto.MESSAGE_EVENT:
            return

        event = hostproto.event_from_payload(payload.get("event"))
        if event is None:
            return
        if event.provider_session_id and not self._provider_session_id:
            self._provider_session_id = event.provider_session_id
        if not event.terminal:
            # Anything that is not a turn ending means work is happening, so a
            # session that had been reported complete is not any more. Belt to
            # the helper's braces: the helper resets its own flag when a turn
            # begins, and this keeps the parent from offering a follow-up box
            # for a session that has already taken one.
            with self._lock:
                self._turn_complete = False
        if event.kind == normalize.KIND_CLARIFICATION_REQUESTED:
            # The helper's own token for this question, carried as the provider
            # event id. Remembered so an answer can be routed back to the
            # question that is actually open, and so a duplicate question event
            # cannot open a second one.
            with self._lock:
                if event.provider_event_id:
                    self._pending_token = event.provider_event_id
        with self._lock:
            self._buffer.append(event)

    # -- bookkeeping ---------------------------------------------------------

    def _next_local_sequence(self) -> int:
        with self._lock:
            self._local_sequence += 1
            return 2_000_000 + self._local_sequence

    def _emit(self, kind: str, *, text: str, **fields: Any) -> None:
        event = build_event(
            kind=kind,
            provider=normalize.PROVIDER,
            provider_sequence=self._next_local_sequence(),
            observed_at=now_iso(),
            provider_session_id=self._provider_session_id,
            text=text,
            **fields,
        )
        with self._lock:
            self._buffer.append(event)

    def note_cancelled(self) -> None:
        """Record that this session stopped because it was cancelled.

        Called by the adapter when a cancel has been delivered and the helper is
        gone without having produced a terminal event of its own. It exists
        because ``cancelled`` is a claim about what happened, and the object that
        knows whether a cancel was actually delivered is this one.
        """
        if self._cancel_requested:
            self._emit(KIND_CANCELLED, text="This task was stopped.")

    def note_lost(self) -> None:
        """Record that the helper went away without reporting anything."""
        self._emit(
            KIND_PROVIDER_FAILED,
            text="The Claude session ended unexpectedly.",
            detail=safe_line(self._error, 60) or "helper_stopped",
            failure_code="transport_error",
        )


def _spawn_helper(*, project_root: Path) -> Any:
    """Start one helper process. The only ``Popen`` in this package.

    Every argument is either a constant in this file or a value the server
    resolved. Read it as the answer to "what exactly does Cofferdam run, and what
    can it see" — because this call is the whole answer.
    """
    return subprocess.Popen(  # noqa: S603 - fixed argv, shell=False, no request input
        [sys.executable, "-m", HOST_MODULE],
        cwd=str(project_root),
        env=hostenv.build_child_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        # Discarded rather than piped. Nothing reads it, so a helper that wrote
        # to it could neither block on a full pipe nor put a provider's words
        # into a Cofferdam log.
        stderr=subprocess.DEVNULL,
        shell=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=True,
    )


def read_start_time(pid: int) -> Optional[int]:
    """Field 22 of ``/proc/<pid>/stat``: the process start time, in clock ticks.

    Duplicated from the Claude Code adapter rather than imported from it, and the
    duplication is deliberate — this package must not import that one. Task Core
    asserts the boundary, and an adapter that reached into a sibling would be
    stranded the day that sibling is retired, which the roadmap says it will be.
    Fifteen lines is a smaller cost than that coupling.

    Parsed by splitting on the **last** ``)``, because field 2 is the executable
    name in parentheses and may itself contain spaces and parentheses.
    """
    try:
        with open("/proc/" + str(int(pid)) + "/stat", "r", encoding="utf-8") as handle:
            raw = handle.read()
    except (OSError, ValueError):
        return None
    _, _, tail = raw.rpartition(")")
    fields = tail.split()
    # After the closing paren, fields[0] is state (field 3), so start time
    # (field 22) is at index 19.
    if len(fields) <= 19:
        return None
    try:
        return int(fields[19])
    except ValueError:
        return None


class OwnedChild:
    """The identity of one helper process, recorded at launch and re-checked.

    The same four-fact rule the Claude Code adapter has enforced since M2G, and
    it is here for the same reason: **a pid is a small integer the kernel
    reuses.** Between recording one and signalling it, the helper can exit and
    somebody's editor can be given the same number. On a personal workstation
    that editor is the thing this whole codebase exists not to disturb.

    So a signal is sent only when the pid, the start time read from
    ``/proc/<pid>/stat``, and the process group all still agree with what was
    recorded at launch. The start time is the clause that does the work: the
    kernel assigns it at exec and a recycled pid cannot reproduce it.

    The **group** is what is signalled rather than the pid, because
    :func:`_spawn_helper` passes ``start_new_session=True`` and the CLI the SDK
    starts runs inside that group. Signalling the helper alone would stop the
    helper and leave its CLI grandchild running — an orphan holding a
    subscription session, which is precisely the leak this milestone names.

    Nothing here enumerates processes, reads a process name, or names a group
    Cofferdam did not create.

    **Recorded at launch, in :meth:`HostSession.start`, immediately after
    ``Popen`` returns.** Building it later — at the moment of stopping — would
    also be sound, because a pid cannot be recycled until its parent reaps it and
    this process is the parent; but "sound because of something happening
    elsewhere" is a worse property than "recorded when it was true", and the
    first reading of the code should not have to reconstruct that argument.
    """

    def __init__(self, process: Any) -> None:
        self.process = process
        self.pid: Optional[int] = None
        self.start_time: Optional[int] = None
        self.pgid: Optional[int] = None

        pid = getattr(process, "pid", None)
        if not isinstance(pid, int) or pid <= 0:
            # A test double without a pid. It is stopped through its own
            # `terminate`/`kill`, and no signal is sent to anything.
            return
        self.pid = pid
        self.start_time = read_start_time(pid)
        try:
            self.pgid = os.getpgid(pid)
        except OSError:  # pragma: no cover - the child exited immediately
            self.pgid = None

    def still_ours(self) -> bool:
        """Whether the recorded pid still refers to the process that was launched.

        Every clause matters, and the group clause is the one that makes a signal
        *targetable*: it confirms the group about to be signalled is the group
        this child leads, rather than whatever group that pid now belongs to.
        """
        if self.pid is None or self.start_time is None or self.pgid is None:
            return False
        if self.process.poll() is not None:
            return False
        if read_start_time(self.pid) != self.start_time:
            return False
        try:
            return os.getpgid(self.pid) == self.pgid
        except OSError:
            return False

    def signal_group(self, signal_number: int) -> bool:
        """Signal the owned group, after re-verifying it is still owned.

        ``False`` means nothing was signalled — either the identity no longer
        matches, or the process is already gone. Both are safe answers and
        neither is a reason to broaden the search.
        """
        if not self.still_ours() or self.pgid is None:
            return False
        try:
            os.killpg(self.pgid, signal_number)
        except OSError:
            return False
        return True


def _stop_process(process: Any, owned: Optional[OwnedChild] = None) -> bool:
    """Wait, then signal the owned group, terminate-then-kill. Bounded throughout.

    ``owned`` is the identity recorded when the process was launched. It is
    optional only so that a caller holding nothing but a ``Popen`` — which is
    what the process-level tests hold — still gets the group behaviour rather
    than silently falling back; the identity it builds here is the same one, read
    a moment later.

    The graceful step comes first and waits longest, for the reason the Claude
    Code adapter's escalation is shaped the same way: an agent killed mid-write
    is one that did not finish writing the file it was editing.

    Every branch ends in a ``wait``, so the child is reaped rather than left as a
    zombie holding a slot in the process table — a helper that is gone and never
    waited for is still a process as far as the kernel is concerned.

    A double with no pid falls through to its own ``terminate``/``kill``, which
    is what the tests exercise and what a non-POSIX host would get.
    """
    try:
        process.wait(timeout=CLOSE_TIMEOUT_SECONDS)
        return True
    except Exception:
        pass

    if owned is None:
        owned = OwnedChild(process)
    for signal_number, fallback in (
        (signal.SIGTERM, "terminate"),
        (signal.SIGKILL, "kill"),
    ):
        if not owned.signal_group(signal_number):
            action = getattr(process, fallback, None)
            if not callable(action):  # pragma: no cover - a double without one
                continue
            try:
                action()
            except Exception:
                continue
        try:
            process.wait(timeout=TERMINATE_TIMEOUT_SECONDS)
            return True
        except Exception:
            continue
    return False


__all__ = [
    "CLOSE_TIMEOUT_SECONDS",
    "OwnedChild",
    "read_start_time",
    "HOST_MODULE",
    "MAX_BUFFERED_EVENTS",
    "FOLLOWUP_ACK_TIMEOUT_SECONDS",
    "FOLLOWUP_POLL_SECONDS",
    "READY_TIMEOUT_SECONDS",
    "START_TIMEOUT_SECONDS",
    "TERMINATE_TIMEOUT_SECONDS",
    "HostSession",
]
