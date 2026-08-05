"""The WirePlumber/PipeWire backend: the only thing here that runs a program.

Everything above this module works on plain dictionaries. This module is the
single place where the audio feature touches the host, and it documents itself
against the checklist every Cofferdam backend answers:

Resources it owns
    Audio devices (sound cards), sink nodes (outputs), and playback stream
    nodes, as they exist in the running PipeWire graph. It does not own user
    preferences, labels, or anything that must outlive the graph.

Evidence source
    ``pw-dump`` for the graph — one JSON dump of every object, read once per
    snapshot so outputs and streams always describe the same instant — and
    ``wpctl`` for volume, which is also the interface that *sets* volume. Both
    are invoked as fixed argv through
    :func:`~cofferdam.workstation.adapters.base.run_fixed`. No shell is ever
    constructed, and no caller-supplied text reaches an argument vector: the
    only client-derived value that becomes an argument is an integer percentage
    that has already been range-checked, and it is rendered by ``str(int)``.

Limitations
    * PipeWire global ids are reused after an object is destroyed. This backend
      therefore never treats one as an identity — see ``models.py``.
    * ``pactl`` is deliberately not used and is not installed on the target
      host; the PulseAudio compatibility layer would add a second vocabulary
      for the same objects without adding a capability.
    * A device whose card profile is ``off`` — an HDMI card with no display
      attached, typically — publishes **no sink at all**. It is genuinely not an
      available output, and this backend does not manufacture a placeholder for
      it. Making it selectable would require a profile switch, which is a
      different and more invasive action than choosing a default.
    * Moving an already-playing stream is not offered. See ``actions.py``.

Status and error semantics
    Every method returns data or raises
    :class:`~cofferdam.workstation.errors.AdapterError`. Nothing here returns a
    partially-filled structure with a silent hole, and nothing here decides what
    a failure *means* for the API — the discovery and action layers do that.

Supported actions
    ``set-default``, ``set-volume``, ``set-mute``, each addressed by a node id
    that the caller has just re-resolved from a fresh graph read.

Identity stability
    Graph-scoped. A ``cookie`` change means every node id in circulation is
    meaningless, and the discovery layer folds that cookie into every
    ``resource_id`` so stale ids cannot resolve.

The two volume scales, and why only one of them is published
-------------------------------------------------------------
PipeWire stores a sink's gain as a **linear** multiplier in
``Props.channelVolumes``. ``wpctl`` — and GNOME's own slider — work in a
**cubic** perceptual scale, where the linear gain is the cube of the displayed
value. On the development host the built-in speaker read ``0.846138`` linear and
``0.95`` through ``wpctl``, and ``0.95³ ≈ 0.857`` while ``∛0.846138 ≈ 0.9458``,
which rounds to the ``0.95`` ``wpctl`` printed.

Publishing the linear number as a percentage would put a figure on the phone
that disagrees with the number on the laptop screen for the same speaker. So
volume is read through ``wpctl get-volume`` — the same interface and the same
scale as ``wpctl set-volume`` — and no curve is assumed anywhere in this
codebase. ``wpctl`` prints two decimal places, which is exactly the 1%
granularity of the 0–100 product range, so an integer percentage round-trips
without loss.

Mute is read from the graph instead, where it is an unambiguous boolean, and
``wpctl``'s ``[MUTED]`` marker is used as corroboration rather than as the
source. A boolean has no scale to get wrong, and reading it from the graph makes
the verification after a write independent of the tool that performed it.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..adapters.base import run_fixed
from ..errors import AdapterError, AdapterUnsupported

BACKEND_NAME = "wireplumber"

PW_DUMP = "pw-dump"
WPCTL = "wpctl"

# A graph dump on a desk machine is tens of kilobytes and returns immediately.
# A multi-second ceiling is generous and still bounds a wedged daemon.
GRAPH_TIMEOUT_SECONDS = 10
ACTION_TIMEOUT_SECONDS = 10

# PipeWire object types this backend reads.
TYPE_CORE = "PipeWire:Interface:Core"
TYPE_DEVICE = "PipeWire:Interface:Device"
TYPE_NODE = "PipeWire:Interface:Node"
TYPE_CLIENT = "PipeWire:Interface:Client"
TYPE_METADATA = "PipeWire:Interface:Metadata"
TYPE_LINK = "PipeWire:Interface:Link"

MEDIA_CLASS_SINK = "Audio/Sink"
MEDIA_CLASS_STREAM_OUTPUT = "Stream/Output/Audio"

# The metadata object and key WirePlumber uses to record the default sink. The
# value is keyed by the sink's *node name*, not by its id — which is the
# strongest available hint that the node name is the durable handle and the id
# is not.
METADATA_DEFAULT = "default"
KEY_DEFAULT_SINK = "default.audio.sink"


# ---------------------------------------------------------------------------
# parsed graph
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphNode:
    """One node in the graph, reduced to the fields this feature reads."""

    node_id: int
    object_serial: Optional[int]
    node_name: Optional[str]
    props: Mapping[str, Any]
    state: Optional[str] = None
    mute: Optional[bool] = None
    channel_volumes: Tuple[float, ...] = ()
    channel_map: Tuple[str, ...] = ()

    @property
    def media_class(self) -> Optional[str]:
        value = self.props.get("media.class")
        return value if isinstance(value, str) else None


@dataclass(frozen=True)
class GraphDevice:
    """One sound card, with the profile and routes it currently reports."""

    device_id: int
    props: Mapping[str, Any]
    active_profile: Optional[Mapping[str, Any]] = None
    routes: Tuple[Mapping[str, Any], ...] = ()
    active_routes: Tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class Graph:
    """One read of the whole PipeWire graph.

    ``cookie`` is the graph's identity. It is carried as the raw integer here
    and fingerprinted by the discovery layer; this module stays free of the
    publication rules.
    """

    cookie: Optional[int]
    server_version: Optional[str]
    nodes: Mapping[int, GraphNode] = field(default_factory=dict)
    devices: Mapping[int, GraphDevice] = field(default_factory=dict)
    clients: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)
    default_sink_name: Optional[str] = None
    # (output-node-id, input-node-id) for every link. A playback stream reaches
    # its sink through one link per channel, so this is deduplicated by the
    # discovery layer rather than here.
    links: Tuple[Tuple[int, int], ...] = ()
    warnings: Tuple[str, ...] = ()

    def sinks_for_stream(self, stream_node_id: int) -> List[int]:
        """Sink node ids this stream is currently linked to.

        Normally exactly one. Zero means the stream is playing into nothing —
        which is what an idle or just-started stream looks like — and more than
        one means it is genuinely fanned out, which the caller reports rather
        than resolving arbitrarily.
        """
        sinks = {n.node_id for n in self.sinks()}
        return sorted({dst for src, dst in self.links if src == stream_node_id and dst in sinks})

    def sinks(self) -> List[GraphNode]:
        return [n for n in self.nodes.values() if n.media_class == MEDIA_CLASS_SINK]

    def playback_streams(self) -> List[GraphNode]:
        return [n for n in self.nodes.values() if n.media_class == MEDIA_CLASS_STREAM_OUTPUT]

    def node_by_name(self, node_name: str) -> Optional[GraphNode]:
        """The single node with this name, or ``None`` if absent or ambiguous.

        Ambiguity returns ``None`` rather than a first match. Two sinks sharing
        a node name should not happen, and if it ever does, refusing to act is
        the only answer that cannot be silently wrong.
        """
        found = [n for n in self.nodes.values() if n.node_name == node_name]
        return found[0] if len(found) == 1 else None


# ---------------------------------------------------------------------------
# the backend
# ---------------------------------------------------------------------------


def _as_int(value: Any) -> Optional[int]:
    """``int`` from a JSON value that may be a string, or ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _first_param(params: Mapping[str, Any], name: str) -> Optional[Mapping[str, Any]]:
    entries = params.get(name)
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                return entry
    return None


def _all_params(params: Mapping[str, Any], name: str) -> Tuple[Mapping[str, Any], ...]:
    entries = params.get(name)
    if not isinstance(entries, list):
        return ()
    return tuple(entry for entry in entries if isinstance(entry, dict))


class WirePlumberBackend:
    """Reads the PipeWire graph and performs the three supported mixer actions.

    ``runner`` is injected so tests drive the whole stack without a sound
    server. It is called as ``runner(argv, timeout=...)`` and must return an
    object with ``returncode``, ``stdout`` and ``stderr`` — the shape
    :func:`~cofferdam.workstation.adapters.base.run_fixed` already returns.
    """

    name = BACKEND_NAME

    def __init__(self, runner=run_fixed, which=shutil.which) -> None:
        self._run = runner
        self._which = which

    # -- availability --------------------------------------------------------

    def available(self) -> Tuple[bool, Optional[str]]:
        """Whether both required programs exist, and which one is missing."""
        for program in (PW_DUMP, WPCTL):
            if not self._which(program):
                return False, (
                    f"the '{program}' program is not installed, so this host's audio graph "
                    "cannot be read"
                )
        return True, None

    # -- reading -------------------------------------------------------------

    def read_graph(self) -> Graph:
        """One dump of the graph, parsed into the handful of shapes above."""
        available, reason = self.available()
        if not available:
            raise AdapterUnsupported(reason or "no audio backend on this host")

        completed = self._run([PW_DUMP], timeout=GRAPH_TIMEOUT_SECONDS)
        if completed.returncode != 0:
            # stderr is not forwarded: it is a program's diagnostic text, and
            # the error envelope is a bounded, code-owned vocabulary.
            raise AdapterError("could not read the audio graph")

        raw = completed.stdout
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            objects = json.loads(raw or "[]")
        except ValueError as exc:
            raise AdapterError("the audio graph could not be parsed", exc) from exc
        if not isinstance(objects, list):
            raise AdapterError("the audio graph had an unexpected shape")

        return self._parse(objects)

    def _parse(self, objects: Sequence[Any]) -> Graph:
        cookie: Optional[int] = None
        server_version: Optional[str] = None
        nodes: Dict[int, GraphNode] = {}
        devices: Dict[int, GraphDevice] = {}
        clients: Dict[int, Mapping[str, Any]] = {}
        links: List[Tuple[int, int]] = []
        default_sink_name: Optional[str] = None
        warnings: List[str] = []

        for entry in objects:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("type")
            object_id = _as_int(entry.get("id"))
            info = entry.get("info")
            info = info if isinstance(info, dict) else {}
            props = info.get("props")
            if not isinstance(props, dict):
                # Metadata objects carry their properties at the top level and
                # publish no ``info`` at all, unlike nodes and devices. Looking
                # in both places is what lets the default-sink lookup work at
                # all — reading only ``info.props`` silently found no default
                # and reported a host with a perfectly good speaker as having
                # none.
                props = entry.get("props")
                props = props if isinstance(props, dict) else {}
            params = info.get("params")
            params = params if isinstance(params, dict) else {}

            if kind == TYPE_CORE:
                cookie = _as_int(info.get("cookie"))
                version = info.get("version")
                server_version = version if isinstance(version, str) else None

            elif kind == TYPE_NODE and object_id is not None:
                node_name = props.get("node.name")
                mixer = _first_param(params, "Props") or {}
                volumes = mixer.get("channelVolumes")
                channel_map = mixer.get("channelMap")
                mute = mixer.get("mute")
                state = info.get("state")
                nodes[object_id] = GraphNode(
                    node_id=object_id,
                    object_serial=_as_int(props.get("object.serial")),
                    node_name=node_name if isinstance(node_name, str) else None,
                    props=props,
                    state=state if isinstance(state, str) else None,
                    mute=mute if isinstance(mute, bool) else None,
                    channel_volumes=tuple(
                        float(v) for v in volumes if isinstance(v, (int, float))
                    )
                    if isinstance(volumes, list)
                    else (),
                    channel_map=tuple(c for c in channel_map if isinstance(c, str))
                    if isinstance(channel_map, list)
                    else (),
                )

            elif kind == TYPE_DEVICE and object_id is not None:
                devices[object_id] = GraphDevice(
                    device_id=object_id,
                    props=props,
                    active_profile=_first_param(params, "Profile"),
                    routes=_all_params(params, "EnumRoute"),
                    active_routes=_all_params(params, "Route"),
                )

            elif kind == TYPE_CLIENT and object_id is not None:
                clients[object_id] = props

            elif kind == TYPE_LINK:
                source = _as_int(info.get("output-node-id"))
                target = _as_int(info.get("input-node-id"))
                if source is not None and target is not None:
                    links.append((source, target))

            elif kind == TYPE_METADATA:
                if props.get("metadata.name") != METADATA_DEFAULT:
                    continue
                entries = entry.get("metadata")
                if not isinstance(entries, list):
                    continue
                for item in entries:
                    if not isinstance(item, dict) or item.get("key") != KEY_DEFAULT_SINK:
                        continue
                    value = item.get("value")
                    # WirePlumber stores ``{"name": "<node.name>"}``. A bare
                    # string is accepted too rather than dropped, because the
                    # shape is a daemon detail and losing the default over it
                    # would be a worse failure than tolerating both.
                    if isinstance(value, dict):
                        name = value.get("name")
                        if isinstance(name, str):
                            default_sink_name = name
                    elif isinstance(value, str):
                        default_sink_name = value

        if cookie is None:
            warnings.append(
                "this audio graph published no cookie, so a graph restart cannot be detected"
            )
        return Graph(
            cookie=cookie,
            server_version=server_version,
            nodes=nodes,
            devices=devices,
            clients=clients,
            default_sink_name=default_sink_name,
            links=tuple(dict.fromkeys(links)),
            warnings=tuple(warnings),
        )

    def read_volume(self, node_id: int) -> Tuple[Optional[int], Optional[bool]]:
        """``(percent, muted)`` for one node, as ``wpctl`` reports them.

        The percentage is authoritative — it is the scale the setter uses. The
        mute flag returned here is only ``wpctl``'s marker; the discovery layer
        prefers the graph's own boolean and uses this to corroborate.

        A node that has gone away between the graph read and this call yields
        ``(None, None)`` rather than raising: one disappearing output must not
        take the whole snapshot down.
        """
        if not isinstance(node_id, int) or isinstance(node_id, bool) or node_id < 0:
            # Defensive: a node id is always backend-derived, never client text.
            raise AdapterError("invalid node id")
        completed = self._run([WPCTL, "get-volume", str(node_id)], timeout=ACTION_TIMEOUT_SECONDS)
        if completed.returncode != 0:
            return None, None
        return self._parse_volume(completed.stdout)

    @staticmethod
    def _parse_volume(raw: Any) -> Tuple[Optional[int], Optional[bool]]:
        """Parse ``Volume: 0.25`` / ``Volume: 0.25 [MUTED]``.

        Tolerant about everything except the number: an unparseable line yields
        ``None`` and the caller reports the volume as unknown, which is a true
        statement, rather than guessing a figure a user might act on.
        """
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        if not isinstance(raw, str):
            return None, None
        text = raw.strip()
        if not text:
            return None, None
        muted = "MUTED" in text.upper()
        marker = text.lower().find("volume:")
        if marker < 0:
            return None, muted or None
        remainder = text[marker + len("volume:") :].strip()
        token = remainder.split()[0] if remainder.split() else ""
        try:
            value = float(token)
        except ValueError:
            return None, muted or None
        if value < 0:
            return None, muted or None
        # The published range stops at 100%. A host left above unity by another
        # tool is reported *at* the ceiling rather than above it, and the
        # discovery layer raises a warning so the number is never silently wrong.
        return min(100, int(round(value * 100))), muted

    # -- writing -------------------------------------------------------------
    #
    # Three actions, each a fixed argv whose only variable parts are a
    # backend-derived node id and an already-validated integer.

    def set_default_sink(self, node_id: int) -> None:
        self._act([WPCTL, "set-default", str(int(node_id))], "could not select that output")

    def set_volume_percent(self, node_id: int, percent: int) -> None:
        if not isinstance(percent, int) or isinstance(percent, bool):  # pragma: no cover
            raise AdapterError("invalid volume")
        if percent < 0 or percent > 100:
            # Belt and braces. The action layer rejects out-of-range input long
            # before here; this makes it impossible for a future caller to reach
            # amplification through this backend by mistake.
            raise AdapterError("volume out of range")
        self._act(
            [WPCTL, "set-volume", str(int(node_id)), f"{percent}%"],
            "could not change the volume",
        )

    def set_mute(self, node_id: int, muted: bool) -> None:
        self._act(
            [WPCTL, "set-mute", str(int(node_id)), "1" if muted else "0"],
            "could not change mute",
        )

    def _act(self, argv: Sequence[str], failure: str) -> None:
        completed = self._run(list(argv), timeout=ACTION_TIMEOUT_SECONDS)
        if completed.returncode != 0:
            raise AdapterError(failure)


__all__ = [
    "ACTION_TIMEOUT_SECONDS",
    "BACKEND_NAME",
    "GRAPH_TIMEOUT_SECONDS",
    "Graph",
    "GraphDevice",
    "GraphNode",
    "KEY_DEFAULT_SINK",
    "MEDIA_CLASS_SINK",
    "MEDIA_CLASS_STREAM_OUTPUT",
    "METADATA_DEFAULT",
    "WirePlumberBackend",
    "WPCTL",
    "PW_DUMP",
]
