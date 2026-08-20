"""Where a development worker runs, and what it can reach while it runs.

Two modules, and they answer the two questions that make a coding worker
different from every agent Cofferdam has run before:

:mod:`.worktree`
    *Where may it write?* An isolated Git worktree cut by Cofferdam from a
    host-registered project, on a code-owned branch, outside every project
    checkout. No function here takes a path from anywhere but code.
:mod:`.sandbox`
    *What can it reach?* An unprivileged ``bubblewrap`` namespace in which the
    authorized worktree is present and the rest of the machine is **absent** —
    not denied, absent.

The pair is the answer to a problem the ``claude-code`` adapter solved by
removing Bash. A development worker needs a shell to run tests and make commits,
so the boundary moves from *which tools exist* to *what the process can reach*,
and only the second survives a model that decides to look around.
"""

from __future__ import annotations

from . import sandbox, worktree
from .sandbox import SandboxPlan, SandboxUnavailable
from .worktree import DevelopmentWorktree, WorktreeError

__all__ = [
    "DevelopmentWorktree",
    "SandboxPlan",
    "SandboxUnavailable",
    "WorktreeError",
    "sandbox",
    "worktree",
]
