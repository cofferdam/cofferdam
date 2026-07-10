"""Strict, fail-closed proposal schema and parser.

A proposal is a mapping with *exactly* the author-supplied keys of schema v1.
Parsing rejects — before any guard runs — unknown/missing keys, wrong types,
server-derived fields, unsupported versions/kinds, and structurally invalid
string fields. The guard (PR2b) only ever receives a valid ``Proposal``.

Rejection is data-driven and deterministic: ``parse_proposal`` returns a
``ParseResult`` carrying stable ``ReasonCode``s and never raises on malformed
input (fail-closed wrapper). Path *structure* (traversal, drive letters,
symlinks, protected paths) is not judged here — that is ``paths.assess_path``,
a separate gate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional, Tuple

from .verdict import ReasonCode

SCHEMA_VERSION = 1
KIND_SINGLE_FILE_DIFF = "single_file_diff"

REQUIRED_KEYS = frozenset({"schema_version", "kind", "target_path", "diff"})

# Names an author must never supply — they are server-derived outputs. Present
# in the input -> rejection (a proposal supplying its own verdict is a red
# flag). Compared casefolded so ``Verdict`` / ``VERDICT`` cannot slip past.
SERVER_DERIVED_KEYS = frozenset({
    "verdict",
    "risk",
    "reasons",
    "canonical_path",
    "hash",
    "id",
    "timestamp",
    "created_at",
})

# Reserved for a FUTURE schema_version (display-only human context, never read
# by guard logic). NOT accepted under v1; present now -> rejection. Reserving
# the name here prevents it being repurposed for anything else later.
RESERVED_FUTURE_KEYS = frozenset({"description"})


@dataclass(frozen=True)
class Proposal:
    """A structurally valid schema-v1 proposal. Path structure is unvalidated
    here (see ``paths.assess_path``)."""

    schema_version: int
    kind: str
    target_path: str
    diff: str


@dataclass(frozen=True)
class ParseResult:
    ok: bool
    proposal: Optional[Proposal]
    reasons: Tuple[ReasonCode, ...]

    @classmethod
    def rejected(cls, *reasons: ReasonCode) -> "ParseResult":
        ordered = tuple(sorted(set(reasons), key=lambda code: code.value))
        return cls(False, None, ordered)

    @classmethod
    def accepted(cls, proposal: Proposal) -> "ParseResult":
        return cls(True, proposal, ())


def _is_utf8(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def parse_proposal(raw: object) -> ParseResult:
    """Strictly parse a raw mapping into a ``Proposal``, failing closed.

    Never raises on malformed input; always returns a ``ParseResult``.
    """
    try:
        return _parse(raw)
    except Exception:
        # Fail-closed: no malformed input escapes as an exception.
        return ParseResult.rejected(ReasonCode.SCHEMA_NOT_A_MAPPING)


def _parse(raw: object) -> ParseResult:
    if not isinstance(raw, Mapping):
        return ParseResult.rejected(ReasonCode.SCHEMA_NOT_A_MAPPING)

    keys = set(raw.keys())
    reasons = []

    for key in keys - REQUIRED_KEYS:
        folded = key.casefold() if isinstance(key, str) else None
        if folded is not None and folded in SERVER_DERIVED_KEYS:
            reasons.append(ReasonCode.SCHEMA_SERVER_FIELD_PRESENT)
        elif isinstance(key, str) and key in RESERVED_FUTURE_KEYS:
            reasons.append(ReasonCode.SCHEMA_RESERVED_FUTURE_KEY)
        else:
            reasons.append(ReasonCode.SCHEMA_UNKNOWN_KEY)

    if REQUIRED_KEYS - keys:
        reasons.append(ReasonCode.SCHEMA_MISSING_KEY)

    if reasons:
        return ParseResult.rejected(*reasons)

    schema_version = raw["schema_version"]
    kind = raw["kind"]
    target_path = raw["target_path"]
    diff = raw["diff"]

    # bool is a subclass of int; reject it explicitly for schema_version.
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        reasons.append(ReasonCode.SCHEMA_WRONG_TYPE)
    if not isinstance(kind, str):
        reasons.append(ReasonCode.SCHEMA_WRONG_TYPE)
    if not isinstance(target_path, str):
        reasons.append(ReasonCode.SCHEMA_WRONG_TYPE)
    if not isinstance(diff, str):
        reasons.append(ReasonCode.SCHEMA_WRONG_TYPE)
    if reasons:
        return ParseResult.rejected(*reasons)

    if schema_version != SCHEMA_VERSION:
        reasons.append(ReasonCode.SCHEMA_UNSUPPORTED_VERSION)
    if kind != KIND_SINGLE_FILE_DIFF:
        reasons.append(ReasonCode.SCHEMA_UNKNOWN_KIND)
    if target_path == "":
        reasons.append(ReasonCode.SCHEMA_EMPTY_TARGET)
    if diff == "":
        reasons.append(ReasonCode.SCHEMA_EMPTY_DIFF)
    # NUL is never legitimate in either field. (Broader path control-character
    # rejection happens in paths.normalize_target; a tab is legal in a diff.)
    if "\x00" in target_path or "\x00" in diff:
        reasons.append(ReasonCode.SCHEMA_NUL_OR_CONTROL)
    if not _is_utf8(target_path) or not _is_utf8(diff):
        reasons.append(ReasonCode.SCHEMA_NON_UTF8)

    if reasons:
        return ParseResult.rejected(*reasons)

    return ParseResult.accepted(
        Proposal(
            schema_version=schema_version,
            kind=kind,
            target_path=target_path,
            diff=diff,
        )
    )
