"""Media and application launch profiles (M2B3A).

What this module is
-------------------

A **closed, code-owned catalogue** of the media services Cofferdam can open, and
the only place that turns a provider plus a user's search text into something
launchable. It is the media equivalent of the adapter's application table: a
caller picks an id from :data:`PROVIDER_IDS`, and the target is built here.

Why the catalogue is code and not a registry
--------------------------------------------

The M2A registries are descriptive — "this machine has an application called
Opera" — and deliberately cannot name a program or a URL. A media provider *is*
a URL (or a URI scheme), so putting one in a registry would hand a JSON file the
power to aim a browser anywhere. Everything here is therefore a constant in
source: the ids, the home targets, and the search builders. A client sends a
provider id and, at most, plain search text. **No caller anywhere — API, PWA, or
registry file — can supply a URL, a template, a query-parameter name, or a
program.** Adding a provider means editing this file and shipping a build.

What "open" and "search" mean
-----------------------------

Both mean *a launch was accepted and confirmed by the adapter*, and nothing
more. Opening Netflix does not mean a video is playing; searching Spotify does
not mean a track started. There is no playback control in this build, so every
result carries :data:`PLAYBACK_NOT_STARTED` rather than letting a green toast
imply something the product did not do. The seams where real playback and real
result selection would attach are documented in ``docs/MEDIA_PROFILES.md``.

Route evidence
--------------

Each search route below was checked against the live service before it was
listed, and a provider whose search route could not be confirmed exposes *open
only* with a reason a person can read. Apple TV+ is the case that forced the
distinction: its unqualified ``/search?term=`` redirects to the storefront root
and **discards the query**, so a "search" built on it would open the home page
while reporting success. That is precisely the false success this project
refuses, so TV+ ships without search until a route Cofferdam can construct
exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import quote, quote_plus

from .errors import MediaProviderUnknown, MediaQueryInvalid, MediaSearchUnsupported

# -- vocabularies ------------------------------------------------------------

KIND_NATIVE_APP = "native_app"
KIND_WEB_SERVICE = "web_service"
PROVIDER_KINDS = (KIND_NATIVE_APP, KIND_WEB_SERVICE)

MEDIA_ACTION_OPEN = "open"
MEDIA_ACTION_SEARCH = "search"
MEDIA_ACTIONS = (MEDIA_ACTION_OPEN, MEDIA_ACTION_SEARCH)

# -- capabilities (M2B3A.1) --------------------------------------------------
#
# ``supported_actions`` says what the *catalogue* offers. Capabilities answer a
# different question: what can this *host* do right now. Whether structured
# search works depends on credentials that live outside the catalogue entirely,
# and can change between two requests with no code change at all — so it cannot
# be a constant on the provider, and it is computed per request instead.
#
# A closed vocabulary, so the phone can branch on exact names and an adapter
# cannot invent a capability by returning a new string.
CAPABILITY_OPEN_HOME = "open_home"
CAPABILITY_OPEN_SEARCH_PAGE = "open_search_page"
CAPABILITY_STRUCTURED_SEARCH = "structured_search"
CAPABILITY_OPEN_SELECTED_RESULT = "open_selected_result"
CAPABILITY_OPEN_FIRST_RESULT = "open_first_result"
CAPABILITY_AUTO_OPEN_FIRST = "auto_open_first_supported"
CAPABILITY_PLAYBACK_CONTROL = "playback_control"

CAPABILITIES = (
    CAPABILITY_OPEN_HOME,
    CAPABILITY_OPEN_SEARCH_PAGE,
    CAPABILITY_STRUCTURED_SEARCH,
    CAPABILITY_OPEN_SELECTED_RESULT,
    CAPABILITY_OPEN_FIRST_RESULT,
    CAPABILITY_AUTO_OPEN_FIRST,
    CAPABILITY_PLAYBACK_CONTROL,
)

# How a built target must be handed to the host.
TARGET_URL = "url"
TARGET_APPLICATION_URI = "application_uri"

# The single honest thing this build can say about playback, on every result.
PLAYBACK_NOT_STARTED = "not_started"

# Cofferdam's own default browser for media and generic links (M2B3A). This is
# a *product* default that lives inside Cofferdam; it does not read or change
# the operating system's default browser or any file association.
DEFAULT_BROWSER_KEY = "opera"

# Search text is a bounded human phrase, not a payload. Long enough for a film
# title with a subtitle, short enough that nothing here becomes a data channel.
MAX_QUERY_LENGTH = 120


# ---------------------------------------------------------------------------
# query validation
# ---------------------------------------------------------------------------


def _is_control(character: str) -> bool:
    """C0, DEL, C1, and the two Unicode line separators.

    C1 and U+2028/U+2029 are included because "no control characters" has to
    mean it for text that will be percent-encoded into a URL and written into a
    log line, not just for the ASCII range.
    """
    codepoint = ord(character)
    return (
        codepoint < 0x20
        or codepoint == 0x7F
        or 0x80 <= codepoint <= 0x9F
        or codepoint in (0x2028, 0x2029)
    )


def validate_query(raw: object) -> str:
    """Reduce caller text to a bounded, control-free search phrase.

    Raises :class:`~cofferdam.workstation.errors.MediaQueryInvalid` rather than
    sanitising: silently stripping a rejected character would launch a search
    for something the user did not type.
    """
    if not isinstance(raw, str):
        raise MediaQueryInvalid("the search text must be a string")
    query = raw.strip()
    if not query:
        raise MediaQueryInvalid("enter something to search for")
    if len(query) > MAX_QUERY_LENGTH:
        raise MediaQueryInvalid(
            f"the search text must be at most {MAX_QUERY_LENGTH} characters",
            f"it was {len(query)} characters",
        )
    if any(_is_control(character) for character in query):
        raise MediaQueryInvalid(
            "the search text must not contain control characters",
            "line breaks, tabs and other control characters are not accepted",
        )
    return query


# ---------------------------------------------------------------------------
# targets and providers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MediaTarget:
    """Something the adapter can be asked to open, built entirely in this file.

    ``kind`` decides which adapter door it goes through: :data:`TARGET_URL`
    through the browser path, :data:`TARGET_APPLICATION_URI` through the native
    application's own registered scheme handler.
    """

    kind: str
    value: str
    application_key: Optional[str] = None


# Search builders. Each is a closure over one service's *verified* route, and
# each percent-encodes the query itself — there is no shared template string a
# provider could be pointed at, and no caller-reachable formatting step.
#
# ``quote_plus`` for query-string values (space -> ``+``, the form encoding
# every one of these services uses); ``quote(safe="")`` for the Spotify URI,
# where the phrase is a URI component rather than a form field. Both encode
# ``/``, ``?``, ``&``, ``=`` and ``#``, so a query can never grow a parameter,
# a path segment, or a fragment of its own.


def _youtube_search(query: str) -> str:
    return "https://www.youtube.com/results?search_query=" + quote_plus(query)


def _netflix_search(query: str) -> str:
    return "https://www.netflix.com/search?q=" + quote_plus(query)


def _prime_video_search(query: str) -> str:
    return "https://www.primevideo.com/search?phrase=" + quote_plus(query)


def _spotify_search(query: str) -> str:
    return "spotify:search:" + quote(query, safe="")


@dataclass(frozen=True)
class MediaProvider:
    """One thing a person can ask Cofferdam to open, and what it honestly does.

    ``limitations`` is shown verbatim on the phone. It is not marketing copy:
    it is where "opening this does not start playback" and "you must already be
    signed in" get said, so the card cannot imply more than the action performs.
    """

    id: str
    name: str
    kind: str
    supported_actions: Tuple[str, ...]
    limitations: Tuple[str, ...]
    # native_app only
    application_key: Optional[str] = None
    # web_service only
    home_url: Optional[str] = None
    browser_key: Optional[str] = None
    # search, when it is offered at all
    _search_builder: Optional[Callable[[str], str]] = None
    _search_target_kind: Optional[str] = None
    search_unavailable_reason: Optional[str] = None
    # M2B3A.1. Names the official-provider adapter that can return real results
    # for this provider, or ``None`` where no official interface is used. It is
    # a *key*, not an adapter instance: the catalogue stays a plain data
    # structure with no network machinery hanging off it, and the service maps
    # the key to a built adapter. Netflix, Prime Video and TV+ leave it ``None``
    # and therefore cannot acquire structured search by accident.
    structured_search_key: Optional[str] = None

    # -- questions the rest of the product asks -----------------------------

    def supports(self, action: str) -> bool:
        return action in self.supported_actions

    @property
    def offers_structured_search(self) -> bool:
        """Whether an official adapter exists — *not* whether it is usable.

        Usability additionally requires credentials, which is a host fact
        rather than a catalogue fact. See :meth:`capabilities`.
        """
        return self.structured_search_key is not None

    def capabilities(self, *, structured_search_configured: bool = False) -> Dict[str, bool]:
        """What this host can do with this provider, right now.

        ``structured_search_configured`` is supplied by the caller from the
        credential store. Everything downstream of structured search —
        selecting a result, opening the first one — is gated on the same fact,
        because without results there is nothing to select.

        ``playback_control`` is present and ``False`` for every provider. Saying
        it explicitly is the point: a missing key reads as "not implemented
        yet", while an explicit ``False`` is a statement that this build does
        not do it.
        """
        usable = self.offers_structured_search and structured_search_configured
        return {
            CAPABILITY_OPEN_HOME: True,
            CAPABILITY_OPEN_SEARCH_PAGE: self.supports(MEDIA_ACTION_SEARCH),
            CAPABILITY_STRUCTURED_SEARCH: usable,
            CAPABILITY_OPEN_SELECTED_RESULT: usable,
            CAPABILITY_OPEN_FIRST_RESULT: usable,
            # Deferred in M2B3A.1: only the explicit button ships. Reported as
            # a capability so the phone need not guess, and so turning it on
            # later is a change to one value rather than a new vocabulary.
            CAPABILITY_AUTO_OPEN_FIRST: False,
            CAPABILITY_PLAYBACK_CONTROL: False,
        }

    @property
    def requires_browser(self) -> bool:
        return self.kind == KIND_WEB_SERVICE

    def open_target(self) -> Optional[MediaTarget]:
        """Where "Open" goes, or ``None`` for a native application.

        ``None`` is not a missing case: opening Spotify means launching the
        installed application by its allowlisted key, which is the adapter's
        existing door and needs no target at all. Only a web service has an
        address to open. Nothing here depends on caller input.
        """
        if self.kind == KIND_NATIVE_APP:
            return None
        return MediaTarget(kind=TARGET_URL, value=self.home_url or "")

    def search_target(self, query: str) -> MediaTarget:
        """Where "Search" goes, or a refusal that says why it does not exist."""
        if not self.supports(MEDIA_ACTION_SEARCH) or self._search_builder is None:
            raise MediaSearchUnsupported(
                f"{self.name} search is not available in this build",
                self.search_unavailable_reason,
            )
        return MediaTarget(
            kind=self._search_target_kind or TARGET_URL,
            value=self._search_builder(query),
            application_key=self.application_key,
        )

    def to_dict(self, *, structured_search_configured: bool = False) -> Dict[str, object]:
        """The catalogue as the phone sees it.

        Deliberately carries **no** URL template, no query-parameter name, and
        no builder — the client picks a provider id and types a phrase, and has
        no vocabulary for anything else. ``home_url`` is included because it is
        a constant destination a person may reasonably want to read, not a
        template anything can fill in.

        Since M2B3A.1 it also carries ``capabilities``. Note what is *not* here:
        no credential value, no credential path, no key prefix, no adapter
        internals. ``structured_search_configured`` arrives as a bare boolean
        that the credential store derived; this method never sees a secret.
        """
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "capabilities": self.capabilities(
                structured_search_configured=structured_search_configured
            ),
            # Whether an official adapter exists *at all*, independent of
            # credentials. The phone needs both facts to say the right thing:
            # "not configured yet" belongs only to a provider that could be
            # configured, and Netflix/Prime/TV+ must never be offered a setup
            # hint for a feature this build was never going to give them.
            "offers_structured_search": self.offers_structured_search,
            "supported_actions": list(self.supported_actions),
            "requires_browser": self.requires_browser,
            "browser_key": self.browser_key,
            "application_key": self.application_key,
            "home_url": self.home_url,
            "limitations": list(self.limitations),
            "search_unavailable_reason": self.search_unavailable_reason,
            "playback_control": False,
        }


# ---------------------------------------------------------------------------
# the catalogue
# ---------------------------------------------------------------------------

_NO_PLAYBACK = "Opening this does not start playback — it opens the app or the page."
_SIGN_IN = "You need to be signed in to this service already; Cofferdam never signs in for you."

MEDIA_PROVIDERS: Tuple[MediaProvider, ...] = (
    MediaProvider(
        id="spotify",
        name="Spotify",
        kind=KIND_NATIVE_APP,
        application_key="spotify",
        supported_actions=(MEDIA_ACTION_OPEN, MEDIA_ACTION_SEARCH),
        # Verified on this host before it was listed: the installed Spotify
        # desktop application registers ``x-scheme-handler/spotify``, so handing
        # it a ``spotify:`` URI is its own supported entry point rather than a
        # trick. No account, token, or Web API call is involved.
        _search_builder=_spotify_search,
        _search_target_kind=TARGET_APPLICATION_URI,
        # M2B3A.1: official Web API catalogue search, when credentials exist.
        # The client-credentials flow it uses reaches only non-user endpoints,
        # so this adapter *cannot* control playback even in principle.
        structured_search_key="spotify",
        limitations=(
            "Find results uses Spotify's official catalogue search. Picking one opens that exact "
            "item in the Spotify app — it does not start playing it.",
            "Search opens Spotify's own search results for your words. "
            "It does not pick a track and it does not start playing anything.",
            "Playback control needs Spotify account consent and is not part of this build.",
        ),
    ),
    MediaProvider(
        id="youtube",
        name="YouTube",
        kind=KIND_WEB_SERVICE,
        home_url="https://www.youtube.com/",
        browser_key=DEFAULT_BROWSER_KEY,
        supported_actions=(MEDIA_ACTION_OPEN, MEDIA_ACTION_SEARCH),
        _search_builder=_youtube_search,
        _search_target_kind=TARGET_URL,
        # M2B3A.1: official Data API v3 video search, when an API key exists.
        structured_search_key="youtube",
        limitations=(
            "Find results uses YouTube's official search API. Picking one opens that exact video "
            "in Opera — it does not start playing it.",
            "Search opens the YouTube results page for your words. "
            "It does not open the first result and it does not start a video.",
            "The official API allows roughly 100 searches a day by default.",
        ),
    ),
    MediaProvider(
        id="netflix",
        name="Netflix",
        kind=KIND_WEB_SERVICE,
        home_url="https://www.netflix.com/",
        browser_key=DEFAULT_BROWSER_KEY,
        supported_actions=(MEDIA_ACTION_OPEN, MEDIA_ACTION_SEARCH),
        _search_builder=_netflix_search,
        _search_target_kind=TARGET_URL,
        limitations=(
            "Search opens Netflix's search page for your words. Nothing is selected or played.",
            _SIGN_IN,
        ),
    ),
    MediaProvider(
        id="prime-video",
        name="Prime Video",
        kind=KIND_WEB_SERVICE,
        home_url="https://www.primevideo.com/",
        browser_key=DEFAULT_BROWSER_KEY,
        supported_actions=(MEDIA_ACTION_OPEN, MEDIA_ACTION_SEARCH),
        _search_builder=_prime_video_search,
        _search_target_kind=TARGET_URL,
        limitations=(
            "Search opens Prime Video's search page for your words. Nothing is selected or played.",
            _SIGN_IN,
        ),
    ),
    MediaProvider(
        id="tv-plus",
        name="TV+",
        kind=KIND_WEB_SERVICE,
        home_url="https://tv.apple.com/",
        browser_key=DEFAULT_BROWSER_KEY,
        # Open only, and the reason is a measurement rather than a guess.
        supported_actions=(MEDIA_ACTION_OPEN,),
        search_unavailable_reason=(
            "TV+ has no search address Cofferdam can build: the plain search page redirects to "
            "the regional home page and drops the words entirely, and the form that does work "
            "needs the storefront region for your account, which Cofferdam does not know. "
            "Searching on the page itself works normally once it is open."
        ),
        limitations=(
            "Open only in this build — use the search box on the page once TV+ opens.",
            _SIGN_IN,
        ),
    ),
)

PROVIDER_IDS: Tuple[str, ...] = tuple(provider.id for provider in MEDIA_PROVIDERS)

_BY_ID: Dict[str, MediaProvider] = {provider.id: provider for provider in MEDIA_PROVIDERS}


def get_provider(provider_id: object) -> MediaProvider:
    """Resolve an id from the allowlist, or refuse.

    Compared exactly — no trimming, no case folding. A near-miss is a malformed
    or hostile request, and normalising it into a match is how an allowlist
    stops being one.
    """
    if isinstance(provider_id, str):
        provider = _BY_ID.get(provider_id)
        if provider is not None:
            return provider
    raise MediaProviderUnknown(
        "no such media provider",
        "known providers: " + ", ".join(PROVIDER_IDS),
    )


def catalogue(configured_providers: Tuple[str, ...] = ()) -> Tuple[Dict[str, object], ...]:
    """The whole catalogue, in declaration order, as plain dictionaries.

    ``configured_providers`` lists the ids whose official-search credentials the
    credential store reports as usable. It is a tuple of *ids* rather than
    anything credential-shaped, so no secret can reach this function even by
    accident.
    """
    return tuple(
        provider.to_dict(structured_search_configured=provider.id in configured_providers)
        for provider in MEDIA_PROVIDERS
    )


__all__ = [
    "DEFAULT_BROWSER_KEY",
    "KIND_NATIVE_APP",
    "KIND_WEB_SERVICE",
    "MAX_QUERY_LENGTH",
    "MEDIA_ACTIONS",
    "MEDIA_ACTION_OPEN",
    "MEDIA_ACTION_SEARCH",
    "MEDIA_PROVIDERS",
    "PLAYBACK_NOT_STARTED",
    "PROVIDER_IDS",
    "PROVIDER_KINDS",
    "TARGET_APPLICATION_URI",
    "TARGET_URL",
    "MediaProvider",
    "MediaTarget",
    "catalogue",
    "get_provider",
    "validate_query",
]
