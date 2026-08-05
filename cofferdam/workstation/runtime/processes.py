"""Which of this user's processes are running right now.

Backend: ``/proc``, read directly. No ``ps``, no subprocess, no shell — the
kernel already publishes this as files, and every layer between us and those
files is a layer that could reformat, truncate, or localise them.

Identity
--------
``PID`` alone is never an identity here. PIDs are recycled, and on a busy
workstation they are recycled within minutes; a stale PID is how a later
milestone's "close this application" ends up closing something else. The
identity is ``host + boot + PID + start time``:

* **start time** is ``/proc/<pid>/stat`` field 22, in clock ticks since boot. It
  is assigned by the kernel at fork and never changes, so two processes that
  reuse one PID have different start times with certainty;
* **boot** scopes the tick count, which is meaningless across a reboot;
* **host** scopes the boot.

A future control action must re-read the PID's start time and compare it to the
one in the identity before acting. That check is what makes the identity worth
having, and this module publishes ``start_ticks`` precisely so it can be made.

What is deliberately never read
-------------------------------
``/proc/<pid>/environ`` and ``/proc/<pid>/cmdline`` are **not opened by this
module at all**. Both routinely carry secrets on a real desktop — an API key
passed as an argument, a token in an environment variable, a database URL with
its password, a file path that reveals a document's name. The safe handling of a
secret you have already read into memory is a much harder problem than not
reading it, so the grouping in
:mod:`~cofferdam.workstation.runtime.applications` is built on cgroup
membership and process ancestry instead, neither of which requires a command
line. ``tests/test_runtime_processes.py`` asserts the absence structurally, so
"just this once, for classification" cannot creep back in.

Failure tolerance
-----------------
Enumerating processes is inherently racy: a process listed in ``/proc`` a
microsecond ago may be gone before its ``stat`` is read. That is not an error
and does not degrade the collection — a process that exited is genuinely not
running, which is exactly what the snapshot should say. A process that exists
but *cannot be read* is different: something is there and we cannot describe it,
so that does downgrade the collection to ``partial`` and says so.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .identity import clock_ticks_per_second, fingerprint
from .models import (
    KIND_PROCESSES,
    STABILITY_BOOT,
    Evidence,
    ResourceCollection,
    collected,
    unavailable,
)

BACKEND_PROC = "proc-filesystem"

PROC_PATH = "/proc"

# Field 22 of ``/proc/<pid>/stat``, counted from 1. After the executable name in
# parentheses the fields restart at 3, so this is index 19 of the remainder.
_STAT_STARTTIME_INDEX = 19
_STAT_STATE_INDEX = 0
_STAT_PPID_INDEX = 1

# A desktop runs a few hundred processes. This ceiling exists so a runaway
# fork bomb produces a bounded, honest ``partial`` rather than an API response
# that never finishes serialising.
MAX_PROCESSES = 4096

_PROCESS_STATES = {
    "R": "running",
    "S": "sleeping",
    "D": "uninterruptible",
    "Z": "zombie",
    "T": "stopped",
    "t": "tracing-stop",
    "X": "dead",
    "I": "idle",
}

_LIMITATIONS = (
    "only processes owned by the user this service runs as are enumerated",
    "command lines and environment variables are never read, so a process is described by its "
    "executable and its systemd unit rather than by its arguments",
    "a process that exits during the scan is omitted rather than reported, because it is no "
    "longer running",
    "an executable path is absent when the process is owned by another user or has already exited",
)


@dataclass(frozen=True)
class ProcessFacts:
    """Everything read about one process. Internal; the API sees a subset."""

    pid: int
    ppid: Optional[int]
    name: Optional[str]
    state: Optional[str]
    start_ticks: Optional[int]
    executable_path: Optional[str]
    cgroup_path: Optional[str]
    unit: Optional[str]
    uid: Optional[int]


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def _parse_stat(raw: str) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    """``(state, ppid, start_ticks)`` from ``/proc/<pid>/stat``.

    The executable name sits in parentheses and may itself contain spaces and
    parentheses — ``(Web Content)`` is a real Firefox process name — so the
    split is anchored on the **last** ``)``, never on whitespace.
    """
    close = raw.rfind(")")
    if close < 0:
        return None, None, None
    fields = raw[close + 1 :].split()
    if len(fields) <= _STAT_STARTTIME_INDEX:
        return None, None, None
    state = _PROCESS_STATES.get(fields[_STAT_STATE_INDEX], fields[_STAT_STATE_INDEX])
    try:
        ppid: Optional[int] = int(fields[_STAT_PPID_INDEX])
    except ValueError:
        ppid = None
    try:
        start_ticks: Optional[int] = int(fields[_STAT_STARTTIME_INDEX])
    except ValueError:
        start_ticks = None
    return state, ppid, start_ticks


def _parse_cgroup(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """``(cgroup_path, unit)`` from ``/proc/<pid>/cgroup``.

    cgroup v2 writes a single ``0::<path>`` line. The unit is the deepest path
    component that systemd would name — a ``.scope`` or ``.service``.
    """
    for line in raw.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        path = parts[2].strip()
        if not path:
            continue
        unit = None
        for component in reversed(path.split("/")):
            if component.endswith((".scope", ".service")):
                unit = component
                break
        return path, unit
    return None, None


def read_process(pid: int, proc_root: str = PROC_PATH) -> Optional[ProcessFacts]:
    """Read one process, or ``None`` if it is gone or unreadable.

    ``None`` deliberately conflates "exited" and "cannot be read": the caller
    distinguishes them by checking whether the directory still exists, and the
    two lead to different collection statuses.
    """
    base = Path(proc_root) / str(pid)

    stat_raw = _read_text(base / "stat")
    if stat_raw is None:
        return None
    state, ppid, start_ticks = _parse_stat(stat_raw)

    comm = _read_text(base / "comm")
    name = comm.strip() if comm else None

    try:
        uid: Optional[int] = base.stat().st_uid
    except OSError:
        uid = None

    executable_path: Optional[str] = None
    try:
        executable_path = os.readlink(str(base / "exe"))
    except OSError:
        # Permission denied for another user's process, or the process exited.
        # Absent, not guessed.
        executable_path = None

    cgroup_raw = _read_text(base / "cgroup")
    cgroup_path, unit = _parse_cgroup(cgroup_raw) if cgroup_raw else (None, None)

    return ProcessFacts(
        pid=pid,
        ppid=ppid,
        name=name,
        state=state,
        start_ticks=start_ticks,
        executable_path=executable_path,
        cgroup_path=cgroup_path,
        unit=unit,
        uid=uid,
    )


def process_resource_id(host_id: str, boot_id: str, pid: int, start_ticks: int) -> str:
    """The identity rule, in one place so nothing can implement it differently."""
    return "process-" + fingerprint(
        "cofferdam.process", host_id, boot_id, str(pid), str(start_ticks)
    )


def started_at(start_ticks: Optional[int], boot_epoch_seconds: Optional[int]) -> Optional[str]:
    """Absolute start instant, when the boot time is known to anchor it."""
    if start_ticks is None or boot_epoch_seconds is None:
        return None
    seconds = boot_epoch_seconds + (start_ticks / clock_ticks_per_second())
    return (
        datetime.fromtimestamp(seconds, timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class ProcessDiscovery:
    """Resources owned: this user's live processes.

    Evidence: ``/proc/<pid>/{stat,comm,cgroup,exe}``.

    Limitations: other users' processes are not enumerated; command lines and
    environment variables are never read; a process that exits mid-scan is
    omitted rather than reported.
    """

    kind = KIND_PROCESSES

    def __init__(self, proc_root: str = PROC_PATH, uid: Optional[int] = None) -> None:
        self._proc_root = proc_root
        self._uid = os.getuid() if uid is None else uid

    def read_all(self) -> Tuple[List[ProcessFacts], List[str]]:
        """Every readable process of this user, plus any warnings.

        Returned as facts rather than API items so that
        :mod:`~cofferdam.workstation.runtime.applications` can group on the same
        single scan instead of walking ``/proc`` a second time and seeing a
        different moment.
        """
        facts: List[ProcessFacts] = []
        warnings: List[str] = []
        unreadable = 0

        try:
            entries = os.listdir(self._proc_root)
        except OSError:
            return facts, ["the process table could not be listed on this host"]

        pids = sorted(int(name) for name in entries if name.isdigit())
        truncated = False
        for pid in pids:
            if len(facts) >= MAX_PROCESSES:
                truncated = True
                break
            record = read_process(pid, self._proc_root)
            if record is None:
                # Gone, or belongs to somebody we cannot read. Only the second
                # is worth a warning, and only when the entry still exists.
                if (Path(self._proc_root) / str(pid)).exists():
                    unreadable += 1
                continue
            if record.uid is not None and record.uid != self._uid:
                continue
            facts.append(record)

        if truncated:
            warnings.append(
                "more than " + str(MAX_PROCESSES) + " processes are running; the list was "
                "truncated and is incomplete"
            )
        if unreadable:
            warnings.append(
                str(unreadable) + " process entries exist but could not be read, so they are "
                "missing from this list"
            )
        return facts, warnings

    def collect(
        self,
        host_id: str,
        boot,
        facts: List[ProcessFacts],
        scan_warnings: List[str],
        instance_by_pid: Optional[Dict[int, str]] = None,
    ) -> ResourceCollection:
        evidence = Evidence(
            backend=BACKEND_PROC,
            sources=("/proc/<pid>/stat", "/proc/<pid>/comm", "/proc/<pid>/cgroup", "/proc/<pid>/exe"),
            limitations=_LIMITATIONS,
        )

        if not getattr(boot, "available", False):
            # Without a boot identity a start time cannot be scoped, and a PID
            # on its own is precisely the identity this milestone forbids.
            return unavailable(
                self.kind,
                "this host does not publish a boot identity, so no stable process identity can "
                "be formed (a PID alone is never one)",
                evidence,
            )

        if not Path(self._proc_root).is_dir():
            # Not a Linux host, or /proc is not mounted. "No processes" would
            # be a startling and false thing to say about a running machine.
            return unavailable(
                self.kind,
                "this host does not publish a /proc filesystem, so its processes cannot be "
                "enumerated by this build",
                evidence,
            )

        instance_by_pid = instance_by_pid or {}
        items: List[Dict[str, Any]] = []
        for record in facts:
            if record.start_ticks is None:
                # Without a start time there is no identity, and a process
                # listed without one invites somebody to fall back to the PID.
                continue
            items.append(
                {
                    "resource_id": process_resource_id(
                        host_id, boot.boot_id, record.pid, record.start_ticks
                    ),
                    "kind": "process",
                    "identity": {
                        "source": "pid+start-time",
                        "stability": STABILITY_BOOT,
                        "boot_id": boot.boot_id,
                    },
                    "pid": record.pid,
                    "parent_pid": record.ppid,
                    "start_ticks": record.start_ticks,
                    "started_at": started_at(
                        record.start_ticks, getattr(boot, "boot_epoch_seconds", None)
                    ),
                    "name": record.name,
                    "executable": (
                        os.path.basename(record.executable_path) if record.executable_path else None
                    ),
                    "executable_path": record.executable_path,
                    "state": record.state,
                    "user_id": record.uid,
                    "unit": record.unit,
                    "cgroup": record.cgroup_path,
                    "application_instance_id": instance_by_pid.get(record.pid),
                    "backend": BACKEND_PROC,
                    "overlay": None,
                }
            )
        return collected(self.kind, items, evidence, scan_warnings)


__all__ = [
    "BACKEND_PROC",
    "MAX_PROCESSES",
    "PROC_PATH",
    "ProcessDiscovery",
    "ProcessFacts",
    "process_resource_id",
    "read_process",
    "started_at",
]
