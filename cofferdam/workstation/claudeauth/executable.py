"""Where the installed Claude CLI is. One policy, and it was already written.

The hotfix this file exists for
--------------------------------

M2M PR4's planner provider carried ``DEFAULT_EXECUTABLE = "/usr/bin/claude"`` and
decided availability with ``Path(self._executable).exists()``. On a host where
the CLI installs to ``~/.local/bin/claude`` — which is where the official
installer puts it, and where this machine has it — the planner reported
``available: False`` for every request, so ``createDevelopmentRequest`` answered
``502 upstream_unavailable`` and never reached the provider at all.

Nothing about the planner's credential isolation was wrong. It simply could not
find the program.

Why this is an extraction rather than a fix in place
-----------------------------------------------------

Three modules had already solved this, **identically**:

* ``sessions/claude.py`` (Remote Control),
* ``tasks/adapters/claude_code/cli.py`` (the CLI task adapter),
* ``tasks/adapters/claude_code_worker/cli.py`` (the development worker).

Same ordered directories, same executability test, same ``shutil.which``
fallback, same decision not to resolve the symlink. The planner was the only
consumer that did not reuse them, and writing a fourth policy — even a correct
one — would have made four things to keep in step. So the policy lives here and
those three became bindings that re-export it; their public names and behaviour
are unchanged, which is what keeps the worker, the adapter and Remote Control
exactly as they were.

The policy, and why each part of it
------------------------------------

**A fixed, ordered list of directories, searched before ``PATH``.** The daemon
runs under systemd, where ``PATH`` is whatever the manager supplies rather than
whatever a login shell would build. A resolver that trusted ``PATH`` alone would
work in a terminal and fail as a service, which is the failure mode hardest to
diagnose because every manual check passes.

**Executability, not existence.** ``is_file() and os.access(X_OK)``. A directory
named ``claude``, a dangling entry or a non-executable file are all "not the CLI"
— and ``exists()`` says yes to the first two.

**The symlink is deliberately NOT resolved.** ``~/.local/bin/claude`` is a link
into a versioned directory that ``claude update`` replaces, so a pinned resolved
path rots on the next unrelated update and every run afterwards fails with a
message about nothing. This is recorded at length in ``claude_code/cli.py`` and
is preserved here verbatim in behaviour. "Absolute" and "canonical" are not the
same requirement, and only the first one is wanted.

**No caller-supplied component anywhere.** :data:`EXECUTABLE_NAME` is a constant,
:data:`SEARCH_DIRECTORIES` is a constant, and :func:`find_executable` takes no
arguments — so there is no parameter through which a request, a project registry
entry, a planner result or a model could name a program. That absence is the
security property; it is not enforced by validation because there is nothing to
validate.

**Fail closed.** No match returns ``None``. Nothing here searches the wider
filesystem, reads a configuration file for a path, or falls back to a shell.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional, Tuple

#: The one program name. A constant, never an argument.
EXECUTABLE_NAME = "claude"

#: Searched in this order, before ``PATH`` is consulted at all.
#:
#: ``~/.local/bin`` first because that is where the official installer puts it
#: for a per-user install, which is what this product is. The two system
#: directories follow for a host that installed it globally.
SEARCH_DIRECTORIES: Tuple[str, ...] = (
    "~/.local/bin",
    "/usr/local/bin",
    "/usr/bin",
)


def find_executable() -> Optional[Path]:
    """Locate the installed CLI, or return ``None``.

    Fixed search order, fixed program name, no caller-supplied component. The
    symlink is **not** resolved — see the module docstring.
    """
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


def verify_executable(executable: Optional[Path]) -> bool:
    """Whether that path is still a runnable program, **right now**.

    Called immediately before a launch and by every availability check, because
    resolution happens once and the world does not hold still: a CLI uninstalled
    or half-replaced between service start and a request should produce a
    refusal that says so, not a process that fails in a way nobody can read.

    This is the "check closest to the work" rule the project registry already
    follows for roots, and it is why pinning the path at construction is safe.
    """
    if executable is None:
        return False
    try:
        path = Path(executable)
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def absolute_executable(candidate: Optional[Path]) -> Optional[Path]:
    """An absolute path, or ``None``. **Absolute, not canonical.**

    ``find_executable`` already returns absolute paths — ``expanduser`` produces
    one and ``shutil.which`` produces one — so in production this is an
    assertion rather than a conversion. It exists for the injected-override path
    a test uses, and so that "the stored value is absolute" is a property of one
    named function instead of an assumption at four call sites.

    It does **not** call ``resolve()`` or ``realpath()``. Following the link
    would pin a versioned directory that ``claude update`` replaces, which is the
    documented reason every resolver in this codebase leaves it alone.
    """
    if candidate is None:
        return None
    path = Path(os.path.expanduser(str(candidate)))
    if not path.is_absolute():
        # Refused rather than made absolute against the process's working
        # directory: a relative program name resolved against a cwd is exactly
        # the ambiguity this module exists to remove, and a daemon's cwd is not
        # a thing anybody reasoned about.
        return None
    return path


def describe_resolution() -> dict:
    """Non-secret facts about resolution. **Never the path itself.**

    Published on the planner's ``describe`` so an operator can tell "no CLI on
    this host" from "the CLI is there and the session is not signed in" without
    the remote surface ever learning where it lives.
    """
    found = find_executable()
    return {
        "executable_resolved": found is not None,
        "executable_runnable": verify_executable(found),
        "searched": list(SEARCH_DIRECTORIES) + ["$PATH"],
    }


__all__ = [
    "EXECUTABLE_NAME",
    "SEARCH_DIRECTORIES",
    "absolute_executable",
    "describe_resolution",
    "find_executable",
    "verify_executable",
]
