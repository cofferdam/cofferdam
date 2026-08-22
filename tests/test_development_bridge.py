"""M2M PR4's two Actions, exercised through the real bridge request path.

Not the workstation
-------------------

``tests/test_development_ingress.py`` proves what the *daemon* does with a
development request. This file is about the half a Custom GPT actually touches:
the route, the ``require_bridge_key`` dependency, the body allowlist, the
idempotency claim, the rate limiter, the view functions and the error
translation. Those are separate code on the other side of a process boundary,
and PR #83 is the standing reminder that a defect can live entirely in the
translation between two individually correct sides.

What is asserted
----------------

* ``createDevelopmentRequest`` is the only new write, and it is authenticated,
  bounded, rate-limited and idempotent;
* no field a hostile client invents is accepted, and a refused request never
  reaches the workstation at all;
* every upstream refusal code translates to the bridge code the published
  contract declares — including the two PR #83 got wrong by omission;
* ``readOperationQuestion`` is a read with no write anywhere beside it;
* nothing host-private, credential-shaped or path-shaped reaches a body.
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
    from cofferdam.actions_bridge.service import (
        MUTATIONS,
        OPERATION_IDS,
        create_bridge_app,
    )

    from ._actions_bridge_doubles import (
        FAKE_TOKEN,
        HOST_PATH,
        PLANNER_REQUEST_ID,
        PROJECT_ID,
        FakeInternalClient,
        development_request,
        upstream_error,
    )

KEY = "bridge-test-key-not-a-real-credential-0004"
ROUTE = "/v1/development-requests"
INSTRUCTION = "Plan the next step for the remote status screen."


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class BridgeHarness(unittest.TestCase):
    def setUp(self):
        import dataclasses
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = dataclasses.replace(
            load_bridge_config(Path(self._tmp.name)),
            rate_limit_per_minute=100000,
            rate_limit_burst=100000,
            mutation_rate_limit_per_minute=100000,
            mutation_rate_limit_burst=100000,
        )
        self.upstream = FakeInternalClient()
        self.app = create_bridge_app(
            self.config, external_key=KEY, internal_client=self.upstream
        )
        self.addCleanup(self.app.state.idempotency.close)
        self.client = TestClient(self.app)
        self._keys = 0

    def headers(self, key=KEY):
        return {} if key is None else {"Authorization": "Bearer " + key}

    def new_key(self) -> str:
        self._keys += 1
        return "gpt-dev-%06d" % self._keys

    def post(self, *, key=KEY, **overrides):
        body = {
            "project_id": PROJECT_ID,
            "instruction": INSTRUCTION,
            "client_request_id": overrides.pop("client_request_id", self.new_key()),
        }
        body.update(overrides)
        return self.client.post(ROUTE, json=body, headers=self.headers(key))

    def get(self, path, *, key=KEY):
        return self.client.get(path, headers=self.headers(key))


# -- the contract the app serves -----------------------------------------------


class TheActionIsDeclaredCorrectly(BridgeHarness):
    def test_both_operations_are_declared(self):
        self.assertIn("createDevelopmentRequest", OPERATION_IDS)
        self.assertIn("readOperationQuestion", OPERATION_IDS)

    def test_the_write_is_marked_as_a_mutation(self):
        """So the schema test requires x-openai-isConsequential on it."""
        self.assertIn("createDevelopmentRequest", MUTATIONS)
        self.assertNotIn("readOperationQuestion", MUTATIONS)

    def test_no_approval_dispatch_or_publish_action_exists(self):
        for forbidden in (
            "approveWorkerPrompt", "approve", "rejectWorkerPrompt", "reject",
            "answerPlannerQuestion", "submitPlannerAnswer", "dispatchWorker",
            "dispatch", "publishBranch", "openPullRequest", "mergePullRequest",
            "deploy", "cancelDevelopmentRequest",
        ):
            with self.subTest(operation=forbidden):
                self.assertNotIn(forbidden, OPERATION_IDS)

    def test_no_route_exists_for_any_of_them(self):
        paths = {getattr(route, "path", "") for route in self.app.routes}
        for forbidden in (
            "/v1/development-requests/{planner_request_id}/approve",
            "/v1/development-requests/{planner_request_id}/reject",
            "/v1/development-requests/{planner_request_id}/answer",
            "/v1/development-requests/{planner_request_id}/dispatch",
            "/v1/operations/{project_id}/approve",
            "/v1/operations/{project_id}/answer",
        ):
            with self.subTest(path=forbidden):
                self.assertNotIn(forbidden, paths)

    def test_the_internal_client_has_no_way_to_approve_or_dispatch(self):
        for forbidden in (
            "approve", "reject", "answer_question", "dispatch_worker",
            "publish", "open_pull_request", "merge", "deploy",
        ):
            with self.subTest(name=forbidden):
                self.assertFalse(hasattr(self.upstream, forbidden))


# -- authentication -------------------------------------------------------------


class TheActionIsAuthenticated(BridgeHarness):
    def test_a_missing_key_is_refused(self):
        self.assertEqual(self.post(key=None).status_code, 401)

    def test_a_wrong_key_is_refused(self):
        self.assertEqual(self.post(key="wrong").status_code, 401)

    def test_a_key_in_the_query_string_is_not_read(self):
        response = self.client.post(
            ROUTE + "?key=" + KEY,
            json={
                "project_id": PROJECT_ID,
                "instruction": INSTRUCTION,
                "client_request_id": "gpt-query-000001",
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_an_unauthenticated_request_never_reaches_the_workstation(self):
        self.post(key=None)
        self.post(key="wrong")
        self.assertEqual(self.upstream.called("create_development_request"), 0)

    def test_the_question_read_requires_a_key(self):
        path = "/v1/operations/%s/question/%s" % (PROJECT_ID, PLANNER_REQUEST_ID)
        self.assertEqual(self.get(path, key=None).status_code, 401)
        self.assertEqual(self.get(path, key="wrong").status_code, 401)
        self.assertEqual(self.upstream.called("read_operation_question"), 0)


# -- the body is the boundary ----------------------------------------------------


class TheBodyAllowlistIsTheSurface(BridgeHarness):
    FORBIDDEN = (
        "path", "file_path", "repo_root", "project_root", "cwd",
        "working_directory", "branch", "command", "argv", "shell", "env",
        "environment", "executable", "tools", "tool", "mcp_config",
        "permission_mode", "permissions", "model", "provider", "effort",
        "budget", "planner_action", "action", "worker_prompt", "prompt",
        "subject_fingerprint", "dispatch_id", "task_id", "publication_id",
        "approved", "auto_approve", "projection", "context", "transcript",
        "messages", "adapter_id", "workspace_id",
    )

    def test_no_forbidden_field_is_accepted(self):
        for field in self.FORBIDDEN:
            with self.subTest(field=field):
                response = self.post(**{field: "anything"})
                self.assertEqual(response.status_code, 422, field)
                self.assertEqual(
                    response.json()["error"]["code"], "invalid_request", field
                )
        self.assertEqual(self.upstream.called("create_development_request"), 0)

    def test_the_refusal_names_only_the_four_fields_that_exist(self):
        detail = self.post(cwd="/tmp").json()["error"]["detail"]
        self.assertEqual(
            detail,
            "This operation accepts only: client_request_id, instruction, "
            "project_id, research_notes",
        )

    def test_an_empty_instruction_is_refused_before_the_upstream_call(self):
        for bad in ("", "   "):
            with self.subTest(instruction=repr(bad)):
                self.assertEqual(self.post(instruction=bad).status_code, 422)
        self.assertEqual(self.upstream.called("create_development_request"), 0)

    def test_an_oversized_instruction_is_refused_rather_than_trimmed(self):
        from cofferdam.actions_bridge.limits import (
            MAX_DEVELOPMENT_INSTRUCTION_CHARS,
        )

        response = self.post(
            instruction="x" * (MAX_DEVELOPMENT_INSTRUCTION_CHARS + 1)
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("Summarise", response.json()["error"]["message"])
        self.assertEqual(self.upstream.called("create_development_request"), 0)

    def test_a_control_character_in_the_instruction_is_refused(self):
        self.assertEqual(self.post(instruction="do\x00this").status_code, 422)

    def test_a_malformed_project_id_never_reaches_the_workstation(self):
        for bad in ("../../etc/passwd", "a/b", "%2e%2e", "", "x" * 100):
            with self.subTest(project_id=bad):
                self.assertEqual(self.post(project_id=bad).status_code, 404)
        self.assertEqual(self.upstream.called("create_development_request"), 0)

    def test_a_malformed_idempotency_key_is_refused(self):
        for bad in ("short", "has spaces", "x" * 100, 7, None):
            with self.subTest(client_request_id=bad):
                self.assertEqual(
                    self.post(client_request_id=bad).status_code, 422
                )
        self.assertEqual(self.upstream.called("create_development_request"), 0)

    def test_the_body_must_be_json(self):
        response = self.client.post(
            ROUTE, content=b"project_id=demo", headers={
                **self.headers(), "Content-Type": "text/plain",
            }
        )
        self.assertEqual(response.status_code, 415)

    def test_only_the_four_named_fields_reach_the_workstation(self):
        self.post(research_notes="the bearer boundary already exists")
        name, kwargs = self.upstream.calls[-1]
        self.assertEqual(name, "create_development_request")
        self.assertEqual(
            sorted(kwargs),
            ["client_request_id", "instruction", "project_id", "research_notes"],
        )


# -- idempotency and cost --------------------------------------------------------


class Idempotency(BridgeHarness):
    def test_the_same_key_with_a_different_body_is_a_conflict(self):
        self.post(client_request_id="gpt-fixed-000001")
        response = self.post(
            client_request_id="gpt-fixed-000001", instruction="something else"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["error"]["code"], "idempotency_conflict"
        )
        # Refused on the cheap side: the workstation was asked exactly once.
        self.assertEqual(self.upstream.called("create_development_request"), 1)

    def test_an_identical_retry_is_forwarded_and_the_workstation_decides(self):
        """The daemon owns the mapping; this side must not answer for it.

        The bridge's own table cannot know whether a planner call happened —
        it releases its claim whenever the upstream call fails, including on the
        timeout that a planning turn reliably produces. So an identical retry is
        forwarded, and the workstation's durable receipt answers it.
        """
        self.upstream.development_request_payload = development_request(
            replayed=True, _status=200
        )
        response = self.post(client_request_id="gpt-fixed-000001")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["replayed"])

    def test_a_conflicting_key_upstream_is_published_as_a_conflict(self):
        """The PR #83 failure mode, for this PR's codes: named, not defaulted."""
        self.upstream.raises["create_development_request"] = upstream_error(
            "development_request_conflict", "that key was used for another request"
        )
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["error"]["code"], "idempotency_conflict"
        )

    def test_an_in_flight_request_upstream_is_published_as_in_flight(self):
        self.upstream.raises["create_development_request"] = upstream_error(
            "development_request_in_flight", "still planning"
        )
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["error"]["code"], "request_in_flight"
        )

    def test_a_failed_call_releases_the_claim_so_an_identical_retry_works(self):
        self.upstream.raises["create_development_request"] = upstream_error(
            "development_request_in_flight", "still planning"
        )
        self.assertEqual(self.post(client_request_id="gpt-retry-000001").status_code, 409)
        self.upstream.raises.clear()
        response = self.post(client_request_id="gpt-retry-000001")
        self.assertEqual(response.status_code, 201, response.text)

    def test_a_timeout_says_planning_continues_and_names_the_retry(self):
        """The expected first-call outcome, and the message has to say so."""
        from cofferdam.actions_bridge.errors import (
            CODE_UPSTREAM_TIMEOUT,
            BridgeError,
            status_for,
        )

        self.upstream.raises["create_development_request"] = BridgeError(
            code=CODE_UPSTREAM_TIMEOUT,
            message="Cofferdam did not answer in time.",
            status_code=status_for(CODE_UPSTREAM_TIMEOUT),
        )
        response = self.post()
        self.assertEqual(response.status_code, 504)
        message = response.json()["error"]["message"]
        self.assertIn("still planning", message)
        self.assertIn("same client_request_id", message)
        self.assertIn("nothing will be planned twice", message)
        # And it must not tell a caller to sync a task, which is not a thing
        # this Action produced.
        self.assertNotIn("sync the task", message)


class RateLimiting(BridgeHarness):
    def setUp(self):
        import dataclasses
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # The real mutation bucket, not the generous one. Planning costs money,
        # so it is charged against the tighter of the two buckets like every
        # other mutation.
        self.config = dataclasses.replace(
            load_bridge_config(Path(self._tmp.name)),
            mutation_rate_limit_per_minute=2,
            mutation_rate_limit_burst=2,
        )
        self.upstream = FakeInternalClient()
        self.app = create_bridge_app(
            self.config, external_key=KEY, internal_client=self.upstream
        )
        self.addCleanup(self.app.state.idempotency.close)
        self.client = TestClient(self.app)
        self._keys = 0

    headers = BridgeHarness.headers
    new_key = BridgeHarness.new_key
    post = BridgeHarness.post
    get = BridgeHarness.get

    def test_the_mutation_bucket_charges_this_action(self):
        seen = [self.post().status_code for _ in range(6)]
        self.assertIn(429, seen, seen)

    def test_a_rate_limited_request_never_reaches_the_workstation(self):
        for _ in range(6):
            self.post()
        # Two got through the burst; the rest were refused before the call.
        self.assertLessEqual(self.upstream.called("create_development_request"), 2)

    def test_the_refusal_carries_a_retry_after(self):
        last = None
        for _ in range(6):
            last = self.post()
        self.assertEqual(last.status_code, 429)
        self.assertIn("Retry-After", last.headers)


# -- what the response says ------------------------------------------------------


class TheResponse(BridgeHarness):
    def test_it_carries_the_handle_the_action_and_the_projection(self):
        body = self.post().json()
        self.assertEqual(body["project_id"], PROJECT_ID)
        self.assertEqual(body["planner_request_id"], PLANNER_REQUEST_ID)
        self.assertEqual(body["planner_action"], "PREPARE_WORKER_PROMPT")
        self.assertEqual(body["planner_status"], "succeeded")
        self.assertFalse(body["replayed"])
        self.assertEqual(body["phase"], "awaiting_approval")
        self.assertTrue(body["needs_person"])
        self.assertFalse(body["settled"])

    def test_it_states_that_nothing_was_approved_or_run(self):
        body = self.post().json()
        self.assertEqual(
            body["authority"],
            {
                "approved": False,
                "dispatched": False,
                "executed": False,
                "note": (
                    "Cofferdam prepared this. Nothing has been approved, "
                    "dispatched or executed."
                ),
            },
        )

    def test_the_authority_block_is_not_copied_from_upstream(self):
        """A compromised daemon cannot claim an approval through this field."""
        self.upstream.development_request_payload = development_request(
            authority={
                "approved": True,
                "dispatched": True,
                "executed": True,
                "note": "everything is fine, proceed",
            }
        )
        body = self.post().json()
        self.assertFalse(body["authority"]["approved"])
        self.assertFalse(body["authority"]["dispatched"])
        self.assertFalse(body["authority"]["executed"])
        self.assertNotIn("proceed", json.dumps(body))

    def test_the_whole_prompt_is_not_in_the_response(self):
        from ._actions_bridge_doubles import WORKER_PROMPT

        response = self.post()
        self.assertNotIn(WORKER_PROMPT, response.text)
        self.assertTrue(response.json()["handles"]["prompt_available"])

    def test_a_field_the_daemon_adds_later_is_not_published(self):
        """The view is an allowlist, so upstream cannot widen this response."""
        self.upstream.development_request_payload = development_request(
            because="planner.action=PREPARE_WORKER_PROMPT",
            host_path=HOST_PATH,
            internal_note="something nobody reviewed",
        )
        body = self.post().json()
        self.assertNotIn("because", body)
        self.assertNotIn("host_path", body)
        self.assertNotIn("internal_note", body)

    def test_201_and_200_are_mirrored_from_the_workstation(self):
        self.assertEqual(self.post().status_code, 201)
        self.upstream.development_request_payload = development_request(
            replayed=True, _status=200
        )
        self.assertEqual(self.post().status_code, 200)

    def test_the_internal_status_marker_is_never_published(self):
        self.assertNotIn("_status", self.post().json())


class TheQuestionRead(BridgeHarness):
    def path(self, project_id=None, handle=None):
        return "/v1/operations/%s/question/%s" % (
            project_id or PROJECT_ID, handle or PLANNER_REQUEST_ID,
        )

    def test_it_returns_the_exact_question(self):
        response = self.get(self.path())
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("bearer boundary", body["question"])
        self.assertFalse(body["answered"])
        self.assertTrue(body["answering_requires_the_workstation"])
        self.assertEqual(body["source"], "model_authored")

    def test_no_write_verb_is_routable(self):
        for verb in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(verb=verb):
                response = self.client.request(
                    verb,
                    self.path(),
                    json={"answer": "reuse it"},
                    headers=self.headers(),
                )
                self.assertEqual(response.status_code, 405)

    def test_a_malformed_handle_is_refused_rather_than_escaped(self):
        """Only segments that *survive* client-side normalization are tested.

        `httpx` collapses `..` before the request is sent, so a literal
        `../../secrets` never reaches this route at all — it becomes a request
        for a different, shorter path. Asserting a 404 for it would be asserting
        something about the HTTP client. What matters here is the hostile
        segment that arrives intact, and every one of these does.
        """
        for bad in (
            "plan_%2e%2e", "short", "PLAN_UPPER", "plan_.", "plan_" + "x" * 40,
            "plan-01m0k000000000000000000000",
        ):
            with self.subTest(handle=bad):
                self.assertEqual(self.get(self.path(handle=bad)).status_code, 404)
        self.assertEqual(self.upstream.called("read_operation_question"), 0)

    def test_a_foreign_handle_and_a_missing_one_look_the_same(self):
        self.upstream.raises["read_operation_question"] = upstream_error(
            "operations_not_found", "No such operation for this project."
        )
        foreign = self.get(self.path(project_id="other-project"))
        missing = self.get(self.path())
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(
            foreign.json()["error"]["code"], missing.json()["error"]["code"]
        )

    def test_the_answered_flag_is_published_when_a_person_has_decided(self):
        from ._actions_bridge_doubles import operation_question

        self.upstream.operation_question_payload = operation_question(
            answered=True, answered_subject_fingerprint="a" * 64
        )
        body = self.get(self.path()).json()
        self.assertTrue(body["answered"])
        self.assertEqual(body["answered_subject_fingerprint"], "a" * 64)


# -- upstream refusals translate ---------------------------------------------------


class UpstreamRefusalsTranslate(BridgeHarness):
    CASES = (
        ("project_not_found", 404, "not_found"),
        ("project_unknown", 404, "not_found"),
        ("invalid_project_id", 422, "invalid_request"),
        ("development_request_invalid", 422, "invalid_request"),
        ("development_request_conflict", 409, "idempotency_conflict"),
        ("development_request_in_flight", 409, "request_in_flight"),
        ("development_request_not_allowed_now", 409, "not_allowed_now"),
        ("development_request_abandoned", 409, "not_allowed_now"),
        ("development_planner_disabled", 502, "upstream_unavailable"),
        ("planner_unavailable", 502, "upstream_unavailable"),
        ("workspace_not_active", 409, "not_allowed_now"),
    )

    def test_every_upstream_code_maps_to_the_declared_bridge_code(self):
        for upstream_code, status, bridge_code in self.CASES:
            with self.subTest(upstream=upstream_code):
                self.upstream.raises["create_development_request"] = upstream_error(
                    upstream_code, "refused"
                )
                response = self.post()
                self.assertEqual(response.status_code, status, upstream_code)
                self.assertEqual(
                    response.json()["error"]["code"], bridge_code, upstream_code
                )

    def test_a_planner_refusal_is_distinct_from_a_project_refusal(self):
        """Requirement, not cosmetics: they send a person to different places."""
        self.upstream.raises["create_development_request"] = upstream_error(
            "planner_unavailable", "no planner"
        )
        planner = self.post()
        self.upstream.raises["create_development_request"] = upstream_error(
            "project_not_found", "no such project"
        )
        project = self.post()
        self.assertNotEqual(
            planner.json()["error"]["code"], project.json()["error"]["code"]
        )
        self.assertNotEqual(planner.status_code, project.status_code)

    def test_only_the_upstream_code_travels_as_detail(self):
        """The daemon's ``detail`` is dropped; only its code is republished.

        That is where the workstation's internals would otherwise escape — a
        detail is written for the person who owns the machine and names phases,
        handles and reasons. The *message* is republished, which is safe because
        the daemon's refusal messages are code-owned text with no path in them,
        and `tests/test_development_ingress.py` asserts that on the daemon side
        where it can actually be checked.
        """
        self.upstream.raises["create_development_request"] = upstream_error(
            "development_request_not_allowed_now", "that project is busy"
        )
        error = self.post().json()["error"]
        self.assertEqual(error["detail"], "development_request_not_allowed_now")
        self.assertEqual(error["message"], "that project is busy")


# -- nothing leaks -----------------------------------------------------------------


class NothingLeaks(BridgeHarness):
    def test_no_field_outside_the_allowlist_can_carry_anything_outward(self):
        """The bridge's protection is the allowlist, not a path scanner.

        It cannot scrub a value the workstation told it to publish — a
        ``display_name`` is published because it is a display name. What it can
        guarantee is that a *new* upstream field never reaches a caller, and
        that is the assertion worth making here. The daemon-side suite asserts
        no host path is in the values themselves, where the real paths exist.
        """
        self.upstream.development_request_payload = development_request(
            worktree_path=HOST_PATH,
            state_dir=HOST_PATH,
            planner_executable="/usr/bin/claude",
            provider_session_id="sess-abc123",
        )
        response = self.post()
        self.assertNotIn(HOST_PATH, response.text)
        self.assertNotIn("/usr/bin/claude", response.text)
        self.assertNotIn("sess-abc123", response.text)

    def test_no_credential_reaches_a_body(self):
        body = self.post().text + self.get(
            "/v1/operations/%s/question/%s" % (PROJECT_ID, PLANNER_REQUEST_ID)
        ).text
        self.assertNotIn(KEY, body)
        self.assertNotIn(FAKE_TOKEN, body)

    def test_the_response_carries_the_safe_headers(self):
        response = self.post()
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")

    def test_no_cors_header_is_sent(self):
        response = self.post()
        for header in response.headers:
            with self.subTest(header=header):
                self.assertFalse(header.lower().startswith("access-control-"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
