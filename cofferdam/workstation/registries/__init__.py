"""Semantic registries — what this machine knows about (M2A).

Six versioned JSON documents under ``$COFFERDAM_HOME/config/registries/``
describe the world Cofferdam is allowed to talk about:

======================== ==================================================
file                     what it names
======================== ==================================================
``devices.json``         machines: this workstation, future Pi nodes, phones
``displays.json``        named panels attached to a device ("büyük monitör")
``applications.json``    allowlisted applications, by adapter key
``browser_profiles.json``semantic browser profiles and their URL policy
``agent_profiles.json``  future agent sessions — placeholders in M2A
``conversation_routes.json`` route *templates* — not implemented in M2A
======================== ==================================================

Three properties hold across all of them:

* **They are declarative, not imperative.** A registry selects among
  capabilities the code already has. It cannot introduce one: no executable,
  argv, path, desktop file, or shell fragment is representable in any schema.
* **They are semantic machine configuration, not environment variables.** Stable
  IDs, human aliases, and cross-registry references need structure and
  validation that ``KEY=value`` cannot carry.
* **They never hold secrets.** No token, cookie, password, account identifier,
  or browser profile directory belongs in these files, and no field exists that
  could hold one.

Live state — a browser tab, a ChatGPT conversation, a running task — is
deliberately *not* here. Those are runtime task records for a later milestone;
mixing them into static configuration is how a config file quietly becomes a
session store.

This package is standard-library only. The action schemas use pydantic, but
registry loading has to produce failure messages that are provably free of file
content (see :mod:`.errors`), which is easier to guarantee with explicit
validation than with a framework's own error text — and it keeps configuration
loading importable without the workstation extras.
"""

from __future__ import annotations

from .common import (
    FORBIDDEN_FIELDS,
    ID_PATTERN,
    SUPPORTED_VERSION,
    is_valid_id,
    normalize_alias,
)
from .errors import RegistryError
from .loader import (
    AGENT_PROFILES,
    APPLICATIONS,
    BROWSER_PROFILES,
    CONVERSATION_ROUTES,
    DEVICES,
    DISPLAYS,
    REGISTRY_NAMES,
    Registry,
    RegistryLoad,
    RegistrySet,
    default_browser_profile,
    empty_registry,
    load_registries,
)
from .models import (
    AGENT_ADAPTER_KINDS,
    APPLICATION_ADAPTER_KEYS,
    DEVICE_KINDS,
    DEVICE_PLATFORMS,
    DOMAIN_POLICY_MODES,
    LAUNCH_MODES,
    ROUTE_RETURN_MODES,
    ROUTE_SOURCE_KINDS,
    AgentProfile,
    Application,
    BrowserProfile,
    ConversationRoute,
    Device,
    Display,
    DisplayMatch,
    DomainPolicy,
    RegistryItem,
)
from .storage import registry_document, write_json_atomic

__all__ = [
    "AGENT_ADAPTER_KINDS",
    "AGENT_PROFILES",
    "APPLICATIONS",
    "APPLICATION_ADAPTER_KEYS",
    "AgentProfile",
    "Application",
    "BROWSER_PROFILES",
    "BrowserProfile",
    "CONVERSATION_ROUTES",
    "ConversationRoute",
    "DEVICES",
    "DEVICE_KINDS",
    "DEVICE_PLATFORMS",
    "DISPLAYS",
    "DOMAIN_POLICY_MODES",
    "Device",
    "Display",
    "DisplayMatch",
    "DomainPolicy",
    "FORBIDDEN_FIELDS",
    "ID_PATTERN",
    "LAUNCH_MODES",
    "REGISTRY_NAMES",
    "ROUTE_RETURN_MODES",
    "ROUTE_SOURCE_KINDS",
    "Registry",
    "RegistryError",
    "RegistryItem",
    "RegistryLoad",
    "RegistrySet",
    "SUPPORTED_VERSION",
    "default_browser_profile",
    "empty_registry",
    "is_valid_id",
    "load_registries",
    "normalize_alias",
    "registry_document",
    "write_json_atomic",
]
