"""Which running groups reach the front page, and on what evidence.

The defect these pin is not a wrong fact — the inventory was already correct.
It is a wrong *product*: real-client validation on the phone found Opera and
Firefox listed beside ``evolution-alarm-notify``, ``gsd-disk-utility-notify``
and ``update-notifier``, as though a person might want to click a notification
daemon. Completeness is right for an API and wrong for a control plane's
primary list.

So classification has to satisfy two things at once, and the tests below hold
both:

* the helpers leave the primary list;
* the helpers are still **there**, discoverable one level down, because moving
  data out of sight is acceptable and dropping it is not.

Every fixture builds real ``.desktop`` files in a temporary directory. Nothing
here reads the developer's machine, and nothing classifies by name — a helper
is background because its entry says ``NoDisplay=true`` or because it sits in an
autostart directory, which is what those declarations are for.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cofferdam.workstation.runtime.applications import (
    ApplicationInstanceDiscovery,
    PRESENTATION_BACKGROUND,
    PRESENTATION_UNCLASSIFIED,
    PRESENTATION_USER_FACING,
    desktop_application_id,
)
from cofferdam.workstation.runtime.desktop_entries import DesktopEntryIndex

from ._runtime_doubles import HOST_ID, FakeBoot, FakeProc, app_scope

DEFINITIONS = {"firefox": ("firefox",), "opera": ("opera",)}

VISIBLE_ENTRY = """[Desktop Entry]
Type=Application
Name=Example Editor
Categories=Utility;
"""

NO_DISPLAY_ENTRY = """[Desktop Entry]
Type=Application
Name=Example Helper
NoDisplay=true
"""

HIDDEN_ENTRY = """[Desktop Entry]
Type=Application
Name=Example Hidden
Hidden=true
"""


class PresentationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.proc = FakeProc(root / "proc")
        self.boot = FakeBoot()
        self.menu = root / "applications"
        self.autostart = root / "autostart"
        self.menu.mkdir()
        self.autostart.mkdir()
        self.home = root / "home"
        self.home.mkdir()

    def write_menu_entry(self, application_id: str, body: str = VISIBLE_ENTRY) -> None:
        (self.menu / (application_id + ".desktop")).write_text(body, encoding="utf-8")

    def write_autostart_entry(self, application_id: str, body: str = NO_DISPLAY_ENTRY) -> None:
        (self.autostart / (application_id + ".desktop")).write_text(body, encoding="utf-8")

    def collect(self):
        import os

        from cofferdam.workstation.runtime.processes import ProcessDiscovery

        facts = ProcessDiscovery(
            proc_root=str(self.proc.root), uid=os.getuid()
        ).read_all()[0]
        discovery = ApplicationInstanceDiscovery(
            DEFINITIONS,
            entries=DesktopEntryIndex(
                menu_directories=[str(self.menu)],
                autostart_directories=[str(self.autostart)],
                home=str(self.home),
            ),
        )
        return discovery.collect(HOST_ID, self.boot, facts)[0]

    def by_name(self, collection):
        return {item["display_name"]: item for item in collection.items}

    # -- fixtures mirroring the validation host ------------------------------

    def add_browser(self) -> None:
        """A snap browser that matches a configured application definition."""
        self.proc.add(
            7015, comm="opera", ppid=5664, start_ticks=4278,
            cgroup=app_scope("snap.opera.opera-c65214ed.scope"),
            executable="/snap/opera/477/usr/lib/x86_64-linux-gnu/opera/opera",
        )

    def add_desktop_application(self) -> None:
        """A GNOME-launched application with an ordinary visible menu entry."""
        self.write_menu_entry("com.example.Editor")
        self.proc.add(
            7358, comm="editor", ppid=5664, start_ticks=4364,
            cgroup=app_scope("app-gnome-com.example.Editor-7358.scope"),
            executable="/usr/lib/example/editor",
        )

    def add_notification_helper(self) -> None:
        """A helper that declares ``NoDisplay`` and autostarts, as GNOME's do."""
        self.write_autostart_entry("com.example.AlarmNotify")
        self.proc.add(
            5865, comm="alarm-notify", ppid=5664, start_ticks=3989,
            cgroup=app_scope("app-gnome-com.example.AlarmNotify-5865.scope"),
            executable="/usr/libexec/example/alarm-notify",
        )

    def add_unrecognised_group(self) -> None:
        """A real scope that encodes no application ID at all."""
        self.proc.add(
            12758, comm="bash", ppid=12717, start_ticks=4500,
            cgroup=app_scope("example-spawn-3349f508.scope"),
            executable="/usr/bin/bash",
        )


class PrimaryListHoldsUserFacingApplicationsTests(PresentationTestCase):
    def test_a_definition_match_is_user_facing(self) -> None:
        self.add_browser()
        item = self.collect().items[0]
        self.assertEqual(item["presentation"], PRESENTATION_USER_FACING)
        self.assertEqual(item["presentation_evidence"], "application-definition")

    def test_a_visible_desktop_entry_is_user_facing_without_a_definition(self) -> None:
        """The requirement is "credible desktop application", not "allowlisted".

        Claude Desktop on the validation host matches no application definition
        and is plainly something the user opened. Its visible ``.desktop`` entry
        is the evidence, and it is enough.
        """
        self.add_desktop_application()
        item = self.collect().items[0]
        self.assertIsNone(item["application_id"])
        self.assertEqual(item["presentation"], PRESENTATION_USER_FACING)
        self.assertEqual(item["presentation_evidence"], "desktop-entry-visible")

    def test_background_helpers_are_not_in_the_primary_list(self) -> None:
        self.add_browser()
        self.add_desktop_application()
        self.add_notification_helper()

        collection = self.collect()
        primary = [
            item["display_name"] for item in collection.items
            if item["presentation"] == PRESENTATION_USER_FACING
        ]

        self.assertEqual(len(primary), 2)
        self.assertNotIn("alarm-notify", primary)


class BackgroundHelpersRemainDiscoverableTests(PresentationTestCase):
    def test_a_background_helper_is_still_returned_by_the_api(self) -> None:
        """Moved out of the primary list, not out of the collection."""
        self.add_browser()
        self.add_notification_helper()

        collection = self.collect()
        names = self.by_name(collection)

        self.assertEqual(len(collection.items), 2, "nothing may be dropped from the inventory")
        self.assertIn("alarm-notify", names)
        self.assertEqual(names["alarm-notify"]["presentation"], PRESENTATION_BACKGROUND)
        self.assertEqual(names["alarm-notify"]["state"], "running")

    def test_the_evidence_for_demotion_is_reported(self) -> None:
        """A classification the user cannot audit is just a different guess."""
        self.add_notification_helper()
        item = self.collect().items[0]
        self.assertIn(
            item["presentation_evidence"],
            {"desktop-entry-nodisplay", "xdg-autostart-entry"},
        )

    def test_nodisplay_alone_is_enough_without_autostart(self) -> None:
        self.write_menu_entry("com.example.Helper", NO_DISPLAY_ENTRY)
        self.proc.add(
            6000, comm="helper", ppid=5664, start_ticks=4000,
            cgroup=app_scope("app-gnome-com.example.Helper-6000.scope"),
            executable="/usr/libexec/example/helper",
        )
        item = self.collect().items[0]
        self.assertEqual(item["presentation"], PRESENTATION_BACKGROUND)
        self.assertEqual(item["presentation_evidence"], "desktop-entry-nodisplay")

    def test_hidden_alone_is_enough(self) -> None:
        self.write_menu_entry("com.example.Gone", HIDDEN_ENTRY)
        self.proc.add(
            6001, comm="gone", ppid=5664, start_ticks=4001,
            cgroup=app_scope("app-gnome-com.example.Gone-6001.scope"),
            executable="/usr/libexec/example/gone",
        )
        item = self.collect().items[0]
        self.assertEqual(item["presentation"], PRESENTATION_BACKGROUND)
        self.assertEqual(item["presentation_evidence"], "desktop-entry-hidden")


class ClassificationIsEvidenceNotNameMatchingTests(PresentationTestCase):
    def test_a_visible_application_whose_name_reads_like_a_daemon_is_user_facing(self) -> None:
        """Mutation check against the substring shortcut this replaced.

        A name containing "notifier" classifies on its entry, not its letters.
        Had classification been ``"notif" in name``, this would be background.
        """
        self.write_menu_entry("com.example.NotifierPro", VISIBLE_ENTRY)
        self.proc.add(
            6100, comm="notifier-pro", ppid=5664, start_ticks=4100,
            cgroup=app_scope("app-gnome-com.example.NotifierPro-6100.scope"),
            executable="/usr/bin/notifier-pro",
        )
        item = self.collect().items[0]
        self.assertEqual(item["presentation"], PRESENTATION_USER_FACING)

    def test_an_unrecognised_group_is_unclassified_not_promoted(self) -> None:
        """Uncertain goes to Other. Guessing "probably an app" is how a control
        plane grows a front page full of daemons."""
        self.add_unrecognised_group()
        item = self.collect().items[0]
        self.assertEqual(item["presentation"], PRESENTATION_UNCLASSIFIED)
        self.assertEqual(item["presentation_evidence"], "no-desktop-entry")

    def test_a_missing_desktop_entry_is_not_read_as_background(self) -> None:
        """Absent evidence is not evidence. It must not silently demote."""
        self.add_unrecognised_group()
        item = self.collect().items[0]
        self.assertNotEqual(item["presentation"], PRESENTATION_BACKGROUND)


class DesktopApplicationIdTests(unittest.TestCase):
    def test_a_gnome_scope_yields_the_application_id(self) -> None:
        self.assertEqual(
            desktop_application_id(["app-gnome-com.example.Editor-7358.scope"]),
            "com.example.Editor",
        )

    def test_systemd_escapes_are_decoded(self) -> None:
        self.assertEqual(
            desktop_application_id(["app-gnome-update\\x2dnotifier-5880.scope"]),
            "update-notifier",
        )

    def test_a_snap_scope_yields_snapd_desktop_naming(self) -> None:
        self.assertEqual(
            desktop_application_id(["snap.firefox.firefox-e914192c.scope"]),
            "firefox_firefox",
        )

    def test_an_unrecognised_scope_yields_nothing(self) -> None:
        self.assertIsNone(desktop_application_id(["example-spawn-3349f508.scope"]))


class DesktopEntryLookupIsClosedTests(unittest.TestCase):
    """A unit name is parsed text; a desktop entry is a file read.

    These must not meet on a path anything outside this code can influence.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.menu = root / "applications"
        self.menu.mkdir()
        # Separate directories: sharing one would make every menu entry look
        # autostarted, which is a property of the fixture rather than the code.
        autostart = root / "autostart"
        autostart.mkdir()
        self.index = DesktopEntryIndex(
            menu_directories=[str(self.menu)],
            autostart_directories=[str(autostart)],
            home=str(root),
        )

    def test_a_traversal_sequence_is_rejected(self) -> None:
        self.assertIsNone(self.index.lookup("../../etc/passwd"))

    def test_a_separator_is_rejected(self) -> None:
        self.assertIsNone(self.index.lookup("/etc/shadow"))

    def test_an_absent_entry_is_none_not_a_guess(self) -> None:
        self.assertIsNone(self.index.lookup("com.example.NotInstalled"))

    def test_only_the_desktop_entry_group_is_read(self) -> None:
        """A later group must not override the classification keys."""
        (self.menu / "com.example.Two.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Two\n"
            "[Desktop Action Extra]\nNoDisplay=true\n",
            encoding="utf-8",
        )
        entry = self.index.lookup("com.example.Two")
        self.assertIsNotNone(entry)
        self.assertFalse(entry.no_display)
        self.assertTrue(entry.is_visible_application)


if __name__ == "__main__":
    unittest.main()
