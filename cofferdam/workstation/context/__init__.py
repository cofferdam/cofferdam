"""Deterministic local context assembly: the `LocalContextPack` and its builder.

Answers one question — *given the current user message and Cofferdam's current
state, what bounded **local** context is appropriate to make available to a
future local planner?* — and answers it without a model, a network call, an
index or a guess.

What a `LocalContextPack` is
----------------------------

An ordered, bounded, fully-accounted list of typed parts. Each part says what
kind of truth it is, where it came from as a **semantic reference**, when it was
observed, and how it was selected. Alongside them the pack carries the budget it
was built under and a row for every source that is *not* in it, with a reason.

It is a value, not a record: nothing stores it, caches it or reuses it.

What it is not
--------------

**Not a prompt.** There is no system string, no message array, no role field, no
template and no provider anywhere in this package. Turning context into a
request is the planner's job (M2L) and it does not exist.

**Not a `CloudContextProjection`.** D-2026-08-11-5 makes the pack that leaves the
host a separate type built by an explicit egress policy. That type is not in
this build, and this package adds no path toward one — which is what makes the
local pack safe to be rich.

**Not retrieval.** Selection is a reference somebody recorded, or a bounded
structural slice, and every part is labelled with which. D-2026-08-12-4 makes
semantic retrieval a *required* Mind capability, and requiring it is exactly why
it is not faked here: a keyword heuristic wearing the word "relevant" would be
believed by everything downstream. M2N supplies real candidates through
:class:`~.models.RetrievedCandidate`, which
:meth:`~.builder.ContextBuilder.build` already accepts.

**Not a filesystem authority.** Project memory is read by **role** through
:class:`~..mind.service.MindService`; state is read through
:class:`~..workspace.service.WorkspaceService`. This package holds no path, no
root and no reader of its own, so an unmapped note, `.obsidian/` and anything
outside a granted root are unreachable rather than filtered.

The budget is UTF-8 bytes
-------------------------

Deterministic, model-independent, and already this repository's unit for a
document. A tokenizer would have made a model-free component depend on a
provider, and would have made the same pack cost different amounts on different
models. See :data:`~.models.BUDGET_UNIT`.
"""

from .builder import MAX_MESSAGE_BYTES, ContextBuilder
from .errors import (
    CODE_BUDGET_INVALID,
    CODE_CURRENT_MESSAGE_INVALID,
    CODE_CURRENT_MESSAGE_OVERSIZE,
    CODE_SOURCE_KIND_INVALID,
    CODE_SOURCE_REF_INVALID,
    ContextBudgetInvalid,
    ContextError,
    CurrentMessageInvalid,
    CurrentMessageOversize,
    SourceKindInvalid,
    SourceRefInvalid,
)
from .kinds import (
    KIND_DECISION,
    KIND_EXTERNAL_MODEL_OUTPUT,
    KIND_MACHINE_OBSERVED,
    KIND_MEMORY,
    KIND_PLAN,
    KIND_PLANNER_INFERENCE,
    KIND_USER_INSTRUCTION,
    KIND_WORKER_RESULT,
    KIND_WORKING_STATE,
    OMISSION_REASONS,
    OMIT_BUDGET_EXHAUSTED,
    OMIT_EXPLICIT_SECTION_MISSING,
    OMIT_GRANT_ABSENT,
    OMIT_NO_CURRENT_MESSAGE,
    OMIT_NOT_IN_THIS_BUILD,
    OMIT_NO_ACTIVE_WORKSPACE,
    OMIT_SOURCE_ABSENT,
    OMIT_SOURCE_EMPTY,
    OMIT_SOURCE_UNREADABLE,
    PRODUCIBLE_KINDS,
    SELECTIONS,
    SELECTION_EXPLICIT,
    SELECTION_RETRIEVED,
    SELECTION_STRUCTURAL,
    SELECTION_WHOLE,
    SOURCE_KINDS,
)
from .models import (
    BUDGET_UNIT,
    CONTEXT_API_VERSION,
    LIMITATIONS,
    ContextBudget,
    ContextOmission,
    ContextPart,
    LocalContextPack,
    RetrievedCandidate,
    SectionRef,
)
from .policy import (
    DEFAULT_TOTAL_BUDGET_BYTES,
    GLOBAL_CONTEXT_ROLES,
    PROJECT_CONTEXT_ROLES,
    SOURCE_CAPS,
)

__all__ = [
    "BUDGET_UNIT",
    "CODE_BUDGET_INVALID",
    "CODE_CURRENT_MESSAGE_INVALID",
    "CODE_CURRENT_MESSAGE_OVERSIZE",
    "CODE_SOURCE_KIND_INVALID",
    "CODE_SOURCE_REF_INVALID",
    "CONTEXT_API_VERSION",
    "DEFAULT_TOTAL_BUDGET_BYTES",
    "GLOBAL_CONTEXT_ROLES",
    "KIND_DECISION",
    "KIND_EXTERNAL_MODEL_OUTPUT",
    "KIND_MACHINE_OBSERVED",
    "KIND_MEMORY",
    "KIND_PLAN",
    "KIND_PLANNER_INFERENCE",
    "KIND_USER_INSTRUCTION",
    "KIND_WORKER_RESULT",
    "KIND_WORKING_STATE",
    "LIMITATIONS",
    "MAX_MESSAGE_BYTES",
    "OMISSION_REASONS",
    "OMIT_BUDGET_EXHAUSTED",
    "OMIT_EXPLICIT_SECTION_MISSING",
    "OMIT_GRANT_ABSENT",
    "OMIT_NO_CURRENT_MESSAGE",
    "OMIT_NOT_IN_THIS_BUILD",
    "OMIT_NO_ACTIVE_WORKSPACE",
    "OMIT_SOURCE_ABSENT",
    "OMIT_SOURCE_EMPTY",
    "OMIT_SOURCE_UNREADABLE",
    "PRODUCIBLE_KINDS",
    "PROJECT_CONTEXT_ROLES",
    "SELECTIONS",
    "SELECTION_EXPLICIT",
    "SELECTION_RETRIEVED",
    "SELECTION_STRUCTURAL",
    "SELECTION_WHOLE",
    "SOURCE_CAPS",
    "SOURCE_KINDS",
    "ContextBudget",
    "ContextBuilder",
    "ContextBudgetInvalid",
    "ContextError",
    "ContextOmission",
    "ContextPart",
    "CurrentMessageInvalid",
    "CurrentMessageOversize",
    "LocalContextPack",
    "RetrievedCandidate",
    "SectionRef",
    "SourceKindInvalid",
    "SourceRefInvalid",
]
