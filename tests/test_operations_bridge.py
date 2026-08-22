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

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a dev extra
    yaml = None

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

KEY = "bridge-test-key-not-a-real-credential-0002"


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class BridgeHarness(unittest.TestCase):
    def setUp(self):
        import dataclasses
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # The same construction the other bridge suites use, including the
        # generous limits: a suite that walks four routes several times over is
        # not the traffic the limiter exists to stop, and letting it trip would
        # turn these assertions into flaky ones.
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

    def test_no_traversal_value_ever_reaches_the_workstation(self):
        """The property that matters, stated correctly.

        An earlier version of this asserted that a dot-segment URL is refused.
        It is not, and the reason is worth recording: the HTTP client resolves
        `.` and `..` per RFC 3986 *before the request is sent*, so
        `/v1/operations/alpha/../beta` arrives as `/v1/operations/beta` — a
        different, legitimate route with a clean id. Nothing traversed, and the
        404 the old test wanted would have been the wrong answer.

        What must be true is that no value containing a path separator or a dot
        segment is ever interpolated into an upstream URL. That holds whether the
        client normalized it away or the validator refused it, and it is what
        this now checks.
        """
        for hostile in (
            "../../etc/passwd", "..%2f..%2fetc", "alpha/../beta",
            "%2e%2e%2fetc", "alpha%00", "a" * 200,
        ):
            with self.subTest(value=hostile):
                self.get(f"/v1/operations/{hostile}")

        for name, kwargs in self.upstream.calls:
            for value in kwargs.values():
                if not isinstance(value, str):
                    continue
                self.assertNotIn("..", value, f"{name}: {value!r}")
                self.assertNotIn("/", value, f"{name}: {value!r}")
                self.assertNotIn("%", value, f"{name}: {value!r}")

    def test_a_bare_hostile_segment_is_refused_outright(self):
        """One that survives normalization and must die at the validator."""
        for hostile in ("..%2f..%2fetc", "%2e%2e", "alpha%00"):
            with self.subTest(value=hostile):
                response = self.get(f"/v1/operations/{hostile}")
                self.assertIn(response.status_code, (404, 405))

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

    def test_no_malformed_handle_ever_reaches_the_workstation(self):
        """Same correction as the project-id case: assert the forwarded value.

        A dot-segment handle is resolved away by the client before it is sent, so
        the request becomes some other route. What must never happen is a value
        with a separator, a dot segment or an escape reaching the upstream URL.
        """
        for hostile in ("../../secrets", "plan_../x", "x", "%2e%2e", "plan_" + "z" * 90):
            with self.subTest(value=hostile):
                self.get(self.path(handle=hostile))

        for name, kwargs in self.upstream.calls:
            for value in kwargs.values():
                if not isinstance(value, str):
                    continue
                self.assertNotIn("..", value, f"{name}: {value!r}")
                self.assertNotIn("/", value, f"{name}: {value!r}")

    def test_a_handle_that_survives_normalization_is_refused(self):
        for hostile in ("x", "plan_short", "not-a-handle", "%2e%2e"):
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
        for hostile in ("x", "not-a-handle", "%2e%2e"):
            with self.subTest(value=hostile):
                self.get(self.path(handle=hostile))
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


# -- the translation between the two sides ------------------------------------


class EveryRefusalCodeTheRoutesEmitIsTranslated(unittest.TestCase):
    """The gap between two individually-correct sides.

    The workstation routes refuse with their own codes; the bridge maps upstream
    codes to its own closed vocabulary. Both were right in isolation, and the
    first real end-to-end run showed an unknown project arriving as a 409
    `not_allowed_now` while the published GPT contract declares 404 — because the
    two new codes were never added to the map.

    Unit tests could not see it: the workstation suite asserts the refusal, the
    bridge suite fakes the upstream, and neither runs the translation. So this
    test reads the codes out of the **route source** and checks each one against
    the real translator, which is the narrowest place the two sides meet.
    """

    def test_the_routes_emit_only_codes_the_bridge_can_translate(self):
        """Every refusal code the operations routes construct must translate.

        Read out of the parsed tree, not grepped: the first version matched a
        `"code": "..."` literal and stopped matching the moment the refusal moved
        into a helper, which would have made this guard silently vacuous.
        """
        import ast
        from pathlib import Path

        from cofferdam.actions_bridge.errors import from_upstream_code, status_for

        tree = ast.parse(
            (
                Path(__file__).resolve().parents[1]
                / "cofferdam" / "workstation" / "service.py"
            ).read_text(encoding="utf-8")
        )
        codes = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "_operations_refusal" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    codes.add(first.value)

        self.assertTrue(codes, "no operations refusal codes were found")
        for code in sorted(codes):
            with self.subTest(code=code):
                bridge_code = from_upstream_code(code)
                self.assertEqual(
                    bridge_code, "not_found",
                    f"{code} falls through to {bridge_code}; add it to "
                    "_UPSTREAM_NOT_FOUND in actions_bridge/errors.py",
                )
                self.assertEqual(status_for(bridge_code), 404, code)

    def test_the_routes_use_the_daemon_error_envelope(self):
        """The envelope, not just the code — this is what actually broke.

        `ApiError` serializes to `{"error": {"code": ...}}`, which the bridge
        parses. FastAPI's own exception serializes to `{"detail": {...}}`, the
        bridge finds no code, and a 404 becomes a 409 `not_allowed_now`. Both
        sides were individually correct; only the envelope between them was not.

        Checked structurally, over the parsed tree. A text scan matches the
        prose above, which is the third time that trap has caught a guard in
        this repository.
        """
        import ast
        from pathlib import Path

        tree = ast.parse(
            (
                Path(__file__).resolve().parents[1]
                / "cofferdam" / "workstation" / "service.py"
            ).read_text(encoding="utf-8")
        )
        operations = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name.startswith("read_operation")
                or node.name.startswith("_operations")
                or node.name == "_require_known_project"
            ):
                operations.append(node)
        self.assertTrue(operations, "the operations routes were not found")

        raised = set()
        for function in operations:
            for node in ast.walk(function):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id", None) or getattr(
                        node.func, "attr", None
                    )
                    if name in ("HTTPException", "ApiError"):
                        raised.add(name)
        self.assertNotIn(
            "HTTPException", raised,
            "an operations route raises HTTPException; the bridge cannot read "
            "its envelope. Use ApiError.",
        )
        self.assertIn("ApiError", raised)

    @unittest.skipIf(yaml is None, "PyYAML is not installed (dev extra)")
    def test_the_contract_declares_the_status_the_bridge_returns(self):
        """The published schema and the runtime must agree.

        Gated on PyYAML the way the contract suite is: the stdlib-only runner
        has no parser for the schema, and the two guards above -- which do the
        load-bearing work -- need nothing beyond stdlib and still run there.
        """
        from pathlib import Path

        from cofferdam.actions_bridge.errors import from_upstream_code, status_for

        schema = yaml.safe_load(
            (
                Path(__file__).resolve().parents[1]
                / "docs" / "custom-gpt" / "openapi.yaml"
            ).read_text(encoding="utf-8")
        )
        actual = str(status_for(from_upstream_code("project_unknown")))
        for path in (
            "/v1/operations/{project_id}",
            "/v1/operations/{project_id}/prompt/{planner_request_id}",
            "/v1/operations/{project_id}/result/{dispatch_id}",
        ):
            with self.subTest(path=path):
                declared = set(schema["paths"][path]["get"]["responses"])
                self.assertIn(actual, declared, f"{path} does not declare {actual}")
