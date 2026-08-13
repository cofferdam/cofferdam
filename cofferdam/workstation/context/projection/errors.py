"""The two refusals the projector makes, with stable codes to branch on.

The same posture as :mod:`..errors`: almost nothing that happens during a
projection is an error. A denied source, a secret-shaped part, an exhausted
budget and a duplicate are all real states of a real pack, and the honest answer
to each is a :class:`~.model.ProjectionOmission` row rather than an exception —
because a caller that got an exception would learn *that* something was refused
without learning *what*, and a security boundary that cannot explain itself gets
worked around.

What is left are the cases where producing a projection at all would be a lie:
the input is not a pack, or the requested bound is not a bound.
"""

from __future__ import annotations

from typing import Optional

#: The object handed to the projector is not a :class:`~..models.LocalContextPack`.
#: Refused rather than duck-typed: accepting "anything with a `parts` attribute"
#: is exactly how a dict assembled somewhere else acquires egress eligibility.
CODE_PROJECTION_INPUT_INVALID = "projection_input_invalid"

#: The egress budget is not a positive integer number of UTF-8 bytes. Refused
#: rather than clamped, for the reason :data:`..errors.CODE_BUDGET_INVALID`
#: gives: a caller whose bound was silently replaced reports the wrong one.
CODE_PROJECTION_BUDGET_INVALID = "projection_budget_invalid"


class ProjectionError(Exception):
    """A refusal with a stable code, message and optional detail.

    No message carries a path, a document name or projected text — a refusal is
    about the *shape* of the request, and an error string is the one part of a
    security component most likely to end up in somebody's log.
    """

    def __init__(self, code: str, message: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class ProjectionInputInvalid(ProjectionError):
    """Only a `LocalContextPack` can be projected, and only by this projector."""

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_PROJECTION_INPUT_INVALID,
            "a projection is built from a LocalContextPack",
            detail,
        )


class ProjectionBudgetInvalid(ProjectionError):
    """A bound that is not a positive byte count is not a bound."""

    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_PROJECTION_BUDGET_INVALID,
            "the egress budget is a positive number of UTF-8 bytes",
            detail,
        )


__all__ = [
    "CODE_PROJECTION_BUDGET_INVALID",
    "CODE_PROJECTION_INPUT_INVALID",
    "ProjectionBudgetInvalid",
    "ProjectionError",
    "ProjectionInputInvalid",
]
