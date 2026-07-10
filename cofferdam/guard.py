"""The deterministic guard — the authority.

``evaluate(proposal, repo_view)`` combines the gates into a single verdict:

1. lexical path normalization / containment,
2. Tier-1 protected paths (unconditional block),
3. diff structural validity + strict diff-path matching,
4. symlink / non-regular target via the read-only repo view,
5. Tier-2 protected paths (needs-approval, high risk),
6. otherwise needs-approval.

The signature is **frozen at exactly two positional parameters**
``(proposal, repo_view)``. There is no advisory / model channel anywhere in
this path, so nothing can relax a verdict (the I-3 seam). The outcome is only
ever ``BLOCKED`` or ``NEEDS_APPROVAL`` — never an auto-clear ``ALLOWED``. It is
a pure function of its inputs (plus one ``repo_view`` snapshot); it performs no
network, no subprocess, and no mutation. Any internal error is converted to
``BLOCKED`` (fail-closed).

``repo_view`` outputs never *relax* a decision: the lexical and protected-path
gates run regardless of what the view reports.
"""

from __future__ import annotations

from typing import Iterable

from .diffcheck import validate_diff
from .paths import check_symlink_and_type, match_protected, normalize_target
from .proposal import Proposal
from .repo_view import RepoView
from .verdict import Decision, ReasonCode, Risk, Verdict


def _ordered(reasons: Iterable[ReasonCode]):
    return tuple(sorted(set(reasons), key=lambda code: code.value))


def _blocked(reasons: Iterable[ReasonCode]) -> Verdict:
    return Verdict(Decision.BLOCKED, Risk.HIGH, _ordered(reasons))


def evaluate(proposal: Proposal, repo_view: RepoView) -> Verdict:
    try:
        return _evaluate(proposal, repo_view)
    except Exception:
        return _blocked((ReasonCode.GUARD_INTERNAL_ERROR,))


def _evaluate(proposal: Proposal, repo_view: RepoView) -> Verdict:
    norm = normalize_target(proposal.target_path)
    if not norm.ok:
        return _blocked(norm.reasons)

    tier, protected_reasons = match_protected(norm.path)
    if tier == "block":
        return _blocked(protected_reasons)

    diff_result = validate_diff(proposal.diff, norm.path)
    if not diff_result.ok:
        return _blocked(diff_result.reasons)

    type_reasons = check_symlink_and_type(norm.path.parts, repo_view)
    if type_reasons:
        return _blocked(type_reasons)

    if tier == "high":
        return Verdict(Decision.NEEDS_APPROVAL, Risk.HIGH, _ordered(protected_reasons))

    return Verdict(Decision.NEEDS_APPROVAL, Risk.LOW, ())
