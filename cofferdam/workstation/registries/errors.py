"""Structured, bounded registry errors.

A registry file is *local machine configuration*. When it is wrong the service
has to say so precisely enough to be fixable, and vaguely enough that nothing
from inside the file is echoed back through the API.

Two rules make that safe, and both are enforced by construction here rather
than by reviewer discipline:

1. **Reasons are code-owned strings.** Every message is assembled from this
   module's vocabulary plus a location (``items[3].domain_policy.mode``) and, at
   most, an identifier that already passed :func:`~.common.validate_id` — which
   only admits lowercase ASCII kebab-case. No value read out of the file ever
   reaches a message, so a stray token, cookie, or path pasted into a registry
   cannot be reflected back out of one.
2. **No exception text is forwarded.** ``json`` and ``OSError`` messages carry
   filesystem paths and file content fragments, so they are translated, never
   passed through.
"""

from __future__ import annotations

from typing import Optional

# Closed vocabulary of failure kinds, so the UI (and later Guardian) can branch
# on the cause without parsing prose.
UNREADABLE = "unreadable"
INVALID_JSON = "invalid_json"
INVALID_ENVELOPE = "invalid_envelope"
UNSUPPORTED_VERSION = "unsupported_version"
MISSING_FIELD = "missing_field"
UNKNOWN_FIELD = "unknown_field"
FORBIDDEN_FIELD = "forbidden_field"
INVALID_VALUE = "invalid_value"
DUPLICATE_ID = "duplicate_id"
DUPLICATE_ALIAS = "duplicate_alias"
DANGLING_REFERENCE = "dangling_reference"
CONSTRAINT_VIOLATED = "constraint_violated"
DEPENDENCY_INVALID = "dependency_invalid"

REASON_CODES = (
    UNREADABLE,
    INVALID_JSON,
    INVALID_ENVELOPE,
    UNSUPPORTED_VERSION,
    MISSING_FIELD,
    UNKNOWN_FIELD,
    FORBIDDEN_FIELD,
    INVALID_VALUE,
    DUPLICATE_ID,
    DUPLICATE_ALIAS,
    DANGLING_REFERENCE,
    CONSTRAINT_VIOLATED,
    DEPENDENCY_INVALID,
)

MAX_MESSAGE_CHARS = 240


class RegistryError(Exception):
    """A registry could not be loaded or validated.

    ``registry`` is a code-owned registry name, ``reason`` one of the codes
    above, ``where`` an optional structural location inside the document, and
    ``message`` a short code-owned explanation.
    """

    def __init__(
        self,
        registry: str,
        reason: str,
        message: str,
        where: Optional[str] = None,
    ) -> None:
        self.registry = registry
        self.reason = reason
        self.where = where
        self.message = message[:MAX_MESSAGE_CHARS]
        super().__init__(self.describe())

    def describe(self) -> str:
        location = f" at {self.where}" if self.where else ""
        return f"{self.registry}{location}: {self.message}"

    def to_payload(self) -> dict:
        """The shape the API embeds in a configuration-error response."""
        return {
            "registry": self.registry,
            "reason": self.reason,
            "where": self.where,
            "message": self.message,
        }
