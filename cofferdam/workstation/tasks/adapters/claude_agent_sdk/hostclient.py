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

import subprocess
import sys
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
        self._reader: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._error: Optional[str] = None
        self._provider_session_id: Optional[str] = None
        self._pending_token: Optional[str] = None
        self._cancel_requested = False
        self._local_sequence = 0

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

        released = _stop_process(process)
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
            self._started.set()
            return
        if name == hostproto.MESSAGE_ERROR:
            # Bounded, and already Cofferdam's words: the helper never puts an
            # SDK message into this field. See `host._verify_own_environment`
            # and `session._transport_message`.
            self._error = safe_line(payload.get("detail"), 200) or "the helper refused"
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


def _stop_process(process: Any) -> bool:
    """Wait, then terminate, then kill. Bounded at every step."""
    try:
        process.wait(timeout=CLOSE_TIMEOUT_SECONDS)
        return True
    except Exception:
        pass
    for stop in ("terminate", "kill"):
        action = getattr(process, stop, None)
        if not callable(action):  # pragma: no cover - a double without one
            continue
        try:
            action()
            process.wait(timeout=TERMINATE_TIMEOUT_SECONDS)
            return True
        except Exception:
            continue
    return False


__all__ = [
    "CLOSE_TIMEOUT_SECONDS",
    "HOST_MODULE",
    "MAX_BUFFERED_EVENTS",
    "READY_TIMEOUT_SECONDS",
    "START_TIMEOUT_SECONDS",
    "TERMINATE_TIMEOUT_SECONDS",
    "HostSession",
]
