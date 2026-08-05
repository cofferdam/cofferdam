"""The runtime snapshot: the versioned shape every client reads.

A snapshot is one observation of one machine at one instant. It carries four
resource **collections** — displays, application instances, processes, windows —
and each collection reports its own status independently, because the backends
behind them fail independently. Wayland can refuse to enumerate windows while
``/proc`` happily lists every process; a snapshot that flattened that into one
verdict would have to be wrong about one of them.

Status vocabulary (closed, and the reason this module exists)
-------------------------------------------------------------

``ok``
    The backend ran and the items are the complete answer. **Zero items with
    ``ok`` means the machine genuinely has none of that resource.**
``partial``
    The backend ran, the items are real, and something was missed. ``warnings``
    says what. A process that exited mid-scan lands here.
``unavailable``
    No backend on this host can answer the question at all. ``reason`` says why
    in words a person can act on. There are **no items** — and an empty
    ``unavailable`` collection is a different statement from an empty ``ok``
    one, which is the whole point.
``error``
    A backend that should have worked failed. ``reason`` describes the failure
    kind; it never carries a path, a command line, or an exception trace.

The distinction between ``ok``-and-empty and ``unavailable`` is the rule this
milestone exists to protect. Reporting "no windows" when the truth is "this
system cannot tell you about windows" is the exact false-success shape that M1
found in the launch path and M2A found in the registries.

Every field here is either observed or absent. There is no default that stands
in for an unread value: a display with no serial in its EDID has ``serial:
null``, never ``"unknown"`` and never a value borrowed from somewhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

# Bumped when the shape below changes incompatibly. Clients read it and may
# refuse a shape they do not know; they must never guess.
RUNTIME_SNAPSHOT_VERSION = 1

# -- collection status -------------------------------------------------------

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_UNAVAILABLE = "unavailable"
STATUS_ERROR = "error"

COLLECTION_STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_UNAVAILABLE, STATUS_ERROR)

# -- resource kinds ----------------------------------------------------------

KIND_DISPLAYS = "displays"
KIND_APPLICATIONS = "applications"
KIND_PROCESSES = "processes"
KIND_WINDOWS = "windows"

RESOURCE_KINDS = (KIND_DISPLAYS, KIND_APPLICATIONS, KIND_PROCESSES, KIND_WINDOWS)

# -- identity stability ------------------------------------------------------
#
# How much a resource_id can be trusted to mean the same thing next time.

STABILITY_HARDWARE = "hardware"
"""Derived from the hardware itself (an EDID digest). Survives reboots, cable
swaps, and connector renumbering."""

STABILITY_BOOT = "boot"
"""Stable for this boot only. A process identity is this: PID plus start time
is unique within a boot and meaningless across one."""

STABILITY_SESSION = "session"
"""Stable for this graphical session only."""

STABILITY_WEAK = "weak"
"""Derived from something that can change without the resource changing — a
connector name, for instance. Usable for display, never for control."""

IDENTITY_STABILITIES = (
    STABILITY_HARDWARE,
    STABILITY_BOOT,
    STABILITY_SESSION,
    STABILITY_WEAK,
)


@dataclass(frozen=True)
class Evidence:
    """What a backend looked at, and what it cannot see.

    ``limitations`` is not documentation-in-passing: it is the machine-readable
    half of the honesty rule. A client that shows a resource is expected to be
    able to show why it might be incomplete.
    """

    backend: str
    sources: Tuple[str, ...] = ()
    limitations: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "sources": list(self.sources),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class ResourceCollection:
    """One resource kind's result, with its own independent status."""

    kind: str
    status: str
    items: Tuple[Mapping[str, Any], ...] = ()
    evidence: Optional[Evidence] = None
    reason: Optional[str] = None
    warnings: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in COLLECTION_STATUSES:  # pragma: no cover - programming error
            raise ValueError(f"unknown collection status: {self.status}")
        if self.status in (STATUS_UNAVAILABLE, STATUS_ERROR) and self.items:
            # An unavailable backend has nothing to report. Letting it carry
            # items would make "unavailable" negotiable, and the value of the
            # vocabulary is that it is not.
            raise ValueError(f"a {self.status} collection cannot carry items")
        if self.status in (STATUS_UNAVAILABLE, STATUS_ERROR) and not self.reason:
            raise ValueError(f"a {self.status} collection must say why")

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def to_dict(self) -> Dict[str, Any]:
        # Every documented key is always present, null included. A *missing*
        # key invites "the server is older than I am"; an explicit null says
        # "this host did not report it", which is what we actually mean.
        return {
            "kind": self.kind,
            "status": self.status,
            "count": len(self.items),
            "items": [dict(item) for item in self.items],
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "reason": self.reason,
            "warnings": list(self.warnings),
        }


def unavailable(kind: str, reason: str, evidence: Optional[Evidence] = None) -> ResourceCollection:
    """Shorthand for the case this whole module is built around."""
    return ResourceCollection(kind=kind, status=STATUS_UNAVAILABLE, reason=reason, evidence=evidence)


def failed(kind: str, reason: str, evidence: Optional[Evidence] = None) -> ResourceCollection:
    return ResourceCollection(kind=kind, status=STATUS_ERROR, reason=reason, evidence=evidence)


def collected(
    kind: str,
    items: Sequence[Mapping[str, Any]],
    evidence: Optional[Evidence] = None,
    warnings: Sequence[str] = (),
) -> ResourceCollection:
    """An ``ok`` collection, downgraded to ``partial`` when anything was missed."""
    return ResourceCollection(
        kind=kind,
        status=STATUS_PARTIAL if warnings else STATUS_OK,
        items=tuple(items),
        evidence=evidence,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True)
class RuntimeSnapshot:
    """One observation of one machine, at ``observed_at``.

    The identities are not decoration. A client comparing two snapshots uses
    them to decide whether a resource_id from the older one still means
    anything: a different ``boot.boot_id`` invalidates every process identity, a
    different ``session.session_id`` invalidates every session-scoped resource,
    and a different ``host.host_id`` invalidates all of it.
    """

    observed_at: str
    host: Mapping[str, Any]
    boot: Mapping[str, Any]
    session: Mapping[str, Any]
    collections: Mapping[str, ResourceCollection] = field(default_factory=dict)
    warnings: Tuple[str, ...] = ()
    version: int = RUNTIME_SNAPSHOT_VERSION

    def collection(self, kind: str) -> ResourceCollection:
        return self.collections[kind]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "observed_at": self.observed_at,
            "host": dict(self.host),
            "boot": dict(self.boot),
            "session": dict(self.session),
            "collections": {
                kind: self.collections[kind].to_dict()
                for kind in RESOURCE_KINDS
                if kind in self.collections
            },
            "warnings": list(self.warnings),
        }


__all__ = [
    "COLLECTION_STATUSES",
    "Evidence",
    "IDENTITY_STABILITIES",
    "KIND_APPLICATIONS",
    "KIND_DISPLAYS",
    "KIND_PROCESSES",
    "KIND_WINDOWS",
    "RESOURCE_KINDS",
    "RUNTIME_SNAPSHOT_VERSION",
    "ResourceCollection",
    "RuntimeSnapshot",
    "STABILITY_BOOT",
    "STABILITY_HARDWARE",
    "STABILITY_SESSION",
    "STABILITY_WEAK",
    "STATUS_ERROR",
    "STATUS_OK",
    "STATUS_PARTIAL",
    "STATUS_UNAVAILABLE",
    "collected",
    "failed",
    "unavailable",
]
