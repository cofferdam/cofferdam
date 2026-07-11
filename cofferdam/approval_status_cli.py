"""``cofferdam approval-status`` — read-only approval lookup (no authority).

Reports whether an **active** approval currently exists for a proposal's binding
in this repository. It is strictly read-only: it mints nothing, consumes
nothing, and never creates approval state (if no ledger exists yet, the answer
is simply "no active approval"). It prints no patch bytes or file content, emits
no secret, and renders stored strings inertly (control characters stripped).

Exit codes: ``0`` = an active approval was found; ``1`` = none active; ``2`` =
error (bad input, unbuildable artifact, or a corrupt/unsafe ledger — fail-closed).

The command constructs its own production dependencies (``SystemClock``,
``ApprovalStore`` rooted at the current repo) internally; it exposes **no**
dependency-injection knobs, no ``--yes``/``--force``, and no mint/consume path.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Sequence

from .approval import ApprovalError, find_valid_approval
from .dryrun import DryRunError, build_dry_run_artifact
from .proposal import parse_proposal
from .repo_view import FilesystemRepoView

EXIT_ACTIVE = 0
EXIT_NONE = 1
EXIT_ERROR = 2


def _safe(text: str) -> str:
    """Render a stored string inertly: drop control characters so nothing can
    inject terminal escape sequences."""
    return "".join(ch for ch in text if ord(ch) >= 0x20 and ord(ch) != 0x7F)


def _err(message: str) -> int:
    sys.stderr.write(f"cofferdam approval-status: {message}\n")
    return EXIT_ERROR


def _read_input(args: Sequence[str]) -> str:
    """Read the proposal JSON from ``--file <path>`` or stdin. Rejects unknown
    arguments (fail-closed)."""
    args = list(args)
    if not args:
        return sys.stdin.read()
    if args[0] == "--file":
        if len(args) != 2:
            raise ValueError("usage: approval-status [--file <path>]")
        with open(args[1], "r", encoding="utf-8") as handle:
            return handle.read()
    raise ValueError(f"unknown argument: {args[0]}")


def approval_status_command(args: Sequence[str]) -> int:
    try:
        raw = _read_input(args)
    except (OSError, ValueError) as exc:
        return _err(str(exc))

    try:
        payload = json.loads(raw)
    except ValueError:
        return _err("input is not valid JSON")

    parsed = parse_proposal(payload)
    if not parsed.ok:
        return _err("proposal rejected: " + ",".join(r.value for r in parsed.reasons))

    try:
        repo_view = FilesystemRepoView(os.getcwd())
    except ValueError as exc:
        return _err(str(exc))

    try:
        artifact = build_dry_run_artifact(parsed.proposal, repo_view)
    except DryRunError as exc:
        return _err("cannot build dry-run artifact: " + str(exc))

    try:
        # Supported no-DI wrapper: it constructs its own production clock/store.
        view = find_valid_approval(artifact.bound_hash, repo_view)
    except ApprovalError as exc:
        return _err("approval lookup failed: " + str(exc))

    if view is None:
        sys.stdout.write(
            "no active approval for {path} (binding {bh})\n".format(
                path=_safe(artifact.relative_path), bh=artifact.bound_hash
            )
        )
        return EXIT_NONE

    sys.stdout.write(
        "active approval for {path}\n"
        "  binding:    {bh}\n"
        "  risk:       {risk}\n"
        "  created_at: {created}\n"
        "  expires_at: {expires}\n".format(
            path=_safe(view.relative_path),
            bh=view.bound_hash,
            risk=_safe(view.guard_risk),
            created=view.created_at,
            expires=view.expires_at,
        )
    )
    return EXIT_ACTIVE
