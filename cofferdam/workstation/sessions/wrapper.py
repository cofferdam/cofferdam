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

import errno
import os
import pty
import select
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

#: Rolling window used for marker recognition, in characters. Large enough to
#: hold a prompt split across two terminal writes, far too small to accumulate
#: anything worth calling a transcript.
MARKER_TAIL_CHARS = 512

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


#: Whether the consent-prompt marker below was observed in real process output.
#:
#: ``True``, unlike :data:`AUTH_FORMAT_CONFIRMED`, and the difference is
#: evidence. The M2H PR2 PTY spike ran the fixed argv against the real CLI on
#: this workstation and the host stopped on a question, quoted verbatim in
#: :data:`CONSENT_MARKERS`. That observation is what this flag records.
CONSENT_FORMAT_CONFIRMED = True

#: The observed marker, lowercased for comparison. Product UI text, not session
#: content: it is the same sentence for every user and carries nothing read from
#: a conversation.
#:
#: Matched on the question alone rather than the whole line because the line
#: arrives from a terminal and may carry a cursor-positioning prefix or a
#: trailing repaint.
CONSENT_MARKERS = ("enable remote control?",)


def detect_consent_required(text: object) -> bool:
    """Whether that output is the CLI's one-time Remote Control consent prompt.

    **The finding this exists for.** ``claude remote-control`` does not start a
    session unattended on a workstation that has not already enabled the
    feature. It renders a short explanation and then asks

        Enable Remote Control? (y/n)

    and waits. Cofferdam gives the child ``stdin=/dev/null`` on purpose, so the
    question can never be answered from here — the host sits at the prompt for
    as long as the unit is up, publishes no session, and prints no URL.

    Detecting it is the difference between two very different answers to "is
    Remote Control running?". systemd says the unit is active, and it is; but
    nothing is reachable from a phone and nothing ever will be. Reporting
    ``running`` for that is exactly the confidently-wrong state this milestone
    exists to avoid, so the wrapper reports the question instead and lets the
    person answer it at the machine, once.

    Answering it *for* the user is not Cofferdam's decision to make: enabling
    Remote Control is a consent step about their account, and a daemon that
    types ``y`` at a consent prompt has removed the consent.
    """
    if not CONSENT_FORMAT_CONFIRMED:  # pragma: no cover - flag is True in this build
        return False
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if not isinstance(text, str):
        return False
    lowered = links.strip_ansi(text).lower()
    return any(marker in lowered for marker in CONSENT_MARKERS)


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
        on_consent_required: Optional[Callable[[], None]] = None,
        log: Optional[Callable[[str], None]] = None,
        popen: Optional[Callable] = None,
    ) -> None:
        self._argv = list(argv)
        self._cwd = cwd
        self._on_link = on_link
        self._on_auth_required = on_auth_required
        self._on_consent_required = on_consent_required
        self._log = log if log is not None else _default_log
        self._popen = popen if popen is not None else subprocess.Popen
        self._process = None
        self._scanner = links.LinkScanner()
        self._retained: List[str] = []
        self._auth_reported = False
        self._consent_reported = False
        self._marker_tail = ""
        self._stopping = threading.Event()
        self._escalation: Optional[threading.Timer] = None

    # -- output --------------------------------------------------------------

    @property
    def retained(self) -> List[str]:
        """The bounded, already-redacted tail. Safe to log or attach to state."""
        return list(self._retained)

    def _check_markers(self, text: str) -> None:
        """Report the consent prompt and the auth signal, each at most once.

        A rolling tail rather than the line alone, because the consent prompt is
        a *prompt*: it has no trailing newline, and a terminal can deliver it
        split across two reads. Matching only within one delivered piece would
        miss it exactly when the child is slowest — which is when it matters.

        The tail is bounded to :data:`MARKER_TAIL_CHARS` and holds nothing but
        the most recent characters of the child's own display, which is already
        being retained in redacted form a few lines below.
        """
        self._marker_tail = (self._marker_tail + text)[-MARKER_TAIL_CHARS:]

        if not self._consent_reported and detect_consent_required(self._marker_tail):
            self._consent_reported = True
            if self._on_consent_required is not None:
                self._on_consent_required()
            self._log(
                "the host is waiting for Remote Control to be enabled on this "
                "machine; it cannot be answered from here"
            )

        if not self._auth_reported and detect_auth_required(text):
            self._auth_reported = True
            if self._on_auth_required is not None:
                self._on_auth_required()
            self._log("the host reported that authentication is required")

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

        self._check_markers(line)

        safe = links.redact(line)
        if safe:
            self._retained.append(safe)
            if len(self._retained) > MAX_RETAINED_LINES:
                del self._retained[0 : len(self._retained) - MAX_RETAINED_LINES]

    def _open_terminal(self):
        """A PTY pair, or ``(None, None)`` when the platform has none.

        Falling back to a pipe rather than refusing to start: capturing the link
        is valuable, but it is not worth being the reason a host cannot run at
        all. The consequence — no link on that platform — is reported truthfully
        by ``url_available`` staying false.
        """
        try:
            return pty.openpty()
        except (OSError, AttributeError):  # pragma: no cover - platform dependent
            self._log("no pseudo-terminal is available; the session link cannot be captured")
            return None, None

    def _pump_terminal(self, master) -> None:
        """Read the PTY master incrementally until the child closes it.

        Bounded reads on a bounded buffer. A terminal-aware child repaints, so
        this stream carries far more bytes than a pipe would; none of it is kept
        beyond the redacted tail, and nothing is written to disk.

        ``EIO`` on a master whose slave has closed is the normal end-of-stream on
        Linux, not a failure — treating it as an error would log a scary line
        every time a host exits cleanly.
        """
        if master is None:
            return
        try:
            while True:
                try:
                    ready, _, _ = select.select([master], [], [], 1.0)
                except (OSError, ValueError):
                    return
                if not ready:
                    if self._process is not None and self._process.poll() is not None:
                        return
                    continue
                try:
                    chunk = os.read(master, 4096)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        return
                    return
                if not chunk:
                    return
                self._absorb_chunk(chunk.decode("utf-8", "replace"))
        finally:
            _close(master)
            trailing = self._scanner.finish()
            if trailing is not None and self._on_link is not None:
                self._on_link(trailing)
                self._log("session link captured")

    def _absorb_chunk(self, chunk: str) -> None:
        """Feed a raw terminal chunk to the scanner, line by line for retention.

        A PTY delivers ``\r\n``; normalising here keeps the line-completeness
        rule in :class:`~.links.LinkScanner` working, which is what stops a
        half-arrived URL being treated as a whole one.
        """
        normalised = chunk.replace("\r\n", "\n").replace("\r", "\n")
        found = self._scanner.feed(normalised)
        if found is not None and self._on_link is not None:
            self._on_link(found)
            self._log("session link captured")

        self._check_markers(normalised)

        for line in normalised.split("\n"):
            if not line:
                continue
            safe = links.redact(links.strip_ansi(line))[:MAX_LINE_CHARS]
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
        """Start the child on a pseudo-terminal, watch it, return its status.

        **Why a PTY and not a pipe.** The M2H PR2 pipe-backed spike started a
        real host successfully and captured nothing. Remote Control paints an
        interactive display, and like most such programs it decides what to
        render by asking whether its output is a terminal; a pipe answers no.
        On a PTY the same command renders properly — colour, repaints, its whole
        startup screen.

        So the child gets a Cofferdam-owned PTY. This is the *production* I/O
        shape, not a validation-only one: a capture path that only works under a
        test harness is a capture path nobody has tested.

        **What the PTY then revealed, which matters more.** Rendering is not the
        only thing that was missing. The CLI asks for consent before it enables
        Remote Control at all, and waits — see
        :func:`detect_consent_required`. So the terminal is necessary for
        capture and is not sufficient for it, and this build reports that gap
        rather than presenting a healthy-looking unit as a reachable session.

        What does **not** change: ``stdin`` stays ``/dev/null``, so the terminal
        is one-way and there is no channel through which a prompt could reach
        the session. The child still gets its own session and process group, the
        argv is still fixed, and no shell is involved.

        **The fallback is a pipe, never inheritance.** If this platform has no
        pseudo-terminal, the child gets ``stdout=PIPE`` and the older
        line-reader. What it must never get is ``stdout=None``: that means
        *inherit*, and the parent's stdout under the shipped unit is journald —
        so a failed ``openpty`` would quietly turn every byte the child paints
        into unredacted journal entries. Losing the link on an exotic platform
        is acceptable; writing raw child output to disk is not.
        """
        master, slave = self._open_terminal()
        on_terminal = master is not None

        try:
            self._process = self._popen(  # noqa: S603 - fixed argv, shell is never used
                self._argv,
                cwd=self._cwd,
                stdout=slave if on_terminal else subprocess.PIPE,
                stderr=slave if on_terminal else subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            # The parent must drop its copy of the slave, or reads on the master
            # never see EOF when the child exits and the reader thread hangs
            # forever on a terminal nothing is writing to.
            if slave is not None:
                _close(slave)

        previous = self._install_signal_handlers()
        if on_terminal:
            reader = threading.Thread(
                target=self._pump_terminal, args=(master,), daemon=True
            )
        else:
            reader = threading.Thread(
                target=self._pump, args=(self._process.stdout,), daemon=True
            )
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


def _close(descriptor) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


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
    "CONSENT_FORMAT_CONFIRMED",
    "CONSENT_MARKERS",
    "FORWARDED_SIGNALS",
    "MARKER_TAIL_CHARS",
    "MAX_LINE_CHARS",
    "MAX_RETAINED_LINES",
    "TERM_GRACE_SECONDS",
    "SupervisedHost",
    "detect_auth_required",
    "detect_consent_required",
]
