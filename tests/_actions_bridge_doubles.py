"""Test doubles for the Actions bridge.

:class:`FakeInternalClient` stands in for the real internal client. It has the
**same ten methods and no others**, which is the property that matters: a test
that passes against it is a test against the same call surface production uses,
and a bridge route that reached for an eleventh operation would fail here with
``AttributeError`` rather than quietly working.

It also records every call, so a test can assert what the bridge did *not* do —
that syncing a running task never asks for a result, that create never sends an
``adapter_id``, that a refused request reached no upstream call at all.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from cofferdam.actions_bridge.errors import (
    BridgeError,
    from_upstream_code,
    status_for,
)

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
) -> Dict[str, Any]:
    """A project shaped like ``TaskProject.to_dict``, notes and all."""
    return {
        "project_id": project_id,
        "display_name": "Demo Project",
        "enabled": enabled,
        "remote_control_enabled": True,
        "adapters": ["claude-agent-sdk"] if adapters is None else adapters,
        "notes": "internal note: lives under /home/someone/private/demo",
    }


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
    """The ten operations, scripted. Records every call it receives."""

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

    # -- the ten -------------------------------------------------------------

    def list_projects(self) -> Dict[str, Any]:
        self._record("list_projects")
        return copy.deepcopy(self.projects_payload)

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
