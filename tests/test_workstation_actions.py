"""M1 acceptance tests: the typed action surface.

Covers required checks 5-8 and 11 — including the central safety property that
**no caller can submit a command**, only a registered typed action.
"""

from __future__ import annotations

import unittest
from datetime import datetime

from tests._workstation_doubles import WorkstationTestCase


def _is_iso_utc(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except (TypeError, ValueError):
        return False
    return True


class UnknownActionTests(WorkstationTestCase):
    def test_unknown_actions_are_rejected(self) -> None:
        """(5) Only registered action names are dispatched."""
        for name in ("run_shell", "exec", "take_screenshot ", "TAKE_SCREENSHOT", "", "../take_screenshot"):
            with self.subTest(action=name):
                response = self.post_action(name)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "unknown_action")

    def test_unknown_top_level_fields_are_rejected(self) -> None:
        response = self.client.post(
            "/api/actions",
            json={"action": "take_screenshot", "params": {}, "command": "rm -rf /"},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 422)


class NoArbitraryCommandTests(WorkstationTestCase):
    def test_arbitrary_shell_commands_cannot_be_submitted(self) -> None:
        """(6) No action accepts a command, argv, executable, or shell string."""
        attempts = [
            ("take_screenshot", {"command": "rm -rf /"}),
            ("take_screenshot", {"cmd": "whoami"}),
            ("open_application", {"application": "firefox", "args": ["--proxy-server=evil"]}),
            ("open_application", {"application": "/bin/sh"}),
            ("open_application", {"application": "firefox; rm -rf /"}),
            ("open_application", {"application": "$(whoami)"}),
            ("open_application", {"command": "bash"}),
            ("open_url", {"url": "https://example.com", "browser_path": "/bin/sh"}),
            ("open_url", {"url": "https://example.com", "shell": True}),
        ]
        for action, params in attempts:
            with self.subTest(action=action, params=params):
                response = self.post_action(action, params)
                self.assertIn(response.status_code, (400, 422), f"{action} {params}")
        # Nothing was launched by any of the attempts.
        self.assertEqual(self.adapter.launched, [])
        self.assertEqual(self.adapter.opened_urls, [])

    def test_application_allowlist_is_closed(self) -> None:
        for application in ("bash", "sh", "xterm", "gnome-terminal", "explorer.exe", "firefox\n"):
            with self.subTest(application=application):
                response = self.post_action("open_application", {"application": application})
                self.assertEqual(response.status_code, 422)

    def test_allowlisted_application_launches(self) -> None:
        response = self.post_action("open_application", {"application": "firefox"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.adapter.launched, ["firefox"])


class UrlValidationTests(WorkstationTestCase):
    def test_open_url_validates_the_scheme(self) -> None:
        """(7) Only http/https URLs are accepted."""
        rejected = [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "ftp://example.com",
            "chrome://settings",
            "smb://server/share",
            "https://",            # no host
            "not a url",
            "",
            "  ",
            "http://example.com/\r\nX-Injected: 1",
            "https://example.com/" + "a" * 4000,
        ]
        for url in rejected:
            with self.subTest(url=url):
                response = self.post_action("open_url", {"url": url})
                self.assertEqual(response.status_code, 422, url)
        self.assertEqual(self.adapter.opened_urls, [])

    def test_open_url_accepts_http_and_https(self) -> None:
        for url in ("https://example.com", "http://192.168.1.10:8080/path?q=1"):
            with self.subTest(url=url):
                response = self.post_action("open_url", {"url": url})
                self.assertEqual(response.status_code, 200)
        self.assertEqual(self.adapter.opened_urls, ["https://example.com", "http://192.168.1.10:8080/path?q=1"])


class ActionRecordTests(WorkstationTestCase):
    def test_action_results_have_ids_timestamps_and_status(self) -> None:
        """(8) Every result is an identifiable, timestamped, terminal record."""
        record = self.post_action("take_screenshot").json()

        self.assertTrue(record["action_id"])
        self.assertEqual(len(record["action_id"]), 32)
        self.assertEqual(record["action"], "take_screenshot")
        self.assertEqual(record["status"], "succeeded")
        self.assertTrue(_is_iso_utc(record["started_at"]), record["started_at"])
        self.assertTrue(_is_iso_utc(record["finished_at"]), record["finished_at"])
        self.assertGreaterEqual(record["finished_at"], record["started_at"])
        self.assertIsNone(record["error"])
        self.assertTrue(record["stub"])

    def test_action_ids_are_unique(self) -> None:
        ids = {self.post_action("take_screenshot").json()["action_id"] for _ in range(5)}
        self.assertEqual(len(ids), 5)

    def test_recent_actions_are_recorded_and_bounded(self) -> None:
        for _ in range(3):
            self.post_action("open_application", {"application": "firefox"})
        listed = self.client.get("/api/actions", headers=self.auth).json()["actions"]
        self.assertEqual(len(listed), 3)
        self.assertLessEqual(len(listed), self.config.max_action_records)
        self.assertEqual(listed[0]["action"], "open_application")

    def test_records_survive_a_service_restart(self) -> None:
        self.post_action("take_screenshot")
        from fastapi.testclient import TestClient

        from cofferdam.workstation.service import create_app
        from tests._workstation_doubles import TEST_TOKEN

        restarted = create_app(config=self.config, token=TEST_TOKEN, adapter=self.adapter)
        with TestClient(restarted) as client:
            listed = client.get("/api/actions", headers=self.auth).json()["actions"]
        self.assertEqual(len(listed), 1)


class AdapterFailureTests(WorkstationTestCase):
    adapter_failure = "screenshot"

    def test_adapter_failures_become_bounded_structured_errors(self) -> None:
        """(11) A platform failure is a structured record, not a traceback."""
        response = self.post_action("take_screenshot")
        self.assertEqual(response.status_code, 502)

        record = response.json()
        self.assertEqual(record["status"], "failed")
        self.assertIsNone(record["result"])
        self.assertEqual(record["error"]["code"], "adapter_unsupported")
        self.assertTrue(record["error"]["message"])
        self.assertLessEqual(len(record["error"]["message"]), 300)
        detail = record["error"]["detail"]
        self.assertTrue(detail is None or len(detail) <= 300)
        self.assertNotIn("Traceback", response.text)
        self.assertNotIn("File \"", response.text)

    def test_failed_actions_are_still_recorded(self) -> None:
        action_id = self.post_action("take_screenshot").json()["action_id"]
        listed = self.client.get("/api/actions", headers=self.auth).json()["actions"]
        self.assertEqual(listed[0]["action_id"], action_id)
        self.assertEqual(listed[0]["status"], "failed")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
