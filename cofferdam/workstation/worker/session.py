"""Cofferdam's own Claude *worker* session. One canonical copy, never the operator's.

The defect this exists to fix
-----------------------------

PR1e gave every dispatch a *fresh* synthetic home and **copied** the operator's
``~/.claude/.credentials.json`` into it. The copy was deliberate — the CLI refreshes
its own token, and a read-only bind would fail a long run partway through — but
it created a divergence nobody owned:

1. the worker refreshes the token **in its copy**;
2. the provider **rotates the refresh token**, invalidating the previous one;
3. the copy is thrown away with the dispatch;
4. the operator's file still holds the superseded refresh token.

The next run copies that stale token and cannot refresh. Neither can the
operator's own CLI. This is not hypothetical: it happened during PR1f validation
— one worker run succeeded, the next failed, and then ``claude -p`` on the host
failed too, with *OAuth session expired and could not be refreshed*.

The fix is not better copying. It is **not copying**: one durable state root that
the worker owns, that survives jobs and restarts, and that is the only place its
credentials ever live.

Where the machinery now lives
------------------------------

All of it moved to :mod:`cofferdam.workstation.claudeauth.session` in M2M PR4, when
the development planner needed a session of its own and the only alternatives
were sharing the worker's credential or copying it — the two things this module
was written to refuse.

**Nothing about the worker's behaviour changed.** This module is a binding: every
name below has the signature it has always had, every path resolves where it
always did, and PR1g's tests exercise the shared implementation through here
unmodified. What changed is that the permission rule, the lock discipline, the
status vocabulary and the environment construction now have one implementation
instead of one per component.

Where it is, and who may say
-----------------------------

``<state_dir>/claude-worker/config``, mode 0700, derived from host configuration.
There is no parameter anywhere above this that names it: not on the dispatch API,
not on the project registry, not in a planner result. A caller-selectable
credential location would be a way to point Cofferdam's authenticated session at
a directory somebody else controls.

It is deliberately **not** inside a project, not inside a worktree, and never
appears in a read model, a projection or a log line. It is also not the planner's
— see :mod:`cofferdam.workstation.planner.session`, which owns a separate
directory and a separate login, so neither component can spend or rotate the
other's credential.

The operator's session is left alone
------------------------------------

Nothing here reads ``~/.claude``. Not to copy, not to migrate, not as a fallback.
A fallback would silently reintroduce the rotation bug the moment the worker
session expired, and it would do it at the least observable moment — unattended,
at night. An expired worker session is a typed refusal that names itself.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

from ..claudeauth import session as _core
from ..claudeauth.session import (
    CONFIG_DIRNAME,
    CREDENTIAL_FILENAME,
    LOCK_FILENAME,
    LOCK_TIMEOUT_SECONDS,
    NEEDS_LOGIN,
    PROBE_FIELDS,
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

#: Beneath the state directory, beside the worktrees and the journals.
SESSION_DIRNAME = "claude-worker"

#: Where the persistent config root appears inside the namespace.
#:
#: Under the interior home on purpose. It keeps the credential inside the
#: ``/home/worker/**`` path class the file-tool deny rules already name, so PR#77's
#: alias tests keep testing the thing they were written to test — the move from a
#: disposable home to a persistent one changes where the bytes live on the host,
#: not what the model can reach.
INTERIOR_CONFIG = "/home/worker/.claude"


class WorkerSessionUnavailable(ClaudeSessionUnavailable):
    """Cofferdam's Claude worker session cannot be used, and this says which way.

    A subclass rather than an alias: a caller that catches this is asking about
    the *worker's* session, and the planner's failure must not satisfy it.
    """


#: This component's namespace. The one place the worker's directory name is
#: written down.
NAMESPACE = _core.ClaudeSessionNamespace(
    dirname=SESSION_DIRNAME,
    label="worker",
    interior_config=INTERIOR_CONFIG,
    error=WorkerSessionUnavailable,
)

#: What a person should read, per status. Rendered once from the shared
#: templates so the worker's wording and the planner's cannot drift apart.
SENTENCES: Dict[str, str] = {
    name: NAMESPACE.sentence(name) for name in _core.SENTENCE_TEMPLATES
}


def session_root(state_dir: Path) -> Path:
    return _core.session_root(state_dir, NAMESPACE)


def config_directory(state_dir: Path) -> Path:
    """The one durable place Cofferdam's Claude worker credentials live."""
    return _core.config_directory(state_dir, NAMESPACE)


def lock_path(state_dir: Path) -> Path:
    return _core.lock_path(state_dir, NAMESPACE)


def credential_path(state_dir: Path) -> Path:
    """Where the CLI keeps the worker's credential. **Never read by Cofferdam.**"""
    return _core.credential_path(state_dir, NAMESPACE)


def prepare(state_dir: Path) -> Path:
    """Create the durable config root if absent. **Never populates it.**"""
    return _core.prepare(state_dir, NAMESPACE)


def permissions_safe(state_dir: Path) -> Tuple[bool, Optional[str]]:
    """Whether the durable session is readable only by its owner."""
    return _core.permissions_safe(state_dir, NAMESPACE)


def status(
    state_dir: Path, *, cli_version: Optional[str] = None, cli_present: bool = True
) -> SessionStatus:
    """A non-secret answer to "can the worker sign in"."""
    return _core.status(
        state_dir, NAMESPACE, cli_version=cli_version, cli_present=cli_present
    )


def require_usable(state_dir: Path, *, cli_present: bool = True) -> Path:
    """The config root, or a typed refusal naming what a person must do.

    Called before a worker starts so that an unauthenticated session fails
    *before* a worktree is cut and a process is launched, rather than after.
    """
    return _core.require_usable(state_dir, NAMESPACE, cli_present=cli_present)


def describe(
    state_dir: Path, *, cli_version: Optional[str] = None, cli_present: bool = True
) -> Dict[str, Any]:
    """The doctor surface. Safe to print, safe to log, safe to serialize.

    Since M2M PR4 the payload also carries ``session: "worker"``. Additive and
    non-secret, and worth having now that there is more than one session to be
    looking at: a doctor line that does not say *which* one it describes is a
    doctor line somebody will read about the wrong component.
    """
    return _core.describe(
        state_dir, NAMESPACE, cli_version=cli_version, cli_present=cli_present
    )


@contextmanager
def held(state_dir: Path, *, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[Path]:
    """Hold the worker session for one invocation. Serialized, deliberately."""
    with _core.held(state_dir, NAMESPACE, timeout=timeout) as config:
        yield config


def probe(state_dir: Path, executable: Path, *, timeout: float = 60.0) -> Dict[str, Any]:
    """Ask the CLI whether the worker session is signed in. Non-secret answer."""
    return _core.probe(state_dir, NAMESPACE, executable, timeout=timeout)


def _sentence(state: str) -> str:
    return NAMESPACE.sentence(state)


__all__ = [
    "CONFIG_DIRNAME",
    "CREDENTIAL_FILENAME",
    "INTERIOR_CONFIG",
    "LOCK_FILENAME",
    "LOCK_TIMEOUT_SECONDS",
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
    "PROBE_FIELDS",
    "SessionStatus",
    "WorkerSessionUnavailable",
    "classify_auth_failure",
    "config_directory",
    "credential_path",
    "describe",
    "held",
    "lock_path",
    "permissions_safe",
    "probe",
    "prepare",
    "require_usable",
    "session_root",
    "status",
]
