"""The ``/api/runtime`` surface: authenticated, read-only, and honest in JSON.

Two things are being protected here.

**Authentication.** A runtime inventory is a list of what is plugged into
somebody's desk and what they have open. It is more sensitive than the registry
routes, not less, so every route requires the device token and there is no
"summary" variant that leaks a count to an anonymous caller.

**Read-only.** M2B observes. Process and window *control* is a later milestone
with its own identity re-verification rules, so nothing under ``/api/runtime``
accepts a write method — asserted by asking the router what it actually
registered rather than by trying a few verbs and hoping.
"""

from __future__ import annotations

import unittest

from cofferdam.workstation.runtime.models import (
    RESOURCE_KINDS,
    RUNTIME_SNAPSHOT_VERSION,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    Evidence,
    ResourceCollection,
    RuntimeSnapshot,
    collected,
    unavailable,
)

from ._workstation_doubles import TEST_TOKEN, WorkstationTestCase


class FakeInventory:
    """A scripted inventory, so the API is tested and not this machine."""

    def __init__(self) -> None:
        self.calls = []
        self.snapshot_value = RuntimeSnapshot(
            observed_at="2026-08-05T10:00:00.000Z",
            host={"hostname": "testhost", "host_id": "host-test", "source": "machine-id"},
            boot={"boot_id": "boot-test", "source": "proc-boot-id", "booted_at": None},
            session={
                "available": True,
                "session_id": "gsession-test",
                "session_type": "wayland",
                "reason": None,
            },
            collections={
                "displays": collected(
                    "displays",
                    [
                        {
                            "resource_id": "display-1",
                            "connector": "eDP-1",
                            "model": "0x53ab",
                            "internal": True,
                            "overlay": None,
                        }
                    ],
                    Evidence(backend="mutter-displayconfig"),
                ),
                "applications": collected("applications", [], Evidence(backend="systemd-cgroup")),
                "processes": collected(
                    "processes",
                    [{"resource_id": "process-1", "pid": 4242, "name": "worker"}],
                    Evidence(backend="proc-filesystem"),
                ),
                "windows": unavailable(
                    "windows", "no window backend on this host", Evidence(backend="none-available")
                ),
            },
        )

    def snapshot(self, refresh=False):
        self.calls.append(("snapshot", refresh))
        return self.snapshot_value

    def collection(self, kind, refresh=False):
        self.calls.append(("collection", kind, refresh))
        return self.snapshot_value, self.snapshot_value.collection(kind)


class RuntimeApiTestCase(WorkstationTestCase):
    """Rebuild the app with a scripted inventory injected."""

    def setUp(self) -> None:
        super().setUp()
        from fastapi.testclient import TestClient

        from cofferdam.workstation.service import create_app

        self.client.__exit__(None, None, None)
        self.inventory = FakeInventory()
        self.app = create_app(
            config=self.config,
            token=TEST_TOKEN,
            adapter=self.adapter,
            inventory=self.inventory,
        )
        self.client = TestClient(self.app)
        self.client.__enter__()


class AuthenticationTests(RuntimeApiTestCase):
    """(17) API endpoints require authentication."""

    def test_the_snapshot_route_rejects_an_unauthenticated_request(self) -> None:
        response = self.client.get("/api/runtime")
        self.assertEqual(response.status_code, 401)

    def test_every_collection_route_rejects_an_unauthenticated_request(self) -> None:
        for kind in RESOURCE_KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(self.client.get(f"/api/runtime/{kind}").status_code, 401)

    def test_a_wrong_token_is_rejected(self) -> None:
        response = self.client.get(
            "/api/runtime", headers={"Authorization": "Bearer not-the-token"}
        )
        self.assertEqual(response.status_code, 401)

    def test_an_unauthenticated_rejection_leaks_no_host_detail(self) -> None:
        """A 401 must not become a side channel for the inventory."""
        body = self.client.get("/api/runtime").text
        for leak in ("eDP-1", "display-1", "4242", "worker", "testhost"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, body)

    def test_no_inventory_scan_happens_for_an_unauthenticated_request(self) -> None:
        self.client.get("/api/runtime")
        self.assertEqual(self.inventory.calls, [], "auth must gate the scan, not just the payload")

    def test_the_authenticated_request_succeeds(self) -> None:
        """Mutation check: the assertions above would pass on a route that 401s always."""
        response = self.client.get("/api/runtime", headers=self.auth)
        self.assertEqual(response.status_code, 200)


class SnapshotShapeTests(RuntimeApiTestCase):
    """The versioned contract both clients read."""

    def test_the_snapshot_carries_version_time_and_all_three_identities(self) -> None:
        payload = self.client.get("/api/runtime", headers=self.auth).json()

        self.assertEqual(payload["version"], RUNTIME_SNAPSHOT_VERSION)
        self.assertTrue(payload["observed_at"].endswith("Z"))
        for key in ("host", "boot", "session"):
            with self.subTest(key=key):
                self.assertIn(key, payload)

    def test_every_resource_kind_is_present_with_its_own_status(self) -> None:
        collections = self.client.get("/api/runtime", headers=self.auth).json()["collections"]

        self.assertEqual(set(collections), set(RESOURCE_KINDS))
        self.assertEqual(collections["displays"]["status"], STATUS_OK)
        self.assertEqual(collections["windows"]["status"], STATUS_UNAVAILABLE)

    def test_an_unavailable_collection_is_distinguishable_from_an_empty_one(self) -> None:
        """The property the whole milestone turns on, asserted over the wire."""
        collections = self.client.get("/api/runtime", headers=self.auth).json()["collections"]

        empty_ok = collections["applications"]
        cannot_see = collections["windows"]

        self.assertEqual(empty_ok["count"], cannot_see["count"])
        self.assertEqual(empty_ok["items"], cannot_see["items"])
        self.assertNotEqual(empty_ok["status"], cannot_see["status"])
        self.assertIsNone(empty_ok["reason"])
        self.assertTrue(cannot_see["reason"])

    def test_a_collection_route_returns_the_slice_with_its_header(self) -> None:
        payload = self.client.get("/api/runtime/displays", headers=self.auth).json()

        self.assertEqual(payload["collection"]["kind"], "displays")
        self.assertEqual(payload["collection"]["count"], 1)
        self.assertEqual(payload["observed_at"], "2026-08-05T10:00:00.000Z")
        self.assertEqual(payload["session"]["session_id"], "gsession-test")

    def test_an_unknown_resource_kind_is_a_404_that_does_not_echo_the_request(self) -> None:
        response = self.client.get("/api/runtime/../secrets", headers=self.auth)
        self.assertIn(response.status_code, (404, 400))
        self.assertNotIn("secrets", response.text)

    def test_an_unknown_kind_lists_the_known_ones(self) -> None:
        response = self.client.get("/api/runtime/tabs", headers=self.auth)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("tabs", response.text)
        self.assertIn("displays", response.json()["error"]["detail"])

    def test_refresh_is_passed_through_and_defaults_to_off(self) -> None:
        self.client.get("/api/runtime", headers=self.auth)
        self.client.get("/api/runtime?refresh=true", headers=self.auth)
        self.assertEqual(
            self.inventory.calls, [("snapshot", False), ("snapshot", True)]
        )


class ReadOnlyTests(RuntimeApiTestCase):
    """M2B observes. Control is a later milestone with its own rules.

    M2B2 opens exactly one exception: a user may name a display they can see.
    That is metadata *about* a resource, not control *of* one — it starts,
    stops, moves and reconfigures nothing. The guard below is therefore an
    allowlist rather than a blanket ban, so the next write route has to be
    added here deliberately instead of arriving unnoticed.
    """

    # The complete set of runtime routes permitted to change anything.
    WRITE_ALLOWLIST = {
        ("/api/runtime/displays/{resource_id}/overlay", "PUT"),
        ("/api/runtime/displays/{resource_id}/overlay", "DELETE"),
    }

    def test_only_the_display_overlay_route_accepts_a_write_method(self) -> None:
        offenders = []
        for route in self.app.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/runtime"):
                continue
            methods = set(getattr(route, "methods", set()))
            for method in methods & {"POST", "PUT", "PATCH", "DELETE"}:
                if (path, method) not in self.WRITE_ALLOWLIST:
                    offenders.append(f"{path} {method}")
        self.assertEqual(offenders, [], f"unexpected runtime write route: {offenders}")

    def test_no_runtime_route_accepts_post_or_patch(self) -> None:
        """Overlay writes are PUT/DELETE. Nothing under /api/runtime posts."""
        for route in self.app.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/runtime"):
                continue
            methods = set(getattr(route, "methods", set()))
            self.assertFalse(methods & {"POST", "PATCH"}, f"{path} accepts POST/PATCH")

    def test_process_and_window_control_is_still_absent(self) -> None:
        """The milestone boundary, stated as a test.

        Naming a display is not a licence to terminate a process or move a
        window; those need identity re-verification rules this build does not
        have.
        """
        paths = {getattr(route, "path", "") for route in self.app.routes}
        for forbidden in (
            "/api/runtime/processes/{resource_id}",
            "/api/runtime/applications/{resource_id}/overlay",
            "/api/runtime/windows/{resource_id}",
        ):
            self.assertNotIn(forbidden, paths)

    def test_posting_to_the_snapshot_route_is_refused(self) -> None:
        response = self.client.post("/api/runtime", headers=self.auth, json={})
        self.assertEqual(response.status_code, 405)


class StubAdapterApiTests(WorkstationTestCase):
    """With the real inventory and the stub adapter, nothing is claimed."""

    def test_a_stub_host_reports_unavailable_rather_than_this_machine(self) -> None:
        payload = self.client.get("/api/runtime", headers=self.auth).json()
        for kind, collection in payload["collections"].items():
            with self.subTest(kind=kind):
                self.assertEqual(collection["status"], STATUS_UNAVAILABLE)
                self.assertEqual(collection["items"], [])
                self.assertIn("stub adapter", collection["reason"])


class CollectionVocabularyTests(unittest.TestCase):
    """Mutation checks on the model itself, independent of the API."""

    def test_an_error_collection_may_not_carry_items(self) -> None:
        with self.assertRaises(ValueError):
            ResourceCollection(kind="displays", status="error", items=({"a": 1},), reason="broke")

    def test_an_unknown_status_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            ResourceCollection(kind="displays", status="probably-fine")

    def test_warnings_downgrade_a_collection_to_partial(self) -> None:
        self.assertEqual(collected("processes", [{"a": 1}], None, ()).status, STATUS_OK)
        self.assertEqual(collected("processes", [{"a": 1}], None, ("missed one",)).status, "partial")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
