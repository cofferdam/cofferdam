"""The read-modify-write cycle behind the display-overlay endpoints.

Separated from :mod:`~cofferdam.workstation.service` because the ordering here
is the whole security property, and it should be testable without a client:

1. take a **fresh** runtime snapshot — the request supplies a resource id, and
   the identity behind it is re-derived from live discovery rather than trusted;
2. validate the label and aliases;
3. derive the persistent key from the panel, refusing weak or ambiguous identity;
4. take the registry lock, then re-read the registry *inside* it;
5. build the new document and validate it by loading it back;
6. write atomically;
7. only then report success, with the overlay as it now resolves.

Steps 4 and 5 are the ones that are easy to get wrong. Reading the registry
before taking the lock would reintroduce the lost update the lock exists to
prevent. Skipping the reload in step 5 would let this module write a document
the loader would later refuse — turning a rejected edit into a registry that
fails to load at next start, which is a far worse failure than a 422.

Nothing here reports success it has not earned. If the write raises, the
exception propagates: the previous file is untouched by ``write_json_atomic``,
and the caller gets a real error rather than a card that updated optimistically.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from ..registries.loader import DEVICES, DISPLAYS, load_registries
from ..registries.storage import (
    RegistryLockTimeout,
    registry_document,
    registry_lock,
    write_json_atomic,
)
from .overlay_writes import (
    OverlayWriteRejected,
    REJECT_UNKNOWN_RESOURCE,
    assert_aliases_free,
    assert_identity_unambiguous,
    assert_usable_overlay_id,
    build_overlay_item,
    clean_aliases,
    clean_label,
    display_identity,
    find_display,
    remove,
    resolve_device_id,
    upsert,
)

# A registry that will not load is a configuration error the user must fix by
# hand; this write path must not "repair" it by overwriting, because that would
# silently discard whatever they were in the middle of editing.
REJECT_REGISTRY_UNREADABLE = "registry_unreadable"
REJECT_WRITE_FAILED = "write_failed"
REJECT_BUSY = "registry_busy"
REJECT_INVALID_DOCUMENT = "invalid_registry_document"


class DisplayOverlayStore:
    """Owns the display-overlay write cycle for one configuration."""

    def __init__(self, config, inventory, load: Optional[Callable] = None) -> None:
        self._config = config
        self._inventory = inventory
        self._load = load or load_registries

    # -- helpers -------------------------------------------------------------

    def _connected_displays(self) -> List[Mapping[str, Any]]:
        """Displays discovery can see *now*, refreshed past the read cache.

        The cache exists so a polling phone does not walk /proc every second.
        A write must not be served from it: the whole point is that the identity
        being labelled is verified at the moment of the write.
        """
        snapshot, collection = self._inventory.collection("displays", True)
        if collection.status != "ok":
            raise OverlayWriteRejected(
                REJECT_UNKNOWN_RESOURCE,
                "Displays cannot be read right now, so none can be named.",
                getattr(collection, "reason", None),
            )
        return list(collection.items)

    def _registry_state(self):
        """The current displays registry, or a refusal if it will not load."""
        registries = self._load(self._config)
        load = registries.load(DISPLAYS)
        if not load.ok:
            raise OverlayWriteRejected(
                REJECT_REGISTRY_UNREADABLE,
                "The displays registry is not currently valid, so it will not be modified.",
                "Fix the file by hand first; overwriting it here would discard whatever is "
                "in it.",
            )
        devices = self._load_devices(registries)
        return load.registry, devices

    @staticmethod
    def _load_devices(registries) -> Sequence[Any]:
        registry = registries.get(DEVICES)
        return registry.items if registry is not None else ()

    def _raw_items(self) -> List[Dict[str, Any]]:
        """The registry file's items as plain dicts, read inside the lock.

        Read from disk rather than from the validated model so that fields this
        build does not know about survive a round trip untouched.
        """
        path = self._config.registry_path(DISPLAYS)
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as error:
            raise OverlayWriteRejected(
                REJECT_REGISTRY_UNREADABLE,
                "The displays registry could not be read.",
                str(error),
            )
        items = raw.get("items") if isinstance(raw, dict) else None
        return [dict(item) for item in items] if isinstance(items, list) else []

    def _commit(self, items: Sequence[Mapping[str, Any]]) -> None:
        """Validate by loading back, then write atomically."""
        path = Path(self._config.registry_path(DISPLAYS))
        document = registry_document(items)

        # Validate the exact bytes that are about to be stored. The loader is
        # the authority on what a registry may contain, so asking it now turns
        # a would-be broken file into a refused request.
        self._validate(document)

        try:
            write_json_atomic(path, document)
        except OverlayWriteRejected:
            raise
        except Exception as error:
            # write_json_atomic leaves the original file whole on any failure.
            raise OverlayWriteRejected(
                REJECT_WRITE_FAILED,
                "The change could not be saved, so nothing was changed.",
                type(error).__name__,
            )

    @staticmethod
    def _validate(document: Mapping[str, Any]) -> None:
        """Run the document past the real parser before it is written.

        The same envelope check and per-item parser the loader uses at startup,
        so "would this file load?" is answered by the authority on that question
        rather than by a second, drifting copy of its rules here.
        """
        from ..registries.loader import _PARSERS, parse_envelope

        try:
            raw_items = parse_envelope(DISPLAYS, document)
            parse = _PARSERS[DISPLAYS]
            for position, raw in enumerate(raw_items):
                parse(DISPLAYS, position, raw)
        except Exception as error:
            raise OverlayWriteRejected(
                REJECT_INVALID_DOCUMENT,
                "The change would produce an invalid displays registry, so it was not saved.",
                str(error),
            )

    def _resolved(self, resource_id: str) -> Dict[str, Any]:
        """The display as it now reads, overlay included — the durable truth.

        Re-read after the write rather than assembled from what was sent, so the
        response is what the next reader will see. A response built from the
        request is a claim; this is an observation.
        """
        _, collection = self._inventory.collection("displays", True)
        for item in collection.items:
            if item.get("resource_id") == resource_id:
                return {
                    "resource_id": item.get("resource_id"),
                    "overlay": item.get("overlay"),
                    "identity": item.get("identity"),
                    "connector": item.get("connector"),
                    "model": item.get("model"),
                    "manufacturer": item.get("manufacturer"),
                    "serial": item.get("serial"),
                    "display_name": item.get("display_name"),
                }
        # The display vanished between write and re-read — unplugged mid-request.
        # The write is durable; say so without inventing a resource.
        return {"resource_id": resource_id, "overlay": None, "identity": None}

    # -- operations ----------------------------------------------------------

    def save(self, resource_id: str, label: Any, aliases: Any) -> Dict[str, Any]:
        """Create or replace one display's user-owned name."""
        clean_name = clean_label(label)
        clean_alias_list = clean_aliases(aliases)

        items = self._connected_displays()
        target = find_display(items, resource_id)
        kind, match = display_identity(target)
        assert_identity_unambiguous(items, target, kind, match)

        registry, devices = self._registry_state()
        assert_aliases_free(registry.items, resource_id, clean_name, clean_alias_list)

        path = Path(self._config.registry_path(DISPLAYS))
        try:
            with registry_lock(path):
                existing = self._raw_items()
                device_id = self._device_for(existing, resource_id, devices)
                entry = build_overlay_item(
                    resource_id, device_id, clean_name, clean_alias_list, match
                )
                self._commit(upsert(existing, entry))
        except RegistryLockTimeout as error:
            raise OverlayWriteRejected(REJECT_BUSY, str(error))

        return self._resolved(resource_id)

    def delete(self, resource_id: str) -> Dict[str, Any]:
        """Remove one display's user-owned name."""
        assert_usable_overlay_id(resource_id)
        path = Path(self._config.registry_path(DISPLAYS))
        try:
            with registry_lock(path):
                self._registry_state()
                self._commit(remove(self._raw_items(), resource_id))
        except RegistryLockTimeout as error:
            raise OverlayWriteRejected(REJECT_BUSY, str(error))

        return self._resolved(resource_id)

    @staticmethod
    def _device_for(
        existing: Sequence[Mapping[str, Any]], overlay_id: str, devices: Sequence[Any]
    ) -> str:
        """Keep an existing entry's device; only a new entry has to choose one.

        Editing a label must not silently re-home the display to a different
        device just because the devices registry has grown since it was created.
        """
        for item in existing:
            if item.get("id") == overlay_id and item.get("device_id"):
                return str(item["device_id"])
        return resolve_device_id(devices)


__all__ = [
    "REJECT_BUSY",
    "REJECT_INVALID_DOCUMENT",
    "REJECT_REGISTRY_UNREADABLE",
    "REJECT_WRITE_FAILED",
    "DisplayOverlayStore",
]
