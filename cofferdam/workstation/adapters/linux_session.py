"""Launch GUI work into the user's real graphical session (Linux + systemd).

Why this module exists — an M1 Ubuntu finding
---------------------------------------------
The workstation runs as a systemd **user service** with ``NoNewPrivileges=yes``.
That flag makes the kernel drop file capabilities and setuid transitions across
``execve`` for the service *and for every process it forks*. Ubuntu ships
Firefox as a snap, and ``snap-confine`` carries permitted file capabilities
(``cap_sys_admin``, ``cap_dac_override``, …) that it needs to set confinement
up. Launching the browser as a direct child of the service therefore always
failed, immediately::

    snap-confine is packaged without necessary permissions and cannot continue
    required permitted capability cap_dac_override not found in current
    capabilities: cap_wake_alarm=i

The old adapter started the child and returned its PID without ever waiting, so
that failure was invisible and the action was recorded as *succeeded* while
nothing appeared on screen. ``xdg-open`` made it worse: it exits 0 whether or
not the browser it delegates to ever starts, so even checking an exit code
would not have caught it.

The fix is not to weaken the service's hardening. It is to stop launching GUI
programs as children of the service at all, and instead ask the **systemd user
manager** to start them as transient units. Three properties follow from that:

* the manager does not run under ``NoNewPrivileges``, so snap confinement works;
* the application lands in its own cgroup, so restarting or stopping Cofferdam
  no longer kills the user's browser along with it;
* the application inherits the manager's *current* environment, so a service
  started by lingering **before** anyone has logged in graphically still
  launches into the session that is created later.

Everything here runs fixed argv vectors through
:func:`~cofferdam.workstation.adapters.base.run_fixed`. No caller-supplied text
ever becomes a command, and no shell is involved.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple

from ..errors import AdapterError
from .base import run_fixed

SYSTEMCTL = "systemctl"
SYSTEMD_RUN = "systemd-run"

# systemd's own name for "a graphical session is up and usable". Using it (as
# opposed to sniffing our own environment) is what makes detection correct for a
# lingering service: our environment is frozen at start, this is live.
GRAPHICAL_TARGET = "graphical-session.target"

# Transient units are named with this prefix so stale failed ones can be swept.
UNIT_PREFIX = "cofferdam-app"

# How long a freshly started transient unit is watched before its outcome is
# judged. An exec/confinement failure surfaces in tens of milliseconds; this is
# long enough to catch it with margin, short enough that a phone request does
# not feel hung.
LAUNCH_SETTLE_SECONDS = 2.0
LAUNCH_POLL_SECONDS = 0.1

# Bounded timeouts for the short-lived control commands below.
QUERY_TIMEOUT_SECONDS = 5
LAUNCH_TIMEOUT_SECONDS = 15

_SHOW_PROPERTIES = ("ActiveState", "SubState", "ExecMainStatus", "ExecMainPID", "Result")


@dataclass(frozen=True)
class GraphicalSession:
    """Whether a usable desktop session exists *right now*.

    ``session_id`` is a generation marker for the current graphical session, not
    a logind session ID. It changes every time graphical-session.target is
    activated, so a value captured before a logout never matches the session
    that follows the next login. Carrying it from detection through to launch is
    what stops a queued or in-flight GUI action from being delivered into a
    different session than the one it was authorised against.

    ``environment`` is the user manager's live environment block as it was read
    during this detection — the session's own answer for DISPLAY,
    WAYLAND_DISPLAY, XAUTHORITY and friends. Adapters must take those values
    from here rather than from :data:`os.environ`, which for a service started
    by lingering was frozen before any session existed.
    """

    available: bool
    session_type: Optional[str] = None
    reason: Optional[str] = None
    session_id: Optional[str] = None
    environment: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionLaunch:
    """Observed outcome of starting a transient unit.

    ``state`` is ``"running"`` when the process was still alive at the end of
    the settle window — direct evidence it did not fail on startup — or
    ``"exited"`` when it finished cleanly and quickly, which for a browser
    normally means an existing instance took the request over. ``"exited"`` is
    *not* by itself a success; the caller has to corroborate it.
    """

    unit: str
    pid: Optional[int]
    state: str
    exit_status: Optional[int]


# ---------------------------------------------------------------------------
# session detection
# ---------------------------------------------------------------------------


def _unquote(value: str) -> str:
    """Undo the shell-style quoting ``show-environment`` uses for odd values."""
    if value.startswith("$'") and value.endswith("'") and len(value) >= 3:
        return value[2:-1]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def user_manager_environment() -> Dict[str, str]:
    """The systemd user manager's *live* environment block.

    This is what a transient unit will inherit, and it is refreshed by the
    desktop at graphical login — unlike ``os.environ`` here, which was fixed
    when the service started and may predate the session entirely.
    """
    try:
        completed = run_fixed([SYSTEMCTL, "--user", "show-environment"], timeout=QUERY_TIMEOUT_SECONDS)
    except AdapterError:
        return {}
    if completed.returncode != 0:
        return {}
    environment: Dict[str, str] = {}
    for line in completed.stdout.decode("utf-8", "replace").splitlines():
        key, separator, value = line.partition("=")
        key = key.strip()
        if separator and key:
            environment[key] = _unquote(value.strip())
    return environment


def _graphical_target_state() -> Tuple[bool, Optional[str]]:
    """Ask whether a graphical session is up, and which activation it is.

    This is a **read-only** query. It deliberately uses ``show``/``is-active``
    and never ``start``, ``isolate``, or a dependency edge: naming
    graphical-session.target in a unit's ``Wants=`` is what caused the M1.1
    login loop, because ``Wants=`` activates the target rather than waiting for
    it. Cofferdam follows this target; it never drives it.

    The second element is ``ActiveEnterTimestampMonotonic`` — systemd's own
    monotonic stamp for when the target last went active. It is a stable,
    free session generation marker: constant for the life of one graphical
    session, and different for the next one.
    """
    try:
        completed = run_fixed(
            [
                SYSTEMCTL,
                "--user",
                "show",
                GRAPHICAL_TARGET,
                "--property=ActiveState",
                "--property=ActiveEnterTimestampMonotonic",
            ],
            timeout=QUERY_TIMEOUT_SECONDS,
        )
    except AdapterError:
        return False, None
    values: Dict[str, str] = {}
    for line in completed.stdout.decode("utf-8", "replace").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    if values.get("ActiveState") != "active":
        return False, None
    stamp = values.get("ActiveEnterTimestampMonotonic") or None
    # "0" means "never activated", which would make a stale marker compare
    # equal to a fresh one. Treat it as no marker at all.
    if stamp in ("0", ""):
        stamp = None
    return True, stamp


def _display_endpoint(environment: Dict[str, str]) -> Tuple[bool, Optional[str]]:
    """Check that the compositor/X socket the session advertises really exists."""
    runtime_dir = environment.get("XDG_RUNTIME_DIR") or os.environ.get("XDG_RUNTIME_DIR")

    wayland_display = environment.get("WAYLAND_DISPLAY")
    if wayland_display:
        socket_path = Path(wayland_display)
        if not socket_path.is_absolute():
            if not runtime_dir:
                return False, "WAYLAND_DISPLAY is set but XDG_RUNTIME_DIR is not"
            socket_path = Path(runtime_dir) / wayland_display
        if socket_path.exists():
            return True, None
        return False, "the Wayland compositor socket named by WAYLAND_DISPLAY does not exist"

    display = environment.get("DISPLAY")
    if display:
        if display.startswith(":"):
            number = display[1:].split(".", 1)[0]
            if number.isdigit():
                if Path("/tmp/.X11-unix/X" + number).exists():
                    return True, None
                return False, "the X11 socket named by DISPLAY does not exist"
        # A host-qualified DISPLAY points at a remote X server we cannot stat.
        return True, None

    return False, "neither WAYLAND_DISPLAY nor DISPLAY is published by the user session"


def detect_graphical_session() -> GraphicalSession:
    """Report whether GUI actions can actually reach a desktop right now.

    Never raises: status has to stay serviceable on a host with no desktop at
    all, and a service that came up through lingering must be able to say "no
    session yet" rather than fail.
    """
    environment = user_manager_environment()
    session_type = environment.get("XDG_SESSION_TYPE") or os.environ.get("XDG_SESSION_TYPE")

    active, session_id = _graphical_target_state()
    if not active:
        # Nobody is logged in graphically. The user manager may still be holding
        # DISPLAY/WAYLAND_DISPLAY from a session that has since ended — under
        # lingering it is never torn down — so the target, not the environment,
        # is what decides.
        return GraphicalSession(
            available=False,
            session_type=session_type,
            reason="no graphical session is active on this host yet",
            environment=environment,
        )

    reachable, reason = _display_endpoint(environment)
    if not reachable:
        return GraphicalSession(
            available=False,
            session_type=session_type,
            reason=reason,
            environment=environment,
        )

    return GraphicalSession(
        available=True,
        session_type=session_type,
        session_id=session_id,
        environment=environment,
    )


# ---------------------------------------------------------------------------
# launching
# ---------------------------------------------------------------------------


def _to_int(value: Optional[str]) -> Optional[int]:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _show_unit(unit: str) -> Dict[str, str]:
    properties = ["--property=" + name for name in _SHOW_PROPERTIES]
    try:
        completed = run_fixed(
            [SYSTEMCTL, "--user", "show", unit + ".service"] + properties, timeout=QUERY_TIMEOUT_SECONDS
        )
    except AdapterError:
        return {}
    values: Dict[str, str] = {}
    for line in completed.stdout.decode("utf-8", "replace").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values


def _reset_failed(pattern: str) -> None:
    """Best-effort cleanup so failed transient units do not pile up."""
    try:
        run_fixed([SYSTEMCTL, "--user", "reset-failed", pattern], timeout=QUERY_TIMEOUT_SECONDS)
    except AdapterError:
        pass


def _failure_detail(properties: Dict[str, str]) -> str:
    status = properties.get("ExecMainStatus") or "?"
    result = properties.get("Result") or "failure"
    return "the launcher exited with status " + status + " (systemd result: " + result + ")"


def launch_in_session(
    argv: Sequence[str],
    *,
    description: str,
    settle_seconds: float = LAUNCH_SETTLE_SECONDS,
    expect_session: Optional[str] = None,
) -> SessionLaunch:
    """Start ``argv`` as a transient unit of the user manager and watch it.

    ``argv`` is built entirely by the adapter from its own fixed tables plus, at
    most, a value the service already validated (an http/https URL). It is
    passed as separate argument vector elements, never as a string, and never
    through a shell.

    ``expect_session`` is the :attr:`GraphicalSession.session_id` the caller
    validated against. It is re-checked here, immediately before handing over,
    so an action authorised against one session cannot be delivered into a
    different one if the user logged out in between.

    Raises :class:`AdapterError` if the session changed, if the launch could not
    be handed over, or if the process died during the settle window.
    """
    if expect_session is not None:
        active, current = _graphical_target_state()
        if not active:
            raise AdapterError(
                "the graphical session ended before the application could be started",
                "graphical-session.target is no longer active",
            )
        if current != expect_session:
            raise AdapterError(
                "the graphical session changed before the application could be started",
                "the request was prepared for an earlier session; retry in the current one",
            )

    unit = UNIT_PREFIX + "-" + uuid.uuid4().hex[:12]

    # Sweep any earlier failed unit of ours before adding another.
    _reset_failed(UNIT_PREFIX + "-*")

    command = [
        SYSTEMD_RUN,
        "--user",
        "--quiet",
        "--unit=" + unit,
        "--description=" + description,
        "--",
    ]
    command.extend(argv)

    completed = run_fixed(command, timeout=LAUNCH_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise AdapterError(
            "the session manager refused to start the application",
            completed.stderr.decode("utf-8", "replace")
            or ("systemd-run exited " + str(completed.returncode)),
        )

    return _observe(unit, settle_seconds)


def _observe(unit: str, settle_seconds: float) -> SessionLaunch:
    """Watch a transient unit for the whole settle window before judging it.

    Returning as soon as the unit looks active would reintroduce the original
    bug: a process that fails on startup is briefly ``active`` before it is
    ``failed``. Only a terminal state ends the watch early.
    """
    deadline = time.monotonic() + settle_seconds
    properties: Dict[str, str] = {}

    while True:
        properties = _show_unit(unit)
        active_state = properties.get("ActiveState", "")
        exit_status = _to_int(properties.get("ExecMainStatus"))

        if active_state == "failed":
            _reset_failed(unit + ".service")
            raise AdapterError(
                "the application exited immediately instead of starting",
                _failure_detail(properties),
            )

        if active_state in ("inactive", "deactivating"):
            if exit_status not in (0, None):
                _reset_failed(unit + ".service")
                raise AdapterError(
                    "the application exited immediately instead of starting",
                    _failure_detail(properties),
                )
            return SessionLaunch(unit=unit, pid=None, state="exited", exit_status=exit_status)

        if time.monotonic() >= deadline:
            break
        time.sleep(LAUNCH_POLL_SECONDS)

    # Survived the whole window: the process is really up.
    return SessionLaunch(
        unit=unit,
        pid=_to_int(properties.get("ExecMainPID")) or None,
        state="running",
        exit_status=None,
    )


def process_running(names: Sequence[str]) -> bool:
    """True if a process with one of ``names`` is alive.

    Used to corroborate a fast, clean exit: launching a browser that is already
    running hands the request to the existing instance and returns at once, so
    "exited 0" is only trustworthy when that instance can be seen.
    """
    try:
        import psutil  # type: ignore
    except Exception:  # pragma: no cover - psutil is a declared dependency
        return False

    wanted = {name.lower() for name in names}
    try:
        iterator = psutil.process_iter(["name"])
    except Exception:  # pragma: no cover - defensive
        return False
    for process in iterator:
        try:
            name = (process.info.get("name") or "").lower()
        except Exception:
            continue
        if name in wanted:
            return True
    return False
