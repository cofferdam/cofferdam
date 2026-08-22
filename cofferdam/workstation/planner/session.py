"""The development planner's own Claude session. Not the operator's, not the worker's.

Why the planner needs one at all
---------------------------------

Until M2M PR4 the planner provider ran ``claude -p`` with **no environment
argument**, so the subprocess inherited the daemon's — which meant ``HOME``, which
meant ``~/.claude``, which meant *the operator's personal session*. That was
tolerable while the only caller was a test somebody ran by hand. It stopped being
tolerable the moment a remote Custom GPT request could cause the invocation:
a request arriving over the network would have spent the operator's own
subscription session, refreshed their token, and left no trace saying so.

The two obvious shortcuts are both refused
-------------------------------------------

**Borrowing the operator's session** is what the old behaviour did by accident.
Beyond the authority problem — a remote caller spending a human's personal
account — it reintroduces the rotation defect PR1g documents at length: two
things refreshing one token, one rotation superseding the other, and an operator
whose own CLI stops working for reasons they cannot see.

**Borrowing the worker's session** is the same defect with a nearer neighbour.
The worker's credential exists so that a *contained worker with tools* can act;
the planner has no tools and a completely different blast radius, and one
credential serving both would mean an expired planner locking out the worker, a
rotation during a long worker run being clobbered by a planner call, and no way
to revoke one without revoking the other.

So the planner gets its own namespace under
:mod:`cofferdam.workstation.claudeauth.session`, with its own directory, its own
login and its own lock. Two sessions that can expire, rotate and be revoked
independently.

Where it is
-----------

``<state_dir>/claude-planner/config``, mode 0700, derived from host
configuration. There is no parameter above this that names it — not on the
Actions Bridge, not on the development request, not in the project registry.

What the planner subprocess receives
-------------------------------------

Exactly :func:`~..claudeauth.session.environment`: ``PATH``, ``HOME`` pointing inside
this namespace, ``CLAUDE_CONFIG_DIR`` pointing at this config root, ``NO_COLOR``
and ``TERM``. Built, never inherited. There is no ``ANTHROPIC_API_KEY`` in it
because nothing copies one in, and ``HOME`` cannot reach the operator's home, so
a ``~/.claude`` fallback has nowhere to fall back *to* — absent rather than
disabled.

Fail closed
-----------

:func:`require_usable` is called by the ingress **before** the provider is
touched, so a planner that has never been logged in refuses the remote request
without spending a call, without creating a planner row, and with a typed reason
that names what a person must do. There is no path from that refusal to the
operator's credential.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

from ..claudeauth import session as _core
from ..claudeauth.session import (
    NEEDS_LOGIN,
    STATUS_CLI_MISSING,
    STATUS_LOGIN_REQUIRED,
    STATUS_PERMISSIONS_UNSAFE,
    STATUS_READY,
    STATUS_SESSION_EXPIRED,
    STATUS_UNPREPARED,
    ClaudeSessionUnavailable,
    SessionStatus,
    classify_auth_failure,
)

#: Beneath the state directory, beside ``claude-worker`` and never inside it.
SESSION_DIRNAME = "claude-planner"


class PlannerSessionUnavailable(ClaudeSessionUnavailable):
    """The planner's Claude session cannot be used, and this says which way.

    A subclass rather than an alias, and specifically **not**
    ``WorkerSessionUnavailable``: a caller that catches the worker's failure must
    not have the planner's satisfy it, because the two are fixed by two different
    logins.
    """


#: This component's namespace. The one place the planner's directory name is
#: written down.
#:
#: ``interior_config`` is ``None`` because the planner does not run in a sandbox
#: namespace — it is an ordinary subprocess with a built environment, so it has
#: no interior path for a credential to appear at.
NAMESPACE = _core.ClaudeSessionNamespace(
    dirname=SESSION_DIRNAME,
    label="development planner",
    interior_config=None,
    error=PlannerSessionUnavailable,
)

SENTENCES: Dict[str, str] = {
    name: NAMESPACE.sentence(name) for name in _core.SENTENCE_TEMPLATES
}


def session_root(state_dir: Path) -> Path:
    return _core.session_root(state_dir, NAMESPACE)


def config_directory(state_dir: Path) -> Path:
    """The one durable place the planner's Claude credentials live."""
    return _core.config_directory(state_dir, NAMESPACE)


def credential_path(state_dir: Path) -> Path:
    """Where the CLI keeps the planner's credential. **Never read by Cofferdam.**"""
    return _core.credential_path(state_dir, NAMESPACE)


def lock_path(state_dir: Path) -> Path:
    return _core.lock_path(state_dir, NAMESPACE)


def prepare(state_dir: Path) -> Path:
    """Create the durable config root if absent. **Never populates it.**"""
    return _core.prepare(state_dir, NAMESPACE)


def permissions_safe(state_dir: Path) -> Tuple[bool, Optional[str]]:
    return _core.permissions_safe(state_dir, NAMESPACE)


def status(
    state_dir: Path, *, cli_version: Optional[str] = None, cli_present: bool = True
) -> SessionStatus:
    """A non-secret answer to "can the planner sign in"."""
    return _core.status(
        state_dir, NAMESPACE, cli_version=cli_version, cli_present=cli_present
    )


def require_usable(state_dir: Path, *, cli_present: bool = True) -> Path:
    """The config root, or a typed refusal naming what a person must do.

    Called before the ingress claims an idempotency key, so an unauthenticated
    planner refuses a remote request having created nothing and spent nothing.
    """
    return _core.require_usable(state_dir, NAMESPACE, cli_present=cli_present)


def environment(state_dir: Path) -> Dict[str, str]:
    """The complete environment the planner subprocess runs under.

    Built from constants and this namespace's own paths. Never an overlay on
    ``os.environ`` — see :func:`~..claudeauth.session.environment` for why that is the
    mechanism rather than a precaution.
    """
    return _core.environment(state_dir, NAMESPACE)


def describe(
    state_dir: Path, *, cli_version: Optional[str] = None, cli_present: bool = True
) -> Dict[str, Any]:
    return _core.describe(
        state_dir, NAMESPACE, cli_version=cli_version, cli_present=cli_present
    )


def probe(state_dir: Path, executable: Path, *, timeout: float = 60.0) -> Dict[str, Any]:
    return _core.probe(state_dir, NAMESPACE, executable, timeout=timeout)


@contextmanager
def held(
    state_dir: Path, *, timeout: float = _core.LOCK_TIMEOUT_SECONDS
) -> Iterator[Path]:
    """Hold the planner session for one invocation.

    A different lock file from the worker's, so a long worker run and a planning
    turn never wait on each other — they are different credentials and there is
    nothing to serialize between them.
    """
    with _core.held(state_dir, NAMESPACE, timeout=timeout) as config:
        yield config


__all__ = [
    "NAMESPACE",
    "NEEDS_LOGIN",
    "SENTENCES",
    "SESSION_DIRNAME",
    "STATUS_CLI_MISSING",
    "STATUS_LOGIN_REQUIRED",
    "STATUS_PERMISSIONS_UNSAFE",
    "STATUS_READY",
    "STATUS_SESSION_EXPIRED",
    "STATUS_UNPREPARED",
    "PlannerSessionUnavailable",
    "SessionStatus",
    "classify_auth_failure",
    "config_directory",
    "credential_path",
    "describe",
    "environment",
    "held",
    "lock_path",
    "permissions_safe",
    "prepare",
    "probe",
    "require_usable",
    "session_root",
    "status",
]
