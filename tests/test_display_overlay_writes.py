"""Naming a display: the first write path Cofferdam exposes to the network.

Two properties are under test, and they pull in opposite directions.

**The user must be able to name their monitors.** "Büyük monitör" is more useful
than "VA1650-FHD", and typing it on a phone should just work — including in
Turkish, including when the same word is typed with different capitalisation.

**A name must never become a lie about the hardware.** An overlay adds words to
a display; it may not replace, mutate, or invent one. So the guards here are
mostly about refusing: a connector is not a panel, two identical monitors are
not one monitor, and a request does not get to choose where it is stored.

The fixtures are hermetic. Displays are scripted, the registry lives in a
temporary directory, and nothing reads the developer's own monitors — a test
that passed only on the machine with a ViewSonic plugged in would be worthless.
``FakeInventory`` deliberately runs the **real** ``OverlayResolver`` over the
**real** registry file, so "did the write land" is answered by reading it back
the way the service does, not by trusting what the writer returned.
"""

from __future__ import annotations

import json
import threading
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

from cofferdam.workstation.registries.storage import registry_lock
from cofferdam.workstation.runtime.models import Evidence, RuntimeSnapshot, collected
from cofferdam.workstation.runtime.overlay_store import (
    REJECT_INVALID_DOCUMENT,
    REJECT_WRITE_FAILED,
    DisplayOverlayStore,
)
from cofferdam.workstation.runtime.overlay_writes import (
    OverlayWriteRejected,
    REJECT_AMBIGUOUS_ALIAS,
    REJECT_AMBIGUOUS_IDENTITY,
    REJECT_INVALID_ALIASES,
    REJECT_INVALID_LABEL,
    REJECT_NOT_LABELLED,
    REJECT_UNKNOWN_RESOURCE,
    REJECT_WEAK_IDENTITY,
    clean_aliases,
    clean_label,
)
from cofferdam.workstation.runtime.overlays import OverlayResolver

from ._workstation_doubles import TEST_TOKEN, WorkstationTestCase

# Two panels modelled on the validation host, with the identity fields that
# matter and none of the ones that do not.
EXTERNAL_EDID = "2d08ef984174ccb4862e2fd8fc95c2226791cf9c0db43a61f1c8625285b022cb"
INTERNAL_EDID = "7a9f87f4c542dd8925ba86572d0e140eebe03ed13158a52c62158fb2cd1febb0"


def display(
    resource_id: str,
    connector: str,
    *,
    edid: str = None,
    manufacturer: str = None,
    model: str = None,
    serial: str = None,
    stability: str = "hardware",
):
    """One discovered display, shaped exactly as the real backend emits it."""
    return {
        "resource_id": resource_id,
        "kind": "display",
        "identity": {
            "source": "edid" if edid else "connector",
            "stability": stability,
            "edid_sha256": edid,
        },
        "connector": connector,
        "manufacturer": manufacturer,
        "model": model,
        "serial": serial,
        "display_name": model or connector,
        "internal": connector.startswith("eDP"),
        "connected": True,
        "active": True,
        "primary": connector.startswith("eDP"),
        "logical_size": {"width": 1920, "height": 1080, "scale": 1.0},
        "refresh_rate_hz": 60.0,
        "backend": "mutter-displayconfig",
        "overlay": None,
    }


EXTERNAL = display(
    "display-ext0001",
    "HDMI-1",
    edid=EXTERNAL_EDID,
    manufacturer="VSC",
    model="VA1650-FHD",
    serial="Y39252000375",
)
INTERNAL = display(
    "display-int0001",
    "eDP-1",
    edid=INTERNAL_EDID,
    manufacturer="AUO",
    model="0x53ab",
    serial="0x00000000",
)


class FakeInventory:
    """Scripted displays, resolved through the production overlay resolver.

    The resolver is not stubbed on purpose. Half of what this milestone claims
    is that a written overlay *comes back* attached to the right panel, and a
    fake that simply echoed the label would assert nothing about that.
    """

    def __init__(self, items, registry_loader):
        self.items = [dict(item) for item in items]
        self._registry_loader = registry_loader
        self._resolver = OverlayResolver()
        self.refresh_calls = []

    def _overlays(self):
        try:
            load = self._registry_loader().load("displays")
            return [item for item in load.registry.items if item.enabled] if load.ok else ()
        except Exception:
            return ()

    def collection(self, kind, refresh=False):
        self.refresh_calls.append((kind, refresh))
        items = [dict(item) for item in self.items]
        if kind == "displays":
            self._resolver.resolve_displays(items, self._overlays())
        snapshot = RuntimeSnapshot(
            observed_at="2026-08-05T10:00:00.000Z",
            host={"hostname": "t", "host_id": "host-t", "source": "machine-id"},
            boot={"boot_id": "boot-t", "source": "proc-boot-id", "booted_at": None},
            session={"available": True, "session_id": "s", "session_type": "wayland",
                     "reason": None},
            collections={},
        )
        return snapshot, collected(kind, items, Evidence(backend="mutter-displayconfig"))

    def snapshot(self, refresh=False):  # pragma: no cover - unused by writes
        return self.collection("displays", refresh)[0]


class OverlayTestCase(WorkstationTestCase):
    """A workstation app whose displays are scripted and whose home is a tmpdir."""

    displays = (EXTERNAL, INTERNAL)

    def setUp(self) -> None:
        super().setUp()
        from cofferdam.workstation.registries import load_registries
        from cofferdam.workstation.service import create_app
        from fastapi.testclient import TestClient

        # A display overlay must reference a device; seed exactly one so the
        # cross-reference check the loader performs can succeed.
        self.write_registry(
            "devices",
            [{"id": "test-workstation", "name": "Test workstation", "aliases": [],
              "enabled": True, "kind": "workstation", "platform": "linux", "notes": None}],
        )
        self.write_registry("displays", [])

        loader = lambda: load_registries(self.config)  # noqa: E731
        self.inventory = FakeInventory(self.displays, loader)

        self.client.__exit__(None, None, None)
        self.app = create_app(
            config=self.config, token=TEST_TOKEN, adapter=self.adapter,
            inventory=self.inventory,
        )
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.store = self.app.state.display_overlays

    # -- helpers -------------------------------------------------------------

    @property
    def displays_path(self) -> Path:
        return Path(self.config.registry_path("displays"))

    def write_registry(self, name, items):
        path = Path(self.config.registry_path(name))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": 1, "items": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def stored_items(self):
        return json.loads(self.displays_path.read_text(encoding="utf-8"))["items"]

    def put(self, resource_id, payload):
        return self.client.put(
            f"/api/runtime/displays/{resource_id}/overlay", headers=self.auth, json=payload
        )

    def delete(self, resource_id):
        return self.client.delete(
            f"/api/runtime/displays/{resource_id}/overlay", headers=self.auth
        )

    def card(self, resource_id):
        """The display as the read path now reports it."""
        payload = self.client.get("/api/runtime/displays", headers=self.auth).json()
        for item in payload["collection"]["items"]:
            if item["resource_id"] == resource_id:
                return item
        raise AssertionError("display not found: " + resource_id)


# ---------------------------------------------------------------------------
# (1)-(4) the ordinary flow
# ---------------------------------------------------------------------------


class LabelLifecycleTests(OverlayTestCase):
    def test_a_strongly_identified_display_can_be_named(self) -> None:
        response = self.put("display-ext0001", {"label": "Büyük monitör"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["overlay"]["label"], "Büyük monitör")

        stored = self.stored_items()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["name"], "Büyük monitör")
        self.assertEqual(stored[0]["match"]["edid_sha256"], EXTERNAL_EDID)

    def test_the_label_is_editable(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör"})
        self.put("display-ext0001", {"label": "Masa monitörü"})

        stored = self.stored_items()
        self.assertEqual(len(stored), 1, "editing must replace, not append")
        self.assertEqual(stored[0]["name"], "Masa monitörü")

    def test_the_label_can_be_removed_and_the_hardware_name_returns(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör"})
        self.assertIsNotNone(self.card("display-ext0001")["overlay"])

        response = self.delete("display-ext0001")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stored_items(), [])

        card = self.card("display-ext0001")
        self.assertIsNone(card["overlay"])
        self.assertEqual(card["model"], "VA1650-FHD", "the hardware title must come back")

    def test_aliases_can_be_added_edited_and_removed(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör", "aliases": ["harici ekran"]})
        self.assertEqual(self.stored_items()[0]["aliases"], ["harici ekran"])

        self.put(
            "display-ext0001",
            {"label": "Büyük monitör", "aliases": ["harici ekran", "masa monitörü"]},
        )
        self.assertEqual(
            self.stored_items()[0]["aliases"], ["harici ekran", "masa monitörü"]
        )

        self.put("display-ext0001", {"label": "Büyük monitör", "aliases": []})
        self.assertEqual(self.stored_items()[0]["aliases"], [])

    def test_deleting_an_unlabelled_display_reports_not_found(self) -> None:
        """Chosen behaviour: DELETE is **not** idempotent.

        The repository's convention is that a refusal names its reason rather
        than returning a cheerful no-op — the registry routes, the action
        executor and the overlay resolver all work that way. A silent 200 for a
        display that was never named would also mask the far more likely real
        cause: the user is looking at a different display than they think.
        """
        response = self.delete("display-ext0001")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], REJECT_NOT_LABELLED)


# ---------------------------------------------------------------------------
# (5)-(8) text handling
# ---------------------------------------------------------------------------


class TurkishAndUnicodeTests(OverlayTestCase):
    def test_turkish_spelling_is_preserved_exactly(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör", "aliases": ["ışık masası"]})
        stored = self.stored_items()[0]
        self.assertEqual(stored["name"], "Büyük monitör")
        self.assertEqual(stored["aliases"], ["ışık masası"])

    def test_whitespace_is_trimmed_and_collapsed(self) -> None:
        self.put("display-ext0001", {"label": "  Büyük    monitör  "})
        self.assertEqual(self.stored_items()[0]["name"], "Büyük monitör")

    def test_a_label_that_is_only_whitespace_is_refused(self) -> None:
        response = self.put("display-ext0001", {"label": "   "})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], REJECT_INVALID_LABEL)

    def test_control_characters_are_refused(self) -> None:
        for payload in ({"label": "Bü\x00yük"}, {"label": "ok", "aliases": ["a‮b"]}):
            with self.subTest(payload=payload):
                response = self.put("display-ext0001", payload)
                self.assertEqual(response.status_code, 422)

    def test_case_differences_do_not_create_two_aliases(self) -> None:
        """Turkish-aware, and the user's own spelling survives."""
        self.put(
            "display-ext0001",
            {"label": "Ekran", "aliases": ["Büyük monitör", "büyük monitör", "BÜYÜK MONİTÖR"]},
        )
        aliases = self.stored_items()[0]["aliases"]
        self.assertEqual(aliases, ["Büyük monitör"], "the first spelling is kept")

    def test_dotted_and_dotless_i_fold_together(self) -> None:
        """A naive lower() keeps IŞIK and ışık apart; the registry rule does not."""
        self.put("display-ext0001", {"label": "Ekran", "aliases": ["IŞIK", "ışık"]})
        self.assertEqual(len(self.stored_items()[0]["aliases"]), 1)

    def test_composed_and_decomposed_spellings_are_one_alias(self) -> None:
        """The same word typed two ways: an o-with-diaeresis as a single code
        point, and as a plain o followed by U+0308 COMBINING DIAERESIS.

        An iPhone keyboard and a Linux compose key genuinely produce different
        bytes here, and both are the word the user meant. The two forms are
        built with :mod:`unicodedata` rather than written as literals: a
        decomposed character is invisible in a diff, and an earlier draft of
        this very test silently corrupted its own fixture that way.
        """
        composed = unicodedata.normalize("NFC", "monit\u00f6r")
        decomposed = unicodedata.normalize("NFD", "monit\u00f6r")
        self.assertNotEqual(composed, decomposed, "the fixture must be two spellings")

        self.put("display-ext0001", {"label": "Ekran", "aliases": [composed, decomposed]})
        self.assertEqual(
            self.stored_items()[0]["aliases"], [composed], "stored composed, stored once"
        )

    def test_an_alias_used_by_another_display_is_refused(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör"})
        response = self.put(
            "display-int0001", {"label": "Laptop ekranı", "aliases": ["büyük monitör"]}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], REJECT_AMBIGUOUS_ALIAS)
        # And the refusal changed nothing.
        self.assertEqual(len(self.stored_items()), 1)

    def test_a_label_colliding_with_another_displays_label_is_refused(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör"})
        response = self.put("display-int0001", {"label": "BÜYÜK MONİTÖR"})
        self.assertEqual(response.status_code, 409)

    def test_re_saving_the_same_display_does_not_collide_with_itself(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör", "aliases": ["harici"]})
        response = self.put(
            "display-ext0001", {"label": "Büyük monitör", "aliases": ["harici", "yeni"]}
        )
        self.assertEqual(response.status_code, 200)


class ValidationBoundsTests(unittest.TestCase):
    """Unit-level bounds, without a client."""

    def test_a_non_string_label_is_refused(self) -> None:
        with self.assertRaises(OverlayWriteRejected) as caught:
            clean_label(42)
        self.assertEqual(caught.exception.code, REJECT_INVALID_LABEL)

    def test_an_over_long_label_is_refused(self) -> None:
        with self.assertRaises(OverlayWriteRejected):
            clean_label("x" * 500)

    def test_too_many_aliases_are_refused(self) -> None:
        with self.assertRaises(OverlayWriteRejected) as caught:
            clean_aliases([f"alias {index}" for index in range(200)])
        self.assertEqual(caught.exception.code, REJECT_INVALID_ALIASES)

    def test_aliases_must_be_a_list_of_text(self) -> None:
        for bad in ("not-a-list", [1, 2], [None]):
            with self.subTest(bad=bad):
                with self.assertRaises(OverlayWriteRejected):
                    clean_aliases(bad)

    def test_no_aliases_is_valid(self) -> None:
        self.assertEqual(clean_aliases(None), ())


# ---------------------------------------------------------------------------
# (9)-(12) identity
# ---------------------------------------------------------------------------


class WeakIdentityTests(OverlayTestCase):
    """A connector is a socket. A label stored against one would move house."""

    displays = (display("display-weak001", "HDMI-2", stability="weak"),)

    def test_a_connector_only_display_cannot_be_named(self) -> None:
        response = self.put("display-weak001", {"label": "Büyük monitör"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], REJECT_WEAK_IDENTITY)

    def test_the_refusal_explains_itself_and_writes_nothing(self) -> None:
        response = self.put("display-weak001", {"label": "Büyük monitör"})
        self.assertIn("connector", response.json()["error"]["detail"].lower())
        self.assertEqual(self.stored_items(), [])


class HardwareTripleTests(OverlayTestCase):
    """No EDID digest, but a complete manufacturer/model/serial is panel-grade."""

    displays = (
        display("display-triple01", "DP-1", manufacturer="ACME", model="X1", serial="SN-1"),
    )

    def test_a_complete_triple_may_be_named(self) -> None:
        response = self.put("display-triple01", {"label": "Yan ekran"})
        self.assertEqual(response.status_code, 200)
        stored = self.stored_items()[0]
        self.assertIsNone(stored["match"]["edid_sha256"])
        self.assertEqual(stored["match"]["serial"], "SN-1")


class ConnectorRenumberingTests(OverlayTestCase):
    """(10) The whole reason the key is the panel and not the port."""

    def test_an_overlay_survives_the_display_moving_to_another_connector(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör"})

        # Same panel, same EDID, different socket and a different resource id —
        # exactly what a cable moved to another port produces.
        moved = display(
            "display-ext0002",
            "HDMI-3",
            edid=EXTERNAL_EDID,
            manufacturer="VSC",
            model="VA1650-FHD",
            serial="Y39252000375",
        )
        self.inventory.items = [moved]

        card = self.card("display-ext0002")
        self.assertIsNotNone(card["overlay"], "the label must follow the panel")
        self.assertEqual(card["overlay"]["label"], "Büyük monitör")
        self.assertEqual(card["connector"], "HDMI-3", "the connector is still the live one")

    def test_a_different_panel_in_the_same_connector_is_not_labelled(self) -> None:
        """The mirror image, and the failure that matters more."""
        self.put("display-ext0001", {"label": "Büyük monitör"})

        stranger = display(
            "display-other01",
            "HDMI-1",
            edid="ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
            manufacturer="OTH",
            model="Someone else's",
            serial="SN-9",
        )
        self.inventory.items = [stranger]

        self.assertIsNone(
            self.card("display-other01")["overlay"],
            "a label must not transfer to whatever is plugged into that socket next",
        )


class SameModelTests(OverlayTestCase):
    """(11) Two of the same monitor, distinguishable because EDID differs."""

    displays = (
        display("display-twin0a", "DP-1", edid="a" * 64, manufacturer="ACME",
                model="Twin", serial="SN-A"),
        display("display-twin0b", "DP-2", edid="b" * 64, manufacturer="ACME",
                model="Twin", serial="SN-B"),
    )

    def test_naming_one_does_not_name_the_other(self) -> None:
        self.put("display-twin0a", {"label": "Sol ekran"})
        self.assertEqual(self.card("display-twin0a")["overlay"]["label"], "Sol ekran")
        self.assertIsNone(self.card("display-twin0b")["overlay"])

    def test_both_can_be_named_separately(self) -> None:
        self.put("display-twin0a", {"label": "Sol ekran"})
        self.put("display-twin0b", {"label": "Sağ ekran"})
        self.assertEqual(len(self.stored_items()), 2)
        self.assertEqual(self.card("display-twin0b")["overlay"]["label"], "Sağ ekran")


class AmbiguousIdentityTests(OverlayTestCase):
    """(12) Byte-identical EDID: firmware that publishes no serial."""

    displays = (
        display("display-clone0a", "DP-1", edid="c" * 64, manufacturer="ACME", model="Clone"),
        display("display-clone0b", "DP-2", edid="c" * 64, manufacturer="ACME", model="Clone"),
    )

    def test_neither_clone_can_be_named(self) -> None:
        response = self.put("display-clone0a", {"label": "Sol ekran"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], REJECT_AMBIGUOUS_IDENTITY)
        self.assertEqual(self.stored_items(), [], "failing closed means writing nothing")

    def test_a_hand_written_overlay_for_a_clone_is_applied_to_neither(self) -> None:
        """The read side has to fail closed too, not just the write side."""
        self.write_registry(
            "displays",
            [{"id": "display-clone0a", "device_id": "test-workstation", "name": "Sol ekran",
              "aliases": [], "enabled": True,
              "match": {"connector_hint": None, "manufacturer": None, "model": None,
                        "serial": None, "edid_sha256": "c" * 64}}],
        )
        for resource_id in ("display-clone0a", "display-clone0b"):
            with self.subTest(resource_id=resource_id):
                self.assertIsNone(self.card(resource_id)["overlay"])


# ---------------------------------------------------------------------------
# (13), (24), (25) the overlay never becomes the hardware
# ---------------------------------------------------------------------------


class OverlayNeverReplacesIdentityTests(OverlayTestCase):
    IDENTITY_FIELDS = (
        "resource_id", "connector", "model", "manufacturer", "serial",
        "logical_size", "refresh_rate_hz", "primary", "active", "internal",
    )

    def test_naming_changes_no_identity_field(self) -> None:
        before = self.card("display-ext0001")
        self.put("display-ext0001", {"label": "Büyük monitör", "aliases": ["harici ekran"]})
        after = self.card("display-ext0001")

        for field in self.IDENTITY_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(before[field], after[field])
        self.assertEqual(before["identity"], after["identity"])

    def test_the_resource_id_is_unchanged_by_naming(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör"})
        self.assertEqual(self.card("display-ext0001")["resource_id"], "display-ext0001")

    def test_the_overlay_is_a_separate_field_not_a_substitution(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör"})
        card = self.card("display-ext0001")
        self.assertEqual(card["overlay"]["label"], "Büyük monitör")
        self.assertEqual(card["model"], "VA1650-FHD")
        self.assertEqual(card["display_name"], "VA1650-FHD")

    def test_the_stored_entry_holds_no_runtime_state(self) -> None:
        """Copying live state into the registry would create a second, staler
        source of truth for something discovery already owns."""
        self.put("display-ext0001", {"label": "Büyük monitör"})
        stored = self.stored_items()[0]
        for forbidden in (
            "resolution", "logical_size", "refresh_rate_hz", "position", "scale",
            "primary", "active", "connected", "internal", "resource_id",
        ):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, stored)
        self.assertNotIn("resource_id", stored["match"])


# ---------------------------------------------------------------------------
# (14)-(18) the write path as a security boundary
# ---------------------------------------------------------------------------


class RequestCannotChooseStorageTests(OverlayTestCase):
    def test_a_client_supplied_persistent_key_is_refused(self) -> None:
        for payload in (
            {"label": "x", "edid_sha256": "d" * 64},
            {"label": "x", "id": "display-somewhere-else"},
            {"label": "x", "registry": "devices"},
            {"label": "x", "match": {"edid_sha256": "d" * 64}},
            {"label": "x", "device_id": "other"},
        ):
            with self.subTest(payload=sorted(payload)):
                response = self.put("display-ext0001", payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.stored_items(), [])

    def test_a_path_like_resource_id_reaches_no_file(self) -> None:
        for resource_id in ("../../devices", "..%2f..%2fdevices", "display-nope"):
            with self.subTest(resource_id=resource_id):
                response = self.client.put(
                    f"/api/runtime/displays/{resource_id}/overlay",
                    headers=self.auth,
                    json={"label": "x"},
                )
                self.assertIn(response.status_code, (404, 405, 422))
                self.assertEqual(self.stored_items(), [])

    def test_only_the_displays_registry_is_ever_written(self) -> None:
        before = {
            name: Path(self.config.registry_path(name)).read_bytes()
            for name in ("devices",)
        }
        self.put("display-ext0001", {"label": "Büyük monitör"})
        for name, content in before.items():
            self.assertEqual(Path(self.config.registry_path(name)).read_bytes(), content)


class WriteAuthenticationTests(OverlayTestCase):
    def test_an_unauthenticated_write_is_refused(self) -> None:
        for call in (
            lambda: self.client.put(
                "/api/runtime/displays/display-ext0001/overlay", json={"label": "x"}
            ),
            lambda: self.client.delete("/api/runtime/displays/display-ext0001/overlay"),
        ):
            with self.subTest(call=call):
                response = call()
                self.assertEqual(response.status_code, 401)
                self.assertEqual(self.stored_items(), [])

    def test_a_wrong_token_is_refused(self) -> None:
        response = self.client.put(
            "/api/runtime/displays/display-ext0001/overlay",
            headers={"Authorization": "Bearer wrong"},
            json={"label": "x"},
        )
        self.assertEqual(response.status_code, 401)

    def test_get_never_mutates(self) -> None:
        before = self.displays_path.read_bytes()
        for path in ("/api/runtime", "/api/runtime/displays", "/api/registries/displays"):
            self.client.get(path, headers=self.auth)
        self.assertEqual(self.displays_path.read_bytes(), before)

    def test_the_overlay_route_has_no_get(self) -> None:
        response = self.client.get(
            "/api/runtime/displays/display-ext0001/overlay", headers=self.auth
        )
        # No GET is registered for this path, so the router answers 404. Either
        # that or 405 is fine; what matters is that reading it is not a thing.
        self.assertIn(response.status_code, (404, 405))


class RequestShapeTests(OverlayTestCase):
    def test_an_unsupported_content_type_is_refused(self) -> None:
        response = self.client.put(
            "/api/runtime/displays/display-ext0001/overlay",
            headers={**self.auth, "Content-Type": "text/plain"},
            content=b'{"label": "x"}',
        )
        self.assertEqual(response.status_code, 415)
        self.assertEqual(self.stored_items(), [])

    def test_an_oversized_body_is_refused(self) -> None:
        response = self.client.put(
            "/api/runtime/displays/display-ext0001/overlay",
            headers={**self.auth, "Content-Type": "application/json"},
            content=b'{"label": "' + b"x" * 20000 + b'"}',
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.stored_items(), [])

    def test_malformed_json_is_refused(self) -> None:
        response = self.client.put(
            "/api/runtime/displays/display-ext0001/overlay",
            headers={**self.auth, "Content-Type": "application/json"},
            content=b"{not json",
        )
        self.assertEqual(response.status_code, 400)

    def test_a_json_array_body_is_refused(self) -> None:
        response = self.client.put("display-ext0001".join(
            ("/api/runtime/displays/", "/overlay")), headers=self.auth, json=["label"])
        self.assertEqual(response.status_code, 400)

    def test_an_unknown_resource_is_refused(self) -> None:
        response = self.put("display-does-not-exist", {"label": "x"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], REJECT_UNKNOWN_RESOURCE)


# ---------------------------------------------------------------------------
# (19)-(21) durability
# ---------------------------------------------------------------------------


class DurabilityTests(OverlayTestCase):
    def test_an_invalid_write_leaves_the_previous_file_byte_identical(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör"})
        before = self.displays_path.read_bytes()

        for payload in ({"label": ""}, {"label": "x" * 999}, {"label": "ok", "aliases": [1]}):
            with self.subTest(payload=payload):
                self.assertNotEqual(self.put("display-ext0001", payload).status_code, 200)
                self.assertEqual(self.displays_path.read_bytes(), before)

    def test_a_failing_atomic_write_reports_an_error_and_changes_nothing(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör"})
        before = self.displays_path.read_bytes()

        with mock.patch(
            "cofferdam.workstation.runtime.overlay_store.write_json_atomic",
            side_effect=OSError("disk full"),
        ):
            response = self.put("display-ext0001", {"label": "Yeni ad"})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], REJECT_WRITE_FAILED)
        self.assertEqual(self.displays_path.read_bytes(), before, "no partial write")

    def test_a_write_failure_does_not_report_success(self) -> None:
        with mock.patch(
            "cofferdam.workstation.runtime.overlay_store.write_json_atomic",
            side_effect=OSError("disk full"),
        ):
            response = self.put("display-ext0001", {"label": "Büyük monitör"})
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(self.stored_items(), [])

    def test_a_document_the_loader_would_reject_is_never_written(self) -> None:
        """The pre-write validation, exercised by removing the seeded device.

        Without a device to reference, the entry the writer builds would fail
        the loader's cross-reference check at next start. Refusing now turns a
        broken-registry-at-boot into a 4xx.
        """
        self.write_registry("devices", [])
        response = self.put("display-ext0001", {"label": "Büyük monitör"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.stored_items(), [])

    def test_concurrent_writes_leave_a_valid_document(self) -> None:
        """Ten threads, one registry, no torn file and no lost entry."""
        errors = []

        def worker(index):
            try:
                self.store.save(
                    "display-ext0001" if index % 2 else "display-int0001",
                    f"Ekran {index}",
                    [],
                )
            except OverlayWriteRejected:
                pass  # alias collisions between racing labels are legitimate
            except Exception as error:  # pragma: no cover - the failure under test
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        document = json.loads(self.displays_path.read_text(encoding="utf-8"))
        self.assertEqual(document["version"], 1)
        ids = [item["id"] for item in document["items"]]
        self.assertEqual(len(ids), len(set(ids)), "no duplicated entries")
        self.assertLessEqual(len(ids), 2)

    def test_the_lock_serializes_writers(self) -> None:
        """Mutation check on the lock itself: it must actually exclude."""
        path = self.displays_path
        order = []

        def second():
            with registry_lock(path, timeout=5):
                order.append("second")

        with registry_lock(path, timeout=5):
            thread = threading.Thread(target=second)
            thread.start()
            thread.join(timeout=0.3)
            order.append("first")
        thread.join(timeout=5)

        self.assertEqual(order, ["first", "second"])

    def test_the_response_reflects_the_durable_state(self) -> None:
        """The response is a re-read, not an echo of the request."""
        response = self.put("display-ext0001", {"label": "Büyük monitör"})
        self.assertEqual(
            response.json()["overlay"]["label"], self.stored_items()[0]["name"]
        )


# ---------------------------------------------------------------------------
# (22)-(23) nothing is invented
# ---------------------------------------------------------------------------


class NothingIsSeededTests(OverlayTestCase):
    def test_a_fresh_install_has_no_labels(self) -> None:
        self.assertEqual(self.stored_items(), [])
        for resource_id in ("display-ext0001", "display-int0001"):
            with self.subTest(resource_id=resource_id):
                self.assertIsNone(self.card(resource_id)["overlay"])

    def test_reading_the_inventory_creates_no_registry_entries(self) -> None:
        for _ in range(3):
            self.client.get("/api/runtime", headers=self.auth)
        self.assertEqual(self.stored_items(), [])

    def test_no_example_label_reaches_the_live_registry(self) -> None:
        text = self.displays_path.read_text(encoding="utf-8")
        for banned in ("example", "Büyük", "örnek", "Laptop"):
            self.assertNotIn(banned, text)


class StaleOverlayTests(OverlayTestCase):
    """(23) A stored name for an absent monitor is not a monitor."""

    def test_an_overlay_for_a_disconnected_display_creates_no_display(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör"})
        self.inventory.items = [dict(INTERNAL)]  # external unplugged

        payload = self.client.get("/api/runtime/displays", headers=self.auth).json()
        items = payload["collection"]["items"]
        self.assertEqual([item["resource_id"] for item in items], ["display-int0001"])
        self.assertEqual(payload["collection"]["count"], 1)

    def test_the_stored_overlay_survives_the_disconnection(self) -> None:
        """Stale is not deleted — it is simply not shown as connected."""
        self.put("display-ext0001", {"label": "Büyük monitör"})
        self.inventory.items = [dict(INTERNAL)]
        self.assertEqual(len(self.stored_items()), 1)

    def test_it_resolves_again_when_the_display_returns(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör"})
        self.inventory.items = [dict(INTERNAL)]
        self.client.get("/api/runtime/displays", headers=self.auth)

        self.inventory.items = [dict(EXTERNAL), dict(INTERNAL)]
        self.assertEqual(
            self.card("display-ext0001")["overlay"]["label"], "Büyük monitör"
        )

    def test_a_label_survives_a_service_restart(self) -> None:
        """A new app over the same home must see the same names."""
        from cofferdam.workstation.registries import load_registries
        from cofferdam.workstation.service import create_app
        from fastapi.testclient import TestClient

        self.put("display-ext0001", {"label": "Büyük monitör"})

        loader = lambda: load_registries(self.config)  # noqa: E731
        restarted = create_app(
            config=self.config, token=TEST_TOKEN, adapter=self.adapter,
            inventory=FakeInventory(self.displays, loader),
        )
        with TestClient(restarted) as client:
            payload = client.get("/api/runtime/displays", headers=self.auth).json()
        found = [i for i in payload["collection"]["items"] if i["resource_id"] == "display-ext0001"]
        self.assertEqual(found[0]["overlay"]["label"], "Büyük monitör")


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


class AuditTests(OverlayTestCase):
    def _actions(self):
        return self.client.get("/api/actions", headers=self.auth).json()["actions"]

    def test_a_successful_write_is_recorded(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör"})
        record = self._actions()[0]
        self.assertEqual(record["action"], "overlay_updated")
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["params"]["resource_id"], "display-ext0001")

    def test_a_removal_is_recorded(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör"})
        self.delete("display-ext0001")
        self.assertEqual(self._actions()[0]["action"], "overlay_removed")

    def test_a_rejected_write_is_recorded_as_failed_with_its_code(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör"})
        self.put("display-int0001", {"label": "BÜYÜK MONİTÖR"})
        record = self._actions()[0]
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"]["code"], REJECT_AMBIGUOUS_ALIAS)

    def test_the_audit_trail_holds_no_label_text(self) -> None:
        """The user's words about their own home stay out of a general log."""
        self.put("display-ext0001", {"label": "Büyük monitör", "aliases": ["harici ekran"]})
        serialized = json.dumps(self._actions(), ensure_ascii=False)
        self.assertNotIn("Büyük monitör", serialized)
        self.assertNotIn("harici ekran", serialized)

    def test_the_audit_trail_holds_no_edid_or_token(self) -> None:
        self.put("display-ext0001", {"label": "Büyük monitör"})
        serialized = json.dumps(self._actions(), ensure_ascii=False)
        self.assertNotIn(EXTERNAL_EDID, serialized)
        self.assertNotIn(TEST_TOKEN, serialized)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
