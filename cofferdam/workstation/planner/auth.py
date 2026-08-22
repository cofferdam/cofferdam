"""Log Cofferdam's *development planner* in, once, and ask how it is doing.

    python -m cofferdam.workstation.planner.auth status
    python -m cofferdam.workstation.planner.auth login
    python -m cofferdam.workstation.planner.auth logout

The flow itself lives in :mod:`cofferdam.workstation.claudeauth.cli`, shared with
the worker since M2M PR4. This module is the planner's binding: it supplies the
namespace, the state directory and the executable finder, and nothing else.

Running ``login`` here signs in **only** the planner. It cannot log the operator
out, cannot touch ``~/.claude``, and cannot rotate or revoke the worker's
credential — three separate sessions, three separate logins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from ..claudeauth import cli as claude_auth_cli
from . import session

PROG = "python -m cofferdam.workstation.planner.auth"


def _executable() -> Optional[Path]:
    """The installed CLI, or nothing. The planner's own default, resolved here.

    Deliberately not a configuration key and not an argument: the executable a
    Cofferdam-owned session signs into must not be selectable by anything that
    could be influenced from outside the host.
    """
    from .providers.claude_code import DEFAULT_EXECUTABLE

    candidate = Path(DEFAULT_EXECUTABLE)
    if candidate.exists():
        return candidate
    fallback = Path.home() / ".local" / "bin" / "claude"
    return fallback if fallback.exists() else None


def status_payload(state_dir: Path) -> dict:
    """The doctor answer. Contains no credential material by construction."""
    return claude_auth_cli.status_payload(state_dir, session.NAMESPACE, _executable)


def command_status(state_dir: Path) -> int:
    return claude_auth_cli.command_status(state_dir, session.NAMESPACE, _executable)


def command_login(state_dir: Path) -> int:
    return claude_auth_cli.command_login(state_dir, session.NAMESPACE, _executable)


def command_logout(state_dir: Path) -> int:
    return claude_auth_cli.command_logout(state_dir, session.NAMESPACE, _executable)


def default_state_dir() -> Path:
    """The host's state directory, from host configuration and nothing else."""
    from ..worker.worktree import default_state_dir as _default

    return _default()


def main(argv: Optional[Sequence[str]] = None) -> int:
    return claude_auth_cli.main(
        argv,
        namespace=session.NAMESPACE,
        state_dir=default_state_dir(),
        find=_executable,
        prog=PROG,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
