"""The native Remote Control product, as Lane A is willing to invoke it.

This is the Lane A counterpart of :mod:`..tasks.adapters.claude_code.cli`, and
it holds the *entire* vocabulary Cofferdam will ever speak to ``claude
remote-control``: one program name, one subcommand, one derived session name,
one spawn mode. Nothing here is parameterised by anything a client sent, and
there is no function in this file that accepts a caller-supplied flag, path,
permission mode or environment mapping.

Why this duplicates a little of Lane B
--------------------------------------

Deliberately, and the alternative is worse. Lane B's ``cli`` module is the
delegated-task vocabulary: non-interactive, a fixed tool profile with no Bash, a
budget, no session persistence. Lane A is an *interactive* session a person
drives from their phone, and almost none of those choices transfer. Importing
Lane B's module to get two path helpers would couple the interactive lane to the
delegated one, so that a future change to how a headless task is launched could
silently change what happens when somebody opens a session on their phone.

Thirty lines of duplication at a lane boundary is cheaper than that coupling,
and both files are short enough to read in full — which is the property that
actually makes them auditable.

The version this was built against
----------------------------------

``2.1.221``, the same build Lane B records, with ``claude remote-control
--help`` read on the workstation during the M2H PR1 audit. That help text is
where :data:`SUBCOMMAND` and :data:`SPAWN_MODE` come from.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Optional, Tuple
from ..claudeauth.executable import (
    EXECUTABLE_NAME,
    SEARCH_DIRECTORIES,
    find_executable as _shared_find_executable,
)

#: The version whose ``remote-control --help`` this module was written to.
VERIFIED_CLI_VERSION = "2.1.221"

# `EXECUTABLE_NAME` and `SEARCH_DIRECTORIES` are re-exported from
# `claudeauth.executable`, which owns the one resolution policy since the M2M
# planner-executable hotfix. They were byte-identical here; keeping a second
# definition would be two constants to move in step.

#: The subcommand. There is no second one.
SUBCOMMAND = "remote-control"

#: Pinned rather than left to the default. ``same-dir`` *is* the documented
#: default today, and naming it is protection against that default changing
#: under an unrelated CLI update: ``worktree`` would silently start creating git
#: worktrees inside a registered project, which is a write nobody asked for.
SPAWN_MODE = "same-dir"

#: Prefix for the session name shown in claude.ai/code and the mobile app. The
#: name is derived from the project id — which the registry already constrained
#: to lowercase letters, digits, dash and underscore — and never from client
#: text, so it cannot carry markup, a newline, or somebody else's project name.
SESSION_NAME_PREFIX = "cofferdam-"

#: Flags that must never appear in a built argv, asserted by test rather than
#: trusted to review.
#:
#: ``--permission-mode`` is the one to read twice. The CLI accepts
#: ``bypassPermissions`` there, and a Remote Control host started with it would
#: hand every spawned session unprompted authority over the machine. Cofferdam
#: passes no permission mode at all, so the product's own default applies and
#: the person driving the session answers the prompts on their phone — which is
#: the entire point of an interactive lane.
#:
#: ``--continue`` and ``--session-id`` are absent because resuming somebody
#: else's prior session is a content decision, and Lane A does not make content
#: decisions. ``--debug-file`` is absent because it writes a file whose contents
#: Cofferdam has not audited and would not read.
FORBIDDEN_FLAGS: Tuple[str, ...] = (
    "--permission-mode",
    "bypassPermissions",
    "--continue",
    "-c",
    "--session-id",
    "--debug-file",
    "--dangerously-skip-permissions",
    "--remote-control-session-name-prefix",
)


def find_executable() -> Optional[Path]:
    """Locate the installed CLI, or return ``None``.

    Fixed search order, fixed program name, no caller-supplied component. The
    symlink is **not** resolved, for the reason Lane B records: ``~/.local/bin/
    claude`` points into a versioned directory that ``claude update`` replaces,
    so a pinned resolved path would rot on the next unrelated update.
    """
    return _shared_find_executable()


def verify_executable(executable: Path) -> bool:
    """Whether that path is still a runnable program, right now."""
    try:
        return executable.is_file() and os.access(executable, os.X_OK)
    except OSError:
        return False


def session_name(project_id: str) -> str:
    """The name this host shows in claude.ai/code, derived from the project id."""
    return SESSION_NAME_PREFIX + project_id


def build_argv(executable: Path, project_id: str) -> List[str]:
    """The complete command line, every element of it decided in this file.

    The only caller-supplied value is a project id the registry validated, and
    it appears in exactly one place: the session name. There is no parameter
    here that could add a flag, and no branch that could omit one.
    """
    return [
        str(executable),
        SUBCOMMAND,
        "--name",
        session_name(project_id),
        "--spawn",
        SPAWN_MODE,
    ]


__all__ = [
    "EXECUTABLE_NAME",
    "FORBIDDEN_FLAGS",
    "SEARCH_DIRECTORIES",
    "SESSION_NAME_PREFIX",
    "SPAWN_MODE",
    "SUBCOMMAND",
    "VERIFIED_CLI_VERSION",
    "build_argv",
    "find_executable",
    "session_name",
    "verify_executable",
]
