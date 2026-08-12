"""The pack, its parts, and the accounting that keeps it honest.

`LocalContextPack` is a **value**. It is built, handed to one local caller, and
dropped. There is no store behind this module, no cache, no identifier, no
lifecycle and no route: the pack is not a record of anything, it is an answer to
one question asked once, and the moment it became durable it would be a second
copy of somebody's memory with none of the protections the first copy has.

The local boundary, said in the type system
-------------------------------------------

D-2026-08-11-5 makes the outbound object a **different type**, built by an
explicit egress policy. `CloudContextProjection` does not exist in this build.
Nothing here serializes to a provider, and :meth:`LocalContextPack.to_dict` is a
local diagnostic — the shape a test or a workstation-side reader consumes, not a
wire format. There is no method on this class that sends anything anywhere, and
no caller in the repository that forwards its result off the host.

Two serializations, on purpose
------------------------------

:meth:`~LocalContextPack.to_dict` carries the content. It is for the local
consumer.

:meth:`~LocalContextPack.summary` carries **no content at all** — counts, kinds,
references, byte totals and omission reasons — and is the only one of the two
that is safe to log. Splitting them means "log the pack" cannot be done by
accident: the method that would leak is not the method whose name suggests
logging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .errors import SourceKindInvalid
from .kinds import PRODUCIBLE_KINDS, SELECTIONS, semantic_ref

#: The version of the pack shape, published so a later consumer can branch on it
#: rather than sniffing for keys — the contract Task Core, the workspace payload
#: and the mind payload all publish.
CONTEXT_API_VERSION = 1

#: **UTF-8 bytes.** Deterministic, defined by the encoding rather than by any
#: model, identical on every host, and already this repository's unit for a
#: document (`MAX_DOCUMENT_BYTES`, `base_bytes`, the `bytes` field on every mind
#: payload). A token count would have been the wrong choice twice over: it would
#: make a model-free component depend on a provider's tokenizer, and it would
#: make the same pack cost a different amount on a different model.
BUDGET_UNIT = "utf8_bytes"


def encoded_length(text: str) -> int:
    """The one place a length is measured. Every count in a pack comes here."""
    return len(text.encode("utf-8"))


@dataclass(frozen=True)
class SectionRef:
    """Which part of a document a part came from. Semantic, never a location."""

    section_id: str
    heading: Optional[str]
    level: int
    ordinal: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section_id": self.section_id,
            "heading": self.heading,
            "level": self.level,
            "ordinal": self.ordinal,
        }


@dataclass(frozen=True)
class ContextPart:
    """One piece of context, with enough provenance to be judged.

    Validation happens in :meth:`__post_init__` rather than in the builder, so
    that the guarantees hold for *every* part however it was produced —
    including a candidate handed in by a future retrieval component, which is
    the one producer this package does not control.
    """

    source_kind: str
    source_ref: str
    observed_at: str
    selection: str
    text: str
    truncated: bool = False
    section: Optional[SectionRef] = None
    fields: Optional[Mapping[str, Any]] = None

    def __post_init__(self) -> None:
        if self.source_kind not in PRODUCIBLE_KINDS:
            raise SourceKindInvalid(
                "'"
                + str(self.source_kind)
                + "' has no authority in this build; producible kinds are "
                + ", ".join(PRODUCIBLE_KINDS)
            )
        if self.selection not in SELECTIONS:
            raise ValueError("unknown selection mode: " + str(self.selection))
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
            "truncated": self.truncated,
            "section": self.section.to_dict() if self.section is not None else None,
            "fields": dict(self.fields) if self.fields is not None else None,
        }


@dataclass(frozen=True)
class ContextOmission:
    """Something that is not in the pack, and why. Never silent.

    ``source_kind`` is optional because one omission is about a source kind that
    does not exist yet: the evaluation slot in the priority order. Giving it a
    kind would be inventing a vocabulary word for a system nobody has built.

    ``detail`` is a bounded sentence about the *state*, never about the content
    and never about a location.
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


@dataclass(frozen=True)
class ContextBudget:
    """What was allowed, what was used, what was left. Enforced in one place."""

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


@dataclass(frozen=True)
class RetrievedCandidate:
    """A part a retrieval component proposes. **Nothing produces one here.**

    This is the whole M2N extension boundary: one typed carrier, accepted by
    :meth:`~.builder.ContextBuilder.build`, subject to the same provenance
    validation, the same budget and the same ordering as everything else. It is
    not a plugin system and there is no registry — a component that has
    candidates passes them in, and a build with none is the ordinary case.
    """

    source_kind: str
    source_ref: str
    text: str
    section: Optional[SectionRef] = None


@dataclass(frozen=True)
class LocalContextPack:
    """Bounded local context, in priority order, with everything accounted for.

    **Local.** Rich on purpose, and rich only because it does not leave the
    host. See the module docstring and D-2026-08-11-5.
    """

    built_at: str
    workspace_id: Optional[str]
    project_id: Optional[str]
    parts: Tuple[ContextPart, ...]
    omissions: Tuple[ContextOmission, ...]
    budget: ContextBudget
    version: int = CONTEXT_API_VERSION
    limitations: Tuple[str, ...] = field(default_factory=lambda: LIMITATIONS)

    @property
    def truncated_parts(self) -> int:
        return sum(1 for part in self.parts if part.truncated)

    def to_dict(self) -> Dict[str, Any]:
        """The whole pack, content included. For a **local** consumer only."""
        return {
            "version": self.version,
            "built_at": self.built_at,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "budget": self.budget.to_dict(),
            "parts": [part.to_dict() for part in self.parts],
            "omissions": [omission.to_dict() for omission in self.omissions],
            "limitations": list(self.limitations),
        }

    def summary(self) -> Dict[str, Any]:
        """Structural facts only. **The one shape that is safe to log.**

        Deliberately excludes every byte of memory: no message, no objective, no
        document text, no heading. What is left is what an operator actually
        needs from a log line — how much context was assembled, out of what
        kinds, against what budget, and what was left out and why.
        """
        kinds: List[str] = []
        for part in self.parts:
            if part.source_kind not in kinds:
                kinds.append(part.source_kind)
        return {
            "version": self.version,
            "built_at": self.built_at,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "parts": len(self.parts),
            "source_kinds": sorted(kinds),
            "source_refs": [part.source_ref for part in self.parts],
            "truncated_parts": self.truncated_parts,
            "budget": self.budget.to_dict(),
            "omissions": [
                {"source_ref": omission.source_ref, "reason": omission.reason}
                for omission in self.omissions
            ],
        }


#: What this PR can and cannot say, carried in the pack rather than only in the
#: docs — the posture Task Core, the workspace payload and the mind payload all
#: take. A consumer should never have to infer how much of this is real.
LIMITATIONS: Tuple[str, ...] = (
    "This pack is local. There is no code path in this build that sends it anywhere.",
    "Selection is explicit or structural. There is no semantic retrieval yet (M2N).",
    "The budget is counted in UTF-8 bytes and is independent of any model or tokenizer.",
    "The current message is never trimmed; a message larger than the budget refuses instead.",
    "Global mind is limited to communication style and preferences by context policy.",
    "Nothing here evaluates anything: evidence and evaluation are M2K and do not exist.",
    "The pack is not stored, cached or reused. It describes the moment it was built.",
)


__all__ = [
    "BUDGET_UNIT",
    "CONTEXT_API_VERSION",
    "LIMITATIONS",
    "ContextBudget",
    "ContextOmission",
    "ContextPart",
    "LocalContextPack",
    "RetrievedCandidate",
    "SectionRef",
    "encoded_length",
]
