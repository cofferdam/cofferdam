"""Immutable, non-authorizing dry-run artifact (read-only).

Assembles the PR3a binding artifact for a guard-passing proposal: the exact
patch bytes, the canonical target, component hashes, and the ``bound_hash``.
Constructing it performs no mutation, no subprocess, no network, and no
rendering side effect.

**Single source of truth for the patch (balanced fix 1).** The bytes bound and
hashed are derived **internally** from the validated proposal —
``proposal.diff.encode("utf-8")`` — and are *not* a separate caller-supplied
input. This makes it impossible to validate one patch (via the guard) and bind
another. The same ``patch_bytes`` value feeds ``DryRunArtifact.patch_bytes`` (the
in-memory render/execute source), ``patch_hash``, and ``bound_hash``. No newline
normalization happens here — the exact UTF-8 encoding of the validated string is
bound, and the PR3c byte-exactness STOP gate is responsible for proving those
exact bytes can be checked and applied without transformation.

**Single authoritative root (balanced fix 3).** The repository root comes solely
from the injected ``repo_view`` (``root_real_path`` / ``root_bytes``); there is
no separate ``repo_root`` argument, so target resolution, pre-state reading, and
``repo_root_id`` cannot diverge across two roots.

This is emphatically **not** an approval: there is no nonce, no ``approval_id``,
no expiry, no ledger, and no execution capability — those are PR3b/PR3c. The
in-memory ``patch_bytes`` is the render source; persisted approval/audit records
(PR3b/PR3d) are content-light and are not produced here.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import canonicalize, hashing
from .diffcheck import MAX_DIFF_BYTES as MAX_PATCH_BYTES
from .guard import evaluate
from .paths import normalize_target
from .prestate import read_prestate
from .proposal import Proposal
from .repo_view import RepoView
from .verdict import Decision

SCHEMA_VERSION = 1


class DryRunError(Exception):
    """The dry-run artifact cannot be built (invalid path, blocked proposal,
    over-size patch, or an unreadable/unsafe target). Fail-closed."""


@dataclass(frozen=True)
class DryRunArtifact:
    """An immutable binding artifact. No approval state.

    ``patch_bytes`` is the exact validated patch (the in-memory render/execute
    source). Deliberately absent: nonce, approval_id, expiry, ledger reference,
    file content, and any execution capability.
    """

    schema_version: int
    relative_path: str        # display-safe repo-relative normalized path
    patch_bytes: bytes        # exact validated bytes == proposal.diff.encode("utf-8")
    canonical_path_hash: str
    patch_hash: str
    prestate_hash: str
    repo_root_id: str
    bound_hash: str
    guard_decision: str       # always "needs_approval" for a buildable artifact
    guard_risk: str


def build_dry_run_artifact(proposal: Proposal, repo_view: RepoView) -> DryRunArtifact:
    """Build the dry-run artifact from a proposal and the authoritative repo
    view. Read-only and deterministic; raises ``DryRunError`` (fail-closed) on
    any unsafe or invalid input.

    The patch bytes are derived internally from the validated proposal, and the
    repository root is taken solely from ``repo_view``.
    """
    # Single source of truth for the patch: the validated proposal string.
    patch_bytes = proposal.diff.encode("utf-8")
    if len(patch_bytes) > MAX_PATCH_BYTES:
        raise DryRunError("patch exceeds the maximum size")

    norm = normalize_target(proposal.target_path)
    if not norm.ok:
        raise DryRunError("target path is not valid")

    # The dry-run only exists for a guard-passing proposal (fail closed on BLOCKED).
    verdict = evaluate(proposal, repo_view)
    if verdict.decision is not Decision.NEEDS_APPROVAL:
        raise DryRunError("proposal is not in a needs-approval state")

    # Root comes only from the view — the single authoritative source.
    root_real = repo_view.root_real_path()
    try:
        canon = canonicalize.canonicalize_target(root_real, norm.path)
    except canonicalize.CanonicalizationError as exc:
        raise DryRunError(str(exc)) from exc

    prestate = read_prestate(repo_view, norm.path.parts)

    canonical_path_hash = hashing.canonical_path_hash(canon.path_bytes)
    patch_hash = hashing.patch_hash(patch_bytes)
    prestate_hash = prestate.hash_hex()
    repo_root_id = hashing.repo_root_id(repo_view.root_bytes())
    bound_hash = hashing.bound_hash(
        canonical_path_hash, patch_hash, prestate_hash, repo_root_id
    )

    return DryRunArtifact(
        schema_version=SCHEMA_VERSION,
        relative_path="/".join(norm.path.parts),
        patch_bytes=patch_bytes,
        canonical_path_hash=canonical_path_hash,
        patch_hash=patch_hash,
        prestate_hash=prestate_hash,
        repo_root_id=repo_root_id,
        bound_hash=bound_hash,
        guard_decision=verdict.decision.value,
        guard_risk=verdict.risk.value,
    )
