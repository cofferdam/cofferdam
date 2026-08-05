"""Fixtures for the runtime-inventory tests (M2B).

Two kinds of double live here:

* **A fake ``/proc``.** Real directories and files with the exact shapes the
  kernel writes, so ``ProcessDiscovery`` is exercised through its real parsing
  rather than through a mock that agrees with it. That matters: the ``stat``
  parser has to survive an executable name containing spaces and parentheses,
  which only a real file can demonstrate.

* **A real EDID builder.** :func:`build_edid` emits byte-correct 128-byte
  blocks, checksum included, so the EDID parser is tested against structure
  rather than against a dictionary the test wrote. A test that asserts "an
  absent serial is not invented" is worthless if the fixture never produced a
  block with an absent serial.

Everything is standard-library only, so these tests run on the stdlib-only CI
path alongside the Trust Core suite.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

# ---------------------------------------------------------------------------
# EDID
# ---------------------------------------------------------------------------

EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"

DESCRIPTOR_MONITOR_NAME = 0xFC
DESCRIPTOR_MONITOR_SERIAL = 0xFF


def _pack_manufacturer(code: str) -> bytes:
    """Three letters into the five-bits-each encoding EDID uses."""
    letters = [ord(char.upper()) - ord("A") + 1 for char in code[:3]]
    packed = (letters[0] << 10) | (letters[1] << 5) | letters[2]
    return bytes(((packed >> 8) & 0xFF, packed & 0xFF))


def _text_descriptor(tag: int, text: str) -> bytes:
    body = text.encode("ascii")[:13]
    if len(body) < 13:
        body = body + b"\n" + b" " * (12 - len(body))
    return bytes((0, 0, 0, tag, 0)) + body


def _timing_descriptor(width_mm: int, height_mm: int) -> bytes:
    """A detailed-timing descriptor carrying only the physical size.

    The timing numbers are placeholders; the parser reads bytes 12-14 for
    millimetres and nothing else from this block.
    """
    block = bytearray(18)
    block[0] = 0x01  # non-zero: this is a detailed timing, not a text descriptor
    block[1] = 0x1D
    block[12] = width_mm & 0xFF
    block[13] = height_mm & 0xFF
    block[14] = ((width_mm >> 8) & 0x0F) << 4 | ((height_mm >> 8) & 0x0F)
    return bytes(block)


def build_edid(
    manufacturer: str = "AAA",
    product_code: int = 0x1234,
    serial_number: int = 0,
    model_name: Optional[str] = None,
    serial_text: Optional[str] = None,
    width_mm: Optional[int] = None,
    height_mm: Optional[int] = None,
    width_cm: int = 0,
    height_cm: int = 0,
) -> bytes:
    """A valid 128-byte EDID base block.

    ``model_name`` and ``serial_text`` are optional on purpose: a panel that
    publishes neither is the case the "do not invent values" rule is about, and
    the fixture has to be able to produce it.
    """
    block = bytearray(128)
    block[0:8] = EDID_HEADER
    block[8:10] = _pack_manufacturer(manufacturer)
    block[10] = product_code & 0xFF
    block[11] = (product_code >> 8) & 0xFF
    block[12:16] = serial_number.to_bytes(4, "little")
    block[18] = 1  # EDID 1.4
    block[19] = 4
    block[21] = width_cm
    block[22] = height_cm

    descriptors: List[bytes] = []
    if width_mm and height_mm:
        descriptors.append(_timing_descriptor(width_mm, height_mm))
    if model_name is not None:
        descriptors.append(_text_descriptor(DESCRIPTOR_MONITOR_NAME, model_name))
    if serial_text is not None:
        descriptors.append(_text_descriptor(DESCRIPTOR_MONITOR_SERIAL, serial_text))
    while len(descriptors) < 4:
        descriptors.append(bytes(18))

    for index, offset in enumerate((54, 72, 90, 108)):
        block[offset : offset + 18] = descriptors[index]

    block[126] = 0  # no extension blocks
    block[127] = (-sum(block[:127])) & 0xFF
    return bytes(block)


def write_drm_tree(root: Path, connectors: Sequence[dict]) -> Path:
    """Build a fake ``/sys/class/drm``.

    Each entry is ``{"name": "eDP-1", "status": "connected", "enabled":
    "enabled", "edid": b"..."}``; ``edid`` may be ``None`` for a connector that
    publishes none, which is how a real disconnected port behaves.
    """
    root.mkdir(parents=True, exist_ok=True)
    # Devices that are not connectors. Present so the scanner is shown skipping
    # them rather than being handed a pre-filtered directory.
    (root / "card1").mkdir(exist_ok=True)
    (root / "renderD128").mkdir(exist_ok=True)
    (root / "version").write_text("drm 1.1.0\n", encoding="utf-8")

    for entry in connectors:
        directory = root / ("card1-" + entry["name"])
        directory.mkdir(exist_ok=True)
        (directory / "status").write_text(entry.get("status", "connected") + "\n", encoding="utf-8")
        (directory / "enabled").write_text(entry.get("enabled", "enabled") + "\n", encoding="utf-8")
        (directory / "edid").write_bytes(entry.get("edid") or b"")
    return root


# ---------------------------------------------------------------------------
# Mutter replies
# ---------------------------------------------------------------------------


def variant(type_code: str, data):
    return {"type": type_code, "data": data}


def monitor(
    connector: str,
    vendor: str,
    product: str,
    serial: str,
    width: int = 1920,
    height: int = 1080,
    refresh: float = 60.0,
    is_builtin: Optional[bool] = None,
    display_name: Optional[str] = None,
):
    """One monitor entry in ``GetCurrentState``'s reply shape."""
    properties: Dict[str, dict] = {}
    if is_builtin is not None:
        properties["is-builtin"] = variant("b", is_builtin)
    if display_name is not None:
        properties["display-name"] = variant("s", display_name)
    mode = [
        "%dx%d@%.3f" % (width, height, refresh),
        width,
        height,
        refresh,
        1.0,
        [1.0, 2.0],
        {"is-current": variant("b", True), "is-preferred": variant("b", True)},
    ]
    return [[connector, vendor, product, serial], [mode], properties]


def logical_monitor(monitors, x=0, y=0, scale=1.0, transform=0, primary=False):
    return [x, y, scale, transform, primary, [entry[0] for entry in monitors], {}]


def current_state(monitors: Iterable, logicals: Iterable):
    """The four-field reply ``GetCurrentState`` returns."""
    monitors = list(monitors)
    return [1, monitors, list(logicals), {}]


# ---------------------------------------------------------------------------
# /proc
# ---------------------------------------------------------------------------


class FakeProc:
    """A writable fake ``/proc`` that ``ProcessDiscovery`` reads for real."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def add(
        self,
        pid: int,
        comm: str = "worker",
        ppid: int = 1,
        start_ticks: int = 1000,
        state: str = "S",
        cgroup: str = "0::/user.slice/user-1000.slice/user@1000.service/init.scope",
        executable: Optional[str] = None,
    ) -> Path:
        directory = self.root / str(pid)
        directory.mkdir(exist_ok=True)
        # Real ``stat`` layout. Fields are numbered from 1: pid, (comm), state,
        # ppid, … and starttime is field 22. After the closing parenthesis the
        # remainder starts at field 3, so state and ppid are followed by
        # seventeen fields before starttime. The filler values are never read;
        # their *count* is what makes the parser's index correct, so getting it
        # wrong here would silently weaken every start-time assertion.
        fields = [str(pid), "(" + comm + ")", state, str(ppid)]
        fields.extend(["0"] * 17)
        fields.append(str(start_ticks))
        fields.extend(["0"] * 30)
        (directory / "stat").write_text(" ".join(fields) + "\n", encoding="utf-8")
        (directory / "comm").write_text(comm + "\n", encoding="utf-8")
        (directory / "cgroup").write_text(cgroup + "\n", encoding="utf-8")
        if executable:
            link = directory / "exe"
            if link.is_symlink() or link.exists():
                link.unlink()
            # Deliberately dangling: the target need not exist for readlink,
            # and a real /proc/<pid>/exe often points at a deleted file.
            os.symlink(executable, str(link))
        return directory

    def remove(self, pid: int) -> None:
        """Make a process vanish, as one does mid-scan."""
        directory = self.root / str(pid)
        for child in sorted(directory.iterdir()):
            if child.is_symlink():
                child.unlink()
            else:
                child.unlink()
        directory.rmdir()


def app_scope(unit: str) -> str:
    """A cgroup path for a launched application: a scope inside app.slice."""
    return "0::/user.slice/user-1000.slice/user@1000.service/app.slice/" + unit


def session_cgroup(unit: str = "dbus.service") -> str:
    """A cgroup path for session infrastructure — never an application."""
    return "0::/user.slice/user-1000.slice/user@1000.service/session.slice/" + unit


# ---------------------------------------------------------------------------
# identities and sessions
# ---------------------------------------------------------------------------


class FakeSession:
    """Stands in for ``linux_session.GraphicalSession``."""

    def __init__(self, available=True, session_id="stamp-1", session_type="wayland", reason=None):
        self.available = available
        self.session_id = session_id
        self.session_type = session_type
        self.reason = reason
        self.environment = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}


class FakeBoot:
    """Stands in for ``identity.BootIdentity``."""

    def __init__(self, boot_id="boot-testboot0000", boot_epoch_seconds=1_700_000_000):
        self.boot_id = boot_id
        self.boot_epoch_seconds = boot_epoch_seconds
        self.source = "proc-boot-id"
        self.booted_at = "2023-11-14T22:13:20Z"

    @property
    def available(self) -> bool:
        return self.boot_id is not None

    def to_dict(self):
        return {"boot_id": self.boot_id, "source": self.source, "booted_at": self.booted_at}


class FakeOverlay:
    """Stands in for a ``registries.models.Display`` overlay entry."""

    class Match:
        def __init__(self, edid_sha256=None, manufacturer=None, model=None, serial=None, connector_hint=None):
            self.edid_sha256 = edid_sha256
            self.manufacturer = manufacturer
            self.model = model
            self.serial = serial
            self.connector_hint = connector_hint

    def __init__(self, id, name, aliases=(), enabled=True, **match):
        self.id = id
        self.name = name
        self.aliases = tuple(aliases)
        self.enabled = enabled
        self.match = self.Match(**match)


HOST_ID = "host-testhost000000"


# ---------------------------------------------------------------------------
# scanning web sources
# ---------------------------------------------------------------------------

_JS_COMMENT = re.compile(r"/\*.*?\*/|(?<![:\w])//[^\n]*", re.S)


def code_only(source: str) -> str:
    """A JavaScript source with its comments removed.

    The web-honesty guards ask what a file can *render*, so they have to scan
    code rather than prose. Without this, a comment explaining "no sample data,
    ever" would trip a scan for the word "sample" — which would push the next
    author to delete the explanation rather than keep the property.
    """
    return _JS_COMMENT.sub(" ", source)


__all__ = [
    "FakeBoot",
    "FakeOverlay",
    "FakeProc",
    "FakeSession",
    "HOST_ID",
    "app_scope",
    "build_edid",
    "code_only",
    "current_state",
    "logical_monitor",
    "monitor",
    "session_cgroup",
    "variant",
    "write_drm_tree",
]
