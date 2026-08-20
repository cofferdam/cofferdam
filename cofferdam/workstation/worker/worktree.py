"""Where a development worker is allowed to write: an isolated Git worktree.

The rule this module exists to hold
-----------------------------------

**Cofferdam establishes the worktree; the worker starts inside one already
authorized.** A coding worker needs write access to a repository, which is the
single largest authority Cofferdam has ever granted a model — and the only safe
version of it is one where the model never chose the location.

So there is no function here that takes a path from anywhere but code. The
caller supplies a :class:`~..tasks.projects.TaskProject` (resolved from the
host-owned registry) and a task id (minted by Task Core), and every path and ref
below is *derived* from those two. There is no ``worktree_path`` parameter, no
``branch`` parameter, no ``base_ref`` parameter, and no place a prompt's text
could become a filename.

Why the worktree lives outside the project
------------------------------------------

Under ``state/worker-worktrees/`` rather than inside the repository it belongs
to. A worktree nested in the project would appear in that project's own status,
would be reachable by a worker doing ordinary directory work, and — the deciding
reason — would make "the canonical checkout is untouched" a claim about
directory naming rather than about location.

What "untouched" means, precisely
---------------------------------

``git worktree add`` writes bookkeeping under the canonical repository's
``.git/worktrees/``. That is not avoidable and it is not a caveat being hidden:
it is how Git registers a linked worktree at all. What the canonical repository
does *not* get is any change to its working tree, its index, its ``HEAD``, or
any existing branch. :func:`assert_canonical_untouched` is the test hook that
pins exactly that, and it is deliberately specific about which of those it
checks — a helper that claimed more than it verified would be worse than none.

Branch naming
-------------

``cofferdam/worker/<task_id>``. Code-owned prefix, and the only variable part is
a Task Core id — 26 base32 characters behind ``task_`` — which is a valid Git ref
component by construction. Model text never reaches a ref: there is no argument
here it could arrive in, and :func:`branch_name` re-validates the id anyway so
that a future caller passing something else fails loudly instead of creating a
ref out of a sentence.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from ..tasks.identity import valid_task_id
from ..tasks.projects import valid_project_id, verify_root

#: Under ``state/``, beside the databases. Cofferdam-owned, and not inside any
#: project it serves.
WORKTREES_DIRNAME = "worker-worktrees"

#: The one branch namespace this build will create. A worker's branch is always
#: under it, so ``git branch --list 'cofferdam/worker/*'`` is a complete answer
#: to "what did the worker layer create here".
BRANCH_PREFIX = "cofferdam/worker/"

#: How long any one Git command may take. Git operations here are local and
#: fast; one that has not answered in this long is stuck, and a stuck dispatch
#: should fail truthfully rather than hang a worker slot forever.
GIT_TIMEOUT_SECONDS = 120.0

#: Refs a worker branch may never be, checked after construction rather than
#: trusted. Present by name so that a change to :func:`branch_name` that somehow
#: produced one of these is a failure instead of a catastrophe.
FORBIDDEN_BRANCHES = frozenset({"main", "master", "HEAD", "trunk", "release"})

_SAFE_REF = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]{0,120}\Z")


class WorktreeError(Exception):
    """Cofferdam could not establish an authorized place for the worker to work.

    Always raised *before* a worker is started. A dispatch that cannot get a
    worktree has not run anything, which is the only safe direction for this
    failure to fall.
    """

    def __init__(self, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.detail = detail


def branch_name(task_id: str) -> str:
    """The code-owned branch for one worker task. Derived, never supplied."""
    if not valid_task_id(task_id):
        raise WorktreeError("a worker branch is named after a task id", detail=str(task_id))
    name = BRANCH_PREFIX + task_id
    if not _SAFE_REF.match(name) or name in FORBIDDEN_BRANCHES:
        # Unreachable while task ids keep their grammar. Kept because the cost
        # of being wrong here is a worker committing onto a shared branch.
        raise WorktreeError("refusing to use that as a branch name", detail=name)
    return name


def worktrees_root(state_dir: Path) -> Path:
    return Path(state_dir) / WORKTREES_DIRNAME


def default_state_dir() -> Path:
    """Where worktrees go when nobody said, resolved from host configuration.

    The same shape ``cli.find_executable`` uses, and for the same reason: the
    value is host-owned — ``COFFERDAM_HOME`` or ``~/cofferdam``, read by the
    daemon's own config loader — so the adapter can resolve it itself instead of
    being handed a path through the registry.

    That matters more than it looks. ``build_registry`` takes booleans and
    nothing else, deliberately: a path parameter there would be the one argument
    on the code-owned adapter table that carries a location, and this function
    exists so that adding a worker did not have to widen it.
    """
    from ..config import load_config

    return load_config().state_dir


def worktree_path(state_dir: Path, project_id: str, task_id: str) -> Path:
    """Where one dispatch's worktree lives. A pure function of code-owned ids.

    Both components are validated rather than trusted, because this is the value
    that becomes a filesystem path and later a bind mount. A project id and a
    task id both have closed grammars with no separator in them, so no pair can
    produce a path that escapes the root.
    """
    if not valid_project_id(project_id):
        raise WorktreeError("that is not a project id", detail=str(project_id))
    if not valid_task_id(task_id):
        raise WorktreeError("that is not a task id", detail=str(task_id))
    return worktrees_root(state_dir) / project_id / task_id


@dataclass(frozen=True)
class DevelopmentWorktree:
    """One isolated place a worker may write, and what it was cut from."""

    project_id: str
    task_id: str
    path: Path
    branch: str
    base_commit: str
    canonical_root: Path

    def to_dict(self) -> dict:
        """The safe shape. **Neither path is in it.**

        The same rule that keeps ``root`` out of ``TaskProject.to_dict``: a
        client learns *that* a worker has an isolated worktree and which branch
        it is on, never where the machine keeps it. ``base_commit`` is a commit
        id, which is a name for a state rather than a location.
        """
        return {
            "project_id": self.project_id,
            "branch": self.branch,
            "base_commit": self.base_commit,
        }


def _git(root: Path, arguments: Sequence[str], *, timeout: float = GIT_TIMEOUT_SECONDS):
    """Run one fixed Git command in one verified directory.

    ``arguments`` is always a literal list built in this module. There is no
    caller-supplied element anywhere, ``shell`` is never used, and the working
    directory is a root the project registry already verified.
    """
    executable = shutil.which("git")
    if executable is None:
        raise WorktreeError("git is not installed on this host")
    try:
        return subprocess.run(
            [executable, *arguments],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise WorktreeError("a git command did not finish in time", detail=arguments[0])
    except OSError as exc:
        raise WorktreeError("git could not be run", detail=str(exc))


def _require(result, message: str) -> str:
    if result.returncode != 0:
        raise WorktreeError(message, detail=(result.stderr or "").strip()[:400])
    return (result.stdout or "").strip()


def is_git_repository(root: Path) -> bool:
    try:
        result = _git(root, ["rev-parse", "--is-inside-work-tree"])
    except WorktreeError:
        return False
    return result.returncode == 0 and (result.stdout or "").strip() == "true"


def head_commit(root: Path) -> str:
    return _require(_git(root, ["rev-parse", "HEAD"]), "the project has no commit to branch from")


def prepare(
    *, project_id: str, project_root: Path, task_id: str, state_dir: Path
) -> DevelopmentWorktree:
    """Create the isolated worktree one dispatch may write in.

    Every value is derived. ``project_root`` is the root the **core** resolved
    from the host-owned registry — the same value ``TaskContext.project_root``
    carries — and it is re-verified here rather than trusted, the "check closest
    to the work" rule the project registry uses. A directory can be replaced
    between a dispatch decision and this moment, and this is the moment that
    hands out write authority.

    The base is the project's current ``HEAD``, read here rather than named by a
    caller. A ``base_ref`` parameter would be a way to aim a worker at a branch
    somebody else was using.

    Idempotent for one task id: an existing worktree at the derived path that is
    already on the derived branch is returned as-is, so a retried dispatch
    re-enters the same place instead of failing or creating a second one.
    """
    root = verify_root(Path(project_root))
    if not is_git_repository(root):
        raise WorktreeError(
            "this project is not a Git repository, so no worker worktree can be cut",
            detail=project_id,
        )

    branch = branch_name(task_id)
    destination = worktree_path(state_dir, project_id, task_id)

    existing = _existing_worktree(root, destination, branch)
    if existing is not None:
        return DevelopmentWorktree(
            project_id=project_id,
            task_id=task_id,
            path=destination,
            branch=branch,
            base_commit=existing,
            canonical_root=root,
        )

    base = head_commit(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        # A directory where the worktree should be, that Git does not know about.
        # Refused rather than deleted: something put it there, and this module
        # removing a directory it did not create is not a risk worth taking for
        # a case that should not happen.
        raise WorktreeError(
            "something already occupies this dispatch's worktree location",
            detail=task_id,
        )

    _require(
        _git(root, ["worktree", "add", "-b", branch, str(destination), base]),
        "the worker worktree could not be created",
    )
    return DevelopmentWorktree(
        project_id=project_id,
        task_id=task_id,
        path=destination,
        branch=branch,
        base_commit=base,
        canonical_root=root,
    )


def _existing_worktree(root: Path, destination: Path, branch: str) -> Optional[str]:
    """The base commit of an already-prepared worktree, or ``None``.

    Recognised only when the directory Git knows about is the one this module
    would have created *and* it is on the branch this module would have used.
    Anything else is not "already prepared", it is a surprise, and the caller
    finds out by the creation failing rather than by silently adopting it.
    """
    if not destination.is_dir():
        return None
    result = _git(destination, ["rev-parse", "--abbrev-ref", "HEAD"])
    if result.returncode != 0 or (result.stdout or "").strip() != branch:
        return None
    merge_base = _git(destination, ["rev-parse", "HEAD"])
    if merge_base.returncode != 0:
        return None
    return (merge_base.stdout or "").strip()


def remove(worktree: DevelopmentWorktree) -> None:
    """Detach one worktree. **Never deletes the branch or its commits.**

    Used by tests and by cleanup. Evidence survives: the branch remains, so a
    worker's work is still reachable after its working directory is gone, which
    is the ordering a later verification pass needs.
    """
    _git(worktree.canonical_root, ["worktree", "remove", "--force", str(worktree.path)])
    _git(worktree.canonical_root, ["worktree", "prune"])


def worker_branches(root: Path) -> List[str]:
    """Every branch this layer created in one repository."""
    result = _git(root, ["branch", "--list", BRANCH_PREFIX + "*", "--format=%(refname:short)"])
    if result.returncode != 0:
        return []
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def canonical_state(root: Path) -> dict:
    """The facts "the canonical checkout was untouched" actually means.

    Returned as a dict so a test can capture it before a dispatch and compare
    after. Deliberately does **not** include ``.git/worktrees`` bookkeeping,
    which ``git worktree add`` must write — see this module's docstring. What is
    here is what a worker must never move: the checked-out branch, the commit it
    points at, and the working tree's cleanliness.
    """
    return {
        "head_commit": head_commit(root),
        "branch": _require(
            _git(root, ["rev-parse", "--abbrev-ref", "HEAD"]), "no branch"
        ),
        "status": _require(
            _git(root, ["status", "--porcelain"]), "status unavailable"
        ),
    }


__all__ = [
    "BRANCH_PREFIX",
    "FORBIDDEN_BRANCHES",
    "GIT_TIMEOUT_SECONDS",
    "WORKTREES_DIRNAME",
    "DevelopmentWorktree",
    "default_state_dir",
    "WorktreeError",
    "branch_name",
    "canonical_state",
    "head_commit",
    "is_git_repository",
    "prepare",
    "remove",
    "worker_branches",
    "worktree_path",
    "worktrees_root",
]
