"""Verdict vocabulary — the stable enums shared across the trust core.

PR2a introduces only the *vocabulary*: the decision states, the risk
annotation, and the reason-code taxonomy. The full ``Verdict`` container and
its canonical, byte-stable serialization arrive with the guard body in PR2b.

Invariant (no-ALLOWED): a file-edit proposal is only ever ``BLOCKED`` or
``NEEDS_APPROVAL``. There is deliberately no ``ALLOWED`` / auto-apply state in
v0.1 — nothing is ever cleared without an explicit human approval (a later
version). A test asserts this set never grows an auto-clear member.

Reason codes are stable strings. They may later be persisted in the PR3 audit
chain, so renames are a breaking change and follow a documented deprecation
policy; do not repurpose an existing value.
"""

from __future__ import annotations

from enum import Enum


class Decision(str, Enum):
    """The only two outcomes for a file-edit proposal in v0.1."""

    BLOCKED = "blocked"
    NEEDS_APPROVAL = "needs_approval"


class Risk(str, Enum):
    """An annotation on a decision. Never an authorization by itself."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReasonCode(str, Enum):
    """Stable, machine-readable reasons. Sorted by ``value`` for determinism."""

    # -- schema / parse-time --
    SCHEMA_NOT_A_MAPPING = "schema.not_a_mapping"
    SCHEMA_MISSING_KEY = "schema.missing_key"
    SCHEMA_UNKNOWN_KEY = "schema.unknown_key"
    SCHEMA_RESERVED_FUTURE_KEY = "schema.reserved_future_key"
    SCHEMA_WRONG_TYPE = "schema.wrong_type"
    SCHEMA_UNSUPPORTED_VERSION = "schema.unsupported_version"
    SCHEMA_UNKNOWN_KIND = "schema.unknown_kind"
    SCHEMA_SERVER_FIELD_PRESENT = "schema.server_field_present"
    SCHEMA_EMPTY_DIFF = "schema.empty_diff"
    SCHEMA_EMPTY_TARGET = "schema.empty_target"
    SCHEMA_NUL_OR_CONTROL = "schema.nul_or_control"
    SCHEMA_NON_UTF8 = "schema.non_utf8"

    # -- path, lexical --
    PATH_ABSOLUTE = "path.absolute"
    PATH_PARENT_TRAVERSAL = "path.parent_traversal"
    PATH_CURDIR_SEGMENT = "path.curdir_segment"
    PATH_EMPTY_SEGMENT = "path.empty_segment"
    PATH_DRIVE_LETTER = "path.drive_letter"
    PATH_UNC = "path.unc"
    PATH_ALTERNATE_DATA_STREAM = "path.alternate_data_stream"
    PATH_NUL_OR_CONTROL = "path.nul_or_control"
    PATH_TRAILING_DOT_OR_SPACE = "path.trailing_dot_or_space"
    PATH_RESERVED_DEVICE_NAME = "path.reserved_device_name"
    PATH_TOO_DEEP = "path.too_deep"
    PATH_COMPONENT_TOO_LONG = "path.component_too_long"
    PATH_TOO_LONG = "path.too_long"
    PATH_EMPTY_AFTER_NORMALIZE = "path.empty_after_normalize"

    # -- path, via the read-only repo view --
    PATH_SYMLINK_COMPONENT = "path.symlink_component"
    PATH_NON_REGULAR_TARGET = "path.non_regular_target"

    # -- protected paths --
    PROTECTED_BLOCKED = "protected.blocked"
    PROTECTED_HIGH_RISK = "protected.high_risk"
