"""The provider-neutral planner role, and why it is not a ``TaskAdapter``.

``TaskAdapter`` is the interface for something that *makes a thing happen*: it
returns an ``AdapterOutcome`` carrying a ``requested_state``, it declares
``cancel`` and ``recover_after_restart``, and the core validates its state
requests against the lifecycle graph. Every one of those exists because an
adapter owns a running process.

A planner owns nothing. It has no lifecycle to move, no process to cancel and
nothing to reattach to after a restart. Expressing it as a ``TaskAdapter``
would give it a structural way to *ask for* ``STATE_RUNNING`` — a shape that
implies authority it must never have, on the same interface Task Core validates
state transitions from. D-2026-08-20-2 names this as the boundary that must not
blur, so the role gets its own small protocol instead.

What is reused rather than reinvented: the ``CloudContextProjection`` egress
boundary, the code-owned-registry pattern from ``tasks/adapters``, and the
convention that capability is declared rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .models import DevelopmentRequest, PlannerResult


@dataclass(frozen=True)
class ProviderExecution:
    """What the *provider process* reported. Never model-authored.

    Kept separate from :class:`~.models.PlannerResult` on purpose: one is
    Cofferdam's own subprocess describing its run, the other is untrusted model
    output. Merging them would make provenance indistinguishable from content.
    """

    provider_id: str
    requested_model: Optional[str] = None
    #: What actually served the request, when the provider reports it. May
    #: differ from ``requested_model`` — an alias resolves, or a provider falls
    #: back — so both are recorded rather than assumed equal.
    actual_model: Optional[str] = None
    #: Every model the run touched. Providers may make side calls; attributing
    #: the result to one model while a second did work would be untrue.
    models_used: Tuple[str, ...] = ()
    session_id: Optional[str] = None
    duration_ms: Optional[int] = None
    ttft_ms: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    #: Provider-reported and documented by Anthropic as a client-side estimate
    #: that "can differ from your actual bill". Named so nobody later mistakes
    #: it for accounting.
    provider_reported_cost_estimate_usd: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "requested_model": self.requested_model,
            "actual_model": self.actual_model,
            "models_used": list(self.models_used),
            "session_id": self.session_id,
            "duration_ms": self.duration_ms,
            "ttft_ms": self.ttft_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "provider_reported_cost_estimate_usd": (
                self.provider_reported_cost_estimate_usd
            ),
        }


@dataclass(frozen=True)
class PlanningTurn:
    """One planning turn: what the model said, and what the process reported."""

    result: PlannerResult
    execution: ProviderExecution


@dataclass(frozen=True)
class PlannerCapabilities:
    """Declared, not assumed — the same rule the task adapters follow."""

    prepare_development_step: bool = False
    #: True when the provider constrains the model's own output to a schema
    #: rather than only wrapping free text. Host validation runs either way.
    provider_schema_enforcement: bool = False
    #: True when the provider can be invoked with no tools at all, enforced by
    #: the provider rather than by asking the model nicely.
    enforced_no_tools: bool = False

    def to_dict(self) -> Dict[str, bool]:
        return {
            "prepare_development_step": self.prepare_development_step,
            "provider_schema_enforcement": self.provider_schema_enforcement,
            "enforced_no_tools": self.enforced_no_tools,
        }


class DevelopmentPlanner:
    """The role. One operation in PR1c-a, and no way to execute anything.

    There is deliberately no ``dispatch``, no ``run_worker``, no ``cancel`` and
    no tool surface. A planner returns a :class:`PlanningTurn` or raises a
    :mod:`~.errors` failure; it has no third option, and in particular it has no
    option that runs something.
    """

    #: Stable, lowercase, code-owned. Never a vendor name in core logic — the
    #: value lives in the provider implementation.
    planner_id = "planner"

    def capabilities(self) -> PlannerCapabilities:
        return PlannerCapabilities()

    def available(self) -> bool:
        return False

    def unavailable_reason(self) -> Optional[str]:
        return "this planner is not configured"

    def prepare_development_step(self, request: DevelopmentRequest) -> PlanningTurn:
        """Read a bounded request, reason, return a typed result.

        Raises rather than returning a degraded answer: a provider failure is
        not an ``ASK_USER``, and a malformed result is not a ``STOP``.
        """
        raise NotImplementedError("this planner cannot prepare a development step")

    def describe(self) -> Dict[str, Any]:
        return {
            "planner_id": self.planner_id,
            "capabilities": self.capabilities().to_dict(),
            "available": self.available(),
        }


__all__ = [
    "DevelopmentPlanner",
    "PlannerCapabilities",
    "PlanningTurn",
    "ProviderExecution",
]
