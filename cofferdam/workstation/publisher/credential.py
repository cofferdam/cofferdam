"""The Git publishing credential. Held here, and reachable from nowhere else.

Why not the operator's ``gh`` session
--------------------------------------

Inspected on this host rather than assumed: the operator's ``gh`` login is a
**classic** token with scopes ``gist``, ``read:org``, ``repo`` and ``workflow``.
``repo`` alone is full control of every repository the account can reach —
private ones included — and ``workflow`` can rewrite CI definitions. A publisher
that needs to push one branch and open one pull request does not need any of
that, and borrowing it would mean an unattended process holding the operator's
whole GitHub identity.

So the publisher gets its **own** credential, and the shape chosen is a
repository-scoped **fine-grained token** with exactly two permissions:

* *Contents: read and write* — required to push a branch;
* *Pull requests: read and write* — required to open and read a PR.

Deliberately not requested: administration, workflows, environments,
deployments, secrets, organization administration, or any permission on a
repository Cofferdam is not publishing to.

A GitHub App would be narrower again — installation-scoped, short-lived tokens,
no user identity behind it — and is the right answer at more than a couple of
repositories. It is not the right answer for the first one: it needs an App
registration, a private key on this host, JWT signing and an installation-token
exchange, which is a larger secret-handling surface than the thing it protects.
That trade is recorded here so the next person can revisit it rather than
rediscover it.

How the secret reaches Git without ending up somewhere it can be read
----------------------------------------------------------------------

Not in ``argv`` — anything on a command line is visible in ``ps`` to anybody on
the host and lands in shell history and process accounting. Not in a remote URL
either, because a URL with a token in it gets written into ``.git/config``,
reflogs and error messages.

Instead the token is stored **in Git's own credentials format** at
:func:`credentials_file`, mode 0600, and Git is told to read it with
``-c credential.helper=store --file=<path>``. The path appears in ``argv``; the
secret never does. The GitHub API side uses :mod:`urllib` with an
``Authorization`` header, so there the secret exists only in this process's
memory — no child, no environment variable, no ``gh`` invocation, and therefore
no chance of silently falling back to the operator's keyring.

What this module will not do
-----------------------------

Read the operator's ``gh`` token, shell out to ``gh``, accept a credential from a
caller, or write a token into any read model, log line or exception. The only
thing it exposes about the secret is whether one exists and what mode it has.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

#: Beneath the state directory, beside the Claude session and never inside it.
PUBLISHER_DIRNAME = "git-publisher"

#: Git's own credential-store format, so Git can read it without help from us.
CREDENTIALS_FILENAME = "git-credentials"

#: The username half of a token credential on GitHub over HTTPS. A constant, not
#: a secret; GitHub ignores it and reads the password field.
TOKEN_USERNAME = "x-access-token"

GITHUB_API = "https://api.github.com"

STATUS_READY = "ready"
STATUS_UNCONFIGURED = "unconfigured"
STATUS_PERMISSIONS_UNSAFE = "permissions_unsafe"
STATUS_MALFORMED = "malformed"

#: Statuses a person fixes by configuring the publisher.
NEEDS_CONFIGURATION: frozenset = frozenset(
    {STATUS_UNCONFIGURED, STATUS_PERMISSIONS_UNSAFE, STATUS_MALFORMED}
)

#: What a fine-grained token looks like. Used to *reject* something pasted into
#: the wrong place — a classic ``ghp_``/``gho_`` token is accepted too, but the
#: doctor says which kind it is so an operator can see they granted more than
#: they meant to.
_FINE_GRAINED = re.compile(r"^github_pat_[A-Za-z0-9_]{20,}$")
_CLASSIC = re.compile(r"^gh[pous]_[A-Za-z0-9]{20,}$")

_CREDENTIAL_LINE = re.compile(
    r"^https://(?P<user>[^:@/]+):(?P<secret>[^@]+)@(?P<host>[^/\s]+)$"
)


class PublisherCredentialUnavailable(Exception):
    """The publisher cannot authenticate, and this says which way.

    Separate from every worker failure on purpose. A publishing credential that
    is missing is not a development step that went wrong: the worker's commit is
    finished and safe on a local branch, and the only thing that failed is
    Cofferdam's ability to send it. Reporting that as a worker failure would send
    somebody to read a model's output looking for a mistake that is not there.
    """

    def __init__(self, message: str, *, status: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail

    @property
    def needs_configuration(self) -> bool:
        return self.status in NEEDS_CONFIGURATION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "needs_configuration": self.needs_configuration,
            "message": str(self),
            "detail": self.detail,
        }


def publisher_root(state_dir: Path) -> Path:
    return Path(state_dir) / PUBLISHER_DIRNAME


def credentials_file(state_dir: Path) -> Path:
    """The one durable place the publishing credential lives.

    A pure function of the host's state directory, exactly like the Claude
    session's config root and for the same reason: no caller supplies it, and
    there is no parameter above this that selects one.
    """
    return publisher_root(state_dir) / CREDENTIALS_FILENAME


def prepare(state_dir: Path) -> Path:
    """Create the credential directory. **Never populates it.**

    Same rule as the Claude session: a prepared publisher is an *unconfigured*
    publisher and says so until somebody configures it. Nothing here reads the
    operator's ``gh`` token, and there is deliberately no import path from it —
    a convenience import would be indistinguishable from the credential
    separation quietly not existing.
    """
    root = publisher_root(Path(state_dir))
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:  # pragma: no cover - odd filesystems
        pass
    return root


def store(state_dir: Path, token: str, *, host: str = "github.com") -> Path:
    """Write the credential in Git's format, 0600, replacing any previous one.

    Called by the configure command with a token the operator pasted, and by
    nothing else. The token is not logged, not echoed and not returned.
    """
    token = (token or "").strip()
    if not token:
        raise PublisherCredentialUnavailable(
            "no token was given", status=STATUS_MALFORMED
        )
    if "\n" in token or "@" in token or "/" in token:
        # Would corrupt the credential file's line format, and a value with
        # those in it is not a GitHub token.
        raise PublisherCredentialUnavailable(
            "that does not look like a GitHub token", status=STATUS_MALFORMED
        )
    prepare(state_dir)
    path = credentials_file(Path(state_dir))
    # Created 0600 *before* the secret is written, not chmod-ed afterwards: a
    # chmod after the write leaves a window where the file exists world-readable.
    handle = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        os.write(handle, f"https://{TOKEN_USERNAME}:{token}@{host}\n".encode())
    finally:
        os.close(handle)
    os.chmod(path, 0o600)
    return path


def token_kind(token: str) -> str:
    if _FINE_GRAINED.match(token or ""):
        return "fine_grained"
    if _CLASSIC.match(token or ""):
        return "classic"
    return "unknown"


def _read(state_dir: Path) -> Tuple[Optional[str], Optional[str]]:
    """``(token, host)`` from the credential file. Never logged, never returned
    to a caller outside this module's own operations."""
    path = credentials_file(Path(state_dir))
    if not path.is_file():
        return None, None
    try:
        line = path.read_text(encoding="utf-8").strip().splitlines()[0]
    except (OSError, IndexError):
        return None, None
    found = _CREDENTIAL_LINE.match(line)
    if not found:
        return None, None
    return found.group("secret"), found.group("host")


def permissions_safe(state_dir: Path) -> Tuple[bool, Optional[str]]:
    root = publisher_root(Path(state_dir))
    path = credentials_file(Path(state_dir))
    if not path.is_file():
        return False, "the publisher credential has not been configured"
    if root.stat().st_mode & 0o077:
        return False, "the publisher directory is readable beyond its owner"
    if path.stat().st_mode & 0o077:
        return False, "the publisher credential is readable beyond its owner"
    return True, None


@dataclass(frozen=True)
class PublisherStatus:
    """What can be said about the publishing credential without exposing it.

    **No token field, and no place to add one.** The only facts here are
    existence, mode, which *kind* of token it is, and whatever GitHub says about
    it when asked — none of which is the secret.
    """

    status: str
    configured: bool
    permissions_ok: bool
    token_kind: Optional[str] = None
    host: Optional[str] = None
    detail: Optional[str] = None

    @property
    def usable(self) -> bool:
        return self.status == STATUS_READY

    @property
    def needs_configuration(self) -> bool:
        return self.status in NEEDS_CONFIGURATION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "usable": self.usable,
            "needs_configuration": self.needs_configuration,
            "configured": self.configured,
            "permissions_ok": self.permissions_ok,
            "token_kind": self.token_kind,
            "host": self.host,
            "detail": self.detail,
        }


def status(state_dir: Path) -> PublisherStatus:
    """A non-secret answer to "can Cofferdam publish". Makes no network call."""
    path = credentials_file(Path(state_dir))
    if not path.is_file():
        return PublisherStatus(
            status=STATUS_UNCONFIGURED, configured=False, permissions_ok=False,
            detail="the publisher credential has not been configured",
        )
    permissions_ok, permission_detail = permissions_safe(Path(state_dir))
    if not permissions_ok:
        return PublisherStatus(
            status=STATUS_PERMISSIONS_UNSAFE, configured=True, permissions_ok=False,
            detail=permission_detail,
        )
    token, host = _read(Path(state_dir))
    if token is None:
        return PublisherStatus(
            status=STATUS_MALFORMED, configured=True, permissions_ok=True,
            detail="the credential file is not in Git's credential format",
        )
    return PublisherStatus(
        status=STATUS_READY, configured=True, permissions_ok=True,
        token_kind=token_kind(token), host=host,
    )


def require_usable(state_dir: Path) -> Path:
    """The credentials file path, or a typed refusal naming what to do.

    Returns a **path**, never the token. Callers hand the path to Git; only
    :func:`api_request` ever holds the secret itself.
    """
    found = status(Path(state_dir))
    if not found.usable:
        raise PublisherCredentialUnavailable(
            SENTENCES.get(found.status, "the publisher cannot authenticate"),
            status=found.status,
            detail=found.detail,
        )
    return credentials_file(Path(state_dir))


SENTENCES: Dict[str, str] = {
    STATUS_READY: "Cofferdam's Git publisher is configured.",
    STATUS_UNCONFIGURED: (
        "Cofferdam's Git publisher has no credential yet. It needs its own "
        "repository-scoped GitHub token — separate from your personal gh login, "
        "which is not read or used."
    ),
    STATUS_PERMISSIONS_UNSAFE: (
        "Cofferdam's Git publisher credential has unsafe permissions and will "
        "not be used until that is corrected."
    ),
    STATUS_MALFORMED: (
        "Cofferdam's Git publisher credential could not be read. It needs to be "
        "configured again."
    ),
}


def api_request(
    state_dir: Path,
    method: str,
    path: str,
    *,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Tuple[int, Any]:
    """One GitHub REST call, authenticated with the publisher's own token.

    ``urllib`` with an ``Authorization`` header rather than ``gh``: the secret
    exists only in this process's memory, there is no child process to inherit
    it, nothing lands in an environment variable, and — the part that matters
    most — there is no path by which the operator's keyring could be used
    instead. ``gh`` would silently fall back to it.

    ``path`` is built by this package from validated identifiers; it is never a
    caller's string. The return is ``(status, parsed)`` so callers branch on the
    code rather than on an exception for ordinary outcomes like *already exists*.
    """
    token, _ = _read(Path(state_dir))
    if token is None:
        raise PublisherCredentialUnavailable(
            SENTENCES[STATUS_UNCONFIGURED], status=STATUS_UNCONFIGURED
        )
    if not path.startswith("/"):  # pragma: no cover - callers pass literals
        raise ValueError("an API path must be rooted")

    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        GITHUB_API + path,
        data=data,
        method=method.upper(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cofferdam-publisher",
            **({"Content-Type": "application/json"} if data else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        return exc.code, parsed
    except urllib.error.URLError as exc:
        raise PublisherCredentialUnavailable(
            "GitHub could not be reached",
            status=STATUS_READY,
            detail=type(exc).__name__,
        )
    finally:
        del token


def describe(state_dir: Path, *, reach: bool = False) -> Dict[str, Any]:
    """The doctor surface. Safe to print, safe to log, safe to serialize."""
    found = status(Path(state_dir))
    payload = found.to_dict()
    payload["sentence"] = SENTENCES.get(found.status, "")
    if reach and found.usable:
        # What GitHub says about the token, filtered to non-secret facts.
        code, parsed = api_request(Path(state_dir), "GET", "/user")
        if code == 200 and isinstance(parsed, dict):
            payload["identity"] = {
                "login": parsed.get("login"), "type": parsed.get("type")
            }
            payload["reachable"] = True
        elif code == 403 and isinstance(parsed, dict):
            # A fine-grained token has no /user access; that is expected and is
            # not a failure. Reported as-is rather than smoothed over.
            payload["identity"] = {"login": None, "type": "fine_grained_token"}
            payload["reachable"] = True
        else:
            payload["reachable"] = False
            payload["detail"] = f"GitHub answered {code}"
    return payload


__all__ = [
    "CREDENTIALS_FILENAME",
    "GITHUB_API",
    "NEEDS_CONFIGURATION",
    "PUBLISHER_DIRNAME",
    "SENTENCES",
    "STATUS_MALFORMED",
    "STATUS_PERMISSIONS_UNSAFE",
    "STATUS_READY",
    "STATUS_UNCONFIGURED",
    "TOKEN_USERNAME",
    "PublisherCredentialUnavailable",
    "PublisherStatus",
    "api_request",
    "credentials_file",
    "describe",
    "permissions_safe",
    "prepare",
    "publisher_root",
    "require_usable",
    "status",
    "store",
    "token_kind",
]
