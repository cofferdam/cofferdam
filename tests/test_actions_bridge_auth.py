"""The external credential boundary, and the surface a caller can reach.

Two questions, and nothing else in this file.

*Who gets in?* Only a caller presenting the external key as a Bearer header.
Not a query string, not a cookie, not the Cofferdam device token, not a
close-but-wrong value.

*What exists to be reached?* Nine paths. Everything else is 404, including every
path that would be interesting to somebody probing — the daemon's own routes,
the PWA, `/.env`, a traversal. Those are not refusals the bridge computes; they
are paths that were never declared.
"""

from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the extras are absent
    TestClient = None

if TestClient is not None:
    from cofferdam.actions_bridge.config import (
        BridgeConfigError,
        generate_external_key,
        load_bridge_config,
        read_secret_file,
    )
    from cofferdam.actions_bridge.service import OPERATION_IDS, create_bridge_app

    from ._actions_bridge_doubles import TASK_ID, FakeInternalClient

KEY = "bridge-test-key-not-a-real-credential-0001"
OTHER_KEY = "bridge-test-key-not-a-real-credential-0002"


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class BridgeAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.config = load_bridge_config(self.home)
        self.upstream = FakeInternalClient()
        self.app = create_bridge_app(
            self.config, external_key=KEY, internal_client=self.upstream
        )
        self.client = TestClient(self.app)

    def auth(self, key: str = KEY) -> dict:
        return {"Authorization": "Bearer " + key}

    # -- who gets in ---------------------------------------------------------

    def test_a_missing_credential_is_401(self) -> None:
        response = self.client.get("/v1/projects")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "unauthorized")
        self.assertEqual(self.upstream.called("list_projects"), 0)

    def test_a_wrong_credential_is_401(self) -> None:
        response = self.client.get("/v1/projects", headers=self.auth(OTHER_KEY))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.upstream.called("list_projects"), 0)

    def test_a_prefix_of_the_key_is_401(self) -> None:
        """A truncated key must not pass. Guards against a prefix comparison."""
        response = self.client.get("/v1/projects", headers=self.auth(KEY[:-1]))
        self.assertEqual(response.status_code, 401)

    def test_the_correct_credential_is_accepted(self) -> None:
        response = self.client.get("/v1/projects", headers=self.auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.upstream.called("list_projects"), 1)

    def test_the_scheme_is_matched_case_insensitively_but_required(self) -> None:
        self.assertEqual(
            self.client.get(
                "/v1/projects", headers={"Authorization": "bearer " + KEY}
            ).status_code,
            200,
        )
        for header in ("Basic " + KEY, "Token " + KEY, KEY):
            with self.subTest(header=header):
                response = self.client.get(
                    "/v1/projects", headers={"Authorization": header}
                )
                self.assertEqual(response.status_code, 401)

    def test_a_key_in_the_query_string_is_not_accepted(self) -> None:
        """Never read from there. A query string reaches logs and history."""
        for query in (
            f"?api_key={KEY}",
            f"?key={KEY}",
            f"?token={KEY}",
            f"?access_token={KEY}",
        ):
            with self.subTest(query=query):
                response = self.client.get("/v1/projects" + query)
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.upstream.called("list_projects"), 0)

    def test_a_key_in_a_cookie_or_custom_header_is_not_accepted(self) -> None:
        self.assertEqual(
            self.client.get(
                "/v1/projects", headers={"X-Api-Key": KEY}
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/v1/projects", cookies={"key": KEY}).status_code, 401
        )

    def test_the_comparison_is_constant_time(self) -> None:
        """Asserted structurally, because timing cannot be asserted reliably.

        The route reads ``secrets.compare_digest``; a test that measured
        durations would be flaky on a loaded machine and would pass for a
        ``==`` comparison whenever the scheduler was kind.
        """
        import inspect

        from cofferdam.actions_bridge import service as module

        source = inspect.getsource(module.create_bridge_app)
        self.assertIn("compare_digest", source)
        # And nothing compares the key with ==, which would defeat the above.
        self.assertNotIn("== external_key", source)
        self.assertNotIn("external_key ==", source)

    # -- every mutation is authenticated -------------------------------------

    def test_no_operation_is_anonymous_except_health(self) -> None:
        unauthenticated = [
            ("GET", "/v1/projects"),
            ("GET", "/v1/tasks"),
            ("POST", "/v1/tasks"),
            ("GET", f"/v1/tasks/{TASK_ID}"),
            ("POST", f"/v1/tasks/{TASK_ID}/answer"),
            ("POST", f"/v1/tasks/{TASK_ID}/followup"),
            ("POST", f"/v1/tasks/{TASK_ID}/cancel"),
            ("POST", f"/v1/tasks/{TASK_ID}/finish"),
        ]
        for method, path in unauthenticated:
            with self.subTest(path=path):
                response = self.client.request(method, path, json={})
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.upstream.calls, [])

    def test_health_is_anonymous_and_says_almost_nothing(self) -> None:
        response = self.client.get("/v1/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            sorted(body),
            ["authenticated_operations", "service", "status", "version"],
        )
        # No host, no uptime, no task count, no upstream state.
        text = json.dumps(body).lower()
        for leak in ("task", "uptime", "host", "pid", "127.0.0.1", "cofferdam/"):
            self.assertNotIn(leak, text)

    # -- what exists ---------------------------------------------------------

    def test_only_the_declared_paths_exist(self) -> None:
        declared = {
            route.path
            for route in self.app.routes
            if getattr(route, "path", "").startswith("/v1")
        }
        self.assertEqual(
            declared,
            {
                "/v1/health",
                "/v1/projects",
                # M2J PR4 — the read surface. One GET, no mutation.
                "/v1/projects/{project_id}/context",
                "/v1/tasks",
                "/v1/tasks/{task_id}",
                "/v1/tasks/{task_id}/answer",
                "/v1/tasks/{task_id}/followup",
                "/v1/tasks/{task_id}/cancel",
                "/v1/tasks/{task_id}/finish",
            },
        )
        self.assertEqual(len(OPERATION_IDS), 10)

    def test_there_is_no_generic_proxy_path(self) -> None:
        """Not refused — absent. Each of these is a 404 with no handler."""
        probes = [
            "/api/tasks",
            "/api/status",
            "/api/actions",
            "/api/registries",
            "/api/remote-control/demo-project",
            "/api/task-adapters",
            "/v1/proxy",
            "/v1/api/tasks",
            "/v1/tasks/../../api/status",
            "/v1/../api/tasks",
            "/.env",
            "/admin",
            "/metrics",
            "/",
            "/ws",
            "/healthz",
        ]
        for path in probes:
            with self.subTest(path=path):
                response = self.client.get(path, headers=self.auth())
                self.assertIn(response.status_code, (404, 405))
        self.assertEqual(self.upstream.calls, [])

    def test_an_arbitrary_method_is_refused(self) -> None:
        for method in ("PUT", "PATCH", "DELETE", "HEAD"):
            with self.subTest(method=method):
                response = self.client.request(
                    method, "/v1/projects", headers=self.auth()
                )
                self.assertIn(response.status_code, (404, 405))
        self.assertEqual(self.upstream.called("list_projects"), 0)

    def test_an_external_request_cannot_set_the_internal_auth_header(self) -> None:
        """A caller's headers do not reach the internal client.

        The upstream double records what it was called with, and the internal
        client builds its whole header set from constants — so the only way this
        could fail is a refactor that started forwarding headers.
        """
        self.client.get(
            "/v1/projects",
            headers={
                **self.auth(),
                "X-Forwarded-Authorization": "Bearer smuggled",
                "X-Cofferdam-Token": "smuggled",
                "Cookie": "token=smuggled",
            },
        )
        self.assertEqual(self.upstream.calls, [("list_projects", {})])


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class SecretFileTests(unittest.TestCase):
    """The mode check, and the generator that cannot leak what it writes."""

    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.config = load_bridge_config(self.home)
        (self.home / "secrets").mkdir(parents=True)

    def test_a_missing_file_fails_startup_with_the_path_and_the_remedy(self) -> None:
        with self.assertRaises(BridgeConfigError) as caught:
            read_secret_file(self.config.external_key_path, what="external key")
        message = str(caught.exception)
        self.assertIn(str(self.config.external_key_path), message)
        self.assertIn("--generate-key", message)

    def test_a_group_or_world_readable_file_is_refused(self) -> None:
        path = self.config.external_key_path
        for mode in (0o644, 0o640, 0o604, 0o666, 0o700, 0o755):
            with self.subTest(mode=oct(mode)):
                path.write_text("a-key\n", encoding="utf-8")
                path.chmod(mode)
                with self.assertRaises(BridgeConfigError) as caught:
                    read_secret_file(path, what="external key")
                # Refused, not corrected — and the file still has the bad mode.
                self.assertIn("readable by more than its owner", str(caught.exception))
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)

    def test_an_owner_only_file_is_read(self) -> None:
        path = self.config.external_key_path
        path.write_text("  a-key  \n", encoding="utf-8")
        path.chmod(0o600)
        self.assertEqual(read_secret_file(path, what="external key"), "a-key")

    def test_an_empty_file_is_refused(self) -> None:
        path = self.config.external_key_path
        path.write_text("   \n", encoding="utf-8")
        path.chmod(0o600)
        with self.assertRaises(BridgeConfigError):
            read_secret_file(path, what="external key")

    def test_a_symlinked_credential_is_refused(self) -> None:
        real = self.home / "elsewhere"
        real.write_text("a-key\n", encoding="utf-8")
        real.chmod(0o600)
        try:
            self.config.external_key_path.symlink_to(real)
        except (OSError, NotImplementedError):  # pragma: no cover - platform
            self.skipTest("this platform cannot create a symlink")
        with self.assertRaises(BridgeConfigError) as caught:
            read_secret_file(self.config.external_key_path, what="external key")
        self.assertIn("symlink", str(caught.exception))

    def test_a_generated_key_is_0600_and_never_returned(self) -> None:
        path = generate_external_key(self.config)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        value = path.read_text(encoding="utf-8").strip()
        self.assertGreaterEqual(len(value), 32)
        # The function returns a path, not a secret. Asserted on the signature
        # so a later change that "helpfully" returned the value fails here.
        self.assertIsInstance(path, Path)

    def test_generation_refuses_to_overwrite_without_force(self) -> None:
        first = generate_external_key(self.config).read_text(encoding="utf-8")
        with self.assertRaises(BridgeConfigError):
            generate_external_key(self.config)
        self.assertEqual(
            self.config.external_key_path.read_text(encoding="utf-8"), first
        )
        generate_external_key(self.config, force=True)
        self.assertNotEqual(
            self.config.external_key_path.read_text(encoding="utf-8"), first
        )

    def test_the_two_credentials_are_different_files(self) -> None:
        self.assertNotEqual(
            self.config.external_key_path, self.config.internal_token_path
        )
        self.assertIn("actions-bridge-key", self.config.external_key_path.name)
        self.assertIn("internal", self.config.internal_token_path.name)

    def test_the_config_summary_contains_no_secret_value(self) -> None:
        generate_external_key(self.config)
        secret = self.config.external_key_path.read_text(encoding="utf-8").strip()
        rendered = json.dumps(self.config.summary())
        self.assertNotIn(secret, rendered)
        # The paths are named, which is what an operator needs.
        self.assertIn("actions-bridge-key", rendered)


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class KeyRotationTests(unittest.TestCase):
    """Rotation is 'write a new file and restart', and that is the contract."""

    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.config = load_bridge_config(self.home)
        (self.home / "secrets").mkdir(parents=True)

    def test_a_rotated_key_takes_effect_on_the_next_start(self) -> None:
        generate_external_key(self.config)
        first = read_secret_file(self.config.external_key_path, what="key")
        app = create_bridge_app(
            self.config, external_key=first, internal_client=FakeInternalClient()
        )
        client = TestClient(app)
        self.assertEqual(
            client.get(
                "/v1/projects", headers={"Authorization": "Bearer " + first}
            ).status_code,
            200,
        )

        generate_external_key(self.config, force=True)
        second = read_secret_file(self.config.external_key_path, what="key")
        self.assertNotEqual(first, second)

        # The running app still holds the old key — the value is closed over at
        # startup, deliberately, so a half-written file cannot lock the bridge
        # out mid-flight. A restart is what adopts the new one.
        self.assertEqual(
            client.get(
                "/v1/projects", headers={"Authorization": "Bearer " + second}
            ).status_code,
            401,
        )
        restarted = TestClient(
            create_bridge_app(
                self.config, external_key=second, internal_client=FakeInternalClient()
            )
        )
        self.assertEqual(
            restarted.get(
                "/v1/projects", headers={"Authorization": "Bearer " + second}
            ).status_code,
            200,
        )
        self.assertEqual(
            restarted.get(
                "/v1/projects", headers={"Authorization": "Bearer " + first}
            ).status_code,
            401,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
