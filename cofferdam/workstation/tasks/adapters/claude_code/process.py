"""One Claude process, owned by one task, signalled only after it is identified.

This module launches the CLI, reads its stream, and stops it. It never composes
a command line — :mod:`.cli` does that — and it never decides what a task means:
it reports what the process did and lets the adapter turn that into a Task Core
report.

Process identity, and why a pid is not enough
---------------------------------------------

A pid is a small integer that the kernel reuses. Between the moment Cofferdam
records one and the moment it sends a signal, the process can exit and an
unrelated program can be given the same number — and a personal workstation is
exactly where that unrelated program is somebody's editor.

So a run is identified by four facts together: the pid, the process *start time*
read from ``/proc/<pid>/stat`` field 22, the process group id, and the adapter
run id this object was constructed with. :meth:`ClaudeRun.still_ours` requires
all of them to agree before any signal is sent. The start time is the one that
does the work — it is assigned by the kernel at exec and cannot be reproduced by
a recycled pid.

What is never done here
-----------------------

No ``shell=True``. No ``bash -c``. No ``pkill``, ``killall``, ``pidof``, or any
match on a process *name* — a signal is sent to a number this object launched
and verified, or it is not sent. No signal is ever sent to a pid that arrived
from anywhere but :meth:`start`.
"""

from __future__ import annotations

import errno
import os
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import cli
from .frames import (
    MAX_FRAME_BYTES,
    MAX_TOTAL_BYTES,
    StreamRecord,
    StreamState,
    parse_frame,
)

#: How long :meth:`start` waits for proof the process is really running — a
#: ``system/init`` frame carrying the session id Cofferdam chose. A launch that
#: cannot show that within this window is a failed launch.
START_EVIDENCE_TIMEOUT_SECONDS = 90.0

#: The cancellation escalation. Each step is tried only after the previous one
#: was given this long and the process was re-verified as still ours.
#:
#: SIGTERM first because the CLI documents it: the in-progress turn is aborted,
#: ``SessionEnd`` hooks run, and it exits 143. Probe 2 measured 0.4 s. SIGKILL
#: exists for the case where it does not, and is not the first thing tried,
#: because a Claude process killed mid-write is one that did not get to finish
#: writing the file it was editing.
CANCEL_TERM_WAIT_SECONDS = 10.0
CANCEL_KILL_WAIT_SECONDS = 5.0

#: Exit code the CLI documents for a SIGTERM'd run.
EXIT_SIGTERM = 143


def read_start_time(pid: int) -> Optional[int]:
    """Field 22 of ``/proc/<pid>/stat``: the process start time, in clock ticks.

    Parsed by splitting on the **last** ``)`` rather than on whitespace. Field 2
    is the executable name in parentheses and may itself contain spaces and
    parentheses, so a naive split lands on the wrong field for any program with
    a space in its name — which is the sort of bug that only shows up on the one
    machine where it matters.
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


class ClaudeRun:
    """A single launched CLI process and everything known about it.

    One instance per task. It owns the subprocess, the reader thread, the
    bounded :class:`~.frames.StreamState`, and the lock that guards both.
    """

    def __init__(
        self,
        *,
        task_id: str,
        executable: Path,
        project_root: Path,
        session_id: Optional[str] = None,
        environment: Optional[Dict[str, str]] = None,
        popen: Optional[Callable] = None,
    ) -> None:
        self.task_id = task_id
        #: Distinguishes two runs of the same task, so a stale reader thread
        #: from an earlier run cannot be mistaken for the current one.
        self.run_id = uuid.uuid4().hex
        #: Chosen here, by the server. There is no parameter on any public
        #: method, route or model that lets a client supply this value, and the
        #: adapter verifies that the CLI reports back the same one.
        self.session_id = session_id or str(uuid.uuid4())
        self.executable = executable
        self.project_root = project_root
        self._environment = environment
        self._popen = popen or subprocess.Popen

        self.state = StreamState()
        self.lock = threading.RLock()
        self.process = None
        self.pid: Optional[int] = None
        self.start_time: Optional[int] = None
        self.pgid: Optional[int] = None
        self.exit_code: Optional[int] = None
        self.launch_error: Optional[str] = None
        self.stream_finished = threading.Event()
        self._turn_boundary = threading.Event()
        self._session_ready = threading.Event()
        self._reader: Optional[threading.Thread] = None
        self._stdin_closed = False
        self.cancel_requested = False
        self.signals_sent: List[str] = []
        #: The turn waiting to be sent, held only so it can be written to stdin
        #: by :meth:`send_turn`. It exists as an attribute rather than a local
        #: so that "does task content ever reach the command line" is a question
        #: a mutation test can actually ask — the mutation appends this to the
        #: argv, and `test_the_launched_argv_is_exactly_the_template` fails.
        self.pending_prompt: str = ""


    # -- launching -----------------------------------------------------------

    def start(self, first_turn: str) -> bool:
        """Launch, deliver the first turn, and wait for evidence it is running.

        Returns ``True`` only when a ``system/init`` frame arrived carrying the
        session id Cofferdam chose. Everything else — including a process that
        started and then said nothing — is a failed launch, because a task
        reported as ``running`` on the strength of a successful ``fork`` would
        be a claim about work that may never have begun.

        **The first turn is sent before the wait, and that ordering is the
        whole method.** The installed CLI does not emit ``system/init`` when it
        starts; it emits it when it receives its first user message. An earlier
        version of this method launched, waited for init, and only then sent the
        prompt — a deadlock that resolved ninety seconds later as "started but
        never reported a ready session".

        Every test passed, because the fake CLI in ``tests/_claude_doubles.py``
        emitted init eagerly and so encoded the assumption rather than the
        behaviour. Running against the real binary is what found it, and the
        fake now waits for stdin exactly as the real one does. It is the reason
        this milestone was told to verify the installed CLI instead of
        remembering it.
        """
        argv = cli.build_argv(self.executable, self.session_id)
        env = (
            cli.build_environment()
            if self._environment is None
            else dict(self._environment)
        )
        try:
            self.process = self._popen(
                argv,
                cwd=str(self.project_root),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                # No shell, ever. The argv is a list of strings built entirely
                # in `cli.build_argv`, and shell=False means no element of it
                # is ever interpreted as syntax by anything.
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                # Its own process group, which is what makes cancellation
                # targetable: the signal goes to a group Cofferdam created for
                # this task and to nothing else. It also detaches the child from
                # the daemon's controlling terminal, so a Ctrl-C reaching the
                # daemon cannot reach a task.
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            self.launch_error = type(exc).__name__
            return False

        self.pid = self.process.pid
        self.start_time = read_start_time(self.pid)
        try:
            self.pgid = os.getpgid(self.pid)
        except OSError:
            self.pgid = self.pid

        self._reader = threading.Thread(
            target=self._read_stream,
            name="claude-code-" + self.run_id[:8],
            daemon=True,
        )
        self._reader.start()

        # Before the wait. See the docstring.
        if not self.send_turn(first_turn):
            self.launch_error = "prompt_not_delivered"
            self.stop(reason="launch_failed")
            return False

        if not self._session_ready.wait(START_EVIDENCE_TIMEOUT_SECONDS):
            self.launch_error = "no_session_evidence"
            self.stop(reason="launch_failed")
            return False
        with self.lock:
            reported = self.state.session_id
        if reported != self.session_id:
            # The process is running something, but not the session this task
            # asked for. Refusing is the only safe answer: adopting it would
            # mean a task attached to a conversation nobody can account for.
            self.launch_error = "session_mismatch"
            self.stop(reason="session_mismatch")
            return False
        return True

    # -- identity ------------------------------------------------------------

    def still_ours(self) -> bool:
        """Whether the pid still refers to the process this object launched.

        Every clause matters. ``exists`` alone would be satisfied by a recycled
        pid; the start time is what makes the answer specific to one exec. The
        process-group check is what makes a signal targetable at a group
        Cofferdam created rather than at whatever group that pid now belongs to.
        """
        if self.pid is None or self.start_time is None:
            return False
        if self.exit_code is not None:
            return False
        current = read_start_time(self.pid)
        if current is None or current != self.start_time:
            return False
        try:
            if os.getpgid(self.pid) != self.pgid:
                return False
        except OSError:
            return False
        return True

    def poll(self) -> Optional[int]:
        if self.process is None:
            return self.exit_code
        code = self.process.poll()
        if code is not None:
            self.exit_code = code
        return code

    # -- talking to it -------------------------------------------------------

    def send_turn(self, text: str) -> bool:
        """Deliver one user turn on stdin as a single JSON line.

        This is the documented content channel for ``--input-format
        stream-json``, and it is the *only* way task content reaches the CLI.
        The text is not in argv, not in the environment, not in a file, not in a
        URL and not in the process title.

        ``json.dumps`` with ``ensure_ascii=True`` escapes every non-ASCII
        character to a ``\\uXXXX`` sequence, so a Turkish prompt survives
        regardless of what the child's stdout encoding turns out to be — the
        bytes on the pipe are ASCII and the CLI's own JSON reader reconstitutes
        the characters.
        """
        import json

        if self.process is None or self.process.stdin is None or self._stdin_closed:
            return False
        self.pending_prompt = text
        message = {
            "type": "user",
            "message": {"role": "user", "content": text},
            "parent_tool_use_id": None,
        }
        try:
            self._turn_boundary.clear()
            self.process.stdin.write(json.dumps(message, ensure_ascii=True) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError):
            return False
        return True

    def turn_in_progress(self) -> bool:
        """Whether a turn has been sent whose ``result`` frame has not arrived."""
        return not self._turn_boundary.is_set() and self.poll() is None

    def close_input(self) -> None:
        """Close stdin, which is how the CLI is told there are no more turns."""
        if self.process is None or self._stdin_closed:
            return
        self._stdin_closed = True
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except OSError:
            pass

    # -- reading -------------------------------------------------------------

    def _read_stream(self) -> None:
        """Consume stdout incrementally, bounded in both directions.

        Reads character by character into a line buffer rather than using
        ``for line in stdout``, because the iterator form will happily
        accumulate an unbounded line before yielding it. Here a line that
        exceeds :data:`~.frames.MAX_FRAME_BYTES` stops being buffered and the
        rest of it is drained to the newline — the frame is refused, the stream
        stays synchronised, and memory does not move.
        """
        stream = self.process.stdout if self.process is not None else None
        if stream is None:
            self.stream_finished.set()
            self._session_ready.set()
            return
        buffer: List[str] = []
        length = 0
        overflowing = False
        try:
            while True:
                chunk = stream.read(1)
                if not chunk:
                    break
                with self.lock:
                    self.state.bytes_seen += len(chunk.encode("utf-8", "replace"))
                    if self.state.bytes_seen > MAX_TOTAL_BYTES:
                        self.state.truncated = True
                        break
                if chunk != "\n":
                    if overflowing:
                        continue
                    length += 1
                    if length > MAX_FRAME_BYTES:
                        overflowing = True
                        buffer = []
                        with self.lock:
                            self.state.oversized_frames += 1
                        continue
                    buffer.append(chunk)
                    continue
                line = "".join(buffer)
                buffer = []
                length = 0
                was_overflowing = overflowing
                overflowing = False
                if was_overflowing:
                    continue
                self._consume(line)
        except (OSError, ValueError):
            pass
        finally:
            self.poll()
            self.stream_finished.set()
            # Unblock anything waiting on evidence or on a turn: the stream is
            # over, so nothing more is coming and a waiter must not hang.
            self._session_ready.set()
            self._turn_boundary.set()

    def _consume(self, line: str) -> None:
        with self.lock:
            records = parse_frame(line, self.state)
            for record in records:
                self._absorb(record)
        from .frames import KIND_SESSION_READY, KIND_TURN_RESULT

        for record in records:
            if record.kind == KIND_SESSION_READY:
                self._session_ready.set()
            elif record.kind == KIND_TURN_RESULT:
                self._turn_boundary.set()

    def _absorb(self, record: StreamRecord) -> None:
        """Fold one record into the bounded summary. Called with the lock held."""
        from .frames import (
            KIND_ASSISTANT_TEXT,
            KIND_RETRY,
            KIND_THINKING_PROGRESS,
            KIND_TOOL_ACTIVITY,
            KIND_TOOL_RESULT,
            KIND_TURN_RESULT,
        )

        self.state.add(record)
        if record.kind in (KIND_TOOL_ACTIVITY, KIND_TOOL_RESULT, KIND_RETRY):
            self.state.latest_activity = record.text
        elif record.kind == KIND_THINKING_PROGRESS:
            if not self.state.latest_activity:
                self.state.latest_activity = record.text
        elif record.kind == KIND_ASSISTANT_TEXT:
            self.state.latest_output = record.text
            self.state.latest_activity = "Claude is writing a reply."
        elif record.kind == KIND_TURN_RESULT:
            self.state.latest_activity = None

    def wait_for_turn(self, timeout: float) -> bool:
        """Block until the current turn produces a result, or time out."""
        return self._turn_boundary.wait(timeout)

    # -- stopping ------------------------------------------------------------

    def stop(self, *, reason: str = "cancel") -> Dict[str, object]:
        """Bounded escalation, re-verifying identity before every signal.

        Returns a small record of what was actually done, which becomes the
        cancellation evidence on the task. It reports what happened rather than
        what was intended: a process that had already exited produces
        ``already_exited``, not a fabricated "terminated".
        """
        self.cancel_requested = True
        outcome: Dict[str, object] = {"reason": reason, "signals": []}

        if self.process is None:
            outcome["result"] = "never_started"
            return outcome

        self.close_input()
        if self.poll() is not None:
            outcome["result"] = "already_exited"
            outcome["exit_code"] = self.exit_code
            return outcome

        if not self.still_ours():
            # The pid is gone or is no longer the process launched here. Sending
            # a signal now would be sending it to somebody else's program, so
            # nothing is sent and the task says so.
            outcome["result"] = "identity_lost"
            return outcome

        for signal_name, signal_number, wait in (
            ("SIGTERM", signal.SIGTERM, CANCEL_TERM_WAIT_SECONDS),
            ("SIGKILL", signal.SIGKILL, CANCEL_KILL_WAIT_SECONDS),
        ):
            # Re-verified before *each* signal, not once at the top. Between
            # SIGTERM and SIGKILL the process may exit and its pid be reused,
            # and a SIGKILL sent on the strength of a check made ten seconds ago
            # is a SIGKILL sent to whatever holds that pid now.
            if not self.still_ours():
                break
            try:
                os.killpg(self.pgid, signal_number)
                self.signals_sent.append(signal_name)
                outcome["signals"].append(signal_name)  # type: ignore[union-attr]
            except OSError as exc:
                if exc.errno == errno.ESRCH:
                    break
                outcome["result"] = "signal_failed"
                return outcome
            if self._wait_for_exit(wait):
                break

        self.poll()
        if self.exit_code is None and self.still_ours():
            outcome["result"] = "still_running"
        else:
            outcome["result"] = "stopped"
            outcome["exit_code"] = self.exit_code
        return outcome

    def _wait_for_exit(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.poll() is not None:
                return True
            time.sleep(0.05)
        return self.poll() is not None

    def reap(self, timeout: float = 5.0) -> Optional[int]:
        """Collect the exit status so the child does not linger as a zombie."""
        if self.process is None:
            return self.exit_code
        try:
            self.exit_code = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        except OSError:
            return self.exit_code
        return self.exit_code


__all__ = [
    "CANCEL_KILL_WAIT_SECONDS",
    "CANCEL_TERM_WAIT_SECONDS",
    "EXIT_SIGTERM",
    "START_EVIDENCE_TIMEOUT_SECONDS",
    "ClaudeRun",
    "read_start_time",
]
