"""Planner Claude sessions, in the four states a test needs them in.

Why a helper rather than a mock
--------------------------------

The planner's credential boundary is made of *directories and file modes* — a
config root at a derived path, 0700, with or without a ``.credentials.json`` in
it. A mock of ``session.status`` would let every test pass while proving nothing
about the thing that actually decides: what is on disk, and what environment gets
built from it.

So these build real directories with real modes, and the code under test reads
them exactly as it would in production. The credential file's *contents* are a
marker string, because nothing in Cofferdam ever opens it — a fact these doubles
are in a position to make obvious.

The four states
---------------

``signed_in``      prepared, credential present, 0700 — the only usable one
``never_logged_in``  prepared, no credential — ``login_required``
``unprepared``     no directory at all — ``unprepared``
``unsafe``         prepared and credential present, but group-readable
"""

from __future__ import annotations

import os
from pathlib import Path

from cofferdam.workstation.planner import session as planner_session
from cofferdam.workstation.worker import session as worker_session

#: What goes in the fake credential file. Never parsed by anything in Cofferdam,
#: and distinctive so a leak test can search for it by value.
PLANNER_CREDENTIAL_MARKER = '{"marker": "PLANNER-CREDENTIAL-SENTINEL-4B1D"}'
WORKER_CREDENTIAL_MARKER = '{"marker": "WORKER-CREDENTIAL-SENTINEL-77C2"}'
OPERATOR_CREDENTIAL_MARKER = '{"marker": "OPERATOR-CREDENTIAL-SENTINEL-A930"}'


def _write_credential(path: Path, marker: str, *, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(marker, encoding="utf-8")
    os.chmod(path, mode)
    return path


def signed_in_planner_session(state_dir: Path) -> Path:
    """A planner session that reports ``ready``. Returns the state directory."""
    state_dir = Path(state_dir)
    planner_session.prepare(state_dir)
    _write_credential(
        planner_session.credential_path(state_dir), PLANNER_CREDENTIAL_MARKER
    )
    return state_dir


def never_logged_in_planner_session(state_dir: Path) -> Path:
    """Prepared and empty: the state a fresh host is in."""
    state_dir = Path(state_dir)
    planner_session.prepare(state_dir)
    return state_dir


def unsafe_planner_session(state_dir: Path) -> Path:
    """A credential anybody in the group can read. Must never be used."""
    state_dir = Path(state_dir)
    planner_session.prepare(state_dir)
    _write_credential(
        planner_session.credential_path(state_dir),
        PLANNER_CREDENTIAL_MARKER,
        mode=0o644,
    )
    os.chmod(planner_session.config_directory(state_dir), 0o755)
    return state_dir


def signed_in_worker_session(state_dir: Path) -> Path:
    """A *worker* session that would authenticate. The planner must ignore it."""
    state_dir = Path(state_dir)
    worker_session.prepare(state_dir)
    _write_credential(
        worker_session.credential_path(state_dir), WORKER_CREDENTIAL_MARKER
    )
    return state_dir


def operator_home(root: Path) -> Path:
    """A plausible operator ``$HOME`` with a valid-looking ``~/.claude``.

    Used to prove a negative: the planner is given an environment in which this
    directory is unreachable, so a ``~/.claude`` lookup cannot arrive here even
    though the file is real and would otherwise authenticate.
    """
    root = Path(root)
    _write_credential(root / ".claude" / ".credentials.json", OPERATOR_CREDENTIAL_MARKER)
    return root


__all__ = [
    "OPERATOR_CREDENTIAL_MARKER",
    "PLANNER_CREDENTIAL_MARKER",
    "WORKER_CREDENTIAL_MARKER",
    "never_logged_in_planner_session",
    "operator_home",
    "signed_in_planner_session",
    "signed_in_worker_session",
    "unsafe_planner_session",
]
