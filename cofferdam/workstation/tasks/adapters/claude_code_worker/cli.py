"""The development worker's profile. A second file, and deliberately not a flag.

Why this is not a parameter on the existing adapter
---------------------------------------------------

``adapters/claude_code/cli.py`` says of its own tool list that Bash's absence is
"the single most important line in this package", because a Bash tool inside an
approved project root is still a general shell reachable by writing English into
a phone. That reasoning is correct and this module does not weaken it.

A development worker needs a shell — running the project's tests, reading Git
state and making a commit *are* the work. So the capability is not added to the
existing profile, where it would retroactively grant a shell to every task any
phone has ever been able to create. It lives in a separate profile, on a separate
adapter id, which a project must list explicitly before anything can use it.

Two projects can therefore differ: one permits ``claude-code`` and gets an agent
that cannot run a command; one permits ``claude-code-worker`` and gets a
development worker. Neither is a mode of the other, and there is no field
anywhere that turns the first into the second.

Why a shell is acceptable here and not there
--------------------------------------------

Because the boundary moved. The CLI adapter's guarantee is *this process has no
tool that runs commands*. This adapter's guarantee is *this process cannot reach
anything but its own worktree* — an unprivileged ``bubblewrap`` namespace in
which the rest of the machine is absent rather than denied (see
:mod:`...worker.sandbox`). The tool allowlist below is the second layer, not the
only one, and it is the layer that would be worth little on its own.

That ordering matters for a reader deciding whether this is safe: if the
containment were removed, this profile would not be enough, and the adapter
refuses to start rather than running without it.

What is still absent
--------------------

No MCP, from anywhere. No settings file, so the profile in this file is the
profile that runs. No plugin, no agent definition, no ``--add-dir``, no resume.
The forbidden-flag list is asserted by test rather than trusted to review.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: The version this profile was written against, recorded rather than required.
VERIFIED_CLI_VERSION = "2.1.221"

EXECUTABLE_NAME = "claude"

SEARCH_DIRECTORIES: Tuple[str, ...] = (
    "~/.local/bin",
    "/usr/local/bin",
    "/usr/bin",
)

#: The model a development step runs on. Code-owned: there is no parameter
#: anywhere above this that selects a model, because "which model" is the first
#: field a caller learns to send and the second is "which tools".
PROFILE_MODEL = "sonnet"

#: Built-in tools the worker may have at all.
#:
#: ``Bash`` is here and its scope is not open — see :data:`BASH_ALLOWLIST`. The
#: rest are the same file tools the CLI adapter grants, and they are bounded by
#: the namespace rather than by the tool: ``Read`` cannot reach a path that is
#: not mounted.
PROFILE_TOOLS: Tuple[str, ...] = (
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "TodoWrite",
)

#: The command prefixes the worker may run, as ``--allowedTools`` patterns.
#:
#: A development step needs to inspect Git, run the project's checks, and read
#: the tree. It does not need a package installer, a network fetcher, a
#: privilege escalation or a service manager, and none is here.
#:
#: This is an allowlist of *prefixes*, so ``Bash(git *)`` permits ``git status``
#: and ``git commit`` and does not permit ``gitfoo``. It is enforced by the CLI,
#: which makes it a real boundary and not the only one — a command that slipped
#: through still runs in a namespace containing one worktree.
BASH_ALLOWLIST: Tuple[str, ...] = (
    "Bash(git *)",
    "Bash(python3 *)",
    "Bash(python -m *)",
    "Bash(pytest *)",
    "Bash(ls *)",
    "Bash(cat *)",
    "Bash(head *)",
    "Bash(tail *)",
    "Bash(grep *)",
    "Bash(find *)",
    "Bash(wc *)",
    "Bash(diff *)",
    "Bash(mkdir *)",
    "Bash(make *)",
    "Bash(npm test*)",
    "Bash(npm run *)",
)

#: Denied by name as well as omitted from the allowlist, because defence in
#: depth costs nothing here and the list documents intent to a reader.
#:
#: ``sudo`` and the service tools cannot work inside the namespace anyway; they
#: are named so that their absence is a decision somebody wrote down rather than
#: an accident of which prefixes happened to be allowed.
BASH_DENYLIST: Tuple[str, ...] = (
    "Bash(sudo *)",
    "Bash(su *)",
    "Bash(systemctl *)",
    "Bash(apt *)",
    "Bash(apt-get *)",
    "Bash(pip install *)",
    "Bash(curl *)",
    "Bash(wget *)",
    "Bash(ssh *)",
    "Bash(scp *)",
    "Bash(docker *)",
    "Bash(git merge *)",
    "Bash(git push --force*)",
    "Bash(git rebase *)",
    "Bash(git reset --hard*)",
)

#: ``acceptEdits`` rather than ``bypassPermissions``.
#:
#: The stronger-sounding mode is the wrong choice even inside containment: it
#: would make the allowlist above decorative, and the two layers are worth more
#: than one. Edits are accepted without a prompt because a headless run has
#: nobody to answer one; commands are governed by the allowlist.
PROFILE_PERMISSION_MODE = "acceptEdits"

#: Bounds. Larger than the CLI adapter's, because a development step legitimately
#: takes more turns than answering a question — and still finite, so a worker
#: that has not finished stops on its own rather than running until somebody
#: notices.
PROFILE_MAX_TURNS = 60
PROFILE_MAX_BUDGET_USD = "5.00"

#: How long one worker may run before Cofferdam stops waiting.
PROFILE_TIMEOUT_SECONDS = 1800.0

#: Flags that must never appear in a built argv, asserted by test.
FORBIDDEN_FLAGS: Tuple[str, ...] = (
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "--bg",
    "--background",
    "--cloud",
    "--worktree",
    "--tmux",
    "--ide",
    "--chrome",
    "--plugin-dir",
    "--plugin-url",
    "--mcp-config",
    "--agents",
    "--add-dir",
    "--continue",
    "--resume",
    "--fork-session",
)

#: Git identity for anything the worker commits.
#:
#: Forced, and forced to something that reads as what it is. A commit a model
#: made must not be attributable to the operator: months later, ``git log`` is
#: the record of who did what, and a worker borrowing a person's name is the
#: quiet kind of wrong that is very hard to undo.
GIT_AUTHOR_NAME = "Cofferdam Worker"
GIT_AUTHOR_EMAIL = "worker@cofferdam.local"

GIT_ENVIRONMENT: Dict[str, str] = {
    "GIT_AUTHOR_NAME": GIT_AUTHOR_NAME,
    "GIT_AUTHOR_EMAIL": GIT_AUTHOR_EMAIL,
    "GIT_COMMITTER_NAME": GIT_AUTHOR_NAME,
    "GIT_COMMITTER_EMAIL": GIT_AUTHOR_EMAIL,
    # No pager, no editor. A worker that opened one would hang forever.
    "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_EDITOR": "true",
}


def find_executable() -> Optional[Path]:
    """Locate the installed CLI, or return ``None``. Fixed order, fixed name."""
    for directory in SEARCH_DIRECTORIES:
        candidate = Path(os.path.expanduser(directory)) / EXECUTABLE_NAME
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which(EXECUTABLE_NAME)
    if found:
        candidate = Path(found)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def resolve_cli_directory(executable: Path) -> Path:
    """The directory to mount read-only into the namespace.

    Resolved through its symlink here, unlike the CLI adapter's launch path, and
    for a different reason: this value becomes a **bind mount source**, and a
    bind of a symlink is a bind of wherever it pointed at mount time. Knowing
    exactly what is being exposed matters more here than surviving a
    ``claude update`` mid-run — and the resolution happens per dispatch, so an
    update between dispatches is picked up anyway.
    """
    return Path(os.path.realpath(executable))


def build_interior_argv(*, interior_cli: str, interior_worktree: str) -> List[str]:
    """The CLI command line as it exists *inside* the namespace.

    Every element is a constant of this module or an interior path constant of
    the sandbox. The prompt is not an argument: it goes on stdin, exactly as the
    planner's does, so no part of it can be read as a flag.

    ``--add-dir`` is absent and its absence is load-bearing: the worker's
    reachable filesystem is decided by the mount namespace, and a flag that
    could widen it from inside would make the outer boundary advisory.
    """
    return [
        interior_cli,
        "-p",
        "--model",
        PROFILE_MODEL,
        "--output-format",
        "json",
        "--permission-mode",
        PROFILE_PERMISSION_MODE,
        "--allowedTools",
        *PROFILE_TOOLS,
        *BASH_ALLOWLIST,
        "--disallowedTools",
        *BASH_DENYLIST,
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--max-turns",
        str(PROFILE_MAX_TURNS),
        "--max-budget-usd",
        PROFILE_MAX_BUDGET_USD,
    ]


__all__ = [
    "BASH_ALLOWLIST",
    "BASH_DENYLIST",
    "EXECUTABLE_NAME",
    "FORBIDDEN_FLAGS",
    "GIT_AUTHOR_EMAIL",
    "GIT_AUTHOR_NAME",
    "GIT_ENVIRONMENT",
    "PROFILE_MAX_BUDGET_USD",
    "PROFILE_MAX_TURNS",
    "PROFILE_MODEL",
    "PROFILE_PERMISSION_MODE",
    "PROFILE_TIMEOUT_SECONDS",
    "PROFILE_TOOLS",
    "VERIFIED_CLI_VERSION",
    "build_interior_argv",
    "find_executable",
    "resolve_cli_directory",
]
