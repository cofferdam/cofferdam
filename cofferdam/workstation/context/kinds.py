"""The three closed vocabularies a pack is written in, and one grammar.

Everything a :class:`~.models.ContextPart` says about itself comes from here:
*what kind of truth is this*, *how was it chosen*, and *where did it come from*.
All three are code-owned words rather than free text, for the reason
:mod:`..mind.roles` gives about roles — a vocabulary a caller can extend is a
vocabulary nobody downstream can branch on.

Why nine source kinds when this build produces five
---------------------------------------------------

The nine are the recorded vocabulary (`ROADMAP.md`, M2J PR3). Declaring all of
them and making four **unreachable** is the same posture the workspace store
takes with its `planner` source word: the vocabulary is stable from the start,
so the first component that has a genuine `worker_result` does not get to invent
a tenth word for it, and until that component exists no part can claim to be
one. :data:`PRODUCIBLE_KINDS` is enforced in :class:`~.models.ContextPart`, so
"unreachable" is a construction error rather than a convention.

Why `source_ref` is a grammar rather than a path
------------------------------------------------

A reference answers *which authority produced this*, and the authority is a
role, not a location. `project:cofferdam:plan#m2j` is stable across a moved
checkout, an A/B slot flip and a renamed file, and it tells a reader nothing
about the host's filesystem — which is exactly the trade the role model makes
everywhere else in this product. :func:`semantic_ref` refuses anything with a
separator, a home marker or a parent segment in it, at construction, so a
reference that could leak a location never exists to be filtered later.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# -- what kind of truth ------------------------------------------------------

#: The person's own words, this turn. Never trimmed, never reordered.
KIND_USER_INSTRUCTION = "user_instruction"

#: Cofferdam's own durable state — the active workspace and its Working Context.
KIND_WORKING_STATE = "working_state"

#: Project plan material.
KIND_PLAN = "plan"

#: A recorded decision.
KIND_DECISION = "decision"

#: Canonical Markdown memory that is neither plan nor decision — project status
#: and the granted vault's style and preference documents.
KIND_MEMORY = "memory"

#: Reserved. What a delegated worker *claimed*, which M2K makes a first-class
#: distinction from what Cofferdam observed (D-2026-08-11-6).
KIND_WORKER_RESULT = "worker_result"

#: Reserved. What Cofferdam observed itself. M2K.
KIND_MACHINE_OBSERVED = "machine_observed"

#: Reserved. Text from a web page or another model: data with provenance, never
#: instructions (`DESIGN.md`).
KIND_EXTERNAL_MODEL_OUTPUT = "external_model_output"

#: Reserved. Something the local planner inferred. M2L.
KIND_PLANNER_INFERENCE = "planner_inference"

SOURCE_KINDS: Tuple[str, ...] = (
    KIND_USER_INSTRUCTION,
    KIND_WORKING_STATE,
    KIND_PLAN,
    KIND_DECISION,
    KIND_MEMORY,
    KIND_WORKER_RESULT,
    KIND_MACHINE_OBSERVED,
    KIND_EXTERNAL_MODEL_OUTPUT,
    KIND_PLANNER_INFERENCE,
)

#: The five this build has an authority for. The other four have no producer,
#: and a part claiming one is refused rather than accepted and rendered.
PRODUCIBLE_KINDS: Tuple[str, ...] = (
    KIND_USER_INSTRUCTION,
    KIND_WORKING_STATE,
    KIND_PLAN,
    KIND_DECISION,
    KIND_MEMORY,
)

# -- how it was chosen -------------------------------------------------------

#: The whole thing, unselected — the user's message and the Working Context.
SELECTION_WHOLE = "whole"

#: A section a **stored reference chose by name**. The strongest signal PR3 has,
#: and the only one that reflects an intention somebody recorded.
SELECTION_EXPLICIT = "explicit"

#: A bounded prefix (or, for an append-ordered document, suffix) of the source.
#: Deterministic and honest about being structural: this is *not* relevance, and
#: nothing in this package pretends it is.
SELECTION_STRUCTURAL = "structural"

#: Supplied by a retrieval component as a candidate. **No producer exists in
#: this build** — the word is here so M2N's candidates arrive labelled as what
#: they are rather than indistinguishable from a direct read.
SELECTION_RETRIEVED = "retrieved"

SELECTIONS: Tuple[str, ...] = (
    SELECTION_WHOLE,
    SELECTION_EXPLICIT,
    SELECTION_STRUCTURAL,
    SELECTION_RETRIEVED,
)

# -- why something is not here -----------------------------------------------

#: Nothing is selected because no workspace is active. An ordinary state.
OMIT_NO_ACTIVE_WORKSPACE = "no_active_workspace"

#: The role is not mapped, or the project behind it is missing or disabled.
#: There is no file, and Cofferdam never guesses one.
OMIT_SOURCE_ABSENT = "source_absent"

#: There is no global vault grant, or it is not turned on.
OMIT_GRANT_ABSENT = "grant_absent"

#: Mapped, and unreadable right now — missing, not an ordinary file, past the
#: read bound, or not UTF-8. Deliberately one reason, for the same purpose
#: `mind_role_unavailable` is one code: the alternatives are all facts about the
#: host's filesystem.
OMIT_SOURCE_UNREADABLE = "source_unreadable"

#: Readable and blank. Not counted as context, because an empty part would
#: consume a priority slot and say nothing.
OMIT_SOURCE_EMPTY = "source_empty"

#: A stored reference named a section that is not in the document. The role is
#: omitted rather than replaced by a structural pick: a substitute would answer
#: a question nobody asked, and would look identical afterwards.
OMIT_EXPLICIT_SECTION_MISSING = "explicit_section_missing"

#: There was no budget left by the time this source's turn came.
OMIT_BUDGET_EXHAUSTED = "budget_exhausted"

#: The priority order has a slot for this and **the system that would fill it
#: does not exist yet**. Recorded rather than skipped so that "there is no
#: evaluation here" is a statement rather than an absence somebody has to
#: interpret. Nothing is fabricated to fill it.
OMIT_NOT_IN_THIS_BUILD = "source_not_in_this_build"

OMISSION_REASONS: Tuple[str, ...] = (
    OMIT_NO_ACTIVE_WORKSPACE,
    OMIT_SOURCE_ABSENT,
    OMIT_GRANT_ABSENT,
    OMIT_SOURCE_UNREADABLE,
    OMIT_SOURCE_EMPTY,
    OMIT_EXPLICIT_SECTION_MISSING,
    OMIT_BUDGET_EXHAUSTED,
    OMIT_NOT_IN_THIS_BUILD,
)

# -- the reference grammar ---------------------------------------------------

#: ``scheme:body``, where the body may carry further colon-separated identity
#: and an optional ``#section``. No separator, no home marker, no parent
#: segment, no scheme this package did not write.
_REF = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9_.:#-]+$")

_REF_FORBIDDEN = ("/", "\\", "~", "$", "..", " ")

#: The schemes that exist. A reference is refused if its scheme is not one of
#: these, so a candidate cannot introduce `file:` or `https:` by spelling it
#: correctly.
REF_SCHEMES: Tuple[str, ...] = ("user", "workspace", "project", "global", "evaluation")

MAX_REF_CHARS = 200


def semantic_ref(value: object) -> str:
    """Validate a source reference, or refuse. Never rewrites one.

    Refusing rather than sanitising is the same choice :func:`..mind.roles.valid_role`
    makes: a cleaned path is still something that came from a path, and the
    property worth having is that no reference anywhere in a pack was ever
    derived from a location.
    """
    from .errors import SourceRefInvalid

    if not isinstance(value, str):
        raise SourceRefInvalid("expected a reference of the form 'scheme:identity'")
    if not value or len(value) > MAX_REF_CHARS:
        raise SourceRefInvalid("a reference is between 1 and " + str(MAX_REF_CHARS) + " characters")
    for marker in _REF_FORBIDDEN:
        if marker in value:
            raise SourceRefInvalid("a reference names a role, never a location")
    if not _REF.match(value):
        raise SourceRefInvalid("a reference names a role, never a location")
    if value.split(":", 1)[0] not in REF_SCHEMES:
        raise SourceRefInvalid("'" + value.split(":", 1)[0] + "' is not a reference scheme")
    return value


def user_ref() -> str:
    return "user:current_message"


def working_context_ref(workspace_id: str) -> str:
    return semantic_ref("workspace:" + workspace_id + ":working_context")


def project_ref(project_id: str, role: str, section_id: Optional[str] = None) -> str:
    base = "project:" + project_id + ":" + role
    return semantic_ref(base + "#" + section_id if section_id else base)


def global_ref(role: str, section_id: Optional[str] = None) -> str:
    base = "global:" + role
    return semantic_ref(base + "#" + section_id if section_id else base)


__all__ = [
    "KIND_DECISION",
    "KIND_EXTERNAL_MODEL_OUTPUT",
    "KIND_MACHINE_OBSERVED",
    "KIND_MEMORY",
    "KIND_PLAN",
    "KIND_PLANNER_INFERENCE",
    "KIND_USER_INSTRUCTION",
    "KIND_WORKER_RESULT",
    "KIND_WORKING_STATE",
    "MAX_REF_CHARS",
    "OMISSION_REASONS",
    "OMIT_BUDGET_EXHAUSTED",
    "OMIT_EXPLICIT_SECTION_MISSING",
    "OMIT_GRANT_ABSENT",
    "OMIT_NOT_IN_THIS_BUILD",
    "OMIT_NO_ACTIVE_WORKSPACE",
    "OMIT_SOURCE_ABSENT",
    "OMIT_SOURCE_EMPTY",
    "OMIT_SOURCE_UNREADABLE",
    "PRODUCIBLE_KINDS",
    "REF_SCHEMES",
    "SELECTIONS",
    "SELECTION_EXPLICIT",
    "SELECTION_RETRIEVED",
    "SELECTION_STRUCTURAL",
    "SELECTION_WHOLE",
    "SOURCE_KINDS",
    "global_ref",
    "project_ref",
    "semantic_ref",
    "user_ref",
    "working_context_ref",
]
