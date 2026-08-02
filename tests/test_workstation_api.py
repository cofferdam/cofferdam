"""M1 acceptance tests: health, authentication, status schema, screenshots.

Covers required checks 1-4 and 10.
"""

from __future__ import annotations

import unittest

from tests._workstation_doubles import TEST_TOKEN, WorkstationTestCase


class HealthTests(WorkstationTestCase):
    def test_healthz_is_public_and_reports_ok(self) -> None:
        """(1) /healthz works — and needs no token, so systemd can probe it."""
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_healthz_leaks_no_host_detail(self) -> None:
        payload = response = self.client.get("/healthz").json()
        self.assertEqual(set(payload), {"status", "api_version"})
        self.assertNotIn("hostname", str(response))


class AuthenticationTests(WorkstationTestCase):
    def test_authentication_is_required(self) -> None:
        """(2) Every state-revealing or state-changing route requires a token."""
        protected = [
            ("get", "/api/status"),
            ("get", "/api/actions"),
            ("post", "/api/actions"),
            ("post", "/api/actions/screenshot"),
            ("post", "/api/actions/open-application"),
            ("post", "/api/actions/open-url"),
            ("get", "/api/screenshots/abc123"),
        ]
        for method, path in protected:
            with self.subTest(path=path):
                if method == "post":
                    response = self.client.post(path, json={})
                else:
                    response = self.client.get(path)
                self.assertEqual(response.status_code, 401, path)
                self.assertEqual(response.json()["error"]["code"], "unauthorized")

    def test_invalid_tokens_are_rejected(self) -> None:
        """(3) Wrong, empty, malformed, and near-miss tokens all fail closed."""
        candidates = [
            {"Authorization": "Bearer wrong-token"},
            {"Authorization": "Bearer "},
            {"Authorization": TEST_TOKEN},  # missing the Bearer scheme
            {"Authorization": "Basic " + TEST_TOKEN},
            {"Authorization": "Bearer " + TEST_TOKEN + "x"},
            {"Authorization": "Bearer " + TEST_TOKEN[:-1]},
        ]
        for headers in candidates:
            with self.subTest(headers=headers):
                response = self.client.get("/api/status", headers=headers)
                self.assertEqual(response.status_code, 401)

    def test_valid_token_is_accepted(self) -> None:
        self.assertEqual(self.client.get("/api/status", headers=self.auth).status_code, 200)

    def test_error_responses_never_echo_the_token(self) -> None:
        response = self.client.get("/api/status", headers={"Authorization": "Bearer wrong-token"})
        self.assertNotIn("wrong-token", response.text)
        self.assertNotIn(TEST_TOKEN, response.text)

    def test_no_endpoint_ever_returns_the_token(self) -> None:
        """The device token is never readable back through the API."""
        self.client.post("/api/actions/screenshot", headers=self.auth)
        responses = [
            self.client.get("/healthz"),
            self.client.get("/api/status", headers=self.auth),
            self.client.get("/api/actions", headers=self.auth),
            self.client.post("/api/actions", json={"action": "nope"}, headers=self.auth),
            self.client.get("/", headers=self.auth),
        ]
        for response in responses:
            with self.subTest(url=str(response.url)):
                self.assertNotIn(TEST_TOKEN, response.text)


class StatusSchemaTests(WorkstationTestCase):
    def test_status_matches_its_schema(self) -> None:
        """(4) The status response has the documented shape and types."""
        payload = self.client.get("/api/status", headers=self.auth).json()

        self.assertEqual(set(payload), {"service", "host", "applications"})

        service = payload["service"]
        self.assertEqual(set(service), {"api_version", "milestone", "actions", "event_clients"})
        self.assertIsInstance(service["actions"], list)
        self.assertIn("take_screenshot", service["actions"])

        host = payload["host"]
        expected_keys = {
            "hostname", "platform", "session_type", "adapter", "stub",
            "uptime_seconds", "cpu_percent", "memory_total_bytes", "memory_used_bytes",
            "disk_total_bytes", "disk_used_bytes", "display_count", "capabilities", "notes",
        }
        self.assertEqual(set(host), expected_keys)
        self.assertIsInstance(host["hostname"], str)
        self.assertIsInstance(host["stub"], bool)
        self.assertIsInstance(host["capabilities"], dict)
        self.assertIsInstance(host["notes"], list)
        self.assertIsInstance(payload["applications"], list)

    def test_stub_adapter_is_always_identified(self) -> None:
        """A stub run can never masquerade as a validated Ubuntu run."""
        payload = self.client.get("/api/status", headers=self.auth).json()
        self.assertTrue(payload["host"]["stub"])
        self.assertEqual(payload["host"]["adapter"], "stub")


class ScreenshotTests(WorkstationTestCase):
    def test_screenshot_is_served_only_to_authenticated_users(self) -> None:
        """(10) The PNG artifact is behind the same token as everything else."""
        record = self.client.post("/api/actions/screenshot", headers=self.auth).json()
        url = record["result"]["screenshot_url"]

        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers={"Authorization": "Bearer nope"}).status_code, 401)

        authorized = self.client.get(url, headers=self.auth)
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(authorized.headers["content-type"], "image/png")
        self.assertTrue(authorized.content.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_screenshot_path_cannot_traverse(self) -> None:
        for candidate in ("../../secrets/token", "..%2F..%2Fsecrets%2Ftoken", "not-hex!", "a" * 200):
            with self.subTest(candidate=candidate):
                response = self.client.get(f"/api/screenshots/{candidate}", headers=self.auth)
                self.assertIn(response.status_code, (400, 404))

    def test_missing_screenshot_is_a_structured_404(self) -> None:
        response = self.client.get("/api/screenshots/" + "0" * 32, headers=self.auth)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
