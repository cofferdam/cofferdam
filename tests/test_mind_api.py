"""The authenticated Mind routes: who may read memory, and who may change it.

The security questions a service test can ask that a unit test cannot:

* is every route on the **device token**, including every read;
* does the **bridge credential** reach any of them — it must not, and here the
  reason is stronger than convention: acceptance is the authority to write
  durable memory, and D-2026-08-11-4 says the planner and the Actions bridge
  have no acceptance route *at all*;
* is the request vocabulary genuinely closed — a body carrying `path`, `root`,
  `filename`, `workspace_id` or `source` must be **refused**, not filtered;
* does a read ever change anything on disk;
* is a filesystem path ever published.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the extras are absent
    TestClient = None

TOKEN = "test-device-token-not-a-real-credential"
PROJECT_ID = "demo"
WORKSPACE_ID = "demo-workspace"


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class MindApiTestCase(unittest.TestCase):
    grant_vault = True
    enable_bridge_caller = False

    def setUp(self) -> None:
        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.service import create_app

        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)

        self.project_root = self.home / "projects" / PROJECT_ID
        self.project_root.mkdir(parents=True)
        (self.project_root / "STATUS.md").write_text("# Status\n\noriginal\n", encoding="utf-8")
        (self.project_root / "UNRELATED.md").write_text("keep\n", encoding="utf-8")
        self.vault_root = self.home / "vault"
        self.vault_root.mkdir()
        (self.vault_root / "USER.md").write_text("# User\n", encoding="utf-8")

        config = load_config(self.home)
        overrides = {"enable_validation_task_adapter": True}
        if self.enable_bridge_caller:
            overrides["enable_actions_bridge_caller"] = True
        config = type(config)(**{**config.__dict__, **overrides})
        config.ensure_dirs()

        (config.config_dir / "task-projects.json").write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "project_id": PROJECT_ID,
                            "display_name": "Demo project",
                            "root": str(self.project_root),
                            "adapters": ["validation"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (config.config_dir / "workspaces.json").write_text(
            json.dumps(
                {
                    "workspaces": [
                        {
                            "workspace_id": WORKSPACE_ID,
                            "project_id": PROJECT_ID,
                            "documents": {"status": "STATUS.md"},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.config = config
        if self.grant_vault:
            self.write_grant(enabled=True)

        self.app = create_app(config=config, token=TOKEN, adapter=StubAdapter(config))
        self.client = TestClient(self.app)
        self.auth = {"Authorization": "Bearer " + TOKEN}

    def write_grant(self, *, enabled=True, omit_enabled=False):
        """Write the host's grant, exactly as an operator would.

        `enabled` is a parameter rather than a constant because the tests below
        have to reach the states an operator can actually be in: granted, turned
        off, and — the one this milestone made fail closed — written but never
        activated.
        """
        vault = {"root": str(self.vault_root), "documents": {"user": "USER.md"}}
        if not omit_enabled:
            vault["enabled"] = enabled
        (self.config.config_dir / "mind-grant.json").write_text(
            json.dumps({"global_vault": vault}), encoding="utf-8"
        )

    def remove_grant(self):
        (self.config.config_dir / "mind-grant.json").unlink()

    def activate(self):
        return self.client.put(
            "/api/workspace/active", json={"workspace_id": WORKSPACE_ID}, headers=self.auth
        )

    def propose(self, **body):
        payload = {
            "scope": "project",
            "role": "status",
            "content": "# Status\n\nrewritten\n",
            "reason": "record the new state",
        }
        payload.update(body)
        return self.client.post("/api/mind/proposals", json=payload, headers=self.auth)


ROUTES = (
    ("GET", "/api/mind", None),
    ("GET", "/api/mind/documents/project/status", None),
    ("GET", "/api/mind/proposals", None),
    ("GET", "/api/mind/proposals/mprop_0000000000000000000000000", None),
    ("POST", "/api/mind/proposals",
     {"scope": "project", "role": "status", "content": "x", "reason": "y"}),
    ("POST", "/api/mind/proposals/mprop_0000000000000000000000000/accept", {}),
    ("POST", "/api/mind/proposals/mprop_0000000000000000000000000/reject", {}),
)


class Authentication(MindApiTestCase):
    def test_every_route_requires_the_device_token(self):
        for method, path, body in ROUTES:
            response = self.client.request(method, path, json=body)
            self.assertEqual(response.status_code, 401, method + " " + path)

    def test_a_wrong_token_is_refused(self):
        for method, path, body in ROUTES:
            response = self.client.request(
                method, path, json=body, headers={"Authorization": "Bearer wrong"}
            )
            self.assertEqual(response.status_code, 401, method + " " + path)


class BridgeBoundary(MindApiTestCase):
    """Acceptance is the authority to write memory. The bridge cannot ask for it.

    The refusal is structural rather than a check: these routes depend on
    ``require_token``, which has never heard of the bridge credential. A bridge
    request is a 401 because nothing here can recognise it — the same argument
    D-2026-08-09-2 makes for Remote Control, and the property D-2026-08-11-4
    requires when it says the bridge has *no acceptance route at all*.
    """

    enable_bridge_caller = True

    def setUp(self) -> None:
        super().setUp()
        from cofferdam.workstation.config import load_or_create_actions_bridge_token

        self.bridge_token = load_or_create_actions_bridge_token(self.config)

    def test_the_bridge_credential_exists_on_this_host(self):
        """Otherwise the refusals below would prove nothing."""
        self.assertIsNotNone(self.bridge_token)

    def test_the_bridge_credential_reaches_no_mind_route(self):
        for method, path, body in ROUTES:
            response = self.client.request(
                method,
                path,
                json=body,
                headers={"Authorization": "Bearer " + self.bridge_token},
            )
            self.assertEqual(response.status_code, 401, method + " " + path)

    def test_the_same_credential_does_reach_a_task_route(self):
        """The control: the credential is real and works where it is meant to."""
        response = self.client.get(
            "/api/tasks", headers={"Authorization": "Bearer " + self.bridge_token}
        )
        self.assertEqual(response.status_code, 200)

    def test_the_bridge_application_exposes_no_memory_operation(self):
        """Not a refusal on the bridge — an absence of the route entirely.

        Built as a real application rather than read as source, so this stays
        true if somebody adds a route in a later PR without editing this test.
        """
        from cofferdam.actions_bridge.config import load_bridge_config
        from cofferdam.actions_bridge.service import create_bridge_app

        from ._actions_bridge_doubles import FakeInternalClient

        app = create_bridge_app(
            load_bridge_config(self.home),
            external_key="bridge-test-key-not-a-real-credential-0001",
            internal_client=FakeInternalClient(),
        )
        self.addCleanup(app.state.idempotency.close)
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertIn("/v1/tasks", paths)  # the control: this really is the bridge
        for path in paths:
            for word in ("mind", "memory", "proposal", "vault", "document"):
                self.assertNotIn(word, path, path)


class RouteAuthority(MindApiTestCase):
    """The dependency graph itself, not just the responses it produces.

    A 401 test proves today's behaviour. This proves the *reason* for it: every
    mind route depends on `require_token` and none depends on
    `require_task_caller`, which is the dependency the Actions bridge credential
    can satisfy. A future PR that swapped one for the other would fail here even
    if it kept every status code the same.
    """

    def mind_routes(self):
        return [
            route
            for route in self.app.routes
            if getattr(route, "path", "").startswith("/api/mind")
        ]

    def test_there_are_seven_of_them(self):
        self.assertEqual(len(self.mind_routes()), 7)

    def test_every_one_depends_on_the_device_token_and_not_the_task_caller(self):
        for route in self.mind_routes():
            names = {
                getattr(dependency.call, "__name__", "")
                for dependency in route.dependant.dependencies
            }
            self.assertIn("require_token", names, route.path)
            self.assertNotIn("require_task_caller", names, route.path)

    def test_the_only_writing_methods_are_the_three_proposal_verbs(self):
        """No PUT, no PATCH, no DELETE anywhere in the mind surface."""
        writes = {
            (method, route.path)
            for route in self.mind_routes()
            for method in route.methods
            if method not in ("GET", "HEAD", "OPTIONS")
        }
        self.assertEqual(
            writes,
            {
                ("POST", "/api/mind/proposals"),
                ("POST", "/api/mind/proposals/{proposal_id}/accept"),
                ("POST", "/api/mind/proposals/{proposal_id}/reject"),
            },
        )


class Reads(MindApiTestCase):
    def test_the_overview_is_truthful_before_a_workspace_is_active(self):
        response = self.client.get("/api/mind", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["problem"], "no_active_workspace")
        self.assertTrue(payload["global_vault"]["granted"])

    def test_the_overview_lists_roles_once_a_workspace_is_active(self):
        self.activate()
        payload = self.client.get("/api/mind", headers=self.auth).json()
        self.assertIsNone(payload["problem"])
        roles = {(d["scope"], d["role"]) for d in payload["documents"]}
        self.assertEqual(roles, {("project", "status"), ("global", "user")})

    def test_a_document_read_carries_no_store(self):
        self.activate()
        response = self.client.get("/api/mind/documents/project/status", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_a_read_publishes_no_path(self):
        self.activate()
        for path in ("/api/mind", "/api/mind/documents/project/status",
                     "/api/mind/documents/global/user", "/api/mind/proposals"):
            body = self.client.get(path, headers=self.auth).text
            self.assertNotIn(str(self.project_root), body, path)
            self.assertNotIn(str(self.vault_root), body, path)
            self.assertNotIn("STATUS.md", body, path)

    def test_a_read_changes_nothing_on_disk(self):
        self.activate()
        before = (self.project_root / "STATUS.md").read_bytes()
        self.client.get("/api/mind", headers=self.auth)
        self.client.get("/api/mind/documents/project/status", headers=self.auth)
        self.assertEqual((self.project_root / "STATUS.md").read_bytes(), before)

    def test_an_unknown_scope_or_role_in_the_url_is_refused(self):
        self.activate()
        for path in (
            "/api/mind/documents/filesystem/status",
            "/api/mind/documents/project/passwd",
            "/api/mind/documents/project/..%2F..%2Fetc%2Fpasswd",
            "/api/mind/documents/global/status",
        ):
            response = self.client.get(path, headers=self.auth)
            self.assertIn(response.status_code, (404, 422), path)


class NoGrantRoutes(MindApiTestCase):
    grant_vault = False

    def test_the_global_mind_is_absent_from_the_overview(self):
        self.activate()
        payload = self.client.get("/api/mind", headers=self.auth).json()
        self.assertFalse(payload["global_vault"]["granted"])
        self.assertEqual([d for d in payload["documents"] if d["scope"] == "global"], [])

    def test_a_global_read_is_refused_with_a_semantic_code(self):
        self.activate()
        response = self.client.get("/api/mind/documents/global/user", headers=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "mind_global_grant_missing")

    def test_no_route_can_create_a_grant(self):
        """There is nowhere to send one."""
        for method in ("POST", "PUT", "PATCH"):
            for path in ("/api/mind", "/api/mind/grant", "/api/mind/vault"):
                response = self.client.request(
                    method, path, json={"root": str(self.vault_root)}, headers=self.auth
                )
                self.assertIn(response.status_code, (404, 405), method + " " + path)
        self.assertFalse((self.config.config_dir / "mind-grant.json").exists())


class GrantAuthorityOverTheApi(MindApiTestCase):
    """`enabled: true` is the grant, enforced at every route (D-2026-08-12-2).

    The loader tests prove the parse. These prove the *consequence*: that every
    state short of an explicit yes leaves the global mind unreadable and
    unchangeable through the real API, and that nothing reachable over the
    network can move the host into a yes.
    """

    def global_read(self):
        return self.client.get("/api/mind/documents/global/user", headers=self.auth)

    def propose_global(self):
        return self.client.post(
            "/api/mind/proposals",
            json={
                "scope": "global",
                "role": "user",
                "content": "# User\n\nproposed\n",
                "reason": "y",
            },
            headers=self.auth,
        )

    def test_the_four_inactive_states_all_refuse(self):
        for label, write in (
            ("absent", self.remove_grant),
            ("enabled omitted", lambda: self.write_grant(omit_enabled=True)),
            ("enabled false", lambda: self.write_grant(enabled=False)),
            ("enabled not a boolean", lambda: self.write_grant(enabled="true")),
        ):
            with self.subTest(state=label):
                write()
                read = self.global_read()
                self.assertEqual(read.status_code, 409, label)
                self.assertEqual(
                    read.json()["error"]["code"], "mind_global_grant_missing", label
                )
                overview = self.client.get("/api/mind", headers=self.auth).json()
                self.assertFalse(overview["global_vault"]["granted"], label)
                self.assertEqual(
                    [d for d in overview["documents"] if d["scope"] == "global"], [], label
                )

    def test_only_enabled_true_reads(self):
        self.write_grant(enabled=True)
        self.assertEqual(self.global_read().status_code, 200)

    def test_proposal_creation_follows_the_same_grant_authority(self):
        """A proposal is not a way in around the read refusal."""
        for label, write in (
            ("absent", self.remove_grant),
            ("enabled omitted", lambda: self.write_grant(omit_enabled=True)),
            ("enabled false", lambda: self.write_grant(enabled=False)),
            ("enabled not a boolean", lambda: self.write_grant(enabled=1)),
        ):
            with self.subTest(state=label):
                write()
                self.activate()
                response = self.propose_global()
                self.assertEqual(response.status_code, 409, label)
                self.assertEqual(
                    response.json()["error"]["code"], "mind_global_grant_missing", label
                )

    def test_acceptance_re_resolves_the_grant_and_revocation_refuses(self):
        """The grant is re-read at apply time, not trusted from creation time.

        Each variant turns a *valid, pending* proposal into one whose authority
        has been withdrawn, and asserts the vault file is byte-identical
        afterwards. This is the case a cached grant would get wrong.
        """
        for label, revoke in (
            ("removed", self.remove_grant),
            ("enabled dropped", lambda: self.write_grant(omit_enabled=True)),
            ("enabled false", lambda: self.write_grant(enabled=False)),
            ("enabled not a boolean", lambda: self.write_grant(enabled="yes")),
        ):
            with self.subTest(revocation=label):
                self.write_grant(enabled=True)
                self.activate()
                created = self.propose_global()
                self.assertEqual(created.status_code, 201, label)
                proposal_id = created.json()["proposal_id"]
                before = (self.vault_root / "USER.md").read_bytes()

                revoke()
                response = self.client.post(
                    "/api/mind/proposals/" + proposal_id + "/accept", headers=self.auth
                )
                self.assertEqual(response.status_code, 409, label)
                self.assertEqual(
                    response.json()["error"]["code"], "mind_global_grant_missing", label
                )
                self.assertEqual((self.vault_root / "USER.md").read_bytes(), before, label)

                # Still pending: the authority was withdrawn, the proposal was
                # not decided, and restoring the grant must not have lost it.
                self.write_grant(enabled=True)
                fetched = self.client.get(
                    "/api/mind/proposals/" + proposal_id, headers=self.auth
                ).json()
                self.assertEqual(fetched["state"], "pending", label)

    def test_no_route_can_write_the_grant_file(self):
        """Not a refused write — there is nowhere to send one."""
        self.remove_grant()
        bodies = (
            {"root": str(self.vault_root)},
            {"enabled": True},
            {"global_vault": {"root": str(self.vault_root), "enabled": True}},
        )
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for path in (
                "/api/mind",
                "/api/mind/grant",
                "/api/mind/vault",
                "/api/mind/documents/global/user",
                "/api/mind/proposals",
            ):
                for body in bodies:
                    response = self.client.request(method, path, json=body, headers=self.auth)
                    self.assertNotEqual(response.status_code, 200, method + " " + path)
        self.assertFalse((self.config.config_dir / "mind-grant.json").exists())

    def test_the_grant_path_is_never_published(self):
        self.write_grant(omit_enabled=True)
        for path in ("/api/mind", "/api/mind/documents/global/user"):
            body = self.client.get(path, headers=self.auth).text
            self.assertNotIn(str(self.vault_root), body, path)
            self.assertNotIn("mind-grant.json", body, path)


class BridgeCannotReachTheGrant(MindApiTestCase):
    """The bridge credential cannot read, activate or change the grant."""

    grant_vault = True
    enable_bridge_caller = True

    def setUp(self) -> None:
        super().setUp()
        from cofferdam.workstation.config import load_or_create_actions_bridge_token

        self.bridge_token = load_or_create_actions_bridge_token(self.config)
        self.bridge_auth = {"Authorization": "Bearer " + self.bridge_token}

    def test_the_bridge_cannot_read_or_change_the_grant(self):
        self.assertIsNotNone(self.bridge_token)
        for method, path in (
            ("GET", "/api/mind"),
            ("GET", "/api/mind/documents/global/user"),
            ("POST", "/api/mind"),
            ("PUT", "/api/mind"),
            ("POST", "/api/mind/grant"),
            ("PUT", "/api/mind/grant"),
        ):
            response = self.client.request(
                method, path, json={"enabled": True}, headers=self.bridge_auth
            )
            self.assertNotEqual(response.status_code, 200, method + " " + path)

    def test_the_grant_file_is_unchanged_by_every_bridge_attempt(self):
        before = (self.config.config_dir / "mind-grant.json").read_bytes()
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            self.client.request(
                method, "/api/mind", json={"enabled": False}, headers=self.bridge_auth
            )
        self.assertEqual((self.config.config_dir / "mind-grant.json").read_bytes(), before)


class ClosedVocabulary(MindApiTestCase):
    FORBIDDEN = (
        {"path": "/etc/passwd"},
        {"root": "/etc"},
        {"absolute_path": "/etc/passwd"},
        {"relative_path": "../STATUS.md"},
        {"filename": "STATUS.md"},
        {"cwd": "/tmp"},
        {"command": "rm -rf /"},
        {"argv": ["rm"]},
        {"workspace_id": "other"},
        {"source": "planner"},
        {"state": "applied"},
        {"base_hash": "0" * 64},
        {"operation": "delete"},
        {"adapter_id": "claude-code"},
        {"model": "gpt-4"},
    )

    def test_a_forbidden_field_is_refused_not_filtered(self):
        self.activate()
        for extra in self.FORBIDDEN:
            with self.subTest(field=sorted(extra)[0]):
                response = self.propose(**extra)
                self.assertEqual(response.status_code, 422, sorted(extra)[0])
                self.assertEqual(response.json()["error"]["code"], "invalid_params")

    def test_the_accepted_body_is_exactly_four_fields(self):
        self.activate()
        self.assertEqual(self.propose().status_code, 201)

    def test_a_missing_field_is_refused(self):
        self.activate()
        for missing in ("scope", "role", "content", "reason"):
            body = {
                "scope": "project",
                "role": "status",
                "content": "x\n",
                "reason": "y",
            }
            body.pop(missing)
            response = self.client.post("/api/mind/proposals", json=body, headers=self.auth)
            self.assertEqual(response.status_code, 422, missing)

    def test_an_oversized_body_is_refused_before_it_is_parsed(self):
        self.activate()
        response = self.client.post(
            "/api/mind/proposals",
            content=json.dumps(
                {
                    "scope": "project",
                    "role": "status",
                    "content": "x" * (2 * 1024 * 1024),
                    "reason": "y",
                }
            ),
            headers={**self.auth, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_a_non_json_content_type_is_refused(self):
        self.activate()
        response = self.client.post(
            "/api/mind/proposals",
            content="scope=project",
            headers={**self.auth, "Content-Type": "text/plain"},
        )
        self.assertEqual(response.status_code, 415)

    def test_accept_and_reject_take_no_body_fields(self):
        self.activate()
        proposal_id = self.propose().json()["proposal_id"]
        response = self.client.post(
            "/api/mind/proposals/" + proposal_id + "/accept",
            json={"base_hash": "0" * 64},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual((self.project_root / "STATUS.md").read_text(encoding="utf-8"),
                         "# Status\n\noriginal\n")


class Lifecycle(MindApiTestCase):
    def test_the_whole_path_from_proposal_to_applied(self):
        self.activate()
        created = self.propose()
        self.assertEqual(created.status_code, 201)
        proposal_id = created.json()["proposal_id"]
        # Nothing on disk yet.
        self.assertEqual((self.project_root / "STATUS.md").read_text(encoding="utf-8"),
                         "# Status\n\noriginal\n")

        accepted = self.client.post(
            "/api/mind/proposals/" + proposal_id + "/accept", headers=self.auth
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["state"], "applied")
        self.assertEqual((self.project_root / "STATUS.md").read_text(encoding="utf-8"),
                         "# Status\n\nrewritten\n")
        self.assertEqual((self.project_root / "UNRELATED.md").read_text(encoding="utf-8"),
                         "keep\n")

    def test_a_drifted_target_is_a_409_stale(self):
        self.activate()
        proposal_id = self.propose().json()["proposal_id"]
        (self.project_root / "STATUS.md").write_text("by hand\n", encoding="utf-8")

        response = self.client.post(
            "/api/mind/proposals/" + proposal_id + "/accept", headers=self.auth
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "mind_proposal_stale")
        self.assertEqual((self.project_root / "STATUS.md").read_text(encoding="utf-8"),
                         "by hand\n")

    def test_replaying_an_applied_proposal_is_a_409(self):
        self.activate()
        proposal_id = self.propose().json()["proposal_id"]
        self.client.post("/api/mind/proposals/" + proposal_id + "/accept", headers=self.auth)
        again = self.client.post(
            "/api/mind/proposals/" + proposal_id + "/accept", headers=self.auth
        )
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json()["error"]["code"], "mind_proposal_not_pending")

    def test_rejection_then_acceptance_is_a_409(self):
        self.activate()
        proposal_id = self.propose().json()["proposal_id"]
        rejected = self.client.post(
            "/api/mind/proposals/" + proposal_id + "/reject", headers=self.auth
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.json()["state"], "rejected")

        response = self.client.post(
            "/api/mind/proposals/" + proposal_id + "/accept", headers=self.auth
        )
        self.assertEqual(response.status_code, 409)

    def test_an_unknown_proposal_id_is_a_404(self):
        self.activate()
        for candidate in ("mprop_0000000000000000000000000", "nonsense"):
            response = self.client.post(
                "/api/mind/proposals/" + candidate + "/accept", headers=self.auth
            )
            self.assertEqual(response.status_code, 404, candidate)

    def test_creating_without_an_active_workspace_is_a_409(self):
        response = self.propose()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "workspace_active_unset")

    def test_no_task_is_created_by_any_of_it(self):
        self.activate()
        proposal_id = self.propose().json()["proposal_id"]
        self.client.post("/api/mind/proposals/" + proposal_id + "/accept", headers=self.auth)
        tasks = self.client.get("/api/tasks", headers=self.auth).json()
        self.assertEqual(tasks["tasks"], [])


class UnconfiguredHost(MindApiTestCase):
    """A host with no workspaces, no roles and no grant answers rather than failing."""

    grant_vault = False

    def setUp(self) -> None:
        super().setUp()
        (self.config.config_dir / "workspaces.json").unlink()

    def test_the_overview_answers_truthfully(self):
        response = self.client.get("/api/mind", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["documents"], [])
        self.assertFalse(payload["global_vault"]["granted"])

    def test_listing_proposals_creates_no_database(self):
        response = self.client.get("/api/mind/proposals", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["proposals"], [])
        self.assertFalse((self.config.state_dir / "mind").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
