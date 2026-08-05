"""The host adapter interface plus shared, platform-neutral helpers.

Every capability the product exposes goes through this interface, so the API,
the action registry, and the PWA never learn what OS they are talking to.

Invariants for **all** implementations:

* No implementation ever accepts a command, argument list, or shell string from
  a caller. Applications are chosen from :data:`APPLICATION_KEYS` — a fixed
  logical allowlist that each adapter maps to its own fixed argv.
* No implementation uses ``shell=True``, ``os.system``, or string-built
  commands. (``tests/test_workstation_no_shell.py`` enforces this by scanning
  this package's source.)
* Failures are raised as :class:`~cofferdam.workstation.errors.AdapterError`
  (or :class:`AdapterUnsupported`), never as raw OS exceptions.
"""

from __future__ import annotations

import abc
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence

from ..errors import AdapterError

# Logical application keys. Callers may only ever send one of these strings;
# the mapping to a real executable is the adapter's private business.
#
# ``opera`` joined the list in M2A because the browser-profile registry can
# select it. It is appended rather than inserted so the legacy "first installed
# browser wins" URL path keeps choosing exactly what it chose before.
APPLICATION_KEYS: tuple = ("firefox", "chromium", "google-chrome", "opera")

SUBPROCESS_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class HostStatus:
    hostname: str
    platform: str
    session_type: Optional[str]
    adapter: str
    stub: bool
    uptime_seconds: Optional[float]
    cpu_percent: Optional[float]
    memory_total_bytes: Optional[int]
    memory_used_bytes: Optional[int]
    disk_total_bytes: Optional[int]
    disk_used_bytes: Optional[int]
    display_count: Optional[int]
    capabilities: Dict[str, bool] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Screenshot:
    png_bytes: bytes
    width: Optional[int] = None
    height: Optional[int] = None
    tool: Optional[str] = None


@dataclass(frozen=True)
class ApplicationLaunch:
    application: str
    pid: Optional[int] = None
    detail: Optional[str] = None


class HostAdapter(abc.ABC):
    """Platform capability provider: status, screenshot, launch, open URL."""

    name: str = "base"
    stub: bool = False

    def __init__(self, config) -> None:
        self._config = config

    @abc.abstractmethod
    def host_status(self) -> HostStatus:
        """Current host status. Must not raise for partially-available data."""

    @abc.abstractmethod
    def take_screenshot(self) -> Screenshot:
        """Capture the host's screen as PNG bytes."""

    @abc.abstractmethod
    def open_application(self, application: str) -> ApplicationLaunch:
        """Launch an allowlisted application by logical key."""

    @abc.abstractmethod
    def open_url(self, url: str, application: Optional[str] = None) -> ApplicationLaunch:
        """Open a validated http(s) URL in a browser.

        ``application`` is either ``None`` — meaning "this host's usual
        browser", the pre-M2A behaviour — or one of :data:`APPLICATION_KEYS`,
        already resolved from a browser profile by the service. It is a logical
        key, never a program name: the adapter still owns the mapping.
        """

    def available_applications(self) -> List[str]:
        """Subset of :data:`APPLICATION_KEYS` this host can actually launch."""
        return list(APPLICATION_KEYS)

    def application_executables(self) -> Dict[str, tuple]:
        """Logical key -> executable **basenames** this host would run for it.

        Runtime inventory (M2B) uses this to decide whether a discovered group
        of processes belongs to an application Cofferdam already knows about.
        It is a read-only view of the adapter's own fixed table, exposed so the
        discovery layer never has to hardcode a program name or reach into a
        private mapping — the adapter stays the single owner of the
        logical-key-to-program relationship.

        The default is empty: an adapter that cannot say leaves every
        discovered instance honestly unmapped.
        """
        return {}


# ---------------------------------------------------------------------------
# helpers shared by the real (non-stub) adapters
# ---------------------------------------------------------------------------


def run_fixed(
    argv: Sequence[str],
    *,
    timeout: int = SUBPROCESS_TIMEOUT_SECONDS,
    env: Optional[Mapping[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Run a fully adapter-constructed argv, capturing output, never via a shell.

    ``argv`` must be built by the adapter from constants plus, at most, values
    the service itself validated (a URL scheme-checked to http/https, a path the
    service generated). Nothing here interpolates caller text into a string.

    ``env``, when given, replaces the child's environment wholesale. It exists
    so an adapter can hand a program the *verified graphical session's* display
    variables instead of this service's own frozen ones; it is built by the
    adapter, never from caller-supplied text.
    """
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, shell is never used
            list(argv),
            capture_output=True,
            timeout=timeout,
            check=False,
            env=None if env is None else dict(env),
        )
    except FileNotFoundError as exc:
        raise AdapterError(f"required program not found: {argv[0]}", exc) from exc
    except subprocess.TimeoutExpired as exc:
        raise AdapterError(f"program timed out: {argv[0]}", exc) from exc
    except OSError as exc:
        raise AdapterError(f"could not run program: {argv[0]}", exc) from exc


def spawn_fixed(argv: Sequence[str]) -> int:
    """Start a detached, adapter-constructed argv and return its PID.

    **This returns as soon as the fork succeeds and is not evidence that the
    program kept running.** A process that dies milliseconds later still yields
    a PID here. Any adapter reporting a graphical action as succeeded must
    confirm the outcome separately — see
    :mod:`~cofferdam.workstation.adapters.linux_session`, which replaced this
    helper on Linux after it produced exactly that false success on Ubuntu.
    """
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, shell is never used
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise AdapterError(f"application not installed: {argv[0]}", exc) from exc
    except OSError as exc:
        raise AdapterError(f"could not start application: {argv[0]}", exc) from exc
    return process.pid


def first_available(candidates: Sequence[str]) -> Optional[str]:
    """First executable on PATH from ``candidates``."""
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def psutil_metrics() -> dict:
    """Best-effort cross-platform metrics; missing psutil degrades to ``None``."""
    try:
        import psutil  # type: ignore
    except Exception:  # pragma: no cover - psutil is a declared dependency
        return {}
    metrics: dict = {}
    try:
        metrics["cpu_percent"] = psutil.cpu_percent(interval=None)
    except Exception:
        pass
    try:
        memory = psutil.virtual_memory()
        metrics["memory_total_bytes"] = int(memory.total)
        metrics["memory_used_bytes"] = int(memory.total - memory.available)
    except Exception:
        pass
    try:
        import time

        metrics["uptime_seconds"] = max(0.0, time.time() - psutil.boot_time())
    except Exception:
        pass
    return metrics


def disk_metrics(path: str) -> dict:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return {}
    return {"disk_total_bytes": int(usage.total), "disk_used_bytes": int(usage.used)}
