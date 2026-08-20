"""The planner's two contracts: what goes in, and what may come back.

Both are closed. The request has no field an execution primitive could arrive
in, and the result has no field one could leave in — asserted from the dataclass
fields themselves in ``tests/test_planner_contracts.py`` rather than defended by
validation, because a field that does not exist cannot be forgotten about.

**Planner output is data, never execution.** A model that emits text resembling
a shell command, an XML tool call, JSON-RPC, an MCP method or an instruction to
another worker has emitted *text*. ``PREPARE_WORKER_PROMPT`` means "store this
for a separate bounded worker a person will confirm", never "run this".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields as dataclass_fields
from typing import Any, Dict, Mapping, Optional, Tuple

from ..context.projection.model import CloudContextProjection
from .errors import PlannerResultInvalid

#: Bumped when the closed shape below changes in a way a stored result would
#: notice. A result carrying any other value is refused rather than coerced.
PLANNER_RESULT_SCHEMA_VERSION = 1

ACTION_ASK_USER = "ASK_USER"
ACTION_PREPARE_WORKER_PROMPT = "PREPARE_WORKER_PROMPT"
ACTION_STOP = "STOP"

#: The whole vocabulary. PR1c-a deliberately stops here: `REPORT_RESULT` and
#: `PROPOSE_NEXT_STEP` belong to the evaluation turn, which does not exist yet,
#: and an action nothing can produce is an action nothing can be tested against.
PLANNER_ACTIONS: Tuple[str, ...] = (
    ACTION_ASK_USER,
    ACTION_PREPARE_WORKER_PROMPT,
    ACTION_STOP,
)

MAX_SUMMARY_CHARS = 2000
MAX_BASIS_CHARS = 4000
MAX_WORKER_PROMPT_CHARS = 20000
MAX_USER_QUESTION_CHARS = 2000
MAX_INTENT_CHARS = 8000
MAX_NOTE_CHARS = 20000

#: Names that would turn a semantic answer into an execution surface. Refused
#: anywhere in a result object, at any depth, whatever their value — including
#: as a key the provider schema would otherwise have allowed through.
FORBIDDEN_RESULT_KEYS = frozenset(
    {
        "command",
        "commands",
        "argv",
        "args",
        "shell",
        "cwd",
        "working_directory",
        "env",
        "environment",
        "tool",
        "tool_name",
        "tool_call",
        "tool_calls",
        "function_call",
        "mcp",
        "mcp_method",
        "mcp_server",
        "executable",
        "exec",
        "run",
        "script",
        "path",
        "file_path",
    }
)


# -- request ------------------------------------------------------------------


@dataclass(frozen=True)
class DevelopmentRequest:
    """One development step, as much of it as a planner is allowed to see.

    **The projection is required and is the only route project context takes.**
    A :class:`CloudContextProjection` can only be produced by
    ``ContextProjector.project``, so a request cannot be assembled from a
    ``LocalContextPack`` — there is no field it would fit in, and no constructor
    that would accept one. That is the egress rule from D-2026-08-20-1 expressed
    as a type: rich local read authority is not permission to send.

    Everything else here is user- or Custom-GPT-authored *prose*. There is no
    path, no directory, no command, no argv, no environment, no tool name, no
    MCP configuration and no provider flag, because a planner needs none of
    them and a request that could carry one would eventually carry one.
    """

    request_id: str
    user_intent: str
    projection: CloudContextProjection
    current_objective: Optional[str] = None
    plan_checkpoint: Optional[str] = None
    expected_next_step: Optional[str] = None
    #: Supplied by the private Custom GPT. Advisory context, never authority —
    #: the contract prompt tells the planner to treat it as somebody else's
    #: research rather than as a project decision.
    research_notes: Optional[str] = None
    prompt_writing_guidance: Optional[str] = None
    #: What this step is allowed to touch. Prose, read by the model; the real
    #: boundary is that the planner cannot act at all.
    authority_boundary: Optional[str] = None
    provenance: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.projection, CloudContextProjection):
            raise TypeError(
                "DevelopmentRequest.projection must be a CloudContextProjection; "
                "local context may not reach an external planner"
            )
        if not (self.user_intent or "").strip():
            raise ValueError("user_intent must not be empty")
        for name, limit in (
            ("user_intent", MAX_INTENT_CHARS),
            ("research_notes", MAX_NOTE_CHARS),
            ("prompt_writing_guidance", MAX_NOTE_CHARS),
        ):
            value = getattr(self, name)
            if value is not None and len(value) > limit:
                raise ValueError(f"{name} exceeds {limit} characters")

    def to_prompt_payload(self) -> Dict[str, Any]:
        """The bounded packet handed to a provider. Data only, no directives."""
        return {
            "request_id": self.request_id,
            "user_intent": self.user_intent,
            "current_objective": self.current_objective,
            "plan_checkpoint": self.plan_checkpoint,
            "expected_next_step": self.expected_next_step,
            "research_notes_from_custom_gpt": self.research_notes,
            "prompt_writing_guidance": self.prompt_writing_guidance,
            "authority_boundary": self.authority_boundary,
            "project_context": self.projection.to_dict(),
        }


# -- result -------------------------------------------------------------------


@dataclass(frozen=True)
class PlannerResult:
    """What a planning turn may say. Nothing here is dispatchable."""

    action: str
    summary: str
    confidence: float
    schema_version: int = PLANNER_RESULT_SCHEMA_VERSION
    worker_prompt: Optional[str] = None
    user_question: Optional[str] = None
    decision_basis: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "action": self.action,
            "summary": self.summary,
            "confidence": self.confidence,
            "worker_prompt": self.worker_prompt,
            "user_question": self.user_question,
            "decision_basis": self.decision_basis,
        }


#: Gate 1. Handed to the provider so it constrains the model's own output.
#: `additionalProperties: false` is the load-bearing line: it is what stops a
#: model deciding to add `command` next to `summary`.
PLANNER_RESULT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "action", "summary", "confidence"],
    "properties": {
        "schema_version": {"type": "integer", "enum": [PLANNER_RESULT_SCHEMA_VERSION]},
        "action": {"type": "string", "enum": list(PLANNER_ACTIONS)},
        "summary": {"type": "string", "maxLength": MAX_SUMMARY_CHARS},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "worker_prompt": {
            "type": ["string", "null"],
            "maxLength": MAX_WORKER_PROMPT_CHARS,
        },
        "user_question": {
            "type": ["string", "null"],
            "maxLength": MAX_USER_QUESTION_CHARS,
        },
        "decision_basis": {"type": ["string", "null"], "maxLength": MAX_BASIS_CHARS},
    },
}

_ALLOWED_KEYS = frozenset(PLANNER_RESULT_SCHEMA["properties"])


def _scan_for_execution_primitives(value: Any, path: str = "$") -> None:
    """Refuse an execution surface anywhere in the object, at any depth."""
    if isinstance(value, Mapping):
        for key, sub in value.items():
            lowered = str(key).strip().lower()
            if lowered in FORBIDDEN_RESULT_KEYS:
                raise PlannerResultInvalid(
                    "planner result contains an execution primitive",
                    detail=f"{path}.{key} is a forbidden key",
                )
            _scan_for_execution_primitives(sub, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, sub in enumerate(value):
            _scan_for_execution_primitives(sub, f"{path}[{index}]")


def validate_planner_result(payload: Any) -> PlannerResult:
    """Gate 2. Strict, closed, and never a repair.

    Provider-side schema enforcement is a useful first gate and not the
    authority: it is the provider's implementation of *our* schema, it does not
    check cross-field semantics, and a future provider may not have it at all.
    So everything is re-checked here, and anything that does not validate raises
    rather than degrading into a guess.
    """
    if not isinstance(payload, Mapping):
        raise PlannerResultInvalid(
            "planner result is not an object", detail=type(payload).__name__
        )

    _scan_for_execution_primitives(payload)

    unknown = sorted(set(map(str, payload)) - _ALLOWED_KEYS)
    if unknown:
        raise PlannerResultInvalid(
            "planner result has fields the contract does not define",
            detail=", ".join(unknown),
        )

    version = payload.get("schema_version")
    if version != PLANNER_RESULT_SCHEMA_VERSION:
        raise PlannerResultInvalid(
            "planner result schema_version is not the one this host speaks",
            detail=f"got {version!r}, expected {PLANNER_RESULT_SCHEMA_VERSION}",
        )

    action = payload.get("action")
    if action not in PLANNER_ACTIONS:
        raise PlannerResultInvalid(
            "planner result action is not in the closed vocabulary",
            detail=repr(action),
        )

    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise PlannerResultInvalid("summary must be a non-empty string")
    if len(summary) > MAX_SUMMARY_CHARS:
        raise PlannerResultInvalid("summary exceeds the bound")

    confidence = payload.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise PlannerResultInvalid("confidence must be a number", detail=repr(confidence))
    confidence = float(confidence)
    if not (0.0 <= confidence <= 1.0):
        raise PlannerResultInvalid("confidence is out of range", detail=repr(confidence))

    def _optional_text(name: str, limit: int) -> Optional[str]:
        value = payload.get(name)
        if value is None:
            return None
        if not isinstance(value, str):
            raise PlannerResultInvalid(f"{name} must be a string or null")
        if len(value) > limit:
            raise PlannerResultInvalid(f"{name} exceeds the bound")
        return value

    worker_prompt = _optional_text("worker_prompt", MAX_WORKER_PROMPT_CHARS)
    user_question = _optional_text("user_question", MAX_USER_QUESTION_CHARS)
    decision_basis = _optional_text("decision_basis", MAX_BASIS_CHARS)

    # -- cross-field semantics. The provider schema cannot express these, so a
    # -- result can be schema-valid and still mean something incoherent.
    if action == ACTION_ASK_USER:
        if not (user_question or "").strip():
            raise PlannerResultInvalid("ASK_USER requires a user_question")
        if worker_prompt is not None:
            # The dangerous shape: a question *and* something a caller might
            # dispatch anyway. One action, one artefact.
            raise PlannerResultInvalid("ASK_USER must not carry a worker_prompt")
    elif action == ACTION_PREPARE_WORKER_PROMPT:
        if not (worker_prompt or "").strip():
            raise PlannerResultInvalid(
                "PREPARE_WORKER_PROMPT requires a non-empty worker_prompt"
            )
        if user_question is not None:
            raise PlannerResultInvalid(
                "PREPARE_WORKER_PROMPT must not also ask the user"
            )
    elif action == ACTION_STOP:
        if worker_prompt is not None:
            raise PlannerResultInvalid("STOP must not carry a worker_prompt")
        if not (decision_basis or summary or "").strip():
            raise PlannerResultInvalid("STOP requires an explanation")

    return PlannerResult(
        schema_version=PLANNER_RESULT_SCHEMA_VERSION,
        action=action,
        summary=summary,
        confidence=confidence,
        worker_prompt=worker_prompt,
        user_question=user_question,
        decision_basis=decision_basis,
    )


def request_field_names() -> Tuple[str, ...]:
    """Used by tests to assert the request cannot grow an execution field."""
    return tuple(f.name for f in dataclass_fields(DevelopmentRequest))


def result_field_names() -> Tuple[str, ...]:
    return tuple(f.name for f in dataclass_fields(PlannerResult))


__all__ = [
    "ACTION_ASK_USER",
    "ACTION_PREPARE_WORKER_PROMPT",
    "ACTION_STOP",
    "PLANNER_ACTIONS",
    "PLANNER_RESULT_SCHEMA",
    "PLANNER_RESULT_SCHEMA_VERSION",
    "FORBIDDEN_RESULT_KEYS",
    "DevelopmentRequest",
    "PlannerResult",
    "validate_planner_result",
    "request_field_names",
    "result_field_names",
]
