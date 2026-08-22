"""The three bounded reads a status surface needs beyond the overview.

Why these are separate calls
-----------------------------

:mod:`.view` deliberately carries neither the approved prompt nor the worker's
result: a status read happens on every poll and both are large. So the projection
publishes *handles* and these two functions turn a handle into the thing it
names — asked for explicitly, and only when somebody wants to look.

The project is part of the address, not a filter
--------------------------------------------------

Every function here takes ``project_id`` **and** the handle, and refuses when the
handle does not belong to that project. That shape is the isolation property:
there is no call that says "give me prompt X" without also saying which project
it is supposed to be in, so a handle belonging to another project is a
:class:`NotFound` rather than a successful read of somebody else's work.

The refusal is deliberately **not found** rather than *forbidden*. A caller that
can tell "that id exists but is not yours" apart from "no such id" can enumerate
other projects' work by watching which error comes back.

Reading a question is not answering one
----------------------------------------

:func:`question` returns the exact text a planner asked. It is a *read*: it
writes nothing, records no authority, and there is no function in this module
that takes an answer. That separation is deliberate and load-bearing — a
reconnecting client has to be able to show somebody the question they are being
asked without that act being, or being mistaken for, a reply to it.

Never "the latest"
------------------

All three reads resolve a durable id and stop. None falls back to the most
recent anything — a fallback would mean a stale handle silently returning a
*different* operation's prompt, which is the exact failure that makes a remote
surface untrustworthy.

Machine facts stay separable
-----------------------------

:func:`result` returns Cofferdam's own observations and the model's claims in
two labelled blocks, exactly as :mod:`.view` does, and it never derives one from
the other. A commit id, a check exit status and a pull request number are things
Cofferdam recorded; "the tests pass" is something a model wrote.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

#: Bounds. A remote client should be able to render these without paging, and a
#: prompt that has grown past this is a prompt worth reading on the workstation.
MAX_PROMPT_CHARS = 20000
MAX_RESULT_CHARS = 8000
#: A question is already bounded to 2000 characters by the planner result
#: contract, so this never clips a valid one. It is here so that a row written by
#: some future contract with a wider bound cannot make this read unbounded.
MAX_QUESTION_CHARS = 2000


class OperationsNotFound(Exception):
    """No such thing, for this project. Also what a foreign handle produces.

    One exception for both cases on purpose — see the module docstring on why
    distinguishing them would be an enumeration oracle.
    """

    def __init__(self, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.detail = detail

    def to_dict(self) -> Dict[str, Any]:
        return {"code": "operations_not_found", "message": str(self), "detail": self.detail}


@dataclass(frozen=True)
class PromptRead:
    """The exact text that was approved, and what binds it.

    ``verified`` is the load-bearing field. The prompt is re-checked against the
    digest recorded when it was dispatched, so this can answer *"was the text
    Cofferdam sent the text you approved"* rather than merely *"here is what is
    in the database now"*.
    """

    project_id: str
    planner_request_id: str
    dispatch_id: Optional[str]
    prompt: str
    truncated: bool
    verified: bool
    approved_fingerprint: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "planner_request_id": self.planner_request_id,
            "dispatch_id": self.dispatch_id,
            "prompt": self.prompt,
            "truncated": self.truncated,
            # False means the stored text no longer matches the digest recorded
            # at dispatch. The honest answer to "what did you send" is never
            # "here is what is there now", so a client must show this.
            "matches_dispatched_digest": self.verified,
            "approved_subject_fingerprint": self.approved_fingerprint,
        }


@dataclass(frozen=True)
class QuestionRead:
    """The exact question a planner asked, and whether it is still open.

    ``answered`` is read from the durable authority record, not from the planner
    row: what the model asked and what a person decided are two facts, kept in
    two tables precisely so that neither overwrites the other, and this read
    reports both rather than collapsing them into one.

    There is no field here for an answer to arrive in. That is the shape of the
    boundary rather than a rule about it — see the module docstring.
    """

    project_id: str
    planner_request_id: str
    question: str
    truncated: bool
    #: The model's own words about why it asked. Labelled, because it is a claim.
    planner_summary: Optional[str]
    answered: bool
    #: What the recorded answer bound itself to, once there is one. ``None``
    #: while the question is open, which is the honest value: a fingerprint is a
    #: property of a decision, and no decision has been made yet. Published for
    #: the same reason ``approved_subject_fingerprint`` is — so a reader can see
    #: that an answer committed to this exact text — and never so that a client
    #: can construct one.
    answered_subject_fingerprint: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "planner_request_id": self.planner_request_id,
            "question": self.question,
            "truncated": self.truncated,
            "planner_summary": self.planner_summary,
            "answered": self.answered,
            "answered_subject_fingerprint": self.answered_subject_fingerprint,
            # Restated in the payload most likely to be mistaken for a prompt to
            # reply through. There is no remote answer path in this build.
            "answering_requires_the_workstation": True,
            "source": "model_authored",
        }


@dataclass(frozen=True)
class ResultRead:
    """What Cofferdam observed, beside what the model said. Never merged."""

    project_id: str
    dispatch_id: str
    task_id: str
    machine: Dict[str, Any]
    claims: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "dispatch_id": self.dispatch_id,
            "task_id": self.task_id,
            "machine": self.machine,
            "claims": self.claims,
            # Restated in the payload a client is most likely to read as a
            # verdict. A finished worker is a finished process, and a check's
            # exit status is one command's exit status.
            "worker_completion_is_not_acceptance": True,
        }


def prompt(store, *, project_id: str, planner_request_id: str) -> PromptRead:
    """The exact approved prompt for one request, if it belongs to this project."""
    record = _require_request(store, project_id, planner_request_id)
    if not getattr(record, "worker_prompt", None):
        raise OperationsNotFound(
            "that request has no worker prompt", detail=planner_request_id
        )

    dispatch = store.dispatch(planner_request_id)
    text = str(record.worker_prompt)
    truncated = len(text) > MAX_PROMPT_CHARS

    verified = False
    if dispatch is not None:
        from ..planner.dispatch import worker_prompt_digest

        # Verified against the **untruncated** text: the digest was taken over
        # what was sent, and checking a clipped copy would always fail.
        verified = worker_prompt_digest(text) == dispatch.worker_prompt_sha256

    authority = store.authority_event(planner_request_id)
    return PromptRead(
        project_id=project_id,
        planner_request_id=planner_request_id,
        dispatch_id=getattr(dispatch, "dispatch_id", None),
        prompt=text[:MAX_PROMPT_CHARS],
        truncated=truncated,
        verified=verified,
        approved_fingerprint=getattr(authority, "subject_fingerprint", None),
    )


def question(store, *, project_id: str, planner_request_id: str) -> QuestionRead:
    """The exact pending question for one request, if it belongs to this project.

    Needed because the overview says *that* a question is open and never says
    what it is — the claims block carries a clipped planner summary, which is the
    model's account of its own reasoning and not the sentence somebody has to
    answer. A conversation that reconnects tomorrow could previously learn only
    that Cofferdam was waiting on it.

    Refuses a request that asked nothing, rather than returning an empty string:
    "there is no question here" and "the question is blank" are different facts,
    and a client that cannot tell them apart will render the second as the first.
    """
    record = _require_request(store, project_id, planner_request_id)
    text = getattr(record, "user_question", None)
    if not (text or "").strip():
        raise OperationsNotFound(
            "that request has no question", detail=planner_request_id
        )

    text = str(text)
    authority = store.authority_event(planner_request_id)
    return QuestionRead(
        project_id=project_id,
        planner_request_id=planner_request_id,
        question=text[:MAX_QUESTION_CHARS],
        truncated=len(text) > MAX_QUESTION_CHARS,
        planner_summary=_clip(getattr(record, "summary", None), 1000),
        # Any authority event on a question request is an answer: `derive_gate`
        # admits only the answer gate for an ASK_USER row, and the table's own
        # CHECK constraints admit only one event per request.
        answered=authority is not None,
        answered_subject_fingerprint=getattr(
            authority, "subject_fingerprint", None
        ),
    )


def result(store, tasks, *, project_id: str, dispatch_id: str) -> ResultRead:
    """Machine observations and model claims for one dispatch, project-scoped."""
    dispatch = _require_dispatch(store, project_id, dispatch_id)

    task = None
    for name in ("get_task", "get"):
        getter = getattr(tasks, name, None)
        if getter is None:
            continue
        try:
            task = getter(dispatch.task_id)
            break
        except Exception:  # pragma: no cover - defensive
            task = None

    reconciliation = store.reconciliation(dispatch_id)
    publication = store.publication(dispatch_id)
    record = store.get(dispatch.planner_request_id)

    machine: Dict[str, Any] = {
        "branch": dispatch.branch,
        "worker_state": getattr(task, "state", None),
        "worker_started_at": getattr(task, "started_at", None),
        "worker_completed_at": getattr(task, "completed_at", None),
        "commit": None,
        "checks": None,
        "publication": None,
        "observed_by": "cofferdam",
    }

    if reconciliation is not None:
        machine["commit"] = reconciliation.recovered_commit
        machine["checks"] = {
            "observed": bool(reconciliation.checks_observed),
            "exit_zero": (
                None
                if reconciliation.check_exit_zero is None
                else bool(reconciliation.check_exit_zero)
            ),
        }
        machine["restart"] = {
            "occurred": True,
            "outcome": reconciliation.outcome,
            "work_preserved": bool(reconciliation.worktree_retained),
        }
    else:
        machine["restart"] = {"occurred": False}

    if publication is not None:
        # The publication's commit is the authority when both exist: it is the
        # commit that was actually published, recorded at the moment it was.
        machine["commit"] = publication.commit_sha or machine["commit"]
        machine["publication"] = {
            "state": publication.state,
            "repository": publication.repository,
            "branch": publication.branch,
            "base_branch": publication.base_branch,
            "pull_request": (
                None
                if publication.pull_request_number is None
                else {
                    "number": publication.pull_request_number,
                    "url": publication.pull_request_url,
                    "state": publication.pull_request_state,
                }
            ),
            "failure": publication.failure_reason,
        }

    return ResultRead(
        project_id=project_id,
        dispatch_id=dispatch_id,
        task_id=dispatch.task_id,
        machine=machine,
        claims={
            "planner_summary": _clip(getattr(record, "summary", None), 1000),
            "worker_report": _clip(getattr(task, "final_result", None), MAX_RESULT_CHARS),
            "source": "model_authored",
        },
    )


def _require_request(store, project_id: str, planner_request_id: str):
    record = store.get(planner_request_id)
    if record is None or getattr(record, "project_id", None) != project_id:
        # Same refusal for "no such request" and "belongs to another project".
        raise OperationsNotFound(
            "no such operation for this project", detail=planner_request_id
        )
    return record


def _require_dispatch(store, project_id: str, dispatch_id: str):
    for candidate in store.recent_dispatches(limit=200):
        if candidate.dispatch_id != dispatch_id:
            continue
        if candidate.project_id != project_id:
            break
        return candidate
    raise OperationsNotFound(
        "no such operation for this project", detail=dispatch_id
    )


def _clip(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = [
    "MAX_PROMPT_CHARS",
    "MAX_QUESTION_CHARS",
    "MAX_RESULT_CHARS",
    "ResultRead",
    "OperationsNotFound",
    "PromptRead",
    "QuestionRead",
    "question",
    "result",
    "prompt",
]
