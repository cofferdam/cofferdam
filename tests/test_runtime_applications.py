"""Application instances: one Opera, not nineteen — and never a guessed one.

The failure modes these pin are the ones that make an inventory actively
misleading rather than merely incomplete:

* nineteen Opera renderers listed as nineteen running Operas;
* a Firefox that is installed being reported as a Firefox that is running;
* a process group mapped to a definition it merely resembles;
* an application launched outside Cofferdam being invisible, which would make
  the inventory a list of what Cofferdam did rather than what is running.

The fixtures mirror what was actually observed on the validation host: Opera as
a snap scope with nineteen processes, and a GNOME-launched application that
produces two scope units for one launch.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cofferdam.workstation.runtime.applications import (
    ApplicationInstanceDiscovery,
    instance_key,
)
from cofferdam.workstation.runtime.models import STATUS_OK

from ._runtime_doubles import HOST_ID, FakeBoot, FakeProc, app_scope, session_cgroup

OPERA_SCOPE = "snap.opera.opera-c65214ed-76a4-487d-8c9b-17430c930513.scope"
OPERA_EXE = "/snap/opera/477/usr/lib/x86_64-linux-gnu/opera/opera"

# What the adapter's launch table looks like on a host with Opera and Firefox
# installed. Both definitions are *available*; only one will be running.
DEFINITIONS = {
    "firefox": ("firefox", "firefox-esr"),
    "chromium": ("chromium", "chromium-browser"),
    "opera": ("opera", "opera-stable"),
}


class ApplicationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.proc = FakeProc(Path(self._tmp.name) / "proc")
        self.boot = FakeBoot()

    def facts(self):
        import os

        from cofferdam.workstation.runtime.processes import ProcessDiscovery

        discovery = ProcessDiscovery(proc_root=str(self.proc.root), uid=os.getuid())
        return discovery.read_all()[0]

    def collect(self, definitions=None):
        discovery = ApplicationInstanceDiscovery(
            DEFINITIONS if definitions is None else definitions
        )
        return discovery.collect(HOST_ID, self.boot, self.facts())

    def add_opera(self, process_count: int = 19) -> None:
        """One browser process and its helpers, all in one snap scope."""
        self.proc.add(
            7015, comm="opera", ppid=5664, start_ticks=4278,
            cgroup=app_scope(OPERA_SCOPE), executable=OPERA_EXE,
        )
        for index in range(1, process_count):
            self.proc.add(
                7015 + index, comm="opera", ppid=7015, start_ticks=4300 + index,
                cgroup=app_scope(OPERA_SCOPE), executable=OPERA_EXE,
            )


class BrowserHelpersAreOneInstanceTests(ApplicationTestCase):
    """(8) Opera child/render processes are not separate Opera app instances."""

    def test_nineteen_opera_processes_are_one_running_application(self) -> None:
        self.add_opera(19)
        collection, _ = self.collect()

        self.assertEqual(collection.status, STATUS_OK)
        self.assertEqual(len(collection.items), 1, "each renderer became its own application")
        instance = collection.items[0]
        self.assertEqual(instance["application_id"], "opera")
        self.assertEqual(instance["process_count"], 19)
        self.assertEqual(instance["primary_pid"], 7015)
        self.assertEqual(len(instance["child_pids"]), 18)

    def test_the_root_is_the_browser_process_not_a_renderer(self) -> None:
        """Mutation check: the root must be chosen by ancestry, not by luck.

        A renderer started earlier than the browser it belongs to cannot be
        picked as the instance's main process — its parent is inside the group,
        so it is not a root.
        """
        self.proc.add(
            7015, comm="opera", ppid=5664, start_ticks=9999,
            cgroup=app_scope(OPERA_SCOPE), executable=OPERA_EXE,
        )
        self.proc.add(
            7100, comm="opera", ppid=7015, start_ticks=1,
            cgroup=app_scope(OPERA_SCOPE), executable=OPERA_EXE,
        )
        collection, _ = self.collect()
        self.assertEqual(collection.items[0]["primary_pid"], 7015)

    def test_a_second_independent_launch_is_a_second_instance(self) -> None:
        """Grouping must not become "all Opera processes are one Opera"."""
        self.add_opera(3)
        other = "snap.opera.opera-99999999-aaaa-bbbb-cccc-dddddddddddd.scope"
        self.proc.add(
            9000, comm="opera", ppid=5664, start_ticks=8000,
            cgroup=app_scope(other), executable=OPERA_EXE,
        )
        collection, _ = self.collect()
        self.assertEqual(len(collection.items), 2)

    def test_every_member_pid_maps_back_to_its_instance(self) -> None:
        self.add_opera(5)
        collection, by_pid = self.collect()
        instance_id = collection.items[0]["resource_id"]
        self.assertEqual(set(by_pid.values()), {instance_id})
        self.assertEqual(len(by_pid), 5)


class DefinitionIsNotAnInstanceTests(ApplicationTestCase):
    """(9) Firefox definition availability does not imply a running instance."""

    def test_an_available_definition_with_no_processes_produces_no_instance(self) -> None:
        self.add_opera(3)
        collection, _ = self.collect()

        running = {item["application_id"] for item in collection.items}
        self.assertIn("opera", running)
        self.assertNotIn(
            "firefox",
            running,
            "Firefox is in the definition table and is not running; the two must not be conflated",
        )

    def test_an_empty_machine_reports_no_running_applications(self) -> None:
        """Definitions present, nothing launched: an empty, successful list."""
        collection, _ = self.collect()
        self.assertEqual(collection.status, STATUS_OK)
        self.assertEqual(collection.items, ())

    def test_a_running_firefox_would_be_reported(self) -> None:
        """Mutation check on the two tests above.

        They would both pass if the collector never reported anything at all.
        This proves the collector does map a definition when the processes are
        genuinely there.
        """
        self.proc.add(
            8100, comm="firefox", ppid=5664, start_ticks=100,
            cgroup=app_scope("snap.firefox.firefox-abcd.scope"),
            executable="/snap/firefox/1234/usr/lib/firefox/firefox",
        )
        collection, _ = self.collect()
        self.assertEqual([item["application_id"] for item in collection.items], ["firefox"])


class LaunchedOutsideCofferdamTests(ApplicationTestCase):
    """(10) An application launched outside Cofferdam may still be discovered.

    Launch attribution is three-valued. The rule under test throughout is that
    the absence of Cofferdam's transient unit is never by itself evidence that
    something else performed the launch.
    """

    def test_a_snap_launch_is_unattributable_not_external(self) -> None:
        self.add_opera(3)
        collection, _ = self.collect()
        instance = collection.items[0]

        self.assertEqual(
            instance["launch_source"], "unknown",
            "a snap scope is equally consistent with either launcher",
        )
        self.assertEqual(instance["unit_kind"], "snap")
        self.assertEqual(instance["state"], "running")

    def test_a_gnome_launched_application_is_confirmed_external(self) -> None:
        """``app-gnome-`` is positive evidence: the shell named itself."""
        self.proc.add(
            5880, comm="update-notifier", ppid=5664, start_ticks=3989,
            cgroup=app_scope("app-gnome-update\\x2dnotifier-5880.scope"),
            executable="/usr/bin/update-notifier",
        )
        collection, _ = self.collect()

        self.assertEqual(collection.items[0]["launch_source"], "confirmed_external")

    def test_a_cofferdam_started_snap_is_unknown_after_reparenting(self) -> None:
        """Regression for the live-validation finding of 2026-08-05.

        Cofferdam launched Firefox through ``open_application``; snapd moved it
        out of ``cofferdam-app-<hex>.service`` into
        ``snap.firefox.firefox-<uuid>.scope`` before the first scan. The old
        boolean reported ``launched_by_cofferdam: false`` — a statement that
        something else had launched it, which was untrue.

        The only honest answer once the evidence is gone is ``unknown``, and it
        must specifically not be ``confirmed_external``.
        """
        self.proc.add(
            30041, comm="firefox", ppid=2446, start_ticks=715790,
            cgroup=app_scope(
                "snap.firefox.firefox-e914192c-b83f-4d6b-8e17-b96f7bbc045e.scope"
            ),
            executable="/snap/firefox/8107/usr/lib/firefox/firefox",
        )
        collection, _ = self.collect()
        instance = collection.items[0]

        self.assertEqual(instance["launch_source"], "unknown")
        self.assertNotIn(
            "launched_by_cofferdam", instance,
            "the boolean model was removed; it could not express this case",
        )

    def test_a_cofferdam_launched_application_is_marked_as_ours(self) -> None:
        self.proc.add(
            9100, comm="firefox", ppid=1, start_ticks=200,
            cgroup=app_scope("cofferdam-app-a1b2c3d4e5f6.service"),
            executable="/usr/bin/firefox",
        )
        collection, _ = self.collect()
        instance = collection.items[0]

        self.assertEqual(instance["launch_source"], "confirmed_cofferdam")
        self.assertEqual(instance["unit_kind"], "cofferdam")

    def test_one_gnome_launch_producing_two_scopes_is_one_instance(self) -> None:
        """Observed on the validation host: ``app-`` and ``app-gnome-`` pairs.

        Both encode the same application ID and the same launcher PID, so they
        are one launch. Reporting two would show the user a duplicate of every
        application they opened from the desktop.
        """
        for pid, unit in (
            (7358, "app-com.anthropic.Claude-7358.scope"),
            (7360, "app-gnome-com.anthropic.Claude-7358.scope"),
        ):
            self.proc.add(
                pid, comm="claude-desktop", ppid=5664, start_ticks=1000 + pid,
                cgroup=app_scope(unit), executable="/opt/Claude/claude-desktop",
            )
        collection, _ = self.collect()

        self.assertEqual(len(collection.items), 1)
        self.assertEqual(collection.items[0]["process_count"], 2)

    def test_two_different_applications_are_not_merged(self) -> None:
        """Mutation check on the merge above: the grammar must discriminate."""
        for pid, unit in (
            (100, "app-org.gnome.Nautilus-100.scope"),
            (200, "app-org.gnome.TextEditor-200.scope"),
        ):
            self.proc.add(pid, comm="app", ppid=5664, start_ticks=pid, cgroup=app_scope(unit))
        collection, _ = self.collect()
        self.assertEqual(len(collection.items), 2)


class AmbiguityStaysUnmappedTests(ApplicationTestCase):
    """(11) An ambiguous process is left unmapped rather than falsely classified."""

    def test_an_unrecognised_executable_yields_an_unmapped_instance(self) -> None:
        self.proc.add(
            9200, comm="some-app", ppid=5664, start_ticks=300,
            cgroup=app_scope("app-com.example.Thing-9200.scope"),
            executable="/opt/example/some-app",
        )
        collection, _ = self.collect()
        instance = collection.items[0]

        self.assertIsNone(instance["application_id"])
        self.assertIsNone(instance["match_method"])
        self.assertEqual(instance["display_name"], "some-app", "the name comes from the executable")

    def test_a_name_that_merely_contains_a_definition_is_not_matched(self) -> None:
        """``operator`` contains ``opera``. Substring matching would claim it."""
        self.proc.add(
            9300, comm="operator", ppid=5664, start_ticks=400,
            cgroup=app_scope("app-com.example.Operator-9300.scope"),
            executable="/usr/bin/operator",
        )
        collection, _ = self.collect()
        self.assertIsNone(collection.items[0]["application_id"])

    def test_a_bundled_helper_does_not_speak_for_the_whole_application(self) -> None:
        """An Electron application shipping a binary named ``chromium``.

        Matching on any member would report it as Chromium: wrong, and wrong in
        a way that looks entirely plausible on a card.
        """
        unit = "app-com.example.Editor-9400.scope"
        self.proc.add(
            9400, comm="editor", ppid=5664, start_ticks=500,
            cgroup=app_scope(unit), executable="/opt/editor/editor",
        )
        self.proc.add(
            9401, comm="chromium", ppid=9400, start_ticks=501,
            cgroup=app_scope(unit), executable="/opt/editor/vendor/chromium",
        )
        collection, _ = self.collect()

        self.assertEqual(len(collection.items), 1)
        self.assertIsNone(
            collection.items[0]["application_id"],
            "a bundled helper must not make the application claim to be that definition",
        )

    def test_an_adapter_with_no_launch_table_leaves_everything_unmapped(self) -> None:
        self.add_opera(3)
        collection, _ = self.collect(definitions={})
        self.assertIsNone(collection.items[0]["application_id"])
        self.assertEqual(collection.items[0]["process_count"], 3, "still discovered, just unmapped")


class InstanceBoundaryTests(unittest.TestCase):
    """What counts as an instance — asserted directly on the rule."""

    def test_an_app_slice_scope_is_an_instance(self) -> None:
        self.assertIsNotNone(
            instance_key(app_scope("app-org.gnome.Nautilus-100.scope").split("::", 1)[1],
                         "app-org.gnome.Nautilus-100.scope")
        )

    def test_a_shared_activation_unit_is_not_an_instance(self) -> None:
        """The terminal on the validation host lives in ``dbus.service``.

        Every D-Bus-activated program shares it. Treating a shared unit as an
        instance boundary would fuse unrelated applications into one card.
        """
        self.assertIsNone(
            instance_key(session_cgroup("dbus.service").split("::", 1)[1], "dbus.service")
        )

    def test_a_service_inside_app_slice_is_not_an_instance(self) -> None:
        """``dconf.service`` and ``ssh-agent.service`` live in app.slice too.

        They are session infrastructure. A ``.scope`` is what systemd creates
        for a process it did not fork itself — a launched application.
        """
        for unit in ("dconf.service", "ssh-agent.service", "gnome-keyring-daemon.service"):
            with self.subTest(unit=unit):
                self.assertIsNone(
                    instance_key(app_scope(unit).split("::", 1)[1], unit)
                )

    def test_our_own_transient_unit_is_an_instance(self) -> None:
        unit = "cofferdam-app-a1b2c3d4e5f6.service"
        key = instance_key(app_scope(unit).split("::", 1)[1], unit)
        self.assertIsNotNone(key)
        self.assertEqual(key.unit_kind, "cofferdam")

    def test_a_process_in_no_unit_belongs_to_no_instance(self) -> None:
        self.assertIsNone(instance_key(None, None))
        self.assertIsNone(instance_key("0::/", None))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
