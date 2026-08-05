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

    def test_the_live_view_issues_no_unexpected_write_verb(self) -> None:
        """PUT and DELETE belong to the overlay routes. POST and PATCH are
        nobody's — M2B2 added naming, not control."""
        for verb in ('method: "POST"', 'method: "PATCH"'):
            with self.subTest(verb=verb):
                self.assertNotIn(verb, self.live_js)

    def test_every_write_targets_the_display_overlay_route(self) -> None:
        self.assertIn('"/api/runtime/displays/" + encodeURIComponent', self.live_js)
        self.assertIn('"/overlay"', self.live_js)
        for forbidden in ("/api/runtime/processes/", "/api/runtime/applications/"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.live_js)

    def test_the_live_view_offers_no_control_action(self) -> None:
        """No "close", no "kill", no "move to display" — none of it is built."""
        for banned in ("kill", "terminate", "closeWindow", "moveToDisplay", "/api/actions"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, self.live_js)

    def test_application_instance_labels_are_still_not_offered(self) -> None:
        """M2B2 is displays only.

        An application instance's identity is boot-scoped — PID plus start time
        — so a label attached to one could not survive the restart that makes a
        label worth having. That needs a Cofferdam-owned session model, which is
        later work.
        """
        card = self.live_js[self.live_js.index("function applicationCard") :]
        card = card[: card.index("function processRow")]
        self.assertNotIn("data-name", card)
        self.assertNotIn("nameEditor", card)

    def test_the_display_naming_control_exists(self) -> None:
        for control in ("Name display", "Edit name", "Remove custom name"):
            with self.subTest(control=control):
                self.assertIn(control, self.live_js)
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


class DisplayNamingFlowTests(unittest.TestCase):
    """The M2B2 editor, scanned for the properties that make it trustworthy.

    The write itself is covered end-to-end in
    ``tests/test_display_overlay_writes.py``; what matters here is the client's
    behaviour around it — that it does not claim success early, cannot be
    double-submitted, and keeps what the user typed when the server refuses.
    """

    def setUp(self) -> None:
        self.live_js = live_code()

    def test_the_card_is_not_updated_before_the_server_confirms(self) -> None:
        """The refresh happens in the success branch, after a durable write.

        An optimistic update here would be the display equivalent of the false
        success this project keeps refusing: the card would show a name the
        service never stored.
        """
        self.assertIn("if (!response.ok)", self.live_js)
        self.assertIn("return load(true);", self.live_js)

    def test_double_submission_is_prevented(self) -> None:
        self.assertIn("if (saving) { return; }", self.live_js)
        self.assertIn('(saving ? " disabled" : "")', self.live_js)

    def test_a_failed_save_keeps_the_form_open_with_the_error(self) -> None:
        self.assertIn("saveError = refusal(response)", self.live_js)
        self.assertIn("name-error", self.live_js)

    def test_the_server_reason_is_shown_not_a_generic_message(self) -> None:
        """`detail` carries the only part of a fail-closed refusal the user can
        act on — why a connector is not a panel, and so on."""
        self.assertIn("error.detail", self.live_js)

    def test_a_weak_identity_display_is_explained_rather_than_offered(self) -> None:
        self.assertIn("cannot be named", self.live_js)
        self.assertIn("edid_sha256", self.live_js)

    def test_polling_does_not_redraw_an_open_editor(self) -> None:
        self.assertIn("editing === null", self.live_js)

    def test_the_draft_is_held_in_javascript_not_read_off_the_dom(self) -> None:
        """A 30s poll replaces the markup; reading the input back would lose it."""
        self.assertIn("draftLabel = event.target.value", self.live_js)
        self.assertIn("draftAliases = event.target.value", self.live_js)

    def test_signing_out_clears_the_draft(self) -> None:
        stop = self.live_js[self.live_js.index("function stop()") :]
        self.assertIn("draftLabel = \"\"", stop[:400])

    def test_the_label_becomes_the_title_and_hardware_moves_to_the_subtitle(self) -> None:
        """The card must add the user's name, never replace the panel's."""
        self.assertIn("var heading = label || hardware;", self.live_js)
        self.assertIn("subtitleParts = label ? [hardware, item.connector]", self.live_js)

    def test_aliases_appear_only_in_the_expanded_details(self) -> None:
        """The collapsed card shows a name and the hardware, nothing more.

        Asserted against the two strings the collapsed card is actually built
        from, rather than against the whole function — the alias list is
        computed near the top and that is fine; what matters is that it does not
        reach the title or the subtitle.
        """
        card = self.live_js[self.live_js.index("function displayCard") :]
        card = card[: card.index("function nameEditor")]

        heading = re.search(r"var heading = .*?;", card, re.S).group(0)
        subtitle = re.search(r"var subtitle = .*?;", card, re.S).group(0)
        self.assertNotIn("alias", heading)
        self.assertNotIn("alias", subtitle)
        self.assertIn("Also known as", card, "but they must be reachable when expanded")

    def test_the_client_never_sends_a_persistent_key(self) -> None:
        """Only label and aliases go up; the key is the server's to derive."""
        body = self.live_js[self.live_js.index("body: { label: draftLabel") :][:120]
        for forbidden in ("edid", "match", "device_id", "registry", "id:"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
