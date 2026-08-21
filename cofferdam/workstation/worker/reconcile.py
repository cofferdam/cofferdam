"""What actually happened to a worker the daemon died underneath. Read-only.

The rule, and it is the whole module
------------------------------------

**Recovery is not re-execution.** Nothing here starts a worker, runs a check,
makes a commit, resets a working tree, deletes a directory or writes a single
byte into a repository. Every function reads: the durable journal, the
filesystem, and Git. The output is a *classification* — a statement about what is
true now — which the caller then records.

That is the same shape :meth:`MindService.recover_after_restart` already uses for
an interrupted memory apply, and for the same reason. An operation continued by a
restart is an operation nobody authorized at the moment it happened. A person
approved *one* development step; a daemon that restarts and re-sends that step is
performing a second one on the strength of the first approval.

Why Git is the authority and the journal is only a lead
--------------------------------------------------------

:mod:`.journal` records intent before each operation and the result after, so a
crash leaves an *open intent* — "a commit was attempted and we never saw it
finish". That is a question, not an answer. This module answers it by asking Git
whether the commit exists, on the branch this dispatch owns, with the author
identity Cofferdam forces.

The consequence worth stating: a journal that says ``committed`` is never
believed on its own. If the journal names commit ``abc`` and Git does not have
``abc`` on this branch, the answer is :data:`OUTCOME_CONTRADICTORY` — not
"committed". Trusting the cheaper record over the stronger one is how a recovery
pass starts producing confident wrong answers.

Why nothing here concludes "completed"
---------------------------------------

A commit existing is not the execution contract being satisfied. The contract is
worker edits → Cofferdam's checks → Cofferdam's commit → an observed result, and
a crash means the last of those was never observed. So the strongest thing this
module will say about a recovered commit is :data:`OUTCOME_COMMIT_RECOVERED`:
*the commit is real, it belongs to this dispatch, and the run was still
interrupted*. The commit is preserved and reported; success is not claimed.

Identity, and refusing the wrong worktree
------------------------------------------

Every path is **derived** from the durable ``(project_id, task_id)`` pair, never
supplied by a caller — :func:`..worktree.worktree_path` and
:func:`..worktree.branch_name` are pure functions of exactly those two ids. A
directory that exists where this dispatch's worktree belongs, but is on another
branch, is :data:`OUTCOME_WORKTREE_MISMATCHED` and is left completely alone. It
is somebody else's, and the one thing recovery must not do to somebody else's
work is touch it.

Total by construction
---------------------

This runs at start-up, before the first request is served, so a failure here is a
daemon that does not start. Every unexpected condition resolves to a conservative
outcome rather than an exception: Cofferdam could not determine what happened,
which is a different and more honest claim than knowing nothing happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import journal, worktree

#: No journal, no worktree, no branch. The dispatch row exists and nothing else
#: does, so the worker never got as far as being prepared.
OUTCOME_NEVER_STARTED = "never_started"

#: A worktree on the right branch, still exactly at its base commit, with a clean
#: working tree. Prepared and then nothing — no edits to preserve.
OUTCOME_NO_WORK_FOUND = "no_work_found"

#: Uncommitted modifications in this dispatch's worktree. **Preserved.**
OUTCOME_PARTIAL_WORK_PRESERVED = "partial_work_preserved"

#: A commit exists on this dispatch's branch, ahead of the base, authored by the
#: worker identity. Recovered and reported — not called success.
OUTCOME_COMMIT_RECOVERED = "commit_recovered"

#: Durable state says a worktree was prepared; the directory is gone. Not
#: recreated, because recreating it is how a restart becomes a second run.
OUTCOME_WORKTREE_MISSING = "worktree_missing"

#: Something is at this dispatch's worktree location that is not this dispatch's
#: worktree. Left untouched.
OUTCOME_WORKTREE_MISMATCHED = "worktree_mismatched"

#: The records disagree with the repository in a way that cannot be resolved by
#: reading — for example a ``committed`` entry naming a commit Git does not have.
OUTCOME_CONTRADICTORY = "contradictory"

#: Git or the filesystem could not be read at all.
OUTCOME_UNDETERMINED = "undetermined"

OUTCOMES: Tuple[str, ...] = (
    OUTCOME_NEVER_STARTED,
    OUTCOME_NO_WORK_FOUND,
    OUTCOME_PARTIAL_WORK_PRESERVED,
    OUTCOME_COMMIT_RECOVERED,
    OUTCOME_WORKTREE_MISSING,
    OUTCOME_WORKTREE_MISMATCHED,
    OUTCOME_CONTRADICTORY,
    OUTCOME_UNDETERMINED,
)

#: Outcomes where a person has something to look at, and Cofferdam should say so
#: rather than filing the dispatch under "done".
NEEDS_ATTENTION: frozenset = frozenset(
    {
        OUTCOME_PARTIAL_WORK_PRESERVED,
        OUTCOME_WORKTREE_MISSING,
        OUTCOME_WORKTREE_MISMATCHED,
        OUTCOME_CONTRADICTORY,
        OUTCOME_UNDETERMINED,
    }
)

#: Outcomes that leave a worktree on disk worth keeping. PR1f deletes nothing;
#: this exists so a later cleanup policy has something truthful to read.
RETAINS_WORKTREE: frozenset = frozenset(
    {
        OUTCOME_NO_WORK_FOUND,
        OUTCOME_PARTIAL_WORK_PRESERVED,
        OUTCOME_COMMIT_RECOVERED,
        OUTCOME_WORKTREE_MISMATCHED,
        OUTCOME_CONTRADICTORY,
    }
)


@dataclass(frozen=True)
class Reconciliation:
    """What one interrupted dispatch turned out to be. Facts, not a verdict."""

    outcome: str
    #: How far :mod:`.journal` saw the dispatch get. ``None`` when no journal
    #: survived, which is itself informative — it means the crash beat the first
    #: write, or the journal was never written.
    furthest_phase: Optional[str] = None
    #: Intent phases with no result. What made this a question worth asking.
    open_intents: Tuple[str, ...] = ()
    #: The commit Cofferdam *verified* on this dispatch's branch. Never the
    #: journal's word for it — see the module docstring.
    recovered_commit: Optional[str] = None
    #: Whether Cofferdam ran the project's checks and saw them exit.
    checks_observed: bool = False
    check_exit_zero: Optional[bool] = None
    changed_files: int = 0
    worktree_retained: bool = False
    detail: Optional[str] = None

    @property
    def needs_attention(self) -> bool:
        return self.outcome in NEEDS_ATTENTION

    def to_dict(self) -> Dict[str, Any]:
        """The safe shape. **No path is in it**, the same rule the rest of the
        worker read model follows: a client learns what happened and on which
        branch, never where on this machine anything lives."""
        return {
            "outcome": self.outcome,
            "furthest_phase": self.furthest_phase,
            "open_intents": list(self.open_intents),
            "recovered_commit": self.recovered_commit,
            "checks_observed": self.checks_observed,
            "check_exit_zero": self.check_exit_zero,
            "changed_files": self.changed_files,
            "worktree_retained": self.worktree_retained,
            "needs_attention": self.needs_attention,
            "detail": self.detail,
        }


def classify(
    *,
    project_id: str,
    task_id: str,
    project_root: Path,
    state_dir: Path,
) -> Reconciliation:
    """Decide what happened to one dispatch. Reads only; never raises.

    ``project_root`` is the root the caller re-resolved from the **durable**
    project registry, exactly as a dispatch does. It is not a recovery-time
    path from anywhere else, and this function derives every other location from
    it plus the two ids.
    """
    try:
        return _classify(
            project_id=project_id,
            task_id=task_id,
            project_root=Path(project_root),
            state_dir=Path(state_dir),
        )
    except Exception as exc:  # pragma: no cover - defensive; start-up must not fail
        return Reconciliation(
            outcome=OUTCOME_UNDETERMINED,
            detail=f"{type(exc).__name__} while reading durable state",
        )


def _classify(
    *, project_id: str, task_id: str, project_root: Path, state_dir: Path
) -> Reconciliation:
    entries = journal.read(state_dir, project_id, task_id)
    furthest = journal.furthest_phase(entries)
    intents = journal.open_intents(entries)

    checks_entry = journal.latest(entries, journal.PHASE_CHECKS_COMPLETED)
    checks_observed = checks_entry is not None
    check_exit_zero = checks_entry.exit_zero if checks_entry is not None else None

    def build(outcome: str, **extra: Any) -> Reconciliation:
        return Reconciliation(
            outcome=outcome,
            furthest_phase=furthest,
            open_intents=intents,
            checks_observed=checks_observed,
            check_exit_zero=check_exit_zero,
            worktree_retained=outcome in RETAINS_WORKTREE,
            **extra,
        )

    branch = worktree.branch_name(task_id)
    path = worktree.worktree_path(state_dir, project_id, task_id)

    if not path.is_dir():
        # Never prepared, or prepared and then gone. The journal tells the two
        # apart, and they are materially different: one is a dispatch that did
        # nothing, the other is missing evidence somebody may care about.
        if not entries:
            return build(
                OUTCOME_NEVER_STARTED, detail="no worktree and no durable phase record"
            )
        return build(
            OUTCOME_WORKTREE_MISSING,
            detail="durable state records a prepared worktree; the directory is gone",
        )

    identity = _worktree_identity(path)
    if identity is None:
        return build(
            OUTCOME_UNDETERMINED, detail="the worktree could not be read with Git"
        )
    actual_branch, head = identity
    if actual_branch != branch:
        # Somebody else's. Not inspected further, not modified, not removed.
        return build(
            OUTCOME_WORKTREE_MISMATCHED,
            detail="a worktree exists at this dispatch's location on another branch",
        )

    # The branch name alone is not identity. Branch names are derived from the
    # task id, and two projects' registries are separate files — so a worktree
    # could carry the right branch name and still be attached to another
    # project's repository. This is the check that makes recovery's project
    # isolation a property of the filesystem rather than of the directory layout:
    # the worktree must share a Git directory with the root the caller
    # re-resolved from the durable registry.
    if not _belongs_to(path, project_root):
        return build(
            OUTCOME_WORKTREE_MISMATCHED,
            detail="this worktree is attached to a different project's repository",
        )

    prepared = journal.latest(entries, journal.PHASE_PREPARED)
    base_commit = prepared.base_commit if prepared is not None else None
    commit = _dispatch_commit(path, head, base_commit)
    dirty_count = _changed_file_count(path)
    if dirty_count is None:
        return build(
            OUTCOME_UNDETERMINED, detail="the worktree status could not be read"
        )

    journal_commit = journal.latest(entries, journal.PHASE_COMMITTED)
    if journal_commit is not None and journal_commit.commit and commit is None:
        # The journal is not the authority, and this is the case that proves it.
        return build(
            OUTCOME_CONTRADICTORY,
            changed_files=dirty_count,
            detail="durable state records a commit that this branch does not have",
        )

    if commit is not None:
        return build(
            OUTCOME_COMMIT_RECOVERED,
            recovered_commit=commit,
            changed_files=dirty_count,
            detail=(
                "the commit exists on this dispatch's branch and was not repeated"
            ),
        )

    if dirty_count > 0:
        return build(
            OUTCOME_PARTIAL_WORK_PRESERVED,
            changed_files=dirty_count,
            detail="uncommitted worker edits are preserved in this dispatch's worktree",
        )

    return build(
        OUTCOME_NO_WORK_FOUND,
        detail="the worktree is on its base commit with nothing changed",
    )


def _belongs_to(path: Path, project_root: Path) -> bool:
    """Whether this worktree is attached to this project's repository.

    ``--git-common-dir`` is the shared ``.git`` a linked worktree points back at,
    so two worktrees of the same repository agree on it and worktrees of
    different repositories never do. Resolved on both sides before comparing,
    because one may be relative and one absolute, and a symlinked state
    directory would otherwise make two names for one directory look like two
    directories.

    Fails **closed**: if either side cannot be read, the answer is "no", and the
    caller reports a mismatch rather than proceeding on an unverified identity.
    """
    ours = worktree._git(path, ["rev-parse", "--git-common-dir"])
    theirs = worktree._git(Path(project_root), ["rev-parse", "--git-common-dir"])
    if ours.returncode != 0 or theirs.returncode != 0:
        return False
    try:
        left = (Path(path) / (ours.stdout or "").strip()).resolve()
        right = (Path(project_root) / (theirs.stdout or "").strip()).resolve()
    except OSError:  # pragma: no cover - defensive
        return False
    return left == right


def _worktree_identity(path: Path) -> Optional[Tuple[str, str]]:
    """``(branch, head)`` for a worktree, or ``None`` if Git cannot say."""
    branch = worktree._git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch.returncode != 0:
        return None
    head = worktree._git(path, ["rev-parse", "HEAD"])
    if head.returncode != 0:
        return None
    return (branch.stdout or "").strip(), (head.stdout or "").strip()


def _dispatch_commit(path: Path, head: str, base_commit: Optional[str]) -> Optional[str]:
    """The commit this dispatch made, verified against Git, or ``None``.

    Two independent things must hold, and each rules out a different way of being
    wrong:

    * ``HEAD`` has **moved off the base** the worktree was cut from. This is the
      exact test, and it is exact only because ``base_commit`` was written down
      by the ``prepared`` entry at the moment it was true — the project's own
      ``HEAD`` moves, so re-reading it now would answer a question about a
      different commit;
    * the commit's author is the worker identity :func:`..worktree.commit_all`
      forces. This is what stops a commit that was already in the project's
      history from being adopted as this dispatch's.

    The author check is not redundant with the base check. Without a recorded
    base — an old dispatch, or a journal lost before its first write — the base
    test cannot run at all, and authorship is then the only evidence left. In
    that case being strict is right: a branch whose ``HEAD`` is not worker-authored
    is not claimed, and the caller sees ``no_work_found`` rather than a commit
    Cofferdam cannot vouch for.
    """
    from ..tasks.adapters.claude_code_worker import cli

    author = worktree._git(path, ["log", "-1", "--format=%ae", head])
    if author.returncode != 0:
        return None
    if (author.stdout or "").strip() != cli.GIT_AUTHOR_EMAIL:
        # Either the worktree is still sitting on its base commit, or something
        # on this branch was authored by somebody else. Neither is ours to claim.
        return None
    if base_commit is not None and head == base_commit:
        # Worker identity on the base commit itself: possible when the project's
        # own history already contains a merged worker commit. Not this dispatch's.
        return None
    return head


def _changed_file_count(path: Path) -> Optional[int]:
    result = worktree._git(path, ["status", "--porcelain"])
    if result.returncode != 0:
        return None
    return len([line for line in (result.stdout or "").splitlines() if line.strip()])


def changed_paths(path: Path, limit: int = 50) -> List[str]:
    """Which files are uncommitted, for a person deciding what to do about them.

    Bounded, and relative to the worktree by construction — ``git status
    --porcelain`` never emits an absolute path — so this stays free of host
    layout even though it names files.
    """
    result = worktree._git(path, ["status", "--porcelain"])
    if result.returncode != 0:
        return []
    found = []
    for line in (result.stdout or "").splitlines()[:limit]:
        stripped = line[3:].strip() if len(line) > 3 else ""
        if stripped:
            found.append(stripped)
    return found


__all__ = [
    "NEEDS_ATTENTION",
    "OUTCOMES",
    "OUTCOME_COMMIT_RECOVERED",
    "OUTCOME_CONTRADICTORY",
    "OUTCOME_NEVER_STARTED",
    "OUTCOME_NO_WORK_FOUND",
    "OUTCOME_PARTIAL_WORK_PRESERVED",
    "OUTCOME_UNDETERMINED",
    "OUTCOME_WORKTREE_MISMATCHED",
    "OUTCOME_WORKTREE_MISSING",
    "RETAINS_WORKTREE",
    "Reconciliation",
    "changed_paths",
    "classify",
]
