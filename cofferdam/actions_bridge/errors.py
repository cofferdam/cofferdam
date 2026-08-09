"""The bridge's error envelope: a closed vocabulary, and nothing behind it.

One shape, mirroring the workstation's::

    {"error": {"code": "...", "message": "...", "detail": "..." | null}}

Three properties this file exists to hold.

**The vocabulary is closed.** A model reads these codes and decides what to tell
somebody. A code it has never seen is a code it will improvise around, so there
is no path that invents one — every raise names a constant from this module.

**``detail`` is a sentence, not a cause.** It is bounded, single-line, and
composed from Cofferdam's own words. It never carries an exception's ``str()``,
a traceback, a URL, a filesystem path, an environment value or a credential. The
unhandled-exception handler in :mod:`~cofferdam.actions_bridge.service` does not
even pass the exception type through, which is the difference between this
surface and the private one: the private API is read by its owner, and this one
is read by a model provider.

**Upstream codes are translated, not forwarded.** Task Core has a rich error
vocabulary — ``task_clarification_not_delivered``, ``task_followup_in_flight``,
``task_project_root_invalid``. Some of those describe the workstation's insides.
:func:`from_upstream_code` maps the ones a remote caller can act on and collapses
the rest, so the bridge never leaks a distinction that only matters on the host.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .limits import MAX_ERROR_SUMMARY_CHARS

# -- the closed vocabulary ----------------------------------------------------

#: No credential, or one that did not match. Never says which.
CODE_UNAUTHORIZED = "unauthorized"
#: The request was shaped wrong: an unknown field, a bad type, a bound exceeded.
CODE_INVALID_REQUEST = "invalid_request"
#: A named project, task or question does not exist, or is not one this bridge
#: may see. Deliberately one code for both: "exists but you may not" is an
#: answer, and a caller that can tell the two apart can enumerate.
CODE_NOT_FOUND = "not_found"
#: The project exists and is not eligible for delegated tasks from here.
CODE_PROJECT_NOT_ELIGIBLE = "project_not_eligible"
#: The task cannot do this now — wrong state, no live session, a question open.
CODE_NOT_ALLOWED_NOW = "not_allowed_now"
#: A question shape this bridge cannot carry. Distinct from the above because
#: the right response is "use the local surface", not "wait and retry".
CODE_UNSUPPORTED_QUESTION_SHAPE = "unsupported_question_shape"
#: The same ``client_request_id`` arrived with a different body.
CODE_IDEMPOTENCY_CONFLICT = "idempotency_conflict"
#: The same ``client_request_id`` is being processed right now.
CODE_REQUEST_IN_FLIGHT = "request_in_flight"
#: Too many requests, or too many at once.
CODE_RATE_LIMITED = "rate_limited"
#: The request body, or a field in it, is too large.
CODE_TOO_LARGE = "too_large"
#: Cofferdam did not answer in time. The important half is in the message: the
#: work may still be running, and the caller should sync rather than retry.
CODE_UPSTREAM_TIMEOUT = "upstream_timeout"
#: Cofferdam could not be reached, or answered something the bridge cannot read.
CODE_UPSTREAM_UNAVAILABLE = "upstream_unavailable"
#: A capability that exists in the contract and not in this build. Today only
#: the artifact operations, which are absent rather than stubbed.
CODE_NOT_IMPLEMENTED = "not_implemented"
#: Anything else. Carries no detail at all.
CODE_INTERNAL = "internal_error"

ERROR_CODES = (
    CODE_UNAUTHORIZED,
    CODE_INVALID_REQUEST,
    CODE_NOT_FOUND,
    CODE_PROJECT_NOT_ELIGIBLE,
    CODE_NOT_ALLOWED_NOW,
    CODE_UNSUPPORTED_QUESTION_SHAPE,
    CODE_IDEMPOTENCY_CONFLICT,
    CODE_REQUEST_IN_FLIGHT,
    CODE_RATE_LIMITED,
    CODE_TOO_LARGE,
    CODE_UPSTREAM_TIMEOUT,
    CODE_UPSTREAM_UNAVAILABLE,
    CODE_NOT_IMPLEMENTED,
    CODE_INTERNAL,
)


def bounded_detail(value: Any) -> Optional[str]:
    """One short, single-line, safe detail string — or nothing.

    Newlines collapse rather than survive: a multi-line detail is how a
    traceback would look if one ever reached here, and a single line makes that
    visible in a log rather than convincing.
    """
    if value is None:
        return None
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return None
    if len(text) > MAX_ERROR_SUMMARY_CHARS:
        text = text[: MAX_ERROR_SUMMARY_CHARS - 1] + "…"
    return text


@dataclass(frozen=True)
class BridgeError(Exception):
    """One refusal the bridge is willing to publish."""

    code: str
    message: str
    status_code: int = 400
    detail: Optional[str] = None

    def __post_init__(self) -> None:
        # Not a defensive check — a guard against this file drifting. A code
        # outside the tuple means somebody added a raise without adding the
        # word, and a model would meet a code no instruction mentions.
        if self.code not in ERROR_CODES:
            raise ValueError("unknown bridge error code: " + str(self.code))

    def to_payload(self) -> Dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "detail": bounded_detail(self.detail),
            }
        }


# -- translating Cofferdam's vocabulary ---------------------------------------
#
# Task Core's codes are listed by name so that adding one upstream is a visible
# decision here rather than a silent fall-through. The fall-through is safe —
# `not_allowed_now` is true of every refusal in that layer — but it is also
# lossy, and a reader should be able to see which codes were considered.

_UPSTREAM_NOT_FOUND = frozenset(
    {
        "task_unknown",
        "task_project_unknown",
        "task_clarification_unknown",
        "not_found",
    }
)

_UPSTREAM_NOT_ELIGIBLE = frozenset(
    {
        "task_project_disabled",
        "task_project_root_invalid",
        "task_adapter_unknown",
        "task_adapter_disabled",
        "task_adapter_not_permitted_for_project",
    }
)

_UPSTREAM_INVALID = frozenset(
    {
        "invalid_params",
        "task_prompt_invalid",
        "task_followup_invalid",
        "task_request_id_invalid",
        "task_clarification_invalid",
    }
)

_UPSTREAM_CONFLICT = frozenset({"task_idempotency_conflict"})

_UPSTREAM_UNSUPPORTED_SHAPE = frozenset(
    {
        "task_clarification_unsupported",
        "task_clarification_not_delivered",
    }
)

#: Everything else in Task Core's vocabulary — ``task_already_finished``,
#: ``task_not_waiting_for_input``, ``task_session_unavailable``,
#: ``task_followup_in_flight``, ``task_clarification_closed``,
#: ``task_clarification_pending``, ``task_turn_limit_reached``,
#: ``task_illegal_transition``, ``task_result_not_ready``,
#: ``task_followup_unsupported``, ``task_cancel_unsupported`` — all mean the
#: same thing to somebody holding a phone in another country: not now.
_DEFAULT_UPSTREAM = CODE_NOT_ALLOWED_NOW


def from_upstream_code(code: Any) -> str:
    """The bridge code for a Cofferdam error code."""
    if code in _UPSTREAM_NOT_FOUND:
        return CODE_NOT_FOUND
    if code in _UPSTREAM_NOT_ELIGIBLE:
        return CODE_PROJECT_NOT_ELIGIBLE
    if code in _UPSTREAM_INVALID:
        return CODE_INVALID_REQUEST
    if code in _UPSTREAM_CONFLICT:
        return CODE_IDEMPOTENCY_CONFLICT
    if code in _UPSTREAM_UNSUPPORTED_SHAPE:
        return CODE_UNSUPPORTED_QUESTION_SHAPE
    if code == "unauthorized":
        # The bridge's *internal* credential was refused. That is a deployment
        # fault on this machine, not something the caller did, and telling a
        # model provider "unauthorized" would send it to re-enter its own key.
        return CODE_UPSTREAM_UNAVAILABLE
    return _DEFAULT_UPSTREAM


_STATUS_FOR_CODE: Dict[str, int] = {
    CODE_UNAUTHORIZED: 401,
    CODE_INVALID_REQUEST: 422,
    CODE_NOT_FOUND: 404,
    CODE_PROJECT_NOT_ELIGIBLE: 409,
    CODE_NOT_ALLOWED_NOW: 409,
    CODE_UNSUPPORTED_QUESTION_SHAPE: 409,
    CODE_IDEMPOTENCY_CONFLICT: 409,
    CODE_REQUEST_IN_FLIGHT: 409,
    CODE_RATE_LIMITED: 429,
    CODE_TOO_LARGE: 413,
    CODE_UPSTREAM_TIMEOUT: 504,
    CODE_UPSTREAM_UNAVAILABLE: 502,
    CODE_NOT_IMPLEMENTED: 501,
    CODE_INTERNAL: 500,
}


def status_for(code: str) -> int:
    return _STATUS_FOR_CODE.get(code, 400)


__all__ = [
    "BridgeError",
    "ERROR_CODES",
    "bounded_detail",
    "from_upstream_code",
    "status_for",
] + [name for name in dir() if name.startswith("CODE_")]
