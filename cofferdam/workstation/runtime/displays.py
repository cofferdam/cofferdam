"""Which displays are connected to this workstation right now.

Backend selection — and why it is not ``xrandr``
------------------------------------------------
This host runs GNOME on Wayland. Under Wayland, ``xrandr`` talks to XWayland,
which reports a *synthetic* layout maintained for X11 clients: it is derived
from the compositor's real configuration but is not it, and on some
configurations it shows a single merged screen where two physical panels exist.
M1 already used ``xrandr --listmonitors`` for a display *count*, and a count was
the most it could honestly support. Treating that output as the authoritative
description of the session's displays is exactly the kind of "it looked like it
worked" evidence this project has been burned by before, so it is not used here.

Two backends are used instead, and each is used for what it actually knows:

``mutter-displayconfig`` (primary, session-scoped)
    ``org.gnome.Mutter.DisplayConfig.GetCurrentState`` on the session bus. This
    is the compositor answering about its own state: which monitors it sees,
    which are built in, how they are laid out logically, at what scale,
    orientation and refresh rate, and which logical monitor is primary. Nothing
    else on this host can answer the layout questions at all.

``drm-sysfs`` (supplementary, hardware)
    ``/sys/class/drm/*/`` — the kernel's own view. It supplies the raw EDID
    block, which gives the hardware fingerprint that display identity is built
    on, and the physical millimetres that ``GetCurrentState`` does not report.
    It is read directly rather than through the compositor's deprecated
    ``GetResources``.

The two are joined on the panel's own EDID-derived ``(manufacturer, model,
serial)`` triple rather than on connector names, because the names differ
between the two interfaces — the kernel says ``card1-HDMI-A-1`` where Mutter
says ``HDMI-1``. Matching on content is exact; matching on a name mapping would
be a guess maintained by hand. Connector-name matching remains as a fallback
and is recorded in the evidence when it was used.

Session scope
-------------
Displays here are **session-scoped**. Before a graphical login the collection is
``unavailable``, not empty. The kernel would happily list connected connectors
at that point, but "a panel is plugged in" and "the desktop is driving these
displays in this layout" are different claims, and only the second is what the
rest of the product means by a display. Reporting the first as the live
inventory would be a smaller lie than an empty list, but still a lie.

Nothing in this module changes display configuration. ``ApplyMonitorsConfig``
and its relatives are never called.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .dbus import DbusUnavailable, call_method, variant_map
from .edid import EdidInfo, parse_edid
from .identity import fingerprint
from .models import (
    KIND_DISPLAYS,
    STABILITY_HARDWARE,
    STABILITY_WEAK,
    Evidence,
    ResourceCollection,
    collected,
    failed,
    unavailable,
)

BACKEND_MUTTER = "mutter-displayconfig"
BACKEND_DRM_SYSFS = "drm-sysfs"

MUTTER_DESTINATION = "org.gnome.Mutter.DisplayConfig"
MUTTER_OBJECT_PATH = "/org/gnome/Mutter/DisplayConfig"
MUTTER_INTERFACE = "org.gnome.Mutter.DisplayConfig"
MUTTER_GET_CURRENT_STATE = "GetCurrentState"

DRM_CLASS_PATH = "/sys/class/drm"

# An EDID with extensions is a few hundred bytes. This ceiling is generous and
# still refuses to read a pathological sysfs entry into memory.
MAX_EDID_BYTES = 32 * 1024

# Mutter's transform enum. 4-7 are the same rotations with a horizontal flip.
_TRANSFORMS = {
    0: "normal",
    1: "left",
    2: "inverted",
    3: "right",
    4: "flipped",
    5: "flipped-left",
    6: "flipped-inverted",
    7: "flipped-right",
}

# Connector-name prefixes the kernel uses for panels wired inside the chassis.
# Used **only** when the compositor did not report ``is-builtin``; the
# compositor's own answer always wins, and which was used is published.
_INTERNAL_CONNECTOR_PREFIXES = ("eDP", "LVDS", "DSI", "eDP-", "DPI")

AMBIGUOUS_EDID_WARNING = (
    "two displays report identical hardware identity; both are shown, and both are marked "
    "weakly identified"
)

_LIMITATIONS = (
    "displays are session-scoped: before a graphical login this collection is "
    "unavailable rather than empty",
    "a connected panel the compositor has not enabled appears with active=false; "
    "a disconnected connector is not reported at all",
    "physical millimetres and the EDID fingerprint come from the kernel, not from "
    "the compositor, and are absent for a panel whose EDID could not be read",
)


# ---------------------------------------------------------------------------
# the kernel's side
# ---------------------------------------------------------------------------


class DrmConnector:
    """One ``/sys/class/drm/cardN-<connector>`` entry."""

    __slots__ = ("name", "status", "enabled", "edid")

    def __init__(self, name: str, status: Optional[str], enabled: Optional[str], edid: Optional[EdidInfo]):
        self.name = name
        self.status = status
        self.enabled = enabled
        self.edid = edid

    @property
    def connected(self) -> bool:
        return self.status == "connected"


def _read_sysfs_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _read_sysfs_edid(path: Path) -> Optional[EdidInfo]:
    try:
        if path.stat().st_size > MAX_EDID_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        # A disconnected or racing connector: not an error, just no EDID.
        return None
    return parse_edid(data)


def read_drm_connectors(root: str = DRM_CLASS_PATH) -> List[DrmConnector]:
    """Every DRM connector the kernel currently exposes. Never raises."""
    connectors: List[DrmConnector] = []
    try:
        entries = sorted(Path(root).iterdir())
    except OSError:
        return connectors
    for entry in entries:
        # ``cardN-<connector>``; ``cardN`` itself and ``renderD*`` are devices,
        # not connectors.
        name = entry.name
        if "-" not in name or not name.startswith("card"):
            continue
        connector_name = name.split("-", 1)[1]
        connectors.append(
            DrmConnector(
                name=connector_name,
                status=_read_sysfs_text(entry / "status"),
                enabled=_read_sysfs_text(entry / "enabled"),
                edid=_read_sysfs_edid(entry / "edid"),
            )
        )
    return connectors


def normalize_connector(name: str) -> str:
    """Fold the kernel's connector spelling towards the compositor's.

    The kernel distinguishes HDMI type A from type B and DVI-I from DVI-D;
    userspace display servers historically do not. This is a *fallback* join
    key, used only when the EDID triple did not match, and the evidence records
    when it was relied on.
    """
    folded = name.strip()
    for prefix, replacement in (("HDMI-A-", "HDMI-"), ("HDMI-B-", "HDMI-"), ("DVI-D-", "DVI-"), ("DVI-I-", "DVI-")):
        if folded.startswith(prefix):
            folded = replacement + folded[len(prefix) :]
            break
    return folded.lower()


# ---------------------------------------------------------------------------
# the compositor's side
# ---------------------------------------------------------------------------


def _monitor_spec(raw: Any) -> Optional[Tuple[str, str, str, str]]:
    """``(connector, vendor, product, serial)`` — the compositor's monitor key."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    connector, vendor, product, serial = (str(value) for value in raw[:4])
    return connector, vendor, product, serial


def _current_mode(modes: Any) -> Dict[str, Any]:
    """The mode flagged ``is-current``, reduced to what a UI shows."""
    if not isinstance(modes, (list, tuple)):
        return {}
    for mode in modes:
        if not isinstance(mode, (list, tuple)) or len(mode) < 6:
            continue
        properties = variant_map(mode[6] if len(mode) > 6 else {})
        if not properties.get("is-current"):
            continue
        return {
            "mode_id": str(mode[0]),
            "width": int(mode[1]) if isinstance(mode[1], (int, float)) else None,
            "height": int(mode[2]) if isinstance(mode[2], (int, float)) else None,
            "refresh_rate_hz": round(float(mode[3]), 3) if isinstance(mode[3], (int, float)) else None,
        }
    return {}


class _LogicalPlacement:
    """Where the compositor put a monitor, if it enabled it at all."""

    __slots__ = ("x", "y", "scale", "transform", "primary")

    def __init__(self, x, y, scale, transform, primary):
        self.x = x
        self.y = y
        self.scale = scale
        self.transform = transform
        self.primary = primary


def _logical_placements(raw: Any) -> Dict[Tuple[str, str, str, str], _LogicalPlacement]:
    """Map each monitor spec to the logical monitor that is driving it."""
    placements: Dict[Tuple[str, str, str, str], _LogicalPlacement] = {}
    if not isinstance(raw, (list, tuple)):
        return placements
    for logical in raw:
        if not isinstance(logical, (list, tuple)) or len(logical) < 6:
            continue
        try:
            placement = _LogicalPlacement(
                x=int(logical[0]),
                y=int(logical[1]),
                scale=round(float(logical[2]), 4),
                transform=int(logical[3]),
                primary=bool(logical[4]),
            )
        except (TypeError, ValueError):
            continue
        for spec in logical[5] if isinstance(logical[5], (list, tuple)) else ():
            key = _monitor_spec(spec)
            if key is not None:
                placements[key] = placement
    return placements


# ---------------------------------------------------------------------------
# joining, and the identity rule
# ---------------------------------------------------------------------------


def _match_connector(
    spec: Tuple[str, str, str, str], connectors: Sequence[DrmConnector]
) -> Tuple[Optional[DrmConnector], Optional[str]]:
    """Find the kernel connector behind a compositor monitor.

    Returns the connector and how it was found, so the evidence can say whether
    the exact content match or the name fallback was used.
    """
    connector_name, vendor, product, serial = spec

    triple = (vendor, product, serial)
    exact = [
        entry
        for entry in connectors
        if entry.edid is not None and entry.edid.match_key == triple
    ]
    if len(exact) == 1:
        return exact[0], "edid-triple"
    if len(exact) > 1:
        # Two panels reporting the same manufacturer, model and serial. Fall
        # through to the name join rather than pick one arbitrarily.
        pass

    wanted = normalize_connector(connector_name)
    by_name = [entry for entry in connectors if normalize_connector(entry.name) == wanted]
    if len(by_name) == 1:
        return by_name[0], "connector-name"
    return None, None


def _is_internal(properties: Mapping[str, Any], connector_name: str) -> Tuple[Optional[bool], Optional[str]]:
    """Internal or external — from evidence, never from a name we liked.

    The compositor states it outright with ``is-builtin``; that answer is taken
    whenever it is present. The connector-prefix fallback is a real kernel
    convention (``eDP``/``LVDS``/``DSI`` are chassis-internal by definition of
    the connector type), and the source is published either way so a caller can
    weigh it.
    """
    if "is-builtin" in properties:
        return bool(properties["is-builtin"]), "compositor-is-builtin"
    prefix = connector_name.split("-", 1)[0].upper()
    for candidate in _INTERNAL_CONNECTOR_PREFIXES:
        if prefix == candidate.rstrip("-").upper():
            return True, "connector-type"
    if prefix in ("HDMI", "DP", "VGA", "DVI"):
        return False, "connector-type"
    return None, None


def _display_identity(
    host_id: str, edid: Optional[EdidInfo], connector_name: str
) -> Tuple[str, Dict[str, Any]]:
    """The resource id, and an honest account of how strong it is.

    Preferred: a digest of the panel's own EDID, scoped to this host. That
    survives a reboot, a cable moved to another port, and a connector renumber —
    which is precisely what a user label has to survive to stay attached to the
    right panel.

    Fallback: the connector name, scoped to this host, and marked ``weak``. A
    connector name is a slot, not a panel; two different monitors plugged into
    the same port in turn would share it. It is published so the display is
    still identified and still shown, and marked so nothing durable is built on
    it.
    """
    if edid is not None:
        return (
            "display-" + fingerprint("cofferdam.display.edid", host_id, edid.sha256),
            {
                "source": "edid",
                "stability": STABILITY_HARDWARE,
                "edid_sha256": edid.sha256,
            },
        )
    return (
        "display-" + fingerprint("cofferdam.display.connector", host_id, connector_name),
        {
            "source": "connector",
            "stability": STABILITY_WEAK,
            "edid_sha256": None,
        },
    )


def _string_or_none(value: Any) -> Optional[str]:
    """Keep a real string; turn absence into ``None`` rather than into ``""``."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# the discovery backend
# ---------------------------------------------------------------------------


class DisplayDiscovery:
    """Resources owned: connected displays of the current graphical session.

    Evidence: ``org.gnome.Mutter.DisplayConfig.GetCurrentState`` for the
    session's own view, ``/sys/class/drm`` for EDID and physical size.

    Limitations: session-scoped (unavailable before login); a display the
    compositor has not enabled is reported with ``active=false``; a disconnected
    connector is not reported at all; a panel whose EDID cannot be read falls
    back to a connector-derived identity that is explicitly marked weak.
    """

    kind = KIND_DISPLAYS

    def __init__(self, drm_root: str = DRM_CLASS_PATH) -> None:
        self._drm_root = drm_root

    def collect(self, host_id: str, session) -> ResourceCollection:
        evidence_sources: List[str] = []
        join_notes: List[str] = []

        if not getattr(session, "available", False):
            return unavailable(
                self.kind,
                getattr(session, "reason", None)
                or "no graphical session is active, so no display layout exists to report",
                Evidence(backend=BACKEND_MUTTER, limitations=_LIMITATIONS),
            )

        try:
            reply = call_method(
                MUTTER_DESTINATION,
                MUTTER_OBJECT_PATH,
                MUTTER_INTERFACE,
                MUTTER_GET_CURRENT_STATE,
                session_environment=getattr(session, "environment", None),
            )
        except DbusUnavailable as exc:
            return unavailable(
                self.kind,
                "this desktop does not expose a display-configuration interface this build can "
                "read: " + exc.reason,
                Evidence(backend=BACKEND_MUTTER, limitations=_LIMITATIONS),
            )

        if len(reply) < 3:
            return failed(
                self.kind,
                "the desktop's display-configuration reply did not have the expected shape",
                Evidence(backend=BACKEND_MUTTER, limitations=_LIMITATIONS),
            )

        evidence_sources.append(MUTTER_DESTINATION + "." + MUTTER_GET_CURRENT_STATE)

        monitors = reply[1] if isinstance(reply[1], (list, tuple)) else []
        placements = _logical_placements(reply[2])

        connectors = read_drm_connectors(self._drm_root)
        if connectors:
            evidence_sources.append(self._drm_root)

        items: List[Dict[str, Any]] = []
        warnings: List[str] = []
        seen_ids: Dict[str, int] = {}

        for monitor in monitors:
            if not isinstance(monitor, (list, tuple)) or len(monitor) < 3:
                warnings.append("a monitor entry from the desktop could not be read and was skipped")
                continue
            spec = _monitor_spec(monitor[0])
            if spec is None:
                warnings.append("a monitor entry from the desktop carried no usable identity")
                continue

            item = self._build(spec, monitor, placements, connectors, host_id, join_notes)
            seen_ids[item["resource_id"]] = seen_ids.get(item["resource_id"], 0) + 1
            items.append(item)

        # Two panels with byte-identical EDIDs — some models ship without a
        # serial — would otherwise collapse into one resource. They stay two
        # resources, disambiguated by connector, and say that they are weakly
        # identified rather than silently claiming a hardware identity.
        ambiguous = AMBIGUOUS_EDID_WARNING
        for item in items:
            if seen_ids.get(item["resource_id"], 0) > 1:
                item["identity"]["stability"] = STABILITY_WEAK
                item["identity"]["source"] = "edid-ambiguous"
                item["resource_id"] += "-" + normalize_connector(item["connector"])
                if ambiguous not in warnings:
                    warnings.append(ambiguous)

        evidence = Evidence(
            backend=BACKEND_MUTTER,
            sources=tuple(evidence_sources),
            limitations=_LIMITATIONS + tuple(dict.fromkeys(join_notes)),
        )
        return collected(self.kind, items, evidence, warnings)

    def _build(
        self,
        spec: Tuple[str, str, str, str],
        monitor: Sequence[Any],
        placements: Mapping[Tuple[str, str, str, str], _LogicalPlacement],
        connectors: Sequence[DrmConnector],
        host_id: str,
        join_notes: List[str],
    ) -> Dict[str, Any]:
        connector_name, vendor, product, serial = spec
        properties = variant_map(monitor[2] if len(monitor) > 2 else {})

        drm, join = _match_connector(spec, connectors)
        if drm is None:
            join_notes.append(
                "at least one display could not be matched to a kernel connector, so its EDID "
                "fingerprint and physical size are absent"
            )
        elif join == "connector-name":
            join_notes.append(
                "at least one display was matched to its kernel connector by name rather than by "
                "EDID content"
            )

        edid = drm.edid if drm is not None else None
        resource_id, identity = _display_identity(host_id, edid, connector_name)

        internal, internal_source = _is_internal(properties, connector_name)
        placement = placements.get(spec)
        mode = _current_mode(monitor[1] if len(monitor) > 1 else [])

        return {
            "resource_id": resource_id,
            "kind": "display",
            "identity": identity,
            "connector": connector_name,
            "drm_connector": drm.name if drm is not None else None,
            # Reported by the compositor from EDID. Kept verbatim: a panel whose
            # EDID carries no model descriptor really is described by its
            # numeric product code, and rewriting that into "Unknown" would
            # discard the only model information the hardware gave.
            "manufacturer": _string_or_none(vendor),
            "model": _string_or_none(product),
            "serial": _string_or_none(serial),
            # Whether "model" is a name the panel published or its numeric
            # product code rendered as hex. A caller choosing a heading needs
            # to know which it got; both are truthful, only one is a name.
            "model_source": edid.model_source if edid is not None else None,
            "serial_source": edid.serial_source if edid is not None else None,
            "display_name": _string_or_none(properties.get("display-name")),
            "internal": internal,
            "internal_source": internal_source,
            # Only connected monitors are reported at all by GetCurrentState;
            # the kernel's own status is carried alongside when it was matched.
            "connected": True if drm is None else drm.connected,
            "active": placement is not None,
            "primary": placement.primary if placement is not None else None,
            "position": {"x": placement.x, "y": placement.y} if placement is not None else None,
            "logical_size": (
                {
                    "width": mode.get("width"),
                    "height": mode.get("height"),
                    "scale": placement.scale,
                }
                if placement is not None and mode
                else None
            ),
            "scale": placement.scale if placement is not None else None,
            "refresh_rate_hz": mode.get("refresh_rate_hz"),
            "current_mode": mode.get("mode_id"),
            "orientation": (
                _TRANSFORMS.get(placement.transform, "unknown") if placement is not None else None
            ),
            "physical_size_mm": (
                {"width": edid.width_mm, "height": edid.height_mm}
                if edid is not None and edid.width_mm and edid.height_mm
                else None
            ),
            "backend": BACKEND_MUTTER,
            "hardware_backend": BACKEND_DRM_SYSFS if drm is not None else None,
            "match_method": join,
            # Filled in by the overlay resolver; never by discovery itself.
            "overlay": None,
        }


__all__ = [
    "BACKEND_DRM_SYSFS",
    "BACKEND_MUTTER",
    "DRM_CLASS_PATH",
    "DisplayDiscovery",
    "DrmConnector",
    "normalize_connector",
    "read_drm_connectors",
]
