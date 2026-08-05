"""Turning a PipeWire graph into outputs and streams a phone can safely show.

Two rules shape everything below.

**Only bounded, named fields leave this module.** Output and stream dictionaries
are built by *listing the keys to include*, never by copying a property bag and
removing the bad ones. PipeWire node properties are an open dictionary that any
application can extend, and a stream's ``media.name`` is the track or video
title — so a denylist would be one Spotify release away from leaking what
someone is listening to. An allowlist cannot leak a key nobody wrote down.

**An application is associated only on evidence that the application did not
supply.** ``application.name`` is a string the client chose; anything may call
itself Spotify. The trustworthy field is ``pipewire.sec.pid``, which the
PipeWire daemon writes from the peer credentials of the socket connection and a
client cannot forge. That pid is resolved through ``/proc`` to a real
executable, and only an exact basename match against the adapter's own launch
table produces an association. Everything else stays ``unclassified`` with a
reason, because a wrong attribution — telling someone Spotify is playing when it
is not — is worse than no attribution.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..runtime.identity import BootIdentity, fingerprint
from ..runtime.processes import PROC_PATH, process_resource_id, read_process
from .models import (
    DEVICE_BLUETOOTH,
    DEVICE_BUILTIN_SPEAKER,
    DEVICE_HDMI,
    DEVICE_UNKNOWN,
    DEVICE_USB,
    Evidence,
    ResourceCollection,
    collected,
    unavailable,
)
from .models import KIND_OUTPUTS, KIND_STREAMS
from .wireplumber import BACKEND_NAME, Graph, GraphDevice, GraphNode

# -- identity ----------------------------------------------------------------
#
# Each rule lives in exactly one function so nothing can implement it twice and
# differently.


def graph_identity_value(host_id: str, cookie: int) -> str:
    return "agraph-" + fingerprint("cofferdam.audio.graph", host_id, str(cookie))


def output_resource_id(host_id: str, graph_id: str, node_name: str) -> str:
    """The id an action addresses.

    Graph-scoped on purpose: when PipeWire restarts, every node id in the world
    changes meaning, so every id a client is holding must stop resolving. Naming
    the graph in the digest achieves that without the client having to check
    anything.
    """
    return "aout-" + fingerprint("cofferdam.audio.output", host_id, graph_id, node_name)


def output_stable_id(host_id: str, node_name: str) -> str:
    """The id a *preference* would be keyed by — no graph in the digest.

    Nothing in this milestone writes preferences. The value is published now so
    that when the preferred-output overlay arrives it has a key that already
    survives restarts, rather than a graph-scoped id that would silently rot.
    """
    return "asink-" + fingerprint("cofferdam.audio.sink", host_id, node_name)


def stream_resource_id(host_id: str, graph_id: str, object_serial: int) -> str:
    """A stream's id, keyed by PipeWire's monotonic serial.

    A stream has no durable name — three Opera tabs all present as ``opera`` —
    so the serial does the work. It is allocated once per object and never
    reused within a graph, which is exactly the property a node id lacks.
    """
    return "astream-" + fingerprint("cofferdam.audio.stream", host_id, graph_id, str(object_serial))


# -- device classification ---------------------------------------------------

_ROUTE_HDMI_PREFIX = "hdmi-"


def _sink_pcm_device(node: GraphNode) -> Optional[int]:
    """The card's PCM device index this sink node belongs to.

    Routes are declared against this index, so it is how a sink is matched to
    the physical outlet it drives.
    """
    raw = node.props.get("card.profile.device")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def active_route_for(device: Optional[GraphDevice], node: GraphNode) -> Optional[Mapping[str, Any]]:
    """The output route this sink is currently driving, if the card declares one."""
    if device is None:
        return None
    index = _sink_pcm_device(node)
    if index is None:
        return None
    for route in device.active_routes:
        if route.get("direction") != "Output":
            continue
        if route.get("device") == index:
            return route
    for route in device.routes:
        if route.get("direction") != "Output":
            continue
        devices = route.get("devices")
        if isinstance(devices, list) and index in devices:
            return route
    return None


def classify_device(
    device: Optional[GraphDevice], route: Optional[Mapping[str, Any]]
) -> Tuple[str, str]:
    """``(device_type, evidence)`` from structured properties only.

    No name matching. The signals are the device's API and bus — which the
    kernel and PipeWire set, not the user — and the ACP route name, which comes
    from a fixed profile vocabulary (``hdmi-output-0``, ``[Out] Speaker``) rather
    than from anything a person typed.
    """
    props = device.props if device is not None else {}
    api = props.get("device.api")
    bus = props.get("device.bus")

    if api == "bluez5":
        return DEVICE_BLUETOOTH, "device.api reports the Bluetooth backend"

    route_name = route.get("name") if isinstance(route, Mapping) else None
    if isinstance(route_name, str) and route_name.lower().startswith(_ROUTE_HDMI_PREFIX):
        return DEVICE_HDMI, "the active card route is an HDMI/DisplayPort route"

    if bus == "usb":
        return DEVICE_USB, "device.bus reports USB"
    if bus == "pci" and api == "alsa":
        return DEVICE_BUILTIN_SPEAKER, "an internal PCI sound card"
    return DEVICE_UNKNOWN, "no device API or bus this backend recognises"


# -- outputs -----------------------------------------------------------------

OUTPUT_LIMITATIONS = (
    "a PipeWire node id identifies an object only within the running graph and is reused "
    "after that object is destroyed; actions are addressed by resource_id instead",
    "a sound card whose profile is off — an HDMI port with no display attached, typically — "
    "publishes no output at all and cannot be selected until something is connected to it",
    "volume is read and written on the same perceptual scale the desktop's own slider uses, "
    "and is reported to the nearest whole percent",
)


def _text(props: Mapping[str, Any], key: str) -> Optional[str]:
    """A property, only when it is a non-empty string. Never a placeholder."""
    value = props.get(key)
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


class AudioOutputDiscovery:
    """Builds the outputs collection from one graph read.

    ``read_volume`` is the backend's per-node volume reader, injected so this
    class stays testable without a sound server and so the volume scale lives in
    exactly one place.
    """

    def __init__(self, read_volume) -> None:
        self._read_volume = read_volume

    def collect(self, host_id: str, graph_id: str, graph: Graph) -> Tuple[ResourceCollection, List[str]]:
        items: List[Dict[str, Any]] = []
        warnings: List[str] = []

        evidence = Evidence(
            backend=BACKEND_NAME,
            sources=("pw-dump", "wpctl get-volume"),
            limitations=OUTPUT_LIMITATIONS,
        )

        for node in sorted(graph.sinks(), key=lambda n: n.node_id):
            if not node.node_name:
                # Without a stable name there is nothing to build an identity
                # from, so the honest move is to skip it and say so rather than
                # publish something no action could ever resolve.
                warnings.append(
                    "one output was skipped because it published no stable node name"
                )
                continue

            device_id = node.props.get("device.id")
            device = graph.devices.get(device_id) if isinstance(device_id, int) else None
            route = active_route_for(device, node)
            device_type, type_evidence = classify_device(device, route)

            percent, wpctl_muted = self._read_volume(node.node_id)
            muted = node.mute if node.mute is not None else wpctl_muted
            if (
                node.mute is not None
                and wpctl_muted is not None
                and node.mute != wpctl_muted
            ):
                # Two sources disagreeing is worth surfacing, not smoothing over.
                warnings.append(
                    "one output reported conflicting mute state between the graph and wpctl"
                )
            if percent is None:
                warnings.append("one output's volume could not be read")

            items.append(
                {
                    "resource_id": output_resource_id(host_id, graph_id, node.node_name),
                    "stable_id": output_stable_id(host_id, node.node_name),
                    "identity_stability": "hardware" if device is not None else "weak",
                    # Published as an observation and labelled as transient. It
                    # is never accepted back from a client as authority.
                    "node_id": node.node_id,
                    "node_id_is_transient": True,
                    "object_serial": node.object_serial,
                    "node_name": node.node_name,
                    "display_name": _text(node.props, "node.description")
                    or _text(node.props, "node.nick"),
                    "description": _text(device.props, "device.description") if device else None,
                    "device_type": device_type,
                    "device_type_evidence": type_evidence,
                    "route": route.get("description") if isinstance(route, Mapping) else None,
                    "profile": (device.active_profile or {}).get("description")
                    if device is not None
                    else None,
                    "available": True,
                    "is_default": bool(
                        graph.default_sink_name and node.node_name == graph.default_sink_name
                    ),
                    "volume_percent": percent,
                    "muted": muted,
                    "channels": len(node.channel_map) or None,
                    "channel_map": list(node.channel_map) or None,
                }
            )

        warnings.extend(_inactive_device_warnings(graph))
        return collected(KIND_OUTPUTS, items, evidence, warnings), warnings


def _inactive_device_warnings(graph: Graph) -> List[str]:
    """Explain cards that exist but currently offer no output.

    Without this, an HDMI monitor that is plugged into video but not carrying
    audio simply does not appear, and the user is left to guess whether
    Cofferdam is broken or their cable is. The device description is the card's
    own name — bounded, non-secret, and already visible in the desktop's own
    sound settings.
    """
    warnings: List[str] = []
    live_device_ids = {
        node.props.get("device.id") for node in graph.sinks() if node.props.get("device.id")
    }
    for device in graph.devices.values():
        props = device.props
        if props.get("media.class") != "Audio/Device":
            continue
        if device.device_id in live_device_ids:
            continue
        name = _text(props, "device.description") or _text(props, "device.nick")
        if not name:
            continue
        profile = (device.active_profile or {}).get("name")
        if profile == "off":
            warnings.append(
                f"{name} currently offers no audio output: its card profile is off, which on "
                "an HDMI or DisplayPort card normally means nothing is connected to it"
            )
        else:
            warnings.append(f"{name} currently offers no audio output")
    return warnings


# -- streams -----------------------------------------------------------------

ASSOCIATION_IDENTIFIED = "identified"
ASSOCIATION_UNCLASSIFIED = "unclassified"

STREAM_LIMITATIONS = (
    "a stream is associated with an application only when the kernel-verified process behind "
    "its connection resolves to a known executable; a stream that does not is reported as "
    "unclassified rather than guessed",
    "what is being played — track, video, or page title — is never read or published",
    "a stream's identity lasts only as long as the audio graph it lives in",
)


class AudioStreamDiscovery:
    """Builds the streams collection, associating applications only on evidence.

    ``known_executables`` is the adapter's launch table — logical application key
    to the executable basenames that key may run. An empty table is not a
    failure: every stream is then honestly unclassified, and the outputs half of
    the feature is unaffected.
    """

    def __init__(
        self,
        known_executables: Optional[Mapping[str, Sequence[str]]] = None,
        proc_root: str = PROC_PATH,
        process_reader=read_process,
    ) -> None:
        self._known = {
            key: {str(name) for name in names} for key, names in (known_executables or {}).items()
        }
        self._proc_root = proc_root
        self._read_process = process_reader

    def collect(
        self,
        host_id: str,
        graph_id: str,
        graph: Graph,
        boot: BootIdentity,
        output_id_by_node: Mapping[int, str],
    ) -> ResourceCollection:
        evidence = Evidence(
            backend=BACKEND_NAME,
            sources=("pw-dump", "/proc/<pid>/stat", "/proc/<pid>/exe"),
            limitations=STREAM_LIMITATIONS,
        )
        items: List[Dict[str, Any]] = []
        warnings: List[str] = []

        for node in sorted(graph.playback_streams(), key=lambda n: n.node_id):
            if node.object_serial is None:
                warnings.append(
                    "one stream was skipped because it published no serial to identify it by"
                )
                continue

            sinks = graph.sinks_for_stream(node.node_id)
            current_output: Optional[str] = None
            if len(sinks) == 1:
                current_output = output_id_by_node.get(sinks[0])
            elif len(sinks) > 1:
                warnings.append("one stream is playing to more than one output at once")

            association = self._associate(node, graph, boot, host_id)

            items.append(
                {
                    "resource_id": stream_resource_id(host_id, graph_id, node.object_serial),
                    "node_id": node.node_id,
                    "node_id_is_transient": True,
                    "object_serial": node.object_serial,
                    # Declared by the application, and labelled as such. Shown
                    # because it is what a person recognises; never used to
                    # decide the association below.
                    "declared_application_name": _text(node.props, "application.name"),
                    "media_role": _text(node.props, "media.role"),
                    "state": node.state,
                    "current_output_resource_id": current_output,
                    "current_output_is_known": current_output is not None,
                    "volume_percent": _stream_volume_percent(node),
                    "muted": node.mute,
                    "association": association,
                }
            )

        return collected(KIND_STREAMS, items, evidence, warnings)

    # -- association -------------------------------------------------------

    def _associate(
        self, node: GraphNode, graph: Graph, boot: BootIdentity, host_id: str
    ) -> Dict[str, Any]:
        """Identify the application behind a stream, or decline to.

        The chain is: the stream's client object -> that client's
        ``pipewire.sec.pid``, written by the daemon from socket peer
        credentials -> ``/proc/<pid>/exe`` -> an exact basename match in the
        launch table. Every link must hold. A break anywhere yields
        ``unclassified`` and the reason, which is a useful answer in itself.
        """
        unclassified = lambda reason: {  # noqa: E731 - a local shape, not a policy
            "status": ASSOCIATION_UNCLASSIFIED,
            "application": None,
            "process_resource_id": None,
            "evidence": None,
            "reason": reason,
        }

        client_id = node.props.get("client.id")
        if not isinstance(client_id, int) or client_id not in graph.clients:
            return unclassified("this stream publishes no client to trace back to a process")

        client_props = graph.clients[client_id]
        pid = client_props.get("pipewire.sec.pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return unclassified(
                "the audio server recorded no verified process for this stream's connection"
            )

        facts = self._read_process(pid, self._proc_root)
        if facts is None:
            return unclassified("the process behind this stream could not be read or has exited")
        if not facts.executable_path:
            return unclassified("the executable behind this stream is not readable")

        basename = os.path.basename(facts.executable_path)
        matches = sorted(key for key, names in self._known.items() if basename in names)
        if not matches:
            return unclassified(
                "this stream's executable is not one of the applications Cofferdam knows"
            )
        if len(matches) > 1:
            # Two logical applications claiming one binary is a table problem,
            # and picking one would be a guess.
            return unclassified("this stream's executable maps to more than one known application")

        resource = None
        if boot.available and boot.boot_id and facts.start_ticks is not None:
            resource = process_resource_id(host_id, boot.boot_id, pid, facts.start_ticks)

        return {
            "status": ASSOCIATION_IDENTIFIED,
            "application": matches[0],
            # Ties this stream to the same process resource the runtime
            # inventory publishes, so the two views agree by construction.
            "process_resource_id": resource,
            "evidence": "the audio server's kernel-verified process for this connection runs a "
            "known executable for this application",
            "reason": None,
        }


def _stream_volume_percent(node: GraphNode) -> Optional[int]:
    """A stream's own volume, which is linear and separate from output volume.

    Reported for completeness and never used as, or mixed with, the system
    output volume: a per-application level and the level of the speaker itself
    are different things, and this milestone owns only the second.
    """
    if not node.channel_volumes:
        return None
    highest = max(node.channel_volumes)
    if highest < 0:
        return None
    return min(100, int(round(highest * 100)))


def streams_unavailable(reason: str) -> ResourceCollection:
    return unavailable(KIND_STREAMS, reason, Evidence(backend=BACKEND_NAME))


__all__ = [
    "ASSOCIATION_IDENTIFIED",
    "ASSOCIATION_UNCLASSIFIED",
    "AudioOutputDiscovery",
    "AudioStreamDiscovery",
    "OUTPUT_LIMITATIONS",
    "STREAM_LIMITATIONS",
    "active_route_for",
    "classify_device",
    "graph_identity_value",
    "output_resource_id",
    "output_stable_id",
    "stream_resource_id",
    "streams_unavailable",
]
