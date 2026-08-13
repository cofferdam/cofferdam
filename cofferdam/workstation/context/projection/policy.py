"""What may leave the host, and the closed vocabulary for saying why not.

Separated from the projector for the reason :mod:`..policy` is separated from
the builder: these are **decisions**, and a change to one should look like a diff
to a policy file rather than an adjustment inside a loop. The difference is what
the two files decide. `..policy` answers *should this be in this pack* — a
question about usefulness. This file answers *may this leave the machine* — a
question about consequence, where the cost of being wrong is not a worse answer
but a permanent disclosure.

So the posture here is the opposite one. The context policy names what is
*eligible* and lets the mind's grant do the gating. This policy names what is
allowed and **denies everything else**, including source kinds and reference
schemes that do not exist yet.

Why the reference, not the kind
-------------------------------

`global:preferences` and `project:cofferdam:status` are **both**
:data:`..kinds.KIND_MEMORY`. A policy keyed on `source_kind` would therefore
publish the operator's personal preferences the first time somebody wrote
`if part.source_kind == KIND_MEMORY`, and the diff would look correct.

Eligibility is decided on the **decomposed semantic reference** — scheme,
identity, role — and the kind is then required to *agree* with it. Disagreement
is its own refusal (:data:`OMIT_SOURCE_KIND_MISMATCH`) rather than a fallthrough,
because a part whose kind and reference tell different stories is either a bug in
a producer or an attempt to smuggle, and neither should be resolved by guessing
which half to believe.

Why one narrow profile
----------------------

:data:`PROJECT_CONTEXT_EXTERNAL_V1` is a **profile**, not a framework. There is
no registry, no per-destination table, no caller-supplied allowlist and no way to
name a policy that does not exist in this file. D-2026-08-11-5 allows a workspace
policy to *later* permit selected global extracts; that is an opt-in naming what
may leave, and it is not built here. A generic policy engine built before its
second consumer would be configuration nobody audits, guarding the one thing that
most needs auditing.
"""

from __future__ import annotations

from typing import Dict, NamedTuple, Optional, Tuple

from ..kinds import (
    KIND_DECISION,
    KIND_MEMORY,
    KIND_PLAN,
    KIND_USER_INSTRUCTION,
    KIND_WORKING_STATE,
)

#: The initial — and only — egress profile. Versioned in the identifier itself,
#: so a projection that reaches a log or a future audit says which rules produced
#: it rather than which rules happened to be current when it was read.
PROJECT_CONTEXT_EXTERNAL_V1 = "project_context_external_v1"

#: The shape version of the projection, published like every other Cofferdam
#: payload so a consumer branches on a number rather than sniffing for keys.
PROJECTION_API_VERSION = 1

# -- the budget --------------------------------------------------------------

#: **16 KiB of UTF-8.** A quarter of the local pack's 64 KiB, and deliberately
#: not the same number: the local budget was chosen so a rich pack fits, and
#: reusing it would make "how much may the planner see" and "how much may leave
#: the machine" one setting that a later tuning change would widen by accident.
#:
#: The size is chosen against three constraints. It has to hold a real objective
#: plus bounded status, plan and decision material, which the caps below add up
#: to. It has to survive JSON escaping, base structural metadata and transport
#: framing with comfortable room left — a 16 KiB payload is unremarkable to any
#: HTTP body limit. And it must not encode a *provider's* wire limit: there is no
#: tokenizer here, no model, and no assumption about which destination a later
#: authorized surface picks (D-2026-08-11-5 makes projection destination-neutral).
DEFAULT_PROJECTION_BUDGET_BYTES = 16 * 1024

#: Per-slot ceilings, applied before the remaining total, so an early source
#: cannot consume the whole budget. Working context is the tightest because it is
#: four short fields; the project roles get the room because they are the point.
SOURCE_CAPS: Dict[str, int] = {
    "workspace:working_context": 2 * 1024,
    "project:status": 4 * 1024,
    "project:plan": 5 * 1024,
    "project:decisions": 5 * 1024,
}

# -- what is allowed ---------------------------------------------------------

#: Project roles eligible for egress. A closed list, and **narrower than the
#: pack's**: `design` is mapped and readable on the production host, is not in a
#: pack, and is not introduced here — adding a role to an egress policy because
#: the document happens to exist is the drift this file exists to make visible.
ALLOWED_PROJECT_ROLES: Tuple[str, ...] = ("status", "plan", "decisions")

#: The kind each allowed role's material **must** carry. A part that disagrees is
#: refused rather than corrected.
REQUIRED_PROJECT_KINDS: Dict[str, str] = {
    "status": KIND_MEMORY,
    "plan": KIND_PLAN,
    "decisions": KIND_DECISION,
}

#: The Working Context fields that may leave the host, projected from PR3's
#: structured `fields` and **never** from its rendered text — the rendered text
#: already contains `delegated worker:` and `active task:` lines, so parsing it
#: would be a re-derivation of exactly what this allowlist exists to drop.
#:
#: Each is here for a stated reason: what we are trying to do, what we think is
#: next, and the two references a person recorded by hand about where the work
#: stands. Together they are what an external surface needs to understand the
#: project's current state, and nothing more.
ALLOWED_WORKING_CONTEXT_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("objective", "objective"),
    ("expected next step", "expected_next_step"),
    ("plan checkpoint", "plan_checkpoint"),
    ("pending decision", "pending_decision_ref"),
)

#: Named so the exclusion is a decision with a reason rather than an omission
#: from a list. `delegated_worker`, `delegation` and `active_task` are adapter,
#: provider and Task Core internals; `revision` and the `objective_*` metadata
#: are Cofferdam's own bookkeeping; `latest_evidence_ref` names a system that
#: does not exist (M2K) and would be an unresolvable reference on the wire.
DENIED_WORKING_CONTEXT_FIELDS: Tuple[str, ...] = (
    "active_task",
    "delegated_worker",
    "delegation",
    "latest_evidence_ref",
    "objective_set_at",
    "objective_source",
    "project_id",
    "revision",
    "workspace_id",
)

#: The kind a denied scheme's material is expected to carry, so that a part whose
#: kind contradicts its own reference is reported as a contradiction rather than
#: as an ordinary policy exclusion. `evaluation:` is absent on purpose: nothing
#: produces one, so there is no kind it could be expected to have.
_DENIED_SCHEME_KINDS: Dict[str, str] = {
    "global": KIND_MEMORY,
    "user": KIND_USER_INSTRUCTION,
}

# -- why something is not here -----------------------------------------------

#: The source is not eligible under this profile. Global mind in all four roles,
#: the current user message, an unlisted project role, another project, another
#: workspace, and the evaluation slot all land here.
OMIT_POLICY_EXCLUDED = "policy_excluded"

#: The kind and the reference disagree — a `plan` part claiming `global:`
#: provenance, or a `working_state` part claiming a project role. Refused rather
#: than resolved in favour of either half.
OMIT_SOURCE_KIND_MISMATCH = "source_kind_mismatch"

#: The reference is well-formed for the pack and means nothing to this policy.
#: A scheme this profile cannot classify is denied, not passed through.
OMIT_SOURCE_REF_UNSUPPORTED = "source_ref_unsupported"

#: The text matched a credential pattern. The **whole part** is dropped rather
#: than rewritten: a clever lossy edit of material that might be a secret is a
#: guess about which bytes mattered, and being wrong once is permanent.
OMIT_SENSITIVE_CONTENT = "sensitive_content_omitted"

#: Nothing was left after redaction, or the source was blank to begin with.
OMIT_SOURCE_EMPTY = "source_empty"

#: No egress budget remained when this source's turn came.
OMIT_BUDGET_EXHAUSTED = "budget_exhausted"

#: An identical part was already projected. Recorded rather than skipped, so the
#: byte accounting and the part count still add up for a reader.
OMIT_DUPLICATE_PART = "duplicate_part"

OMISSION_REASONS: Tuple[str, ...] = (
    OMIT_POLICY_EXCLUDED,
    OMIT_SOURCE_KIND_MISMATCH,
    OMIT_SOURCE_REF_UNSUPPORTED,
    OMIT_SENSITIVE_CONTENT,
    OMIT_SOURCE_EMPTY,
    OMIT_BUDGET_EXHAUSTED,
    OMIT_DUPLICATE_PART,
)

#: The one transformation this policy permits on text that is otherwise allowed.
#: Recorded on the part itself, because "this content was altered" is something a
#: consumer must be able to see rather than infer.
REDACTION_PATH = "path_redacted"

REDACTIONS: Tuple[str, ...] = (REDACTION_PATH,)


class Eligibility(NamedTuple):
    """The policy's answer about one part. `slot` names its cap and nothing else."""

    allowed: bool
    slot: Optional[str] = None
    role: Optional[str] = None
    reason: Optional[str] = None


def _split(source_ref: str) -> Tuple[str, Tuple[str, ...]]:
    """Scheme and colon-separated identity, with any `#section` discarded.

    Discarding the section is safe and deliberate: `..kinds.semantic_ref` already
    restricted the whole reference to `[A-Za-z0-9_.:#-]`, and eligibility is a
    property of the document a section came from, never of the section.
    """
    body = source_ref.split("#", 1)[0]
    scheme, _, rest = body.partition(":")
    return scheme, tuple(part for part in rest.split(":") if part)


def classify(
    source_ref: str,
    source_kind: str,
    *,
    workspace_id: Optional[str],
    project_id: Optional[str],
) -> Eligibility:
    """Decide one part. **Denies by default**, including for unknown schemes.

    ``workspace_id`` and ``project_id`` come from the pack being projected, so a
    part naming a *different* workspace or project is refused: a projection
    describes one workspace, and a reference from somewhere else in the same pack
    is either a retrieval candidate that has not earned egress or a mistake.
    """
    scheme, identity = _split(source_ref)

    if scheme == "workspace":
        if len(identity) != 2 or identity[1] != "working_context":
            return Eligibility(False, reason=OMIT_SOURCE_REF_UNSUPPORTED)
        if source_kind != KIND_WORKING_STATE:
            return Eligibility(False, reason=OMIT_SOURCE_KIND_MISMATCH)
        if workspace_id is None or identity[0] != workspace_id:
            return Eligibility(False, reason=OMIT_POLICY_EXCLUDED)
        return Eligibility(True, slot="workspace:working_context")

    if scheme == "project":
        if len(identity) != 2:
            return Eligibility(False, reason=OMIT_SOURCE_REF_UNSUPPORTED)
        owner, role = identity
        if role not in ALLOWED_PROJECT_ROLES:
            return Eligibility(False, reason=OMIT_POLICY_EXCLUDED)
        if project_id is None or owner != project_id:
            return Eligibility(False, reason=OMIT_POLICY_EXCLUDED)
        if source_kind != REQUIRED_PROJECT_KINDS[role]:
            return Eligibility(False, reason=OMIT_SOURCE_KIND_MISMATCH)
        return Eligibility(True, slot="project:" + role, role=role)

    expected = _DENIED_SCHEME_KINDS.get(scheme)
    if expected is not None and source_kind != expected:
        return Eligibility(False, reason=OMIT_SOURCE_KIND_MISMATCH)
    if scheme in _DENIED_SCHEME_KINDS or scheme == "evaluation":
        return Eligibility(False, reason=OMIT_POLICY_EXCLUDED)
    return Eligibility(False, reason=OMIT_SOURCE_REF_UNSUPPORTED)


def cap_for(slot: str) -> int:
    return SOURCE_CAPS[slot]


__all__ = [
    "ALLOWED_PROJECT_ROLES",
    "ALLOWED_WORKING_CONTEXT_FIELDS",
    "DEFAULT_PROJECTION_BUDGET_BYTES",
    "DENIED_WORKING_CONTEXT_FIELDS",
    "Eligibility",
    "OMISSION_REASONS",
    "OMIT_BUDGET_EXHAUSTED",
    "OMIT_DUPLICATE_PART",
    "OMIT_POLICY_EXCLUDED",
    "OMIT_SENSITIVE_CONTENT",
    "OMIT_SOURCE_EMPTY",
    "OMIT_SOURCE_KIND_MISMATCH",
    "OMIT_SOURCE_REF_UNSUPPORTED",
    "PROJECTION_API_VERSION",
    "PROJECT_CONTEXT_EXTERNAL_V1",
    "REDACTIONS",
    "REDACTION_PATH",
    "REQUIRED_PROJECT_KINDS",
    "SOURCE_CAPS",
    "cap_for",
    "classify",
]
