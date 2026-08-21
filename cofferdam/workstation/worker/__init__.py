"""Where a development worker runs, what it can reach, and what it had done.

Four modules, answering the questions that make a coding worker different from
every agent Cofferdam has run before:

:mod:`.worktree`
    *Where may it write?* An isolated Git worktree cut by Cofferdam from a
    host-registered project, on a code-owned branch, outside every project
    checkout. No function here takes a path from anywhere but code.
:mod:`.sandbox`
    *What can it reach?* An unprivileged ``bubblewrap`` namespace in which the
    authorized worktree is present and the rest of the machine is **absent** —
    not denied, absent.

The first pair is the answer to a problem the ``claude-code`` adapter solved by
removing Bash. A development worker needs a shell to run tests and make commits,
so the boundary moves from *which tools exist* to *what the process can reach*,
and only the second survives a model that decides to look around.

:mod:`.journal`
    *How far did it get?* An append-only phase record, written as each phase
    happens, so a crash leaves an answerable question rather than a silence.
:mod:`.reconcile`
    *What is actually true now?* A read-only classification of an interrupted
    dispatch from that journal plus Git. It never re-executes anything.

The second pair exists because the first two make a worker that can be
*interrupted*: it holds a worktree, it may have committed, and the process that
knew about it is gone. Recovery is not re-execution — see :mod:`.reconcile`.
"""

from __future__ import annotations

from . import journal, reconcile, sandbox, session, worktree
from .sandbox import SandboxPlan, SandboxUnavailable
from .worktree import DevelopmentWorktree, WorktreeError

from .reconcile import Reconciliation
from .session import SessionStatus, WorkerSessionUnavailable

__all__ = [
    "DevelopmentWorktree",
    "Reconciliation",
    "SandboxPlan",
    "SessionStatus",
    "WorkerSessionUnavailable",
    "SandboxUnavailable",
    "WorktreeError",
    "journal",
    "reconcile",
    "sandbox",
    "session",
    "worktree",
]
