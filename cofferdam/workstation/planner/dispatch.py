"""The gate between a person's approval and a worker that can write code.

Where this sits
---------------

    planner result   →  what the model proposed        (PR1c)
    authority event  →  what the person authorized     (PR1d)
    dispatch         →  what Cofferdam handed a worker (here)
    task             →  what the worker did            (Task Core)

Four facts, four owners, and none of them written over another. This module owns
the third and the *conditions* under which it may exist.

The gate
--------

Every one of these is a hard refusal, checked in :func:`evaluate_dispatch`
before anything is created:

* the planner invocation succeeded;
* its action is ``PREPARE_WORKER_PROMPT``;
* the human gate is ``approved`` — not awaiting, not rejected, not answered;
* the approval still binds the current subject;
* the approved fingerprint equals a fingerprint recomputed **now** from the
  exact persisted prompt.

The last two look redundant and are not. ``binds_current_subject`` is derived by
the authority layer from the same values; recomputing here is an independent
arrival at the same number by the dispatch layer, which is what makes the check
survive a future refactor of either one. Cheap, and the thing it protects against
— running a prompt nobody approved — is not.

The identity that makes a retry safe
------------------------------------

``planner.sqlite3`` and ``tasks.sqlite3`` are different databases and there is no
transaction across them. So the sequence "check no dispatch exists → create task
→ record dispatch" has a crash window in the middle, and a crash there followed
by a retry must not start a second worker.

The fix is not a lock. It is that the Task Core request key is **derived** rather
than minted: :func:`dispatch_request_key` is a pure function of the planner
request id, the approved fingerprint and the worker kind, so a retry after a
crash computes the same key, Task Core's own idempotency table returns the task
it already created, and the linkage row is then written for that same task.
Nothing is launched twice because the second attempt never asks for a second
task.

That mechanism is Task Core's, not a new one. ``create_task`` has taken a
``client_request_id`` and returned ``(row, created)`` since M2F; this module
supplies a deterministic value where a phone supplies a random one.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .authority import (
    GATE_CONFIRMATION,
    GATE_STATE_APPROVED,
    HumanGate,
)
from .hashing import authority_subject_fingerprint
from .models import ACTION_PREPARE_WORKER_PROMPT, PLANNER_RESULT_SCHEMA_VERSION
from .store import STATUS_SUCCEEDED, PlannerRecord

DISPATCH_ID_PREFIX = "disp_"
_DISPATCH_ID_BYTES = 13

#: The worker kind this build dispatches to. One value, and the tuple is the
#: assertion: there is no Codex, no second Claude model, no fallback and no
#: routing decision, because a fallback is a second thing that can run a prompt
#: somebody approved for the first.
WORKER_KIND_CLAUDE_CODE = "claude_code_worker"
WORKER_KINDS: Tuple[str, ...] = (WORKER_KIND_CLAUDE_CODE,)

#: Domain tag for the derived Task Core request key. Its own tag, versioned, for
#: the reason every other hash in this package has one: the value is durable, and
#: changing what it covers must produce a different value rather than silently
#: colliding with the old shape.
TAG_DISPATCH_REQUEST = b"cofferdam.planner.dispatch.request.v1"

#: Why a dispatch was refused. A closed vocabulary, because "why did nothing
#: run" is asked later and free-form text answers it differently every time.
REFUSE_NO_REQUEST = "planner_request_unknown"
REFUSE_NOT_SUCCEEDED = "planner_invocation_did_not_succeed"
REFUSE_NOT_A_PROMPT = "planner_result_is_not_a_prepared_prompt"
REFUSE_NO_PROMPT = "prepared_prompt_missing"
REFUSE_SCHEMA = "result_schema_unsupported"
REFUSE_NOT_APPROVED = "not_approved"
REFUSE_REJECTED = "approval_rejected"
REFUSE_AWAITING = "awaiting_human_confirmation"
REFUSE_STALE = "approval_does_not_bind_current_prompt"
REFUSE_NO_PROJECT = "project_identity_missing"
REFUSE_PROJECT_UNRESOLVED = "project_unresolved"
REFUSE_PROJECT_INELIGIBLE = "project_not_eligible_for_development"

REFUSAL_REASONS: Tuple[str, ...] = (
    REFUSE_NO_REQUEST,
    REFUSE_NOT_SUCCEEDED,
    REFUSE_NOT_A_PROMPT,
    REFUSE_NO_PROMPT,
    REFUSE_SCHEMA,
    REFUSE_NOT_APPROVED,
    REFUSE_REJECTED,
    REFUSE_AWAITING,
    REFUSE_STALE,
    REFUSE_NO_PROJECT,
    REFUSE_PROJECT_UNRESOLVED,
    REFUSE_PROJECT_INELIGIBLE,
)


def new_dispatch_id() -> str:
    """Opaque and content-free. Takes no argument, so nothing can arrive in one."""
    return DISPATCH_ID_PREFIX + secrets.token_hex(_DISPATCH_ID_BYTES)


def worker_prompt_digest(prompt: str) -> str:
    """A plain digest of the exact prompt bytes, for the dispatch record.

    Distinct from the authority subject fingerprint, which binds the request id
    and action as well. This one answers a narrower question — *are these the
    same bytes* — and it is stored so a later reader can compare what was
    dispatched against what a repository or a log holds without needing to know
    the authority tag's field order.
    """
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def dispatch_request_key(
    *, planner_request_id: str, subject_fingerprint: str, worker_kind: str
) -> str:
    """The Task Core idempotency key for one approved step. **Derived, not minted.**

    This is the whole cross-database safety argument in one function. Because the
    key is a pure function of immutable approved authority, every attempt to
    dispatch the same approved prompt computes the same key — so Task Core
    returns the task it already made instead of making another, and a crash
    between task creation and linkage costs a retry rather than a second worker.

    Length-prefixed like every other digest in this package, so the three fields
    cannot alias. Bounded well under Task Core's 128-character limit for a
    request key.
    """
    if worker_kind not in WORKER_KINDS:
        raise ValueError("unknown worker kind: " + str(worker_kind))
    hasher = hashlib.sha256()
    hasher.update(TAG_DISPATCH_REQUEST)
    for field in (
        planner_request_id.encode("utf-8"),
        subject_fingerprint.encode("ascii"),
        worker_kind.encode("ascii"),
    ):
        hasher.update(len(field).to_bytes(8, "big"))
        hasher.update(field)
    return "plandisp-" + hasher.hexdigest()


@dataclass(frozen=True)
class DispatchDecision:
    """Whether this approved result may become a worker, and why not if not."""

    allowed: bool
    reason: Optional[str] = None
    detail: Optional[str] = None
    worker_prompt: Optional[str] = None
    subject_fingerprint: Optional[str] = None
    authority_event_id: Optional[str] = None
    project_id: Optional[str] = None
    workspace_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "detail": self.detail,
            "project_id": self.project_id,
            "approved_subject_fingerprint": self.subject_fingerprint,
        }


def evaluate_dispatch(record: PlannerRecord, gate: HumanGate) -> DispatchDecision:
    """The whole gate, as a pure function. No side effect, no I/O, no launch.

    Pure on purpose: the conditions under which Cofferdam may run a coding worker
    should be readable in one place, testable without a repository or a process,
    and impossible to satisfy accidentally by calling something that also starts
    things.
    """

    def refuse(reason: str, detail: Optional[str] = None) -> DispatchDecision:
        return DispatchDecision(allowed=False, reason=reason, detail=detail)

    if record.status != STATUS_SUCCEEDED:
        return refuse(REFUSE_NOT_SUCCEEDED, record.status)
    if record.action != ACTION_PREPARE_WORKER_PROMPT:
        return refuse(REFUSE_NOT_A_PROMPT, record.action)
    if record.result_schema_version != PLANNER_RESULT_SCHEMA_VERSION:
        return refuse(REFUSE_SCHEMA, str(record.result_schema_version))

    prompt = record.worker_prompt
    if not (prompt or "").strip():
        return refuse(REFUSE_NO_PROMPT)

    if gate.kind != GATE_CONFIRMATION:
        return refuse(REFUSE_NOT_APPROVED, gate.kind)
    if gate.event is None:
        return refuse(REFUSE_AWAITING, gate.state)
    if gate.state != GATE_STATE_APPROVED:
        # A rejection and a still-open gate are different sentences, and a
        # person reading this later deserves to know which one stopped the work.
        return refuse(
            REFUSE_REJECTED if gate.state == "rejected" else REFUSE_AWAITING, gate.state
        )
    if gate.binds_current_subject is not True:
        return refuse(REFUSE_STALE, gate.subject_fingerprint)

    # Recomputed here rather than taken from the gate. An independent arrival at
    # the same number by the layer that is about to *act* on it — so a future
    # change to how the authority layer derives its value cannot silently widen
    # what may be dispatched.
    recomputed = authority_subject_fingerprint(
        planner_request_id=record.planner_request_id,
        result_schema_version=record.result_schema_version,
        action=record.action,
        subject=prompt,
    )
    if recomputed != gate.event.subject_fingerprint:
        return refuse(REFUSE_STALE, recomputed)

    if not (record.project_id or "").strip():
        # The planner request never carried a project. There is nowhere to run
        # this, and guessing a project would be choosing which repository a
        # model's prompt edits.
        return refuse(REFUSE_NO_PROJECT)

    return DispatchDecision(
        allowed=True,
        worker_prompt=prompt,
        subject_fingerprint=gate.event.subject_fingerprint,
        authority_event_id=gate.event.authority_event_id,
        project_id=record.project_id,
        workspace_id=record.workspace_id,
    )


__all__ = [
    "DISPATCH_ID_PREFIX",
    "REFUSAL_REASONS",
    "REFUSE_AWAITING",
    "REFUSE_NOT_APPROVED",
    "REFUSE_NOT_A_PROMPT",
    "REFUSE_NOT_SUCCEEDED",
    "REFUSE_NO_PROJECT",
    "REFUSE_NO_PROMPT",
    "REFUSE_NO_REQUEST",
    "REFUSE_PROJECT_INELIGIBLE",
    "REFUSE_PROJECT_UNRESOLVED",
    "REFUSE_REJECTED",
    "REFUSE_SCHEMA",
    "REFUSE_STALE",
    "TAG_DISPATCH_REQUEST",
    "WORKER_KINDS",
    "WORKER_KIND_CLAUDE_CODE",
    "DispatchDecision",
    "dispatch_request_key",
    "evaluate_dispatch",
    "new_dispatch_id",
    "worker_prompt_digest",
]
