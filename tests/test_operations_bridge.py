"""The remote read surface, exercised through the real bridge request path.

Not the Python service
----------------------

Every test here goes through the ASGI app: a real route, the real
``require_bridge_key`` dependency, the real id validators, the real view
functions and the real response envelope. Testing the underlying service alone
would leave the part a Custom GPT actually touches unverified — and the parts
most likely to be wrong (auth, id validation, what the view publishes) all live
on this side.

Skipped where the workstation extras are absent, like every other bridge suite,
so this runs in CI's Workstation job. That is stated plainly rather than left for
somebody to infer from a skip count.

What is asserted
----------------

* the surface is **read-only** — every route is a GET, and no verb that could
  change anything is routable;
* authentication is mandatory and unchanged;
* ids are refused rather than escaped, and a foreign handle is indistinguishable
  from a missing one;
* the response publishes the canonical projection rather than re-deriving it;
* nothing host-private, credential-shaped or path-shaped is ever in a body.
"""

from __future__ import annotations

import json
import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the extras are absent
    TestClient = None

if TestClient is not None:
    from cofferdam.actions_bridge.config import load_bridge_config
    from cofferdam.actions_bridge.service import OPERATION_IDS, create_bridge_app

    from ._actions_bridge_doubles import (
        DISPATCH_ID,
        FAKE_TOKEN,
        HOST_PATH,
        PLANNER_REQUEST_ID,
        PROJECT_ID,
        TASK_ID,
        WORKER_CLAIM,
        WORKER_PROMPT,
        FakeInternalClient,
        operations_entry,
        upstream_error,
    )

KEY = "test-bridge-key-0123456789abcdef"


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class BridgeHarness(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.upstream = FakeInternalClient()
        config = load_bridge_config(
            home=Path(self._tmp.name), external_key=KEY, internal_base_url="http://x"
        )
        self.app = create_bridge_app(config=config, internal_client=self.upstream)
        self.client = TestClient(self.app)

    def get(self, path, *, key=KEY):
        headers = {} if key is None else {"Authorization": f"Bearer {key}"}
        return self.client.get(path, headers=headers)

    def body(self, response):
        payload = response.json()
        return payload.get("data", payload)


# -- authentication ------------------------------------------------------------


class TheReadSurfaceIsAuthenticated(BridgeHarness):
    """The existing boundary, unchanged. These routes add no way around it."""

    def paths(self):
        return (
            "/v1/operations",
            f"/v1/operations/{PROJECT_ID}",
            f"/v1/operations/{PROJECT_ID}/prompt/{PLANNER_REQUEST_ID}",
            f"/v1/operations/{PROJECT_ID}/result/{DISPATCH_ID}",
        )

    def test_every_route_requires_a_key(self):
        for path in self.paths():
            with self.subTest(path=path):
                self.assertEqual(self.get(path, key=None).status_code, 401)

    def test_a_wrong_key_is_refused(self):
        for path in self.paths():
            with self.subTest(path=path):
                self.assertEqual(self.get(path, key="wrong").status_code, 401)

    def test_a_key_in_the_query_string_is_not_accepted(self):
        """Query strings reach access logs; the bridge never reads a key there."""
        response = self.client.get(f"/v1/operations?key={KEY}")
        self.assertEqual(response.status_code, 401)

    def test_an_unauthenticated_request_never_reaches_the_workstation(self):
        self.get("/v1/operations", key=None)
        self.assertEqual(self.upstream.called("read_operations"), 0)


# -- read-only ------------------------------------------------------------------


class TheSurfaceCannotChangeAnything(BridgeHarness):
    def test_no_write_verb_is_routable_on_any_operations_path(self):
        for path in (
            "/v1/operations",
            f"/v1/operations/{PROJECT_ID}",
            f"/v1/operations/{PROJECT_ID}/prompt/{PLANNER_REQUEST_ID}",
            f"/v1/operations/{PROJECT_ID}/result/{DISPATCH_ID}",
        ):
            for verb in ("post", "put", "patch", "delete"):
                with self.subTest(path=path, verb=verb):
                    response = getattr(self.client, verb)(
                        path, headers={"Authorization": f"Bearer {KEY}"}
                    )
                    self.assertEqual(response.status_code, 405, f"{verb} {path}")

    def test_the_upstream_client_is_only_ever_asked_to_read(self):
        self.get("/v1/operations")
        self.get(f"/v1/operations/{PROJECT_ID}")
        self.get(f"/v1/operations/{PROJECT_ID}/prompt/{PLANNER_REQUEST_ID}")
        self.get(f"/v1/operations/{PROJECT_ID}/result/{DISPATCH_ID}")
        for name, _ in self.upstream.calls:
            self.assertTrue(name.startswith("read_"), name)

    def test_no_control_operation_is_reachable(self):
        """The phase declares available actions. None of them is implemented."""
        for path in (
            f"/v1/operations/{PROJECT_ID}/approve",
            f"/v1/operations/{PROJECT_ID}/answer",
            f"/v1/operations/{PROJECT_ID}/cancel",
            f"/v1/operations/{PROJECT_ID}/publish",
        ):
            with self.subTest(path=path):
                response = self.client.post(
                    path, headers={"Authorization": f"Bearer {KEY}"}, json={}
                )
                self.assertIn(response.status_code, (404, 405))

    def test_the_four_operations_are_registered(self):
        for name in (
            "readOperations", "readProjectOperations",
            "readOperationPrompt", "readOperationResult",
        ):
            self.assertIn(name, OPERATION_IDS, name)


# -- the overview ----------------------------------------------------------------


class TheOverviewPublishesTheProjection(BridgeHarness):
    def test_it_returns_the_canonical_phase_and_sentence(self):
        payload = self.body(self.get("/v1/operations"))
        entry = payload["projects"][0]
        self.assertEqual(entry["phase"], "pr_ready")
        self.assertIn("ready for your review", entry["sentence"])
        self.assertTrue(entry["needs_person"])

    def test_it_does_not_re_derive_the_phase(self):
        """Whatever the workstation says is what is published."""
        self.upstream.operations_payload = {
            "projects": [operations_entry(phase="worker_running",
                                          sentence="A development worker is editing.",
                                          needs_person=False, busy=True,
                                          settled=False)],
            "count": 1,
        }
        entry = self.body(self.get("/v1/operations"))["projects"][0]
        self.assertEqual(entry["phase"], "worker_running")
        self.assertFalse(entry["needs_person"])
        self.assertTrue(entry["busy"])

    def test_attention_is_computed_from_the_published_predicate(self):
        self.upstream.operations_payload = {
            "projects": [
                operations_entry(project_id="alpha", needs_person=True),
                operations_entry(project_id="beta", needs_person=False),
            ],
            "count": 2,
        }
        payload = self.body(self.get("/v1/operations"))
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["attention_count"], 1)
        self.assertEqual(payload["attention"][0]["project_id"], "alpha")

    def test_the_handles_a_later_control_needs_are_published(self):
        entry = self.body(self.get("/v1/operations"))["projects"][0]
        handles = entry["handles"]
        self.assertEqual(handles["planner_request_id"], PLANNER_REQUEST_ID)
        self.assertEqual(handles["dispatch_id"], DISPATCH_ID)
        self.assertTrue(handles["prompt_available"])

    def test_the_internal_because_field_is_not_published(self):
        """It names an internal row and column. A debugging aid, not an export."""
        rendered = json.dumps(self.body(self.get("/v1/operations")))
        self.assertNotIn("because", rendered)
        self.assertNotIn("publication.state=published", rendered)

    def test_the_prompt_is_not_carried_by_a_status_read(self):
        rendered = json.dumps(self.body(self.get("/v1/operations")))
        self.assertNotIn(WORKER_PROMPT.strip(), rendered)

    def test_an_unknown_field_added_upstream_is_not_published(self):
        """An allowlist, so a new upstream field is opt-in rather than automatic."""
        self.upstream.operations_payload = {
            "projects": [operations_entry(secret_note=HOST_PATH)],
            "count": 1,
        }
        rendered = json.dumps(self.body(self.get("/v1/operations")))
        self.assertNotIn("secret_note", rendered)
        self.assertNotIn(HOST_PATH, rendered)


# -- one project ------------------------------------------------------------------


class TheProjectDetailIsScoped(BridgeHarness):
    def test_it_forwards_the_validated_project_id(self):
        self.get(f"/v1/operations/{PROJECT_ID}")
        name, kwargs = self.upstream.calls[-1]
        self.assertEqual(name, "read_project_operations")
        self.assertEqual(kwargs["project_id"], PROJECT_ID)

    def test_a_traversal_attempt_is_refused_not_escaped(self):
        for hostile in ("../../etc/passwd", "..%2f..%2fetc", "alpha/../beta"):
            with self.subTest(value=hostile):
                response = self.get(f"/v1/operations/{hostile}")
                self.assertIn(response.status_code, (404, 405))
                self.assertEqual(self.upstream.called("read_project_operations"), 0)

    def test_an_unknown_project_is_a_not_found_from_upstream(self):
        self.upstream.raises["read_project_operations"] = upstream_error(
            "not_found", "No such project on this workstation."
        )
        response = self.get("/v1/operations/nosuchproject")
        self.assertEqual(response.status_code, 404)

    def test_a_disabled_project_looks_the_same_as_an_unknown_one(self):
        self.upstream.raises["read_project_operations"] = upstream_error(
            "not_found", "No such project on this workstation."
        )
        disabled = self.get("/v1/operations/disabledproject")
        unknown = self.get("/v1/operations/nosuchproject")
        self.assertEqual(disabled.status_code, unknown.status_code)
        self.assertEqual(disabled.json()["error"]["message"],
                         unknown.json()["error"]["message"])


# -- prompt and evidence -----------------------------------------------------------


class ThePromptReadIsAddressedByBothIds(BridgeHarness):
    def path(self, project_id=None, handle=None):
        # Resolved in the body, not as defaults: the constants are imported only
        # when the extras are present, and a default argument is evaluated at
        # class-definition time -- which happens even when the suite is skipped.
        return (
            f"/v1/operations/{project_id or PROJECT_ID}"
            f"/prompt/{handle or PLANNER_REQUEST_ID}"
        )

    def test_the_exact_prompt_comes_back(self):
        payload = self.body(self.get(self.path()))
        self.assertEqual(payload["prompt"], WORKER_PROMPT)
        self.assertTrue(payload["matches_dispatched_digest"])
        self.assertFalse(payload["truncated"])

    def test_both_ids_are_forwarded(self):
        self.get(self.path())
        name, kwargs = self.upstream.calls[-1]
        self.assertEqual(name, "read_operation_prompt")
        self.assertEqual(kwargs["project_id"], PROJECT_ID)
        self.assertEqual(kwargs["planner_request_id"], PLANNER_REQUEST_ID)

    def test_a_malformed_handle_never_reaches_the_workstation(self):
        for hostile in ("../../secrets", "plan_../x", "x", "%2e%2e"):
            with self.subTest(value=hostile):
                response = self.get(self.path(handle=hostile))
                self.assertIn(response.status_code, (404, 405))
        self.assertEqual(self.upstream.called("read_operation_prompt"), 0)

    def test_a_foreign_handle_is_a_plain_not_found(self):
        self.upstream.raises["read_operation_prompt"] = upstream_error(
            "not_found", "No such operation for this project."
        )
        response = self.get(self.path(project_id="beta"))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("beta", response.json()["error"]["message"].lower())

    def test_a_prompt_that_no_longer_matches_says_so(self):
        self.upstream.operation_prompt_payload = dict(
            self.upstream.operation_prompt_payload, matches_dispatched_digest=False
        )
        payload = self.body(self.get(self.path()))
        self.assertFalse(payload["matches_dispatched_digest"])


class TheResultReadSeparatesFactFromClaim(BridgeHarness):
    def path(self, project_id=None, handle=None):
        return (
            f"/v1/operations/{project_id or PROJECT_ID}"
            f"/result/{handle or DISPATCH_ID}"
        )

    def test_machine_facts_are_labelled_as_observed_by_cofferdam(self):
        payload = self.body(self.get(self.path()))
        self.assertEqual(payload["machine"]["observed_by"], "cofferdam")
        self.assertEqual(payload["machine"]["commit"], "a" * 40)
        self.assertIs(payload["machine"]["checks"]["exit_zero"], True)

    def test_the_pull_request_is_addressable(self):
        payload = self.body(self.get(self.path()))
        pull_request = payload["machine"]["publication"]["pull_request"]
        self.assertEqual(pull_request["number"], 5)
        self.assertIn("/pull/5", pull_request["url"])

    def test_the_worker_report_is_a_labelled_claim(self):
        payload = self.body(self.get(self.path()))
        self.assertEqual(payload["claims"]["worker_report"], WORKER_CLAIM)
        self.assertEqual(payload["claims"]["source"], "model_authored")

    def test_no_claim_appears_in_the_machine_block(self):
        payload = self.body(self.get(self.path()))
        self.assertNotIn(WORKER_CLAIM, json.dumps(payload["machine"]))

    def test_completion_is_not_acceptance_is_restated(self):
        payload = self.body(self.get(self.path()))
        self.assertTrue(payload["worker_completion_is_not_acceptance"])

    def test_a_malformed_dispatch_handle_never_reaches_upstream(self):
        self.get(self.path(handle="../../etc"))
        self.assertEqual(self.upstream.called("read_operation_result"), 0)


# -- one sweep for everything that must never appear -------------------------------


class NothingHostPrivateIsEverPublished(BridgeHarness):
    """One pass over every response body in this surface."""

    def bodies(self):
        self.upstream.operations_payload = {
            "projects": [
                operations_entry(
                    worktree_path=HOST_PATH,
                    machine=dict(
                        operations_entry()["machine"],
                        credential_path="/home/nrgis/cofferdam/state/git-publisher",
                        token=FAKE_TOKEN,
                    ),
                )
            ],
            "count": 1,
        }
        self.upstream.operation_prompt_payload = dict(
            self.upstream.operation_prompt_payload, host_path=HOST_PATH
        )
        self.upstream.operation_result_payload = dict(
            self.upstream.operation_result_payload, host_path=HOST_PATH
        )
        return [
            self.get("/v1/operations"),
            self.get(f"/v1/operations/{PROJECT_ID}"),
            self.get(f"/v1/operations/{PROJECT_ID}/prompt/{PLANNER_REQUEST_ID}"),
            self.get(f"/v1/operations/{PROJECT_ID}/result/{DISPATCH_ID}"),
        ]

    def test_no_response_carries_a_host_path(self):
        for response in self.bodies():
            rendered = json.dumps(response.json())
            self.assertNotIn(HOST_PATH, rendered)
            self.assertNotIn("/home/", rendered)
            self.assertNotIn("worktree_path", rendered)

    def test_no_response_carries_credential_material(self):
        for response in self.bodies():
            rendered = json.dumps(response.json())
            for forbidden in (FAKE_TOKEN, "github_pat_", "credential_path",
                              "git-credentials", "sk-ant-", "Bearer "):
                self.assertNotIn(forbidden, rendered, forbidden)

    def test_every_response_carries_the_safe_headers(self):
        for response in self.bodies():
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_no_response_carries_an_environment_variable_name(self):
        for response in self.bodies():
            rendered = json.dumps(response.json())
            for forbidden in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "COFFERDAM_HOME",
                              "PATH", "HOME"):
                self.assertNotIn(forbidden, rendered, forbidden)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
