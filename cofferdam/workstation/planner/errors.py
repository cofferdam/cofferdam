"""Truthful failures for a planning turn.

Every error here is a *refusal to produce a result*, and none of them is
recoverable into an action. That is the point: the one thing a planner failure
must never become is a plausible-looking ``ASK_USER`` or
``PREPARE_WORKER_PROMPT``, because both of those are things a person would act
on. A failed planning turn stays a failed planning turn.
"""

from __future__ import annotations

from typing import Optional


class PlannerError(Exception):
    """Base for every planning failure. Carries a closed reason code."""

    #: Stable, lowercase, code-owned. Safe to record and to branch on.
    reason_code = "planner_error"

    def __init__(self, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.detail = detail

    def to_dict(self) -> dict:
        return {
            "reason_code": self.reason_code,
            "message": str(self),
            "detail": self.detail,
        }


class PlannerUnavailable(PlannerError):
    """The provider could not be reached or is not usable on this host."""

    reason_code = "planner_unavailable"


class PlannerInvocationFailed(PlannerError):
    """The provider ran and failed. Says nothing about what the model thought."""

    reason_code = "planner_invocation_failed"


class PlannerTimeout(PlannerError):
    """The provider did not answer inside the bound this host allows."""

    reason_code = "planner_timeout"


class PlannerEnvelopeInvalid(PlannerError):
    """The provider's own process envelope was not the shape it documents.

    Distinct from :class:`PlannerResultInvalid` on purpose. This is *our
    subprocess* misbehaving; that one is *the model's output* being wrong, and
    conflating them would hide which half of the system to look at.
    """

    reason_code = "planner_envelope_invalid"


class PlannerResultMissing(PlannerError):
    """The envelope arrived but carried no structured output to validate."""

    reason_code = "planner_result_missing"


class PlannerResultInvalid(PlannerError):
    """The model's output is not a valid :class:`~.models.PlannerResult`.

    Raised for a schema failure, a semantic cross-field failure, or an attempt
    to express an execution primitive. It is never repaired.
    """

    reason_code = "planner_result_invalid"


class PlannerContextRefused(PlannerError):
    """The request did not carry a projection eligible to leave the host."""

    reason_code = "planner_context_refused"


# -- human authority ----------------------------------------------------------
#
# These are refusals to record a *person's decision*, which makes them a
# different kind of failure from everything above: nothing here says a planning
# turn went wrong. They say Cofferdam will not write an authority record that
# would not be true. Every one of them leaves the gate exactly as it was.


class PlannerAuthorityError(PlannerError):
    """Base for every refusal on the human authority path."""

    reason_code = "planner_authority_error"


class PlannerAuthorityInvalid(PlannerAuthorityError):
    """The submitted decision was not one this host could record truthfully.

    An answer that is empty or over the bound, a provenance source this build may
    not attribute, an action word outside the closed vocabulary. Refused rather
    than trimmed: a truncated answer is a different answer from the one somebody
    gave, and attributing a decision to the wrong surface makes the provenance
    worse than absent.
    """

    reason_code = "planner_authority_invalid"


class PlannerAuthorityRefused(PlannerAuthorityError):
    """The action does not belong to the gate this planner result derives.

    Approving a question, answering a prepared prompt, deciding anything about a
    ``STOP`` or about an invocation that never succeeded. The persisted result
    determines the vocabulary, and a caller does not get to reinterpret it.
    """

    reason_code = "planner_authority_refused"


class PlannerAuthorityStale(PlannerAuthorityError):
    """The subject moved out from under the decision being submitted.

    The caller named the fingerprint it believed it was authorizing and the
    persisted result no longer hashes to it. The same refusal Mind's base hash
    makes: what somebody read is not what would now be authorized, and re-reading
    is the entire point.
    """

    reason_code = "planner_authority_stale"


class PlannerAuthorityConflict(PlannerAuthorityError):
    """A terminal decision already exists and this one contradicts it.

    Never resolved by overwriting. An approval that becomes a rejection because
    somebody sent a second request is a history that lost the first decision, and
    the whole reason these records are durable is so that they cannot.
    """

    reason_code = "planner_authority_conflict"


__all__ = [
    "PlannerError",
    "PlannerUnavailable",
    "PlannerInvocationFailed",
    "PlannerTimeout",
    "PlannerEnvelopeInvalid",
    "PlannerResultMissing",
    "PlannerResultInvalid",
    "PlannerContextRefused",
    "PlannerAuthorityError",
    "PlannerAuthorityInvalid",
    "PlannerAuthorityRefused",
    "PlannerAuthorityStale",
    "PlannerAuthorityConflict",
]
