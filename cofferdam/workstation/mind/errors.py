"""Refusals a person should see, with stable codes to branch on.

The same shape as :mod:`..tasks.errors` and :mod:`..workspace.errors`: not an
HTTP concern, because the service layer maps a code to a status exactly once.

Two rules hold for every message in this file.

**No message carries a path.** Not the vault root, not the project root, not the
document name. A refusal is rendered on a phone, logged, and screenshotted, and
the whole point of the role model is that the client never learns where anything
lives — a message that leaked it would hand back the vocabulary the API is
specifically denied.

**Each code says which authority refused**, because they are edited in different
places. "No grant" sends somebody to `mind-grant.json`; "role not configured"
sends them to `workspaces.json`; "stale" sends them to the document itself. One
generic refusal would send them to all three.
"""

from __future__ import annotations

from typing import Optional

#: The scope word is not one this product has. Distinct from an invalid role so
#: that a client asking for a scope from a later version gets a sentence about
#: the scope rather than a confusing complaint about the role.
CODE_SCOPE_INVALID = "mind_scope_invalid"

#: The role word is not in this scope's vocabulary. **This is what a path in the
#: role field produces**, and it is the refusal that happens before anything is
#: resolved against a filesystem.
CODE_ROLE_INVALID = "mind_role_invalid"

#: A real role that this host has not mapped to a document. Not a licence to
#: guess a filename: a role with no mapping has no file, even when a file with
#: the obvious name is sitting right there.
CODE_ROLE_UNCONFIGURED = "mind_role_unconfigured"

#: Mapped, and the file cannot be used **right now** — missing, not a regular
#: file, reached through a link, outside the root it must stay inside, or
#: unreadable. Deliberately one code: publishing which of those it was would
#: describe the host's filesystem to a client, one refusal at a time.
CODE_ROLE_UNAVAILABLE = "mind_role_unavailable"

#: There is no global vault grant on this host. The vault is not refused — it
#: does not exist as far as this process is concerned, which is the stronger
#: statement D-2026-08-08-2 makes about absent surfaces.
CODE_GLOBAL_GRANT_MISSING = "mind_global_grant_missing"

#: The proposed document is missing, empty, not text, or past the bound. An
#: empty document is included on purpose: a mutation that leaves nothing behind
#: is a deletion wearing a mutation's clothes, and deletion is never proposable
#: (D-2026-08-11-4, point 6).
CODE_CONTENT_INVALID = "mind_content_invalid"

#: The one-line why is missing or too long. Required rather than optional,
#: because "why does my USER.md say this" is the question point 5 of
#: D-2026-08-11-4 says must have an answer.
CODE_REASON_INVALID = "mind_reason_invalid"

#: A provenance word this build cannot honestly record. Only the device-token
#: surface exists in this milestone, so `user` is the only value a proposal can
#: carry; `planner` and `cofferdam` are reserved in the store's vocabulary and
#: unreachable until something that is one of them exists. Unreachable from any
#: route — there is no `source` field in any body — and present so that the
#: first caller which tries to assign one gets a refusal rather than a row.
CODE_SOURCE_INVALID = "mind_source_invalid"

CODE_PROPOSAL_UNKNOWN = "mind_proposal_unknown"

#: Accept or reject arrived for a proposal that is no longer pending. The state
#: is named in the detail, the same way ``task_already_finished`` names it: a
#: decided proposal's history is not rewritten.
CODE_PROPOSAL_NOT_PENDING = "mind_proposal_not_pending"

#: **The heart of the milestone.** The document changed since the proposal was
#: created, so the diff a person reviewed is not the diff that would land. The
#: proposal is marked stale and nothing is written. There is no three-way merge
#: and no silent refresh: a new proposal is a new review.
CODE_PROPOSAL_STALE = "mind_proposal_stale"

#: **The host authority that resolved the target changed.** Distinct from
#: staleness on purpose: staleness means "this document says something else
#: now", and this means "this role *is* something else now". A content-only
#: check cannot tell them apart — remap a role to a byte-identical file and the
#: base hash still matches — so the proposal carries a fingerprint of the
#: authority as well as of the bytes, and this is what a mismatch reports.
CODE_TARGET_AUTHORITY_CHANGED = "mind_target_authority_changed"

#: This platform cannot resolve a target without a pathname race, so it does not
#: resolve one at all. There is deliberately no fallback to a pathname walk: a
#: weaker guarantee that looks identical from the outside is worse than a
#: refusal, because nothing downstream would know it had been weakened.
CODE_RESOLUTION_UNSUPPORTED = "mind_resolution_unsupported"

#: The active workspace is not the one the proposal was made in. Refused rather
#: than applied against the current workspace, because a proposal that followed
#: a workspace switch would land somebody's edit in a different project's
#: repository — and it would render perfectly well afterwards.
CODE_PROPOSAL_WORKSPACE_CHANGED = "mind_proposal_workspace_changed"

#: The atomic replace itself failed. The target is unchanged — that is what
#: makes the temporary-file-then-replace protocol worth the trouble — and the
#: proposal stays pending so the operation can be retried once the disk problem
#: is fixed.
CODE_APPLY_FAILED = "mind_apply_failed"


class MindError(Exception):
    """A refusal with a stable code, message and optional detail."""

    def __init__(self, code: str, message: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class ScopeInvalid(MindError):
    def __init__(self) -> None:
        super().__init__(
            CODE_SCOPE_INVALID,
            "that is not a kind of memory this workstation has",
            "the scopes are 'project' and 'global'",
        )


class RoleInvalid(MindError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_ROLE_INVALID,
            "that is not a document role",
            detail or "a request names a role, never a file",
        )


class RoleUnconfigured(MindError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_ROLE_UNCONFIGURED,
            "no document is mapped to that role on this workstation",
            detail or "map it in the host's configuration; Cofferdam never picks a file itself",
        )


class RoleUnavailable(MindError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_ROLE_UNAVAILABLE,
            "the document for that role cannot be read right now",
            detail or "it is missing, is not an ordinary file, or is reached through a link",
        )


class GlobalGrantMissing(MindError):
    def __init__(self) -> None:
        super().__init__(
            CODE_GLOBAL_GRANT_MISSING,
            "this workstation has not been granted a global memory vault",
            "the grant is written on the host; there is no route that creates one",
        )


class ContentInvalid(MindError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_CONTENT_INVALID,
            "that document content cannot be proposed as written",
            detail or "it must be text, not empty, and within the size bound",
        )


class ReasonInvalid(MindError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_REASON_INVALID,
            "a proposal needs one short line saying why",
            detail or "so that 'why does this document say this' has an answer later",
        )


class SourceInvalid(MindError):
    def __init__(self) -> None:
        super().__init__(
            CODE_SOURCE_INVALID,
            "that provenance cannot be recorded on this workstation",
            "only a person on the device-token surface can propose a memory change here",
        )


class ProposalUnknown(MindError):
    def __init__(self) -> None:
        super().__init__(
            CODE_PROPOSAL_UNKNOWN,
            "no such memory proposal on this workstation",
            "it may have been removed with the proposal state",
        )


class ProposalNotPending(MindError):
    def __init__(self, state: str) -> None:
        super().__init__(
            CODE_PROPOSAL_NOT_PENDING,
            "that proposal is already " + state,
            "a decided proposal is not decided again; make a new one",
        )
        self.state = state


class ProposalStale(MindError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_PROPOSAL_STALE,
            "that document changed since the proposal was made, so nothing was written",
            detail or "read the document as it is now and propose again",
        )


class TargetAuthorityChanged(MindError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_TARGET_AUTHORITY_CHANGED,
            "that role now refers to a different document, so nothing was written",
            detail or "the change was reviewed against the previous one; read it again and propose",
        )


class ResolutionUnsupported(MindError):
    def __init__(self) -> None:
        super().__init__(
            CODE_RESOLUTION_UNSUPPORTED,
            "this workstation cannot open memory documents safely",
            "the platform lacks directory-relative file access, and there is no fallback",
        )


class ProposalWorkspaceChanged(MindError):
    def __init__(self) -> None:
        super().__init__(
            CODE_PROPOSAL_WORKSPACE_CHANGED,
            "that proposal belongs to a different workspace than the active one",
            "switch back to the workspace it was made in to decide it",
        )


class ApplyFailed(MindError):
    def __init__(self, detail: Optional[str] = None) -> None:
        super().__init__(
            CODE_APPLY_FAILED,
            "the document could not be written, and was left unchanged",
            detail or "the proposal is still pending and can be accepted again",
        )


__all__ = [
    "ApplyFailed",
    "CODE_APPLY_FAILED",
    "CODE_CONTENT_INVALID",
    "CODE_GLOBAL_GRANT_MISSING",
    "CODE_PROPOSAL_NOT_PENDING",
    "CODE_PROPOSAL_STALE",
    "CODE_PROPOSAL_UNKNOWN",
    "CODE_PROPOSAL_WORKSPACE_CHANGED",
    "CODE_REASON_INVALID",
    "CODE_ROLE_INVALID",
    "CODE_ROLE_UNAVAILABLE",
    "CODE_ROLE_UNCONFIGURED",
    "CODE_RESOLUTION_UNSUPPORTED",
    "CODE_TARGET_AUTHORITY_CHANGED",
    "CODE_SCOPE_INVALID",
    "CODE_SOURCE_INVALID",
    "ContentInvalid",
    "GlobalGrantMissing",
    "MindError",
    "ProposalNotPending",
    "ProposalStale",
    "ProposalUnknown",
    "ProposalWorkspaceChanged",
    "ReasonInvalid",
    "RoleInvalid",
    "RoleUnavailable",
    "RoleUnconfigured",
    "ResolutionUnsupported",
    "ScopeInvalid",
    "SourceInvalid",
    "TargetAuthorityChanged",
]
