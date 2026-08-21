"""Which GitHub repository a project publishes to. Read from the repository.

The identity chain, and why it has no configurable link
--------------------------------------------------------

    durable dispatch → project_id → host project registry → verified root
                     → that checkout's own `origin` URL → owner/repo

Every hop is something Cofferdam already trusts for other reasons, and the last
one is deliberately **the repository's own remote** rather than a field in a
configuration file. A ``remote_url`` in the project registry would be a
caller-editable value that decides where an authenticated push goes — and the
registry already refuses ``token`` and ``secret`` fields for exactly that reason.
Reading ``git remote get-url origin`` means the answer comes from the same
checkout the worker branch was cut from, so pointing a publisher somewhere else
requires changing the repository itself.

There is no parameter anywhere above this that names a repository, a remote, or
a URL.

Parsing, and refusing what cannot be parsed
--------------------------------------------

Only GitHub HTTPS and SSH forms are recognised, and only into an
``owner``/``repo`` pair matching GitHub's own naming rules. A URL that does not
parse is a refusal, never a guess: guessing here would mean an authenticated
push aimed at an address nobody verified.

A URL carrying **userinfo** — ``https://user:token@github.com/...`` — is refused
outright rather than stripped. Cofferdam supplies its own credential through
Git's credential store, so a remote that already carries one is a repository
configured in a way this publisher will not silently work around.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

GIT_TIMEOUT_SECONDS = 30.0

#: GitHub's own rules: owners and repositories are alphanumerics, hyphen,
#: underscore and dot, and a repository may not be `.` or `..`.
_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"

#: The ``.git`` suffix is stripped *before* matching rather than expressed as an
#: optional group. ``_NAME`` accepts dots, so an optional trailing group is never
#: reached — the name pattern swallows ``.git`` and the repository comes out
#: called ``cofferdam.git``. Found by the first parse of this host's own remote.
_DOT_GIT = ".git"

_HTTPS = re.compile(
    r"^https://(?:www\.)?github\.com/(?P<owner>" + _NAME + r")/(?P<repo>" + _NAME + r")$"
)
_SSH = re.compile(
    r"^(?:ssh://)?git@github\.com[:/](?P<owner>" + _NAME + r")/(?P<repo>" + _NAME + r")$"
)

#: The only remote name Cofferdam publishes to. Not a parameter: a caller that
#: could name a remote could add one first.
REMOTE_NAME = "origin"


class RemoteUnresolved(Exception):
    """This project has no usable GitHub remote, and says why.

    Fails closed. Every branch in :func:`resolve` that cannot establish an exact
    ``owner/repo`` raises rather than returning a best guess, because the value
    is about to receive an authenticated push.
    """

    def __init__(self, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.detail = detail


@dataclass(frozen=True)
class GitHubRepository:
    """One repository, named the way GitHub names it."""

    owner: str
    repo: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def api_path(self) -> str:
        return f"/repos/{self.owner}/{self.repo}"

    @property
    def https_url(self) -> str:
        """The URL Cofferdam pushes to. **Never carries a credential.**

        The token reaches Git through its credential store, so this stays a
        plain URL — one that can appear in a log line, an error message or a
        read model without leaking anything.
        """
        return f"https://github.com/{self.owner}/{self.repo}.git"

    def to_dict(self) -> Dict[str, Any]:
        return {"owner": self.owner, "repo": self.repo, "full_name": self.full_name}


def parse(url: str) -> GitHubRepository:
    """``owner/repo`` from a remote URL, or a refusal."""
    candidate = (url or "").strip()
    if not candidate:
        raise RemoteUnresolved("this project has no remote configured")
    if "@" in candidate and not candidate.startswith(("git@", "ssh://git@")):
        # userinfo in an HTTPS URL. Refused rather than stripped -- see module.
        raise RemoteUnresolved(
            "this project's remote carries an embedded credential",
            detail="Cofferdam supplies its own; refusing to publish through that remote",
        )
    candidate = candidate.rstrip("/")
    if candidate.endswith(_DOT_GIT):
        candidate = candidate[: -len(_DOT_GIT)]
    for pattern in (_HTTPS, _SSH):
        found = pattern.match(candidate)
        if found:
            repo = found.group("repo")
            if repo in (".", ".."):  # pragma: no cover - regex already excludes
                break
            return GitHubRepository(owner=found.group("owner"), repo=repo)
    raise RemoteUnresolved(
        "this project's remote is not a GitHub repository Cofferdam can publish to"
    )


def resolve(project_root: Path) -> GitHubRepository:
    """The GitHub repository this checkout pushes to. Read with Git, not guessed."""
    result = _git(Path(project_root), ["remote", "get-url", REMOTE_NAME])
    if result.returncode != 0:
        raise RemoteUnresolved(
            "this project has no '" + REMOTE_NAME + "' remote",
            detail=(result.stderr or "").strip()[:200] or None,
        )
    return parse((result.stdout or "").strip())


def _git(root: Path, arguments) -> subprocess.CompletedProcess:
    """One read-only Git call. Constant argv, no shell, built environment.

    The same narrow shape ``worker.worktree`` uses: a list, never a string; a
    timeout; an environment of literal keys rather than the daemon's own; and no
    value interpolated from anything a caller or a model supplied.
    """
    return subprocess.run(
        ["git", *arguments],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        stdin=subprocess.DEVNULL,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": "/nonexistent",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_PAGER": "cat",
        },
    )


__all__ = [
    "GIT_TIMEOUT_SECONDS",
    "REMOTE_NAME",
    "GitHubRepository",
    "RemoteUnresolved",
    "parse",
    "resolve",
]
