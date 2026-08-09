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


class StaticAssetsAreRevalidated(WorkstationTestCase):
    """A changed frontend file must never be served from a phone's cache blind.

    **Found the hard way.** M2I PR4 fixed a follow-up draft defect, the fix was
    verified in a real browser, and it then failed on a real phone — which
    produced three provider turns from one intended message. The file was
    correct and the file was not on the phone: assets carried no
    ``Cache-Control`` at all, and a response that says nothing about its own
    freshness may be given a heuristic lifetime by the browser. iOS Safari does
    exactly that.

    ``no-cache`` is the fix and is not ``no-store``: the copy may be kept, it
    just may not be used without asking. With the ``ETag`` Starlette already
    sends, the ordinary case is a 304 with no body.
    """

    SHELL = (
        "/index.html",
        "/app.js",
        "/tasks.js",
        "/styles.css",
        "/sw.js",
        "/manifest.webmanifest",
    )

    def test_every_shell_asset_requires_revalidation(self) -> None:
        for path in self.SHELL:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("cache-control"), "no-cache")

    def test_the_directory_index_is_covered_too(self) -> None:
        """``/`` serves index.html through the same handler, so it must match."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"), "no-cache")

    def test_an_asset_still_carries_a_validator(self) -> None:
        """Revalidation is only cheap if there is something to revalidate against."""
        response = self.client.get("/tasks.js")
        self.assertTrue(
            response.headers.get("etag") or response.headers.get("last-modified"),
            "no ETag or Last-Modified, so every load would be a full download",
        )

    def test_a_conditional_request_is_answered_304_and_still_says_no_cache(self) -> None:
        """The 304 refreshes stored freshness, so it must carry the rule as well.

        A ``Not Modified`` that omitted the directive would hand back a copy the
        browser may then use without asking again — which is the original defect
        with an extra step.
        """
        first = self.client.get("/tasks.js")
        etag = first.headers.get("etag")
        if not etag:  # pragma: no cover - Starlette always sends one today
            self.skipTest("no ETag to revalidate against")
        second = self.client.get("/tasks.js", headers={"If-None-Match": etag})
        self.assertEqual(second.status_code, 304)
        self.assertEqual(second.headers.get("cache-control"), "no-cache")

    def test_no_asset_is_given_an_indefinite_lifetime(self) -> None:
        """The failure mode this class exists to prevent, stated as a rule.

        ``max-age`` without revalidation is how an asset becomes unreachable by a
        deploy. If one is ever introduced it has to be paired with ``no-cache``
        or ``must-revalidate``, and this fails until it is.
        """
        for path in self.SHELL:
            with self.subTest(path=path):
                directive = (self.client.get(path).headers.get("cache-control") or "").lower()
                if "max-age" in directive:
                    self.assertTrue(
                        "no-cache" in directive or "must-revalidate" in directive,
                        directive + " lets a stale asset be used without asking",
                    )

    def test_a_changed_file_is_served_as_changed(self) -> None:
        """End to end: edit an asset, and the next conditional request is a 200.

        Written against a real file in the served directory rather than a mock,
        because the property under test is the whole path — handler, validator
        and header together.
        """
        from cofferdam.workstation.service import WEB_ROOT

        target = WEB_ROOT / "sw.js"
        original = target.read_text(encoding="utf-8")
        self.addCleanup(target.write_text, original, encoding="utf-8")

        first = self.client.get("/sw.js")
        etag = first.headers.get("etag")
        target.write_text(original + "\n/* changed */\n", encoding="utf-8")

        again = self.client.get("/sw.js", headers={"If-None-Match": etag or ""})
        self.assertEqual(again.status_code, 200, "a changed asset was answered 304")
        self.assertIn("/* changed */", again.text)
        self.assertEqual(again.headers.get("cache-control"), "no-cache")

    def test_task_content_is_still_no_store_not_merely_no_cache(self) -> None:
        """The two rules are different and must not be collapsed into one.

        An asset may be stored and revalidated. A task's prompt, question or
        result may not be written to disk at all.
        """
        from cofferdam.workstation.service import (
            STATIC_ASSET_HEADERS,
            TASK_CONTENT_HEADERS,
        )

        self.assertEqual(STATIC_ASSET_HEADERS["Cache-Control"], "no-cache")
        self.assertEqual(TASK_CONTENT_HEADERS["Cache-Control"], "no-store")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
