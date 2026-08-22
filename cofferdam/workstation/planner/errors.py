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


# -- remote development request ingress ---------------------------------------
#
# These are refusals to *accept a request at all*, which makes them earlier than
# everything above: nothing here describes a planning turn, because in every one
# of these cases no planner was invoked and no planner row exists. They are the
# door, not the room.
#
# The distinction matters for cost. A planning turn spends a real cloud call, so
# a refusal that arrives after the invocation is a refusal somebody paid for.
# Each of these fires before that point, and a test asserts it.


class PlannerIngressError(PlannerError):
    """Base for every refusal to accept a remote development request."""

    reason_code = "development_request_refused"


class PlannerIngressInvalid(PlannerIngressError):
    """The request was not shaped like one this host could act on.

    A malformed project id, an empty instruction, an instruction over the bound.
    Refused rather than trimmed, for the reason an over-long answer is: a
    shortened instruction is a different instruction from the one somebody sent.
    """

    reason_code = "development_request_invalid"


class PlannerIngressNotAllowedNow(PlannerIngressError):
    """This project already has an unresolved development step.

    The first remote development workflow is sequential by decision, not by
    accident. A second request while a question is open, a prompt is awaiting
    approval or a worker is still in flight would create a competing thread whose
    authority nobody granted — so it is refused, and the refusal names the
    operation that is actually current so a caller can go and look at it.

    Deliberately **not** resolved by falling back to "the latest": that is the
    shape that makes a remote surface untrustworthy, and `operations.reads` gives
    the same argument for its own refusals.
    """

    reason_code = "development_request_not_allowed_now"


class PlannerIngressConflict(PlannerIngressError):
    """The same ``client_request_id`` arrived carrying a different request.

    Refused rather than treated as a retry. Returning the first request's planner
    result for the second instruction would be the worst available answer, and it
    would do so having spent nothing — the conflict is detected before any
    provider is touched.
    """

    reason_code = "development_request_conflict"


class PlannerIngressInFlight(PlannerIngressError):
    """That request is being planned right now.

    The honest answer to a retry that arrives mid-invocation. A planning turn
    outlives a Custom GPT Action's round trip by design, so this is the *expected*
    answer to a normal retry rather than an exceptional one, and the caller reads
    the operations projection instead of sending again.
    """

    reason_code = "development_request_in_flight"


class PlannerIngressAbandoned(PlannerIngressError):
    """A previous attempt under this id was interrupted, and will not be rerun.

    The same doctrine :meth:`PlannerService.reconcile_interrupted` follows: this
    host does not know whether the provider ran, so it will neither claim it did
    nor spend a second call asserting it did not. A genuinely new attempt needs a
    new ``client_request_id``, which is a decision a person makes rather than one
    a retry loop makes by itself.
    """

    reason_code = "development_request_abandoned"


__all__ = [
    "PlannerError",
    "PlannerIngressAbandoned",
    "PlannerIngressConflict",
    "PlannerIngressError",
    "PlannerIngressInFlight",
    "PlannerIngressInvalid",
    "PlannerIngressNotAllowedNow",
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
