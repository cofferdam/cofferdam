"""Which browser opens a URL, and whether it is allowed to.

This is the one place where the M2A registries actually change behaviour, so
the rules are written down here rather than spread through the adapter.

Selection
---------

===============================  ==========================================
request                          result
===============================  ==========================================
explicit ``browser_id``          that browser, or a structured refusal
explicit ``browser_profile_id``  that profile, or a structured refusal
no id, one enabled default       the default profile, if its browser is present
no id, no usable default         Opera, if it is installed (M2B3A)
none of the above                the pre-M2A legacy browser launch, unchanged
===============================  ==========================================

**An explicit selection never degrades into a different one.** Naming a profile
or a browser is a statement about which browser context may see the URL; quietly
opening it somewhere else would break the only guarantee either one offers.

Opera as the product default (M2B3A)
------------------------------------

When nothing has been configured, Cofferdam opens links in **Opera**. This is a
preference *inside Cofferdam*: it reads nothing from the desktop, changes no file
association, and leaves the operating system's own default browser exactly as the
user set it. Firefox is untouched and stays explicitly selectable — by profile,
or by ``browser_id`` on the action itself.

It sits below both configured paths on purpose. A registry default still wins,
so a user who has written down a preference keeps it; the product default only
answers the question "nothing is configured, so what should a link do?", where
the previous answer — "whichever browser sorts first in the adapter's table" —
was an implementation detail rather than a decision. The adapter's legacy order
is deliberately *not* edited to achieve this: that order is still what a host
with no Opera falls back to, and rewriting it would change the legacy path
itself rather than layering a preference above it.

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

from .adapters.base import BROWSER_KEYS
from .errors import (
    ApplicationUnavailable,
    BrowserProfileInvalid,
    ConfigurationInvalid,
    DomainNotAllowed,
)
from .media import DEFAULT_BROWSER_KEY as PRODUCT_DEFAULT_BROWSER
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
SOURCE_EXPLICIT_BROWSER = "explicit-browser"
SOURCE_DEFAULT = "default-profile"
SOURCE_PRODUCT_DEFAULT = "product-default"
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
    browser_id: Optional[str] = None,
) -> BrowserChoice:
    """Resolve a browser for ``url``, or raise a structured refusal.

    ``browser_id`` (M2B3A) names a browser directly, without a profile. It is
    the honest option for a machine that has configured no registries at all —
    "open this in Firefox" should not require writing a JSON file first — and it
    is what the phone's media cards use to pin a service to Opera. Like an
    explicit profile it never degrades: an unavailable browser is refused, not
    quietly replaced.
    """
    if browser_id is not None and browser_profile_id is not None:
        # Two explicit selections that could disagree. Picking one would
        # silently discard a statement the caller made on purpose.
        raise BrowserProfileInvalid(
            "choose either a browser or a browser profile, not both",
            "browser_id names a browser directly; browser_profile_id names a configured "
            "profile that already carries its own browser",
        )

    try:
        registries = load_registries(config)
        profiles = registries.require(BROWSER_PROFILES)
        applications = registries.require("applications")
    except RegistryError as error:
        raise ConfigurationInvalid(
            "the local browser configuration could not be read",
            error.describe(),
        ) from None

    if browser_id is not None:
        if browser_id not in BROWSER_KEYS:
            raise BrowserProfileInvalid(
                "no such browser",
                "known browsers: " + ", ".join(BROWSER_KEYS),
            )
        # The standing policy still binds. A machine whose default profile
        # restricts which domains Cofferdam may open has made a statement about
        # *this machine*, not about one browser — so naming a different browser
        # cannot become the way around it. Without a configured default there is
        # no policy to apply, exactly as on the legacy path.
        standing = default_browser_profile(profiles)
        if standing is not None:
            _require_domain_allowed(standing, url)
        if browser_id not in available_applications:
            raise ApplicationUnavailable(
                f"{browser_id} is not available on this host",
                "the browser is not installed, or is not launchable from this session",
            )
        return BrowserChoice(application_key=browser_id, source=SOURCE_EXPLICIT_BROWSER)

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
        return _product_default(available_applications)

    # The default profile's policy still binds: it was selected, so it decides.
    _require_domain_allowed(profile, url)
    try:
        return _choice(profile, applications, available_applications, SOURCE_DEFAULT)
    except ApplicationUnavailable:
        # Only an *implicit* selection may degrade, and only to the behaviour
        # that predates registries entirely. The policy check above already ran.
        return _product_default(available_applications)


def _product_default(available_applications: Sequence[str]) -> BrowserChoice:
    """Cofferdam's own preference, used only when nothing is configured.

    Falls through to :data:`LEGACY_CHOICE` when the preferred browser is not
    installed, so a host without Opera behaves exactly as it did before M2B3A
    rather than failing on a preference it cannot honour. Nothing here reads or
    writes the desktop's default-browser setting.
    """
    if PRODUCT_DEFAULT_BROWSER in available_applications:
        return BrowserChoice(
            application_key=PRODUCT_DEFAULT_BROWSER, source=SOURCE_PRODUCT_DEFAULT
        )
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
    "PRODUCT_DEFAULT_BROWSER",
    "SOURCE_DEFAULT",
    "SOURCE_EXPLICIT",
    "SOURCE_EXPLICIT_BROWSER",
    "SOURCE_LEGACY",
    "SOURCE_PRODUCT_DEFAULT",
    "BrowserChoice",
    "select_browser",
]
