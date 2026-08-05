"""Attaching a user's label to a discovered resource — without becoming it.

The layering rule (D-2026-08-04-6) in one sentence: **a resource is discovered
first and labelled afterwards, and removing the label must leave the resource
fully identified.**

This module is the join between the two layers. It reads the existing
``displays.json`` overlay registry and, where an overlay unambiguously describes
a display that discovery actually found, attaches it to that display's ``overlay``
field. The display's own ``resource_id``, ``connector``, ``manufacturer``,
``model``, ``serial`` and EDID fingerprint are untouched. Nothing here can
rename, replace, or create a resource.

What counts as a safe match
---------------------------
Only the **EDID fingerprint** (``match.edid_sha256``), or a full
``manufacturer`` + ``model`` + ``serial`` triple where all three were reported
by the hardware. Both identify a physical panel.

``connector_hint`` alone is explicitly **not** enough, and this is the point of
the whole exercise. ``HDMI-1`` is a socket. Unplug one monitor, plug in a
different one, and the connector name is unchanged while the panel is not — an
overlay matched on the hint alone would silently move a user's label onto
somebody else's monitor. The registry schema already calls that field a *hint*;
this is where the word is enforced.

Ambiguity fails closed: if two overlay entries match one display, or one overlay
matches two displays, no overlay is applied and the reason is recorded.

Not implemented here (M2B2)
---------------------------
Creating or editing an overlay. This build resolves overlays that already exist
in a file the user wrote by hand. The follow-up milestone adds the flow:

1. the user selects a discovered card in the PWA or desktop client;
2. they add or edit a label and aliases;
3. the overlay is written atomically, keyed by the resource's **stable**
   identity — the EDID fingerprint for a display — via the existing
   :func:`~cofferdam.workstation.registries.storage.write_json_atomic`;
4. the resource keeps its system identity; the label is layered on it;
5. a display that is later disconnected still has its overlay, and stays
   distinguishable from one that is connected, because the overlay is keyed to
   the panel rather than to the connector it happened to be in.

Every discovered resource already carries an ``overlay`` field and a stable
``resource_id``, so that flow needs no change to the identity model.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

MATCH_EDID = "edid-sha256"
MATCH_HARDWARE_TRIPLE = "manufacturer-model-serial"

# Reasons an overlay was deliberately *not* applied. Published so a UI can
# explain why a display the user labelled is showing up unlabelled.
SKIP_AMBIGUOUS = "more than one overlay entry matches this display"
SKIP_CLAIMED = "this overlay entry matches more than one connected display"


def _triple(item: Mapping[str, Any]) -> Optional[tuple]:
    """A display's hardware triple, only when all three parts were reported."""
    manufacturer = item.get("manufacturer")
    model = item.get("model")
    serial = item.get("serial")
    if manufacturer and model and serial:
        return (str(manufacturer), str(model), str(serial))
    return None


def _overlay_triple(match) -> Optional[tuple]:
    manufacturer = getattr(match, "manufacturer", None)
    model = getattr(match, "model", None)
    serial = getattr(match, "serial", None)
    if manufacturer and model and serial:
        return (str(manufacturer), str(model), str(serial))
    return None


def _overlay_payload(entry, method: str) -> Dict[str, Any]:
    """What an overlay contributes: a label, aliases, and where it came from.

    Note what is *not* here — no id substitution, no connector, no resolution.
    An overlay adds names to a resource; it never restates the resource.
    """
    return {
        "overlay_id": entry.id,
        "label": entry.name,
        "aliases": list(entry.aliases),
        "enabled": entry.enabled,
        "matched_by": method,
        "source": "displays registry",
    }


class OverlayResolver:
    """Resolve user overlays onto discovered resources. Read-only.

    Owns nothing. Given a display collection's items and the loaded overlay
    registry, it fills each item's ``overlay`` field where — and only where — a
    single, unambiguous, hardware-grade match exists.
    """

    def resolve_displays(
        self, items: Sequence[Dict[str, Any]], overlays: Sequence[Any]
    ) -> List[str]:
        """Attach overlays in place. Returns any warnings worth publishing."""
        warnings: List[str] = []
        if not items or not overlays:
            return warnings

        # Candidate matches per display, so ambiguity on either side is visible
        # before anything is attached.
        candidates: Dict[int, List[tuple]] = {index: [] for index in range(len(items))}
        claims: Dict[str, List[int]] = {}

        for entry in overlays:
            match = getattr(entry, "match", None)
            if match is None:
                continue
            wanted_edid = (getattr(match, "edid_sha256", None) or "").lower() or None
            wanted_triple = _overlay_triple(match)

            for index, item in enumerate(items):
                identity = item.get("identity") or {}
                found_edid = (identity.get("edid_sha256") or "").lower() or None

                method = None
                if wanted_edid and found_edid and wanted_edid == found_edid:
                    method = MATCH_EDID
                elif wanted_triple and wanted_triple == _triple(item):
                    method = MATCH_HARDWARE_TRIPLE

                # A connector hint is never a match on its own. It exists to
                # help a human write the file, not to bind a label to a socket.
                if method is None:
                    continue

                candidates[index].append((entry, method))
                claims.setdefault(entry.id, []).append(index)

        for index, matches in candidates.items():
            if not matches:
                continue
            if len(matches) > 1:
                items[index]["overlay_skipped"] = SKIP_AMBIGUOUS
                warnings.append(SKIP_AMBIGUOUS)
                continue
            entry, method = matches[0]
            if len(claims.get(entry.id, [])) > 1:
                items[index]["overlay_skipped"] = SKIP_CLAIMED
                warnings.append(SKIP_CLAIMED)
                continue
            items[index]["overlay"] = _overlay_payload(entry, method)

        # Repeated ambiguity is one problem, not five.
        return list(dict.fromkeys(warnings))


__all__ = [
    "MATCH_EDID",
    "MATCH_HARDWARE_TRIPLE",
    "OverlayResolver",
    "SKIP_AMBIGUOUS",
    "SKIP_CLAIMED",
]
