"""The six M2A registry schemas.

Each registry is a versioned document of items sharing four fields — ``id``,
``name``, ``aliases``, ``enabled`` — plus a small, closed set of registry
specific fields. Everything here is **descriptive**: what exists and what it is
called. Nothing here says *how* to do anything.

That distinction is the whole point of the boundary. A registry may say "this
machine has an application called Opera, adapter key ``opera``". It may never
say "run ``/snap/bin/opera --user-data-dir=…``". Executables, argv, desktop
files, and profile directories stay code-owned allowlists in the adapters, so
editing a JSON file can select between capabilities the code already has but
can never introduce a new one. Secrets, cookies, tokens, account identifiers,
and live conversation/tab identifiers are out of scope by construction: there is
no field to put them in, and :data:`~.common.FORBIDDEN_FIELDS` rejects the
obvious attempts by name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import errors as reasons
from .common import (
    MAX_ITEMS,
    MAX_NOTES_LENGTH,
    ItemReader,
    normalize_alias,
)
from .errors import RegistryError

# -- closed vocabularies -----------------------------------------------------

DEVICE_KINDS = ("workstation", "raspberry-pi", "phone", "tablet", "other")
DEVICE_PLATFORMS = ("linux", "windows", "macos", "ios", "android", "other")

# Adapter keys the code has an implementation for. A registry may only select
# from this tuple; it can never name a program.
APPLICATION_ADAPTER_KEYS = ("opera", "firefox")

LAUNCH_MODES = ("default-instance",)
DOMAIN_POLICY_MODES = ("allow-all", "allow-list")

AGENT_ADAPTER_KINDS = ("claude-code", "codex-cli", "ollama", "custom-placeholder")
# M2A ships no agent execution at all. The only status a profile may declare is
# the honest one; a registry cannot promote itself to "ready".
AGENT_EXECUTION_STATUSES = ("not-implemented",)

ROUTE_SOURCE_KINDS = ("manual", "opera-extension", "future-connector")
ROUTE_RETURN_MODES = ("manual-copy", "prepare-then-confirm")

MAX_DOMAINS = 64
MAX_HOSTNAME_LENGTH = 253
MAX_LABEL_LENGTH = 63
_LABEL_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


# ---------------------------------------------------------------------------
# items
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistryItem:
    id: str
    name: str
    aliases: Tuple[str, ...]
    enabled: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "aliases": list(self.aliases),
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class Device(RegistryItem):
    kind: str = "other"
    platform: str = "other"
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        payload.update({"kind": self.kind, "platform": self.platform, "notes": self.notes})
        return payload


@dataclass(frozen=True)
class DisplayMatch:
    """Hints for recognising a physical panel later. Identity, never action."""

    connector_hint: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    edid_sha256: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "connector_hint": self.connector_hint,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "serial": self.serial,
            "edid_sha256": self.edid_sha256,
        }


@dataclass(frozen=True)
class Display(RegistryItem):
    device_id: str = ""
    match: DisplayMatch = field(default_factory=DisplayMatch)

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        payload.update({"device_id": self.device_id, "match": self.match.to_dict()})
        return payload


@dataclass(frozen=True)
class Application(RegistryItem):
    adapter_key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        payload["adapter_key"] = self.adapter_key
        return payload


@dataclass(frozen=True)
class DomainPolicy:
    mode: str = "allow-all"
    domains: Tuple[str, ...] = ()

    def allows(self, hostname: str) -> bool:
        """Suffix matching on label boundaries only.

        ``example.com`` allows ``example.com`` and ``docs.example.com``. It does
        **not** allow ``badexample.com`` — the plain ``endswith`` that would is
        the classic allowlist bypass, so the boundary dot is required.
        """
        if self.mode == "allow-all":
            return True
        host = hostname.strip().rstrip(".").lower()
        if not host:
            return False
        return any(host == domain or host.endswith("." + domain) for domain in self.domains)

    def to_dict(self) -> Dict[str, Any]:
        return {"mode": self.mode, "domains": list(self.domains)}


@dataclass(frozen=True)
class BrowserProfile(RegistryItem):
    application_id: str = ""
    default_for_url: bool = False
    preferred_display_id: Optional[str] = None
    launch_mode: str = "default-instance"
    domain_policy: DomainPolicy = field(default_factory=DomainPolicy)

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        payload.update(
            {
                "application_id": self.application_id,
                "default_for_url": self.default_for_url,
                "preferred_display_id": self.preferred_display_id,
                "launch_mode": self.launch_mode,
                "domain_policy": self.domain_policy.to_dict(),
            }
        )
        return payload


@dataclass(frozen=True)
class AgentProfile(RegistryItem):
    adapter_kind: str = "custom-placeholder"
    execution_status: str = "not-implemented"

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        payload.update(
            {"adapter_kind": self.adapter_kind, "execution_status": self.execution_status}
        )
        return payload


@dataclass(frozen=True)
class ConversationRoute(RegistryItem):
    source_kind: str = "manual"
    target_agent_profile_id: str = ""
    return_mode: str = "manual-copy"

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        payload.update(
            {
                "source_kind": self.source_kind,
                "target_agent_profile_id": self.target_agent_profile_id,
                "return_mode": self.return_mode,
            }
        )
        return payload


# ---------------------------------------------------------------------------
# parsers
# ---------------------------------------------------------------------------


def _base(reader: ItemReader) -> Dict[str, Any]:
    return {
        "id": reader.identifier("id"),
        "name": reader.string("name"),
        "aliases": reader.aliases(),
        "enabled": reader.boolean("enabled"),
    }


def parse_device(registry: str, position: int, raw: Any) -> Device:
    reader = ItemReader(registry, f"items[{position}]", raw)
    common = _base(reader)
    kind = reader.choice("kind", DEVICE_KINDS)
    platform = reader.choice("platform", DEVICE_PLATFORMS)
    # Bounded, plain, optional prose. It is shown to a human and is never
    # interpreted as an instruction by Cofferdam or handed to a model as one.
    notes = reader.string(
        "notes", required=False, allow_none=True, max_length=MAX_NOTES_LENGTH, allow_empty=True
    )
    reader.finish()
    return Device(kind=kind, platform=platform, notes=notes, **common)


def parse_display(registry: str, position: int, raw: Any) -> Display:
    reader = ItemReader(registry, f"items[{position}]", raw)
    common = _base(reader)
    device_id = reader.identifier("device_id")
    match_reader = reader.mapping("match", required=False)
    match = _parse_display_match(match_reader) if match_reader is not None else DisplayMatch()
    reader.finish()
    return Display(device_id=device_id, match=match, **common)


def _parse_display_match(reader: ItemReader) -> DisplayMatch:
    # A connector name is a *hint*: DP-1 becomes DP-2 when a cable moves, so it
    # can help find a panel but must never be treated as its permanent identity.
    connector_hint = reader.string("connector_hint", required=False, allow_none=True, max_length=64)
    manufacturer = reader.string("manufacturer", required=False, allow_none=True)
    model = reader.string("model", required=False, allow_none=True)
    serial = reader.string("serial", required=False, allow_none=True)
    edid = reader.string("edid_sha256", required=False, allow_none=True, max_length=64)
    if edid is not None and not _SHA256_PATTERN.match(edid):
        raise reader.fail(
            reasons.INVALID_VALUE,
            "must be exactly 64 hexadecimal characters (a SHA-256 digest)",
            field="edid_sha256",
        )
    reader.finish()
    return DisplayMatch(
        connector_hint=connector_hint,
        manufacturer=manufacturer,
        model=model,
        serial=serial,
        edid_sha256=edid.lower() if edid else None,
    )


def parse_application(registry: str, position: int, raw: Any) -> Application:
    reader = ItemReader(registry, f"items[{position}]", raw)
    common = _base(reader)
    adapter_key = reader.choice("adapter_key", APPLICATION_ADAPTER_KEYS)
    reader.finish()
    return Application(adapter_key=adapter_key, **common)


def parse_browser_profile(registry: str, position: int, raw: Any) -> BrowserProfile:
    reader = ItemReader(registry, f"items[{position}]", raw)
    common = _base(reader)
    application_id = reader.identifier("application_id")
    default_for_url = reader.boolean("default_for_url", required=False, default=False)
    preferred_display_id = reader.identifier("preferred_display_id", required=False, allow_none=True)
    launch_mode = reader.choice("launch_mode", LAUNCH_MODES)
    policy_reader = reader.mapping("domain_policy")
    policy = _parse_domain_policy(policy_reader)
    reader.finish()
    return BrowserProfile(
        application_id=application_id,
        default_for_url=default_for_url,
        preferred_display_id=preferred_display_id,
        launch_mode=launch_mode,
        domain_policy=policy,
        **common,
    )


def _parse_domain_policy(reader: ItemReader) -> DomainPolicy:
    mode = reader.choice("mode", DOMAIN_POLICY_MODES)
    raw_domains = reader.string_list("domains", max_entries=MAX_DOMAINS)
    reader.finish()

    if mode == "allow-all":
        if raw_domains:
            raise reader.fail(
                reasons.CONSTRAINT_VIOLATED,
                "'allow-all' takes no domains; use 'allow-list' to restrict",
                field="domains",
            )
        return DomainPolicy(mode=mode, domains=())

    if not raw_domains:
        raise reader.fail(
            reasons.CONSTRAINT_VIOLATED,
            "'allow-list' requires at least one hostname",
            field="domains",
        )
    normalized: List[str] = []
    for index, entry in enumerate(raw_domains):
        host = _validate_hostname(reader, f"domains[{index}]", entry)
        if host in normalized:
            raise reader.fail(
                reasons.CONSTRAINT_VIOLATED, "duplicate domain entry", field=f"domains[{index}]"
            )
        normalized.append(host)
    return DomainPolicy(mode=mode, domains=tuple(normalized))


def _validate_hostname(reader: ItemReader, where: str, value: str) -> str:
    """Accept a bare registrable hostname and nothing else.

    A scheme, path, port, credential, wildcard or regex fragment in an
    allowlist entry is always either a mistake or an attempt to widen the
    policy past what suffix matching can reason about, so each is refused by
    name rather than stripped into something that looks like it worked.
    """
    host = value.strip().rstrip(".").lower()
    if not host:
        raise reader.fail(reasons.INVALID_VALUE, "a domain entry must not be blank", field=where)
    if len(host) > MAX_HOSTNAME_LENGTH:
        raise reader.fail(
            reasons.INVALID_VALUE,
            f"a domain entry must be at most {MAX_HOSTNAME_LENGTH} characters",
            field=where,
        )
    if "://" in host or "/" in host:
        raise reader.fail(
            reasons.INVALID_VALUE,
            "a domain entry is a bare hostname: no scheme and no path",
            field=where,
        )
    if ":" in host:
        raise reader.fail(reasons.INVALID_VALUE, "a domain entry must not carry a port", field=where)
    if "@" in host:
        raise reader.fail(
            reasons.INVALID_VALUE, "a domain entry must not carry credentials", field=where
        )
    if "*" in host or "?" in host:
        raise reader.fail(
            reasons.INVALID_VALUE,
            "wildcards are not accepted; a listed domain already covers its subdomains",
            field=where,
        )
    labels = host.split(".")
    if len(labels) < 2:
        raise reader.fail(
            reasons.INVALID_VALUE,
            "a domain entry must be a dotted hostname such as example.com",
            field=where,
        )
    for label in labels:
        if not label or len(label) > MAX_LABEL_LENGTH or not _LABEL_PATTERN.match(label):
            raise reader.fail(
                reasons.INVALID_VALUE,
                "each hostname label must be 1-63 characters of a-z, 0-9 or '-', "
                "not starting or ending with '-' (use punycode for non-ASCII names)",
                field=where,
            )
    return host


def parse_agent_profile(registry: str, position: int, raw: Any) -> AgentProfile:
    reader = ItemReader(registry, f"items[{position}]", raw)
    common = _base(reader)
    adapter_kind = reader.choice("adapter_kind", AGENT_ADAPTER_KINDS)
    execution_status = reader.choice("execution_status", AGENT_EXECUTION_STATUSES)
    reader.finish()
    return AgentProfile(adapter_kind=adapter_kind, execution_status=execution_status, **common)


def parse_conversation_route(registry: str, position: int, raw: Any) -> ConversationRoute:
    reader = ItemReader(registry, f"items[{position}]", raw)
    common = _base(reader)
    source_kind = reader.choice("source_kind", ROUTE_SOURCE_KINDS)
    target_agent_profile_id = reader.identifier("target_agent_profile_id")
    return_mode = reader.choice("return_mode", ROUTE_RETURN_MODES)
    reader.finish()
    return ConversationRoute(
        source_kind=source_kind,
        target_agent_profile_id=target_agent_profile_id,
        return_mode=return_mode,
        **common,
    )


__all__ = [
    "AGENT_ADAPTER_KINDS",
    "AGENT_EXECUTION_STATUSES",
    "APPLICATION_ADAPTER_KEYS",
    "AgentProfile",
    "Application",
    "BrowserProfile",
    "ConversationRoute",
    "DEVICE_KINDS",
    "DEVICE_PLATFORMS",
    "DOMAIN_POLICY_MODES",
    "Device",
    "Display",
    "DisplayMatch",
    "DomainPolicy",
    "LAUNCH_MODES",
    "MAX_ITEMS",
    "ROUTE_RETURN_MODES",
    "ROUTE_SOURCE_KINDS",
    "RegistryError",
    "RegistryItem",
    "normalize_alias",
    "parse_agent_profile",
    "parse_application",
    "parse_browser_profile",
    "parse_conversation_route",
    "parse_device",
    "parse_display",
]
