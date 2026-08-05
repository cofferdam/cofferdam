"""(18) The PWA never displays example data as live inventory.

The live view is the place where a well-meaning "let's show something so the
panel isn't empty" does the most damage: a card that looks discovered *is* a
claim that it was discovered. These tests scan ``web/live.js`` and
``web/index.html`` for the shapes that claim would take.

They are structural scans rather than DOM tests because the property is about
what the file can possibly render. A rendering test proves one path is honest;
a scan proves the dishonest path is not in the file at all.

Comments are stripped before scanning. A guard that trips on the sentence
explaining why the rule exists teaches the next author to delete the
explanation, which is the opposite of what these tests are for.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from ._runtime_doubles import code_only

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"
EXAMPLES_DIR = REPO_ROOT / "examples" / "registries"


def live_code() -> str:
    return code_only((WEB_DIR / "live.js").read_text(encoding="utf-8"))


class NoSampleDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.live_js = live_code()
        self.index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")

    def test_the_live_view_ships_no_placeholder_resource(self) -> None:
        """No demo monitor, no demo browser, in the code that renders live cards."""
        banned = (
            "example-",
            "eDP-1",
            "HDMI-1",
            "1920x1080",
            "büyük",
            "küçük",
            "sample",
            "demo",
            "placeholder",
            "lorem",
        )
        lowered = self.live_js.lower()
        for text in banned:
            with self.subTest(text=text):
                self.assertNotIn(text.lower(), lowered)

    def test_the_live_view_holds_no_hardcoded_resource_array(self) -> None:
        """A literal array of card-shaped objects is what sample data looks like."""
        self.assertNotIn("resource_id:", self.live_js)
        self.assertNotIn('"resource_id"', self.live_js)

    def test_no_example_registry_id_appears_anywhere_in_the_web_client(self) -> None:
        identifiers = set()
        for path in sorted(EXAMPLES_DIR.glob("*.json")):
            for match in re.finditer(r'"id"\s*:\s*"(example[^"]*)"', path.read_text(encoding="utf-8")):
                identifiers.add(match.group(1))
        self.assertTrue(identifiers, "the examples should contain example-prefixed ids")

        for path in sorted(WEB_DIR.glob("*")):
            if not path.is_file() or path.suffix not in (".js", ".html", ".css"):
                continue
            body = path.read_text(encoding="utf-8")
            for identifier in identifiers:
                with self.subTest(file=path.name, identifier=identifier):
                    self.assertNotIn(identifier, body)


class UnavailableIsNotEmptyTests(unittest.TestCase):
    """The view must render the two states differently, or the backend's care is wasted."""

    def setUp(self) -> None:
        self.live_js = live_code()

    def test_an_unavailable_collection_renders_the_backend_reason(self) -> None:
        self.assertIn('collection.status === "unavailable"', self.live_js)
        self.assertIn("collection.reason", self.live_js)

    def test_an_empty_ok_collection_renders_a_different_message(self) -> None:
        self.assertIn("None found right now", self.live_js)

    def test_the_unavailable_branch_comes_before_the_empty_branch(self) -> None:
        """Order matters: an unavailable collection has zero items too.

        Checking emptiness first would render every unavailable collection as
        "none found", which is the exact false statement this milestone exists
        to prevent.
        """
        unavailable_at = self.live_js.index('collection.status === "unavailable"')
        empty_at = self.live_js.index("collection.items.length")
        self.assertLess(unavailable_at, empty_at)

    def test_a_missing_window_count_is_never_rendered_as_zero(self) -> None:
        self.assertIn("window discovery is unavailable on this host", self.live_js)


class AbsentValuesAreLabelledTests(unittest.TestCase):
    def setUp(self) -> None:
        self.live_js = live_code()

    def test_an_absent_field_renders_as_not_reported(self) -> None:
        self.assertIn("not reported", self.live_js)

    def test_no_guessed_placeholder_string_is_rendered(self) -> None:
        lowered = self.live_js.lower()
        for banned in ('"unknown"', "'unknown'", '"n/a"', "'n/a'", '"generic"'):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, lowered)


class LiveViewIsReadOnlyTests(unittest.TestCase):
    """M2B discovers. It does not offer a control that does not exist yet."""

    def setUp(self) -> None:
        self.live_js = live_code()

    def test_the_live_view_issues_no_write_request(self) -> None:
        for verb in ('method: "POST"', 'method: "DELETE"', 'method: "PUT"', 'method: "PATCH"'):
            with self.subTest(verb=verb):
                self.assertNotIn(verb, self.live_js)
        self.assertNotIn("body:", self.live_js)

    def test_the_live_view_offers_no_control_action(self) -> None:
        """No "close", no "kill", no "move to display" — none of it is built."""
        for banned in ("kill", "terminate", "closeWindow", "moveToDisplay", "/api/actions"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, self.live_js)

    def test_label_editing_is_not_offered_before_it_exists(self) -> None:
        """M2B2 adds it. Offering a control that silently does nothing is worse
        than not offering one."""
        self.assertNotIn("editLabel", self.live_js)
        self.assertNotIn("saveLabel", self.live_js)
        self.assertIn("item.overlay", self.live_js)


class PollingIsConservativeTests(unittest.TestCase):
    """Each poll costs the workstation a walk of its process table."""

    def test_the_poll_interval_is_not_aggressive(self) -> None:
        live_js = live_code()
        match = re.search(r"var POLL_MS = (\d+);", live_js)
        self.assertIsNotNone(match, "the poll interval should be a named constant")
        self.assertGreaterEqual(
            int(match.group(1)),
            15000,
            "polling faster than every 15s makes the host scan /proc continuously",
        )

    def test_polling_pauses_while_the_page_is_hidden(self) -> None:
        self.assertIn("document.hidden", live_code())

    def test_signing_out_stops_the_polling(self) -> None:
        self.assertIn("CofferdamLive.stop()", code_only((WEB_DIR / "app.js").read_text(encoding="utf-8")))


class ProminenceTests(unittest.TestCase):
    """A control plane's front page is not a system monitor.

    Real-client validation found every fact on the page true and the page still
    wrong: three GNOME helpers sat beside Opera and Firefox, and ~116 processes
    rendered before anything a person controls. These scans pin the fix in the
    direction that matters — demote, never drop.
    """

    def setUp(self) -> None:
        self.live_js = live_code()
        self.app_js = code_only((WEB_DIR / "app.js").read_text(encoding="utf-8"))
        self.index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")

    def test_the_primary_application_list_is_the_user_facing_bucket(self) -> None:
        self.assertIn("user_facing", self.live_js)
        self.assertIn("presentation", self.live_js)

    def test_background_and_other_groups_have_their_own_sections(self) -> None:
        self.assertIn("Background services", self.live_js)
        self.assertIn("Other running groups", self.live_js)

    def test_demoted_groups_are_still_rendered_as_cards(self) -> None:
        """Moved out of the primary list, not dropped from the page.

        The section must render ``applicationCard`` for the non-primary buckets
        too; a client that only rendered the primary bucket would be hiding
        running software behind a count.
        """
        self.assertGreaterEqual(
            self.live_js.count("applicationCard"),
            2,
            "background/other buckets must render real cards, not a count",
        )

    def test_no_bucket_is_filtered_out_of_the_request(self) -> None:
        """Prominence is a client concern; the API call stays unfiltered."""
        self.assertNotIn("presentation=", self.live_js)
        self.assertIn('"/api/runtime"', self.live_js)

    def test_the_process_inspector_is_collapsed_by_default(self) -> None:
        """``sections.processes`` starts falsy, so the disclosure is closed and
        — the point of the exercise — the list is not built at all."""
        self.assertIn("Process inspector", self.live_js)
        self.assertIn("var open = !!sections.processes;", self.live_js)
        self.assertNotIn("sections.processes = true", self.live_js)

    def test_the_process_count_is_shown_while_collapsed(self) -> None:
        """Collapsing must not hide how much is there."""
        self.assertIn('<span class="count">\' + total', self.live_js)

    def test_the_process_inspector_offers_search_and_an_instance_filter(self) -> None:
        self.assertIn("processQuery", self.live_js)
        self.assertIn("processInstance", self.live_js)
        self.assertIn("application_instance_id", self.live_js)

    def test_process_rows_keep_pid_start_time_and_state(self) -> None:
        self.assertIn('"PID " + item.pid', self.live_js)
        self.assertIn("item.state", self.live_js)
        self.assertIn("item.started_at", self.live_js)

    def test_an_application_card_leads_with_a_short_reference_not_a_pid(self) -> None:
        """PID stays available, but it is not the card's identity."""
        self.assertIn("shortRef(item.resource_id)", self.live_js)
        self.assertIn('["Resource ID"', self.live_js)
        self.assertIn('"PID " + item.primary_pid', self.live_js)

    def test_display_technical_details_survive_behind_a_second_disclosure(self) -> None:
        """Moved behind expansion, not removed."""
        self.assertIn("technical(item.resource_id", self.live_js)
        for retained in ("Serial", "Hardware fingerprint", "Manufacturer",
                         "Physical size", "Discovered by", "Identity"):
            with self.subTest(retained=retained):
                self.assertIn(retained, self.live_js)

    def test_windows_stays_a_truthful_capability_row(self) -> None:
        self.assertIn("capability-row", self.live_js)
        self.assertIn("collection.reason", self.live_js)

    def test_an_unavailable_capability_is_not_offered_as_a_normal_action(self) -> None:
        """Hidden from the primary row, and named in a collapsed area with the
        host's own reason — not left sitting at the top greyed out."""
        self.assertIn("screenshotSlot", self.index_html)
        self.assertIn("capabilitiesUnavailable", self.index_html)
        self.assertIn("renderUnavailableCapabilities", self.app_js)
        self.assertIn("slot.hidden = capabilities.screenshot === false", self.app_js)

    def test_the_unavailable_area_is_collapsed_and_starts_hidden(self) -> None:
        self.assertIn('id="capabilitiesUnavailable" hidden', self.index_html)

    def test_installed_stays_separate_from_running(self) -> None:
        """The two vocabularies must not merge under the reshuffle.

        Configuration says "installed — can launch"; the live view says
        "running". That distinction is what the whole milestone exists for, and
        a prominence change is exactly when it would get blurred.
        """
        self.assertIn("Not the same as installed", self.live_js)
        self.assertNotIn("can launch", self.live_js)
        self.assertIn(
            "This is not a list of what is currently connected, running, or open.",
            self.index_html,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
