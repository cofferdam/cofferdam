"""Log Cofferdam's Claude worker in, once, and ask how it is doing.

    python -m cofferdam.workstation.worker.auth status
    python -m cofferdam.workstation.worker.auth login

Why this exists at all
----------------------

The worker's session is a *separate account session* from the operator's, kept in
a directory Cofferdam owns (:mod:`.session`). Separate sessions need their own
login, and there is exactly one moment a person has to be involved: the first
one. This is that moment, and nothing more.

What it deliberately is not
---------------------------

**Not a route.** No HTTP surface, no device-token endpoint, no phone flow. A
remote endpoint that could initiate a login is a remote endpoint that could
initiate a login *for somebody else's account*, and no part of this milestone
needs one.

**Not an automation.** ``login`` hands the terminal to the real ``claude auth
login`` and gets out of the way. Cofferdam does not type a password, does not
drive a browser, does not handle a cookie and does not read the token that comes
back. Its entire contribution is two environment variables — which is the whole
trick: the same first-party login flow, pointed at Cofferdam's config root
instead of the operator's.

**Not a migration.** It never copies the operator's credential in. See
:func:`~.session.prepare`.

The operator's own session is untouched
----------------------------------------

``login`` sets ``CLAUDE_CONFIG_DIR`` to the worker's directory, so the flow reads
and writes there and nowhere else — verified against CLI 2.1.221, which writes
its entire state root under that variable and leaves ``HOME`` alone. Running it
cannot log the operator out, cannot rotate their token, and cannot overwrite
``~/.claude``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import session
from .worktree import default_state_dir


def _executable() -> Optional[Path]:
    from ..tasks.adapters.claude_code_worker import cli

    return cli.find_executable()


def _version(executable: Optional[Path]) -> Optional[str]:
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True, text=True, timeout=30,
            stdin=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (completed.stdout or "").strip() or None


def status_payload(state_dir: Path) -> dict:
    """The doctor answer. Contains no credential material by construction."""
    executable = _executable()
    payload = session.describe(
        state_dir,
        cli_version=_version(executable),
        cli_present=executable is not None,
    )
    if executable is not None and payload["prepared"]:
        # The CLI's own answer, filtered to the three non-secret fields it
        # reports. Kept beside Cofferdam's view rather than merged into it: one
        # is what the filesystem shows, the other is what the provider thinks,
        # and a single field would have to pick which one it meant.
        payload["cli_auth"] = session.probe(state_dir, executable)
    return payload


def command_status(state_dir: Path) -> int:
    payload = status_payload(state_dir)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("usable") else 1


def command_login(state_dir: Path) -> int:
    """Hand the terminal to the real login flow, pointed at the worker's root."""
    executable = _executable()
    if executable is None:
        print("The Claude Code CLI is not installed on this host.", file=sys.stderr)
        return 2

    config = session.prepare(state_dir)
    print("Logging in Cofferdam's OWN Claude worker session.")
    print("This is separate from your personal Claude session and does not")
    print("touch ~/.claude. Cofferdam never sees the credentials that result.")
    print()

    # Inherited rather than rebuilt from nothing, because an interactive login
    # legitimately needs a terminal, a browser opener and the user's display.
    # The two variables that decide *which session* is being logged in are
    # overridden, and those are the ones that matter here.
    environment = dict(os.environ)
    environment["CLAUDE_CONFIG_DIR"] = str(config)
    environment.pop("ANTHROPIC_API_KEY", None)
    environment.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

    try:
        completed = subprocess.run(
            [str(executable), "auth", "login"], env=environment
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"The login flow could not be started: {type(exc).__name__}", file=sys.stderr)
        return 2

    found = session.status(state_dir, cli_present=True)
    print()
    print(session.SENTENCES.get(found.status, "Worker session status unknown."))
    return completed.returncode if completed.returncode else (0 if found.usable else 1)


def command_logout(state_dir: Path) -> int:
    """Sign the *worker* session out. Never the operator's."""
    executable = _executable()
    if executable is None:
        print("The Claude Code CLI is not installed on this host.", file=sys.stderr)
        return 2
    config = session.config_directory(state_dir)
    if not config.is_dir():
        print("Cofferdam's Claude worker session was never set up.")
        return 0
    environment = dict(os.environ)
    environment["CLAUDE_CONFIG_DIR"] = str(config)
    completed = subprocess.run([str(executable), "auth", "logout"], env=environment)
    return completed.returncode


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cofferdam.workstation.worker.auth",
        description=(
            "Manage Cofferdam's own Claude worker session. Separate from your "
            "personal Claude session; ~/.claude is never read or written."
        ),
    )
    parser.add_argument(
        "command", choices=("status", "login", "logout"),
        help="status: non-secret diagnostic. login: one-time interactive sign-in.",
    )
    arguments = parser.parse_args(argv)
    state_dir = default_state_dir()

    if arguments.command == "status":
        return command_status(state_dir)
    if arguments.command == "login":
        return command_login(state_dir)
    return command_logout(state_dir)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
