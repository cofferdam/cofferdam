"""Writing a user's label onto a discovered display — the first write path.

M2B1 discovered displays and resolved overlays a user had written **by hand**.
This module is the other half: the authenticated flow that creates, edits and
removes those overlays from the phone. It is the first network-reachable write
into runtime configuration in this product, so it is built as a security
boundary rather than as a convenience.

The layering rule is unchanged (D-2026-08-04-6): **a resource is discovered
first and labelled afterwards, and removing the label must leave the resource
fully identified.** Nothing here may rename, replace, or create a display. An
overlay contributes a label and aliases; the connector, manufacturer, model,
serial, EDID digest, resolution and ``resource_id`` stay owned by discovery and
are never copied into the overlay, because a copy is a second source of truth
that can go stale and start lying.

The client never names the thing it is writing to
-------------------------------------------------
A request addresses a display by its **runtime** ``resource_id`` and nothing
else. The server takes a fresh snapshot, finds that resource, and derives the
persistent key from what discovery reports about the panel. There is no request
field for an EDID digest, a registry name, a file path, or an overlay id — not
validated-and-rejected, simply absent from the schema. A caller cannot ask for a
key it prefers, so it cannot ask for someone else's.

Identity strong enough to persist
---------------------------------
Only a panel-grade identity may carry a durable label:

1. the **host-scoped EDID digest** — already what a display's ``resource_id`` is
   built from, and what survives a reboot, a cable moved to another port, and a
   connector renumber;
2. a full ``manufacturer`` + ``model`` + ``serial`` triple, and only when all
   three were actually reported.

A ``weak`` identity — a display that published no EDID, identified only by its
connector — is **refused**, with a reason. This is the stricter of the two
options the milestone allowed, and it is chosen because the read side already
enforces exactly this rule: ``overlays.OverlayResolver`` will not match on a
connector hint, so a weak overlay could be written and would then never resolve.
Storing something that cannot work, and warning about it, would be a worse lie
than declining. ``HDMI-1`` is a socket, not a monitor.

Ambiguity fails closed, on both sides
-------------------------------------
If two connected displays share the identity being written — genuinely possible
with two identical panels whose EDID contains no serial — the write is refused.
The alternative is picking one, which is a guess that silently attaches a user's
name to the wrong monitor.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..registries.common import (
    MAX_ALIAS_LENGTH,
    MAX_ALIASES,
    MAX_ITEMS,
    MAX_NAME_LENGTH,
    is_valid_id,
    normalize_alias,
)

# Reasons a write is refused. Each is a stable code the PWA can branch on, and
# each carries prose the user can act on.
REJECT_UNKNOWN_RESOURCE = "unknown_resource"
REJECT_WEAK_IDENTITY = "weak_identity"
REJECT_AMBIGUOUS_IDENTITY = "ambiguous_identity"
REJECT_AMBIGUOUS_ALIAS = "ambiguous_alias"
REJECT_INVALID_LABEL = "invalid_label"
REJECT_INVALID_ALIASES = "invalid_aliases"
REJECT_NO_DEVICE = "no_device"
REJECT_AMBIGUOUS_DEVICE = "ambiguous_device"
REJECT_REGISTRY_FULL = "registry_full"
REJECT_NOT_LABELLED = "not_labelled"

# Overlay keys are derived, never supplied. This is the identity kind recorded
# alongside a written entry so a reader can see which rule applied.
KEY_EDID = "edid-sha256"
KEY_HARDWARE_TRIPLE = "manufacturer-model-serial"


class OverlayWriteRejected(Exception):
    """A write refused for a reason the user should see.

    Carries a stable ``code`` plus prose. It is deliberately not an HTTP
    concern: the service layer maps codes to statuses, and this module stays
    testable without a client.
    """

    def __init__(self, code: str, message: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _has_control_characters(text: str) -> bool:
    """Any Unicode control or format character.

    Category ``Cc`` catches the ASCII controls; ``Cf`` catches the invisible
    formatting characters — zero-width joiners, bidirectional overrides — that
    let two different strings render identically. A label is a short human
    phrase and needs none of them.
    """
    return any(unicodedata.category(ch) in ("Cc", "Cf") for ch in text)


def clean_label(raw: Any) -> str:
    """Validate and normalize a label, or refuse it.

    NFC normalization matters here and is not cosmetic: ``ö`` can be one code
    point or ``o`` plus a combining diaeresis, and a user typing Turkish on iOS
    may produce either. Storing the composed form keeps a later exact comparison
    honest. The user's own spelling and case are otherwise preserved exactly.
    """
    if not isinstance(raw, str):
        raise OverlayWriteRejected(REJECT_INVALID_LABEL, "the label must be text")

    text = unicodedata.normalize("NFC", raw)
    text = " ".join(text.split())

    if not text:
        raise OverlayWriteRejected(
            REJECT_INVALID_LABEL, "the label cannot be empty once whitespace is trimmed"
        )
    if len(text) > MAX_NAME_LENGTH:
        raise OverlayWriteRejected(
            REJECT_INVALID_LABEL,
            f"the label must be at most {MAX_NAME_LENGTH} characters",
        )
    if _has_control_characters(text):
        raise OverlayWriteRejected(
            REJECT_INVALID_LABEL, "the label cannot contain control characters"
        )
    return text


def clean_aliases(raw: Any) -> Tuple[str, ...]:
    """Validate a list of aliases, dropping duplicates that only differ by case.

    Duplicate detection uses the registry's Turkish-aware
    :func:`~cofferdam.workstation.registries.common.normalize_alias`, so
    ``Büyük monitör`` and ``büyük monitör`` collapse to one entry — and so do
    ``IŞIK`` and ``ışık``, which a naive ``lower()`` would keep apart. The
    **first** spelling the user wrote is the one kept; normalization decides
    equality and never decides presentation.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise OverlayWriteRejected(REJECT_INVALID_ALIASES, "aliases must be a list")
    if len(raw) > MAX_ALIASES:
        raise OverlayWriteRejected(
            REJECT_INVALID_ALIASES, f"at most {MAX_ALIASES} aliases are allowed"
        )

    kept: List[str] = []
    seen: set = set()
    for entry in raw:
        if not isinstance(entry, str):
            raise OverlayWriteRejected(REJECT_INVALID_ALIASES, "each alias must be text")
        text = unicodedata.normalize("NFC", entry)
        text = " ".join(text.split())
        if not text:
            raise OverlayWriteRejected(
                REJECT_INVALID_ALIASES, "an alias cannot be empty once whitespace is trimmed"
            )
        if len(text) > MAX_ALIAS_LENGTH:
            raise OverlayWriteRejected(
                REJECT_INVALID_ALIASES,
                f"an alias must be at most {MAX_ALIAS_LENGTH} characters",
            )
        if _has_control_characters(text):
            raise OverlayWriteRejected(
                REJECT_INVALID_ALIASES, "an alias cannot contain control characters"
            )
        key = normalize_alias(text)
        if key in seen:
            continue
        seen.add(key)
        kept.append(text)
    return tuple(kept)


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------


def display_identity(item: Mapping[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """The persistent match a display may be labelled by, or a refusal.

    Returns ``(kind, match_fields)`` where ``match_fields`` is the
    ``DisplayMatch`` payload to persist. The connector is recorded only as
    ``connector_hint`` — helpful when a human later reads the file, never used
    to resolve, and explicitly documented as such in the schema.
    """
    identity = item.get("identity") or {}
    edid = identity.get("edid_sha256")
    if edid:
        return (
            KEY_EDID,
            {
                "connector_hint": item.get("connector"),
                "manufacturer": item.get("manufacturer"),
                "model": item.get("model"),
                "serial": item.get("serial"),
                "edid_sha256": str(edid).lower(),
            },
        )

    manufacturer, model, serial = (
        item.get("manufacturer"),
        item.get("model"),
        item.get("serial"),
    )
    if manufacturer and model and serial:
        return (
            KEY_HARDWARE_TRIPLE,
            {
                "connector_hint": item.get("connector"),
                "manufacturer": str(manufacturer),
                "model": str(model),
                "serial": str(serial),
                "edid_sha256": None,
            },
        )

    raise OverlayWriteRejected(
        REJECT_WEAK_IDENTITY,
        "This display cannot be named yet.",
        "It reported no EDID and no complete manufacturer/model/serial, so the only thing "
        "identifying it is its connector. A connector is a socket, not a monitor: a label "
        "stored against it would move to whatever is plugged in there next. Cofferdam "
        "declines rather than store a name that would silently attach to the wrong panel.",
    )


def assert_usable_overlay_id(resource_id: str) -> None:
    """The resource id has to be a legal registry id, because it becomes one.

    Discovery builds display resource ids as ``display-<lowercase hex digest>``,
    which is already valid kebab-case, so this never fires in practice. It is
    here because the *consequence* of it firing would otherwise be a confusing
    422 from the document validator at the very end of the write, and because a
    future resource-id scheme that broke the rule should say so plainly rather
    than mysteriously refuse to save.
    """
    if not is_valid_id(resource_id):
        raise OverlayWriteRejected(
            REJECT_UNKNOWN_RESOURCE,
            "That display cannot be named.",
            "Its identifier is not in the form Cofferdam stores overlays under.",
        )


def find_display(items: Sequence[Mapping[str, Any]], resource_id: str) -> Mapping[str, Any]:
    """The one connected display with this resource id, or a refusal."""
    assert_usable_overlay_id(resource_id)
    matches = [item for item in items if item.get("resource_id") == resource_id]
    if not matches:
        raise OverlayWriteRejected(
            REJECT_UNKNOWN_RESOURCE,
            "That display is not currently connected.",
            "Overlays are written against a display discovery can see right now, so its "
            "identity can be verified rather than taken on trust from the request.",
        )
    if len(matches) > 1:  # pragma: no cover - resource ids are de-duplicated upstream
        raise OverlayWriteRejected(
            REJECT_AMBIGUOUS_IDENTITY,
            "More than one connected display reports that identifier.",
        )
    return matches[0]


def assert_identity_unambiguous(
    items: Sequence[Mapping[str, Any]], target: Mapping[str, Any], kind: str, match: Mapping[str, Any]
) -> None:
    """Refuse when another connected display shares this persistent identity.

    Two identical monitors whose EDID carries no serial number really do produce
    the same digest. Discovery already distinguishes them for display purposes
    by appending the connector to the resource id, but that distinction is
    exactly the thing that must not become persistent. So the label is refused
    for both rather than attached to whichever happened to be first.
    """
    target_id = target.get("resource_id")
    for item in items:
        if item.get("resource_id") == target_id:
            continue
        if kind == KEY_EDID:
            other = ((item.get("identity") or {}).get("edid_sha256") or "").lower()
            if other and other == match.get("edid_sha256"):
                raise OverlayWriteRejected(
                    REJECT_AMBIGUOUS_IDENTITY,
                    "Two connected displays report the same hardware identity.",
                    "Their EDID is byte-identical — usually two of the same monitor model "
                    "whose firmware publishes no serial number. Cofferdam will not guess "
                    "which one you meant, so neither is labelled.",
                )
        elif kind == KEY_HARDWARE_TRIPLE:
            triple = (item.get("manufacturer"), item.get("model"), item.get("serial"))
            if all(triple) and tuple(str(part) for part in triple) == (
                match.get("manufacturer"),
                match.get("model"),
                match.get("serial"),
            ):
                raise OverlayWriteRejected(
                    REJECT_AMBIGUOUS_IDENTITY,
                    "Two connected displays report the same manufacturer, model and serial.",
                )


def assert_aliases_free(
    existing: Sequence[Any], overlay_id: str, label: str, aliases: Sequence[str]
) -> None:
    """Refuse aliases already used by a *different* display overlay.

    The registry's alias index is what turns "büyük monitör" into a resource, so
    a phrase pointing at two displays makes both unaddressable. Checked against
    the label as well, because the label is an addressable phrase too.
    """
    wanted = {normalize_alias(text) for text in (label,) + tuple(aliases)}
    for entry in existing:
        if getattr(entry, "id", None) == overlay_id:
            continue
        taken = {normalize_alias(getattr(entry, "name", "") or "")}
        taken |= {normalize_alias(text) for text in getattr(entry, "aliases", ()) or ()}
        clash = wanted & taken
        if clash:
            raise OverlayWriteRejected(
                REJECT_AMBIGUOUS_ALIAS,
                "Another display already uses that name.",
                "The phrase " + repr(sorted(clash)[0]) + " would then refer to two displays, "
                "and neither could be addressed by it.",
            )


def resolve_device_id(devices: Sequence[Any]) -> str:
    """Which device a new display overlay belongs to.

    The displays registry requires every item to reference a device, and the
    loader enforces it. Nothing in this build tells the daemon *which* of
    several registered devices is the machine it is running on, so:

    * exactly one enabled device — use it;
    * none — refuse, because the write would produce a registry that fails its
      own cross-reference check on the next load;
    * several — refuse, because choosing one would be a guess with no evidence.

    Binding a running daemon to its own device entry is a real gap, and it is
    documented rather than papered over with a heuristic.
    """
    enabled = [entry for entry in devices if getattr(entry, "enabled", True)]
    if len(enabled) == 1:
        return enabled[0].id
    if not enabled:
        raise OverlayWriteRejected(
            REJECT_NO_DEVICE,
            "No device is registered on this machine yet.",
            "A display overlay has to belong to a device. Add one entry to "
            "devices.json and try again.",
        )
    raise OverlayWriteRejected(
        REJECT_AMBIGUOUS_DEVICE,
        "More than one device is registered, and this build cannot tell which one it runs on.",
        "Naming a display would mean guessing which device it is attached to.",
    )


# ---------------------------------------------------------------------------
# building the stored entry
# ---------------------------------------------------------------------------


def build_overlay_item(
    overlay_id: str,
    device_id: str,
    label: str,
    aliases: Sequence[str],
    match: Mapping[str, Any],
) -> Dict[str, Any]:
    """The registry item to persist — user-owned fields plus match evidence.

    Nothing about the display's *current* state goes in: no resolution, no
    position, no scale, no primary flag, no connected flag. Those change while
    the label does not, and a stored copy would be a second, staler source of
    truth for something discovery already owns.
    """
    return {
        "id": overlay_id,
        "device_id": device_id,
        "name": label,
        "aliases": list(aliases),
        "enabled": True,
        "match": dict(match),
    }


def upsert(items: Sequence[Mapping[str, Any]], entry: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Replace the entry with this id, or append it. Order is otherwise kept."""
    result = [dict(item) for item in items]
    for index, item in enumerate(result):
        if item.get("id") == entry["id"]:
            result[index] = dict(entry)
            return result
    if len(result) >= MAX_ITEMS:
        raise OverlayWriteRejected(
            REJECT_REGISTRY_FULL,
            f"The displays registry already holds {MAX_ITEMS} entries.",
        )
    result.append(dict(entry))
    return result


def remove(items: Sequence[Mapping[str, Any]], overlay_id: str) -> List[Dict[str, Any]]:
    """Drop the entry with this id, or refuse because there was nothing to drop."""
    result = [dict(item) for item in items if item.get("id") != overlay_id]
    if len(result) == len(items):
        raise OverlayWriteRejected(
            REJECT_NOT_LABELLED,
            "That display has no custom name to remove.",
        )
    return result


__all__ = [
    "KEY_EDID",
    "KEY_HARDWARE_TRIPLE",
    "OverlayWriteRejected",
    "REJECT_AMBIGUOUS_ALIAS",
    "REJECT_AMBIGUOUS_DEVICE",
    "REJECT_AMBIGUOUS_IDENTITY",
    "REJECT_INVALID_ALIASES",
    "REJECT_INVALID_LABEL",
    "REJECT_NOT_LABELLED",
    "REJECT_NO_DEVICE",
    "REJECT_REGISTRY_FULL",
    "REJECT_UNKNOWN_RESOURCE",
    "REJECT_WEAK_IDENTITY",
    "assert_aliases_free",
    "assert_usable_overlay_id",
    "assert_identity_unambiguous",
    "build_overlay_item",
    "clean_aliases",
    "clean_label",
    "display_identity",
    "find_display",
    "remove",
    "resolve_device_id",
    "upsert",
]
