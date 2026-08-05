"""Failure states for official-provider search, as a closed vocabulary.

Every way a provider call can fail is named here, because the *distinctions*
carry the product behaviour: "you have not configured this yet" and "Spotify
rejected the credentials you configured" and "you have used today's quota" all
look like "search did not work" from a distance, and all three need a different
sentence on the phone.

Two invariants hold for every class below.

**No credential material, ever.** Not the value, not a prefix, not a length, not
a hint. Each exception carries a code, a short human message, and an optional
bounded detail assembled from constants plus, at most, a provider status code.
A provider's own response body is never pasted into a detail — it is attacker-
influenced text at worst and noise at best.

**No unbounded waiting.** ``retry_after_seconds`` is carried as data for the
client to display; nothing here ever sleeps on it. A daemon that blocked on a
provider's Retry-After would have handed that provider the ability to stall the
whole workstation.
"""

from __future__ import annotations

from typing import Optional

from ..errors import AdapterError

# Error codes, extending the closed vocabulary in ``workstation.errors``.
CODE_PROVIDER_UNCONFIGURED = "media_provider_unconfigured"
CODE_PROVIDER_REJECTED = "media_provider_rejected"
CODE_PROVIDER_RATE_LIMITED = "media_provider_rate_limited"
CODE_PROVIDER_UNAVAILABLE = "media_provider_unavailable"
CODE_PROVIDER_MALFORMED = "media_provider_malformed_response"
CODE_SEARCH_NOT_FOUND = "media_search_not_found"
CODE_SEARCH_EXPIRED = "media_search_expired"
CODE_RESULT_NOT_FOUND = "media_result_not_found"


class ProviderSearchError(AdapterError):
    """Base for every official-provider search failure.

    An :class:`~cofferdam.workstation.errors.AdapterError` subclass so a failed
    search is recorded as a *failed action* with an accurate code, exactly like
    the M2A/M2B3A refusals, rather than surfacing as an internal error.
    """

    code = CODE_PROVIDER_UNAVAILABLE

    def __init__(
        self,
        message: str,
        detail: Optional[str] = None,
        *,
        retry_after_seconds: Optional[int] = None,
    ) -> None:
        super().__init__(message, detail)
        # Data for the UI to show. Never slept on: see the module docstring.
        self.retry_after_seconds = retry_after_seconds


class ProviderUnconfigured(ProviderSearchError):
    """No usable credentials for this provider on this host.

    The most common state, and a completely normal one. The phone shows
    "structured results not configured" and keeps offering the M2B3A open and
    search-page actions.
    """

    code = CODE_PROVIDER_UNCONFIGURED


class ProviderRejected(ProviderSearchError):
    """The provider refused the configured credentials (401/403).

    Distinct from :class:`ProviderUnconfigured` on purpose: "you have not set
    this up" and "what you set up is being refused" lead to different fixes, and
    collapsing them sends a person to re-read setup instructions they already
    followed correctly.
    """

    code = CODE_PROVIDER_REJECTED


class ProviderRateLimited(ProviderSearchError):
    """Rate limited or out of quota (429, or a quota-exhausted 403).

    YouTube's default allocation is roughly a hundred searches a day, so this is
    a state a real user will meet — it deserves its own message rather than
    looking like an outage.
    """

    code = CODE_PROVIDER_RATE_LIMITED


class ProviderTemporarilyUnavailable(ProviderSearchError):
    """Timeout, connection failure, or a provider 5xx."""

    code = CODE_PROVIDER_UNAVAILABLE


class ProviderMalformedResponse(ProviderSearchError):
    """The provider answered, but not with something this adapter can read.

    Fails closed rather than salvaging partial data: a half-parsed result list
    would put items on the phone that nobody can vouch for.
    """

    code = CODE_PROVIDER_MALFORMED


class SearchSessionNotFound(AdapterError):
    """No such search session on this server."""

    code = CODE_SEARCH_NOT_FOUND


class SearchSessionExpired(AdapterError):
    """The search session existed but has aged out.

    Separate from "not found" because the user-facing answer differs: search
    again, rather than something went wrong.
    """

    code = CODE_SEARCH_EXPIRED


class ResultNotFound(AdapterError):
    """No such result in that session — including a result from another provider."""

    code = CODE_RESULT_NOT_FOUND


__all__ = [
    "CODE_PROVIDER_MALFORMED",
    "CODE_PROVIDER_RATE_LIMITED",
    "CODE_PROVIDER_REJECTED",
    "CODE_PROVIDER_UNAVAILABLE",
    "CODE_PROVIDER_UNCONFIGURED",
    "CODE_RESULT_NOT_FOUND",
    "CODE_SEARCH_EXPIRED",
    "CODE_SEARCH_NOT_FOUND",
    "ProviderMalformedResponse",
    "ProviderRateLimited",
    "ProviderRejected",
    "ProviderSearchError",
    "ProviderTemporarilyUnavailable",
    "ProviderUnconfigured",
    "ResultNotFound",
    "SearchSessionExpired",
    "SearchSessionNotFound",
]
