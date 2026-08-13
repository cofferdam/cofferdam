"""The bounded object a later authorized surface may send, and nothing more.

`CloudContextProjection` is a **separate type** from
:class:`~..models.LocalContextPack` because D-2026-08-11-5 makes the boundary a
compile-time question rather than a review question. This module is where that
sentence becomes structural, so it is worth being exact about *why* the local
pack could not simply be serialized:

* :attr:`~..models.ContextPart.fields` carries the whole Working Context —
  ``delegated_worker``, ``delegation``, ``active_task`` with its canonical task
  id, status and state, and ``revision``. It exists so a local consumer can read
  absence as absence, and every one of those is a host internal.
* The working-state part's ``text`` is *rendered from those same fields*, so
  ``delegated worker:`` and ``active task:`` lines are already in the content. A
  projection that copied text would inherit them; this one re-renders from a
  field allowlist instead.
* :class:`~..models.SectionRef` carries ``heading`` — **raw document text**.
  ``section_id`` is slug-restricted and safe; the heading it came from is not.
  So no section object survives, and the slug rides in ``source_ref`` where the
  grammar already bounds it.
* A pack always holds ``user:current_message``, and on a granted host it holds
  global style and preference material.

None of that is a flaw in the pack. It is correct for a rich local object that
nothing can send, which is exactly why the object something *can* send is a
different one.

A projection is still a value
-----------------------------

Built, handed to one caller, dropped. No id, no store, no cache, no history and
no route — the same posture as the pack, for a stronger reason: a durable record
of what once left the host is a second copy of the thing the boundary exists to
bound.

Two serializations, on purpose
------------------------------

:meth:`~CloudContextProjection.to_dict` carries the content and is what a later
surface would send. :meth:`~CloudContextProjection.summary` carries **counts
only** — not even the source references, because a reference can carry a section
slug derived from a document heading, and a heading is somebody's words. It is
the one shape that is safe to log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..kinds import semantic_ref
from .policy import PROJECTION_API_VERSION, PROJECT_CONTEXT_EXTERNAL_V1
from .sanitizer import RESIDUAL_LIMITATIONS

#: UTF-8 bytes, the same unit the pack uses, for the same reason: defined by the
#: encoding rather than by a provider's tokenizer, and identical on every host.
BUDGET_UNIT = "utf8_bytes"


def encoded_length(text: str) -> int:
    return len(text.encode("utf-8"))


@dataclass(frozen=True, kw_only=True)
class CloudContextPart:
    """One piece of egress-eligible context.

    Keyword-only and validated at construction, like every other typed object in
    this package. What is *absent* is the design: no ``fields``, no ``section``,
    no filename, no heading — a part that cannot hold a host internal cannot leak
    one through a caller that forgot to strip it.
    """

    source_kind: str
    source_ref: str
    observed_at: str
    selection: str
    text: str
    redactions: Tuple[str, ...] = ()
    truncated: bool = False

    def __post_init__(self) -> None:
        semantic_ref(self.source_ref)

    @property
    def content_bytes(self) -> int:
        return encoded_length(self.text)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "observed_at": self.observed_at,
            "selection": self.selection,
            "text": self.text,
            "content_bytes": self.content_bytes,
            "redactions": list(self.redactions),
            "truncated": self.truncated,
        }


@dataclass(frozen=True, kw_only=True)
class ProjectionOmission:
    """Something the pack had and the projection does not, with a closed reason.

    ``detail`` is a bounded sentence about the **decision**, never about the
    content that caused it. The sensitive-content case is the one that matters:
    a detail quoting what matched would put the secret in the omission row, which
    is the failure the row exists to prevent.
    """

    source_ref: str
    reason: str
    source_kind: Optional[str] = None
    detail: Optional[str] = None

    def __post_init__(self) -> None:
        semantic_ref(self.source_ref)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "source_kind": self.source_kind,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True, kw_only=True)
class ProjectionBudget:
    """The egress bound. Its own number, never inherited from the local pack."""

    total: int
    consumed: int
    unit: str = BUDGET_UNIT

    @property
    def remaining(self) -> int:
        return self.total - self.consumed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unit": self.unit,
            "total": self.total,
            "consumed": self.consumed,
            "remaining": self.remaining,
        }


@dataclass(frozen=True, kw_only=True)
class CloudContextProjection:
    """Bounded project context, eligible to leave the host. **Not authorized to.**

    Eligibility and transmission are different questions and this object answers
    only the first. It performs no network activity, and holding one is not
    permission to send it: a surface that does still needs its own authentication,
    its own authorization, a named destination and — where the request has
    consequences — the user semantics that go with them.

    Keyword-only construction is deliberate. There is no positional form, no
    ``from_pack`` and no ``parse``: the only way to obtain one is
    :meth:`~.service.ContextProjector.project`, so "which policy produced this"
    always has an answer.
    """

    policy_id: str = PROJECT_CONTEXT_EXTERNAL_V1
    built_at: str
    workspace_id: Optional[str]
    project_id: Optional[str]
    parts: Tuple[CloudContextPart, ...]
    omissions: Tuple[ProjectionOmission, ...]
    budget: ProjectionBudget
    version: int = PROJECTION_API_VERSION
    limitations: Tuple[str, ...] = field(default_factory=lambda: LIMITATIONS)

    @property
    def redacted_parts(self) -> int:
        return sum(1 for part in self.parts if part.redactions)

    @property
    def truncated_parts(self) -> int:
        return sum(1 for part in self.parts if part.truncated)

    def to_dict(self) -> Dict[str, Any]:
        """The whole projection. What an authorized surface would actually send."""
        return {
            "version": self.version,
            "policy_id": self.policy_id,
            "built_at": self.built_at,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "budget": self.budget.to_dict(),
            "parts": [part.to_dict() for part in self.parts],
            "omissions": [omission.to_dict() for omission in self.omissions],
            "limitations": list(self.limitations),
        }

    def summary(self) -> Dict[str, Any]:
        """Counts and closed vocabulary only. **The one shape that is safe to log.**

        Narrower than :meth:`~..models.LocalContextPack.summary`, which lists its
        source references. A projection's references can carry a section slug, a
        slug is derived from a heading, and a heading is the operator's own words
        about their own project — so this reports *how many* of each kind and
        *which reasons* fired, and never which document said what.
        """
        kinds: List[str] = []
        for part in self.parts:
            if part.source_kind not in kinds:
                kinds.append(part.source_kind)
        reasons: Dict[str, int] = {}
        for omission in self.omissions:
            reasons[omission.reason] = reasons.get(omission.reason, 0) + 1
        return {
            "version": self.version,
            "policy_id": self.policy_id,
            "built_at": self.built_at,
            "parts": len(self.parts),
            "source_kinds": sorted(kinds),
            "redacted_parts": self.redacted_parts,
            "truncated_parts": self.truncated_parts,
            "budget": self.budget.to_dict(),
            "omissions": len(self.omissions),
            "omission_reasons": dict(sorted(reasons.items())),
        }


#: Carried on the object rather than only in the docs, the posture every other
#: Cofferdam payload takes. A consumer should never have to infer how much of a
#: security claim is real, and the sanitizer's limits are part of the claim.
LIMITATIONS: Tuple[str, ...] = (
    "This object is eligible to leave the host. It is not authorized to: transport is separate.",
    "Nothing here performed network activity. A projection is prepared, never sent.",
    "Global Mind is excluded in all four roles, including any present in the local pack.",
    "The current user message is excluded by this profile.",
    "Working Context is projected from an explicit field allowlist, never from rendered text.",
    "Recognised local paths are replaced and declared; credential-shaped parts are omitted whole.",
) + RESIDUAL_LIMITATIONS


__all__ = [
    "BUDGET_UNIT",
    "LIMITATIONS",
    "CloudContextPart",
    "CloudContextProjection",
    "ProjectionBudget",
    "ProjectionOmission",
    "encoded_length",
]
