"""Configure and inspect Cofferdam's Git publisher.

    python -m cofferdam.workstation.publisher.cli status
    python -m cofferdam.workstation.publisher.cli configure

Separate from ``worker.auth`` on purpose: that one signs in the *model*, this one
holds the credential that writes to GitHub, and the two are different authorities
with different blast radii. A single ``cofferdam auth`` covering both would make
them feel like one thing.

``configure`` reads the token from **stdin**, never from a command-line argument.
An argument is visible in ``ps`` to every user on the host and lands in shell
history; stdin does neither. Nothing is echoed, and the token is not returned,
logged or included in any status output.

No HTTP route, for the same reason ``worker.auth`` has none.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from ..worker.worktree import default_state_dir
from . import credential


def command_status(state_dir: Path, *, reach: bool) -> int:
    payload = credential.describe(state_dir, reach=reach)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("usable") else 1


def command_configure(state_dir: Path) -> int:
    """Store a token read from stdin. Prints guidance, never the value."""
    if sys.stdin.isatty():
        print("Paste Cofferdam's Git publisher token, then press Enter.")
        print()
        print("This must be its OWN token, not your personal gh login:")
        print("  GitHub → Settings → Developer settings")
        print("         → Personal access tokens → Fine-grained tokens")
        print("  Repository access: only the repository Cofferdam publishes to")
        print("  Permissions: Contents = Read and write")
        print("               Pull requests = Read and write")
        print("  Nothing else. No administration, no workflows, no secrets.")
        print()
    token = sys.stdin.readline().strip()
    if not token:
        print("No token given; nothing was stored.", file=sys.stderr)
        return 2
    try:
        credential.store(state_dir, token)
    except credential.PublisherCredentialUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        del token
    found = credential.status(state_dir)
    print(credential.SENTENCES.get(found.status, ""))
    print(f"token kind: {found.token_kind}")
    if found.token_kind == "classic":
        print(
            "warning: that is a CLASSIC token. A fine-grained token scoped to "
            "one repository grants far less.",
            file=sys.stderr,
        )
    return 0 if found.usable else 1


def command_forget(state_dir: Path) -> int:
    path = credential.credentials_file(state_dir)
    if path.is_file():
        path.unlink()
        print("The Git publisher credential was removed.")
    else:
        print("There was no Git publisher credential to remove.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cofferdam.workstation.publisher.cli",
        description=(
            "Manage Cofferdam's Git publishing credential. Separate from your "
            "personal gh login, which is never read or used."
        ),
    )
    parser.add_argument("command", choices=("status", "configure", "forget"))
    parser.add_argument(
        "--reach", action="store_true",
        help="status only: ask GitHub whether the credential works.",
    )
    arguments = parser.parse_args(argv)
    state_dir = default_state_dir()

    if arguments.command == "status":
        return command_status(state_dir, reach=arguments.reach)
    if arguments.command == "configure":
        return command_configure(state_dir)
    return command_forget(state_dir)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
