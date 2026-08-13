"""M2J PR4 — the whole path, from the bridge's public route to the final bytes.

Custom GPT → bridge auth → scoped internal client → workstation route →
ProjectContextService → message-free LocalContextPack → CloudContextProjection →
serializer → the JSON a caller actually receives.

Both apps are real and are wired to each other over an in-process transport. No
external network is used and no provider is involved; the only "call" is the
bridge reaching the daemon, which is what the deployed pair does over loopback.
"""

from __future__ import annotations

import json
import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

from .test_project_context_api import ContextApiHarness
from .test_workspace_api import PROJECT_ID, WORKSPACE_ID

BRIDGE_KEY = "bridge-key-not-a-real-credential-000000000001"
FAKE_SECRET = "ZQXFAKEtok9d2f81b40c7ae653aa10"
SENTINEL_DESIGN = "SENTINEL-DESIGN-BODY"


class _DaemonTransport:
    """A sync httpx transport that hands each request to the daemon app.

    Deliberately minimal: it forwards method, path, headers and body, and
    returns status, headers and body. It adds no retry, no redirect handling and
    no rewriting, because anything it "helped" with would be a property the real
    client is supposed to have.
    """

    def __init__(self, client) -> None:
        self._client = client

    def handle_request(self, request):
        import httpx

        target = request.url.raw_path.decode("ascii")
        response = self._client.request(
            request.method,
            target,
            headers={
                key: value
                for key, value in request.headers.items()
                if key.lower() not in ("host", "content-length")
            },
            content=request.read() or None,
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=[(k, v) for k, v in response.headers.items()
                     if k.lower() not in ("content-length", "content-encoding")],
            content=response.content,
            request=request,
        )

    def close(self) -> None:
        return None


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class EndToEnd(ContextApiHarness):
    """One vertical slice, asserted on the response the outside world sees."""

    def setUp(self) -> None:
        super().setUp()

        from cofferdam.actions_bridge.internal import InternalTaskClient
        from cofferdam.actions_bridge.service import create_bridge_app

        # The bridge's real internal client, wired to the real daemon app.
        #
        # `httpx.ASGITransport` is async-only and `InternalTaskClient` holds a
        # sync `httpx.Client` on purpose, so the two do not compose. Rather than
        # relax the client for a test, the transport below forwards each request
        # into the daemon's own TestClient — the same app, the same routing, the
        # same credential handling, minus a TCP hop that would add a port and a
        # thread without adding a property worth asserting.
        self.internal = InternalTaskClient(
            base_url="http://workstation.invalid",
            token=self.bridge_token,
            timeout=10.0,
            transport=_DaemonTransport(self.client),
        )
        self.addCleanup(self.internal.close)
        self.bridge_app = create_bridge_app(
            external_key=BRIDGE_KEY, internal_client=self.internal
        )
        self.bridge = TestClient(self.bridge_app)
        self.addCleanup(self.bridge.close)

    def bridge_get(self, project_id=PROJECT_ID, key=BRIDGE_KEY):
        headers = {"Authorization": "Bearer " + key} if key else {}
        return self.bridge.get(
            "/v1/projects/" + str(project_id) + "/context", headers=headers
        )

    # -- the happy path ------------------------------------------------------

    def test_the_full_path_returns_a_bounded_projection(self):
        self.activate_workspace()
        self.client.put(
            "/api/workspace/objective",
            json={"objective": "finish the vertical slice"},
            headers=self.auth,
        )
        self.client.put(
            "/api/workspace/context",
            json={"expected_next_step": "open the PR"},
            headers=self.auth,
        )
        response = self.bridge_get()
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        text = response.text

        # 1-2: identity, resolved by the host
        self.assertEqual(payload["project_id"], PROJECT_ID)
        self.assertEqual(payload["workspace_id"], WORKSPACE_ID)
        # 3-5: the eligible half survives
        self.assertIn("finish the vertical slice", text)
        self.assertIn("open the PR", text)
        refs = {part["source_ref"] for part in payload["context"]["parts"]}
        for role in ("status", "plan", "decisions"):
            self.assertIn("project:" + PROJECT_ID + ":" + role, refs)
        # 6-8: the ineligible half does not
        self.assertNotIn(SENTINEL_DESIGN, text)
        self.assertNotIn("user:current_message", text)
        self.assertNotIn("user_instruction", text)
        for internal in ("active_task", "delegated_worker", "latest_evidence_ref"):
            self.assertNotIn(internal, text)
        # 9: provenance survives, and names the policy
        self.assertEqual(
            payload["context"]["policy_id"], "project_context_external_v1"
        )
        # 10: bounded
        self.assertLess(len(text.encode("utf-8")), 128 * 1024)

    def test_no_global_mind_or_path_crosses_the_full_path(self):
        self.activate_workspace()
        text = self.bridge_get().text
        for fragment in (str(self.home), str(self.project_root), "/home/", "/tmp/",
                         "slots/a", "slots/b", ".obsidian"):
            self.assertNotIn(fragment, text)

    def test_a_fake_secret_in_project_markdown_omits_the_whole_part(self):
        self.activate_workspace()
        (self.project_root / "STATUS.md").write_text(
            "# Status\n\nAn otherwise eligible sentence.\n"
            "API_KEY=" + FAKE_SECRET + "\n",
            encoding="utf-8",
        )
        response = self.bridge_get()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(FAKE_SECRET, response.text)
        self.assertNotIn("otherwise eligible sentence", response.text)

    def test_a_doubled_separator_path_is_redacted_through_the_full_path(self):
        self.activate_workspace()
        (self.project_root / "STATUS.md").write_text(
            "# Status\n\nIt lives at /home//fake-user/thing and slots//a.\n",
            encoding="utf-8",
        )
        text = self.bridge_get().text
        self.assertNotIn("/home//fake-user", text)
        self.assertNotIn("slots//a", text)

    # -- authority -----------------------------------------------------------

    def test_an_unauthenticated_public_request_is_refused(self):
        self.activate_workspace()
        self.assertEqual(self.bridge_get(key=None).status_code, 401)

    def test_a_wrong_bridge_key_is_refused(self):
        self.activate_workspace()
        self.assertEqual(self.bridge_get(key="wrong-key").status_code, 401)

    def test_a_non_active_project_is_refused_through_the_bridge(self):
        """No workspace activated, so the read has no authority to answer."""
        response = self.bridge_get()
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(str(self.project_root), response.text)

    def test_a_hostile_project_id_is_refused_at_the_bridge(self):
        self.activate_workspace()
        for hostile in ("..", "a%2Fb", "a b", "x" * 200):
            with self.subTest(value=hostile):
                response = self.bridge_get(hostile)
                self.assertNotEqual(response.status_code, 200)
                self.assertNotIn("/tmp/", response.text)

    # -- the shape of the surface -------------------------------------------

    def test_the_bridge_exposes_no_sync_workspace_operation(self):
        from cofferdam.actions_bridge.service import OPERATION_IDS

        self.assertNotIn("syncWorkspace", OPERATION_IDS)
        for path in ("/v1/workspace", "/v1/workspaces", "/v1/workspace/sync"):
            with self.subTest(path=path):
                self.assertEqual(
                    self.bridge.get(
                        path, headers={"Authorization": "Bearer " + BRIDGE_KEY}
                    ).status_code,
                    404,
                )

    def test_the_context_route_refuses_every_write_method(self):
        self.activate_workspace()
        headers = {"Authorization": "Bearer " + BRIDGE_KEY}
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.bridge, method)(
                    "/v1/projects/" + PROJECT_ID + "/context", headers=headers
                )
                self.assertEqual(response.status_code, 405)

    def test_repeated_reads_mutate_nothing(self):
        self.activate_workspace()
        self.client.put(
            "/api/workspace/objective",
            json={"objective": "unchanged"},
            headers=self.auth,
        )
        before = self.current().json()["working_context"]
        before_files = {str(path) for path in self.home.rglob("*")}
        before_tasks = self.client.get("/api/tasks", headers=self.auth).json()

        for _ in range(5):
            self.assertEqual(self.bridge_get().status_code, 200)

        self.assertEqual(before, self.current().json()["working_context"])
        self.assertEqual(before_files, {str(path) for path in self.home.rglob("*")})
        self.assertEqual(
            before_tasks, self.client.get("/api/tasks", headers=self.auth).json()
        )

    def test_the_internal_status_field_never_reaches_a_caller(self):
        """The internal client attaches `_status`; the public body must not."""
        self.activate_workspace()
        response = self.bridge_get()
        self.assertNotIn("_status", response.json())
        self.assertNotIn("_status", response.text)

    def test_the_bridge_bound_is_tighter_than_the_workstation_ceiling(self):
        """Both bounds are real, and the smaller one governs externally.

        The workstation refuses above 128 KiB with `response_too_large`; the
        bridge refuses above its own 60 KiB with a 500 rather than truncating.
        Neither ever emits partial JSON, which is the property that matters.
        """
        from cofferdam.actions_bridge.limits import MAX_RESPONSE_BYTES
        from cofferdam.workstation.projectcontext import MAX_SERIALIZED_RESPONSE_BYTES

        self.assertLess(MAX_RESPONSE_BYTES, MAX_SERIALIZED_RESPONSE_BYTES)

    def test_a_large_unicode_project_still_fits_the_bridge_bound(self):
        from cofferdam.actions_bridge.limits import MAX_RESPONSE_BYTES

        self.activate_workspace()
        for name in ("STATUS.md", "ROADMAP.md", "DECISIONS.md"):
            (self.project_root / name).write_text(
                "# H\n\n" + (" é中" * 12000) + "\n", encoding="utf-8"
            )
        response = self.bridge_get()
        self.assertEqual(response.status_code, 200)
        self.assertLess(len(response.text.encode("utf-8")), MAX_RESPONSE_BYTES)

    def test_the_bridge_never_sees_a_local_pack(self):
        """The transport carries the projection envelope and nothing else."""
        self.activate_workspace()
        payload = self.bridge_get().json()
        self.assertEqual(
            sorted(payload), ["context", "project_id", "version", "workspace_id"]
        )
        self.assertEqual(
            sorted(payload["context"]),
            ["budget", "built_at", "limitations", "omissions", "parts",
             "policy_id", "project_id", "version", "workspace_id"],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
