"""M2A: the read-only registry endpoints and browser-profile-aware ``open_url``.

Exercised through the real application, so authentication, error envelopes, and
the action-record path are covered as they actually behave over HTTP.
"""

from __future__ import annotations

import json
import unittest

from tests._workstation_doubles import TEST_TOKEN, WorkstationTestCase

REGISTRY_NAMES = (
    "devices",
    "displays",
    "applications",
    "browser_profiles",
    "agent_profiles",
    "conversation_routes",
)


class RegistryApiTestCase(WorkstationTestCase):
    def write(self, name: str, items) -> None:
        self.config.registry_path(name).write_text(
            json.dumps({"version": 1, "items": items}, ensure_ascii=False), encoding="utf-8"
        )

    def write_raw(self, name: str, text: str) -> None:
        self.config.registry_path(name).write_text(text, encoding="utf-8")

    def seed(self, profiles=None) -> None:
        self.write(
            "devices",
            [
                {
                    "id": "ubuntu-workstation",
                    "name": "Ubuntu workstation",
                    "aliases": ["bilgisayar"],
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
                {"id": "firefox", "name": "Firefox", "aliases": [], "enabled": True, "adapter_key": "firefox"},
            ],
        )
        self.write("browser_profiles", profiles if profiles is not None else [self.profile()])
        self.write(
            "agent_profiles",
            [
                {
                    "id": "claude-code-cofferdam",
                    "name": "Claude Code · Cofferdam",
                    "aliases": ["cofferdam claude"],
                    "enabled": True,
                    "adapter_kind": "claude-code",
                    "execution_status": "not-implemented",
                }
            ],
        )
        self.write(
            "conversation_routes",
            [
                {
                    "id": "chatgpt-to-cofferdam-claude",
                    "name": "ChatGPT → Cofferdam Claude",
                    "aliases": ["bu chati yolla"],
                    "enabled": True,
                    "source_kind": "opera-extension",
                    "target_agent_profile_id": "claude-code-cofferdam",
                    "return_mode": "prepare-then-confirm",
                }
            ],
        )

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


class RegistryAuthenticationTests(RegistryApiTestCase):
    def test_registry_routes_require_the_device_token(self) -> None:
        paths = ["/api/registries"] + [f"/api/registries/{name}" for name in REGISTRY_NAMES]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["error"]["code"], "unauthorized")

    def test_a_wrong_token_is_rejected(self) -> None:
        response = self.client.get(
            "/api/registries", headers={"Authorization": "Bearer not-the-token"}
        )
        self.assertEqual(response.status_code, 401)

    def test_registry_responses_never_contain_the_token(self) -> None:
        self.seed()
        for path in ["/api/registries", "/api/registries/devices"]:
            with self.subTest(path=path):
                self.assertNotIn(TEST_TOKEN, self.client.get(path, headers=self.auth).text)


class RegistrySummaryTests(RegistryApiTestCase):
    def test_an_unconfigured_machine_reports_six_valid_empty_registries(self) -> None:
        payload = self.client.get("/api/registries", headers=self.auth).json()
        self.assertEqual(payload["supported_version"], 1)
        self.assertEqual([r["name"] for r in payload["registries"]], list(REGISTRY_NAMES))
        for entry in payload["registries"]:
            with self.subTest(registry=entry["name"]):
                self.assertEqual(entry["status"], "ok")
                self.assertEqual(entry["item_count"], 0)
                self.assertEqual(entry["version"], 1)
                self.assertIsNone(entry["error"])

    def test_counts_reflect_the_files(self) -> None:
        self.seed()
        payload = self.client.get("/api/registries", headers=self.auth).json()
        counts = {entry["name"]: entry["item_count"] for entry in payload["registries"]}
        self.assertEqual(counts["applications"], 2)
        self.assertEqual(counts["browser_profiles"], 1)

    def test_the_summary_exposes_no_filesystem_path(self) -> None:
        self.seed()
        body = self.client.get("/api/registries", headers=self.auth).text
        self.assertNotIn(str(self.home), body)
        self.assertNotIn(".json", body)

    def test_an_invalid_registry_is_reported_without_hiding_the_others(self) -> None:
        self.seed()
        self.write_raw("agent_profiles", "{broken")
        payload = self.client.get("/api/registries", headers=self.auth).json()
        statuses = {entry["name"]: entry["status"] for entry in payload["registries"]}
        self.assertEqual(statuses["agent_profiles"], "error")
        self.assertEqual(statuses["devices"], "ok")
        broken = [e for e in payload["registries"] if e["name"] == "agent_profiles"][0]
        self.assertEqual(broken["error"]["reason"], "invalid_json")
        self.assertIsNone(broken["item_count"], "a failed load must expose no item data")


class RegistryDetailTests(RegistryApiTestCase):
    def test_every_registry_name_is_served(self) -> None:
        self.seed()
        for name in REGISTRY_NAMES:
            with self.subTest(registry=name):
                payload = self.client.get(f"/api/registries/{name}", headers=self.auth).json()
                self.assertEqual(payload["name"], name)
                self.assertEqual(payload["version"], 1)
                self.assertIsInstance(payload["items"], list)

    def test_items_are_returned_validated_and_complete(self) -> None:
        self.seed()
        payload = self.client.get("/api/registries/displays", headers=self.auth).json()
        item = payload["items"][0]
        self.assertEqual(item["id"], "large-monitor")
        self.assertEqual(item["name"], "Büyük monitör")
        self.assertEqual(item["aliases"], ["büyük ekran"])
        self.assertEqual(item["device_id"], "ubuntu-workstation")
        self.assertIn("match", item)

    def test_an_unknown_registry_name_is_a_structured_404(self) -> None:
        response = self.client.get("/api/registries/passwords", headers=self.auth)
        self.assertEqual(response.status_code, 404)
        error = response.json()["error"]
        self.assertEqual(error["code"], "not_found")
        # The requested name is arbitrary request text and is not echoed back.
        self.assertNotIn("passwords", json.dumps(error))

    def test_a_traversal_attempt_is_a_404_not_a_file_read(self) -> None:
        for candidate in ("..%2F..%2Fsecrets%2Ftoken", "devices.json", "DEVICES", " devices"):
            with self.subTest(candidate=candidate):
                response = self.client.get(f"/api/registries/{candidate}", headers=self.auth)
                self.assertIn(response.status_code, (404, 400))

    def test_invalid_registry_data_returns_a_bounded_configuration_error(self) -> None:
        self.write_raw("devices", '{"version": 1, "items": [{"id": "x", "secret": "hunter2"}]}')
        response = self.client.get("/api/registries/devices", headers=self.auth)
        self.assertEqual(response.status_code, 500)
        error = response.json()["error"]
        self.assertEqual(error["code"], "configuration_invalid")
        body = json.dumps(error)
        # No file content, no path, no raw exception text.
        self.assertNotIn("hunter2", body)
        self.assertNotIn(str(self.home), body)
        self.assertNotIn("Traceback", body)
        self.assertLessEqual(len(error["detail"] or ""), 300)

    def test_unknown_version_is_reported_as_a_configuration_error(self) -> None:
        self.config.registry_path("devices").write_text(
            json.dumps({"version": 99, "items": []}), encoding="utf-8"
        )
        response = self.client.get("/api/registries/devices", headers=self.auth)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "configuration_invalid")


class NoRegistryWriteApiTests(RegistryApiTestCase):
    def test_no_registry_write_method_exists(self) -> None:
        """M2A ships no way to change configuration over the network."""
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for path in ("/api/registries", "/api/registries/devices"):
                with self.subTest(method=method, path=path):
                    response = self.client.request(method, path, headers=self.auth)
                    self.assertIn(response.status_code, (404, 405), f"{method} {path}")

    def test_no_registry_route_is_registered_for_a_write_method(self) -> None:
        for route in self.app.routes:
            methods = getattr(route, "methods", set()) or set()
            path = getattr(route, "path", "")
            if path.startswith("/api/registries"):
                with self.subTest(path=path):
                    self.assertEqual(methods - {"GET", "HEAD"}, set())

    def test_a_registry_file_is_never_created_by_reading(self) -> None:
        self.client.get("/api/registries", headers=self.auth)
        self.client.get("/api/registries/devices", headers=self.auth)
        self.assertEqual(sorted(p.name for p in self.config.registries_dir.iterdir()), [])


class PlaceholderHonestyTests(RegistryApiTestCase):
    def test_agent_profiles_are_served_as_not_implemented(self) -> None:
        self.seed()
        payload = self.client.get("/api/registries/agent_profiles", headers=self.auth).json()
        for item in payload["items"]:
            with self.subTest(agent=item["id"]):
                self.assertEqual(item["execution_status"], "not-implemented")

    def test_conversation_routes_carry_no_live_conversation_state(self) -> None:
        self.seed()
        payload = self.client.get("/api/registries/conversation_routes", headers=self.auth).json()
        allowed = {
            "id",
            "name",
            "aliases",
            "enabled",
            "source_kind",
            "target_agent_profile_id",
            "return_mode",
        }
        for item in payload["items"]:
            with self.subTest(route=item["id"]):
                self.assertEqual(set(item), allowed)

    def test_no_agent_or_route_action_exists(self) -> None:
        payload = self.client.get("/api/status", headers=self.auth).json()
        self.assertEqual(
            set(payload["service"]["actions"]),
            {
                "take_screenshot",
                "open_application",
                "open_url",
                # M2B3A. Listed explicitly rather than loosened into a subset
                # check: the value of this assertion is that the action set is
                # exactly what was built, so a new capability has to be added
                # here deliberately and cannot arrive unnoticed.
                "open_media_provider",
                "search_media_provider",
                # M2B3A.1
                "find_media_results",
                "open_media_result",
            },
        )


# ---------------------------------------------------------------------------
# open_url through the API
# ---------------------------------------------------------------------------


class OpenUrlCompatibilityTests(RegistryApiTestCase):
    def test_a_url_only_request_still_works_with_no_registries(self) -> None:
        """(backward-compatible URL-only open_url)

        The *request* is still the pre-M2A one — a bare ``url``, no profile —
        and it still succeeds with no registries present. What changed in
        M2B3A is only which browser answers when nothing is configured: Opera,
        by product decision, instead of whichever browser the adapter's table
        happened to list first.
        """
        response = self.post_action("open_url", {"url": "https://example.com"})
        self.assertEqual(response.status_code, 200)
        record = response.json()
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["result"]["url"], "https://example.com")
        self.assertIsNone(record["result"]["browser_profile_id"])
        self.assertEqual(record["result"]["selection"], "product-default")
        self.assertEqual(self.adapter.opened_with, ["opera"])

    def test_without_opera_a_url_only_request_takes_the_legacy_path(self) -> None:
        """A host that cannot honour the preference behaves as it did before."""
        self.adapter.missing_applications = ("opera",)
        response = self.post_action("open_url", {"url": "https://example.com"})
        self.assertEqual(response.status_code, 200)
        record = response.json()
        self.assertEqual(record["result"]["selection"], "legacy")
        self.assertEqual(self.adapter.opened_with, [None])

    def test_the_convenience_route_is_unchanged_for_url_only(self) -> None:
        response = self.client.post(
            "/api/actions/open-url", json={"url": "https://example.com"}, headers=self.auth
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.adapter.opened_urls, ["https://example.com"])

    def test_scheme_validation_is_not_relaxed(self) -> None:
        for url in ("file:///etc/passwd", "javascript:alert(1)", "ftp://example.com", "data:text/html,x"):
            with self.subTest(url=url):
                response = self.post_action("open_url", {"url": url})
                self.assertEqual(response.status_code, 422)


class OpenUrlProfileTests(RegistryApiTestCase):
    def test_an_explicit_profile_selects_its_application(self) -> None:
        self.seed()
        response = self.post_action(
            "open_url", {"url": "https://example.com", "browser_profile_id": "personal-opera"}
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(result["browser_profile_id"], "personal-opera")
        self.assertEqual(result["browser_profile_name"], "Kişisel Opera")
        self.assertEqual(result["selection"], "explicit-profile")
        self.assertEqual(result["application"], "opera")
        # Metadata only: recorded, but nothing moved a window.
        self.assertEqual(result["preferred_display_id"], "large-monitor")
        self.assertEqual(self.adapter.opened_with, ["opera"])

    def test_the_default_profile_is_used_when_none_is_given(self) -> None:
        self.seed()
        response = self.post_action("open_url", {"url": "https://example.com"})
        result = response.json()["result"]
        self.assertEqual(result["selection"], "default-profile")
        self.assertEqual(result["browser_profile_id"], "personal-opera")

    def test_an_unknown_profile_fails_closed_and_launches_nothing(self) -> None:
        self.seed()
        response = self.post_action(
            "open_url", {"url": "https://example.com", "browser_profile_id": "no-such-profile"}
        )
        self.assertEqual(response.status_code, 502)
        record = response.json()
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"]["code"], "browser_profile_invalid")
        self.assertEqual(self.adapter.opened_urls, [])

    def test_a_disabled_profile_fails_closed(self) -> None:
        self.seed(profiles=[self.profile(enabled=False, default_for_url=False)])
        response = self.post_action(
            "open_url", {"url": "https://example.com", "browser_profile_id": "personal-opera"}
        )
        self.assertEqual(response.json()["error"]["code"], "browser_profile_invalid")
        self.assertEqual(self.adapter.opened_urls, [])

    def test_a_malformed_profile_id_is_rejected_before_execution(self) -> None:
        for candidate in ("../devices", "Personal Opera", "opera;rm -rf /", "/bin/sh", "a" * 100):
            with self.subTest(candidate=candidate):
                response = self.post_action(
                    "open_url", {"url": "https://example.com", "browser_profile_id": candidate}
                )
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.adapter.opened_urls, [])

    def test_a_disallowed_domain_is_refused_visibly(self) -> None:
        self.seed(
            profiles=[
                self.profile(domain_policy={"mode": "allow-list", "domains": ["example.com"]})
            ]
        )
        response = self.post_action(
            "open_url", {"url": "https://badexample.com", "browser_profile_id": "personal-opera"}
        )
        self.assertEqual(response.status_code, 502)
        error = response.json()["error"]
        self.assertEqual(error["code"], "domain_not_allowed")
        self.assertIn("Kişisel Opera", error["message"])
        self.assertEqual(self.adapter.opened_urls, [])

    def test_an_allowed_subdomain_passes(self) -> None:
        self.seed(
            profiles=[
                self.profile(domain_policy={"mode": "allow-list", "domains": ["example.com"]})
            ]
        )
        response = self.post_action(
            "open_url", {"url": "https://docs.example.com/x", "browser_profile_id": "personal-opera"}
        )
        self.assertEqual(response.status_code, 200)

    def test_invalid_registry_data_refuses_the_action_rather_than_guessing(self) -> None:
        self.seed()
        self.write_raw("browser_profiles", "{broken")
        response = self.post_action("open_url", {"url": "https://example.com"})
        self.assertEqual(response.status_code, 502)
        error = response.json()["error"]
        self.assertEqual(error["code"], "configuration_invalid")
        self.assertNotIn(str(self.home), json.dumps(error))
        self.assertEqual(self.adapter.opened_urls, [])

    def test_no_command_like_field_can_ride_along_with_a_profile(self) -> None:
        self.seed()
        attempts = [
            {"url": "https://example.com", "browser_profile_id": "personal-opera", "argv": ["x"]},
            {"url": "https://example.com", "executable": "/bin/sh"},
            {"url": "https://example.com", "user_data_dir": "/home/x/.config/opera"},
            {"url": "https://example.com", "application": "opera"},
        ]
        for params in attempts:
            with self.subTest(params=sorted(params)):
                self.assertEqual(self.post_action("open_url", params).status_code, 422)
        self.assertEqual(self.adapter.opened_urls, [])


class OpenUrlUnavailableBrowserTests(RegistryApiTestCase):
    """The stub is told Opera is missing, so availability is exercised for real."""

    def setUp(self) -> None:
        super().setUp()
        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.service import create_app
        from fastapi.testclient import TestClient

        self.client.__exit__(None, None, None)
        self.adapter = StubAdapter(self.config, missing_applications=("opera",))
        self.app = create_app(config=self.config, token=TEST_TOKEN, adapter=self.adapter)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def test_an_explicit_unavailable_profile_reports_it_and_does_not_substitute(self) -> None:
        self.seed(
            profiles=[
                self.profile(default_for_url=False),
                {
                    "id": "fallback-firefox",
                    "name": "Yedek Firefox",
                    "aliases": ["yedek"],
                    "enabled": True,
                    "application_id": "firefox",
                    "default_for_url": True,
                    "preferred_display_id": None,
                    "launch_mode": "default-instance",
                    "domain_policy": {"mode": "allow-all", "domains": []},
                },
            ]
        )
        response = self.post_action(
            "open_url", {"url": "https://example.com", "browser_profile_id": "personal-opera"}
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "application_unavailable")
        self.assertEqual(self.adapter.opened_urls, [])

    def test_an_unavailable_default_falls_back_to_the_legacy_launch(self) -> None:
        self.seed()
        response = self.post_action("open_url", {"url": "https://example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["selection"], "legacy")
        self.assertEqual(self.adapter.opened_with, [None])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
