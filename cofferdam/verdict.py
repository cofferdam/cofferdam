"""Verdict vocabulary — the stable enums shared across the trust core.

This is the single home for the guard's output contract: the decision states,
the risk annotation, the reason-code taxonomy, and the immutable ``Verdict``
container with its canonical, byte-stable serialization.

Invariant (no-ALLOWED): a file-edit proposal is only ever ``BLOCKED`` or
``NEEDS_APPROVAL``. There is deliberately no ``ALLOWED`` / auto-apply state in
v0.1 — nothing is ever cleared without an explicit human approval (a later
version). A test asserts this set never grows an auto-clear member.

Reason codes are stable strings. They may later be persisted in the PR3 audit
chain, so renames are a breaking change and follow a documented deprecation
policy; do not repurpose an existing value.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Tuple


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

    # -- diff structure --
    DIFF_EMPTY = "diff.empty"
    DIFF_MALFORMED = "diff.malformed"
    DIFF_MULTIPLE_FILES = "diff.multiple_files"
    DIFF_BINARY = "diff.binary"
    DIFF_TRUNCATED = "diff.truncated"
    DIFF_PATH_MISMATCH = "diff.path_mismatch"
    DIFF_TOO_LARGE = "diff.too_large"

    # -- guard --
    GUARD_INTERNAL_ERROR = "guard.internal_error"


@dataclass(frozen=True)
class Verdict:
    """The guard's output. ``reasons`` are deterministically ordered by the
    guard; serialization is canonical and **byte-stable** (sorted keys, sorted
    reasons, fixed separators, ``ensure_ascii``) so PR3 audit hashing can rely
    on it."""

    decision: Decision
    risk: Risk
    reasons: Tuple[ReasonCode, ...]

    def to_canonical_json(self) -> str:
        payload = {
            "decision": self.decision.value,
            "reasons": sorted(code.value for code in self.reasons),
            "risk": self.risk.value,
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
