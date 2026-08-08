"""The systemd user manager, as this package is willing to speak to it.

Three verbs — ``show``, ``start``, ``stop`` — against one unit template, always
with ``--user``, always as a fixed argv list, never through a shell. The only
variable element in any command this module builds is a unit name produced by
:func:`.units.unit_name` from an id the project registry already validated.

Why ``--user`` is a constant and not a parameter
------------------------------------------------

A scope argument would be the single most valuable field an attacker could
reach. ``systemctl --system start anything.service`` is a different security
domain, gated by polkit, and a supervisor that could be talked into it would
have turned a per-project convenience into a privilege boundary. So the flag is
a literal in the argv builders below, there is no parameter that could change
it, and a test asserts every command this module can produce contains it.

Why the runner is injected
--------------------------

So the tests can prove the argv without a systemd on the other end. The default
is :func:`..adapters.base.run_fixed`, the same vetted helper the desktop
adapters use — which is also why this module contains no ``subprocess`` import
of its own, and why the repository's "subprocess lives only in adapter code"
structural test keeps passing without an exemption for this package.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional, Sequence, Tuple

from ..adapters.base import run_fixed
from ..errors import AdapterError
from .errors import BackendRefused, BackendUnavailable
from .model import (
    STATE_UNKNOWN,
    NativeSessionStatus,
    map_active_state,
)
from .units import unit_name

SYSTEMCTL = "systemctl"

#: Read-only questions are fast or they are broken. Five seconds is long enough
#: for a loaded user manager and short enough that a status screen never hangs.
QUERY_TIMEOUT_SECONDS = 5

#: ``start`` is a ``Type=simple`` transition, so systemd returns as soon as the
#: job is queued and the fork succeeds — it does not wait for the program to be
#: useful. Fifteen seconds is for a busy manager, not for Claude.
CONTROL_TIMEOUT_SECONDS = 15

#: ``stop`` is not the same shape, and treating it as if it were is what this
#: constant fixes.
#:
#: ``systemctl stop`` blocks until the job *completes*, and completing means the
#: unit's whole shutdown sequence has run. The M2H PR2 live validation measured
#: it: the CLI does not exit on SIGTERM, so the wrapper waits out its grace
#: period and then kills the process group, and the call returns after about
#: fifteen seconds — right at the old shared timeout. Cofferdam therefore
#: answered HTTP 503 "the user service manager did not answer" for a stop that
#: had in fact worked, leaving the caller to believe a session was still up
#: while the unit sat inactive with status 0.
#:
#: So this bound is derived from the unit rather than guessed: the shipped
#: template sets ``TimeoutStopSec=30``, which is the longest systemd will let a
#: shutdown take before the manager kills the unit's cgroup itself. Fifteen
#: seconds of margin on top covers a loaded manager. Past this, "did not answer"
#: is finally the truth rather than impatience.
STOP_TIMEOUT_SECONDS = 45

#: Exactly the properties needed to answer "is it up, since when, and is the
#: template even installed". Nothing here reads a log, and there is no journal
#: call anywhere in this package.
SHOW_PROPERTIES: Tuple[str, ...] = (
    "LoadState",
    "ActiveState",
    "SubState",
    "ActiveEnterTimestamp",
)

#: How much of a failed command's stderr is allowed into a status object. Long
#: enough for "Unit cofferdam-rc@x.service not found.", short enough that a
#: manager having a bad day cannot push a wall of text into a PWA card or a log
#: line. Journal output is never read at all — this is only what ``systemctl``
#: itself printed.
MAX_ERROR_CHARS = 200

#: A runner takes a fixed argv and a timeout and returns something with
#: ``returncode``, ``stdout`` and ``stderr``. Injected so tests need no systemd.
CommandRunner = Callable[..., object]


def _redact(raw: object) -> Optional[str]:
    """One short, single-line, control-character-free sentence, or ``None``.

    Applied to everything that comes back from ``systemctl`` before it can reach
    a status object. Control characters are stripped rather than escaped so that
    nothing downstream can be talked into interpreting them, and the result is
    collapsed to a single line so a multi-line failure cannot forge extra
    entries in a log that is read line by line.
    """
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", "replace")
    elif isinstance(raw, str):
        text = raw
    else:
        return None
    cleaned = " ".join(
        "".join(
            character
            for character in text
            if not (ord(character) < 0x20 or ord(character) == 0x7F)
        ).split()
    )
    if not cleaned:
        return None
    if len(cleaned) > MAX_ERROR_CHARS:
        return cleaned[: MAX_ERROR_CHARS - 1] + "…"
    return cleaned


def _decode(raw: object) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        return raw
    return ""


def show_argv(unit: str) -> Sequence[str]:
    """The exact read-only status command, fixed but for the unit name."""
    return [SYSTEMCTL, "--user", "show", unit] + [
        "--property=" + name for name in SHOW_PROPERTIES
    ]


def start_argv(unit: str) -> Sequence[str]:
    return [SYSTEMCTL, "--user", "start", unit]


def stop_argv(unit: str) -> Sequence[str]:
    return [SYSTEMCTL, "--user", "stop", unit]


def _run(runner: CommandRunner, argv: Sequence[str], timeout: int):
    try:
        return runner(list(argv), timeout=timeout)
    except AdapterError as exc:
        # run_fixed already turned "not installed", "timed out" and "could not
        # run" into this one type. The detail is Cofferdam's sentence, not the
        # exception's string, which could carry a path.
        raise BackendUnavailable() from exc


def parse_show(stdout: object) -> Dict[str, str]:
    """``Key=Value`` lines into a mapping. Unparseable lines are dropped.

    Split on the first ``=`` only: ``ActiveEnterTimestamp`` values contain no
    ``=`` today, but a property that did would otherwise silently lose its tail.
    """
    values: Dict[str, str] = {}
    for line in _decode(stdout).splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip():
            values[key.strip()] = value.strip()
    return values


class SystemdUserBackend:
    """Start, stop and inspect one Cofferdam-owned user unit per project."""

    def __init__(
        self,
        runner: Optional[CommandRunner] = None,
        *,
        query_timeout: int = QUERY_TIMEOUT_SECONDS,
        control_timeout: int = CONTROL_TIMEOUT_SECONDS,
        stop_timeout: int = STOP_TIMEOUT_SECONDS,
    ) -> None:
        self._runner = runner if runner is not None else run_fixed
        self._query_timeout = query_timeout
        self._control_timeout = control_timeout
        self._stop_timeout = stop_timeout

    # -- reading -------------------------------------------------------------

    def status(self, project_id: str) -> NativeSessionStatus:
        """What systemd currently says about that project's host.

        Never raises for a *reachable* manager that reports something odd: an
        unrecognised or missing ``ActiveState`` becomes
        :data:`~.model.STATE_UNKNOWN` with a short error, because a status call
        that throws is a status screen that shows nothing at all.
        """
        unit = unit_name(project_id)
        completed = _run(self._runner, show_argv(unit), self._query_timeout)

        if getattr(completed, "returncode", 1) != 0:
            return NativeSessionStatus(
                project_id=project_id,
                unit=unit,
                state=STATE_UNKNOWN,
                error=_redact(getattr(completed, "stderr", None))
                or "the user service manager did not answer",
            )

        values = parse_show(getattr(completed, "stdout", b""))
        load_state = values.get("LoadState")

        # A template that has never been installed answers ActiveState=inactive
        # with LoadState=not-found, and reporting that as "stopped" would be a
        # lie of exactly the kind this package exists to avoid: it reads as "the
        # host is down" when the truth is "there is no host to bring up".
        if load_state != "loaded":
            return NativeSessionStatus(
                project_id=project_id,
                unit=unit,
                state=STATE_UNKNOWN,
                active_state=values.get("ActiveState"),
                sub_state=values.get("SubState"),
                error="the Remote Control unit template is not installed on this workstation",
            )

        active_state = values.get("ActiveState")
        state = map_active_state(active_state)
        started_at = values.get("ActiveEnterTimestamp") or None

        return NativeSessionStatus(
            project_id=project_id,
            unit=unit,
            state=state,
            active_state=active_state,
            sub_state=values.get("SubState"),
            started_at=started_at,
            error=(
                None
                if state != STATE_UNKNOWN
                else "the user service manager reported a state this build does not recognise"
            ),
        )

    # -- writing -------------------------------------------------------------

    def start(self, project_id: str) -> None:
        """Queue a start for that project's unit. Raises on refusal."""
        self._control(start_argv(unit_name(project_id)))

    def stop(self, project_id: str) -> None:
        """Stop that project's unit, waiting out the real shutdown.

        The longer bound is the whole point — see
        :data:`STOP_TIMEOUT_SECONDS`. ``stop`` blocks until the unit is down,
        and the child this package supervises does not exit on SIGTERM.
        """
        self._control(stop_argv(unit_name(project_id)), timeout=self._stop_timeout)

    def _control(self, argv: Sequence[str], *, timeout: Optional[int] = None) -> None:
        completed = _run(
            self._runner, argv, self._control_timeout if timeout is None else timeout
        )
        if getattr(completed, "returncode", 1) != 0:
            raise BackendRefused(_redact(getattr(completed, "stderr", None)))


__all__ = [
    "CONTROL_TIMEOUT_SECONDS",
    "STOP_TIMEOUT_SECONDS",
    "MAX_ERROR_CHARS",
    "QUERY_TIMEOUT_SECONDS",
    "SHOW_PROPERTIES",
    "SYSTEMCTL",
    "CommandRunner",
    "SystemdUserBackend",
    "parse_show",
    "show_argv",
    "start_argv",
    "stop_argv",
]
