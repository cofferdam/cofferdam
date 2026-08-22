"""Cofferdam's payloads in, the bridge's payloads out. An allowlist both ways.

This is the file that decides what a model provider gets to see, and it is
written as **construction, not filtering**. Every function below builds a fresh
dictionary from named keys. None of them copies an upstream payload and deletes
fields from it, because a delete-list silently stops covering a key the day
somebody upstream adds one — and the key that gets added is never the harmless
one.

What never crosses this boundary, by never being read
-----------------------------------------------------

``provider_session_id`` · the task prompt · raw task events · the event cursor ·
``correlation_id`` · ``lifecycle_revision`` · ``provider_event_id`` ·
``provider_sequence`` · ``resource_summary`` · any filesystem path · any
adapter internals · any transcript, reasoning, tool input, command or
environment. Several of those are not in the upstream payloads at all — Task
Core already refuses to publish them — and the ones that are (``prompt``,
``provider_session_id``, ``correlation_id``) are simply never named here.

The truncation rule
-------------------

Text coming *out* of Cofferdam is truncated rather than refused, and the payload
says so. Nobody can retype an agent's result, so cutting it and admitting the
cut beats losing it. The inverse rule — text coming *in* is refused rather than
truncated — lives in :mod:`~cofferdam.actions_bridge.service`.

Display references
------------------

A canonical task id is 31 characters of base32. Nobody says that out loud, and a
model asked to repeat one will eventually get a character wrong. So every task
also carries a short ``display_ref`` — ``CF-`` plus six uppercase hex, derived
by digest from the id. It is **display only**: no Action accepts one, and
:func:`display_ref` is one-way. A caller that has lost the canonical id calls
``list_recent_tasks`` and reads it back rather than reconstructing it.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple

from .limits import (
    BRIDGE_API_VERSION,
    MAX_ACTIVITY_CHARS,
    MAX_OPTION_DESCRIPTION_CHARS,
    MAX_OPTION_LABEL_CHARS,
    MAX_OPTIONS,
    MAX_QUESTION_CHARS,
    MAX_RESULT_CHARS,
    MAX_TITLE_CHARS,
)

# -- states, as the bridge publishes them -------------------------------------
#
# Task Core's own words, unchanged. A second vocabulary meaning the same twelve
# things would be a translation table that could disagree with the thing it
# translates, and the first disagreement would be invisible.
#
# They are listed here only so the OpenAPI enum and this module cannot drift: a
# test asserts this tuple equals the schema's.

STATES: Tuple[str, ...] = (
    "created",
    "queued",
    "starting",
    "running",
    "waiting_for_user",
    "ready_for_followup",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
    "recovery_required",
)

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})

#: The reason a task is waiting, when it is. ``clarification`` is the only one
#: the bridge can act on; the rest are reported and pointed at the local
#: surface. ``authentication`` is the sharpest case — a task waiting for a
#: sign-in must never grow an answer box on a surface a model provider reads.
WAITING_REASON_CLARIFICATION = "clarification"
WAITING_REASON_APPROVAL = "approval"
WAITING_REASON_AUTHENTICATION = "authentication"

#: Waiting reasons that require a person at the workstation. Each is published
#: as ``local_action_required`` with its own reason word, and none of them has
#: an Action that could satisfy it.
LOCAL_ONLY_WAITING_REASONS = frozenset(
    {
        WAITING_REASON_APPROVAL,
        WAITING_REASON_AUTHENTICATION,
        "privileged_action",
        "adapter_input",
        "unknown",
    }
)

#: The one question shape this bridge carries. Everything else is reported as
#: unsupported rather than simplified into this one — see
#: :func:`clarification_view`.
ANSWER_MODE_SINGLE_CHOICE = "single_choice"

#: What the caller should do next, as a closed vocabulary. A model reads this
#: and it must not have to interpret prose to know whether to wait or act.
NEXT_SYNC = "sync_task"
NEXT_ANSWER = "submit_choice_answer"
NEXT_FOLLOWUP_OR_FINISH = "send_followup_or_finish_task"
NEXT_LOCAL = "open_the_local_cofferdam_surface"
NEXT_NOTHING = "nothing"

NEXT_OPERATIONS: Tuple[str, ...] = (
    NEXT_SYNC,
    NEXT_ANSWER,
    NEXT_FOLLOWUP_OR_FINISH,
    NEXT_LOCAL,
    NEXT_NOTHING,
)

# -- delegation, as Cofferdam reports it --------------------------------------
#
# Task Core's own words again, for the same reason as STATES: this process talks
# to the daemon over HTTP and deliberately imports none of its code, so the
# vocabulary has to be repeated somewhere, and a test asserts the two tuples are
# equal. Repeating it is the cost of the process boundary; a translation table
# would be the cost plus a place to disagree.

DELEGATION_OK = "ok"
DELEGATION_NO_ADAPTER = "no_adapter"
DELEGATION_AMBIGUOUS = "ambiguous_adapter"
DELEGATION_UNAVAILABLE = "delegated_adapter_unavailable"

DELEGATIONS: Tuple[str, ...] = (
    DELEGATION_OK,
    DELEGATION_NO_ADAPTER,
    DELEGATION_AMBIGUOUS,
    DELEGATION_UNAVAILABLE,
)

#: The two statuses that mean "somebody has to edit a file on the workstation",
#: as opposed to "this project was never going to take delegated work". Only
#: these two produce ``project_not_eligible``; see the create route.
#:
#: ``no_adapter`` is deliberately absent. A project that permits no adapter is
#: not misconfigured — it is what a Remote-Control-only or validation-only
#: project looks like — and it never appears in ``listProjects`` at all, so a
#: caller naming one has guessed an id.
AMBIGUOUS_DELEGATIONS = frozenset({DELEGATION_AMBIGUOUS, DELEGATION_UNAVAILABLE})


# -- text ---------------------------------------------------------------------


def clipped(value: Any, limit: int) -> Tuple[Optional[str], bool]:
    """Bounded text, and whether it was cut. Never raises on a hostile value."""
    if not isinstance(value, str):
        return None, False
    if len(value) <= limit:
        return value, False
    return value[: limit - 1] + "…", True


def _text(value: Any, limit: int) -> Optional[str]:
    return clipped(value, limit)[0]


def display_ref(task_id: Any) -> Optional[str]:
    """``CF-`` plus six uppercase hex, derived from the canonical id.

    A digest rather than a prefix of the id itself. The first characters of a
    task id encode its creation time in milliseconds, so a visible prefix would
    publish *when* every task was made in a field designed to be read aloud and
    pasted into a chat — and the timestamp is already in ``created_at``, where
    somebody chose to put it.

    One-way on purpose. There is no lookup from a display reference back to a
    task id anywhere in this package: an Action that accepted one would be an
    Action that resolves a six-character token to a live task, and six
    characters is not enough entropy to be a handle.
    """
    if not isinstance(task_id, str) or not task_id:
        return None
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    return "CF-" + digest[:6].upper()


# -- projects -----------------------------------------------------------------


def project_view(entry: Any) -> Optional[Dict[str, Any]]:
    """One project, as the bridge publishes it.

    Four fields out of the six Cofferdam holds, and the two that are dropped are
    the point of the function.

    ``notes`` is free text a person wrote into a host configuration file. Today
    one of them reads "Disposable. Safe to delete and recreate." — harmless, and
    exactly the kind of field that later says "the one with the client's data in
    it". It is a note to the operator, not a label for a model.

    ``remote_control_enabled`` describes Lane A: whether this project may host a
    native interactive Claude session. The bridge has no Action that touches
    Lane A and must never suggest one exists, so it does not report the
    capability either.

    The root path is not dropped here — it was never published. Cofferdam's own
    ``TaskProject.to_dict`` has no field for it.

    ``delegation`` is read and not republished. It is the *reason* a project
    cannot take work, which is useful to the operator looking at the PWA and is
    nothing a model provider can act on: the fix is always an edit to a file on
    the workstation. What the model gets is the consequence, in the field that
    already exists for it — ``accepts_tasks`` — so a project whose delegation is
    ambiguous is simply one the GPT is never offered.
    """
    if not isinstance(entry, dict):
        return None
    project_id = entry.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return None
    adapters = entry.get("adapters")
    labels = (
        [a for a in adapters if isinstance(a, str)][:8]
        if isinstance(adapters, list)
        else []
    )
    delegated = entry.get("delegated_adapter")
    return {
        "project_id": project_id,
        "display_name": _text(entry.get("display_name"), MAX_TITLE_CHARS)
        or project_id,
        "enabled": bool(entry.get("enabled")),
        # Adapter *ids*, which are code-owned words like ``claude-code``. They
        # tell a model whether a project can take delegated work at all, which
        # it needs in order to avoid proposing a task somewhere that will refuse
        # one. They name no model, no flag and no binary.
        #
        # Still the *permitted* list rather than the resolved one, and the field
        # keeps the meaning its published description gives it. A project that
        # permits one adapter therefore shows exactly the adapter that will run,
        # which is what the two Gate B sandboxes look like.
        "task_adapters": labels,
        # Resolvability, not merely eligibility. Before M2I.5 PR3 this asked
        # "does the project permit any adapter", which was the same question
        # while every delegated project permitted exactly one. It is not the same
        # question once two can coexist: a project permitting both Claude
        # transports and delegating to neither has adapters and cannot take a
        # task, and telling a model otherwise would send it to a create that
        # fails for a reason it cannot fix.
        "accepts_tasks": bool(entry.get("enabled"))
        and isinstance(delegated, str)
        and bool(delegated),
    }


def delegation_for_project(
    payload: Any, project_id: str
) -> Tuple[Optional[str], Optional[str]]:
    """The host's answer to "which adapter runs here", and its reason.

    Returns ``(adapter_id, delegation_status)``. ``(None, None)`` means the
    project is not in the payload at all, or is disabled — the caller turns that
    into "no such project", deliberately the same answer as a project that does
    not exist, because a caller able to tell those apart can enumerate.

    **This function reads a decision; it does not make one.** The decision is
    ``TaskProject.delegation`` on the workstation, published as
    ``delegated_adapter``. That matters more than it looks: the bridge is the
    process holding a credential given to a model provider, and the last thing
    it should own is which agent runs on the machine.

    What is gone from here, and must not come back
    ----------------------------------------------

    Until M2I.5 PR3 this function took the **first entry of the project's
    adapter list**. That was defensible only while every delegated project
    permitted exactly one adapter — and Gate B makes two coexist, which is
    precisely the condition it was never valid under.

    It was also not the rule its own docstring claimed. Cofferdam sorts that
    list at load, so "first" meant *alphabetically first*: with both Claude
    transports permitted, ``claude-agent-sdk`` would have quietly won over
    ``claude-code`` because of where the letters fall. Nobody would have chosen
    that, and nobody would have seen it happen.

    So there is no ordering here now, and no fallback of any kind. A payload
    that does not carry ``delegated_adapter`` — an older daemon than this
    bridge, say — yields ``None`` and a refused create, which is the safe
    direction for a version skew to break in.
    """
    entries = payload.get("projects") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return None, None
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("project_id") != project_id:
            continue
        if not entry.get("enabled"):
            return None, None
        status = entry.get("delegation")
        status = status if isinstance(status, str) and status else None
        delegated = entry.get("delegated_adapter")
        if isinstance(delegated, str) and delegated:
            return delegated, status
        return None, status
    return None, None


def adapter_for_project(payload: Any, project_id: str) -> Optional[str]:
    """The delegated adapter id, or ``None``. See :func:`delegation_for_project`."""
    return delegation_for_project(payload, project_id)[0]


def projects_view(payload: Any) -> Dict[str, Any]:
    entries = payload.get("projects") if isinstance(payload, dict) else None
    projects = []
    if isinstance(entries, list):
        for entry in entries:
            view = project_view(entry)
            # Only projects that can actually take a task. A model offered a
            # project that will refuse every create is a model that will keep
            # trying, and the refusal it gets says nothing it can act on.
            if view is not None and view["accepts_tasks"]:
                projects.append(view)
    return {"version": BRIDGE_API_VERSION, "projects": projects}


# -- clarifications -----------------------------------------------------------


def _option_view(entry: Any) -> Optional[Dict[str, Any]]:
    """One option. Dropped entirely if it has no ``option_id``.

    An option without an id cannot be submitted — the answer route's whole
    safety property is that a client sends an identifier Cofferdam minted a
    moment ago. Publishing one anyway would show somebody a choice that cannot
    be chosen, which is worse than showing them one fewer.

    ``value`` is deliberately absent. It is provider text, it is what *would* be
    sent if the option were picked, and a model that could read it might compose
    something similar into a field that takes prose. The bridge exposes the id
    and the label; the value never leaves the host.
    """
    if not isinstance(entry, dict):
        return None
    option_id = entry.get("option_id")
    if not isinstance(option_id, str) or not option_id:
        return None
    label = _text(entry.get("label"), MAX_OPTION_LABEL_CHARS)
    if not label:
        return None
    return {
        "option_id": option_id,
        "label": label,
        "description": _text(entry.get("description"), MAX_OPTION_DESCRIPTION_CHARS),
    }


def clarification_view(entry: Any) -> Dict[str, Any]:
    """One pending question, or an honest statement that it cannot be carried.

    Three outcomes, and the third is the one this function exists for.

    A **single-choice question with usable options** is published in full, with
    ``clarification_supported: true``.

    **Anything else** — free text, multiple choice, an unknown mode, a
    single-choice question whose options lost their ids — is published as
    ``clarification_supported: false`` with a reason word and
    ``local_action_required: true``. The question text is still carried, so
    somebody reading their phone knows what is being asked. What is *not* done
    is the tempting thing: no option list is invented, no free-text question is
    reduced to yes/no, and no "best guess" is offered. A fabricated question is
    a wrong answer delivered to an agent in somebody's name.

    ``schema_verified`` is passed through unchanged. Cofferdam sets it when the
    question came through a provider schema it has actually verified rather than
    inferred, and a caller deciding whether to trust a normalized question
    should be able to see that distinction rather than have it decided here.
    """
    if not isinstance(entry, dict):
        return {
            "clarification_supported": False,
            "reason": "unsupported_question_shape",
            "local_action_required": True,
        }

    question_id = entry.get("question_id")
    question = _text(entry.get("question"), MAX_QUESTION_CHARS)
    answer_mode = entry.get("answer_mode")
    raw_options = entry.get("options")
    options: List[Dict[str, Any]] = []
    if isinstance(raw_options, list):
        for candidate in raw_options[:MAX_OPTIONS]:
            view = _option_view(candidate)
            if view is not None:
                options.append(view)

    base: Dict[str, Any] = {
        "question_id": question_id if isinstance(question_id, str) else None,
        "question": question,
        "requested_at": entry.get("requested_at"),
        "schema_verified": bool(entry.get("schema_verified")),
    }

    if answer_mode != ANSWER_MODE_SINGLE_CHOICE:
        return {
            **base,
            "clarification_supported": False,
            "answer_mode": answer_mode if isinstance(answer_mode, str) else "unknown",
            "reason": "unsupported_question_shape",
            "local_action_required": True,
            "options": [],
        }
    if not options:
        return {
            **base,
            "clarification_supported": False,
            "answer_mode": ANSWER_MODE_SINGLE_CHOICE,
            "reason": "options_not_submittable",
            "local_action_required": True,
            "options": [],
        }
    if not base["question_id"]:
        return {
            **base,
            "clarification_supported": False,
            "answer_mode": ANSWER_MODE_SINGLE_CHOICE,
            "reason": "unsupported_question_shape",
            "local_action_required": True,
            "options": [],
        }
    return {
        **base,
        "clarification_supported": True,
        "answer_mode": ANSWER_MODE_SINGLE_CHOICE,
        "reason": None,
        "local_action_required": False,
        "options": options,
        # Said in the payload rather than left to an instruction file, because
        # this is the sentence a model most needs at the moment it is composing
        # an answer, and an instruction is easier to lose than a field.
        "answer_rules": (
            "Submit exactly one option_id from this list. Custom text cannot be "
            "carried on this question shape."
        ),
    }


# -- tasks ---------------------------------------------------------------------


def _capabilities(snapshot: Dict[str, Any]) -> Dict[str, bool]:
    caps = snapshot.get("adapter_capabilities")
    return caps if isinstance(caps, dict) else {}


def task_row(snapshot: Any) -> Optional[Dict[str, Any]]:
    """One row for ``list_recent_tasks``. No content, ever.

    ``latest_activity`` is a bounded line Cofferdam composed about the *shape* of
    what happened, not the content of it, and it is the one thing that makes a
    list readable. The result, the output and the prompt are all absent: a list
    is a list, and a model that wants an answer calls ``sync_task`` for the one
    task it means.
    """
    if not isinstance(snapshot, dict):
        return None
    task_id = snapshot.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return None
    state = snapshot.get("state")
    caps = _capabilities(snapshot)
    waiting_reason = snapshot.get("waiting_reason")
    return {
        "task_id": task_id,
        "display_ref": display_ref(task_id),
        "project": _text(snapshot.get("project_display_name"), MAX_TITLE_CHARS)
        or snapshot.get("project_id"),
        "state": state,
        "terminal": bool(snapshot.get("terminal")),
        "title": _text(snapshot.get("title"), MAX_TITLE_CHARS),
        "updated_at": snapshot.get("updated_at"),
        # Booleans a model can branch on without knowing the state machine.
        "has_pending_question": waiting_reason == WAITING_REASON_CLARIFICATION,
        "local_action_required": waiting_reason in LOCAL_ONLY_WAITING_REASONS,
        "result_available": state in TERMINAL_STATES
        or state == "ready_for_followup",
        "follow_up_available": state == "ready_for_followup"
        and bool(caps.get("followup")),
    }


def recent_tasks_view(payload: Any, *, limit: int) -> Dict[str, Any]:
    """A bounded, deterministically ordered recent list.

    The daemon already returns newest-first; the sort here is not trust in that,
    it is a guarantee this response makes on its own. ``updated_at`` descending
    with the task id as the tiebreak, so two tasks touched in the same
    millisecond come back in the same order on every call — a list that
    reshuffles is a list a model will describe as "changed".
    """
    rows: List[Dict[str, Any]] = []
    entries = payload.get("tasks") if isinstance(payload, dict) else None
    if isinstance(entries, list):
        for entry in entries:
            row = task_row(entry)
            if row is not None:
                rows.append(row)
    rows.sort(key=lambda row: (str(row.get("updated_at") or ""), row["task_id"]), reverse=True)
    return {
        "version": BRIDGE_API_VERSION,
        "tasks": rows[:limit],
        "count": len(rows[:limit]),
        "limit": limit,
    }


def _next_operation(
    *, state: Any, supported_question: bool, local_only: bool, follow_up: bool
) -> str:
    if local_only:
        return NEXT_LOCAL
    if supported_question:
        return NEXT_ANSWER
    if state in TERMINAL_STATES:
        return NEXT_NOTHING
    if follow_up:
        return NEXT_FOLLOWUP_OR_FINISH
    return NEXT_SYNC


def task_snapshot_view(
    snapshot: Any,
    *,
    clarification: Optional[Dict[str, Any]] = None,
    result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The one bounded snapshot ``sync_task`` returns.

    Assembled from up to three upstream reads — the task, its open questions and
    its latest result — and published as one object, because a model that has to
    make three calls to answer "what happened" will make two of them and guess
    the third.

    The result section carries the *latest completed turn's* text and says
    whether that is also the task's final word, restating the distinction
    ``TaskResult`` makes rather than flattening it. ``provider_session_id`` is
    present in the upstream result and is not read here.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    task_id = snapshot.get("task_id")
    state = snapshot.get("state")
    caps = _capabilities(snapshot)
    waiting_reason = snapshot.get("waiting_reason")
    local_only = waiting_reason in LOCAL_ONLY_WAITING_REASONS

    question = clarification if isinstance(clarification, dict) else None
    supported_question = bool(question and question.get("clarification_supported"))

    failure = snapshot.get("failure")
    failure_code = None
    failure_summary = None
    if isinstance(failure, dict):
        failure_code = failure.get("code")
        failure_summary = _text(failure.get("summary"), MAX_ACTIVITY_CHARS)

    view: Dict[str, Any] = {
        "version": BRIDGE_API_VERSION,
        "task_id": task_id,
        "display_ref": display_ref(task_id),
        "project": _text(snapshot.get("project_display_name"), MAX_TITLE_CHARS)
        or snapshot.get("project_id"),
        "state": state,
        "terminal": bool(snapshot.get("terminal")),
        "title": _text(snapshot.get("title"), MAX_TITLE_CHARS),
        "created_at": snapshot.get("created_at"),
        "updated_at": snapshot.get("updated_at"),
        "latest_activity": _text(snapshot.get("latest_activity"), MAX_ACTIVITY_CHARS),
        "local_action_required": local_only,
        "local_action_reason": waiting_reason if local_only else None,
        "can_cancel": bool(caps.get("cancel")) and state not in TERMINAL_STATES,
        "can_finish": state == "ready_for_followup",
        "follow_up_available": False,
        "failure_code": failure_code,
        "failure_summary": failure_summary,
        # Absent rather than false. There is no task-owned artifact model in
        # Cofferdam yet, so "no artifacts for this task" would be a claim the
        # host cannot make; "the capability does not exist" is the true one.
        "artifacts_supported": False,
        "artifacts_unavailable_reason": "no_task_owned_artifact_model",
    }

    if question is not None:
        view["pending_question"] = question
    else:
        view["pending_question"] = None

    if isinstance(result, dict):
        text, truncated = clipped(result.get("result"), MAX_RESULT_CHARS)
        view["result"] = {
            "available": text is not None,
            "text": text,
            "truncated": truncated,
            "outcome": result.get("outcome"),
            "succeeded": bool(result.get("succeeded")),
            "is_final": bool(result.get("task_terminal")),
            "completed_at": result.get("completed_at"),
            "turn_number": result.get("turn_number"),
            "turn_count": result.get("turn_count"),
            "failure_code": result.get("failure_code"),
            "failure_summary": _text(
                result.get("failure_summary"), MAX_ACTIVITY_CHARS
            ),
            "meaning": (
                "The latest completed turn's result. When is_final is true this "
                "is also the task's final result."
            ),
        }
        view["follow_up_available"] = bool(result.get("follow_up_available"))
    else:
        view["result"] = {"available": False, "text": None, "truncated": False}
        view["follow_up_available"] = state == "ready_for_followup" and bool(
            caps.get("followup")
        )

    # A question and a follow-up are mutually exclusive on purpose, and the
    # exclusion is enforced here as well as upstream: Task Core refuses a
    # follow-up while a question is open, and a snapshot that advertised both
    # would send a model into a refusal it was told to expect success from.
    if supported_question or (question is not None):
        view["follow_up_available"] = False

    view["next_recommended_operation"] = _next_operation(
        state=state,
        supported_question=supported_question,
        local_only=local_only,
        follow_up=view["follow_up_available"],
    )
    return view


def created_task_view(snapshot: Any, *, created: bool) -> Dict[str, Any]:
    """What ``create_task`` returns. Smaller than a sync on purpose.

    A freshly created task has no result and no question, and returning the
    empty shapes for both would teach a model to read them. ``created`` is false
    when an idempotency key matched, which is the one thing a retrying caller
    most needs to know.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    task_id = snapshot.get("task_id")
    return {
        "version": BRIDGE_API_VERSION,
        "task_id": task_id,
        "display_ref": display_ref(task_id),
        "project": _text(snapshot.get("project_display_name"), MAX_TITLE_CHARS)
        or snapshot.get("project_id"),
        "state": snapshot.get("state"),
        "title": _text(snapshot.get("title"), MAX_TITLE_CHARS),
        "created_at": snapshot.get("created_at"),
        "created": bool(created),
        "next_recommended_operation": NEXT_SYNC,
    }


def mutation_view(
    snapshot: Any, *, replayed: bool, accepted: bool = True
) -> Dict[str, Any]:
    """What an answer, follow-up, cancel or finish returns.

    ``replayed`` is the honest half. A retry that reached Cofferdam once and was
    recognised the second time returns the same normalized state with
    ``replayed: true``, rather than either a false success or a refusal for a
    message that did in fact get through.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    task_id = snapshot.get("task_id")
    state = snapshot.get("state")
    caps = _capabilities(snapshot)
    waiting_reason = snapshot.get("waiting_reason")
    local_only = waiting_reason in LOCAL_ONLY_WAITING_REASONS
    has_question = waiting_reason == WAITING_REASON_CLARIFICATION
    follow_up = (
        state == "ready_for_followup" and bool(caps.get("followup")) and not has_question
    )
    return {
        "version": BRIDGE_API_VERSION,
        "task_id": task_id,
        "display_ref": display_ref(task_id),
        "state": state,
        "terminal": bool(snapshot.get("terminal")),
        "accepted": bool(accepted),
        "replayed": bool(replayed),
        "has_pending_question": has_question,
        "local_action_required": local_only,
        "follow_up_available": follow_up,
        "updated_at": snapshot.get("updated_at"),
        "next_recommended_operation": _next_operation(
            state=state,
            supported_question=has_question,
            local_only=local_only,
            follow_up=follow_up,
        ),
    }


__all__ = [
    "AMBIGUOUS_DELEGATIONS",
    "ANSWER_MODE_SINGLE_CHOICE",
    "DELEGATIONS",
    "DELEGATION_AMBIGUOUS",
    "DELEGATION_NO_ADAPTER",
    "DELEGATION_OK",
    "DELEGATION_UNAVAILABLE",
    "adapter_for_project",
    "LOCAL_ONLY_WAITING_REASONS",
    "NEXT_OPERATIONS",
    "STATES",
    "TERMINAL_STATES",
    "clarification_view",
    "clipped",
    "created_task_view",
    "delegation_for_project",
    "display_ref",
    "mutation_view",
    "project_view",
    "projects_view",
    "recent_tasks_view",
    "task_row",
    "task_snapshot_view",
]


# -- remote operations, read-only (M2M PR2) -----------------------------------
#
# The workstation already publishes the canonical projection, so these views
# **re-shape nothing**: they select the fields the bridge is willing to publish
# and drop everything else. That direction matters. A view that rebuilt the
# phase, or recomputed `needs_person`, would be a second implementation of the
# lifecycle logic living on the wrong side of the wire — exactly what M2M PR1's
# projection exists to prevent. The Custom GPT consumes `phase`, `sentence`,
# `needs_person` and `available_actions` as given.

#: Bounds for the two large reads. A Custom GPT Action response has to stay
#: renderable; a prompt longer than this is one to read on the workstation.
MAX_OPERATION_PROMPT_CHARS = 12000
MAX_OPERATION_RESULT_CHARS = 4000

#: Exactly what an operations entry may carry outward. An allowlist rather than
#: a denylist: a field added upstream is *not* published until somebody adds it
#: here, which is the safe direction for a surface a model talks to.
_OPERATION_FIELDS = (
    "project_id",
    "display_name",
    "phase",
    "sentence",
    "needs_person",
    "busy",
    "settled",
    "available_actions",
)


def _operation_entry(entry: Any) -> Optional[Dict[str, Any]]:
    """One project's operational state, as the bridge publishes it."""
    if not isinstance(entry, dict):
        return None
    project_id = entry.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return None

    published: Dict[str, Any] = {
        name: entry.get(name) for name in _OPERATION_FIELDS if name in entry
    }
    published.setdefault("available_actions", [])

    # Handles, filtered. These are what a later control would be addressed by,
    # and `because` is deliberately dropped: it names an internal row and column
    # and is a debugging aid for the workstation, not something to publish.
    handles = entry.get("handles")
    if isinstance(handles, dict):
        published["handles"] = {
            name: handles.get(name)
            for name in (
                "planner_request_id", "dispatch_id", "task_id",
                "publication_id", "prompt_available",
            )
            if name in handles
        }

    machine = entry.get("machine")
    if isinstance(machine, dict):
        publication = machine.get("publication")
        published["machine"] = {
            "planner_status": machine.get("planner_status"),
            "approved": machine.get("approved"),
            "worker_state": machine.get("worker_state"),
            "restart": machine.get("restart"),
            "worker_completion_is_not_acceptance": machine.get(
                "worker_completion_is_not_acceptance", True
            ),
            "publication": _publication_view(publication),
        }

    claims = entry.get("claims")
    if isinstance(claims, dict):
        published["claims"] = {
            "planner_summary": _text(claims.get("planner_summary"), 600),
            "worker_report": _text(claims.get("worker_report"), 600),
            # Restated on the way out. A client reading this block must be able
            # to see, without cross-referencing, that it is model-authored.
            "source": "model_authored",
        }
    return published


def _publication_view(publication: Any) -> Optional[Dict[str, Any]]:
    """Publication facts, without the remote URL or anything host-private."""
    if not isinstance(publication, dict):
        return None
    pull_request = publication.get("pull_request")
    return {
        "state": publication.get("state"),
        "repository": publication.get("repository"),
        "branch": publication.get("branch"),
        "base_branch": publication.get("base_branch"),
        "commit": publication.get("commit"),
        "pull_request": (
            None
            if not isinstance(pull_request, dict)
            else {
                "number": pull_request.get("number"),
                "url": pull_request.get("url"),
                "state": pull_request.get("state"),
            }
        ),
        "failure": publication.get("failure"),
    }


def operations_overview_view(payload: Any) -> Dict[str, Any]:
    """Every project, plus the subset that cannot move without a person.

    ``attention`` is computed here from the already-published ``needs_person``
    rather than re-derived, so the two can never disagree — and it exists so a
    Custom GPT answering "what needs me?" does not have to filter client-side and
    get the predicate subtly wrong.
    """
    entries = payload.get("projects") if isinstance(payload, dict) else None
    projects = []
    if isinstance(entries, list):
        for entry in entries:
            published = _operation_entry(entry)
            if published is not None:
                projects.append(published)
    attention = [item for item in projects if item.get("needs_person")]
    return {
        "projects": projects,
        "attention": attention,
        "count": len(projects),
        "attention_count": len(attention),
    }


def operations_project_view(payload: Any) -> Dict[str, Any]:
    published = _operation_entry(payload)
    return published if published is not None else {}


def operation_prompt_view(payload: Any) -> Dict[str, Any]:
    """The exact approved prompt, bounded.

    ``matches_dispatched_digest`` is published unchanged and is the field that
    matters: it answers *was the text Cofferdam sent the text that was
    approved*, which "here is what is in the database" does not.
    """
    if not isinstance(payload, dict):
        return {}
    text, clipped_here = clipped(payload.get("prompt"), MAX_OPERATION_PROMPT_CHARS)
    return {
        "project_id": payload.get("project_id"),
        "planner_request_id": payload.get("planner_request_id"),
        "dispatch_id": payload.get("dispatch_id"),
        "prompt": text,
        # Either side may have clipped it; the client needs to know that it is
        # not looking at the whole thing.
        "truncated": bool(payload.get("truncated")) or clipped_here,
        "matches_dispatched_digest": bool(payload.get("matches_dispatched_digest")),
        "approved_subject_fingerprint": payload.get("approved_subject_fingerprint"),
    }


def operation_result_view(payload: Any) -> Dict[str, Any]:
    """Machine observations and model claims, still in two blocks."""
    if not isinstance(payload, dict):
        return {}
    machine = payload.get("machine") if isinstance(payload.get("machine"), dict) else {}
    claims = payload.get("claims") if isinstance(payload.get("claims"), dict) else {}
    return {
        "project_id": payload.get("project_id"),
        "dispatch_id": payload.get("dispatch_id"),
        "task_id": payload.get("task_id"),
        "machine": {
            "branch": machine.get("branch"),
            "commit": machine.get("commit"),
            "worker_state": machine.get("worker_state"),
            "checks": machine.get("checks"),
            "restart": machine.get("restart"),
            "publication": _publication_view(machine.get("publication")),
            "observed_by": "cofferdam",
        },
        "claims": {
            "planner_summary": _text(claims.get("planner_summary"), 600),
            "worker_report": _text(
                claims.get("worker_report"), MAX_OPERATION_RESULT_CHARS
            ),
            "source": "model_authored",
        },
        "worker_completion_is_not_acceptance": True,
    }


# -- remote development requests, planner-only (M2M PR4) ----------------------
#
# The same direction as the read views above: these **re-shape nothing**. The
# workstation publishes the canonical operational projection and this selects
# the fields the bridge is willing to publish. In particular, `phase`,
# `sentence`, `needs_person` and `settled` are passed through as given — a view
# that recomputed any of them would be a second implementation of the lifecycle
# logic on the wrong side of the wire.

#: A question is bounded to 2,000 characters by the planner result contract and
#: to the same number by the workstation's read, so this never clips a valid one.
#: It is here so a wider bound upstream cannot make this response unbounded.
MAX_OPERATION_QUESTION_CHARS = 2_000


def development_request_view(payload: Any) -> Dict[str, Any]:
    """What ``createDevelopmentRequest`` returns.

    Three things a client needs and cannot derive: which planner request this
    produced, whether this call produced it or found it, and what the planner
    decided. Everything else is the operational projection, unchanged.

    ``authority`` is restated rather than assumed. This is the payload a model
    reads immediately after asking for work to be planned, and it is exactly the
    moment a model is most likely to narrate a prepared prompt as though
    something were now going to happen. The block says, in the response itself,
    that nothing was approved, dispatched or executed — and it is true by
    construction, because this route reaches nothing that could do any of them.
    """
    if not isinstance(payload, dict):
        return {}

    published: Dict[str, Any] = {
        name: payload.get(name) for name in _OPERATION_FIELDS if name in payload
    }
    published.setdefault("available_actions", [])

    handles = payload.get("handles")
    if isinstance(handles, dict):
        published["handles"] = {
            name: handles.get(name)
            for name in (
                "planner_request_id", "dispatch_id", "task_id",
                "publication_id", "prompt_available",
            )
            if name in handles
        }

    claims = payload.get("claims")
    if isinstance(claims, dict):
        published["claims"] = {
            "planner_summary": _text(claims.get("planner_summary"), 600),
            "source": "model_authored",
        }

    published.update(
        {
            "version": BRIDGE_API_VERSION,
            "project_id": payload.get("project_id"),
            "planner_request_id": payload.get("planner_request_id"),
            "replayed": bool(payload.get("replayed")),
            "planner_action": payload.get("planner_action"),
            "planner_status": payload.get("planner_status"),
            "planner_failure_code": payload.get("planner_failure_code"),
            # Published unconditionally and as constants, not copied from
            # upstream. A field a daemon could set to `true` is a field a
            # compromised daemon could set to `true`; these three are false in
            # this build because no route on this bridge can make them anything
            # else, and that is a property of the code rather than of a payload.
            "authority": {
                "approved": False,
                "dispatched": False,
                "executed": False,
                "note": (
                    "Cofferdam prepared this. Nothing has been approved, "
                    "dispatched or executed."
                ),
            },
        }
    )
    return published


def operation_question_view(payload: Any) -> Dict[str, Any]:
    """The exact pending question, bounded.

    ``answering_requires_the_workstation`` is a constant rather than a value read
    from upstream, for the reason the authority block above is: it describes what
    this bridge can do, and this bridge has no route that answers a question.
    """
    if not isinstance(payload, dict):
        return {}
    text, clipped_here = clipped(
        payload.get("question"), MAX_OPERATION_QUESTION_CHARS
    )
    return {
        "project_id": payload.get("project_id"),
        "planner_request_id": payload.get("planner_request_id"),
        "question": text,
        "truncated": bool(payload.get("truncated")) or clipped_here,
        "planner_summary": _text(payload.get("planner_summary"), 600),
        "answered": bool(payload.get("answered")),
        "answered_subject_fingerprint": payload.get("answered_subject_fingerprint"),
        "answering_requires_the_workstation": True,
        "source": "model_authored",
    }
