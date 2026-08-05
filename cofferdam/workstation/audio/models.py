"""The audio snapshot: what a client reads, and what it is allowed to believe.

This module is the audio half of what
:mod:`cofferdam.workstation.runtime.models` does for displays and processes, and
it deliberately reuses that module's vocabulary — ``Evidence``,
``ResourceCollection``, and the closed ``ok``/``partial``/``unavailable``/
``error`` status set — rather than inventing a parallel one. A collection that
is empty because this host has no Bluetooth speaker and a collection that is
empty because nothing here can enumerate streams are different statements, and
the runtime milestone already built the shape that keeps them apart.

Three kinds of thing, kept apart on purpose
-------------------------------------------
The architecture rule for this milestone is that these never collapse into each
other:

1. **Device definitions and capabilities** — what a sound card *is*, and which
   profiles it could adopt. Owned by the backend, mostly static within a boot.
2. **Live runtime resources** — the devices, nodes, outputs and streams that
   exist in the PipeWire graph *right now*. Everything here is graph-scoped and
   evaporates when the graph does.
3. **User preferences and overlays** — a preferred output, and labels later.
   Not in this milestone's write path, but the identity model below is built so
   that a preference can key off something that outlives a graph.

Typed actions (:mod:`cofferdam.workstation.audio.actions`) are the fourth thing,
and they consume 2 while being addressed in terms that survive 2 changing
underneath them.

Why a PipeWire node id is never an identity here
------------------------------------------------
A PipeWire global id is a small integer handed out by the daemon and **reused
after the object it named is destroyed**. Node 58 is the built-in speaker on
this host today; after a WirePlumber restart, node 58 may be a Bluetooth
headset, or a microphone, or nothing. Anything that treats that integer as a
name — a cached client id, a queued action, a preference file — is one graph
change away from acting on the wrong device.

So a runtime ``resource_id`` here is a digest over *host + audio graph + the
node's stable name*, and every output additionally publishes the
``object_serial`` PipeWire assigns, which is monotonic within a graph and never
reused. Resolution for an action requires both to still match. The bare node id
travels in the payload as an observation, clearly labelled, and is never
accepted back from a client as authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from ..runtime.models import (  # re-exported so audio callers need one import
    STATUS_ERROR,
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_UNAVAILABLE,
    Evidence,
    ResourceCollection,
    collected,
    failed,
    unavailable,
)

# Bumped when the shape below changes incompatibly. Clients read it and may
# refuse a shape they do not know; they must never guess.
AUDIO_SNAPSHOT_VERSION = 1

# -- resource kinds ----------------------------------------------------------

KIND_OUTPUTS = "outputs"
KIND_STREAMS = "streams"

AUDIO_RESOURCE_KINDS = (KIND_OUTPUTS, KIND_STREAMS)

# -- device categories -------------------------------------------------------
#
# A closed, deliberately coarse vocabulary. It answers "which box does the sound
# come out of", which is the question a person holding a phone is actually
# asking. It is derived from structured backend evidence — the device's API and
# bus, and the ACP route the sink is attached to — never from pattern-matching a
# human-readable name.

DEVICE_BUILTIN_SPEAKER = "builtin_speaker"
DEVICE_HDMI = "hdmi"
DEVICE_USB = "usb"
DEVICE_BLUETOOTH = "bluetooth"
DEVICE_UNKNOWN = "unknown"

DEVICE_TYPES = (
    DEVICE_BUILTIN_SPEAKER,
    DEVICE_HDMI,
    DEVICE_USB,
    DEVICE_BLUETOOTH,
    DEVICE_UNKNOWN,
)

# The analog jack on an internal card is reported as ``builtin_speaker`` with a
# ``route`` of "Headphones" rather than getting a category of its own. The
# category names the *device*; the route names which of its outlets is live, and
# the UI shows both. See docs/AUDIO_CONTROL.md for why that split is the honest
# one on an ACP card, where plugging in headphones is a route change on the same
# device rather than the arrival of a new one.

# -- capability reporting ----------------------------------------------------
#
# A capability the backend does not have must never look like a capability that
# simply found nothing to do. ``move_stream`` is the one this milestone cares
# about: see actions.py for why it is unsupported rather than attempted.

CAPABILITY_SUPPORTED = "supported"
CAPABILITY_UNAVAILABLE = "unavailable"

CAPABILITY_STATES = (CAPABILITY_SUPPORTED, CAPABILITY_UNAVAILABLE)


@dataclass(frozen=True)
class Capability:
    """Whether one typed action can be performed on this host, and why not."""

    name: str
    state: str
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.state not in CAPABILITY_STATES:  # pragma: no cover - programming error
            raise ValueError(f"unknown capability state: {self.state}")
        if self.state == CAPABILITY_UNAVAILABLE and not self.reason:
            raise ValueError("an unavailable capability must say why")

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "state": self.state, "reason": self.reason}


# -- graph identity ----------------------------------------------------------


@dataclass(frozen=True)
class GraphIdentity:
    """Which PipeWire graph a runtime audio resource belongs to.

    PipeWire publishes a per-instance ``cookie`` on its core object. It changes
    whenever the daemon is restarted, which is precisely when every node id in
    circulation stops meaning what it meant. Fingerprinting it gives a value
    that is comparable between two snapshots — the only property needed — while
    keeping a stable global identifier off the wire.

    ``available`` is false on a host with no reachable graph. Resources are not
    published in that case at all, so there is never an identity-less output.
    """

    available: bool
    graph_id: Optional[str] = None
    backend: Optional[str] = None
    server_version: Optional[str] = None
    session_manager: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "graph_id": self.graph_id,
            "backend": self.backend,
            "server_version": self.server_version,
            "session_manager": self.session_manager,
            "reason": self.reason,
        }


# -- the snapshot ------------------------------------------------------------


@dataclass(frozen=True)
class AudioSnapshot:
    """One observation of one machine's audio state, at ``observed_at``.

    ``default_output_resource_id`` is a *pointer into* the outputs collection,
    not a copy of an output. It is null whenever the backend could not determine
    a default, which is a real state and not an error: a graph with no sinks has
    no default, and saying so beats naming one arbitrarily.
    """

    observed_at: str
    host: Mapping[str, Any]
    boot: Mapping[str, Any]
    graph: Mapping[str, Any]
    backend: str
    collections: Mapping[str, ResourceCollection] = field(default_factory=dict)
    default_output_resource_id: Optional[str] = None
    capabilities: Tuple[Capability, ...] = ()
    warnings: Tuple[str, ...] = ()
    version: int = AUDIO_SNAPSHOT_VERSION

    def collection(self, kind: str) -> ResourceCollection:
        return self.collections[kind]

    def outputs(self) -> Tuple[Mapping[str, Any], ...]:
        """Items of the outputs collection, or nothing when it has none."""
        found = self.collections.get(KIND_OUTPUTS)
        return found.items if found is not None else ()

    def streams(self) -> Tuple[Mapping[str, Any], ...]:
        found = self.collections.get(KIND_STREAMS)
        return found.items if found is not None else ()

    def output_by_resource_id(self, resource_id: str) -> Optional[Mapping[str, Any]]:
        """The one output with this id, or ``None``.

        Returns ``None`` rather than raising, and never falls back to matching a
        display name: a caller holding an id for a device that has gone away
        must get "not here", not the closest-looking thing.
        """
        for item in self.outputs():
            if item.get("resource_id") == resource_id:
                return item
        return None

    def stream_by_resource_id(self, resource_id: str) -> Optional[Mapping[str, Any]]:
        for item in self.streams():
            if item.get("resource_id") == resource_id:
                return item
        return None

    def capability(self, name: str) -> Optional[Capability]:
        for entry in self.capabilities:
            if entry.name == name:
                return entry
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "observed_at": self.observed_at,
            "host": dict(self.host),
            "boot": dict(self.boot),
            "graph": dict(self.graph),
            "backend": self.backend,
            "default_output_resource_id": self.default_output_resource_id,
            "collections": {
                kind: self.collections[kind].to_dict()
                for kind in AUDIO_RESOURCE_KINDS
                if kind in self.collections
            },
            "capabilities": [entry.to_dict() for entry in self.capabilities],
            "warnings": list(self.warnings),
        }


__all__ = [
    "AUDIO_RESOURCE_KINDS",
    "AUDIO_SNAPSHOT_VERSION",
    "AudioSnapshot",
    "CAPABILITY_STATES",
    "CAPABILITY_SUPPORTED",
    "CAPABILITY_UNAVAILABLE",
    "Capability",
    "DEVICE_BLUETOOTH",
    "DEVICE_BUILTIN_SPEAKER",
    "DEVICE_HDMI",
    "DEVICE_TYPES",
    "DEVICE_UNKNOWN",
    "DEVICE_USB",
    "Evidence",
    "GraphIdentity",
    "KIND_OUTPUTS",
    "KIND_STREAMS",
    "ResourceCollection",
    "STATUS_ERROR",
    "STATUS_OK",
    "STATUS_PARTIAL",
    "STATUS_UNAVAILABLE",
    "collected",
    "failed",
    "unavailable",
]
