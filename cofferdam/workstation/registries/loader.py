"""Reading, validating, and cross-checking the six registries.

Guarantees this module owes the rest of the product:

* **Nothing partially validated escapes.** A registry either parses completely
  and satisfies every cross-reference, or the caller gets a
  :class:`~.errors.RegistryError` and no items at all. There is no "best effort"
  mode where half a browser policy is in force.
* **Absence is not an error.** A machine that has never been configured has no
  registry files, and that is a valid, empty, version-1 registry. Only a file
  that *exists and is wrong* is a failure.
* **Nothing is ever repaired in place.** A malformed file is reported, never
  rewritten — overwriting it would destroy the only copy of what the user meant.
* **A broken dependency invalidates its dependents.** Browser profiles cannot be
  trusted while the application registry they reference is unreadable, so they
  fail closed rather than loading with unresolvable references.

Reads are not cached. These files are small, they are read at most once per
request, and re-reading means an edit takes effect without restarting the
service — and, more importantly, that a tightened domain policy is never one
stale process away from being ignored.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import errors as reasons
from .common import (
    SUPPORTED_VERSION,
    build_alias_index,
    build_id_index,
    normalize_alias,
    parse_envelope,
    require_reference,
)
from .errors import RegistryError
from .models import (
    BrowserProfile,
    RegistryItem,
    parse_agent_profile,
    parse_application,
    parse_browser_profile,
    parse_conversation_route,
    parse_device,
    parse_display,
)

DEVICES = "devices"
DISPLAYS = "displays"
APPLICATIONS = "applications"
BROWSER_PROFILES = "browser_profiles"
AGENT_PROFILES = "agent_profiles"
CONVERSATION_ROUTES = "conversation_routes"

# Ordered so a registry is always loaded after everything it references.
REGISTRY_NAMES: Tuple[str, ...] = (
    DEVICES,
    DISPLAYS,
    APPLICATIONS,
    BROWSER_PROFILES,
    AGENT_PROFILES,
    CONVERSATION_ROUTES,
)

_PARSERS: Dict[str, Callable[[str, int, Any], RegistryItem]] = {
    DEVICES: parse_device,
    DISPLAYS: parse_display,
    APPLICATIONS: parse_application,
    BROWSER_PROFILES: parse_browser_profile,
    AGENT_PROFILES: parse_agent_profile,
    CONVERSATION_ROUTES: parse_conversation_route,
}

# name -> registries whose items it references
_DEPENDENCIES: Dict[str, Tuple[str, ...]] = {
    DEVICES: (),
    DISPLAYS: (DEVICES,),
    APPLICATIONS: (),
    BROWSER_PROFILES: (APPLICATIONS, DISPLAYS),
    AGENT_PROFILES: (),
    CONVERSATION_ROUTES: (AGENT_PROFILES,),
}

MAX_FILE_BYTES = 512 * 1024

SOURCE_FILE = "file"
SOURCE_DEFAULT = "default"


class Registry:
    """One validated registry: its items plus its lookup indexes."""

    def __init__(
        self,
        name: str,
        version: int,
        items: Sequence[RegistryItem],
        source: str = SOURCE_DEFAULT,
    ) -> None:
        self.name = name
        self.version = version
        self.items: Tuple[RegistryItem, ...] = tuple(items)
        self.source = source
        self._by_id: Dict[str, RegistryItem] = build_id_index(name, self.items)
        self._by_phrase: Dict[str, Tuple[str, ...]] = build_alias_index(name, self.items)

    # -- lookup --------------------------------------------------------------

    def get(self, item_id: str) -> Optional[RegistryItem]:
        """Look up by stable id. Compared exactly: no folding, no trimming."""
        return self._by_id.get(item_id) if isinstance(item_id, str) else None

    def resolve(self, phrase: str) -> Optional[RegistryItem]:
        """Resolve a human phrase, or an exact id, to a single item.

        Ids win over aliases so a reference that is already precise cannot be
        hijacked by someone naming a display after another display's id. If a
        phrase somehow names more than one item this returns ``None`` — the
        caller must ask rather than guess.
        """
        if not isinstance(phrase, str):
            return None
        exact = self._by_id.get(phrase)
        if exact is not None:
            return exact
        matches = self._by_phrase.get(normalize_alias(phrase), ())
        if len(matches) != 1:
            return None
        return self._by_id.get(matches[0])

    def enabled(self) -> Tuple[RegistryItem, ...]:
        return tuple(item for item in self.items if item.enabled)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "items": [item.to_dict() for item in self.items],
        }


def empty_registry(name: str) -> Registry:
    """The safe default for a machine that has not been configured yet."""
    return Registry(name, SUPPORTED_VERSION, (), source=SOURCE_DEFAULT)


class RegistryLoad:
    """The outcome of loading one registry: validated items, or one error."""

    def __init__(
        self,
        name: str,
        registry: Optional[Registry] = None,
        error: Optional[RegistryError] = None,
    ) -> None:
        self.name = name
        self.registry = registry
        self.error = error

    @property
    def ok(self) -> bool:
        return self.error is None and self.registry is not None

    def summary(self) -> Dict[str, Any]:
        """Status only — never a filesystem path, never file content."""
        return {
            "name": self.name,
            "status": "ok" if self.ok else "error",
            "version": self.registry.version if self.ok else None,
            "item_count": len(self.registry.items) if self.ok else None,
            "enabled_count": len(self.registry.enabled()) if self.ok else None,
            "source": self.registry.source if self.ok else None,
            "error": self.error.to_payload() if self.error is not None else None,
        }


class RegistrySet:
    """All six registries, loaded and cross-validated together."""

    def __init__(self, loads: Mapping[str, RegistryLoad]) -> None:
        self._loads: Dict[str, RegistryLoad] = dict(loads)

    def __iter__(self):
        return iter(self._loads[name] for name in REGISTRY_NAMES)

    def load(self, name: str) -> RegistryLoad:
        return self._loads[name]

    def get(self, name: str) -> Optional[Registry]:
        load = self._loads.get(name)
        return load.registry if load is not None and load.ok else None

    def require(self, name: str) -> Registry:
        """Return a validated registry or raise its :class:`RegistryError`."""
        load = self._loads[name]
        if load.error is not None:
            raise load.error
        assert load.registry is not None  # ok implies registry
        return load.registry

    @property
    def ok(self) -> bool:
        return all(load.ok for load in self._loads.values())

    def summary(self) -> List[Dict[str, Any]]:
        return [self._loads[name].summary() for name in REGISTRY_NAMES]


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def _read_document(name: str, path: Path) -> Optional[Any]:
    """Read and JSON-decode one registry file, or ``None`` when it is absent."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise RegistryError(
                name,
                reasons.UNREADABLE,
                f"the registry file is larger than {MAX_FILE_BYTES // 1024} KiB and was not read",
            )
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except NotADirectoryError:
        # A parent component is a file: treat as "not configured", not as a
        # corrupt registry, and never report the offending path.
        return None
    except UnicodeDecodeError:
        raise RegistryError(name, reasons.INVALID_JSON, "the file is not valid UTF-8 text") from None
    except OSError:
        # The message would carry the full path; report the kind, not the trace.
        raise RegistryError(name, reasons.UNREADABLE, "the registry file could not be read") from None

    try:
        return json.loads(raw)
    except ValueError as exc:
        where = None
        line = getattr(exc, "lineno", None)
        column = getattr(exc, "colno", None)
        if isinstance(line, int) and isinstance(column, int):
            where = f"line {line} column {column}"
        raise RegistryError(name, reasons.INVALID_JSON, "the file is not valid JSON", where=where) from None


def _load_one(name: str, path: Path) -> RegistryLoad:
    try:
        document = _read_document(name, path)
        if document is None:
            return RegistryLoad(name, registry=empty_registry(name))
        raw_items = parse_envelope(name, document)
        parse = _PARSERS[name]
        items = [parse(name, position, raw) for position, raw in enumerate(raw_items)]
        registry = Registry(name, SUPPORTED_VERSION, items, source=SOURCE_FILE)
    except RegistryError as error:
        return RegistryLoad(name, error=error)
    return RegistryLoad(name, registry=registry)


def load_registries(config) -> RegistrySet:
    """Load and cross-validate every registry for ``config``'s home."""
    loads: Dict[str, RegistryLoad] = {}
    for name in REGISTRY_NAMES:
        load = _load_one(name, config.registry_path(name))
        if load.ok:
            load = _cross_validate(load, loads)
        loads[name] = load
    return RegistrySet(loads)


def _cross_validate(load: RegistryLoad, loaded: Mapping[str, RegistryLoad]) -> RegistryLoad:
    """Check this registry's references against the ones it depends on."""
    name = load.name
    for dependency in _DEPENDENCIES[name]:
        if not loaded[dependency].ok:
            return RegistryLoad(
                name,
                error=RegistryError(
                    name,
                    reasons.DEPENDENCY_INVALID,
                    f"cannot be validated while the {dependency} registry is invalid",
                ),
            )
    registry = load.registry
    assert registry is not None
    try:
        _check_references(registry, loaded)
    except RegistryError as error:
        return RegistryLoad(name, error=error)
    return load


def _ids(loaded: Mapping[str, RegistryLoad], name: str) -> Dict[str, RegistryItem]:
    registry = loaded[name].registry
    return {item.id: item for item in (registry.items if registry is not None else ())}


def _check_references(registry: Registry, loaded: Mapping[str, RegistryLoad]) -> None:
    name = registry.name

    if name == DISPLAYS:
        devices = _ids(loaded, DEVICES)
        for position, item in enumerate(registry.items):
            # Enabled or disabled: a display on a device that is currently
            # switched off is still a real display. Only a *missing* device is
            # a configuration error.
            require_reference(
                name, f"items[{position}].device_id", item.device_id, devices, DEVICES
            )

    elif name == BROWSER_PROFILES:
        applications = _ids(loaded, APPLICATIONS)
        displays = _ids(loaded, DISPLAYS)
        default_holder: Optional[str] = None
        for position, item in enumerate(registry.items):
            require_reference(
                name,
                f"items[{position}].application_id",
                item.application_id,
                applications,
                APPLICATIONS,
            )
            application = applications[item.application_id]
            if not application.enabled:
                raise RegistryError(
                    name,
                    reasons.CONSTRAINT_VIOLATED,
                    f"application '{item.application_id}' is disabled and cannot back a profile",
                    where=f"items[{position}].application_id",
                )
            require_reference(
                name,
                f"items[{position}].preferred_display_id",
                item.preferred_display_id,
                displays,
                DISPLAYS,
            )
            if item.enabled and item.default_for_url:
                if default_holder is not None:
                    raise RegistryError(
                        name,
                        reasons.CONSTRAINT_VIOLATED,
                        "more than one enabled profile claims default_for_url; "
                        f"'{default_holder}' already does",
                        where=f"items[{position}].default_for_url",
                    )
                default_holder = item.id

    elif name == CONVERSATION_ROUTES:
        agents = _ids(loaded, AGENT_PROFILES)
        for position, item in enumerate(registry.items):
            require_reference(
                name,
                f"items[{position}].target_agent_profile_id",
                item.target_agent_profile_id,
                agents,
                AGENT_PROFILES,
            )


def default_browser_profile(profiles: Registry) -> Optional[BrowserProfile]:
    """The single enabled profile marked ``default_for_url``, if there is one.

    ``None`` covers both "no default configured" and — defensively, since
    validation already rejects it — "more than one claims to be the default".
    Having no default is a supported state: legacy browser selection stays.
    """
    candidates = [
        item
        for item in profiles.items
        if item.enabled and getattr(item, "default_for_url", False)
    ]
    return candidates[0] if len(candidates) == 1 else None


__all__ = [
    "AGENT_PROFILES",
    "APPLICATIONS",
    "BROWSER_PROFILES",
    "CONVERSATION_ROUTES",
    "DEVICES",
    "DISPLAYS",
    "REGISTRY_NAMES",
    "Registry",
    "RegistryLoad",
    "RegistrySet",
    "default_browser_profile",
    "empty_registry",
    "load_registries",
]
