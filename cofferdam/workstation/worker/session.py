"""Cofferdam's own Claude session. One canonical copy, never the operator's.

The defect this exists to fix
-----------------------------

PR1e gave every dispatch a *fresh* synthetic home and **copied** the operator's
``~/.claude/.credentials.json` into it. The copy was deliberate — the CLI refreshes
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
credentials ever live. A rotation written there is simply still there next time.

Why a config directory rather than a home directory
----------------------------------------------------

Measured against the installed CLI (2.1.221) rather than assumed:

* ``CLAUDE_CONFIG_DIR`` relocates the **entire** state root. With it set, the CLI
  read ``.credentials.json`` from there, wrote ``.claude.json``, ``projects/``,
  ``sessions/`` and ``backups/`` there, and wrote **nothing at all** into ``HOME``.
* Without credentials it says ``Not logged in · Please run /login``; with an
  unusable credential it says ``Failed to authenticate: OAuth session expired and
  could not be refreshed``. Two distinct conditions, which is what makes
  :data:`STATUS_LOGIN_REQUIRED` and :data:`STATUS_SESSION_EXPIRED` honest
  answers rather than one guess.

So ``HOME`` inside the namespace becomes a **tmpfs** — genuinely disposable,
nothing persists in it — and exactly one host directory is bound inside it, at the
interior path the config root occupies. The persistent surface is one directory
wide.

Where it is, and who may say
-----------------------------

``<state_dir>/claude-worker/config``, mode 0700, derived from host configuration.
There is no parameter anywhere above this that names it: not on the dispatch API,
not on the project registry, not in a planner result. A caller-selectable
credential location would be a way to point Cofferdam's authenticated session at
a directory somebody else controls.

It is deliberately **not** inside a project, not inside a worktree, and never
appears in a read model, a projection or a log line.

The operator's session is left alone
------------------------------------

Nothing here reads ``~/.claude``. Not to copy, not to migrate, not as a fallback.
A fallback would silently reintroduce the rotation bug the moment the worker
session expired, and it would do it at the least observable moment — unattended,
at night. An expired worker session is a typed refusal that names itself.

Serialized, because sharing was not proven safe
------------------------------------------------

Two CLI processes refreshing one token file could each rotate it, and the loser's
state would be the stale one — the same defect indoors. Nothing in the CLI's
interface documents a lock, so this module takes one: an ``flock`` on a file
beside the config root, held for the length of an invocation. Serialization is a
cost this control plane can pay; a corrupted session is not.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

#: Beneath the state directory, beside the worktrees and the journals.
SESSION_DIRNAME = "claude-worker"
CONFIG_DIRNAME = "config"
LOCK_FILENAME = "session.lock"

#: The credential file the CLI keeps inside its config root. Named here only so
#: status can report *whether it exists* and *what mode it has*. Nothing in
#: Cofferdam parses it, and nothing reads its bytes.
CREDENTIAL_FILENAME = ".credentials.json"

#: Where the persistent config root appears inside the namespace.
#:
#: Under the interior home on purpose. It keeps the credential inside the
#: ``/home/worker/**`` path class the file-tool deny rules already name, so PR#77's
#: alias tests keep testing the thing they were written to test — the move from a
#: disposable home to a persistent one changes where the bytes live on the host,
#: not what the model can reach.
INTERIOR_CONFIG = "/home/worker/.claude"

#: How long to wait for another invocation to finish before refusing.
LOCK_TIMEOUT_SECONDS = 300.0

STATUS_READY = "ready"
STATUS_LOGIN_REQUIRED = "login_required"
STATUS_SESSION_EXPIRED = "session_expired"
STATUS_CLI_MISSING = "cli_missing"
STATUS_UNPREPARED = "unprepared"
STATUS_PERMISSIONS_UNSAFE = "permissions_unsafe"

#: Statuses a person can fix by logging the worker session in. Held as a set so
#: a caller can ask the question without matching strings.
NEEDS_LOGIN: frozenset = frozenset({STATUS_LOGIN_REQUIRED, STATUS_SESSION_EXPIRED})

#: Substrings the CLI prints for each auth condition, measured against 2.1.221.
#:
#: Matched to *classify* a failure, never to decide whether one happened — the
#: exit status does that. A future CLI that reworded these produces
#: ``worker_auth_required`` with a less specific reason, not a run that silently
#: looks like a code failure.
_LOGIN_MARKERS: Tuple[str, ...] = ("not logged in", "please run /login", "/login")
_EXPIRED_MARKERS: Tuple[str, ...] = (
    "oauth session expired",
    "could not be refreshed",
    "session expired",
)


class WorkerSessionUnavailable(Exception):
    """Cofferdam's Claude session cannot be used, and this says which way.

    Carries a :data:`STATUS_READY`-family ``status`` so a caller can tell *this
    needs a person to log in* from *this is broken*. The distinction is the whole
    point: a status screen that says "your code failed" when the truth is "the
    worker needs login" sends somebody debugging the wrong thing.
    """

    def __init__(self, message: str, *, status: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail

    @property
    def needs_login(self) -> bool:
        return self.status in NEEDS_LOGIN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "needs_login": self.needs_login,
            "message": str(self),
            "detail": self.detail,
        }


def session_root(state_dir: Path) -> Path:
    return Path(state_dir) / SESSION_DIRNAME


def config_directory(state_dir: Path) -> Path:
    """The one durable place Cofferdam's Claude credentials live.

    A pure function of the host's state directory. No caller supplies it, and
    there is no parameter above this that selects one.
    """
    return session_root(state_dir) / CONFIG_DIRNAME


def lock_path(state_dir: Path) -> Path:
    return session_root(state_dir) / LOCK_FILENAME


def credential_path(state_dir: Path) -> Path:
    """Where the CLI keeps the worker's credential. **Never read by Cofferdam.**

    Used for ``exists`` and ``stat`` only — see :func:`status`.
    """
    return config_directory(state_dir) / CREDENTIAL_FILENAME


def prepare(state_dir: Path) -> Path:
    """Create the durable config root if absent. **Never populates it.**

    Deliberately does not copy, import or migrate anything — see the module
    docstring. A freshly prepared session is an *unauthenticated* session, and
    it says so through :func:`status` until somebody logs it in. Importing the
    operator's token here is exactly the shortcut that produced the defect this
    module exists to fix, and it would fail silently the first time it mattered.
    """
    root = session_root(Path(state_dir))
    config = config_directory(Path(state_dir))
    config.mkdir(parents=True, exist_ok=True)
    for directory in (root, config):
        try:
            os.chmod(directory, 0o700)
        except OSError:  # pragma: no cover - odd filesystems
            pass
    return config


def permissions_safe(state_dir: Path) -> Tuple[bool, Optional[str]]:
    """Whether the durable session is readable only by its owner.

    Checked rather than assumed on every status read, because a credential store
    that quietly became group-readable is the kind of thing nobody notices until
    it matters.
    """
    config = config_directory(Path(state_dir))
    if not config.is_dir():
        return False, "the worker session directory does not exist yet"
    mode = config.stat().st_mode & 0o777
    if mode & 0o077:
        return False, f"the worker session directory is mode {mode:o}, not 700"
    credential = credential_path(Path(state_dir))
    if credential.is_file():
        credential_mode = credential.stat().st_mode & 0o777
        if credential_mode & 0o077:
            return False, "the worker credential file is readable beyond its owner"
    return True, None


@dataclass(frozen=True)
class SessionStatus:
    """What can be said about Cofferdam's Claude session without opening it.

    **Every field here is non-secret by construction.** There is no token field,
    no expiry read out of the credential, and no place a caller could add one
    without it being obvious in review: nothing in this module parses the
    credential file, so there is nothing to leak.
    """

    status: str
    prepared: bool
    credential_present: bool
    cli_version: Optional[str] = None
    cli_present: bool = False
    permissions_ok: bool = False
    detail: Optional[str] = None

    @property
    def needs_login(self) -> bool:
        return self.status in NEEDS_LOGIN

    @property
    def usable(self) -> bool:
        return self.status == STATUS_READY

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "usable": self.usable,
            "needs_login": self.needs_login,
            "prepared": self.prepared,
            "credential_present": self.credential_present,
            "permissions_ok": self.permissions_ok,
            "cli_present": self.cli_present,
            "cli_version": self.cli_version,
            "detail": self.detail,
        }


def status(state_dir: Path, *, cli_version: Optional[str] = None,
           cli_present: bool = True) -> SessionStatus:
    """A non-secret answer to "can the worker sign in".

    Reads the *presence* and *mode* of the credential file and nothing else. It
    does not open it, does not parse it, and cannot report its expiry — which is
    a limitation accepted on purpose. Reading the file to give a better answer
    would mean Cofferdam handling token bytes for a status line, and the run
    itself already reports the truthful answer through
    :func:`classify_auth_failure`.
    """
    directory = Path(state_dir)
    prepared = config_directory(directory).is_dir()
    credential_present = credential_path(directory).is_file()
    permissions_ok, permission_detail = permissions_safe(directory)

    if not cli_present:
        state = STATUS_CLI_MISSING
        detail = "the Claude Code CLI is not installed on this host"
    elif not prepared:
        state = STATUS_UNPREPARED
        detail = "the worker session directory has not been created yet"
    elif not permissions_ok:
        state = STATUS_PERMISSIONS_UNSAFE
        detail = permission_detail
    elif not credential_present:
        state = STATUS_LOGIN_REQUIRED
        detail = "Cofferdam's Claude worker session has never been logged in"
    else:
        # Present and correctly held. Whether the provider still honours it is
        # only knowable by asking, and a status read does not make network calls.
        state = STATUS_READY
        detail = None

    return SessionStatus(
        status=state,
        prepared=prepared,
        credential_present=credential_present,
        cli_version=cli_version,
        cli_present=cli_present,
        permissions_ok=permissions_ok,
        detail=detail,
    )


def classify_auth_failure(output: str) -> Optional[str]:
    """Which auth condition, if any, the CLI's own words describe.

    Returns a :data:`NEEDS_LOGIN` status or ``None``. ``None`` matters as much as
    the other two: it means *this failure was not about authentication*, and the
    caller must not relabel an ordinary error as "needs login". Mislabelling in
    that direction sends a person to a login screen for a bug.
    """
    lowered = (output or "").lower()
    if any(marker in lowered for marker in _EXPIRED_MARKERS):
        return STATUS_SESSION_EXPIRED
    if any(marker in lowered for marker in _LOGIN_MARKERS):
        return STATUS_LOGIN_REQUIRED
    return None


def require_usable(state_dir: Path, *, cli_present: bool = True) -> Path:
    """The config root, or a typed refusal naming what a person must do.

    Called before a worker starts so that an unauthenticated session fails
    *before* a worktree is cut and a process is launched, rather than after.
    """
    found = status(Path(state_dir), cli_present=cli_present)
    if not found.usable:
        raise WorkerSessionUnavailable(
            _sentence(found.status), status=found.status, detail=found.detail
        )
    return config_directory(Path(state_dir))


#: What a person should read. Kept beside the statuses so a new one cannot be
#: added without a sentence, and so the wording is reviewable in one place.
SENTENCES: Dict[str, str] = {
    STATUS_READY: "Cofferdam's Claude worker session is ready.",
    STATUS_LOGIN_REQUIRED: (
        "Cofferdam's Claude worker session has never been logged in. It needs a "
        "one-time login of its own — this is separate from your personal Claude "
        "session and does not touch it."
    ),
    STATUS_SESSION_EXPIRED: (
        "Cofferdam's Claude worker session has expired and could not refresh "
        "itself. It needs to be logged in again. Your personal Claude session is "
        "unaffected."
    ),
    STATUS_CLI_MISSING: "The Claude Code CLI is not installed on this host.",
    STATUS_UNPREPARED: (
        "Cofferdam's Claude worker session has not been set up on this host yet."
    ),
    STATUS_PERMISSIONS_UNSAFE: (
        "Cofferdam's Claude worker session directory has unsafe permissions and "
        "will not be used until that is corrected."
    ),
}


def _sentence(state: str) -> str:
    return SENTENCES.get(state, "Cofferdam's Claude worker session is not usable.")


@contextmanager
def held(state_dir: Path, *, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[Path]:
    """Hold the session for one invocation. Serialized, deliberately.

    Two CLI processes sharing one credential file could each refresh it, and one
    rotation would supersede the other's — the same defect this module exists to
    fix, reproduced indoors. Nothing in the CLI's interface promises that is
    safe, so it is not assumed to be.

    An ``flock`` rather than a lockfile-with-a-pid: the kernel releases it when
    the holder dies, so a killed daemon does not leave the session wedged. It is
    scoped to this one file and confers no authority over anything else — it is
    not a global worker lock and must not become one.
    """
    directory = Path(state_dir)
    prepare(directory)
    path = lock_path(directory)
    handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _acquire(handle, timeout)
        try:
            yield config_directory(directory)
        finally:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            except OSError:  # pragma: no cover - already released with the fd
                pass
    finally:
        os.close(handle)


def _acquire(handle: int, timeout: float) -> None:
    import time

    deadline = time.monotonic() + max(float(timeout), 0.0)
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):  # pragma: no cover
                raise
            if time.monotonic() >= deadline:
                raise WorkerSessionUnavailable(
                    "another Cofferdam worker is using the Claude session",
                    status=STATUS_READY,
                    detail="the session lock was held for longer than the timeout",
                )
            time.sleep(0.05)


#: The CLI's own auth report. Chosen because it is the one first-party answer
#: that is safe to print: measured against 2.1.221 it emits exactly
#: ``{"loggedIn", "authMethod", "apiProvider"}`` and no token material.
PROBE_FIELDS: Tuple[str, ...] = ("loggedIn", "authMethod", "apiProvider")


def probe(state_dir: Path, executable: Path, *, timeout: float = 60.0) -> Dict[str, Any]:
    """Ask the CLI whether the worker session is signed in. Non-secret answer.

    Runs ``claude auth status`` with ``CLAUDE_CONFIG_DIR`` pointed at the worker's
    own config root, so the answer is about **Cofferdam's** session and never the
    operator's. The environment is built by selection rather than inherited, for
    the same reason the sandbox's is: an inherited ``CLAUDE_CONFIG_DIR`` would
    silently redirect this at somebody else's credentials.

    Only :data:`PROBE_FIELDS` are kept. A future CLI that added a token field to
    this output would not leak it through here, because unknown keys are dropped
    rather than passed along.
    """
    import subprocess

    config = config_directory(Path(state_dir))
    try:
        completed = subprocess.run(
            [str(executable), "auth", "status"],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(config.parent),
                "CLAUDE_CONFIG_DIR": str(config),
                "NO_COLOR": "1",
                "TERM": "dumb",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"reachable": False, "detail": type(exc).__name__}

    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError:
        return {"reachable": False, "detail": "the CLI did not report parsable status"}
    if not isinstance(payload, dict):  # pragma: no cover - defensive
        return {"reachable": False, "detail": "unexpected status shape"}
    kept = {name: payload.get(name) for name in PROBE_FIELDS if name in payload}
    kept["reachable"] = True
    return kept


def describe(state_dir: Path, *, cli_version: Optional[str] = None,
             cli_present: bool = True) -> Dict[str, Any]:
    """The doctor surface. Safe to print, safe to log, safe to serialize.

    Asserted by test to contain no credential material — but the stronger reason
    to believe it is that this module never reads any. The only facts available
    to put here are existence, mode and the CLI's version.
    """
    found = status(Path(state_dir), cli_version=cli_version, cli_present=cli_present)
    payload = found.to_dict()
    payload["sentence"] = _sentence(found.status)
    # Not the path. A doctor line naming the directory would be the one place
    # this feature prints where the credentials live.
    payload["interior_config"] = INTERIOR_CONFIG
    return payload


__all__ = [
    "CONFIG_DIRNAME",
    "CREDENTIAL_FILENAME",
    "INTERIOR_CONFIG",
    "LOCK_TIMEOUT_SECONDS",
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
