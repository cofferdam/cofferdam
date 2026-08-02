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
from typing import Dict, List, Optional, Sequence

from ..errors import AdapterError

# Logical application keys. Callers may only ever send one of these strings;
# the mapping to a real executable is the adapter's private business.
APPLICATION_KEYS: tuple = ("firefox", "chromium", "google-chrome")

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
    def open_url(self, url: str) -> ApplicationLaunch:
        """Open a validated http(s) URL in the host's browser."""

    def available_applications(self) -> List[str]:
        """Subset of :data:`APPLICATION_KEYS` this host can actually launch."""
        return list(APPLICATION_KEYS)


# ---------------------------------------------------------------------------
# helpers shared by the real (non-stub) adapters
# ---------------------------------------------------------------------------


def run_fixed(argv: Sequence[str], *, timeout: int = SUBPROCESS_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    """Run a fully adapter-constructed argv, capturing output, never via a shell.

    ``argv`` must be built by the adapter from constants plus, at most, values
    the service itself validated (a URL scheme-checked to http/https, a path the
    service generated). Nothing here interpolates caller text into a string.
    """
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, shell is never used
            list(argv),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AdapterError(f"required program not found: {argv[0]}", exc) from exc
    except subprocess.TimeoutExpired as exc:
        raise AdapterError(f"program timed out: {argv[0]}", exc) from exc
    except OSError as exc:
        raise AdapterError(f"could not run program: {argv[0]}", exc) from exc


def spawn_fixed(argv: Sequence[str]) -> int:
    """Start a detached, adapter-constructed argv and return its PID."""
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
