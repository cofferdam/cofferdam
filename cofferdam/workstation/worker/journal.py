"""What a worker had already done when the daemon died. Written as it happens.

Why this file exists
--------------------

PR1e's adapter runs a whole dispatch inside one synchronous ``start()`` — cut the
worktree, run the model, run the checks, make the commit — and reports all of it
at the end, in one :class:`AdapterOutcome`. That is a clean shape right up until
the process dies in the middle of it, at which point Cofferdam has **no record of
how far the work got**. The task row says ``running``, which PR1e's own doctrine
already says is not evidence that anything is running.

So a restart could see a worktree with edits in it and a commit on a branch, and
be unable to say whether the commit was this dispatch's or whether the checks had
run — the two questions reconciliation is entirely made of.

This is the smallest thing that answers them: an append-only journal of phases,
written by the code that performs each phase, at a path derived from the same
ids the worktree is derived from.

What it is not
--------------

Not a workflow engine, and not a second lifecycle. Task Core owns whether a task
is running, interrupted or completed, and nothing here has an opinion about that.
These are *execution facts* — "the commit was attempted", "the commit produced
this id" — that exist so a later read can classify what happened instead of
guessing. The vocabulary is closed, the writer is code-owned, and no caller
supplies a phase.

The ordering rule, which is the whole point
-------------------------------------------

For every externally visible operation:

    record the intent → perform the operation → record the observed result

Both halves matter, and for different reasons. Without the *intent* record a
crash during an operation is indistinguishable from one before it, so recovery
cannot tell "the commit may exist" from "no commit was ever attempted". Without
the *result* record the id Git produced is lost.

The consequence is deliberate: a phase pair that is opened and never closed means
**"this may or may not have happened, go and look"**, which is exactly the
question :mod:`..worker.reconcile` then answers against Git. A journal that only
recorded completions could never raise that question.

Nothing here is ever the authority over Git. A ``committed`` entry naming a
commit id is a *lead*; reconciliation still asks Git whether that commit exists
on that branch. The journal narrows the search, it does not settle it.

Durability
----------

Each entry is one line, appended and ``fsync``-ed before the call returns, so an
entry that was written is on the disk when the power goes. Failure to write is
swallowed on purpose — see :func:`record`. A dispatch must not fail because its
bookkeeping could not be written; the cost of that is a recovery that has to fall
back to asking Git, which it can already do.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..runtime.identity import now_iso
from ..tasks.identity import valid_task_id
from ..tasks.projects import valid_project_id
from .worktree import WorktreeError, worktrees_root

#: One directory beside the worktrees, not inside them.
#:
#: Inside would put the journal in the tree the model can write to, which would
#: make a model-authored file the evidence used to decide what the model did.
JOURNALS_DIRNAME = "worker-journals"

#: Phases, in the order a complete dispatch passes through them.
#:
#: Closed vocabulary. The pairs are (intent, result) and the reconciler reads
#: them as pairs — see the module docstring on why an unclosed pair is a
#: question rather than a failure.
PHASE_PREPARED = "prepared"
PHASE_WORKER_RUNNING = "worker_running"
PHASE_WORKER_RETURNED = "worker_returned"
PHASE_CHECKS_RUNNING = "checks_running"
PHASE_CHECKS_COMPLETED = "checks_completed"
PHASE_COMMIT_PENDING = "commit_pending"
PHASE_COMMITTED = "committed"

PHASES: Tuple[str, ...] = (
    PHASE_PREPARED,
    PHASE_WORKER_RUNNING,
    PHASE_WORKER_RETURNED,
    PHASE_CHECKS_RUNNING,
    PHASE_CHECKS_COMPLETED,
    PHASE_COMMIT_PENDING,
    PHASE_COMMITTED,
)

#: Which result phase closes which intent phase.
CLOSES: Dict[str, str] = {
    PHASE_WORKER_RETURNED: PHASE_WORKER_RUNNING,
    PHASE_CHECKS_COMPLETED: PHASE_CHECKS_RUNNING,
    PHASE_COMMITTED: PHASE_COMMIT_PENDING,
}

#: An entry is small by construction. A worker's *output* never goes in here —
#: that is what the task's final result is for, and it is scrubbed on the way
#: there. This file holds facts with closed shapes.
MAX_DETAIL_CHARS = 200

#: A dispatch that somehow wrote thousands of entries is a bug, and reading all
#: of them at start-up would turn that bug into a slow daemon.
MAX_ENTRIES_READ = 500


@dataclass(frozen=True)
class JournalEntry:
    """One thing that was about to happen, or had just happened."""

    phase: str
    at: str
    detail: Optional[str] = None
    commit: Optional[str] = None
    #: What the worktree was cut from, recorded on ``prepared``.
    #:
    #: The one value recovery cannot re-derive later: the project's ``HEAD``
    #: moves, so "the commit this branch started at" has to be written down at
    #: the moment it was true. It is what makes "did this dispatch commit
    #: anything" an exact comparison rather than an inference from authorship.
    base_commit: Optional[str] = None
    exit_zero: Optional[bool] = None
    check: Optional[str] = None
    failure_code: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"phase": self.phase, "at": self.at}
        for name in ("detail", "commit", "base_commit", "exit_zero", "check", "failure_code"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload


def journal_path(state_dir: Path, project_id: str, task_id: str) -> Path:
    """Where one dispatch's journal lives. A pure function of code-owned ids.

    Deliberately the same derivation :func:`..worktree.worktree_path` uses, from
    the same two validated ids, so recovery can find the journal knowing only
    what the durable dispatch row already holds. Nothing stores this path.
    """
    if not valid_project_id(project_id):
        raise WorktreeError("that is not a project id", detail=str(project_id))
    if not valid_task_id(task_id):
        raise WorktreeError("that is not a task id", detail=str(task_id))
    root = worktrees_root(state_dir).parent / JOURNALS_DIRNAME
    return root / project_id / (task_id + ".jsonl")


def record(
    state_dir: Path,
    project_id: str,
    task_id: str,
    phase: str,
    *,
    detail: Optional[str] = None,
    commit: Optional[str] = None,
    base_commit: Optional[str] = None,
    exit_zero: Optional[bool] = None,
    check: Optional[str] = None,
    failure_code: Optional[str] = None,
) -> Optional[JournalEntry]:
    """Append one phase entry, durably, and never raise.

    **Swallowing the error is the deliberate part.** This is bookkeeping beside
    a real operation, and a dispatch that failed because its journal could not be
    written would be a feature whose reliability was strictly worse than not
    having it. What a missing entry costs is precision: reconciliation falls back
    to asking Git directly, which is the authority anyway.

    ``fsync`` on the file and not on the directory: the file is created once, by
    the ``prepared`` entry, and every later entry appends to a directory entry
    that already reached the disk.
    """
    if phase not in PHASES:  # pragma: no cover - callers pass constants
        raise ValueError(f"unknown worker phase: {phase}")
    entry = JournalEntry(
        phase=phase,
        at=now_iso(),
        detail=_bounded(detail),
        commit=commit,
        base_commit=base_commit,
        exit_zero=exit_zero,
        check=check,
        failure_code=failure_code,
    )
    try:
        path = journal_path(Path(state_dir), project_id, task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, WorktreeError):
        return None
    return entry


def read(state_dir: Path, project_id: str, task_id: str) -> List[JournalEntry]:
    """Every entry for one dispatch, oldest first. Absent journal reads empty.

    Tolerant of a torn final line, which is what a crash mid-``write`` leaves.
    A half-written entry is dropped rather than treated as corruption: the
    entries before it are still true, and they are the ones recovery needs.
    """
    try:
        path = journal_path(Path(state_dir), project_id, task_id)
    except WorktreeError:
        return []
    if not path.is_file():
        return []
    entries: List[JournalEntry] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if len(entries) >= MAX_ENTRIES_READ:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue  # a torn tail, or a line nothing wrote fully
                phase = payload.get("phase")
                if phase not in PHASES:
                    continue
                entries.append(
                    JournalEntry(
                        phase=phase,
                        at=str(payload.get("at") or ""),
                        detail=payload.get("detail"),
                        commit=payload.get("commit"),
                        base_commit=payload.get("base_commit"),
                        exit_zero=payload.get("exit_zero"),
                        check=payload.get("check"),
                        failure_code=payload.get("failure_code"),
                    )
                )
    except OSError:
        return []
    return entries


def reached(entries: List[JournalEntry], phase: str) -> bool:
    return any(entry.phase == phase for entry in entries)


def latest(entries: List[JournalEntry], phase: str) -> Optional[JournalEntry]:
    found = None
    for entry in entries:
        if entry.phase == phase:
            found = entry
    return found


def furthest_phase(entries: List[JournalEntry]) -> Optional[str]:
    """The last phase reached, by the journal's own order rather than by time.

    Ordered by :data:`PHASES` rather than by the ``at`` stamps, because two
    entries written in the same second would otherwise be ranked by string
    comparison of a timestamp — which is a coin toss, not an order.
    """
    rank = {phase: index for index, phase in enumerate(PHASES)}
    best: Optional[str] = None
    for entry in entries:
        if best is None or rank[entry.phase] > rank[best]:
            best = entry.phase
    return best


def open_intents(entries: List[JournalEntry]) -> Tuple[str, ...]:
    """Intent phases with no matching result. The "go and look" set.

    This is the journal's actual output. Everything else here is a convenience;
    this is the thing reconciliation branches on, because an open intent is
    precisely the statement *an operation may have completed and we did not see
    it finish*.
    """
    unmatched = []
    for result_phase, intent_phase in CLOSES.items():
        if reached(entries, intent_phase) and not reached(entries, result_phase):
            unmatched.append(intent_phase)
    return tuple(sorted(unmatched, key=PHASES.index))


def _bounded(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    text = str(text).replace("\n", " ").strip()
    return text[:MAX_DETAIL_CHARS] or None


__all__ = [
    "CLOSES",
    "JOURNALS_DIRNAME",
    "MAX_ENTRIES_READ",
    "PHASES",
    "PHASE_CHECKS_COMPLETED",
    "PHASE_CHECKS_RUNNING",
    "PHASE_COMMITTED",
    "PHASE_COMMIT_PENDING",
    "PHASE_PREPARED",
    "PHASE_WORKER_RETURNED",
    "PHASE_WORKER_RUNNING",
    "JournalEntry",
    "furthest_phase",
    "journal_path",
    "latest",
    "open_intents",
    "read",
    "reached",
    "record",
]
