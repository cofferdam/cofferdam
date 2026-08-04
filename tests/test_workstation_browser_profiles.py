"""M2A: browser-profile selection, domain policy at the action boundary, Opera detection.

Two things are being protected here.

**Backward compatibility.** A URL-only ``open_url`` request must behave exactly
as it did before M2A, on a machine that has no registries at all — which is
every machine until someone writes one.

**Fail-closed selection.** An explicit profile never degrades into a different
browser, an allow-list is never bypassed by an unavailable browser, and invalid
local configuration refuses rather than assuming "allow everything".
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cofferdam.workstation.adapters import linux_x11
from cofferdam.workstation.adapters.base import APPLICATION_KEYS
from cofferdam.workstation.adapters.linux_session import GraphicalSession, SessionLaunch
from cofferdam.workstation.adapters.linux_x11 import LinuxX11Adapter
from cofferdam.workstation.browser_selection import (
    SOURCE_DEFAULT,
    SOURCE_EXPLICIT,
    SOURCE_LEGACY,
    select_browser,
)
from cofferdam.workstation.config import load_config
from cofferdam.workstation.errors import (
    CODE_APPLICATION_UNAVAILABLE,
    CODE_BROWSER_PROFILE_INVALID,
    CODE_CONFIGURATION_INVALID,
    CODE_DOMAIN_NOT_ALLOWED,
    AdapterError,
    AdapterUnsupported,
    ApplicationUnavailable,
    BrowserProfileInvalid,
    ConfigurationInvalid,
    DomainNotAllowed,
)

X11_SESSION = GraphicalSession(available=True, session_type="x11")

ALL_APPLICATIONS = list(APPLICATION_KEYS)


def _envelope(items) -> str:
    return json.dumps({"version": 1, "items": items}, ensure_ascii=False)


class SelectionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = load_config(home=Path(self._tmp.name))
        self.config.ensure_dirs()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, items) -> None:
        self.config.registry_path(name).write_text(_envelope(items), encoding="utf-8")

    def seed(self, profiles=None) -> None:
        """A minimal but complete registry set: Opera + Firefox, one display."""
        self.write(
            "devices",
            [
                {
                    "id": "ubuntu-workstation",
                    "name": "Ubuntu workstation",
                    "aliases": [],
                    "enabled": True,
                    "kind": "workstation",
                    "platform": "linux",
                    "notes": None,
                }
            ],
        )
        self.write(
            "displays",
            [
                {
                    "id": "large-monitor",
                    "device_id": "ubuntu-workstation",
                    "name": "Büyük monitör",
                    "aliases": ["büyük ekran"],
                    "enabled": True,
                }
            ],
        )
        self.write(
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
        self.write("browser_profiles", profiles if profiles is not None else [self.profile()])

    @staticmethod
    def profile(**overrides) -> dict:
        item = {
            "id": "personal-opera",
            "name": "Kişisel Opera",
            "aliases": ["kişisel tarayıcı"],
            "enabled": True,
            "application_id": "opera",
            "default_for_url": True,
            "preferred_display_id": "large-monitor",
            "launch_mode": "default-instance",
            "domain_policy": {"mode": "allow-all", "domains": []},
        }
        item.update(overrides)
        return item

    def select(self, url="https://example.com", profile_id=None, available=None):
        return select_browser(
            self.config,
            url,
            profile_id,
            ALL_APPLICATIONS if available is None else available,
        )


class LegacyBehaviourTests(SelectionTestCase):
    def test_no_registries_at_all_takes_the_legacy_path(self) -> None:
        """The state every machine is in until someone writes a registry."""
        choice = self.select()
        self.assertIsNone(choice.application_key)
        self.assertIsNone(choice.profile_id)
        self.assertEqual(choice.source, SOURCE_LEGACY)

    def test_registries_with_no_profiles_take_the_legacy_path(self) -> None:
        self.seed(profiles=[])
        self.assertEqual(self.select().source, SOURCE_LEGACY)

    def test_no_enabled_default_takes_the_legacy_path(self) -> None:
        self.seed(profiles=[self.profile(default_for_url=False)])
        self.assertEqual(self.select().source, SOURCE_LEGACY)

    def test_a_disabled_default_profile_does_not_count(self) -> None:
        self.seed(profiles=[self.profile(enabled=False)])
        self.assertEqual(self.select().source, SOURCE_LEGACY)


class DefaultProfileTests(SelectionTestCase):
    def test_the_single_enabled_default_is_used(self) -> None:
        self.seed()
        choice = self.select()
        self.assertEqual(choice.application_key, "opera")
        self.assertEqual(choice.profile_id, "personal-opera")
        self.assertEqual(choice.source, SOURCE_DEFAULT)
        self.assertEqual(choice.preferred_display_id, "large-monitor")

    def test_an_unavailable_default_browser_falls_back_to_legacy(self) -> None:
        self.seed()
        choice = self.select(available=["firefox"])
        self.assertIsNone(choice.application_key)
        self.assertEqual(choice.source, SOURCE_LEGACY)

    def test_the_default_profiles_policy_still_binds(self) -> None:
        """Selecting a profile implicitly does not exempt it from its own policy."""
        self.seed(
            profiles=[
                self.profile(domain_policy={"mode": "allow-list", "domains": ["example.com"]})
            ]
        )
        self.assertEqual(self.select(url="https://example.com").source, SOURCE_DEFAULT)
        with self.assertRaises(DomainNotAllowed):
            self.select(url="https://badexample.com")

    def test_policy_is_checked_before_availability(self) -> None:
        """Otherwise an uninstalled browser would be a way around an allow-list."""
        self.seed(
            profiles=[
                self.profile(domain_policy={"mode": "allow-list", "domains": ["example.com"]})
            ]
        )
        with self.assertRaises(DomainNotAllowed):
            self.select(url="https://badexample.com", available=["firefox"])


class ExplicitProfileTests(SelectionTestCase):
    def test_an_explicit_profile_is_used(self) -> None:
        self.seed(
            profiles=[
                self.profile(default_for_url=False),
                self.profile(
                    id="fallback-firefox",
                    name="Yedek Firefox",
                    aliases=["yedek"],
                    application_id="firefox",
                    default_for_url=True,
                    preferred_display_id=None,
                ),
            ]
        )
        choice = self.select(profile_id="personal-opera")
        self.assertEqual(choice.application_key, "opera")
        self.assertEqual(choice.source, SOURCE_EXPLICIT)

    def test_an_unknown_profile_fails_closed(self) -> None:
        self.seed()
        with self.assertRaises(BrowserProfileInvalid) as caught:
            self.select(profile_id="no-such-profile")
        self.assertEqual(caught.exception.code, CODE_BROWSER_PROFILE_INVALID)

    def test_a_disabled_profile_fails_closed(self) -> None:
        self.seed(profiles=[self.profile(enabled=False, default_for_url=False)])
        with self.assertRaises(BrowserProfileInvalid):
            self.select(profile_id="personal-opera")

    def test_an_explicit_profile_never_falls_back(self) -> None:
        """Naming a profile is a statement about which browser may see the URL."""
        self.seed(
            profiles=[
                self.profile(default_for_url=False),
                self.profile(
                    id="fallback-firefox",
                    name="Yedek Firefox",
                    aliases=["yedek"],
                    application_id="firefox",
                    default_for_url=True,
                    preferred_display_id=None,
                ),
            ]
        )
        with self.assertRaises(ApplicationUnavailable) as caught:
            self.select(profile_id="personal-opera", available=["firefox"])
        self.assertEqual(caught.exception.code, CODE_APPLICATION_UNAVAILABLE)

    def test_an_explicit_profile_enforces_its_domain_policy(self) -> None:
        self.seed(
            profiles=[
                self.profile(
                    default_for_url=False,
                    domain_policy={"mode": "allow-list", "domains": ["example.com"]},
                )
            ]
        )
        self.assertEqual(
            self.select(url="https://docs.example.com", profile_id="personal-opera").source,
            SOURCE_EXPLICIT,
        )
        with self.assertRaises(DomainNotAllowed) as caught:
            self.select(url="https://badexample.com", profile_id="personal-opera")
        self.assertEqual(caught.exception.code, CODE_DOMAIN_NOT_ALLOWED)


class InvalidConfigurationTests(SelectionTestCase):
    def test_invalid_registries_fail_closed_rather_than_allowing_everything(self) -> None:
        self.seed()
        self.config.registry_path("browser_profiles").write_text("{broken", encoding="utf-8")
        with self.assertRaises(ConfigurationInvalid) as caught:
            self.select()
        self.assertEqual(caught.exception.code, CODE_CONFIGURATION_INVALID)

    def test_the_configuration_error_leaks_no_path(self) -> None:
        self.seed()
        self.config.registry_path("applications").write_text("{broken", encoding="utf-8")
        with self.assertRaises(ConfigurationInvalid) as caught:
            self.select()
        self.assertNotIn(str(self.config.home), str(caught.exception.detail or ""))


# ---------------------------------------------------------------------------
# Opera detection in the Linux adapter
# ---------------------------------------------------------------------------


def _adapter() -> LinuxX11Adapter:
    return LinuxX11Adapter(config=None)


class OperaDetectionTests(unittest.TestCase):
    def test_opera_candidates_are_bounded_and_carry_no_paths(self) -> None:
        self.assertEqual(linux_x11._APPLICATION_COMMANDS["opera"], ("opera", "opera-stable"))
        for candidate in linux_x11._APPLICATION_COMMANDS["opera"]:
            with self.subTest(candidate=candidate):
                self.assertNotIn("/", candidate)
                self.assertNotIn("\\", candidate)

    def test_opera_desktop_entry_candidates_are_bare_basenames(self) -> None:
        for candidate in linux_x11._APPLICATION_DESKTOP_ENTRIES["opera"]:
            with self.subTest(candidate=candidate):
                self.assertTrue(candidate.endswith(".desktop"))
                self.assertNotIn("/", candidate)

    def test_opera_is_an_allowlisted_application_key(self) -> None:
        self.assertIn("opera", APPLICATION_KEYS)

    def test_adding_opera_did_not_change_the_legacy_browser_preference(self) -> None:
        """Firefox is still chosen first when no profile selects otherwise."""
        self.assertEqual(list(linux_x11._APPLICATION_COMMANDS)[0], "firefox")
        self.assertEqual(list(linux_x11._APPLICATION_COMMANDS)[-1], "opera")

    def test_available_applications_reports_opera_when_on_path(self) -> None:
        with patch.object(linux_x11, "first_available", lambda names: "/snap/bin/" + names[0]):
            self.assertIn("opera", _adapter().available_applications())

    def test_available_applications_omits_opera_when_absent(self) -> None:
        def only_firefox(names):
            return "/usr/bin/firefox" if "firefox" in names else None

        with patch.object(linux_x11, "first_available", only_firefox):
            self.assertEqual(_adapter().available_applications(), ["firefox"])

    def test_desktop_entry_presence_does_not_make_an_application_launchable(self) -> None:
        """Status must offer only what can actually be launched."""
        with patch.object(linux_x11, "first_available", lambda names: None):
            with patch.object(linux_x11, "_desktop_entry_present", lambda names: True):
                self.assertEqual(_adapter().available_applications(), [])

    def test_a_desktop_entry_explains_an_absence(self) -> None:
        with patch.object(linux_x11, "_desktop_entry_present", lambda names: True):
            detail = _adapter().unavailable_detail("opera")
        self.assertIn("desktop entry", detail)

    def test_no_desktop_entry_gives_no_extra_detail(self) -> None:
        with patch.object(linux_x11, "_desktop_entry_present", lambda names: False):
            self.assertIsNone(_adapter().unavailable_detail("opera"))

    def test_desktop_entry_search_only_looks_at_fixed_directories(self) -> None:
        for directory in linux_x11._DESKTOP_ENTRY_DIRECTORIES:
            with self.subTest(directory=directory):
                self.assertTrue(directory.startswith("/"))
        self.assertIn("/var/lib/snapd/desktop/applications", linux_x11._DESKTOP_ENTRY_DIRECTORIES)


class OperaLaunchTests(unittest.TestCase):
    def test_an_explicit_application_is_launched_with_a_fixed_argv(self) -> None:
        recorded = {}

        def fake_launch(argv, **kwargs):
            recorded["argv"] = list(argv)
            return SessionLaunch(unit="u", pid=99, state="running", exit_status=None)

        with patch.object(linux_x11, "detect_graphical_session", lambda: X11_SESSION):
            with patch.object(linux_x11, "first_available", lambda names: "/snap/bin/" + names[0]):
                with patch.object(linux_x11, "launch_in_session", fake_launch):
                    launch = _adapter().open_url("https://example.com", application="opera")

        self.assertEqual(recorded["argv"], ["/snap/bin/opera", "https://example.com"])
        self.assertEqual(launch.application, "opera")
        self.assertEqual(launch.pid, 99)

    def test_a_missing_explicit_application_is_reported_unavailable(self) -> None:
        with patch.object(linux_x11, "detect_graphical_session", lambda: X11_SESSION):
            with patch.object(linux_x11, "first_available", lambda names: None):
                with self.assertRaises(AdapterUnsupported) as caught:
                    _adapter().open_url("https://example.com", application="opera")
        self.assertIn("not installed", str(caught.exception))

    def test_a_non_allowlisted_application_is_refused(self) -> None:
        with patch.object(linux_x11, "detect_graphical_session", lambda: X11_SESSION):
            with self.assertRaises(AdapterUnsupported):
                _adapter().open_url("https://example.com", application="/bin/sh")

    def test_url_only_open_still_uses_the_legacy_first_installed_browser(self) -> None:
        recorded = {}

        def fake_launch(argv, **kwargs):
            recorded["argv"] = list(argv)
            return SessionLaunch(unit="u", pid=7, state="running", exit_status=None)

        def firefox_and_opera(names):
            return "/usr/bin/" + names[0] if names[0] in ("firefox", "opera") else None

        with patch.object(linux_x11, "detect_graphical_session", lambda: X11_SESSION):
            with patch.object(linux_x11, "first_available", firefox_and_opera):
                with patch.object(linux_x11, "launch_in_session", fake_launch):
                    launch = _adapter().open_url("https://example.com")

        self.assertEqual(launch.application, "firefox")
        self.assertEqual(recorded["argv"], ["/usr/bin/firefox", "https://example.com"])

    def test_a_graphical_session_is_still_required(self) -> None:
        no_session = GraphicalSession(available=False, reason="no graphical session")
        with patch.object(linux_x11, "detect_graphical_session", lambda: no_session):
            with self.assertRaises(AdapterUnsupported):
                _adapter().open_url("https://example.com", application="opera")


class DelegationExitStatusTests(unittest.TestCase):
    """Opera exits 24 after handing a URL to a running instance.

    Observed on the real host during M2A validation: snap-packaged Opera 133
    prints "Opening in existing browser session.", opens the tab, and exits 24
    (Chromium's ``CHROME_RESULT_CODE_NORMAL_EXIT_PROCESS_NOTIFIED``). systemd
    marks that unit ``failed``, so before this the adapter called a tab that had
    visibly opened an error.

    The M1 rule still holds and is asserted below: the exit code alone is never
    enough. Only this exact code, only for this application, and only when a
    live instance can actually be seen.
    """

    def test_only_opera_declares_a_delegation_exit_status(self) -> None:
        self.assertEqual(linux_x11._DELEGATION_EXIT_STATUS, {"opera": (24,)})

    def test_an_accepted_exit_status_is_reported_as_exited_not_running(self) -> None:
        from cofferdam.workstation.adapters import linux_session

        properties = {"ActiveState": "failed", "Result": "exit-code", "ExecMainStatus": "24"}
        with patch.object(linux_session, "_show_unit", lambda unit: properties):
            with patch.object(linux_session, "_reset_failed", lambda pattern: None):
                launch = linux_session._observe("unit", 0.0, accept_exit_status=(24,))
        self.assertEqual(launch.state, "exited")
        self.assertEqual(launch.exit_status, 24)

    def test_an_unexpected_exit_status_still_fails(self) -> None:
        from cofferdam.workstation.adapters import linux_session

        properties = {"ActiveState": "failed", "Result": "exit-code", "ExecMainStatus": "1"}
        with patch.object(linux_session, "_show_unit", lambda unit: properties):
            with patch.object(linux_session, "_reset_failed", lambda pattern: None):
                with self.assertRaises(AdapterError):
                    linux_session._observe("unit", 0.0, accept_exit_status=(24,))

    def test_a_delegating_exit_succeeds_only_when_an_instance_is_visible(self) -> None:
        def fake_launch(argv, **kwargs):
            self.assertEqual(kwargs.get("accept_exit_status"), (24,))
            return SessionLaunch(unit="u", pid=None, state="exited", exit_status=24)

        with patch.object(linux_x11, "detect_graphical_session", lambda: X11_SESSION):
            with patch.object(linux_x11, "first_available", lambda names: "/snap/bin/" + names[0]):
                with patch.object(linux_x11, "launch_in_session", fake_launch):
                    with patch.object(linux_x11, "process_running", lambda names: True):
                        launch = _adapter().open_url("https://example.com", application="opera")
                    self.assertEqual(launch.application, "opera")

                    # Same exit code, but nothing is running: that is a real
                    # failure and must not be reported as a success.
                    with patch.object(linux_x11, "process_running", lambda names: False):
                        with self.assertRaises(AdapterError):
                            _adapter().open_url("https://example.com", application="opera")

    def test_firefox_gets_no_delegation_allowance(self) -> None:
        """The relaxation is per-application and does not spread."""

        def fake_launch(argv, **kwargs):
            self.assertEqual(kwargs.get("accept_exit_status"), ())
            return SessionLaunch(unit="u", pid=1, state="running", exit_status=None)

        with patch.object(linux_x11, "detect_graphical_session", lambda: X11_SESSION):
            with patch.object(linux_x11, "first_available", lambda names: "/usr/bin/" + names[0]):
                with patch.object(linux_x11, "launch_in_session", fake_launch):
                    _adapter().open_url("https://example.com", application="firefox")

    def test_opera_process_names_allow_corroboration(self) -> None:
        self.assertIn("opera", linux_x11._APPLICATION_PROCESS_NAMES["opera"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
