"""Log Cofferdam's Claude *worker* in, once, and ask how it is doing.

    python -m cofferdam.workstation.worker.auth status
    python -m cofferdam.workstation.worker.auth login
    python -m cofferdam.workstation.worker.auth logout

The flow itself lives in :mod:`cofferdam.workstation.claudeauth.cli`, shared with
the development planner since M2M PR4. This module is the worker's binding: it
supplies the namespace, the state directory and the executable finder, and
nothing else.

Everything the flow guarantees is stated there — no route, no automation, no
migration, and an environment that points the first-party login at Cofferdam's
own config root rather than the operator's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from ..claudeauth import cli as claude_auth_cli
from . import session
from .worktree import default_state_dir

PROG = "python -m cofferdam.workstation.worker.auth"


def _executable() -> Optional[Path]:
    from ..tasks.adapters.claude_code_worker import cli

    return cli.find_executable()


def status_payload(state_dir: Path) -> dict:
    """The doctor answer. Contains no credential material by construction."""
    return claude_auth_cli.status_payload(state_dir, session.NAMESPACE, _executable)


def command_status(state_dir: Path) -> int:
    return claude_auth_cli.command_status(state_dir, session.NAMESPACE, _executable)


def command_login(state_dir: Path) -> int:
    return claude_auth_cli.command_login(state_dir, session.NAMESPACE, _executable)


def command_logout(state_dir: Path) -> int:
    return claude_auth_cli.command_logout(state_dir, session.NAMESPACE, _executable)


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
