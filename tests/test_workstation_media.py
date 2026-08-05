"""M2B3A: media providers, launch profiles, and Opera as the default browser.

Three things are being protected here.

**The catalogue is closed.** A client picks a provider id and types a phrase.
It cannot supply a URL, a template, a query-parameter name, a program, or a
scheme, and none of those appear in any request schema or in the catalogue the
API serves. These tests assert the *absence* of that vocabulary, not just that
the happy path works.

**Nothing claims playback.** Opening Netflix opens a page; searching Spotify
opens a search. Every media result says so explicitly, on success, because a
green toast is otherwise read as "it is playing".

**Truthful unavailability.** TV+ has no search route Cofferdam can build, so it
refuses with a reason instead of opening the home page and calling that a
search.
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cofferdam.workstation import media
from cofferdam.workstation.adapters import linux_x11
from cofferdam.workstation.adapters.base import (
    APPLICATION_KEYS,
    APPLICATION_URI_SCHEMES,
    BROWSER_KEYS,
)
from cofferdam.workstation.adapters.linux_session import GraphicalSession, SessionLaunch
from cofferdam.workstation.adapters.linux_x11 import LinuxX11Adapter
from cofferdam.workstation.browser_selection import (
    PRODUCT_DEFAULT_BROWSER,
    SOURCE_EXPLICIT_BROWSER,
    SOURCE_PRODUCT_DEFAULT,
    select_browser,
)
from cofferdam.workstation.config import load_config
from cofferdam.workstation.errors import (
    CODE_MEDIA_PROVIDER_UNKNOWN,
    CODE_MEDIA_SEARCH_UNSUPPORTED,
    AdapterUnsupported,
    ApplicationUnavailable,
    BrowserProfileInvalid,
    MediaProviderUnknown,
    MediaQueryInvalid,
    MediaSearchUnsupported,
)

from tests._workstation_doubles import WorkstationTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]

# The action schemas are pydantic; the media catalogue itself is not, and must
# not become so. Everything in this file except the two schema tests below runs
# on a bare interpreter, which is what keeps the stdlib-only CI path meaningful.
try:  # pragma: no cover - import guard
    import pydantic  # noqa: F401

    PYDANTIC_AVAILABLE = True
except Exception:  # pragma: no cover
    PYDANTIC_AVAILABLE = False

requires_schemas = unittest.skipUnless(
    PYDANTIC_AVAILABLE, "pydantic not installed: pip install -e '.[workstation]'"
)

# Every provider the milestone requires, and what it must be.
REQUIRED_PROVIDERS = {
    "spotify": media.KIND_NATIVE_APP,
    "youtube": media.KIND_WEB_SERVICE,
    "netflix": media.KIND_WEB_SERVICE,
    "prime-video": media.KIND_WEB_SERVICE,
    "tv-plus": media.KIND_WEB_SERVICE,
}


# ---------------------------------------------------------------------------
# (1-3) the catalogue itself
# ---------------------------------------------------------------------------


class CatalogueTests(unittest.TestCase):
    def test_every_required_provider_exists(self) -> None:
        """(1)"""
        self.assertEqual(set(media.PROVIDER_IDS), set(REQUIRED_PROVIDERS))

    def test_each_provider_has_the_required_kind(self) -> None:
        """(2, 3) Spotify is the native application; the rest are web services."""
        for provider_id, kind in REQUIRED_PROVIDERS.items():
            with self.subTest(provider=provider_id):
                self.assertEqual(media.get_provider(provider_id).kind, kind)

    def test_the_native_provider_names_an_allowlisted_application(self) -> None:
        provider = media.get_provider("spotify")
        self.assertIn(provider.application_key, APPLICATION_KEYS)
        self.assertIsNone(provider.home_url)
        self.assertFalse(provider.requires_browser)

    def test_every_web_service_opens_in_opera_over_https(self) -> None:
        """(4) Netflix, Prime Video, TV+ and YouTube all route to Opera."""
        for provider_id, kind in REQUIRED_PROVIDERS.items():
            if kind != media.KIND_WEB_SERVICE:
                continue
            provider = media.get_provider(provider_id)
            with self.subTest(provider=provider_id):
                self.assertEqual(provider.browser_key, "opera")
                self.assertTrue(provider.requires_browser)
                self.assertTrue(provider.home_url.startswith("https://"))

    def test_provider_ids_are_allowlisted(self) -> None:
        """(9) Near-misses fail closed rather than being normalised into a match."""
        for candidate in ("netflix ", " netflix", "Netflix", "NETFLIX", "netflix\n", "", None, 7, {}):
            with self.subTest(candidate=repr(candidate)):
                with self.assertRaises(MediaProviderUnknown) as caught:
                    media.get_provider(candidate)
                self.assertEqual(caught.exception.code, CODE_MEDIA_PROVIDER_UNKNOWN)


# ---------------------------------------------------------------------------
# (6-8, 10) query handling
# ---------------------------------------------------------------------------


class QueryValidationTests(unittest.TestCase):
    def test_queries_are_url_encoded(self) -> None:
        """(6) Reserved characters cannot escape the value they were typed into."""
        query = 'a b&c=d?e#f/g"h'
        for provider_id in ("youtube", "netflix", "prime-video"):
            with self.subTest(provider=provider_id):
                url = media.get_provider(provider_id).search_target(query).value
                # Exactly one '?' (the one the route itself contributes) and no
                # bare '&', '=' or '#' after it: the phrase cannot add a second
                # parameter, a fragment, or a path segment.
                self.assertEqual(url.count("?"), 1)
                head, _, tail = url.partition("?")
                _, _, value = tail.partition("=")
                for character in "&=#?/":
                    self.assertNotIn(character, value)
                self.assertNotIn(" ", url)

    def test_a_unicode_query_is_encoded_rather_than_dropped(self) -> None:
        url = media.get_provider("youtube").search_target("mısır türküsü").value
        self.assertIn("m%C4%B1s%C4%B1r", url)

    def test_the_spotify_uri_encodes_its_query_too(self) -> None:
        target = media.get_provider("spotify").search_target("a b/c?d")
        self.assertEqual(target.kind, media.TARGET_APPLICATION_URI)
        self.assertTrue(target.value.startswith("spotify:search:"))
        remainder = target.value[len("spotify:search:") :]
        for character in " /?:":
            self.assertNotIn(character, remainder)

    def test_control_characters_are_rejected(self) -> None:
        """(7) C0, DEL, C1 and the Unicode line separators — not just ``\\n``."""
        for bad in (
            "a\nb",       # LF
            "a\rb",       # CR
            "a\tb",       # TAB
            "a\x00b",     # NUL
            "a\x1bb",     # ESC
            "a\x7fb",     # DEL
            "a\x85b",     # C1 NEL
            "a\u2028b",   # LINE SEPARATOR
            "a\u2029b",   # PARAGRAPH SEPARATOR
        ):
            with self.subTest(query=repr(bad)):
                with self.assertRaises(MediaQueryInvalid):
                    media.validate_query(bad)

    def test_an_ordinary_phrase_with_spaces_is_accepted(self) -> None:
        """The rejections above must not have swallowed normal typing."""
        self.assertEqual(media.validate_query("  the boys  "), "the boys")

    def test_oversized_queries_are_rejected(self) -> None:
        """(8) The boundary itself is fine; one character past it is not."""
        media.validate_query("a" * media.MAX_QUERY_LENGTH)
        with self.assertRaises(MediaQueryInvalid):
            media.validate_query("a" * (media.MAX_QUERY_LENGTH + 1))

    def test_empty_and_whitespace_queries_are_rejected(self) -> None:
        for bad in ("", " ", "   ", "\t", " \t "):
            with self.subTest(query=repr(bad)):
                with self.assertRaises(MediaQueryInvalid):
                    media.validate_query(bad)

    def test_a_non_string_query_is_rejected(self) -> None:
        for bad in (None, 7, ["a"], {"a": 1}):
            with self.subTest(query=repr(bad)):
                with self.assertRaises(MediaQueryInvalid):
                    media.validate_query(bad)


# ---------------------------------------------------------------------------
# (14) truthful unavailability
# ---------------------------------------------------------------------------


class UnsupportedSearchTests(unittest.TestCase):
    def test_tv_plus_reports_search_as_unavailable(self) -> None:
        """(14) And it refuses *before* opening anything."""
        provider = media.get_provider("tv-plus")
        self.assertNotIn(media.MEDIA_ACTION_SEARCH, provider.supported_actions)
        self.assertIn(media.MEDIA_ACTION_OPEN, provider.supported_actions)
        with self.assertRaises(MediaSearchUnsupported) as caught:
            provider.search_target("anything")
        self.assertEqual(caught.exception.code, CODE_MEDIA_SEARCH_UNSUPPORTED)

    def test_an_unsupported_search_explains_itself(self) -> None:
        """A refusal with no reason is indistinguishable from a missing feature."""
        provider = media.get_provider("tv-plus")
        self.assertTrue(provider.search_unavailable_reason)
        self.assertIn("region", provider.search_unavailable_reason.lower())

    def test_every_provider_offering_search_can_actually_build_one(self) -> None:
        """`supported_actions` cannot advertise a route that does not exist."""
        for provider in media.MEDIA_PROVIDERS:
            with self.subTest(provider=provider.id):
                if provider.supports(media.MEDIA_ACTION_SEARCH):
                    self.assertTrue(provider.search_target("x").value)
                else:
                    self.assertTrue(provider.search_unavailable_reason)


# ---------------------------------------------------------------------------
# (10, 11) no client-supplied targets, no shell
# ---------------------------------------------------------------------------


class NoClientSuppliedTargetTests(unittest.TestCase):
    """(10, 11) The structural half: asserted against the source, not behaviour."""

    MEDIA_SOURCE = REPO_ROOT / "cofferdam" / "workstation" / "media.py"

    @requires_schemas
    def test_the_media_schemas_accept_only_an_id_and_a_phrase(self) -> None:
        """(10) No url, template, scheme, host, or parameter name is accepted."""
        from cofferdam.workstation.actions import (
            OpenMediaProviderParams,
            SearchMediaProviderParams,
        )

        self.assertEqual(set(OpenMediaProviderParams.model_fields), {"provider_id"})
        self.assertEqual(set(SearchMediaProviderParams.model_fields), {"provider_id", "query"})

    @requires_schemas
    def test_the_media_schemas_forbid_unknown_fields(self) -> None:
        """(10) A smuggled ``url`` is refused before any launch path is reached."""
        from cofferdam.workstation.actions import SearchMediaProviderParams

        for smuggled in ("url", "template", "search_url", "command", "browser", "scheme"):
            with self.subTest(field=smuggled):
                with self.assertRaises(Exception):
                    SearchMediaProviderParams(
                        provider_id="youtube", query="x", **{smuggled: "https://evil.example"}
                    )

    def test_the_media_module_uses_no_subprocess_and_no_shell(self) -> None:
        """(11) The catalogue builds strings; it never runs anything."""
        source = self.MEDIA_SOURCE.read_text(encoding="utf-8")
        for forbidden in ("subprocess", "os.system", "os.popen", "shell=True"):
            with self.subTest(marker=forbidden):
                self.assertNotIn(forbidden, source)

    def test_the_catalogue_imports_only_the_standard_library(self) -> None:
        """(21) So the stdlib-only CI path keeps exercising it for real.

        The action *schemas* are pydantic and are skipped on a bare interpreter.
        The catalogue — the allowlist, the query rules, and every route — must
        not be, or the checks that matter most here would quietly stop running
        on that path.
        """
        tree = ast.parse(self.MEDIA_SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported - set(sys.stdlib_module_names), set())

    def test_every_route_in_the_catalogue_is_a_source_literal(self) -> None:
        """(10) Routes are constants here, not values assembled from elsewhere.

        Parsed rather than imported so this stays a statement about the file: a
        builder that concatenated a caller-reachable name would show up as a
        non-literal left-hand side of the ``+``.
        """
        tree = ast.parse(self.MEDIA_SOURCE.read_text(encoding="utf-8"))
        # Module-level private ``_<provider>_search`` functions only. Scoped to
        # the module body rather than ``ast.walk`` so a *method* whose name ends
        # in "_search" — ``offers_structured_search`` since M2B3A.1 — cannot be
        # mistaken for a route builder and fail this check for the wrong reason.
        builders = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name.startswith("_")
            and node.name.endswith("_search")
        ]
        self.assertTrue(builders, "expected the per-provider search builders")
        for builder in builders:
            with self.subTest(builder=builder.name):
                returns = [n for n in ast.walk(builder) if isinstance(n, ast.Return)]
                self.assertEqual(len(returns), 1)
                value = returns[0].value
                self.assertIsInstance(value, ast.BinOp, "a route must be literal + encoded query")
                self.assertIsInstance(value.left, ast.Constant)
                self.assertIsInstance(value.left.value, str)
                # The right-hand side must be an encoding call, never raw text.
                self.assertIsInstance(value.right, ast.Call)
                self.assertIn(value.right.func.id, ("quote", "quote_plus"))

    def test_the_served_catalogue_exposes_no_template(self) -> None:
        """(10) The phone is never given the vocabulary to build a target."""
        for entry in media.catalogue():
            with self.subTest(provider=entry["id"]):
                for forbidden in ("search_url", "template", "search_template", "query_parameter"):
                    self.assertNotIn(forbidden, entry)
                blob = json.dumps(entry)
                self.assertNotIn("search_query", blob)
                self.assertNotIn("?q=", blob)


# ---------------------------------------------------------------------------
# (4, 5, 16, 17) browser routing
# ---------------------------------------------------------------------------


class BrowserRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = load_config(home=Path(self._tmp.name))
        self.config.ensure_dirs()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def select(self, url="https://example.com", browser_id=None, profile_id=None, available=None):
        return select_browser(
            self.config,
            url,
            profile_id,
            list(BROWSER_KEYS) if available is None else available,
            browser_id=browser_id,
        )

    def test_opera_is_the_product_default(self) -> None:
        """(4)"""
        self.assertEqual(PRODUCT_DEFAULT_BROWSER, "opera")
        self.assertEqual(media.DEFAULT_BROWSER_KEY, "opera")

    def test_a_generic_link_uses_opera(self) -> None:
        """(4) The unconfigured machine, which is every machine by default."""
        choice = self.select()
        self.assertEqual(choice.application_key, "opera")
        self.assertEqual(choice.source, SOURCE_PRODUCT_DEFAULT)

    def test_firefox_remains_explicitly_selectable(self) -> None:
        """(5) Without needing any registry file to exist first."""
        choice = self.select(browser_id="firefox")
        self.assertEqual(choice.application_key, "firefox")
        self.assertEqual(choice.source, SOURCE_EXPLICIT_BROWSER)

    def test_an_explicit_browser_never_degrades(self) -> None:
        """Naming a browser is a statement, so an absent one refuses."""
        with self.assertRaises(ApplicationUnavailable):
            self.select(browser_id="firefox", available=["opera"])

    def test_an_unknown_browser_fails_closed(self) -> None:
        for candidate in ("spotify", "lynx", "Firefox", "firefox "):
            with self.subTest(candidate=candidate):
                with self.assertRaises(BrowserProfileInvalid):
                    self.select(browser_id=candidate)

    def test_a_browser_and_a_profile_together_are_refused(self) -> None:
        """Two explicit selections that could disagree; picking one would lie."""
        with self.assertRaises(BrowserProfileInvalid):
            self.select(browser_id="firefox", profile_id="personal-opera")

    def test_an_explicit_browser_cannot_bypass_a_configured_allow_list(self) -> None:
        """The standing policy binds whichever browser is named."""
        self._seed_allow_list(["example.com"])
        self.assertEqual(self.select(url="https://example.com", browser_id="firefox").source,
                         SOURCE_EXPLICIT_BROWSER)
        from cofferdam.workstation.errors import DomainNotAllowed

        with self.assertRaises(DomainNotAllowed):
            self.select(url="https://elsewhere.example", browser_id="firefox")

    def _seed_allow_list(self, domains) -> None:
        def write(name, items):
            self.config.registry_path(name).write_text(
                json.dumps({"version": 1, "items": items}), encoding="utf-8"
            )

        write(
            "applications",
            [
                {"id": "opera", "name": "Opera", "aliases": [], "enabled": True, "adapter_key": "opera"},
                {
                    "id": "firefox",
                    "name": "Firefox",
                    "aliases": [],
                    "enabled": True,
                    "adapter_key": "firefox",
                },
            ],
        )
        write(
            "browser_profiles",
            [
                {
                    "id": "restricted",
                    "name": "Restricted",
                    "aliases": [],
                    "enabled": True,
                    "application_id": "opera",
                    "default_for_url": True,
                    "preferred_display_id": None,
                    "launch_mode": "default-instance",
                    "domain_policy": {"mode": "allow-list", "domains": list(domains)},
                }
            ],
        )


# ---------------------------------------------------------------------------
# (16, 17, 18) the real adapter's launch table
# ---------------------------------------------------------------------------


def _adapter() -> LinuxX11Adapter:
    return LinuxX11Adapter(config=None)


class AdapterLaunchTableTests(unittest.TestCase):
    def test_spotify_is_an_allowlisted_application_but_not_a_browser(self) -> None:
        self.assertIn("spotify", APPLICATION_KEYS)
        self.assertNotIn("spotify", BROWSER_KEYS)

    def test_a_url_cannot_be_opened_in_a_non_browser(self) -> None:
        with patch.object(linux_x11, "detect_graphical_session", lambda: GraphicalSession(
            available=True, session_type="x11"
        )):
            with self.assertRaises(AdapterUnsupported):
                _adapter().open_url("https://example.com", application="spotify")

    def test_opera_delegation_still_works(self) -> None:
        """(16) Opera's exit 24 still means "handed to the running instance"."""
        self.assertEqual(linux_x11._DELEGATION_EXIT_STATUS["opera"], (24,))
        launched = {}

        def fake_launch(argv, **kwargs):
            launched["argv"] = list(argv)
            launched["accept"] = kwargs.get("accept_exit_status")
            return SessionLaunch(unit="u", pid=None, state="exited", exit_status=24)

        with patch.object(linux_x11, "detect_graphical_session", lambda: GraphicalSession(
            available=True, session_type="x11"
        )), patch.object(linux_x11, "first_available", lambda names: "/snap/bin/" + names[0]), \
                patch.object(linux_x11, "launch_in_session", fake_launch), \
                patch.object(linux_x11, "process_running", lambda names: True):
            result = _adapter().open_url("https://example.com", application="opera")
        self.assertEqual(result.application, "opera")
        self.assertEqual(launched["argv"], ["/snap/bin/opera", "https://example.com"])
        self.assertEqual(launched["accept"], (24,))

    def test_firefox_launching_still_works(self) -> None:
        """(17)"""
        launched = {}

        def fake_launch(argv, **kwargs):
            launched["argv"] = list(argv)
            return SessionLaunch(unit="u", pid=99, state="running", exit_status=None)

        with patch.object(linux_x11, "detect_graphical_session", lambda: GraphicalSession(
            available=True, session_type="x11"
        )), patch.object(linux_x11, "first_available", lambda names: "/usr/bin/" + names[0]), \
                patch.object(linux_x11, "launch_in_session", fake_launch):
            result = _adapter().open_url("https://example.com", application="firefox")
        self.assertEqual(result.application, "firefox")
        self.assertEqual(launched["argv"], ["/usr/bin/firefox", "https://example.com"])

    def test_a_uri_reaches_the_application_as_one_argv_element(self) -> None:
        """(11) Never concatenated into a command, never through a shell."""
        launched = {}

        def fake_launch(argv, **kwargs):
            launched["argv"] = list(argv)
            return SessionLaunch(unit="u", pid=7, state="running", exit_status=None)

        with patch.object(linux_x11, "detect_graphical_session", lambda: GraphicalSession(
            available=True, session_type="x11"
        )), patch.object(linux_x11, "first_available", lambda names: "/snap/bin/" + names[0]), \
                patch.object(linux_x11, "launch_in_session", fake_launch):
            _adapter().open_application_uri("spotify", "spotify:search:a%20b")
        self.assertEqual(launched["argv"], ["/snap/bin/spotify", "spotify:search:a%20b"])

    def test_a_uri_scheme_outside_the_table_is_refused(self) -> None:
        """The second gate: an allowlisted key still cannot take any scheme."""
        for uri in ("https://evil.example", "file:///etc/passwd", "javascript:alert(1)", "no-scheme"):
            with self.subTest(uri=uri):
                with self.assertRaises(AdapterUnsupported):
                    _adapter().open_application_uri("spotify", uri)

    def test_only_listed_applications_accept_uris(self) -> None:
        for application in ("opera", "firefox", "chromium"):
            with self.subTest(application=application):
                with self.assertRaises(AdapterUnsupported):
                    _adapter().open_application_uri(application, "spotify:search:x")

    def test_no_unofficial_wrapper_is_required(self) -> None:
        """(18) Every launch target is a real program or a plain https URL.

        The web services are represented as URLs opened in Opera; nothing in the
        catalogue names a repackaged desktop build of a website, and the only
        native application named is one the host really installs.
        """
        self.assertEqual(
            {provider.application_key for provider in media.MEDIA_PROVIDERS if provider.application_key},
            {"spotify"},
        )
        self.assertEqual(set(linux_x11._APPLICATION_COMMANDS) - set(BROWSER_KEYS), {"spotify"})
        self.assertEqual(linux_x11._APPLICATION_COMMANDS["spotify"], ("spotify",))
        self.assertEqual(APPLICATION_URI_SCHEMES, {"spotify": ("spotify",)})


# ---------------------------------------------------------------------------
# through the API
# ---------------------------------------------------------------------------


class MediaApiTests(WorkstationTestCase):
    def providers(self) -> dict:
        response = self.client.get("/api/media/providers", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        return {entry["id"]: entry for entry in response.json()["providers"]}

    def open(self, provider_id: str):
        return self.post_action("open_media_provider", {"provider_id": provider_id})

    def search(self, provider_id: str, query: str):
        return self.post_action(
            "search_media_provider", {"provider_id": provider_id, "query": query}
        )

    # -- catalogue ----------------------------------------------------------

    def test_the_catalogue_requires_a_token(self) -> None:
        self.assertEqual(self.client.get("/api/media/providers").status_code, 401)

    def test_the_catalogue_lists_every_provider_with_availability(self) -> None:
        providers = self.providers()
        self.assertEqual(set(providers), set(REQUIRED_PROVIDERS))
        for provider_id, entry in providers.items():
            with self.subTest(provider=provider_id):
                self.assertTrue(entry["available"])
                self.assertFalse(entry["playback_control"])
                self.assertTrue(entry["limitations"])

    def test_an_uninstalled_browser_makes_web_providers_unavailable(self) -> None:
        """Availability is a live answer about this host, not a catalogue fact."""
        self.adapter.missing_applications = ("opera",)
        providers = self.providers()
        self.assertFalse(providers["netflix"]["available"])
        self.assertIn("opera", providers["netflix"]["unavailable_reason"])
        # The native provider is unaffected by a missing browser.
        self.assertTrue(providers["spotify"]["available"])

    def test_availability_is_not_a_claim_that_anything_is_running(self) -> None:
        """(19) The catalogue is definitions; it carries no runtime vocabulary."""
        for entry in self.providers().values():
            with self.subTest(provider=entry["id"]):
                for runtime_field in ("running", "instances", "pid", "windows", "playing"):
                    self.assertNotIn(runtime_field, entry)

    # -- open ---------------------------------------------------------------

    def test_spotify_opens_the_native_application(self) -> None:
        """(2) Not a browser tab, and not a wrapper."""
        record = self.open("spotify").json()
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["result"]["opened_in"], "application")
        self.assertEqual(self.adapter.launched, ["spotify"])
        self.assertEqual(self.adapter.opened_urls, [])

    def test_each_web_provider_opens_in_opera(self) -> None:
        """(4) Netflix, Prime Video, TV+ and YouTube."""
        for provider_id in ("netflix", "prime-video", "tv-plus", "youtube"):
            with self.subTest(provider=provider_id):
                adapter_urls = len(self.adapter.opened_urls)
                record = self.open(provider_id).json()
                self.assertEqual(record["status"], "succeeded")
                self.assertEqual(record["result"]["opened_in"], "browser")
                self.assertEqual(record["result"]["application"], "opera")
                self.assertEqual(self.adapter.opened_with[-1], "opera")
                self.assertEqual(len(self.adapter.opened_urls), adapter_urls + 1)

    def test_opening_does_not_claim_playback(self) -> None:
        """(13) Netflix/Prime/TV+ opened is not Netflix/Prime/TV+ playing."""
        for provider_id in ("netflix", "prime-video", "tv-plus", "spotify"):
            with self.subTest(provider=provider_id):
                result = self.open(provider_id).json()["result"]
                self.assertEqual(result["playback"], media.PLAYBACK_NOT_STARTED)
                self.assertFalse(result["playback_started"])

    # -- search -------------------------------------------------------------

    def test_youtube_search_opens_a_search_page(self) -> None:
        """(15) The results page for the words typed — not an invented result."""
        record = self.search("youtube", "moon landing").json()
        self.assertEqual(record["status"], "succeeded")
        url = self.adapter.opened_urls[-1]
        self.assertEqual(url, "https://www.youtube.com/results?search_query=moon+landing")
        self.assertEqual(self.adapter.opened_with[-1], "opera")

    def test_supported_service_searches_open_the_service_search_page(self) -> None:
        expected = {
            "netflix": "https://www.netflix.com/search?q=dark",
            "prime-video": "https://www.primevideo.com/search?phrase=dark",
        }
        for provider_id, url in expected.items():
            with self.subTest(provider=provider_id):
                self.search(provider_id, "dark")
                self.assertEqual(self.adapter.opened_urls[-1], url)
                self.assertEqual(self.adapter.opened_with[-1], "opera")

    def test_spotify_search_goes_to_the_application_and_claims_no_playback(self) -> None:
        """(12)"""
        record = self.search("spotify", "sezen aksu").json()
        self.assertEqual(record["status"], "succeeded")
        result = record["result"]
        self.assertEqual(result["opened_in"], "application")
        self.assertEqual(self.adapter.opened_uris, [("spotify", "spotify:search:sezen%20aksu")])
        self.assertEqual(self.adapter.opened_urls, [])
        self.assertEqual(result["playback"], media.PLAYBACK_NOT_STARTED)
        self.assertFalse(result["playback_started"])
        self.assertIn("nothing is playing", result["note"].lower())

    def test_an_unsupported_search_refuses_and_opens_nothing(self) -> None:
        """(14) The whole point: no home page opened and called a search."""
        response = self.search("tv-plus", "severance")
        self.assertEqual(response.status_code, 502)
        record = response.json()
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"]["code"], CODE_MEDIA_SEARCH_UNSUPPORTED)
        self.assertTrue(record["error"]["detail"])
        self.assertEqual(self.adapter.opened_urls, [])
        self.assertEqual(self.adapter.opened_uris, [])

    # -- refusals -----------------------------------------------------------

    def test_an_unknown_provider_is_refused(self) -> None:
        """(9)"""
        response = self.open("hbo-max")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "invalid_params")

    def test_the_client_cannot_supply_its_own_url_or_template(self) -> None:
        """(10)"""
        for extra in (
            {"url": "https://evil.example"},
            {"search_url": "https://evil.example/?q="},
            {"template": "{query}"},
            {"command": "rm -rf /"},
            {"browser": "firefox"},
        ):
            with self.subTest(extra=sorted(extra)):
                response = self.post_action(
                    "search_media_provider", dict({"provider_id": "youtube", "query": "x"}, **extra)
                )
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.adapter.opened_urls, [])

    def test_control_characters_and_oversized_queries_are_refused(self) -> None:
        """(7, 8) At the API boundary, not only in the catalogue."""
        for query in ("a\nb", "a\x00b", "a" * (media.MAX_QUERY_LENGTH + 1), "  "):
            with self.subTest(query=repr(query[:12])):
                response = self.search("youtube", query)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.adapter.opened_urls, [])

    # -- explicit browser selection ------------------------------------------

    def test_an_explicit_firefox_url_still_uses_firefox(self) -> None:
        """(5, 17)"""
        record = self.post_action(
            "open_url", {"url": "https://example.com", "browser_id": "firefox"}
        ).json()
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["result"]["selection"], SOURCE_EXPLICIT_BROWSER)
        self.assertEqual(self.adapter.opened_with, ["firefox"])

    def test_a_generic_url_uses_opera(self) -> None:
        """(4)"""
        record = self.post_action("open_url", {"url": "https://example.com"}).json()
        self.assertEqual(record["result"]["application"], "opera")
        self.assertEqual(self.adapter.opened_with, ["opera"])

    def test_a_url_cannot_name_a_non_browser(self) -> None:
        response = self.post_action(
            "open_url", {"url": "https://example.com", "browser_id": "spotify"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.adapter.opened_urls, [])


# ---------------------------------------------------------------------------
# (19) definitions are not instances
# ---------------------------------------------------------------------------


class DefinitionsAreNotInstancesTests(WorkstationTestCase):
    def test_opening_a_provider_does_not_add_a_runtime_instance(self) -> None:
        """(19) Runtime inventory reports what discovery found, and nothing else.

        Opening Netflix opens a page in an existing browser. There is no new
        "Netflix" application on the machine, so nothing may appear as one — a
        media definition becomes a running instance only when real discovery
        finds a process, which the stub host has none of.
        """
        before = self.client.get("/api/runtime/applications", headers=self.auth)
        self.assertEqual(before.status_code, 200)
        before_ids = {item["id"] for item in before.json()["collection"]["items"]}

        self.open_provider("netflix")
        self.open_provider("spotify")

        after = self.client.get(
            "/api/runtime/applications?refresh=true", headers=self.auth
        ).json()
        after_ids = {item["id"] for item in after["collection"]["items"]}
        self.assertEqual(before_ids, after_ids)

        blob = json.dumps(after).lower()
        for provider_id in ("netflix", "prime-video", "tv-plus"):
            with self.subTest(provider=provider_id):
                self.assertNotIn(provider_id, blob)

    def open_provider(self, provider_id: str) -> None:
        response = self.post_action("open_media_provider", {"provider_id": provider_id})
        self.assertEqual(response.status_code, 200)

    def test_the_media_catalogue_is_not_served_as_runtime(self) -> None:
        """The two layers stay separate routes with separate meanings."""
        runtime = self.client.get("/api/runtime", headers=self.auth).json()
        self.assertNotIn("media", runtime)
        self.assertNotIn("providers", runtime)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
