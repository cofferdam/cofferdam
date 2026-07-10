"""Positive-grammar validator for a narrow git unified-diff subset (pure).

This is a *validator*, not an applier: no ``git``, no subprocess, no I/O. It
accepts only the minimal unified-diff shape and rejects everything else
(fail-closed). Accepting a narrow positive grammar — rather than blocklisting
bad features — means unknown or exotic constructs are refused by default.

Accepted grammar (newlines normalized to ``\\n`` first):

    "--- " <path>              exactly one old-file header
    "+++ " <path>              exactly one new-file header
    ( hunk )+                  one or more hunks
      "@@ -a[,b] +c[,d] @@"    optional trailing section heading allowed
      ( " " | "+" | "-" ) line body lines; counts must match the header
      "\\ No newline at end of file"   optional marker (does not count)

Deliberately rejected (so binary / rename / mode / multi-file all fail closed):
``diff --git`` / ``index`` / mode / rename / copy header lines, binary-patch
markers, quoted paths, and any second file header. Every path reference (both
``---`` and ``+++``, minus the ``a/`` / ``b/`` prefix, ``/dev/null`` aside) must
equal ``target_path`` after normalization, or the diff is rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from .paths import NormalizedPath, normalize_target
from .verdict import ReasonCode

# Conservative, named caps (documented; bound work and reject oversized input).
MAX_DIFF_BYTES = 1_000_000
MAX_DIFF_LINES = 20_000
MAX_HUNKS = 2_000
MAX_LINE_LENGTH = 10_000

_HUNK_RE = re.compile(
    r"^@@ -(?P<os>\d+)(?:,(?P<oc>\d+))? \+(?P<ns>\d+)(?:,(?P<nc>\d+))? @@(?: .*)?$"
)
_NO_NEWLINE_MARKER = "\\ No newline at end of file"


@dataclass(frozen=True)
class DiffCheckResult:
    ok: bool
    reasons: Tuple[ReasonCode, ...]


def _fail(reason: ReasonCode) -> DiffCheckResult:
    return DiffCheckResult(False, (reason,))


_OK = DiffCheckResult(True, ())


def _resolve_diff_path(raw: str, prefix: str):
    """Return the normalized parts tuple, or the sentinel ``"devnull"`` /
    ``"invalid"``."""
    if raw == "/dev/null":
        return "devnull"
    candidate = raw[len(prefix):] if raw.startswith(prefix) else raw
    norm = normalize_target(candidate)
    if not norm.ok:
        return "invalid"
    return norm.path.parts


def _match_paths(minus_raw: str, plus_raw: str, target: NormalizedPath) -> Optional[ReasonCode]:
    minus = _resolve_diff_path(minus_raw, "a/")
    plus = _resolve_diff_path(plus_raw, "b/")
    if minus == "devnull" and plus == "devnull":
        return ReasonCode.DIFF_MALFORMED
    for side in (minus, plus):
        if side == "devnull":
            continue
        if side == "invalid" or side != target.parts:
            return ReasonCode.DIFF_PATH_MISMATCH
    return None


def validate_diff(diff_text: str, target: NormalizedPath) -> DiffCheckResult:
    """Validate ``diff_text`` against the narrow grammar, fail-closed.

    ``target`` is the already-normalized target path; every diff path reference
    must match it. Never raises; returns a ``DiffCheckResult``.
    """
    try:
        return _validate(diff_text, target)
    except Exception:
        return _fail(ReasonCode.DIFF_MALFORMED)


def _validate(diff_text: str, target: NormalizedPath) -> DiffCheckResult:
    if len(diff_text.encode("utf-8")) > MAX_DIFF_BYTES:
        return _fail(ReasonCode.DIFF_TOO_LARGE)

    text = diff_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if len(lines) > MAX_DIFF_LINES:
        return _fail(ReasonCode.DIFF_TOO_LARGE)
    if any(len(line) > MAX_LINE_LENGTH for line in lines):
        return _fail(ReasonCode.DIFF_TOO_LARGE)

    # Binary-patch markers are rejected outright, wherever they appear.
    for line in lines:
        if line.startswith("GIT binary patch"):
            return _fail(ReasonCode.DIFF_BINARY)
        if line.startswith("Binary files ") and line.endswith(" differ"):
            return _fail(ReasonCode.DIFF_BINARY)

    total = len(lines)
    index = 0

    if index >= total or not lines[index].startswith("--- "):
        return _fail(ReasonCode.DIFF_MALFORMED)
    minus_path = lines[index][4:]
    index += 1
    if index >= total or not lines[index].startswith("+++ "):
        return _fail(ReasonCode.DIFF_MALFORMED)
    plus_path = lines[index][4:]
    index += 1

    mismatch = _match_paths(minus_path, plus_path, target)
    if mismatch is not None:
        return _fail(mismatch)

    hunks = 0
    while index < total:
        line = lines[index]
        if line == "" and index == total - 1:
            # Trailing newline artifact from the final split.
            index += 1
            continue
        if not line.startswith("@@ "):
            if (
                line.startswith("--- ")
                or line.startswith("+++ ")
                or line.startswith("diff --git")
            ):
                return _fail(ReasonCode.DIFF_MULTIPLE_FILES)
            return _fail(ReasonCode.DIFF_MALFORMED)

        header = _HUNK_RE.match(line)
        if header is None:
            return _fail(ReasonCode.DIFF_MALFORMED)
        old_count = int(header.group("oc")) if header.group("oc") is not None else 1
        new_count = int(header.group("nc")) if header.group("nc") is not None else 1
        index += 1

        old_seen = 0
        new_seen = 0
        while index < total and (old_seen < old_count or new_seen < new_count):
            body = lines[index]
            if body == "" and index == total - 1:
                break  # ran out of body before satisfying the counts
            if body == _NO_NEWLINE_MARKER:
                index += 1
                continue
            if body.startswith(" "):
                old_seen += 1
                new_seen += 1
            elif body.startswith("+"):
                new_seen += 1
            elif body.startswith("-"):
                old_seen += 1
            else:
                return _fail(ReasonCode.DIFF_MALFORMED)
            index += 1

        if old_seen != old_count or new_seen != new_count:
            return _fail(ReasonCode.DIFF_TRUNCATED)

        while index < total and lines[index] == _NO_NEWLINE_MARKER:
            index += 1

        hunks += 1
        if hunks > MAX_HUNKS:
            return _fail(ReasonCode.DIFF_TOO_LARGE)

    if hunks == 0:
        return _fail(ReasonCode.DIFF_EMPTY)
    return _OK
