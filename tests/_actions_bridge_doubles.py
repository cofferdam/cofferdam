"""Test doubles for the Actions bridge.

:class:`FakeInternalClient` stands in for the real internal client. It has the
**same methods and no others**, which is the property that matters: a test that
passes against it is a test against the same call surface production uses, and a
bridge route that reached for an operation the real client does not have would
fail here with ``AttributeError`` rather than quietly working.

It also records every call, so a test can assert what the bridge did *not* do —
that syncing a running task never asks for a result, that create never sends an
``adapter_id``, that a refused request reached no upstream call at all.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cofferdam.actions_bridge.errors import (
    BridgeError,
    from_upstream_code,
    status_for,
)
# The bridge imports no workstation code, and this double is not the bridge. It
# borrows the real projection so that "what the daemon publishes" has one
# definition here too — a hand-written delegation status in a double is a test
# that would keep passing after the real resolver changed.
from cofferdam.workstation.tasks.projects import TaskProject

#: Distinguishes "the caller said nothing" from "the caller said ``None``". A
#: plain ``None`` default cannot: ``delegated_adapter=None`` is exactly how a
#: test asks for the ambiguous case.
_UNSET = object()

TASK_ID = "task_01k0000000000000000000000a"
OTHER_TASK_ID = "task_01k0000000000000000000000b"
QUESTION_ID = "q_" + "ab12cd34ef56" * 2
PROJECT_ID = "demo-project"


def snapshot(
    *,
    task_id: str = TASK_ID,
    state: str = "running",
    waiting_reason: Optional[str] = None,
    terminal: bool = False,
    title: Optional[str] = "A demo task",
    followup: bool = True,
    cancel: bool = True,
    latest_activity: Optional[str] = "reading the tests",
    failure: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """A task snapshot shaped exactly like ``TaskSnapshot.to_dict``.

    Including the fields the bridge must *not* publish — ``correlation_id``,
    ``lifecycle_revision``, ``event_cursor``, ``resource_summary``,
    ``adapter_id`` — so a leak test has something real to catch.
    """
    return {
        "version": 1,
        "task_id": task_id,
        "correlation_id": "tcor-deadbeefdeadbeef",
        "parent_task_id": None,
        "origin": "chatgpt_app",
        "adapter_id": "claude-agent-sdk",
        "adapter_display_name": "Claude Agent SDK",
        "project_id": PROJECT_ID,
        "project_display_name": "Demo Project",
        "state": state,
        "bucket": "active",
        "terminal": terminal,
        "waiting_reason": waiting_reason,
        "lifecycle_revision": 4,
        "created_at": "2026-08-09T10:00:00Z",
        "started_at": "2026-08-09T10:00:01Z",
        "updated_at": "2026-08-09T10:05:00Z",
        "completed_at": None,
        "title": title,
        "latest_activity": latest_activity,
        "failure": failure,
        "cancellation": None,
        "adapter_capabilities": {
            "start": True,
            "followup": followup,
            "cancel": cancel,
            "recover_after_restart": False,
            "structured_progress": True,
            "final_result": True,
            "approvals": False,
            "authentication_waits": False,
            "clarifications": True,
        },
        "event_cursor": 12,
        "resource_summary": {"evidence_reported": 0},
        "limitations": ["a limitation sentence"],
    }


def question(
    *,
    question_id: str = QUESTION_ID,
    answer_mode: str = "single_choice",
    options: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """A pending clarification shaped like ``PendingClarification.to_dict``."""
    if options is None:
        options = [
            {
                "label": "In the existing test file",
                "value": "provider-value-alpha",
                "option_id": "opt1",
                "description": "Keeps related cases together",
            },
            {
                "label": "In a new file",
                "value": "provider-value-beta",
                "option_id": "opt2",
                "description": None,
            },
        ]
    return {
        "version": 1,
        "category": "clarification",
        "question_id": question_id,
        "task_id": TASK_ID,
        "provider": "claude",
        "question": "Where should the new test live?",
        "answer_mode": answer_mode,
        "allows_free_text": answer_mode in ("free_text", "unknown"),
        "schema_verified": True,
        "options": options,
        "requested_at": "2026-08-09T10:04:00Z",
        "status": "pending",
        "answered_at": None,
    }


def result(
    *,
    text: Optional[str] = "Added the regression test. 41 tests pass.",
    outcome: str = "completed",
    terminal: bool = True,
    follow_up: bool = False,
) -> Dict[str, Any]:
    """A result shaped like ``TaskResult.to_dict`` — session id included."""
    return {
        "version": 1,
        "task_id": TASK_ID,
        "task_state": outcome,
        "task_terminal": terminal,
        "outcome": outcome,
        "succeeded": outcome == "completed",
        "completed_at": "2026-08-09T10:06:00Z",
        "provider": "claude",
        # Present upstream, and must never appear in a bridge response.
        "provider_session_id": "sess_should_never_be_published_0001",
        "turn_number": 1,
        "provider_turn_sequence": 1,
        "turn_count": 1,
        "result": text,
        "failure_code": None,
        "failure_summary": None,
        "follow_up_available": follow_up,
        "evidence_source": "adapter_reported",
        "result_meaning": "The latest completed turn's result.",
    }


def project(
    *,
    project_id: str = PROJECT_ID,
    enabled: bool = True,
    adapters: Optional[List[str]] = None,
    delegated_adapter: Any = _UNSET,
    delegation: Any = _UNSET,
) -> Dict[str, Any]:
    """A project shaped like ``TaskProject.to_dict``, notes and all.

    ``delegated_adapter`` and ``delegation`` default to what the real
    ``TaskProject.delegation`` would answer for the given adapter list, so a
    double cannot quietly describe a project the workstation could never
    produce. Pass either explicitly — including ``None`` — to build the
    ambiguous and unavailable cases a test needs.
    """
    names = ["claude-agent-sdk"] if adapters is None else list(adapters)
    if delegated_adapter is _UNSET or delegation is _UNSET:
        resolved, status = TaskProject(
            project_id=project_id,
            display_name="Demo Project",
            root=Path("/nonexistent"),
            adapters=tuple(names),
            delegated_adapter=None if delegated_adapter is _UNSET else delegated_adapter,
        ).delegation()
        if delegated_adapter is _UNSET:
            delegated_adapter = resolved
        if delegation is _UNSET:
            delegation = status
    return {
        "project_id": project_id,
        "display_name": "Demo Project",
        "enabled": enabled,
        "remote_control_enabled": True,
        "adapters": names,
        "delegated_adapter": delegated_adapter,
        "delegation": delegation,
        "notes": "internal note: lives under /home/someone/private/demo",
    }


PLANNER_REQUEST_ID = "plan_01m0k000000000000000000000"
DISPATCH_ID = "dsp_01m0k111111111111111111111"
WORKER_PROMPT = "Implement subtract() in calc.py.\n"
WORKER_CLAIM = "I added subtract() and every test passes."
#: Things that must never appear in a bridge response. Planted upstream so the
#: leakage sweep has something real to fail on.
HOST_PATH = "/home/nrgis/cofferdam/state/worktrees/alpha"
FAKE_TOKEN = "github_pat_11FAKEDOUBLE_DoNotExfiltrate0123456789"


def operations_entry(**overrides: Any) -> Dict[str, Any]:
    """One project's operational state, in the shape the workstation publishes."""
    payload: Dict[str, Any] = {
        "project_id": PROJECT_ID,
        "display_name": "Alpha",
        "phase": "pr_ready",
        "sentence": "A pull request is open and ready for your review.",
        "needs_person": True,
        "busy": False,
        "settled": True,
        "rank": 8,
        # Internal debugging aid. Must not be published outward.
        "because": "publication.state=published",
        "handles": {
            "planner_request_id": PLANNER_REQUEST_ID,
            "dispatch_id": DISPATCH_ID,
            "task_id": TASK_ID,
            "publication_id": "pub_01m0k2222222222222222222",
            "prompt_available": True,
        },
        "machine": {
            "planner_status": "succeeded",
            "planner_action": "PREPARE_WORKER_PROMPT",
            "approved": True,
            "worker_state": "completed",
            "restart": {"occurred": False},
            "worker_completion_is_not_acceptance": True,
            "publication": {
                "state": "published",
                "repository": "cofferdam/publisher-smoke",
                "branch": "cofferdam/worker/" + TASK_ID,
                "base_branch": "main",
                "commit": "a" * 40,
                "pull_request": {
                    "number": 5,
                    "url": "https://github.com/cofferdam/publisher-smoke/pull/5",
                    "state": "open",
                },
                "failure": None,
            },
        },
        "claims": {
            "planner_summary": "add subtract()",
            "worker_report": WORKER_CLAIM,
            "source": "model_authored",
        },
        "available_actions": ["open_pull_request", "inspect_result"],
    }
    payload.update(overrides)
    return payload


def operation_prompt(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "project_id": PROJECT_ID,
        "planner_request_id": PLANNER_REQUEST_ID,
        "dispatch_id": DISPATCH_ID,
        "prompt": WORKER_PROMPT,
        "truncated": False,
        "matches_dispatched_digest": True,
        "approved_subject_fingerprint": "f" * 64,
    }
    payload.update(overrides)
    return payload


def operation_question(**overrides: Any) -> Dict[str, Any]:
    """A pending planner question, in the shape the workstation publishes."""
    payload: Dict[str, Any] = {
        "project_id": PROJECT_ID,
        "planner_request_id": PLANNER_REQUEST_ID,
        "question": (
            "Should the new endpoint reuse the existing bearer boundary, or "
            "carry its own credential?"
        ),
        "truncated": False,
        "planner_summary": "one scoping question before writing a prompt",
        "answered": False,
        "answered_subject_fingerprint": None,
        "answering_requires_the_workstation": True,
        "source": "model_authored",
    }
    payload.update(overrides)
    return payload


def development_request(**overrides: Any) -> Dict[str, Any]:
    """What the workstation returns from `POST /api/development-requests`.

    The operational projection with three extra fields, exactly as the ingress
    assembles it. Defaults to the case worth defaulting to: a prepared prompt
    waiting for a person, which is the shape a client is most likely to
    misreport as "it is running".
    """
    payload: Dict[str, Any] = dict(
        operations_entry(
            phase="awaiting_approval",
            sentence=(
                "A development step is prepared and waiting for your approval."
            ),
            needs_person=True,
            busy=False,
            settled=False,
            rank=4,
            available_actions=[
                "approve", "reject", "inspect_prompt", "inspect_result",
            ],
        )
    )
    payload["handles"] = {
        "planner_request_id": PLANNER_REQUEST_ID,
        "dispatch_id": None,
        "task_id": None,
        "publication_id": None,
        "prompt_available": True,
    }
    payload["machine"] = {
        "planner_status": "succeeded",
        "planner_action": "PREPARE_WORKER_PROMPT",
        "approved": False,
        "worker_state": None,
        "restart": {"occurred": False},
        "worker_completion_is_not_acceptance": True,
        "publication": None,
    }
    payload["claims"] = {
        "planner_summary": "add the read surface, then stop for approval",
        "worker_report": None,
        "source": "model_authored",
    }
    payload.update(
        {
            "planner_request_id": PLANNER_REQUEST_ID,
            "replayed": False,
            "planner_action": "PREPARE_WORKER_PROMPT",
            "planner_status": "succeeded",
            "planner_failure_code": None,
            "authority": {
                "approved": False,
                "dispatched": False,
                "executed": False,
                "note": (
                    "Cofferdam planned this step. Nothing has been approved, "
                    "dispatched or executed."
                ),
            },
            "_status": 201,
        }
    )
    payload.update(overrides)
    return payload


def operation_result(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "project_id": PROJECT_ID,
        "dispatch_id": DISPATCH_ID,
        "task_id": TASK_ID,
        "machine": {
            "branch": "cofferdam/worker/" + TASK_ID,
            "commit": "a" * 40,
            "worker_state": "completed",
            "checks": {"observed": True, "exit_zero": True},
            "restart": {"occurred": False},
            "publication": {
                "state": "published",
                "repository": "cofferdam/publisher-smoke",
                "branch": "cofferdam/worker/" + TASK_ID,
                "base_branch": "main",
                "pull_request": {
                    "number": 5,
                    "url": "https://github.com/cofferdam/publisher-smoke/pull/5",
                    "state": "open",
                },
                "failure": None,
            },
            "observed_by": "cofferdam",
        },
        "claims": {
            "planner_summary": "add subtract()",
            "worker_report": WORKER_CLAIM,
            "source": "model_authored",
        },
        "worker_completion_is_not_acceptance": True,
    }
    payload.update(overrides)
    return payload


def upstream_error(code: str, message: str = "refused") -> BridgeError:
    """The refusal the real client would raise for an upstream error code."""
    bridge_code = from_upstream_code(code)
    return BridgeError(
        code=bridge_code,
        message=message,
        status_code=status_for(bridge_code),
        detail=code,
    )


class FakeInternalClient:
    """Every internal operation, scripted. Records each call it receives."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self.projects_payload: Dict[str, Any] = {
            "projects": [project()],
            "configured": 1,
            "problems": [],
            "source_present": True,
        }
        self.tasks_payload: Dict[str, Any] = {"version": 1, "tasks": [snapshot()]}
        self.task_payload: Dict[str, Any] = {"task": snapshot()}
        self.result_payload: Dict[str, Any] = {"result": result()}
        self.clarifications_payload: Dict[str, Any] = {
            "version": 1,
            "task_id": TASK_ID,
            "state": "waiting_for_user",
            "waiting_reason": "clarification",
            "clarifications": [question()],
        }
        self.create_payload: Dict[str, Any] = {
            "task": snapshot(state="running"),
            "created": True,
            "_status": 201,
        }
        self.operations_payload: Dict[str, Any] = {
            "projects": [operations_entry()],
            "attention": [],
            "count": 1,
        }
        self.project_operations_payload: Dict[str, Any] = operations_entry()
        self.operation_prompt_payload: Dict[str, Any] = operation_prompt()
        self.operation_result_payload: Dict[str, Any] = operation_result()
        self.operation_question_payload: Dict[str, Any] = operation_question()
        self.development_request_payload: Dict[str, Any] = development_request()
        #: Set to a ``BridgeError`` to make the next call of that name raise.
        self.raises: Dict[str, BridgeError] = {}
        #: Counts of how many times each operation actually ran.
        self.counts: Dict[str, int] = {}

    def _record(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))
        self.counts[name] = self.counts.get(name, 0) + 1
        failure = self.raises.get(name)
        if failure is not None:
            raise failure

    def called(self, name: str) -> int:
        return self.counts.get(name, 0)

    # -- the operations -------------------------------------------------------------

    def list_projects(self) -> Dict[str, Any]:
        self._record("list_projects")
        return copy.deepcopy(self.projects_payload)

    # -- remote operations, read-only (M2M PR2) ------------------------------

    def read_operations(self) -> Dict[str, Any]:
        self._record("read_operations")
        return copy.deepcopy(self.operations_payload)

    def read_project_operations(self, project_id: str) -> Dict[str, Any]:
        self._record("read_project_operations", project_id=project_id)
        return copy.deepcopy(self.project_operations_payload)

    def read_operation_prompt(
        self, project_id: str, planner_request_id: str
    ) -> Dict[str, Any]:
        self._record(
            "read_operation_prompt",
            project_id=project_id,
            planner_request_id=planner_request_id,
        )
        return copy.deepcopy(self.operation_prompt_payload)

    def read_operation_result(
        self, project_id: str, dispatch_id: str
    ) -> Dict[str, Any]:
        self._record(
            "read_operation_result",
            project_id=project_id,
            dispatch_id=dispatch_id,
        )
        return copy.deepcopy(self.operation_result_payload)

    def read_operation_question(
        self, project_id: str, planner_request_id: str
    ) -> Dict[str, Any]:
        self._record(
            "read_operation_question",
            project_id=project_id,
            planner_request_id=planner_request_id,
        )
        return copy.deepcopy(self.operation_question_payload)

    # -- remote development requests, planner-only (M2M PR4) -----------------

    def create_development_request(
        self,
        *,
        project_id: str,
        instruction: str,
        client_request_id: str,
        research_notes: Any,
    ) -> Dict[str, Any]:
        self._record(
            "create_development_request",
            project_id=project_id,
            instruction=instruction,
            client_request_id=client_request_id,
            research_notes=research_notes,
        )
        return copy.deepcopy(self.development_request_payload)

    def list_tasks(self, *, limit: int) -> Dict[str, Any]:
        self._record("list_tasks", limit=limit)
        return copy.deepcopy(self.tasks_payload)

    def create_task(
        self,
        *,
        project_id: str,
        adapter_id: Optional[str],
        prompt: str,
        client_request_id: str,
        title: Optional[str],
    ) -> Dict[str, Any]:
        self._record(
            "create_task",
            project_id=project_id,
            adapter_id=adapter_id,
            prompt=prompt,
            client_request_id=client_request_id,
            title=title,
        )
        return copy.deepcopy(self.create_payload)

    def get_task(self, task_id: str) -> Dict[str, Any]:
        self._record("get_task", task_id=task_id)
        return copy.deepcopy(self.task_payload)

    def get_result(self, task_id: str) -> Dict[str, Any]:
        self._record("get_result", task_id=task_id)
        return copy.deepcopy(self.result_payload)

    def list_clarifications(self, task_id: str) -> Dict[str, Any]:
        self._record("list_clarifications", task_id=task_id)
        return copy.deepcopy(self.clarifications_payload)

    def answer_clarification(
        self, *, task_id: str, question_id: str, option_id: str
    ) -> Dict[str, Any]:
        self._record(
            "answer_clarification",
            task_id=task_id,
            question_id=question_id,
            option_id=option_id,
        )
        return copy.deepcopy(self.task_payload)

    def send_followup(
        self, *, task_id: str, followup: str, client_request_id: str
    ) -> Dict[str, Any]:
        self._record(
            "send_followup",
            task_id=task_id,
            followup=followup,
            client_request_id=client_request_id,
        )
        return copy.deepcopy(self.task_payload)

    def cancel_task(self, task_id: str) -> Dict[str, Any]:
        self._record("cancel_task", task_id=task_id)
        return copy.deepcopy(self.task_payload)

    def finish_task(self, task_id: str) -> Dict[str, Any]:
        self._record("finish_task", task_id=task_id)
        return copy.deepcopy(self.task_payload)
