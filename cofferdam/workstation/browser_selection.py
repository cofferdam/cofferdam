"""Which browser opens a URL, and whether it is allowed to.

This is the one place where the M2A registries actually change behaviour, so
the rules are written down here rather than spread through the adapter.

Selection
---------

============================  =============================================
request                       result
============================  =============================================
explicit ``browser_profile_id``  that profile, or a structured refusal
no id, one enabled default    the default profile, if its browser is present
no id, no usable default      the pre-M2A legacy browser launch, unchanged
============================  =============================================

**An explicit profile never degrades into a different one.** Naming a profile is
a statement about which browser context may see the URL; quietly opening it
somewhere else would break the only guarantee a profile offers.

Policy
------

Domain policy is evaluated **before** anything launches, and it applies to
whichever profile was selected — explicit or default. It is deliberately
evaluated before the availability check too: a URL the policy forbids is
refused whether or not the browser happens to be installed, so falling back to
legacy behaviour can never become a way around an allow-list.

Backward compatibility
----------------------

A machine with no registry files has no profiles, therefore no default,
therefore takes the legacy path — so a URL-only request behaves exactly as it
did before M2A. Registries that exist but are *invalid* fail closed instead:
with an unreadable policy the honest answer is "I do not know what you allow",
and guessing "everything" is the wrong way to be wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence
from urllib.parse import urlparse

from .errors import (
    ApplicationUnavailable,
    BrowserProfileInvalid,
    ConfigurationInvalid,
    DomainNotAllowed,
)
from .registries import (
    BROWSER_PROFILES,
    BrowserProfile,
    RegistryError,
    default_browser_profile,
    load_registries,
)

# Registry adapter key -> adapter application key. They coincide today; the
# mapping exists so a registry vocabulary change cannot silently become an
# adapter vocabulary change.
ADAPTER_KEY_TO_APPLICATION = {
    "opera": "opera",
    "firefox": "firefox",
}

SOURCE_EXPLICIT = "explicit-profile"
SOURCE_DEFAULT = "default-profile"
SOURCE_LEGACY = "legacy"


@dataclass(frozen=True)
class BrowserChoice:
    """What the executor should ask the adapter for, and why."""

    application_key: Optional[str]
    profile_id: Optional[str] = None
    profile_name: Optional[str] = None
    preferred_display_id: Optional[str] = None
    source: str = SOURCE_LEGACY

    def to_result(self) -> dict:
        return {
            "browser_profile_id": self.profile_id,
            "browser_profile_name": self.profile_name,
            # Metadata only in M2A: recorded so the phone can show which panel
            # the profile prefers. Nothing here moves a window.
            "preferred_display_id": self.preferred_display_id,
            "selection": self.source,
        }


LEGACY_CHOICE = BrowserChoice(application_key=None, source=SOURCE_LEGACY)


def select_browser(
    config,
    url: str,
    browser_profile_id: Optional[str],
    available_applications: Sequence[str],
) -> BrowserChoice:
    """Resolve a browser profile for ``url``, or raise a structured refusal."""
    try:
        registries = load_registries(config)
        profiles = registries.require(BROWSER_PROFILES)
        applications = registries.require("applications")
    except RegistryError as error:
        raise ConfigurationInvalid(
            "the local browser configuration could not be read",
            error.describe(),
        ) from None

    if browser_profile_id is not None:
        profile = profiles.get(browser_profile_id)
        if profile is None:
            raise BrowserProfileInvalid(
                "no such browser profile",
                "the requested profile is not present in this machine's browser profile registry",
            )
        if not profile.enabled:
            raise BrowserProfileInvalid(
                "that browser profile is disabled",
                "enable it in the browser profile registry, or omit the profile to use the default",
            )
        _require_domain_allowed(profile, url)
        return _choice(profile, applications, available_applications, SOURCE_EXPLICIT)

    profile = default_browser_profile(profiles)
    if profile is None:
        return LEGACY_CHOICE

    # The default profile's policy still binds: it was selected, so it decides.
    _require_domain_allowed(profile, url)
    try:
        return _choice(profile, applications, available_applications, SOURCE_DEFAULT)
    except ApplicationUnavailable:
        # Only an *implicit* selection may degrade, and only to the behaviour
        # that predates registries entirely. The policy check above already ran.
        return LEGACY_CHOICE


def _choice(
    profile: BrowserProfile,
    applications,
    available_applications: Sequence[str],
    source: str,
) -> BrowserChoice:
    application = applications.get(profile.application_id)
    if application is None or not application.enabled:  # pragma: no cover - load-time invariant
        raise BrowserProfileInvalid(
            "that browser profile points at an unusable application",
            "the application it names is missing or disabled in the application registry",
        )
    application_key = ADAPTER_KEY_TO_APPLICATION.get(application.adapter_key)
    if application_key is None:  # pragma: no cover - closed vocabulary
        raise ConfigurationInvalid(
            "that browser profile uses an adapter key this build does not implement"
        )
    if application_key not in available_applications:
        raise ApplicationUnavailable(
            f"{application.name} is not available on this host",
            "the application is not installed, or is not launchable from this session",
        )
    return BrowserChoice(
        application_key=application_key,
        profile_id=profile.id,
        profile_name=profile.name,
        preferred_display_id=profile.preferred_display_id,
        source=source,
    )


def _require_domain_allowed(profile: BrowserProfile, url: str) -> None:
    hostname = _hostname(url)
    if hostname is None:
        # The action schema already required an http(s) URL with a host; this
        # is the belt to that braces, and it fails closed.
        raise DomainNotAllowed(
            "that URL has no host to check against the profile's domain policy"
        )
    if not profile.domain_policy.allows(hostname):
        raise DomainNotAllowed(
            f"'{profile.name}' does not allow that domain",
            "the profile uses an allow-list; add the domain to it, or choose another profile",
        )


def _hostname(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    try:
        host = parsed.hostname
    except ValueError:  # malformed IPv6 literal / bad port
        return None
    return host or None


__all__ = [
    "ADAPTER_KEY_TO_APPLICATION",
    "LEGACY_CHOICE",
    "SOURCE_DEFAULT",
    "SOURCE_EXPLICIT",
    "SOURCE_LEGACY",
    "BrowserChoice",
    "select_browser",
]
