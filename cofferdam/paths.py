"""Path normalization, containment, and protected-path matching (pure).

Every function here is pure and deterministic: given the same inputs (and, for
symlink / type checks, the same ``RepoView`` snapshot) it returns the same
result. No filesystem access happens except through the injected ``RepoView``.
Nothing mutates state, spawns a subprocess, or touches the network.

The rules are conservative and fail-closed: on any doubt the path is BLOCKED.
This is the PR2a containment foundation. Real-path canonicalization bound into
an approval hash (invariant I-8) is PR3; here we refuse escapes lexically and
by blocking any symlink component.

``assess_path`` returns a path-level assessment (a ``Decision`` + ``Risk`` +
reasons). It is NOT the full guard: it judges the target path only. The guard
that combines this with diff validation into a serialized verdict is PR2b.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from . import protected_paths as _protected
from .repo_view import PathType, RepoView
from .verdict import Decision, ReasonCode, Risk

# Conservative, named caps (documented; tunable in a later version).
MAX_PATH_DEPTH = 64          # components
MAX_COMPONENT_LENGTH = 255   # characters per component
MAX_TARGET_PATH_CHARS = 4096  # characters, whole target_path

_SEPARATORS = re.compile(r"[/\\]")

_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


@dataclass(frozen=True)
class NormalizedPath:
    parts: Tuple[str, ...]  # NFC components
    fold: Tuple[str, ...]   # casefolded components, for comparison


@dataclass(frozen=True)
class NormResult:
    ok: bool
    path: Optional[NormalizedPath]
    reasons: Tuple[ReasonCode, ...]


@dataclass(frozen=True)
class PathAssessment:
    decision: Decision
    risk: Risk
    reasons: Tuple[ReasonCode, ...]


def _rejected(reason: ReasonCode) -> NormResult:
    return NormResult(False, None, (reason,))


def _has_control(text: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def normalize_target(raw: str) -> NormResult:
    """Lexically normalize and validate a target path, fail-closed.

    Returns NFC + casefolded components on success. Does not touch the
    filesystem — symlink and file-type checks are ``check_symlink_and_type``.
    """
    if not isinstance(raw, str):
        return _rejected(ReasonCode.SCHEMA_WRONG_TYPE)
    if raw == "":
        return _rejected(ReasonCode.PATH_EMPTY_AFTER_NORMALIZE)
    if _has_control(raw):
        return _rejected(ReasonCode.PATH_NUL_OR_CONTROL)
    if len(raw) > MAX_TARGET_PATH_CHARS:
        return _rejected(ReasonCode.PATH_TOO_LONG)

    if raw.startswith("\\\\") or raw.startswith("//"):
        return _rejected(ReasonCode.PATH_UNC)
    if raw.startswith("/") or raw.startswith("\\"):
        return _rejected(ReasonCode.PATH_ABSOLUTE)
    if len(raw) >= 2 and raw[1] == ":" and raw[0].isascii() and raw[0].isalpha():
        return _rejected(ReasonCode.PATH_DRIVE_LETTER)
    if ":" in raw:
        # Any other colon is an alternate-data-stream / device attempt.
        return _rejected(ReasonCode.PATH_ALTERNATE_DATA_STREAM)

    parts: List[str] = []
    for segment in _SEPARATORS.split(raw):
        seg = unicodedata.normalize("NFC", segment)
        if seg == "":
            return _rejected(ReasonCode.PATH_EMPTY_SEGMENT)
        if seg == ".":
            return _rejected(ReasonCode.PATH_CURDIR_SEGMENT)
        if seg == "..":
            return _rejected(ReasonCode.PATH_PARENT_TRAVERSAL)
        if len(seg) > MAX_COMPONENT_LENGTH:
            return _rejected(ReasonCode.PATH_COMPONENT_TOO_LONG)
        if seg != seg.rstrip(" ."):
            return _rejected(ReasonCode.PATH_TRAILING_DOT_OR_SPACE)
        if seg.split(".", 1)[0].casefold() in _WINDOWS_RESERVED:
            return _rejected(ReasonCode.PATH_RESERVED_DEVICE_NAME)
        parts.append(seg)

    if not parts:
        return _rejected(ReasonCode.PATH_EMPTY_AFTER_NORMALIZE)
    if len(parts) > MAX_PATH_DEPTH:
        return _rejected(ReasonCode.PATH_TOO_DEEP)

    fold = tuple(p.casefold() for p in parts)
    return NormResult(True, NormalizedPath(tuple(parts), fold), ())


def check_symlink_and_type(
    parts: Sequence[str], repo_view: RepoView
) -> Tuple[ReasonCode, ...]:
    """Block if any existing component is a symlink, or the target is a
    non-regular file. Uses one ``repo_view`` snapshot; traversal is bounded by
    the depth cap enforced in ``normalize_target``."""
    total = len(parts)
    for depth in range(1, total + 1):
        prefix = tuple(parts[:depth])
        kind = repo_view.path_type(prefix)
        if kind == PathType.SYMLINK:
            return (ReasonCode.PATH_SYMLINK_COMPONENT,)
        if depth == total and kind in (PathType.DIRECTORY, PathType.OTHER):
            return (ReasonCode.PATH_NON_REGULAR_TARGET,)
    return ()


def match_protected(
    path: NormalizedPath,
) -> Tuple[Optional[str], Tuple[ReasonCode, ...]]:
    """Return ``("block", ...)`` for Tier 1, ``("high", ...)`` for Tier 2, or
    ``(None, ())`` if the path is not protected."""
    if _protected.is_tier1(path.fold):
        return ("block", (ReasonCode.PROTECTED_BLOCKED,))
    if _protected.is_tier2(path.fold):
        return ("high", (ReasonCode.PROTECTED_HIGH_RISK,))
    return (None, ())


def assess_path(raw_target: str, repo_view: RepoView) -> PathAssessment:
    """Assess the target path only (not the diff). Never returns an auto-clear
    decision: the outcome is BLOCKED or NEEDS_APPROVAL, never ALLOWED."""
    norm = normalize_target(raw_target)
    if not norm.ok:
        return PathAssessment(Decision.BLOCKED, Risk.HIGH, norm.reasons)

    tier, protected_reasons = match_protected(norm.path)
    if tier == "block":
        return PathAssessment(Decision.BLOCKED, Risk.HIGH, protected_reasons)

    type_reasons = check_symlink_and_type(norm.path.parts, repo_view)
    if type_reasons:
        return PathAssessment(Decision.BLOCKED, Risk.HIGH, type_reasons)

    if tier == "high":
        return PathAssessment(Decision.NEEDS_APPROVAL, Risk.HIGH, protected_reasons)

    return PathAssessment(Decision.NEEDS_APPROVAL, Risk.LOW, ())
