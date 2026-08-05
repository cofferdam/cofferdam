"""Who and when a runtime observation belongs to.

Every runtime resource is scoped by three identities, because none of them is
sufficient alone:

* **host** — which machine was observed. Two machines can both have a PID 7015.
* **boot** — which boot of that machine. A PID plus a start time only means
  something within one boot; ``starttime`` in ``/proc/<pid>/stat`` is measured
  in clock ticks *since boot*.
* **graphical session** — which login session was up. Displays and windows
  belong to a session; a snapshot taken before login, or one taken in a session
  that has since been replaced, must not be presented as current.

Identities are published as **derived fingerprints**, not as the raw system
values. ``/etc/machine-id`` and ``/proc/sys/kernel/random/boot_id`` are both
world-readable and neither is a secret, but both are exactly the kind of stable
global identifier that should not be handed out verbatim just because a client
authenticated. A domain-separated SHA-256 prefix keeps every property this
module actually needs — stable within a boot, different across boots, comparable
between two snapshots — and gives up only the ability to correlate this machine
with some other system that saw the same raw value.

Nothing here raises. A host with no ``/etc/machine-id``, no readable
``/proc/stat``, or no graphical session still yields a usable identity with the
missing parts reported as missing.
"""

from __future__ import annotations

import hashlib
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

MACHINE_ID_PATHS = ("/etc/machine-id", "/var/lib/dbus/machine-id")
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
PROC_STAT_PATH = "/proc/stat"

# Length of the published hex prefix. 16 hex characters is 64 bits: far more
# than enough to keep the handful of resources on one desk distinct, and short
# enough to read in a UI or a bug report.
FINGERPRINT_LENGTH = 16

SOURCE_MACHINE_ID = "machine-id"
SOURCE_HOSTNAME = "hostname"
SOURCE_PROC_BOOT_ID = "proc-boot-id"
SOURCE_UNAVAILABLE = "unavailable"


def now_iso() -> str:
    """UTC timestamp in the format the action records already use."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def fingerprint(domain: str, *parts: str) -> str:
    """A short, domain-separated digest of ``parts``.

    ``domain`` keeps digests from different kinds of thing out of each other's
    namespace: a host fingerprint and a boot fingerprint computed from the same
    bytes must never come out equal.
    """
    digest = hashlib.sha256()
    digest.update(domain.encode("utf-8"))
    for part in parts:
        digest.update(b"\x1f")
        digest.update(part.encode("utf-8", "replace"))
    return digest.hexdigest()[:FINGERPRINT_LENGTH]


def _read_text(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# host
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HostIdentity:
    """Which machine a snapshot describes."""

    hostname: str
    host_id: str
    source: str

    def to_dict(self) -> Dict[str, Any]:
        return {"hostname": self.hostname, "host_id": self.host_id, "source": self.source}


def detect_host_identity() -> HostIdentity:
    """Fingerprint this machine, preferring the stable systemd machine ID.

    The hostname fallback is weaker on purpose and says so: a hostname can be
    changed at any time, and two machines on a home network can briefly share
    one. ``source`` is published so a client can tell which it got.
    """
    hostname = socket.gethostname()
    for path in MACHINE_ID_PATHS:
        raw = _read_text(path)
        if raw:
            return HostIdentity(
                hostname=hostname,
                host_id="host-" + fingerprint("cofferdam.host", raw),
                source=SOURCE_MACHINE_ID,
            )
    return HostIdentity(
        hostname=hostname,
        host_id="host-" + fingerprint("cofferdam.host.hostname", hostname),
        source=SOURCE_HOSTNAME,
    )


# ---------------------------------------------------------------------------
# boot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BootIdentity:
    """Which boot of the host a snapshot describes.

    ``booted_at`` is what turns a process ``starttime`` — clock ticks since
    boot — into an absolute instant. Without it a process start time is only
    comparable to other process start times from the same boot, which is still
    enough to tell two PID generations apart but not enough to display.
    """

    boot_id: Optional[str]
    source: str
    booted_at: Optional[str] = None
    boot_epoch_seconds: Optional[int] = None

    @property
    def available(self) -> bool:
        return self.boot_id is not None

    def to_dict(self) -> Dict[str, Any]:
        return {"boot_id": self.boot_id, "source": self.source, "booted_at": self.booted_at}


def _boot_epoch_seconds() -> Optional[int]:
    text = _read_text(PROC_STAT_PATH)
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("btime "):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def detect_boot_identity() -> BootIdentity:
    raw = _read_text(BOOT_ID_PATH)
    epoch = _boot_epoch_seconds()
    booted_at = None
    if epoch is not None:
        booted_at = (
            datetime.fromtimestamp(epoch, timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    if not raw:
        # No boot id means no trustworthy process identity, and callers check
        # ``available`` for exactly that reason.
        return BootIdentity(
            boot_id=None,
            source=SOURCE_UNAVAILABLE,
            booted_at=booted_at,
            boot_epoch_seconds=epoch,
        )
    return BootIdentity(
        boot_id="boot-" + fingerprint("cofferdam.boot", raw),
        source=SOURCE_PROC_BOOT_ID,
        booted_at=booted_at,
        boot_epoch_seconds=epoch,
    )


def clock_ticks_per_second() -> int:
    """``CLK_TCK`` — the unit of ``/proc/<pid>/stat`` field 22."""
    try:
        ticks = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError, AttributeError):  # pragma: no cover - non-POSIX
        return 100
    return int(ticks) if ticks and ticks > 0 else 100


# ---------------------------------------------------------------------------
# graphical session
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionIdentity:
    """Which graphical session a session-scoped resource belongs to.

    ``session_id`` is derived from the *generation marker* that
    :func:`~cofferdam.workstation.adapters.linux_session.detect_graphical_session`
    already computes — systemd's ``ActiveEnterTimestampMonotonic`` for
    ``graphical-session.target``. That marker changes on every login, so a
    snapshot taken before a logout can never compare equal to one taken after
    the next login, which is what makes stale display and window data
    detectable rather than merely old.
    """

    available: bool
    session_id: Optional[str] = None
    session_type: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "session_id": self.session_id,
            "session_type": self.session_type,
            "reason": self.reason,
        }


def session_identity_from(session) -> SessionIdentity:
    """Adapt a :class:`GraphicalSession` into a published session identity."""
    if not getattr(session, "available", False):
        return SessionIdentity(
            available=False,
            session_type=getattr(session, "session_type", None),
            reason=getattr(session, "reason", None) or "no graphical session is active",
        )
    marker = getattr(session, "session_id", None)
    return SessionIdentity(
        available=True,
        # A session can be active without systemd having a usable activation
        # stamp for it. That is honestly "available but not identifiable", not
        # a reason to invent an id.
        session_id=("gsession-" + fingerprint("cofferdam.gsession", marker)) if marker else None,
        session_type=getattr(session, "session_type", None),
    )


__all__ = [
    "BOOT_ID_PATH",
    "BootIdentity",
    "FINGERPRINT_LENGTH",
    "HostIdentity",
    "MACHINE_ID_PATHS",
    "SOURCE_HOSTNAME",
    "SOURCE_MACHINE_ID",
    "SOURCE_PROC_BOOT_ID",
    "SOURCE_UNAVAILABLE",
    "SessionIdentity",
    "clock_ticks_per_second",
    "detect_boot_identity",
    "detect_host_identity",
    "fingerprint",
    "now_iso",
    "session_identity_from",
]
