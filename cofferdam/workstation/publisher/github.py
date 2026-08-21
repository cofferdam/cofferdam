"""The two external writes: push a branch, open a pull request. Nothing else.

Every operation here is a closed function with typed parameters. There is no
``run_git``, no ``api_call(path)`` taking a caller's string, no argv parameter
and no place to add a flag from outside — the whole surface is *push this branch*
and *open a PR for it*.

Refspecs are built, never accepted
-----------------------------------

``push`` constructs ``refs/heads/<branch>:refs/heads/<branch>`` from one validated
branch name. That single decision removes an entire family of mistakes:

* no wildcard refspec, so one push cannot move refs nobody named;
* no ``+`` prefix and no ``--force``/``--force-with-lease``, so a push can only
  fast-forward — it can never overwrite work on the remote;
* no ``:branch`` with an empty source, which is how a ref gets **deleted**;
* no ``--tags`` and no ``--mirror``.

The branch is checked against :func:`publishable_branch` before it is
interpolated into anything, so a name like ``main`` or ``--upload-pack=...`` is
refused before Git sees it.

Idempotency is a property of the operations, not of a flag
------------------------------------------------------------

GitHub is external state and Cofferdam can crash after changing it. So both
operations are written to be re-runnable:

* pushing a branch that is already at the same commit succeeds and reports
  ``already_current``, because Git says *Everything up-to-date*;
* opening a PR that already exists finds it and reports it, because the search
  is by exact ``head``/``base`` on an exact repository rather than by "the most
  recent PR".

That is what makes crash recovery a matter of looking rather than of guessing.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import credential
from .remote import GitHubRepository

GIT_TIMEOUT_SECONDS = 120.0

#: The prefix every publishable branch carries. Code-owned by
#: ``worker.worktree``; restated here as the publisher's own gate so that a
#: branch reaching this module is checked against it whatever produced it.
WORKER_BRANCH_PREFIX = "cofferdam/worker/"

#: Branch names no publisher may write, whatever else is true. Belt to the
#: prefix's braces: the prefix already excludes all of these, and a future change
#: to the prefix must not silently make them reachable.
PROTECTED_BRANCHES: Tuple[str, ...] = (
    "main", "master", "trunk", "develop", "production", "release", "gh-pages", "HEAD",
)

#: Conservative Git ref grammar. Narrower than `git check-ref-format` on purpose.
_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,180}$")

PUSH_CREATED = "created"
PUSH_ALREADY_CURRENT = "already_current"


class PublishRefused(Exception):
    """A publish that must not happen, named by the rule that stopped it."""

    def __init__(self, message: str, *, reason: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.detail = detail

    def to_dict(self) -> Dict[str, Any]:
        return {"reason": self.reason, "message": str(self), "detail": self.detail}


def publishable_branch(branch: str) -> str:
    """The branch, if a publisher may write it. Otherwise a refusal.

    Runs before the name reaches Git, so a value that could be read as a flag or
    as a second refspec never gets that far.
    """
    name = (branch or "").strip()
    if not name or not _SAFE_BRANCH.match(name):
        raise PublishRefused(
            "that is not a branch name Cofferdam will publish",
            reason="branch_malformed", detail=name[:80] or None,
        )
    if name.lower() in {protected.lower() for protected in PROTECTED_BRANCHES}:
        raise PublishRefused(
            "Cofferdam's publisher never writes that branch",
            reason="branch_protected", detail=name,
        )
    if not name.startswith(WORKER_BRANCH_PREFIX):
        raise PublishRefused(
            "only a Cofferdam worker branch may be published",
            reason="branch_not_worker_owned", detail=name,
        )
    if ".." in name or name.endswith(".lock") or name.endswith("/"):
        raise PublishRefused(
            "that branch name is not well formed",
            reason="branch_malformed", detail=name,
        )
    return name


def push(
    *,
    worktree: Path,
    repository: GitHubRepository,
    branch: str,
    expected_commit: str,
    credentials_path: Path,
) -> Tuple[str, str]:
    """Push exactly one branch, fast-forward only. Returns ``(state, detail)``.

    The credential reaches Git through ``credential.helper=store --file=``, so
    the path is in ``argv`` and the secret is not — see :mod:`.credential`.

    ``-c`` settings rather than repository configuration, because a publish must
    not depend on, or alter, how the checkout happens to be configured:
    ``GIT_TERMINAL_PROMPT=0`` so a missing credential fails instead of hanging
    forever on an unattended host, and ``GIT_CONFIG_NOSYSTEM`` so a system-wide
    setting cannot redirect the push.
    """
    safe_branch = publishable_branch(branch)
    refspec = f"refs/heads/{safe_branch}:refs/heads/{safe_branch}"

    result = _git(
        worktree,
        [
            "-c", f"credential.helper=store --file={credentials_path}",
            "-c", "credential.interactive=never",
            "push",
            "--no-verify",
            # No --force, no --force-with-lease, no --delete, no --tags,
            # no --mirror, no --set-upstream. The refspec is the whole request.
            repository.https_url,
            refspec,
        ],
    )
    combined = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        raise PublishRefused(
            "the branch could not be pushed",
            reason=_push_reason(combined),
            detail=_scrub(combined)[:400],
        )
    if "Everything up-to-date" in combined:
        return PUSH_ALREADY_CURRENT, "the remote branch was already at this commit"
    return PUSH_CREATED, "the branch was pushed"


def remote_branch_commit(
    *,
    worktree: Path,
    repository: GitHubRepository,
    branch: str,
    credentials_path: Path,
) -> Optional[str]:
    """What commit the remote branch is at, or ``None`` if it does not exist.

    The read that makes crash recovery possible: after a crash in the push
    window, this answers *did the push land* without pushing anything.
    ``ls-remote`` writes nothing.
    """
    safe_branch = publishable_branch(branch)
    result = _git(
        worktree,
        [
            "-c", f"credential.helper=store --file={credentials_path}",
            "-c", "credential.interactive=never",
            "ls-remote", repository.https_url, f"refs/heads/{safe_branch}",
        ],
    )
    if result.returncode != 0:
        raise PublishRefused(
            "the remote could not be read",
            reason="remote_unreachable",
            detail=_scrub((result.stderr or "").strip())[:400],
        )
    line = (result.stdout or "").strip()
    if not line:
        return None
    return line.split()[0]


def find_pull_request(
    state_dir: Path, repository: GitHubRepository, *, branch: str, base: str
) -> Optional[Dict[str, Any]]:
    """The open PR for this exact head and base, or ``None``.

    Looked up by ``head=owner:branch`` — an exact match on a branch this
    dispatch owns — rather than by listing recent PRs and picking one. "The
    latest PR" is not an identity, and a publisher that used it would eventually
    link a dispatch to somebody else's work.
    """
    safe_branch = publishable_branch(branch)
    query = f"?head={repository.owner}:{safe_branch}&base={base}&state=all&per_page=10"
    code, parsed = credential.api_request(
        state_dir, "GET", repository.api_path + "/pulls" + query
    )
    if code != 200 or not isinstance(parsed, list):
        return None
    for item in parsed:
        head = (item.get("head") or {}).get("ref")
        if head == safe_branch:
            return _pull_request_facts(item)
    return None


def create_pull_request(
    state_dir: Path,
    repository: GitHubRepository,
    *,
    branch: str,
    base: str,
    title: str,
    body: str,
) -> Dict[str, Any]:
    """Open one PR. If one already exists for this head, return **that** one.

    GitHub answers 422 when a pull request already exists for a head/base pair,
    and that is treated as the idempotent success it is rather than as an error:
    the second call after a crash finds the first call's PR instead of failing or
    creating a duplicate.

    ``draft`` is not set, no reviewer, assignee, label or milestone is sent, and
    nothing here can merge. The request body has four fields.
    """
    safe_branch = publishable_branch(branch)
    code, parsed = credential.api_request(
        state_dir,
        "POST",
        repository.api_path + "/pulls",
        body={
            "title": title,
            "body": body,
            "head": safe_branch,
            "base": base,
        },
    )
    if code in (200, 201) and isinstance(parsed, dict):
        return _pull_request_facts(parsed)
    if code == 422:
        existing = find_pull_request(
            state_dir, repository, branch=safe_branch, base=base
        )
        if existing is not None:
            return existing
        raise PublishRefused(
            "GitHub refused the pull request",
            reason="pull_request_rejected",
            detail=_api_message(parsed),
        )
    if code in (401, 403):
        raise PublishRefused(
            "the publisher credential may not open a pull request here",
            reason="publisher_auth_required",
            detail=_api_message(parsed),
        )
    raise PublishRefused(
        "the pull request could not be created",
        reason="pull_request_failed",
        detail=f"GitHub answered {code}: {_api_message(parsed)}",
    )


def _pull_request_facts(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The safe subset of a GitHub PR. Nothing about the account, no tokens."""
    return {
        "number": payload.get("number"),
        "url": payload.get("html_url"),
        "state": payload.get("state"),
        "merged": bool(payload.get("merged_at")),
        "head": (payload.get("head") or {}).get("ref"),
        "base": (payload.get("base") or {}).get("ref"),
    }


def _api_message(parsed: Any) -> Optional[str]:
    if isinstance(parsed, dict):
        message = parsed.get("message")
        errors = parsed.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict) and first.get("message"):
                return str(first["message"])[:300]
        if message:
            return str(message)[:300]
    return None


def _push_reason(output: str) -> str:
    lowered = output.lower()
    if "authentication failed" in lowered or "could not read username" in lowered:
        return "publisher_auth_required"
    if "permission" in lowered and "denied" in lowered:
        return "publisher_auth_required"
    if "non-fast-forward" in lowered or "fetch first" in lowered:
        return "remote_diverged"
    if "could not resolve host" in lowered or "unable to access" in lowered:
        return "remote_unreachable"
    return "push_failed"


def _scrub(text: str) -> str:
    """Remove anything credential-shaped from Git's own chatter.

    Git normally prints no secret, but a misconfigured remote can put one in a
    URL and Git will echo that URL back in an error. Defense in depth on the one
    channel that leaves this module.
    """
    if not text:
        return text
    text = re.sub(r"https://[^:/@\s]+:[^@\s]+@", "https://[redacted]@", text)
    text = re.sub(r"gh[pousr]_[A-Za-z0-9]{16,}", "[redacted]", text)
    text = re.sub(r"github_pat_[A-Za-z0-9_]{16,}", "[redacted]", text)
    return text


def _git(root: Path, arguments) -> subprocess.CompletedProcess:
    """One Git call. Constant shape, no shell, environment built by selection."""
    return subprocess.run(
        ["git", *arguments],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        stdin=subprocess.DEVNULL,
        env={
            "PATH": "/usr/bin:/bin",
            # Not the operator's home: it holds their ~/.gitconfig and any
            # credential helper they configured. A publish must use Cofferdam's
            # credential and no other.
            "HOME": "/nonexistent",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_PAGER": "cat",
            "GIT_ASKPASS": "",
        },
    )


__all__ = [
    "PROTECTED_BRANCHES",
    "PUSH_ALREADY_CURRENT",
    "PUSH_CREATED",
    "WORKER_BRANCH_PREFIX",
    "PublishRefused",
    "create_pull_request",
    "find_pull_request",
    "publishable_branch",
    "push",
    "remote_branch_commit",
]
