"""Fixtures for the audio-control tests (M2C).

Two doubles, both built to be exercised through the real code rather than
around it.

* **A pw-dump-shaped graph.** :func:`graph_json` emits the structure ``pw-dump``
  actually produces on the target host, including the two details that broke
  the first implementation: ``Metadata`` objects carry ``props`` at the top
  level with no ``info`` key at all, while nodes and devices carry theirs under
  ``info``. A fixture that emitted one tidy shape would have hidden that.

* **A stateful fake ``wpctl``.** :class:`FakeAudioHost` holds volumes, mutes and
  the default sink, and mutates them when a ``wpctl`` command arrives — so
  "set the volume, then read it back" runs through the same
  write-then-observe path as the real host. It can also be told to *accept a
  command and not apply it*, which is the only way to test that the code
  reports the truth instead of the request.

Standard library only, so these run on the stdlib-only CI path.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

# Values that stand in for a real graph. The cookie is arbitrary; what matters
# is that changing it is what a PipeWire restart looks like.
DEFAULT_COOKIE = 111222333

SPEAKER_NAME = "alsa_output.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__Speaker__sink"
HDMI_NAME = "alsa_output.pci-0000_01_00.1.hdmi-stereo"
BLUETOOTH_NAME = "bluez_output.00_11_22_33_44_55.1"


def device(
    device_id: int,
    name: str,
    description: str,
    api: str = "alsa",
    bus: str = "pci",
    profile: Optional[Dict[str, Any]] = None,
    routes: Sequence[Dict[str, Any]] = (),
    active_routes: Sequence[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    """One ``PipeWire:Interface:Device``, shaped as ``pw-dump`` emits it."""
    return {
        "id": device_id,
        "type": "PipeWire:Interface:Device",
        "info": {
            "props": {
                "device.api": api,
                "device.bus": bus,
                "device.name": name,
                "device.description": description,
                "media.class": "Audio/Device",
                "object.serial": device_id,
            },
            "params": {
                "Profile": [profile] if profile else [],
                "EnumRoute": list(routes),
                "Route": list(active_routes),
            },
        },
    }


def sink(
    node_id: int,
    node_name: str,
    description: str,
    device_id: Optional[int] = None,
    object_serial: Optional[int] = None,
    profile_device: int = 0,
    channel_volumes: Sequence[float] = (0.5, 0.5),
    channel_map: Sequence[str] = ("FL", "FR"),
    mute: bool = False,
    nick: Optional[str] = None,
) -> Dict[str, Any]:
    """One ``Audio/Sink`` node."""
    props: Dict[str, Any] = {
        "media.class": "Audio/Sink",
        "node.name": node_name,
        "node.description": description,
        "object.serial": node_id if object_serial is None else object_serial,
        "card.profile.device": profile_device,
    }
    if device_id is not None:
        props["device.id"] = device_id
    if nick:
        props["node.nick"] = nick
    return {
        "id": node_id,
        "type": "PipeWire:Interface:Node",
        "info": {
            "state": "suspended",
            "props": props,
            "params": {
                "Props": [
                    {
                        "volume": 1.0,
                        "mute": mute,
                        "channelVolumes": list(channel_volumes),
                        "channelMap": list(channel_map),
                    }
                ]
            },
        },
    }


def stream(
    node_id: int,
    client_id: int,
    application_name: str,
    object_serial: Optional[int] = None,
    media_role: str = "Music",
    state: str = "running",
    channel_volumes: Sequence[float] = (1.0, 1.0),
    mute: bool = False,
    media_name: str = "A Track Title Nobody Should See",
) -> Dict[str, Any]:
    """One playback stream node.

    ``media_name`` defaults to something obviously private on purpose: several
    tests assert that this exact string never reaches a published payload, and a
    fixture that omitted it could not prove anything.
    """
    return {
        "id": node_id,
        "type": "PipeWire:Interface:Node",
        "info": {
            "state": state,
            "props": {
                "media.class": "Stream/Output/Audio",
                "node.name": application_name.lower(),
                "application.name": application_name,
                "media.role": media_role,
                "media.name": media_name,
                "client.id": client_id,
                "object.serial": node_id if object_serial is None else object_serial,
            },
            "params": {
                "Props": [
                    {
                        "volume": 1.0,
                        "mute": mute,
                        "channelVolumes": list(channel_volumes),
                        "channelMap": ["FL", "FR"],
                    }
                ]
            },
        },
    }


def client(
    client_id: int,
    application_name: str,
    pid: Optional[int] = None,
    binary: Optional[str] = None,
    media_name: str = "A Track Title Nobody Should See",
) -> Dict[str, Any]:
    """One client object.

    ``pipewire.sec.pid`` is the daemon-written peer credential and is the only
    field the association logic trusts; ``application.name`` and
    ``application.process.binary`` are client-declared and are included so tests
    can prove they are *not* sufficient on their own.
    """
    props: Dict[str, Any] = {
        "application.name": application_name,
        "media.name": media_name,
    }
    if pid is not None:
        props["pipewire.sec.pid"] = pid
        props["application.process.id"] = pid
    if binary is not None:
        props["application.process.binary"] = binary
    return {"id": client_id, "type": "PipeWire:Interface:Client", "info": {"props": props}}


def link(output_node: int, input_node: int, link_id: int = 900) -> Dict[str, Any]:
    return {
        "id": link_id,
        "type": "PipeWire:Interface:Link",
        "info": {"output-node-id": output_node, "input-node-id": input_node},
    }


def default_metadata(sink_name: Optional[str]) -> Dict[str, Any]:
    """The ``default`` metadata object.

    Note the shape: ``props`` at the **top level**, no ``info`` key. This is
    what ``pw-dump`` really emits for metadata, and reproducing it here is what
    keeps the parser honest about where to look.
    """
    entries = []
    if sink_name is not None:
        entries.append(
            {"subject": 0, "key": "default.audio.sink", "value": {"name": sink_name}}
        )
    return {
        "id": 41,
        "type": "PipeWire:Interface:Metadata",
        "props": {"metadata.name": "default"},
        "metadata": entries,
    }


def core(cookie: int = DEFAULT_COOKIE, version: str = "1.6.2") -> Dict[str, Any]:
    return {
        "id": 0,
        "type": "PipeWire:Interface:Core",
        "info": {"cookie": cookie, "version": version, "name": "pipewire-0", "props": {}},
    }


# -- ready-made route/profile shapes -----------------------------------------

SPEAKER_ROUTE = {
    "index": 0,
    "name": "[Out] Speaker",
    "description": "Speaker",
    "direction": "Output",
    "available": "unknown",
    "devices": [0],
    "device": 0,
}
HDMI_ROUTE = {
    "index": 0,
    "name": "hdmi-output-0",
    "description": "HDMI / DisplayPort",
    "direction": "Output",
    "available": "yes",
    "devices": [4],
    "device": 4,
}
HIFI_PROFILE = {"index": 2, "name": "HiFi (Mic1, Mic2, Speaker)", "description": "Play HiFi quality Music"}
OFF_PROFILE = {"index": 0, "name": "off", "description": "Off"}


def builtin_device(device_id: int = 52) -> Dict[str, Any]:
    return device(
        device_id,
        "alsa_card.pci-0000_00_1f.3",
        "Raptor Lake-P/U/H cAVS",
        profile=HIFI_PROFILE,
        routes=[SPEAKER_ROUTE],
        active_routes=[SPEAKER_ROUTE],
    )


def hdmi_device(device_id: int = 51) -> Dict[str, Any]:
    return device(
        device_id,
        "alsa_card.pci-0000_01_00.1",
        "AD107 High Definition Audio Controller",
        profile=HIFI_PROFILE,
        routes=[HDMI_ROUTE],
        active_routes=[HDMI_ROUTE],
    )


def graph_json(objects: Sequence[Dict[str, Any]]) -> str:
    return json.dumps(list(objects))


def simple_graph(
    cookie: int = DEFAULT_COOKIE,
    default_sink: Optional[str] = SPEAKER_NAME,
    with_hdmi: bool = False,
    streams: Sequence[Dict[str, Any]] = (),
    clients: Sequence[Dict[str, Any]] = (),
    links: Sequence[Dict[str, Any]] = (),
    speaker_serial: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """A one- or two-output graph, which is what the target host looks like."""
    objects: List[Dict[str, Any]] = [core(cookie), builtin_device()]
    objects.append(
        sink(58, SPEAKER_NAME, "Raptor Lake-P/U/H cAVS Speaker", device_id=52,
             object_serial=speaker_serial, profile_device=0)
    )
    if with_hdmi:
        objects.append(hdmi_device())
        objects.append(
            sink(70, HDMI_NAME, "AD107 Digital Stereo (HDMI)", device_id=51, profile_device=4)
        )
    objects.extend(streams)
    objects.extend(clients)
    objects.extend(links)
    objects.append(default_metadata(default_sink))
    return objects


# ---------------------------------------------------------------------------
# a fake host
# ---------------------------------------------------------------------------


class Completed:
    """The shape ``run_fixed`` returns."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeAudioHost:
    """A tiny stateful stand-in for ``pw-dump`` plus ``wpctl``.

    It holds the state a real host holds and changes it when a command arrives,
    so a test exercises the real resolve/act/observe loop rather than asserting
    against a canned reply.

    The dishonesty knobs are the point of it:

    ``ignore_writes``
        Accept every command with exit code 0 and change nothing. This is a host
        that says yes and does nothing, and the code must report that truthfully
        instead of echoing the request back as success.
    ``volume_ceiling``
        Clamp applied volumes. A route that will not go above a level is a real
        thing, and the reported state must be the clamped one.
    ``graph_mutator``
        Called with the object list before each dump, so a test can make the
        graph change between the snapshot and the action — the race the
        re-verification step exists to catch.
    """

    def __init__(
        self,
        objects: Optional[Sequence[Dict[str, Any]]] = None,
        volumes: Optional[Dict[int, int]] = None,
        mutes: Optional[Dict[int, bool]] = None,
        default_sink: Optional[str] = SPEAKER_NAME,
        ignore_writes: bool = False,
        volume_ceiling: Optional[int] = None,
        missing_programs: Sequence[str] = (),
        dump_returncode: int = 0,
        dump_stdout: Optional[str] = None,
    ) -> None:
        self.objects = list(objects if objects is not None else simple_graph(default_sink=default_sink))
        self.volumes = dict(volumes or {58: 50})
        self.mutes = dict(mutes or {58: False})
        self.default_sink = default_sink
        self.ignore_writes = ignore_writes
        self.volume_ceiling = volume_ceiling
        self.missing_programs = set(missing_programs)
        self.dump_returncode = dump_returncode
        self.dump_stdout = dump_stdout
        self.graph_mutator = None
        self.calls: List[List[str]] = []

    # -- the two program surfaces ------------------------------------------

    def which(self, program: str) -> Optional[str]:
        return None if program in self.missing_programs else "/usr/bin/" + program

    def run(self, argv: Sequence[str], timeout: int = 0, env=None) -> Completed:
        argv = list(argv)
        self.calls.append(argv)
        program = argv[0]

        if program == "pw-dump":
            if self.dump_stdout is not None:
                return Completed(self.dump_returncode, self.dump_stdout)
            if self.dump_returncode != 0:
                return Completed(self.dump_returncode, "")
            if self.graph_mutator is not None:
                self.graph_mutator(self)
            return Completed(0, graph_json(self._objects_with_state()))

        if program == "wpctl":
            return self._wpctl(argv)

        raise AssertionError(f"the audio code should never run {program!r}")

    # -- wpctl -------------------------------------------------------------

    def _wpctl(self, argv: List[str]) -> Completed:
        command = argv[1] if len(argv) > 1 else ""
        if command == "get-volume":
            node = int(argv[2])
            if node not in self.volumes:
                return Completed(1, "", "Node not found")
            text = "Volume: %.2f" % (self.volumes[node] / 100.0)
            if self.mutes.get(node):
                text += " [MUTED]"
            return Completed(0, text + "\n")

        if command == "set-volume":
            if not self.ignore_writes:
                node = int(argv[2])
                raw = argv[3]
                assert raw.endswith("%"), "volume must be sent as a percentage"
                value = int(raw[:-1])
                if self.volume_ceiling is not None:
                    value = min(value, self.volume_ceiling)
                self.volumes[node] = value
            return Completed(0, "")

        if command == "set-mute":
            if not self.ignore_writes:
                self.mutes[int(argv[2])] = argv[3] == "1"
            return Completed(0, "")

        if command == "set-default":
            if not self.ignore_writes:
                node = int(argv[2])
                for entry in self.objects:
                    if entry.get("id") == node and entry.get("type") == "PipeWire:Interface:Node":
                        self.default_sink = (entry["info"]["props"]).get("node.name")
            return Completed(0, "")

        raise AssertionError(f"unexpected wpctl command {command!r}")

    # -- state projection --------------------------------------------------

    def _objects_with_state(self) -> List[Dict[str, Any]]:
        """The object list with live mute state and the current default folded in."""
        objects = []
        for entry in self.objects:
            if entry.get("type") == "PipeWire:Interface:Metadata":
                objects.append(default_metadata(self.default_sink))
                continue
            if (
                entry.get("type") == "PipeWire:Interface:Node"
                and entry.get("info", {}).get("props", {}).get("media.class") == "Audio/Sink"
            ):
                copied = json.loads(json.dumps(entry))
                node_id = copied["id"]
                if node_id in self.mutes:
                    copied["info"]["params"]["Props"][0]["mute"] = self.mutes[node_id]
                objects.append(copied)
                continue
            objects.append(entry)
        return objects


class FakeProcess:
    """The subset of ``ProcessFacts`` the association logic reads."""

    def __init__(self, pid: int, executable_path: Optional[str], start_ticks: Optional[int] = 4242):
        self.pid = pid
        self.executable_path = executable_path
        self.start_ticks = start_ticks


def process_reader(table: Dict[int, Optional[FakeProcess]]):
    """A ``read_process`` stand-in driven by a pid -> facts table."""

    def read(pid: int, proc_root: str = "/proc"):
        return table.get(pid)

    return read


__all__ = [
    "BLUETOOTH_NAME",
    "Completed",
    "DEFAULT_COOKIE",
    "FakeAudioHost",
    "FakeProcess",
    "HDMI_NAME",
    "HDMI_ROUTE",
    "HIFI_PROFILE",
    "OFF_PROFILE",
    "SPEAKER_NAME",
    "SPEAKER_ROUTE",
    "builtin_device",
    "client",
    "core",
    "default_metadata",
    "device",
    "graph_json",
    "hdmi_device",
    "link",
    "process_reader",
    "simple_graph",
    "sink",
    "stream",
]
