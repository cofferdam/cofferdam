"""The eight Actions: what they accept, what they publish, what they never say.

Organised by the question each section answers.

1. Projects — safe labels, no path, no note.
2. create_task — the closed vocabulary, and no provider settings.
3. recent and sync — every state, and the fields that must never appear.
4. Choice answers — one option id, and every shape that is not one.
5. Follow-up — the same-task contract.
6. Cancel and finish — legal transitions and truthful repeats.
7. Bounds and safety headers.
8. Leakage — one sweep over every response for things that must not be in it.
"""

from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the extras are absent
    TestClient = None

if TestClient is not None:
    from cofferdam.actions_bridge.config import load_bridge_config
    from cofferdam.actions_bridge.limits import (
        MAX_FOLLOWUP_TEXT_CHARS,
        MAX_RESULT_CHARS,
        MAX_TASK_TEXT_CHARS,
    )
    from cofferdam.actions_bridge.service import create_bridge_app

    from ._actions_bridge_doubles import (
        OTHER_TASK_ID,
        PROJECT_ID,
        QUESTION_ID,
        TASK_ID,
        FakeInternalClient,
        project,
        question,
        result,
        snapshot,
        upstream_error,
    )

KEY = "bridge-test-key-not-a-real-credential-0001"


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class BridgeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        # Deliberately generous rate limits for the functional suite. A test
        # that enumerates twenty-six refused fields is not the traffic the
        # limiter exists to stop, and letting it trip would turn a contract
        # assertion into a flaky one. The limiter's own behaviour is asserted
        # in RateLimitTests below, against the shipped defaults.
        self.config = dataclasses.replace(
            load_bridge_config(self.home),
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
        self.headers = {"Authorization": "Bearer " + KEY}
        self._request_number = 0

    def key(self) -> str:
        self._request_number += 1
        return f"test-request-{self._request_number:04d}"

    def get(self, path: str, **kwargs):
        return self.client.get(path, headers=self.headers, **kwargs)

    def post(self, path: str, body: dict):
        return self.client.post(path, headers=self.headers, json=body)

    def create(self, **overrides):
        body = {
            "project_id": PROJECT_ID,
            "task_text": "Add a regression test for the empty-input case.",
            "client_request_id": self.key(),
        }
        body.update(overrides)
        return self.post("/v1/tasks", body)


# -- 1. projects ---------------------------------------------------------------


class ProjectTests(BridgeTestCase):
    def test_projects_publish_safe_labels_only(self) -> None:
        response = self.get("/v1/projects")
        self.assertEqual(response.status_code, 200)
        entry = response.json()["projects"][0]
        self.assertEqual(
            sorted(entry),
            ["accepts_tasks", "display_name", "enabled", "project_id", "task_adapters"],
        )
        self.assertEqual(entry["project_id"], PROJECT_ID)

    def test_no_root_path_or_note_is_ever_returned(self) -> None:
        self.upstream.projects_payload = {
            "projects": [project()],
            "configured": 1,
            "problems": [{"project_id": "broken", "reason": "root is a symlink"}],
            "source_present": True,
        }
        body = self.get("/v1/projects").text
        for leak in (
            "/home/",
            "root",
            "internal note",
            "notes",
            "remote_control",
            "problems",
            "symlink",
        ):
            self.assertNotIn(leak, body, f"{leak!r} leaked into the projects response")

    def test_a_disabled_project_is_not_listed(self) -> None:
        self.upstream.projects_payload = {"projects": [project(enabled=False)]}
        self.assertEqual(self.get("/v1/projects").json()["projects"], [])

    def test_a_project_with_no_adapter_is_not_listed(self) -> None:
        """An enabled project that permits no adapter cannot take a task.

        Listing it would invite a create that Cofferdam refuses for a reason the
        caller cannot see, and the model would keep trying.
        """
        self.upstream.projects_payload = {"projects": [project(adapters=[])]}
        self.assertEqual(self.get("/v1/projects").json()["projects"], [])

    def test_a_malformed_registry_entry_is_dropped_not_published(self) -> None:
        self.upstream.projects_payload = {
            "projects": [{"display_name": "no id"}, None, "a string", project()]
        }
        listed = self.get("/v1/projects").json()["projects"]
        self.assertEqual([p["project_id"] for p in listed], [PROJECT_ID])


# -- 2. create_task -------------------------------------------------------------


class CreateTaskTests(BridgeTestCase):
    def test_a_bounded_task_is_created(self) -> None:
        response = self.create(title="Parser test")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["task_id"], TASK_ID)
        self.assertTrue(body["display_ref"].startswith("CF-"))
        self.assertTrue(body["created"])
        self.assertFalse(body["replayed"])
        self.assertEqual(body["next_recommended_operation"], "sync_task")

    def test_the_adapter_comes_from_the_host_registry_not_the_caller(self) -> None:
        """Task Core requires an adapter id; the *host* supplies it.

        The bridge sends the adapter the workstation delegated, and nothing
        else. There is no field on this Action for one, and a body carrying
        ``adapter_id`` is refused (see the unknown-fields test), so a model
        cannot choose which agent runs on somebody's workstation.
        """
        self.upstream.projects_payload = {
            "projects": [
                project(
                    adapters=["claude-code", "claude-agent-sdk"],
                    delegated_adapter="claude-code",
                    delegation="ok",
                )
            ]
        }
        self.create()
        _, kwargs = self.upstream.calls[-1]
        self.assertEqual(kwargs["adapter_id"], "claude-code")
        # And the registry read happened before the create, on the same request.
        self.assertEqual(
            [name for name, _ in self.upstream.calls][-2:],
            ["list_projects", "create_task"],
        )

    def test_a_project_that_permits_no_adapter_cannot_be_created_in(self) -> None:
        self.upstream.projects_payload = {"projects": [project(adapters=[])]}
        response = self.create()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.upstream.called("create_task"), 0)

    def test_a_disabled_project_cannot_be_created_in(self) -> None:
        self.upstream.projects_payload = {"projects": [project(enabled=False)]}
        self.assertEqual(self.create().status_code, 404)
        self.assertEqual(self.upstream.called("create_task"), 0)

    def test_a_project_absent_from_the_registry_cannot_be_created_in(self) -> None:
        self.upstream.projects_payload = {"projects": [project(project_id="other")]}
        self.assertEqual(self.create().status_code, 404)
        self.assertEqual(self.upstream.called("create_task"), 0)

    def test_unknown_fields_are_refused_not_ignored(self) -> None:
        hostile = [
            "adapter_id",
            "cwd",
            "path",
            "project_root",
            "working_directory",
            "model",
            "effort",
            "tools",
            "permission_mode",
            "budget",
            "env",
            "environment",
            "executable",
            "argv",
            "command",
            "shell",
            "mcp_config",
            "hooks",
            "metadata",
            "origin",
            "source",
            "provider_session_id",
            "session_id",
            "approval_id",
            "tool_name",
            "allow",
        ]
        for field in hostile:
            with self.subTest(field=field):
                response = self.create(**{field: "anything"})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.json()["error"]["code"], "invalid_request"
                )
        self.assertEqual(self.upstream.called("create_task"), 0)

    def test_an_oversized_task_text_is_refused_not_truncated(self) -> None:
        response = self.create(task_text="x" * (MAX_TASK_TEXT_CHARS + 1))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.upstream.called("create_task"), 0)

    def test_an_empty_or_control_character_task_text_is_refused(self) -> None:
        for hostile in ("", "   ", "has\x00a null", "bell\x07here", "esc\x1bhere"):
            with self.subTest(text=repr(hostile)):
                self.assertEqual(self.create(task_text=hostile).status_code, 422)
        self.assertEqual(self.upstream.called("create_task"), 0)

    def test_expected_output_is_composed_under_a_fixed_heading(self) -> None:
        self.create(task_text="Do the thing.", expected_output="A passing test.")
        _, kwargs = self.upstream.calls[-1]
        self.assertEqual(
            kwargs["prompt"], "Do the thing.\n\nExpected output:\nA passing test."
        )

    def test_the_combined_text_cannot_exceed_the_task_bound(self) -> None:
        response = self.create(
            task_text="x" * (MAX_TASK_TEXT_CHARS - 10), expected_output="y" * 500
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.upstream.called("create_task"), 0)

    def test_an_unknown_project_shape_is_refused_before_any_upstream_call(self) -> None:
        for hostile in (
            "../../etc",
            "/home/someone",
            "Demo Project",
            "demo project",
            "",
            "x" * 200,
        ):
            with self.subTest(project_id=hostile):
                response = self.create(project_id=hostile)
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.upstream.called("create_task"), 0)

    def test_a_project_cofferdam_refuses_becomes_a_bounded_error(self) -> None:
        self.upstream.raises["create_task"] = upstream_error("task_project_disabled")
        response = self.create()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "project_not_eligible")

    # -- idempotency ---------------------------------------------------------

    def test_an_identical_retry_creates_one_task(self) -> None:
        request_id = self.key()
        first = self.create(client_request_id=request_id)
        second = self.create(client_request_id=request_id)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["replayed"])
        self.assertFalse(second.json()["created"])
        self.assertEqual(self.upstream.called("create_task"), 1)

    def test_the_same_id_with_a_different_body_is_a_conflict(self) -> None:
        request_id = self.key()
        self.create(client_request_id=request_id)
        response = self.create(
            client_request_id=request_id, task_text="Something else entirely."
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "idempotency_conflict")
        self.assertEqual(self.upstream.called("create_task"), 1)

    def test_a_failed_create_releases_its_request_id_for_a_real_retry(self) -> None:
        request_id = self.key()
        self.upstream.raises["create_task"] = upstream_error(
            "task_store_unavailable"
        )
        self.assertEqual(self.create(client_request_id=request_id).status_code, 409)
        # The mutation never happened, so the identical retry must be allowed to
        # try again rather than meeting a conflict about work nobody did.
        self.upstream.raises.clear()
        self.assertEqual(self.create(client_request_id=request_id).status_code, 201)

    def test_a_malformed_request_id_is_refused(self) -> None:
        for hostile in ("", "short", "has spaces here", "x" * 65, "has/slash", None, 7):
            with self.subTest(value=repr(hostile)):
                self.assertEqual(
                    self.create(client_request_id=hostile).status_code, 422
                )
        self.assertEqual(self.upstream.called("create_task"), 0)

    def test_a_missing_request_id_is_refused(self) -> None:
        response = self.post(
            "/v1/tasks", {"project_id": PROJECT_ID, "task_text": "Do it."}
        )
        self.assertEqual(response.status_code, 422)

    def test_task_core_reporting_a_duplicate_is_reported_truthfully(self) -> None:
        """Cofferdam's own idempotency, seen through the bridge.

        ``created: false`` upstream means a key matched there. The bridge does
        not dress that up as a fresh creation.
        """
        self.upstream.create_payload = {
            "task": snapshot(),
            "created": False,
            "_status": 200,
        }
        response = self.create()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["created"])
        self.assertTrue(response.json()["replayed"])


# -- 3. recent and sync ---------------------------------------------------------


class RecentTaskTests(BridgeTestCase):
    def test_the_list_is_bounded_and_has_no_content(self) -> None:
        response = self.get("/v1/tasks")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["limit"], 10)
        row = body["tasks"][0]
        self.assertEqual(
            sorted(row),
            [
                "display_ref",
                "follow_up_available",
                "has_pending_question",
                "local_action_required",
                "project",
                "result_available",
                "state",
                "task_id",
                "terminal",
                "title",
                "updated_at",
            ],
        )

    def test_the_caller_cannot_raise_the_cap(self) -> None:
        self.get("/v1/tasks?limit=100")
        _, kwargs = self.upstream.calls[-1]
        self.assertEqual(kwargs["limit"], 20)
        self.assertEqual(self.get("/v1/tasks?limit=100").json()["limit"], 20)

    def test_a_nonsense_limit_is_refused_by_the_contract(self) -> None:
        for hostile in ("0", "-5", "abc", "1e9"):
            with self.subTest(limit=hostile):
                self.assertEqual(
                    self.get(f"/v1/tasks?limit={hostile}").status_code, 422
                )

    def test_the_order_is_deterministic(self) -> None:
        self.upstream.tasks_payload = {
            "tasks": [
                snapshot(task_id=OTHER_TASK_ID),
                snapshot(task_id=TASK_ID),
            ]
        }
        first = [row["task_id"] for row in self.get("/v1/tasks").json()["tasks"]]
        self.upstream.tasks_payload["tasks"].reverse()
        second = [row["task_id"] for row in self.get("/v1/tasks").json()["tasks"]]
        self.assertEqual(first, second)


class SyncTests(BridgeTestCase):
    def test_a_running_task_is_not_asked_for_a_result(self) -> None:
        response = self.get(f"/v1/tasks/{TASK_ID}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["state"], "running")
        self.assertFalse(body["result"]["available"])
        self.assertEqual(body["next_recommended_operation"], "sync_task")
        self.assertEqual(self.upstream.called("get_result"), 0)
        self.assertEqual(self.upstream.called("list_clarifications"), 0)

    def test_a_pending_question_is_published_with_its_option_ids(self) -> None:
        self.upstream.task_payload = {
            "task": snapshot(state="waiting_for_user", waiting_reason="clarification")
        }
        body = self.get(f"/v1/tasks/{TASK_ID}").json()
        pending = body["pending_question"]
        self.assertTrue(pending["clarification_supported"])
        self.assertEqual(pending["answer_mode"], "single_choice")
        self.assertEqual(
            [option["option_id"] for option in pending["options"]], ["opt1", "opt2"]
        )
        self.assertEqual(body["next_recommended_operation"], "submit_choice_answer")
        self.assertFalse(body["follow_up_available"])

    def test_an_option_value_is_never_published(self) -> None:
        self.upstream.task_payload = {
            "task": snapshot(state="waiting_for_user", waiting_reason="clarification")
        }
        body = self.get(f"/v1/tasks/{TASK_ID}").text
        self.assertNotIn('"value"', body)
        self.assertNotIn("provider-value-alpha", body)

    def test_an_unsupported_question_shape_is_reported_not_fabricated(self) -> None:
        for mode in ("free_text", "multiple_choice", "unknown", "something_new"):
            with self.subTest(mode=mode):
                self.upstream.task_payload = {
                    "task": snapshot(
                        state="waiting_for_user", waiting_reason="clarification"
                    )
                }
                self.upstream.clarifications_payload = {
                    "clarifications": [question(answer_mode=mode)]
                }
                pending = self.get(f"/v1/tasks/{TASK_ID}").json()["pending_question"]
                self.assertFalse(pending["clarification_supported"])
                self.assertEqual(pending["reason"], "unsupported_question_shape")
                self.assertTrue(pending["local_action_required"])
                self.assertEqual(pending["options"], [])
                # The question text survives, so a person knows what is being
                # asked. Nothing is invented in its place.
                self.assertEqual(pending["question"], "Where should the new test live?")

    def test_options_without_ids_are_reported_as_unsubmittable(self) -> None:
        self.upstream.task_payload = {
            "task": snapshot(state="waiting_for_user", waiting_reason="clarification")
        }
        self.upstream.clarifications_payload = {
            "clarifications": [
                question(
                    options=[
                        {"label": "One", "value": "1", "option_id": None},
                        {"label": "Two", "value": "2", "option_id": None},
                    ]
                )
            ]
        }
        pending = self.get(f"/v1/tasks/{TASK_ID}").json()["pending_question"]
        self.assertFalse(pending["clarification_supported"])
        self.assertEqual(pending["reason"], "options_not_submittable")

    def test_a_local_approval_is_reported_and_cannot_be_satisfied(self) -> None:
        for reason in ("approval", "authentication", "privileged_action"):
            with self.subTest(reason=reason):
                self.upstream.task_payload = {
                    "task": snapshot(
                        state="waiting_for_user", waiting_reason=reason
                    )
                }
                body = self.get(f"/v1/tasks/{TASK_ID}").json()
                self.assertTrue(body["local_action_required"])
                self.assertEqual(body["local_action_reason"], reason)
                self.assertEqual(
                    body["next_recommended_operation"],
                    "open_the_local_cofferdam_surface",
                )
                self.assertIsNone(body["pending_question"])
                self.assertFalse(body["follow_up_available"])
                # And no clarification read happened: an approval is not one.
                self.assertEqual(self.upstream.called("list_clarifications"), 0)

    def test_a_completed_task_carries_its_result(self) -> None:
        self.upstream.task_payload = {
            "task": snapshot(state="completed", terminal=True)
        }
        body = self.get(f"/v1/tasks/{TASK_ID}").json()
        self.assertTrue(body["result"]["available"])
        self.assertTrue(body["result"]["is_final"])
        self.assertTrue(body["result"]["succeeded"])
        self.assertEqual(body["next_recommended_operation"], "nothing")

    def test_a_ready_for_followup_task_offers_a_follow_up(self) -> None:
        self.upstream.task_payload = {"task": snapshot(state="ready_for_followup")}
        self.upstream.result_payload = {
            "result": result(outcome="completed", terminal=False, follow_up=True)
        }
        body = self.get(f"/v1/tasks/{TASK_ID}").json()
        self.assertTrue(body["follow_up_available"])
        self.assertFalse(body["result"]["is_final"])
        self.assertTrue(body["can_finish"])
        self.assertEqual(
            body["next_recommended_operation"], "send_followup_or_finish_task"
        )

    def test_a_long_result_is_truncated_and_says_so(self) -> None:
        self.upstream.task_payload = {
            "task": snapshot(state="completed", terminal=True)
        }
        self.upstream.result_payload = {"result": result(text="y" * 40000)}
        body = self.get(f"/v1/tasks/{TASK_ID}").json()
        self.assertTrue(body["result"]["truncated"])
        self.assertEqual(len(body["result"]["text"]), MAX_RESULT_CHARS)

    def test_a_result_that_is_not_ready_yet_is_not_an_error(self) -> None:
        self.upstream.task_payload = {
            "task": snapshot(state="completed", terminal=True)
        }
        self.upstream.raises["get_result"] = upstream_error("task_result_not_ready")
        body = self.get(f"/v1/tasks/{TASK_ID}").json()
        self.assertFalse(body["result"]["available"])

    def test_a_real_refusal_on_the_result_read_is_not_swallowed(self) -> None:
        self.upstream.task_payload = {
            "task": snapshot(state="completed", terminal=True)
        }
        self.upstream.raises["get_result"] = upstream_error("task_unknown")
        self.assertEqual(self.get(f"/v1/tasks/{TASK_ID}").status_code, 404)

    def test_the_failed_and_interrupted_states_are_reported_truthfully(self) -> None:
        for state in ("failed", "interrupted", "cancelled"):
            with self.subTest(state=state):
                self.upstream.task_payload = {
                    "task": snapshot(
                        state=state,
                        terminal=True,
                        failure={
                            "code": "task_adapter_error",
                            "summary": "the helper stopped",
                        },
                    )
                }
                self.upstream.result_payload = {
                    "result": result(text=None, outcome=state)
                }
                body = self.get(f"/v1/tasks/{TASK_ID}").json()
                self.assertEqual(body["state"], state)
                self.assertTrue(body["terminal"])
                self.assertEqual(body["failure_code"], "task_adapter_error")
                self.assertFalse(body["follow_up_available"])

    def test_artifacts_are_reported_as_unavailable_rather_than_empty(self) -> None:
        body = self.get(f"/v1/tasks/{TASK_ID}").json()
        self.assertFalse(body["artifacts_supported"])
        self.assertEqual(
            body["artifacts_unavailable_reason"], "no_task_owned_artifact_model"
        )
        # And there is no artifact route to call.
        self.assertEqual(
            self.get(f"/v1/tasks/{TASK_ID}/artifacts").status_code, 404
        )

    def test_a_malformed_task_id_is_a_404_before_any_upstream_call(self) -> None:
        for hostile in (
            "not-a-task",
            "task_short",
            "CF-A12F09",
            "../../../etc/passwd",
            "task_" + "z" * 26 + "extra",
            "task_" + "i" * 26,  # 'i' is not in the Crockford alphabet
        ):
            with self.subTest(task_id=hostile):
                self.assertEqual(self.get(f"/v1/tasks/{hostile}").status_code, 404)
        self.assertEqual(self.upstream.called("get_task"), 0)

    def test_a_display_reference_is_never_accepted_as_a_task_id(self) -> None:
        listed = self.get("/v1/tasks").json()["tasks"][0]
        self.assertEqual(
            self.get(f"/v1/tasks/{listed['display_ref']}").status_code, 404
        )


# -- 4. choice answers ----------------------------------------------------------


class ChoiceAnswerTests(BridgeTestCase):
    def answer(self, **overrides):
        body = {
            "question_id": QUESTION_ID,
            "option_id": "opt1",
            "client_request_id": self.key(),
        }
        body.update(overrides)
        return self.post(f"/v1/tasks/{TASK_ID}/answer", body)

    def test_one_exact_option_id_is_accepted(self) -> None:
        response = self.answer()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["accepted"])
        _, kwargs = self.upstream.calls[-1]
        self.assertEqual(kwargs["option_id"], "opt1")
        self.assertEqual(kwargs["question_id"], QUESTION_ID)

    def test_the_upstream_body_carries_no_free_text(self) -> None:
        """The bridge's internal client sends ``option_ids`` and nothing else.

        Asserted at the client, because that is the only place the wire body is
        built — and it has no ``answer`` key at all.
        """
        import inspect

        from cofferdam.actions_bridge import internal

        source = inspect.getsource(internal.InternalTaskClient.answer_clarification)
        self.assertIn('body={"option_ids": [option_id]}', source)
        self.assertNotIn('"answer"', source)

    def test_a_display_number_alone_is_refused(self) -> None:
        """The most likely model mistake, caught before it reaches Cofferdam.

        A display number can never be an option id — Cofferdam's ids begin with
        a letter — so the bridge can say what went wrong instead of forwarding
        it and relaying "that option is not offered".
        """
        for hostile in (1, 2, "1", "2", "", None, ["opt1"], "Opt1", "opt 1"):
            with self.subTest(option=repr(hostile)):
                response = self.answer(option_id=hostile)
                self.assertEqual(response.status_code, 422)
                self.assertIn(
                    "display number", response.json()["error"]["message"]
                )
        self.assertEqual(self.upstream.called("answer_clarification"), 0)

    def test_an_unknown_option_is_refused_by_cofferdam(self) -> None:
        self.upstream.raises["answer_clarification"] = upstream_error(
            "task_clarification_invalid"
        )
        response = self.answer(option_id="opt99")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")

    def test_multiple_options_cannot_be_expressed(self) -> None:
        for hostile in (
            {"option_ids": ["opt1", "opt2"]},
            {"option_id": ["opt1", "opt2"]},
            {"options": ["opt1"]},
        ):
            with self.subTest(body=hostile):
                response = self.answer(**hostile)
                self.assertEqual(response.status_code, 422)

    def test_free_text_alongside_a_choice_is_refused(self) -> None:
        for field in ("answer", "text", "note", "comment", "other", "custom_text"):
            with self.subTest(field=field):
                response = self.answer(**{field: "and also do this"})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.upstream.called("answer_clarification"), 0)

    def test_an_approval_shaped_payload_is_refused(self) -> None:
        for field in (
            "approval_id",
            "tool_name",
            "tool_input",
            "behavior",
            "decision",
            "allow",
            "deny",
            "permission_mode",
            "command",
            "path",
            "cwd",
            "argv",
            "env",
        ):
            with self.subTest(field=field):
                response = self.answer(**{field: "allow"})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.upstream.called("answer_clarification"), 0)

    def test_a_provider_session_id_cannot_be_supplied(self) -> None:
        for field in ("provider_session_id", "session_id", "provider_event_id"):
            with self.subTest(field=field):
                self.assertEqual(self.answer(**{field: "sess_x"}).status_code, 422)

    def test_a_stale_or_already_answered_question_is_refused_truthfully(self) -> None:
        self.upstream.raises["answer_clarification"] = upstream_error(
            "task_clarification_closed"
        )
        response = self.answer()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "not_allowed_now")

    def test_a_question_from_another_task_is_refused(self) -> None:
        self.upstream.raises["answer_clarification"] = upstream_error(
            "task_clarification_unknown"
        )
        self.assertEqual(self.answer().status_code, 404)

    def test_an_identical_retry_answers_once(self) -> None:
        request_id = self.key()
        first = self.answer(client_request_id=request_id)
        second = self.answer(client_request_id=request_id)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(first.json()["replayed"])
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(self.upstream.called("answer_clarification"), 1)

    def test_the_same_id_with_a_different_option_is_a_conflict(self) -> None:
        request_id = self.key()
        self.answer(client_request_id=request_id, option_id="opt1")
        response = self.answer(client_request_id=request_id, option_id="opt2")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "idempotency_conflict")
        self.assertEqual(self.upstream.called("answer_clarification"), 1)

    def test_an_answer_is_scoped_to_its_own_task(self) -> None:
        """The same request id on two tasks is two separate claims."""
        request_id = self.key()
        self.answer(client_request_id=request_id)
        other = self.post(
            f"/v1/tasks/{OTHER_TASK_ID}/answer",
            {
                "question_id": QUESTION_ID,
                "option_id": "opt1",
                "client_request_id": request_id,
            },
        )
        self.assertEqual(other.status_code, 200)
        self.assertFalse(other.json()["replayed"])
        self.assertEqual(self.upstream.called("answer_clarification"), 2)


# -- 5. follow-up ---------------------------------------------------------------


class FollowupTests(BridgeTestCase):
    def followup(self, **overrides):
        body = {
            "followup_text": "Go with option one and run the suite.",
            "client_request_id": self.key(),
        }
        body.update(overrides)
        return self.post(f"/v1/tasks/{TASK_ID}/followup", body)

    def test_a_legal_follow_up_is_delivered(self) -> None:
        response = self.followup()
        self.assertEqual(response.status_code, 200)
        _, kwargs = self.upstream.calls[-1]
        self.assertEqual(kwargs["followup"], "Go with option one and run the suite.")

    def test_nothing_is_wrapped_around_the_follow_up_text(self) -> None:
        self.followup(followup_text="Exactly this.")
        _, kwargs = self.upstream.calls[-1]
        self.assertEqual(kwargs["followup"], "Exactly this.")

    def test_an_open_question_refuses_a_follow_up(self) -> None:
        self.upstream.raises["send_followup"] = upstream_error(
            "task_clarification_pending"
        )
        response = self.followup()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "not_allowed_now")

    def test_a_dead_session_refuses_a_follow_up(self) -> None:
        for code in (
            "task_session_unavailable",
            "task_already_finished",
            "task_not_waiting_for_input",
            "task_followup_in_flight",
            "task_followup_unsupported",
            "task_turn_limit_reached",
        ):
            with self.subTest(code=code):
                self.upstream.raises["send_followup"] = upstream_error(code)
                response = self.followup()
                self.assertEqual(response.status_code, 409)
                self.assertEqual(
                    response.json()["error"]["code"], "not_allowed_now"
                )

    def test_an_oversized_follow_up_is_refused(self) -> None:
        response = self.followup(
            followup_text="x" * (MAX_FOLLOWUP_TEXT_CHARS + 1)
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.upstream.called("send_followup"), 0)

    def test_no_session_id_or_provider_config_can_be_sent(self) -> None:
        for field in (
            "provider_session_id",
            "session_id",
            "model",
            "tools",
            "permission_mode",
            "system_prompt",
            "prompt_prefix",
            "wrapper",
        ):
            with self.subTest(field=field):
                self.assertEqual(self.followup(**{field: "x"}).status_code, 422)
        self.assertEqual(self.upstream.called("send_followup"), 0)

    def test_a_duplicate_request_id_creates_one_turn(self) -> None:
        request_id = self.key()
        self.followup(client_request_id=request_id)
        second = self.followup(client_request_id=request_id)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(self.upstream.called("send_followup"), 1)

    def test_a_conflicting_reuse_returns_conflict(self) -> None:
        request_id = self.key()
        self.followup(client_request_id=request_id)
        response = self.followup(
            client_request_id=request_id, followup_text="A different instruction."
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "idempotency_conflict")

    def test_the_same_key_reaches_task_core_as_well(self) -> None:
        """Two independent guards on one retry, and the second is upstream."""
        request_id = self.key()
        self.followup(client_request_id=request_id)
        _, kwargs = self.upstream.calls[-1]
        self.assertEqual(kwargs["client_request_id"], request_id)


# -- 6. cancel and finish -------------------------------------------------------


class CancelFinishTests(BridgeTestCase):
    def test_cancel_and_finish_are_delivered(self) -> None:
        self.assertEqual(
            self.post(
                f"/v1/tasks/{TASK_ID}/cancel", {"client_request_id": self.key()}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.post(
                f"/v1/tasks/{TASK_ID}/finish", {"client_request_id": self.key()}
            ).status_code,
            200,
        )
        self.assertEqual(self.upstream.called("cancel_task"), 1)
        self.assertEqual(self.upstream.called("finish_task"), 1)

    def test_no_signal_pid_or_process_input_is_accepted(self) -> None:
        for field in ("signal", "pid", "pgid", "process_group", "kill_mode", "force"):
            with self.subTest(field=field):
                response = self.post(
                    f"/v1/tasks/{TASK_ID}/cancel",
                    {"client_request_id": self.key(), field: "9"},
                )
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.upstream.called("cancel_task"), 0)

    def test_the_cancel_reason_is_a_closed_enum(self) -> None:
        self.assertEqual(
            self.post(
                f"/v1/tasks/{TASK_ID}/cancel",
                {"client_request_id": self.key(), "reason": "wrong_project"},
            ).status_code,
            200,
        )
        response = self.post(
            f"/v1/tasks/{TASK_ID}/cancel",
            {"client_request_id": self.key(), "reason": "because I said so"},
        )
        self.assertEqual(response.status_code, 422)

    def test_a_repeated_cancel_is_truthful(self) -> None:
        request_id = self.key()
        self.post(f"/v1/tasks/{TASK_ID}/cancel", {"client_request_id": request_id})
        second = self.post(
            f"/v1/tasks/{TASK_ID}/cancel", {"client_request_id": request_id}
        )
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(self.upstream.called("cancel_task"), 1)

    def test_a_new_cancel_of_an_already_cancelled_task_reports_the_refusal(self) -> None:
        self.upstream.raises["cancel_task"] = upstream_error("task_already_finished")
        response = self.post(
            f"/v1/tasks/{TASK_ID}/cancel", {"client_request_id": self.key()}
        )
        self.assertEqual(response.status_code, 409)

    def test_finish_from_an_illegal_state_is_refused(self) -> None:
        self.upstream.raises["finish_task"] = upstream_error(
            "task_illegal_transition"
        )
        self.assertEqual(
            self.post(
                f"/v1/tasks/{TASK_ID}/finish", {"client_request_id": self.key()}
            ).status_code,
            409,
        )

    def test_another_task_is_untouched(self) -> None:
        self.post(f"/v1/tasks/{TASK_ID}/cancel", {"client_request_id": self.key()})
        cancelled = [
            kwargs for name, kwargs in self.upstream.calls if name == "cancel_task"
        ]
        self.assertEqual(cancelled, [{"task_id": TASK_ID}])


# -- 7. bounds and headers ------------------------------------------------------


class BoundsTests(BridgeTestCase):
    def test_an_oversized_body_is_refused(self) -> None:
        response = self.client.post(
            "/v1/tasks",
            headers={**self.headers, "Content-Type": "application/json"},
            content=b'{"task_text": "' + b"x" * 40000 + b'"}',
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "too_large")

    def test_a_lying_content_length_does_not_get_past_the_second_check(self) -> None:
        """The header is a claim; the read is the fact."""
        response = self.client.post(
            "/v1/tasks",
            headers={
                **self.headers,
                "Content-Type": "application/json",
                "Content-Length": "10",
            },
            content=b'{"task_text": "' + b"x" * 40000 + b'"}',
        )
        self.assertIn(response.status_code, (400, 413, 422))
        self.assertEqual(self.upstream.called("create_task"), 0)

    def test_a_non_json_mutation_body_is_refused(self) -> None:
        response = self.client.post(
            "/v1/tasks",
            headers={**self.headers, "Content-Type": "text/plain"},
            content=b"project_id=demo",
        )
        self.assertEqual(response.status_code, 415)

    def test_malformed_json_and_malformed_unicode_are_refused(self) -> None:
        for content in (b"{not json", b"[]", b'"a string"', b'{"a": \xff\xfe}'):
            with self.subTest(content=content[:20]):
                response = self.client.post(
                    "/v1/tasks",
                    headers={**self.headers, "Content-Type": "application/json"},
                    content=content,
                )
                self.assertEqual(response.status_code, 400)

    def test_every_response_is_no_store_and_nosniff(self) -> None:
        for response in (
            self.get("/v1/projects"),
            self.get("/v1/tasks"),
            self.get(f"/v1/tasks/{TASK_ID}"),
            self.client.get("/v1/health"),
            self.client.get("/v1/projects"),  # the 401
        ):
            with self.subTest(url=str(response.url)):
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(
                    response.headers["x-content-type-options"], "nosniff"
                )

    def test_there_is_no_permissive_cors_header(self) -> None:
        response = self.get("/v1/projects")
        for header in (
            "access-control-allow-origin",
            "access-control-allow-credentials",
            "access-control-allow-headers",
        ):
            self.assertNotIn(header, {k.lower() for k in response.headers})

    def test_an_upstream_timeout_is_a_bounded_504_that_says_to_sync(self) -> None:
        from cofferdam.actions_bridge.errors import (
            CODE_UPSTREAM_TIMEOUT,
            BridgeError,
        )

        self.upstream.raises["get_task"] = BridgeError(
            code=CODE_UPSTREAM_TIMEOUT,
            message=(
                "Cofferdam did not answer in time. The work may still be "
                "running — sync the task rather than sending it again."
            ),
            status_code=504,
        )
        response = self.get(f"/v1/tasks/{TASK_ID}")
        self.assertEqual(response.status_code, 504)
        self.assertIn("sync", response.json()["error"]["message"].lower())

    def test_a_timed_out_mutation_does_not_hold_its_request_id(self) -> None:
        from cofferdam.actions_bridge.errors import (
            CODE_UPSTREAM_TIMEOUT,
            BridgeError,
        )

        request_id = self.key()
        self.upstream.raises["create_task"] = BridgeError(
            code=CODE_UPSTREAM_TIMEOUT, message="timed out", status_code=504
        )
        self.assertEqual(self.create(client_request_id=request_id).status_code, 504)
        self.upstream.raises.clear()
        # The identical retry is a real attempt, not a conflict — and Task Core's
        # own idempotency on the same key is what stops a double create if the
        # first one did in fact land.
        self.assertEqual(self.create(client_request_id=request_id).status_code, 201)


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class RateLimitTests(unittest.TestCase):
    """The shipped limits, against a bridge built with its real defaults.

    Its own class because every other test in this file deliberately runs with
    the limiter turned up — see ``BridgeTestCase.setUp``. Asserting the real
    numbers needs an app that has them.
    """

    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.config = load_bridge_config(Path(self._home.name))
        self.upstream = FakeInternalClient()
        self.app = create_bridge_app(
            self.config, external_key=KEY, internal_client=self.upstream
        )
        self.addCleanup(self.app.state.idempotency.close)
        self.client = TestClient(self.app)
        self.headers = {"Authorization": "Bearer " + KEY}

    def test_the_general_limit_refuses_rather_than_queueing(self) -> None:
        statuses = [
            self.client.get("/v1/projects", headers=self.headers).status_code
            for _ in range(self.config.rate_limit_burst + 40)
        ]
        self.assertIn(429, statuses)
        refusal = self.client.get("/v1/projects", headers=self.headers)
        self.assertEqual(refusal.status_code, 429)
        self.assertEqual(refusal.json()["error"]["code"], "rate_limited")
        self.assertIn("retry-after", {k.lower() for k in refusal.headers})
        self.assertGreaterEqual(int(refusal.headers["retry-after"]), 1)

    def test_mutations_have_their_own_tighter_limit(self) -> None:
        """A caller can poll far more often than it can start work."""
        accepted = 0
        for index in range(self.config.mutation_rate_limit_burst + 10):
            response = self.client.post(
                "/v1/tasks",
                headers=self.headers,
                json={
                    "project_id": PROJECT_ID,
                    "task_text": "Do the thing.",
                    "client_request_id": f"rate-test-{index:04d}",
                },
            )
            if response.status_code in (200, 201):
                accepted += 1
        self.assertLessEqual(accepted, self.config.mutation_rate_limit_burst + 2)
        self.assertEqual(self.upstream.called("create_task"), accepted)

    def test_the_health_check_is_outside_both_buckets(self) -> None:
        """A tunnel probe must not be able to exhaust a real caller's budget."""
        for _ in range(self.config.rate_limit_per_minute * 3):
            self.assertEqual(self.client.get("/v1/health").status_code, 200)

    def test_an_unauthenticated_request_still_costs_a_token(self) -> None:
        """Otherwise the limiter is free to probe around."""
        statuses = {
            self.client.get("/v1/projects").status_code
            for _ in range(self.config.rate_limit_burst + 40)
        }
        self.assertIn(429, statuses)


# -- 8. leakage -----------------------------------------------------------------


class LeakageTests(BridgeTestCase):
    """One sweep: nothing forbidden appears in any response body."""

    FORBIDDEN = (
        "provider_session_id",
        "sess_should_never_be_published",
        "correlation_id",
        "tcor-",
        "lifecycle_revision",
        "event_cursor",
        "resource_summary",
        "/home/",
        "Traceback",
        "internal note",
        "prompt",
        "bearer",
        KEY,
    )

    def responses(self):
        self.upstream.task_payload = {
            "task": snapshot(state="waiting_for_user", waiting_reason="clarification")
        }
        yield self.get("/v1/projects")
        yield self.get("/v1/tasks")
        yield self.get(f"/v1/tasks/{TASK_ID}")
        yield self.create()
        yield self.post(
            f"/v1/tasks/{TASK_ID}/answer",
            {
                "question_id": QUESTION_ID,
                "option_id": "opt1",
                "client_request_id": self.key(),
            },
        )
        yield self.post(
            f"/v1/tasks/{TASK_ID}/followup",
            {"followup_text": "continue", "client_request_id": self.key()},
        )
        yield self.post(
            f"/v1/tasks/{TASK_ID}/cancel", {"client_request_id": self.key()}
        )
        yield self.post(
            f"/v1/tasks/{TASK_ID}/finish", {"client_request_id": self.key()}
        )
        yield self.client.get("/v1/health")

    def test_no_response_carries_a_forbidden_value(self) -> None:
        for response in self.responses():
            body = response.text
            for forbidden in self.FORBIDDEN:
                with self.subTest(url=str(response.url), forbidden=forbidden):
                    self.assertNotIn(forbidden, body)

    def test_no_stack_trace_reaches_the_caller(self) -> None:
        class Exploding:
            def __getattr__(self, name):
                def _boom(*_args, **_kwargs):
                    raise RuntimeError("a secret value 12345 in the message")

                return _boom

        app = create_bridge_app(
            self.config, external_key=KEY, internal_client=Exploding()
        )
        self.addCleanup(app.state.idempotency.close)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/v1/projects", headers=self.headers)
        self.assertEqual(response.status_code, 500)
        body = response.text
        self.assertNotIn("Traceback", body)
        self.assertNotIn("RuntimeError", body)
        self.assertNotIn("12345", body)
        self.assertEqual(response.json()["error"]["code"], "internal_error")
        self.assertIsNone(response.json()["error"]["detail"])

    def test_no_content_reaches_the_log(self) -> None:
        import logging

        from cofferdam.actions_bridge.observe import LOGGER_NAME

        with self.assertLogs(LOGGER_NAME, level=logging.INFO) as captured:
            self.create(task_text="a very secret instruction about acquisitions")
            self.get(f"/v1/tasks/{TASK_ID}")
            self.client.get("/v1/projects")  # a 401
        joined = "\n".join(captured.output)
        for forbidden in (
            "secret instruction",
            "acquisitions",
            KEY,
            TASK_ID,
            "Bearer",
            "Authorization",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, joined)
        # What IS there: the operation, a status and a display reference.
        self.assertIn("op=createTask", joined)
        self.assertIn("status=401", joined)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
