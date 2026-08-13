"""The one step that turns local context into something eligible to leave.

`LocalContextPack` → :func:`~.policy.classify` → :func:`~.sanitizer.sanitize` →
`CloudContextProjection`. There is no other route, no shortcut for a trusted
caller and no flag that widens the policy from outside this package.

What the projector deliberately is not
--------------------------------------

**Not a reader.** It holds no `MindService`, no `WorkspaceService`, no root and
no path. It sees exactly the parts the Context Builder already selected, which
means an excluded Global Mind role is not *re-read and then filtered* — it is
unreachable, the same property :mod:`..builder` has about an unmapped vault note.
Projection is `pack → policy → projection`, never `projector → filesystem`.

**Not configurable by a caller.** ``project()`` takes a pack and an optional
smaller budget. It does not take an allowlist, a source kind, a redaction rule, a
secret pattern, a destination, or a flag that includes global memory. Those are
code-owned, because a remote caller that could name what it wanted to receive
would be the authority on its own permissions.

**Not a sender.** No socket, no client, no serializer to a wire format, no
destination. The output is a Python object. D-2026-08-11-5's separation of
*eligibility* from *transport* is why a surface cannot acquire the second by
importing the first.

**Not a writer.** No proposal, no Working Context field, no Markdown, no cache
and no projection history. Preparing context for egress must not change the state
that was read to prepare it.

The M2N re-check
----------------

A retrieval candidate reaches a pack as an ordinary `ContextPart`, so it reaches
this loop as one too, and it is classified by the same
:func:`~.policy.classify` call as everything else. There is no candidate branch,
no "already vetted" flag and no way for a retrieval component to mark its own
output cloud-safe. Admission to a pack and admission to a projection are decided
twice, by two policies, from two different questions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, List, Optional, Set, Tuple

from ..models import LocalContextPack
from .errors import ProjectionBudgetInvalid, ProjectionInputInvalid
from .model import (
    CloudContextPart,
    CloudContextProjection,
    ProjectionBudget,
    ProjectionOmission,
    encoded_length,
)
from .policy import (
    ALLOWED_WORKING_CONTEXT_FIELDS,
    DEFAULT_PROJECTION_BUDGET_BYTES,
    OMIT_BUDGET_EXHAUSTED,
    OMIT_DUPLICATE_PART,
    OMIT_SENSITIVE_CONTENT,
    OMIT_SOURCE_EMPTY,
    PROJECT_CONTEXT_EXTERNAL_V1,
    cap_for,
    classify,
)
from .sanitizer import HostRedactionEnvironment, sanitize

#: The longest single allowlisted Working Context value that will be rendered.
#: The objective is already bounded by the workspace service; this is the second
#: bound, so a field that a later PR widens cannot widen the projection with it.
MAX_FIELD_CHARS = 1000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _bounded(text: str, limit: int) -> Tuple[str, bool]:
    """Cut to fit the limit in bytes, on a character boundary. Never summarised."""
    if limit <= 0:
        return "", True
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def _working_context_text(fields: object) -> str:
    """Re-render the Working Context from **structured fields**, allowlist only.

    PR3 supplies `fields` alongside the rendered text precisely so a consumer does
    not have to parse a rendering, and this is the consumer that must not. A field
    that is missing, ``None`` or not a string produces no line at all — the same
    rule the builder follows, and the reason a malformed snapshot degrades to less
    context rather than to an exception or to a printed ``None``.
    """
    if not isinstance(fields, dict):
        return ""
    lines: List[str] = []
    for label, key in ALLOWED_WORKING_CONTEXT_FIELDS:
        value = fields.get(key)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if not value:
            continue
        lines.append(label + ": " + value[:MAX_FIELD_CHARS])
    return "\n".join(lines)


class ContextProjector:
    """Applies one named egress policy to one pack. Reads and transforms only.

    ``redaction`` is **required**. A projector built without the host's own root
    values would still apply the generic path patterns and would still look like
    it worked, so the argument has no default: a caller that knows the values
    cannot silently fail to supply them, and a caller that genuinely has none
    writes :meth:`~.sanitizer.HostRedactionEnvironment.none` and means it.

    ``clock`` is injectable for the same reason it is on the builder — so a test
    can freeze the build time and assert that two projections of one frozen pack
    are byte-identical. It is the only non-determinism in the component.
    """

    def __init__(
        self,
        *,
        redaction: HostRedactionEnvironment,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        if not isinstance(redaction, HostRedactionEnvironment):
            raise ProjectionInputInvalid("a redaction environment is required")
        self._redaction = redaction
        self._clock = clock or _utc_now

    def project(
        self,
        pack: object,
        *,
        budget_bytes: Optional[int] = None,
    ) -> CloudContextProjection:
        """Produce the bounded object a later authorized surface may send.

        Every part of the pack ends in exactly one of two lists. There is no
        third outcome and nothing is skipped: a part that is neither projected
        nor explained would be a silent policy decision, which is the one thing a
        boundary must never make.
        """
        if not isinstance(pack, LocalContextPack):
            raise ProjectionInputInvalid("expected a LocalContextPack")

        total = DEFAULT_PROJECTION_BUDGET_BYTES if budget_bytes is None else budget_bytes
        if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
            raise ProjectionBudgetInvalid("the budget must be a positive integer")

        parts: List[CloudContextPart] = []
        omissions: List[ProjectionOmission] = []
        seen: Set[Tuple[str, str, str, str]] = set()
        consumed = 0

        for part in pack.parts:
            verdict = classify(
                part.source_ref,
                part.source_kind,
                workspace_id=pack.workspace_id,
                project_id=pack.project_id,
            )
            if not verdict.allowed:
                omissions.append(
                    ProjectionOmission(
                        source_ref=part.source_ref,
                        source_kind=part.source_kind,
                        reason=verdict.reason,
                        detail="this source is not eligible for egress under " + PROJECT_CONTEXT_EXTERNAL_V1,
                    )
                )
                continue

            identity = (part.source_kind, part.source_ref, part.selection, part.text)
            if identity in seen:
                omissions.append(
                    ProjectionOmission(
                        source_ref=part.source_ref,
                        source_kind=part.source_kind,
                        reason=OMIT_DUPLICATE_PART,
                        detail="an identical part was already projected",
                    )
                )
                continue
            seen.add(identity)

            # The working state is rebuilt from its structured fields. Every
            # other source projects the text the builder already selected.
            if verdict.slot == "workspace:working_context":
                source_text = _working_context_text(part.fields)
            else:
                source_text = part.text

            cleaned = sanitize(source_text, self._redaction)
            if cleaned.sensitive:
                omissions.append(
                    ProjectionOmission(
                        source_ref=part.source_ref,
                        source_kind=part.source_kind,
                        reason=OMIT_SENSITIVE_CONTENT,
                        detail="the material matched a credential shape and was not rewritten",
                    )
                )
                continue

            if not cleaned.text.strip():
                omissions.append(
                    ProjectionOmission(
                        source_ref=part.source_ref,
                        source_kind=part.source_kind,
                        reason=OMIT_SOURCE_EMPTY,
                        detail="nothing remained for this source",
                    )
                )
                continue

            limit = min(cap_for(verdict.slot), total - consumed)
            bounded, truncated = _bounded(cleaned.text, limit)
            if not bounded.strip():
                omissions.append(
                    ProjectionOmission(
                        source_ref=part.source_ref,
                        source_kind=part.source_kind,
                        reason=OMIT_BUDGET_EXHAUSTED,
                        detail="no egress budget remained when this source's turn came",
                    )
                )
                continue

            parts.append(
                CloudContextPart(
                    source_kind=part.source_kind,
                    source_ref=part.source_ref,
                    observed_at=part.observed_at,
                    selection=part.selection,
                    text=bounded,
                    redactions=cleaned.redactions,
                    truncated=truncated or part.truncated,
                )
            )
            consumed += encoded_length(bounded)

        return CloudContextProjection(
            policy_id=PROJECT_CONTEXT_EXTERNAL_V1,
            built_at=self._clock(),
            workspace_id=pack.workspace_id,
            project_id=pack.project_id,
            parts=tuple(parts),
            omissions=tuple(omissions),
            budget=ProjectionBudget(total=total, consumed=consumed),
        )


__all__ = ["MAX_FIELD_CHARS", "ContextProjector"]
