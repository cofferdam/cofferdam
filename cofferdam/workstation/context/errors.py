"""Refusals the Context Builder makes, with stable codes to branch on.

The same shape as :mod:`..mind.errors` and :mod:`..workspace.errors`, and the
same two rules: **no message carries a path**, and each code says which
authority refused.

There are only five, and that is the point. Almost everything that can go wrong
while assembling context is *not* an error — an unmapped role, a revoked grant,
a document nobody wrote, a budget that ran out — because each of those is a real
state of a working host, and the honest response is a pack that says so. Those
are :class:`~.models.ContextOmission` rows, not exceptions.

What is left are the five cases where producing a pack at all would be a lie:
the user's own sentence is missing or unusable, it does not fit and would have
to be trimmed, the budget itself is nonsense, or a caller-supplied candidate
carries provenance this package refuses to publish.
"""

from __future__ import annotations

from typing import Optional

#: The current user message is absent, empty, not text, or carries a NUL. The
#: pack's highest-priority part cannot be built, so no pack is built.
CODE_CURRENT_MESSAGE_INVALID = "context_current_message_invalid"

#: **The message alone is larger than the whole budget.** Refused rather than
#: trimmed, and refused rather than partially answered. Trimming would show a
#: planner a sentence the person did not write, which is the rule the workspace
#: objective and the proposal reason already follow; returning a pack without it
#: would present the incomplete thing as complete. There is no model here to
#: shorten it and there is not supposed to be.
CODE_CURRENT_MESSAGE_OVERSIZE = "context_current_message_oversize"

#: The budget is not a positive integer number of UTF-8 bytes. Refused rather
#: than clamped: a caller that asked for a budget Cofferdam silently replaced
#: would report the wrong bound in its own logs.
CODE_BUDGET_INVALID = "context_budget_invalid"

#: A supplied `source_ref` is not a semantic address. This is what a filesystem
#: path in a retrieval candidate produces, and it is refused at construction
#: rather than filtered at serialization — a reference that never exists cannot
#: leak from somewhere the filter was not applied.
CODE_SOURCE_REF_INVALID = "context_source_ref_invalid"

#: A supplied `source_kind` is not a word this build can honestly produce.
#: Reserved kinds (`worker_result`, `machine_observed`, `external_model_output`,
#: `planner_inference`) are declared in the vocabulary and unreachable until the
#: systems that would be their authority exist.
CODE_SOURCE_KIND_INVALID = "context_source_kind_invalid"


class ContextError(Exception):
    """A refusal with a stable code, message and optional detail."""

    def __init__(self, code: str, message: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class CurrentMessageInvalid(ContextError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_CURRENT_MESSAGE_INVALID,
            "there is no current message to build context around",
            detail or "expected one non-empty line of text",
        )


class CurrentMessageOversize(ContextError):
    """Refused, and deliberately without a pack attached.

    A partial pack would be the failure this refusal exists to prevent, one
    layer down: something would render it, and nothing in it would say that the
    part it was built around is missing.
    """

    def __init__(self, message_bytes: int, budget_bytes: int) -> None:
        super().__init__(
            CODE_CURRENT_MESSAGE_OVERSIZE,
            "the message is larger than the whole context budget",
            str(message_bytes)
            + " bytes against a budget of "
            + str(budget_bytes)
            + " bytes; the message is never trimmed to fit",
        )
        self.message_bytes = message_bytes
        self.budget_bytes = budget_bytes


class ContextBudgetInvalid(ContextError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_BUDGET_INVALID,
            "that is not a context budget",
            detail or "expected a positive whole number of UTF-8 bytes",
        )


class SourceRefInvalid(ContextError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_SOURCE_REF_INVALID,
            "that is not a semantic source reference",
            detail or "a source reference names a role, never a location",
        )


class SourceKindInvalid(ContextError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_SOURCE_KIND_INVALID,
            "that is not a source kind this build can produce",
            detail,
        )


__all__ = [
    "CODE_BUDGET_INVALID",
    "CODE_CURRENT_MESSAGE_INVALID",
    "CODE_CURRENT_MESSAGE_OVERSIZE",
    "CODE_SOURCE_KIND_INVALID",
    "CODE_SOURCE_REF_INVALID",
    "ContextBudgetInvalid",
    "ContextError",
    "CurrentMessageInvalid",
    "CurrentMessageOversize",
    "SourceKindInvalid",
    "SourceRefInvalid",
]
