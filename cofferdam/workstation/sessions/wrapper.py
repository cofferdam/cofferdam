"""Supervising the fixed Claude process so its session link can be captured.

PR1's entry point used ``execv`` and replaced itself with Claude, which was the
right shape for a foundation that only had to start a process. Capturing the
Remote Control link requires *reading* what the child prints, and a process that
has replaced itself cannot read anything. So this module supervises instead, and
pays back the two things ``execv`` gave away for free:

**Signals.** ``SIGTERM`` and ``SIGINT`` are forwarded to the child's process
group, not just the child, so anything it spawned dies with it. systemd's
``KillMode=mixed`` then has nothing left to clean up, and the unit's stop
timeout is a backstop rather than the mechanism.

**Exit status.** The child's exit code is returned unchanged, and a child killed
by a signal is reported as ``128 + signal`` the way a shell would — so
``Restart=on-failure`` and ``systemctl status`` see the truth rather than this
wrapper's opinion of it.

What is read, and what is done with it
--------------------------------------

Output is read to find one thing: the session link. It is scanned by
:class:`~.links.LinkScanner`, written to :mod:`.state`, and **redacted before
anything is logged**. Nothing is parsed for meaning, nothing is stored beyond
the link, and there is no path here that could retain conversation content —
Remote Control prints operational startup output, and this wrapper is attached
to that stream for exactly as long as it takes to find a URL in it.

Buffering is bounded in both directions. A retained tail exists so a failed
start can say *why*; it is capped at a few dozen redacted lines, because the
alternative is an unbounded buffer fed by a process that may run for hours.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from typing import Callable, List, Optional, Sequence

from . import links

#: How many redacted output lines are kept for an error summary.
MAX_RETAINED_LINES = 40

#: Hard cap on a single line. A child that prints a megabyte without a newline
#: does not get to allocate it here.
MAX_LINE_CHARS = 4096

#: How long a forwarded SIGTERM is given before the process group is killed.
#: Shorter than the unit's TimeoutStopSec so this wrapper resolves shutdown
#: itself and systemd's timeout stays a backstop.
TERM_GRACE_SECONDS = 15.0

#: Signals forwarded to the child. SIGHUP is absent deliberately: a user unit is
#: not attached to a terminal, and systemd does not send it here.
FORWARDED_SIGNALS = (signal.SIGTERM, signal.SIGINT)

#: Whether an explicit authentication-required signal has been observed in real
#: process output. It has **not**. Until the live spike records one, this build
#: never reports ``auth_required`` — an unauthenticated host is indistinguishable
#: from a working one at this layer, and saying otherwise would be the exact
#: confident-wrong answer this milestone exists to avoid.
AUTH_FORMAT_CONFIRMED = False

#: Candidate markers, unused while the flag above is False. Present so the live
#: spike has something concrete to confirm or replace rather than starting from
#: a blank file.
AUTH_MARKER_CANDIDATES = ("logged in", "log in", "authenticate", "subscription")


def detect_auth_required(text: object) -> bool:
    """Whether that output explicitly says authentication is needed.

    Always ``False`` until :data:`AUTH_FORMAT_CONFIRMED`. The function exists so
    the call site is written and tested now, and flipping one constant after the
    live observation turns it on — rather than the state being invented from a
    substring that happens to appear in a help message.
    """
    if not AUTH_FORMAT_CONFIRMED:
        return False
    if isinstance(text, bytes):  # pragma: no cover - confirmed-only path
        text = text.decode("utf-8", "replace")
    if not isinstance(text, str):  # pragma: no cover - confirmed-only path
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in AUTH_MARKER_CANDIDATES)


class SupervisedHost:
    """One launched ``claude remote-control``, watched until it exits."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        on_link: Optional[Callable[[str], None]] = None,
        on_auth_required: Optional[Callable[[], None]] = None,
        log: Optional[Callable[[str], None]] = None,
        popen: Optional[Callable] = None,
    ) -> None:
        self._argv = list(argv)
        self._cwd = cwd
        self._on_link = on_link
        self._on_auth_required = on_auth_required
        self._log = log if log is not None else _default_log
        self._popen = popen if popen is not None else subprocess.Popen
        self._process = None
        self._scanner = links.LinkScanner()
        self._retained: List[str] = []
        self._auth_reported = False
        self._stopping = threading.Event()
        self._escalation: Optional[threading.Timer] = None

    # -- output --------------------------------------------------------------

    @property
    def retained(self) -> List[str]:
        """The bounded, already-redacted tail. Safe to log or attach to state."""
        return list(self._retained)

    def _absorb(self, line: str) -> None:
        """One line of child output: scan, redact, retain, log.

        Order matters. The link is extracted *first* and the redacted form is
        what everything downstream sees, so there is no window in which an
        unredacted line exists anywhere but this frame.
        """
        line = line[:MAX_LINE_CHARS]

        found = self._scanner.feed(line)
        if found is not None and self._on_link is not None:
            self._on_link(found)
            self._log("session link captured")

        if not self._auth_reported and detect_auth_required(line):
            self._auth_reported = True
            if self._on_auth_required is not None:
                self._on_auth_required()
            self._log("the host reported that authentication is required")

        safe = links.redact(line)
        if safe:
            self._retained.append(safe)
            if len(self._retained) > MAX_RETAINED_LINES:
                del self._retained[0 : len(self._retained) - MAX_RETAINED_LINES]

    def _pump(self, stream) -> None:
        try:
            for raw in iter(stream.readline, b""):
                self._absorb(raw.decode("utf-8", "replace"))
        except (OSError, ValueError):
            return
        else:
            # A stream that ends without a trailing newline still gets its last
            # line scanned — otherwise a link on the final line of a child that
            # exits immediately would be missed.
            trailing = self._scanner.finish()
            if trailing is not None and self._on_link is not None:
                self._on_link(trailing)
                self._log("session link captured")
        finally:
            try:
                stream.close()
            except OSError:
                pass

    # -- lifecycle -----------------------------------------------------------

    def run(self) -> int:
        """Start the child, watch it, and return its exit status.

        ``start_new_session=True`` puts the child in its own process group so a
        signal can reach everything it spawned. ``stderr`` is merged into
        ``stdout`` because the two are one operational narrative here and
        interleaving them in the order the child produced them is what makes a
        failed start readable.
        """
        self._process = self._popen(  # noqa: S603 - fixed argv, shell is never used
            self._argv,
            cwd=self._cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

        previous = self._install_signal_handlers()
        reader = threading.Thread(target=self._pump, args=(self._process.stdout,), daemon=True)
        reader.start()

        try:
            status = self._process.wait()
        finally:
            self._restore_signal_handlers(previous)
            if self._escalation is not None:
                self._escalation.cancel()
            reader.join(timeout=5.0)

        if self._stopping.is_set():
            # We were asked to stop and we did. Reporting the child's kill
            # status here would tell systemd that a deliberate `systemctl stop`
            # failed — and with Restart=on-failure, a lie in that direction is
            # one that restarts things nobody asked to restart.
            self._log("the Remote Control host stopped on request")
            return 0

        return _exit_status(status)

    def terminate(self) -> None:
        """Forward a stop to the whole process group, then escalate on a timer.

        SIGTERM to the group, and a background timer that escalates to SIGKILL
        only if the child is still there after the grace period. The group
        rather than the pid is the point: Remote Control spawns sessions, and
        terminating only the parent would leave them running with nothing
        supervising them.

        **This must not wait.** It is called from a signal handler, which runs
        on the main thread — the same thread already blocked in
        :meth:`run`'s ``wait()``. Calling ``wait()`` again from here is
        re-entrant on the same child and returns immediately rather than
        waiting, which made every deliberate stop escalate straight to SIGKILL:
        the unit then exited 137 and systemd recorded ``failed`` for what was a
        perfectly clean shutdown. Found by the M2H PR2 live spike, which is
        exactly the class of thing a unit test with a fake process cannot show.
        """
        if self._process is None or self._stopping.is_set():
            return
        self._stopping.set()

        group = _process_group(self._process)
        _signal_group(group, self._process, signal.SIGTERM)

        def escalate() -> None:
            if self._process is not None and self._process.poll() is None:
                self._log("the host did not stop in time; killing its process group")
                _signal_group(group, self._process, signal.SIGKILL)

        timer = threading.Timer(TERM_GRACE_SECONDS, escalate)
        timer.daemon = True
        timer.start()
        self._escalation = timer

    # -- signals -------------------------------------------------------------

    def _install_signal_handlers(self):
        previous = {}
        for number in FORWARDED_SIGNALS:
            try:
                previous[number] = signal.getsignal(number)
                signal.signal(number, self._handle_signal)
            except (ValueError, OSError):  # pragma: no cover - non-main thread
                continue
        return previous

    def _restore_signal_handlers(self, previous) -> None:
        for number, handler in previous.items():
            try:
                signal.signal(number, handler)
            except (ValueError, OSError):  # pragma: no cover - non-main thread
                continue

    def _handle_signal(self, _number, _frame) -> None:
        self.terminate()


def _process_group(process) -> Optional[int]:
    try:
        return os.getpgid(process.pid)
    except (OSError, AttributeError):
        return None


def _signal_group(group: Optional[int], process, number: int) -> None:
    """Signal the group when we have one, the process otherwise.

    Never a name match, never ``pkill``: the target is a group this wrapper
    created, or it is the child object itself, or nothing is signalled.
    """
    try:
        if group is not None:
            os.killpg(group, number)
        else:  # pragma: no cover - only when getpgid failed
            process.send_signal(number)
    except ProcessLookupError:
        return
    except OSError:
        return


def _exit_status(status: int) -> int:
    """A child killed by signal N reported as 128+N, like a shell."""
    if status is None:  # pragma: no cover - defensive
        return 1
    return 128 + (-status) if status < 0 else status


def _default_log(message: str) -> None:
    sys.stdout.write("cofferdam-rc: " + message + "\n")
    sys.stdout.flush()


__all__ = [
    "AUTH_FORMAT_CONFIRMED",
    "AUTH_MARKER_CANDIDATES",
    "FORWARDED_SIGNALS",
    "MAX_LINE_CHARS",
    "MAX_RETAINED_LINES",
    "TERM_GRACE_SECONDS",
    "SupervisedHost",
    "detect_auth_required",
]
