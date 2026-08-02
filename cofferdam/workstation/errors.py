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
