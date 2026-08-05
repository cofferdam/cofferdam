"""Structured, bounded error envelope.

Every failure the API reports uses one shape::

    {"error": {"code": "...", "message": "...", "detail": "..." | null}}

``message`` is short and safe to show in the UI. ``detail`` is optional, always
truncated, and never carries a traceback, an environment value, a token, or a
full filesystem path from an underlying exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

MAX_DETAIL_CHARS = 300

# Error codes are a closed vocabulary so the UI can branch on them.
CODE_UNAUTHORIZED = "unauthorized"
CODE_UNKNOWN_ACTION = "unknown_action"
CODE_INVALID_PARAMS = "invalid_params"
CODE_ADAPTER_FAILED = "adapter_failed"
CODE_ADAPTER_UNSUPPORTED = "adapter_unsupported"
CODE_NOT_FOUND = "not_found"
CODE_INTERNAL = "internal_error"
# M2A: the registry/browser-profile boundary.
CODE_CONFIGURATION_INVALID = "configuration_invalid"
CODE_BROWSER_PROFILE_INVALID = "browser_profile_invalid"
CODE_DOMAIN_NOT_ALLOWED = "domain_not_allowed"
CODE_APPLICATION_UNAVAILABLE = "application_unavailable"
# M2B3A: the media provider boundary.
CODE_MEDIA_PROVIDER_UNKNOWN = "media_provider_unknown"
CODE_MEDIA_SEARCH_UNSUPPORTED = "media_search_unsupported"
CODE_MEDIA_QUERY_INVALID = "media_query_invalid"
# M2C: the audio control boundary. Each of these is a refusal to act on a
# device reference that can no longer be trusted to name what the client meant.
CODE_AUDIO_RESOURCE_UNKNOWN = "audio_resource_unknown"
CODE_AUDIO_RESOURCE_CHANGED = "audio_resource_changed"
CODE_AUDIO_GRAPH_CHANGED = "audio_graph_changed"
CODE_AUDIO_UNAVAILABLE = "audio_unavailable"
CODE_AUDIO_VOLUME_INVALID = "audio_volume_invalid"
CODE_AUDIO_MUTE_INVALID = "audio_mute_invalid"
CODE_AUDIO_ACTION_UNSUPPORTED = "audio_action_unsupported"


def bounded_detail(value: Any) -> Optional[str]:
    """Reduce an arbitrary value to a short, single-line, safe detail string."""
    if value is None:
        return None
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return None
    if len(text) > MAX_DETAIL_CHARS:
        text = text[: MAX_DETAIL_CHARS - 1] + "…"
    return text


@dataclass(frozen=True)
class ApiError(Exception):
    """An error that is safe to serialize to a client."""

    code: str
    message: str
    status_code: int = 400
    detail: Optional[str] = None

    def to_payload(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "detail": bounded_detail(self.detail),
            }
        }


class AdapterError(Exception):
    """A host adapter could not complete an operation.

    Adapters raise this (never a bare ``OSError``/``CalledProcessError``) so the
    service can turn any platform failure into a bounded structured error.
    """

    code = CODE_ADAPTER_FAILED

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = bounded_detail(detail)


class AdapterUnsupported(AdapterError):
    """The requested capability does not exist on this host/platform."""

    code = CODE_ADAPTER_UNSUPPORTED


# ---------------------------------------------------------------------------
# M2A: refusals at the registry / browser-profile boundary
#
# These are :class:`AdapterError` subclasses so that a refused action is
# recorded as a *failed action* with an accurate code, rather than surfacing as
# an unexplained internal error. Each one is a deliberate fail-closed outcome:
# none of them ever degrades into "try something else instead".
# ---------------------------------------------------------------------------


class ConfigurationInvalid(AdapterError):
    """Local registry configuration is unusable, so the action cannot proceed.

    Absent registries are valid and empty; only a file that exists and is wrong
    reaches this. Continuing would mean acting with an unknown policy.
    """

    code = CODE_CONFIGURATION_INVALID


class BrowserProfileInvalid(AdapterError):
    """The requested browser profile does not exist, or is disabled.

    Never falls back to another profile: an explicit request for a profile is a
    statement about *which* browser context may see the URL.
    """

    code = CODE_BROWSER_PROFILE_INVALID


class DomainNotAllowed(AdapterError):
    """The selected profile's domain policy does not permit this host."""

    code = CODE_DOMAIN_NOT_ALLOWED


class ApplicationUnavailable(AdapterError):
    """The application a profile selects is not installed on this host."""

    code = CODE_APPLICATION_UNAVAILABLE


# ---------------------------------------------------------------------------
# M2B3A: refusals at the media-provider boundary
#
# The same fail-closed shape as above. In particular
# :class:`MediaSearchUnsupported` is a *truthful* answer, not a stopgap: a
# provider whose search route Cofferdam cannot build says so and opens nothing,
# rather than opening a home page and letting the green result imply a search
# happened.
# ---------------------------------------------------------------------------


class MediaProviderUnknown(AdapterError):
    """The requested provider id is not in the code-owned allowlist."""

    code = CODE_MEDIA_PROVIDER_UNKNOWN


class MediaSearchUnsupported(AdapterError):
    """This provider offers no search route Cofferdam can construct."""

    code = CODE_MEDIA_SEARCH_UNSUPPORTED


class MediaQueryInvalid(AdapterError):
    """The search text was empty, over-long, or carried control characters."""

    code = CODE_MEDIA_QUERY_INVALID
