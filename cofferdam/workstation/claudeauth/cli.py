"""One-time human sign-in for a Cofferdam-owned Claude session. One flow.

    python -m cofferdam.workstation.worker.auth   status | login | logout
    python -m cofferdam.workstation.planner.auth  status | login | logout

Why this exists at all
----------------------

Each Cofferdam-owned session (:mod:`.session`) is a *separate account
session* from the operator's and from every other component's. Separate sessions
need their own login, and there is exactly one moment a person has to be
involved: the first one. This is that moment, and nothing more.

Written once, bound twice. PR1g wrote this flow for the worker; M2M PR4 needed
the same flow for the planner, and a second copy of a login path is a second
place to forget to drop ``ANTHROPIC_API_KEY``. The namespace is the only
difference between the two entry points.

What it deliberately is not
---------------------------

**Not a route.** No HTTP surface, no device-token endpoint, no phone flow. A
remote endpoint that could initiate a login is a remote endpoint that could
initiate a login *for somebody else's account*, and no part of this milestone
needs one. A test asserts the daemon never imports this module.

**Not an automation.** ``login`` hands the terminal to the real ``claude auth
login`` and gets out of the way. Cofferdam does not type a password, does not
drive a browser, does not handle a cookie and does not read the token that comes
back. Its entire contribution is the environment — which is the whole trick: the
same first-party login flow, pointed at Cofferdam's config root instead of the
operator's.

**Not a migration.** It never copies a credential in, from the operator's session
or from another namespace. See :func:`~.session.prepare`.

The operator's own session is untouched
----------------------------------------

``login`` sets ``CLAUDE_CONFIG_DIR`` to the namespace's directory, so the flow
reads and writes there and nowhere else — verified against CLI 2.1.221, which
writes its entire state root under that variable and leaves ``HOME`` alone.
Running it cannot log the operator out, cannot rotate their token, and cannot
overwrite ``~/.claude``. It also cannot touch another namespace's session, which
is why the worker and the planner can be signed in independently and either can
expire without the other noticing.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import session
from .session import ClaudeSessionNamespace

#: How a binding finds the CLI. A callable rather than a path so each component
#: keeps using the finder it already trusts.
ExecutableFinder = Callable[[], Optional[Path]]


def _version(executable: Optional[Path]) -> Optional[str]:
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (completed.stdout or "").strip() or None


def status_payload(
    state_dir: Path, namespace: ClaudeSessionNamespace, find: ExecutableFinder
) -> dict:
    """The doctor answer. Contains no credential material by construction."""
    executable = find()
    payload = session.describe(
        state_dir,
        namespace,
        cli_version=_version(executable),
        cli_present=executable is not None,
    )
    if executable is not None and payload["prepared"]:
        # The CLI's own answer, filtered to the three non-secret fields it
        # reports. Kept beside Cofferdam's view rather than merged into it: one
        # is what the filesystem shows, the other is what the provider thinks,
        # and a single field would have to pick which one it meant.
        payload["cli_auth"] = session.probe(state_dir, namespace, executable)
    return payload


def command_status(
    state_dir: Path, namespace: ClaudeSessionNamespace, find: ExecutableFinder
) -> int:
    payload = status_payload(state_dir, namespace, find)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("usable") else 1


def command_login(
    state_dir: Path, namespace: ClaudeSessionNamespace, find: ExecutableFinder
) -> int:
    """Hand the terminal to the real login flow, pointed at this config root."""
    executable = find()
    if executable is None:
        print("The Claude Code CLI is not installed on this host.", file=sys.stderr)
        return 2

    session.prepare(state_dir, namespace)
    print(f"Logging in Cofferdam's OWN Claude {namespace.label} session.")
    print("This is separate from your personal Claude session and does not")
    print("touch ~/.claude. Cofferdam never sees the credentials that result.")
    print()

    # Inherited and then corrected, because an interactive login legitimately
    # needs a terminal, a browser opener and the user's display. Which session is
    # being logged in, and which credentials could authenticate the flow as
    # somebody else, are decided by `login_environment` rather than here.
    environment = session.login_environment(
        state_dir, namespace, inherited=os.environ
    )

    try:
        completed = subprocess.run([str(executable), "auth", "login"], env=environment)
    except (OSError, subprocess.SubprocessError) as exc:
        print(
            f"The login flow could not be started: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 2

    found = session.status(state_dir, namespace, cli_present=True)
    print()
    print(namespace.sentence(found.status))
    return completed.returncode if completed.returncode else (0 if found.usable else 1)


def command_logout(
    state_dir: Path, namespace: ClaudeSessionNamespace, find: ExecutableFinder
) -> int:
    """Sign *this* session out. Never the operator's, never another component's."""
    executable = find()
    if executable is None:
        print("The Claude Code CLI is not installed on this host.", file=sys.stderr)
        return 2
    config = session.config_directory(state_dir, namespace)
    if not config.is_dir():
        print(f"Cofferdam's Claude {namespace.label} session was never set up.")
        return 0
    environment = session.login_environment(
        state_dir, namespace, inherited=os.environ
    )
    completed = subprocess.run([str(executable), "auth", "logout"], env=environment)
    return completed.returncode


def main(
    argv: Optional[Sequence[str]],
    *,
    namespace: ClaudeSessionNamespace,
    state_dir: Path,
    find: ExecutableFinder,
    prog: str,
) -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            f"Manage Cofferdam's own Claude {namespace.label} session. Separate "
            "from your personal Claude session; ~/.claude is never read or "
            "written."
        ),
    )
    parser.add_argument(
        "command",
        choices=("status", "login", "logout"),
        help="status: non-secret diagnostic. login: one-time interactive sign-in.",
    )
    arguments = parser.parse_args(argv)

    if arguments.command == "status":
        return command_status(state_dir, namespace, find)
    if arguments.command == "login":
        return command_login(state_dir, namespace, find)
    return command_logout(state_dir, namespace, find)


__all__ = [
    "ExecutableFinder",
    "command_login",
    "command_logout",
    "command_status",
    "main",
    "status_payload",
]
