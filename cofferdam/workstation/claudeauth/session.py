"""Cofferdam's own Claude sessions. One implementation, several namespaces.

Where this came from
--------------------

PR1g wrote all of this for the worker, and it was right the first time. What it
was not was *reusable*: every function derived its directory from one module
constant, so a second component needing its own session had exactly two options —
share the worker's credential, or copy the file.

Both are wrong for the same reason PR1g gives at length. Sharing means two
components rotating one refresh token, which is the divergence that broke a live
worker run and then the operator's own CLI with it. Copying is the same defect
with extra steps.

So the machinery is here, parameterised by a :class:`ClaudeSessionNamespace`, and
:mod:`~cofferdam.workstation.worker.session` is now a binding over it that keeps
its own public API byte-for-byte. Nothing about the worker's behaviour changed;
PR1g's tests exercise this file through that binding unmodified.

What a namespace is
-------------------

A directory name, a label for the sentences a person reads, and the exception
type to raise. Nothing else varies, because nothing else *should* vary: the
permission rule, the lock discipline, the status vocabulary, the failure
classification and the environment construction are the parts that were hard to
get right, and a second copy of them is a second thing to get wrong.

The properties every namespace inherits
----------------------------------------

**Its own persistent config root**, at ``<state_dir>/<dirname>/config``, mode
0700, derived from host configuration. There is no parameter anywhere above this
that names it — a caller-selectable credential location would be a way to point
an authenticated session at a directory somebody else controls.

**No copy, no import, no migration, no fallback.** Nothing here reads
``~/.claude``, and nothing reads another namespace's directory. A fallback would
silently reintroduce the rotation bug at the least observable moment, and a
freshly prepared session is an *unauthenticated* session that says so.

**An explicit environment, built by selection.** :func:`environment` returns the
whole mapping from constants and the namespace's own paths. It is never an update
of ``os.environ``, because an inherited ``CLAUDE_CONFIG_DIR`` would silently
redirect a subprocess at somebody else's credentials, and an inherited
``ANTHROPIC_API_KEY`` would authenticate it as somebody else entirely.

**Serialized.** An ``flock`` beside the config root, held for one invocation. Two
CLI processes refreshing one token file could each rotate it and the loser's
state would be stale — the same defect indoors.

Measured, not assumed
---------------------

Against the installed CLI (2.1.221): ``CLAUDE_CONFIG_DIR`` relocates the entire
state root — credentials read from there, ``.claude.json``, ``projects/``,
``sessions/`` and ``backups/`` written there, and **nothing at all** written into
``HOME``. Without credentials it prints ``Not logged in · Please run /login``;
with an unusable one, ``Failed to authenticate: OAuth session expired and could
not be refreshed``. Two distinct conditions, which is what makes
:data:`STATUS_LOGIN_REQUIRED` and :data:`STATUS_SESSION_EXPIRED` honest answers
rather than one guess.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple, Type

CONFIG_DIRNAME = "config"
LOCK_FILENAME = "session.lock"

#: The credential file the CLI keeps inside its config root. Named here only so
#: status can report *whether it exists* and *what mode it has*. Nothing in
#: Cofferdam parses it, and nothing reads its bytes.
CREDENTIAL_FILENAME = ".credentials.json"

#: How long to wait for another invocation to finish before refusing.
LOCK_TIMEOUT_SECONDS = 300.0

STATUS_READY = "ready"
STATUS_LOGIN_REQUIRED = "login_required"
STATUS_SESSION_EXPIRED = "session_expired"
STATUS_CLI_MISSING = "cli_missing"
STATUS_UNPREPARED = "unprepared"
STATUS_PERMISSIONS_UNSAFE = "permissions_unsafe"

#: Statuses a person can fix by logging the session in. Held as a set so a caller
#: can ask the question without matching strings.
NEEDS_LOGIN: frozenset = frozenset({STATUS_LOGIN_REQUIRED, STATUS_SESSION_EXPIRED})

#: Substrings the CLI prints for each auth condition, measured against 2.1.221.
#:
#: Matched to *classify* a failure, never to decide whether one happened — the
#: exit status does that. A future CLI that reworded these produces a less
#: specific auth reason, not a run that silently looks like a code failure.
_LOGIN_MARKERS: Tuple[str, ...] = ("not logged in", "please run /login", "/login")
_EXPIRED_MARKERS: Tuple[str, ...] = (
    "oauth session expired",
    "could not be refreshed",
    "session expired",
)

#: Variables that would authenticate a subprocess as somebody else, removed from
#: any inherited environment before a login flow runs. Listed rather than
#: filtered by prefix: a prefix rule silently covers variables nobody reviewed.
CREDENTIAL_ENVIRONMENT_NAMES: Tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CONFIG_DIR",
)


class ClaudeSessionUnavailable(Exception):
    """A Cofferdam-owned Claude session cannot be used, and this says which way.

    Carries a :data:`STATUS_READY`-family ``status`` so a caller can tell *this
    needs a person to log in* from *this is broken*. The distinction is the whole
    point: a status screen that says "your code failed" when the truth is "the
    session needs login" sends somebody debugging the wrong thing.
    """

    def __init__(
        self, message: str, *, status: str, detail: Optional[str] = None
    ) -> None:
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


@dataclass(frozen=True)
class ClaudeSessionNamespace:
    """One Cofferdam-owned Claude session: where it lives and what to call it.

    ``dirname`` is a **code-owned constant** in the module that declares the
    namespace. It is never derived from configuration, never from a request, and
    never from anything a model wrote — see the module docstring on why a
    caller-selectable credential location is not a feature.
    """

    #: The directory beneath ``state_dir``. Code-owned, never caller-supplied.
    dirname: str
    #: What a person is told this session is. Used only in prose.
    label: str
    #: Where the config root appears inside a sandbox namespace, when the
    #: component runs in one. ``None`` for a component that runs as an ordinary
    #: subprocess and therefore has no interior path.
    interior_config: Optional[str] = None
    #: Raised by :func:`require_usable` and :func:`held`. A namespace supplies
    #: its own so a caller can catch the failure of *its* session specifically.
    error: Type[ClaudeSessionUnavailable] = ClaudeSessionUnavailable
    #: Per-status prose. Filled from :data:`SENTENCE_TEMPLATES` when absent, so a
    #: new namespace cannot exist without a sentence for every status.
    sentences: Mapping[str, str] = field(default_factory=dict)

    def sentence(self, status_name: str) -> str:
        found = self.sentences.get(status_name)
        if found is not None:
            return found
        template = SENTENCE_TEMPLATES.get(status_name)
        if template is None:  # pragma: no cover - a status with no sentence
            return f"Cofferdam's Claude {self.label} session is not usable."
        return template.format(label=self.label)


#: One sentence per status, written for a person. Kept as data beside the
#: statuses so a new status cannot be added without one, and so the wording is
#: reviewable in one place rather than scattered across a template.
SENTENCE_TEMPLATES: Dict[str, str] = {
    STATUS_READY: "Cofferdam's Claude {label} session is ready.",
    STATUS_LOGIN_REQUIRED: (
        "Cofferdam's Claude {label} session has never been logged in. It needs a "
        "one-time login of its own — this is separate from your personal Claude "
        "session and does not touch it."
    ),
    STATUS_SESSION_EXPIRED: (
        "Cofferdam's Claude {label} session has expired and could not refresh "
        "itself. It needs to be logged in again. Your personal Claude session is "
        "unaffected."
    ),
    STATUS_CLI_MISSING: "The Claude Code CLI is not installed on this host.",
    STATUS_UNPREPARED: (
        "Cofferdam's Claude {label} session has not been set up on this host yet."
    ),
    STATUS_PERMISSIONS_UNSAFE: (
        "Cofferdam's Claude {label} session directory has unsafe permissions and "
        "will not be used until that is corrected."
    ),
}


# -- paths ---------------------------------------------------------------------


def session_root(state_dir: Path, namespace: ClaudeSessionNamespace) -> Path:
    return Path(state_dir) / namespace.dirname


def config_directory(state_dir: Path, namespace: ClaudeSessionNamespace) -> Path:
    """The one durable place this namespace's Claude credentials live.

    A pure function of the host's state directory and a code-owned constant. No
    caller supplies it, and there is no parameter above this that selects one.
    """
    return session_root(state_dir, namespace) / CONFIG_DIRNAME


def lock_path(state_dir: Path, namespace: ClaudeSessionNamespace) -> Path:
    return session_root(state_dir, namespace) / LOCK_FILENAME


def credential_path(state_dir: Path, namespace: ClaudeSessionNamespace) -> Path:
    """Where the CLI keeps this namespace's credential. **Never read.**

    Used for ``exists`` and ``stat`` only — see :func:`status`.
    """
    return config_directory(state_dir, namespace) / CREDENTIAL_FILENAME


def prepare(state_dir: Path, namespace: ClaudeSessionNamespace) -> Path:
    """Create the durable config root if absent. **Never populates it.**

    Deliberately does not copy, import or migrate anything — not from the
    operator's ``~/.claude``, and not from another namespace. A freshly prepared
    session is an *unauthenticated* session, and it says so through :func:`status`
    until somebody logs it in. Importing a credential here is exactly the
    shortcut that produced the defect this design exists to fix, and it would
    fail silently the first time it mattered.
    """
    root = session_root(Path(state_dir), namespace)
    config = config_directory(Path(state_dir), namespace)
    config.mkdir(parents=True, exist_ok=True)
    for directory in (root, config):
        try:
            os.chmod(directory, 0o700)
        except OSError:  # pragma: no cover - odd filesystems
            pass
    return config


def permissions_safe(
    state_dir: Path, namespace: ClaudeSessionNamespace
) -> Tuple[bool, Optional[str]]:
    """Whether the durable session is readable only by its owner.

    Checked rather than assumed on every status read, because a credential store
    that quietly became group-readable is the kind of thing nobody notices until
    it matters.
    """
    config = config_directory(Path(state_dir), namespace)
    if not config.is_dir():
        return False, f"the {namespace.label} session directory does not exist yet"
    mode = config.stat().st_mode & 0o777
    if mode & 0o077:
        return False, (
            f"the {namespace.label} session directory is mode {mode:o}, not 700"
        )
    credential = credential_path(Path(state_dir), namespace)
    if credential.is_file():
        credential_mode = credential.stat().st_mode & 0o777
        if credential_mode & 0o077:
            return False, (
                f"the {namespace.label} credential file is readable beyond its owner"
            )
    return True, None


# -- status --------------------------------------------------------------------


@dataclass(frozen=True)
class SessionStatus:
    """What can be said about a session without opening it.

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


def status(
    state_dir: Path,
    namespace: ClaudeSessionNamespace,
    *,
    cli_version: Optional[str] = None,
    cli_present: bool = True,
) -> SessionStatus:
    """A non-secret answer to "can this session sign in".

    Reads the *presence* and *mode* of the credential file and nothing else. It
    does not open it, does not parse it, and cannot report its expiry — which is
    a limitation accepted on purpose. Reading the file to give a better answer
    would mean Cofferdam handling token bytes for a status line, and the run
    itself already reports the truthful answer through
    :func:`classify_auth_failure`.
    """
    directory = Path(state_dir)
    prepared = config_directory(directory, namespace).is_dir()
    credential_present = credential_path(directory, namespace).is_file()
    permissions_ok, permission_detail = permissions_safe(directory, namespace)

    if not cli_present:
        state = STATUS_CLI_MISSING
        detail = "the Claude Code CLI is not installed on this host"
    elif not prepared:
        state = STATUS_UNPREPARED
        detail = f"the {namespace.label} session directory has not been created yet"
    elif not permissions_ok:
        state = STATUS_PERMISSIONS_UNSAFE
        detail = permission_detail
    elif not credential_present:
        state = STATUS_LOGIN_REQUIRED
        detail = (
            f"Cofferdam's Claude {namespace.label} session has never been logged in"
        )
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


def require_usable(
    state_dir: Path, namespace: ClaudeSessionNamespace, *, cli_present: bool = True
) -> Path:
    """The config root, or a typed refusal naming what a person must do.

    Called *before* a component starts work, so that an unauthenticated session
    fails before anything is cut, launched or charged rather than after.
    """
    found = status(Path(state_dir), namespace, cli_present=cli_present)
    if not found.usable:
        raise namespace.error(
            namespace.sentence(found.status),
            status=found.status,
            detail=found.detail,
        )
    return config_directory(Path(state_dir), namespace)


def describe(
    state_dir: Path,
    namespace: ClaudeSessionNamespace,
    *,
    cli_version: Optional[str] = None,
    cli_present: bool = True,
) -> Dict[str, Any]:
    """The doctor surface. Safe to print, safe to log, safe to serialize.

    Asserted by test to contain no credential material — but the stronger reason
    to believe it is that this module never reads any. The only facts available
    to put here are existence, mode and the CLI's version.
    """
    found = status(
        Path(state_dir), namespace, cli_version=cli_version, cli_present=cli_present
    )
    payload = found.to_dict()
    payload["sentence"] = namespace.sentence(found.status)
    payload["session"] = namespace.label
    # Not the host path. A doctor line naming the directory would be the one
    # place this feature prints where the credentials live.
    if namespace.interior_config is not None:
        payload["interior_config"] = namespace.interior_config
    return payload


# -- the environment a subprocess is given -------------------------------------


def environment(
    state_dir: Path,
    namespace: ClaudeSessionNamespace,
    *,
    path: str = "/usr/bin:/bin",
    extra: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """The complete environment for a CLI subprocess. **Built, never inherited.**

    This function is the credential boundary for every component that runs the
    CLI as an ordinary subprocess, and the fact that it returns a whole mapping
    rather than an overlay is the mechanism:

    * ``HOME`` points inside this namespace, so a ``~/.claude`` lookup can only
      ever resolve within it. The operator's home is not reachable, so there is
      no implicit fallback to their session — not disabled, *absent*.
    * ``CLAUDE_CONFIG_DIR`` is this namespace's own config root, so the CLI reads
      and writes its entire state there.
    * ``ANTHROPIC_API_KEY``, ``ANTHROPIC_AUTH_TOKEN`` and
      ``CLAUDE_CODE_OAUTH_TOKEN`` cannot appear, because nothing is copied in.
      An inherited one would authenticate the subprocess as somebody else while
      every path in it still looked correct.

    ``extra`` exists for values a component genuinely needs (a locale, a proxy).
    It is applied first and **cannot override** the four names above, so a caller
    cannot use it to reintroduce what this function exists to remove.
    """
    config = config_directory(Path(state_dir), namespace)
    built: Dict[str, str] = {}
    for key, value in (extra or {}).items():
        built[str(key)] = str(value)
    for name in CREDENTIAL_ENVIRONMENT_NAMES:
        built.pop(name, None)
    built.update(
        {
            "PATH": path,
            "HOME": str(config.parent),
            "CLAUDE_CONFIG_DIR": str(config),
            "NO_COLOR": "1",
            "TERM": "dumb",
        }
    )
    return built


def login_environment(
    state_dir: Path, namespace: ClaudeSessionNamespace, *, inherited: Mapping[str, str]
) -> Dict[str, str]:
    """The environment for an *interactive* login. Inherited, then corrected.

    Unlike :func:`environment` this starts from the operator's own, because a
    login legitimately needs a terminal, a browser opener and a display. The two
    things that decide *which session is being logged in* are overridden, and
    every variable that could authenticate the flow as somebody else is removed —
    which is the whole trick: the same first-party login flow, pointed at
    Cofferdam's config root instead of the operator's.
    """
    built = dict(inherited)
    for name in CREDENTIAL_ENVIRONMENT_NAMES:
        built.pop(name, None)
    built["CLAUDE_CONFIG_DIR"] = str(config_directory(Path(state_dir), namespace))
    return built


# -- serialization --------------------------------------------------------------


@contextmanager
def held(
    state_dir: Path,
    namespace: ClaudeSessionNamespace,
    *,
    timeout: float = LOCK_TIMEOUT_SECONDS,
) -> Iterator[Path]:
    """Hold one session for one invocation. Serialized, deliberately.

    Two CLI processes sharing one credential file could each refresh it, and one
    rotation would supersede the other's — the defect this design exists to fix,
    reproduced indoors. Nothing in the CLI's interface promises that is safe, so
    it is not assumed to be.

    An ``flock`` rather than a lockfile-with-a-pid: the kernel releases it when
    the holder dies, so a killed daemon does not leave the session wedged. It is
    scoped to one namespace's own file and confers no authority over anything
    else — in particular, the worker's lock and the planner's are different
    files, so neither can block the other.
    """
    directory = Path(state_dir)
    prepare(directory, namespace)
    path = lock_path(directory, namespace)
    handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _acquire(handle, timeout, namespace)
        try:
            yield config_directory(directory, namespace)
        finally:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            except OSError:  # pragma: no cover - already released with the fd
                pass
    finally:
        os.close(handle)


def _acquire(handle: int, timeout: float, namespace: ClaudeSessionNamespace) -> None:
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
                raise namespace.error(
                    f"another Cofferdam {namespace.label} is using the Claude session",
                    status=STATUS_READY,
                    detail="the session lock was held for longer than the timeout",
                )
            time.sleep(0.05)


# -- asking the CLI -------------------------------------------------------------

#: The CLI's own auth report. Chosen because it is the one first-party answer
#: that is safe to print: measured against 2.1.221 it emits exactly
#: ``{"loggedIn", "authMethod", "apiProvider"}`` and no token material.
PROBE_FIELDS: Tuple[str, ...] = ("loggedIn", "authMethod", "apiProvider")


def probe(
    state_dir: Path,
    namespace: ClaudeSessionNamespace,
    executable: Path,
    *,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """Ask the CLI whether this namespace's session is signed in.

    Runs ``claude auth status`` under :func:`environment`, so the answer is about
    **this** session and never the operator's or another component's.

    Only :data:`PROBE_FIELDS` are kept. A future CLI that added a token field to
    this output would not leak it through here, because unknown keys are dropped
    rather than passed along.
    """
    import subprocess

    try:
        completed = subprocess.run(
            [str(executable), "auth", "status"],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env=environment(state_dir, namespace),
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


__all__ = [
    "CONFIG_DIRNAME",
    "CREDENTIAL_ENVIRONMENT_NAMES",
    "CREDENTIAL_FILENAME",
    "LOCK_FILENAME",
    "LOCK_TIMEOUT_SECONDS",
    "NEEDS_LOGIN",
    "PROBE_FIELDS",
    "SENTENCE_TEMPLATES",
    "STATUS_CLI_MISSING",
    "STATUS_LOGIN_REQUIRED",
    "STATUS_PERMISSIONS_UNSAFE",
    "STATUS_READY",
    "STATUS_SESSION_EXPIRED",
    "STATUS_UNPREPARED",
    "ClaudeSessionNamespace",
    "ClaudeSessionUnavailable",
    "SessionStatus",
    "classify_auth_failure",
    "config_directory",
    "credential_path",
    "describe",
    "environment",
    "held",
    "lock_path",
    "login_environment",
    "permissions_safe",
    "prepare",
    "probe",
    "require_usable",
    "session_root",
    "status",
]
