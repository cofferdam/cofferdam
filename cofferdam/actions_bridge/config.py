"""Bridge configuration, and the two credentials it never prints.

Layout under ``COFFERDAM_HOME`` (default ``~/cofferdam``)::

    <home>/secrets/actions-bridge-key            the EXTERNAL key (0600)
    <home>/secrets/actions-bridge-internal-token the INTERNAL token (0600)
    <home>/state/actions-bridge/idempotency.db   the bridge's own small table

The two credentials
-------------------

They are different secrets with different blast radii and they are never the
same bytes.

The **external key** is what the Custom GPT holds. It is typed into the GPT
editor, stored on OpenAI's side, and is the only credential a remote caller ever
presents. Compromising it lets somebody create and steer tasks through the eight
bridge Actions — bad, bounded, and revoked by writing a new file.

The **internal token** is what the bridge presents to the Cofferdam daemon. It
never leaves this machine, never appears in a bridge response, and is generated
by the daemon rather than by anybody's hand. Compromising it means somebody with
local access has the ten task routes — which somebody with local access has
anyway, by reading the device token that sits beside it.

:func:`load_bridge_config` refuses to start when either file is missing,
world-readable or group-readable. Refuses, rather than fixing the mode: a
credential that was briefly readable may already have been read, and quietly
tightening it would hide that. The operator regenerates it.

What is *not* configurable
--------------------------

The upstream **route set**. It is a tuple of constants in
:mod:`~cofferdam.actions_bridge.internal`, not a setting, because a
configuration file that could name a Cofferdam path is a configuration file that
could be edited into a proxy.

Only the base URL is configurable, and it is validated to be an ``http`` or
``https`` origin with no path, query or fragment — so the fixed suffixes the
internal client appends cannot be re-pointed by a crafted base.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from .limits import (
    DEFAULT_RECENT_TASKS,
    MAX_BODY_BYTES,
    MAX_CONCURRENT_REQUESTS,
    MAX_RECENT_TASKS,
    MUTATION_RATE_LIMIT_BURST,
    MUTATION_RATE_LIMIT_PER_MINUTE,
    RATE_LIMIT_BURST,
    RATE_LIMIT_PER_MINUTE,
    UPSTREAM_TIMEOUT_SECONDS,
)

#: Loopback, and it takes a deliberate flag to change. The bridge is designed to
#: sit behind a tunnel that connects *outward*; binding it to an interface is a
#: separate decision with its own approval gate, and the default should be the
#: one that cannot be reached by accident.
DEFAULT_BIND_HOST = "127.0.0.1"
#: Beside the daemon's 7101, not on it. Two processes, two ports, and a
#: misconfigured tunnel that points at the wrong one reaches a service that
#: refuses it rather than the private API.
DEFAULT_BIND_PORT = 7108

ENV_HOME = "COFFERDAM_HOME"
ENV_BIND_HOST = "COFFERDAM_BRIDGE_BIND_HOST"
ENV_BIND_PORT = "COFFERDAM_BRIDGE_BIND_PORT"
ENV_INTERNAL_BASE_URL = "COFFERDAM_BRIDGE_INTERNAL_BASE_URL"

#: Where the daemon listens when nothing says otherwise. Loopback rather than
#: the tailnet address the daemon may also be on: the bridge and the daemon are
#: on the same host, and a loopback hop cannot be observed from the network.
DEFAULT_INTERNAL_BASE_URL = "http://127.0.0.1:7101"

#: 32 bytes, URL-safe. Long enough that the rate limiter is a courtesy rather
#: than the defence.
KEY_BYTES = 32

#: The permission bits a secret file may have. Anything else fails startup.
_ALLOWED_SECRET_MODE = stat.S_IRUSR | stat.S_IWUSR


class BridgeConfigError(RuntimeError):
    """Startup cannot proceed. Carries a path, never a secret's contents."""


@dataclass(frozen=True)
class BridgeConfig:
    """The effective bridge configuration.

    Holds paths and numbers. It does **not** hold either credential: those are
    read at startup into local variables in
    :func:`~cofferdam.actions_bridge.service.create_bridge_app` and closed over,
    so nothing can reach a secret through an object a request handler is given.
    """

    home: Path
    bind_host: str
    bind_port: int
    internal_base_url: str
    external_key_path: Path
    internal_token_path: Path
    state_dir: Path
    upstream_timeout_seconds: float = UPSTREAM_TIMEOUT_SECONDS
    max_body_bytes: int = MAX_BODY_BYTES
    rate_limit_per_minute: int = RATE_LIMIT_PER_MINUTE
    rate_limit_burst: int = RATE_LIMIT_BURST
    mutation_rate_limit_per_minute: int = MUTATION_RATE_LIMIT_PER_MINUTE
    mutation_rate_limit_burst: int = MUTATION_RATE_LIMIT_BURST
    max_concurrent_requests: int = MAX_CONCURRENT_REQUESTS
    default_recent_tasks: int = DEFAULT_RECENT_TASKS
    max_recent_tasks: int = MAX_RECENT_TASKS

    @property
    def idempotency_path(self) -> Path:
        return self.state_dir / "idempotency.db"

    @property
    def loopback_only(self) -> bool:
        return self.bind_host in ("127.0.0.1", "::1", "localhost")

    def summary(self) -> dict:
        """A startup summary safe to print. Paths and numbers, no values.

        The two credential paths are *named* — an operator needs to know which
        files were read — and neither is opened here. Nothing in this dictionary
        has ever held a secret, so there is no field a future change could
        accidentally start filling with one.
        """
        return {
            "bind": f"{self.bind_host}:{self.bind_port}",
            "loopback_only": self.loopback_only,
            "internal_base_url": self.internal_base_url,
            "external_key_file": str(self.external_key_path),
            "internal_token_file": str(self.internal_token_path),
            "idempotency_db": str(self.idempotency_path),
            "upstream_timeout_seconds": self.upstream_timeout_seconds,
            "max_body_bytes": self.max_body_bytes,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "mutation_rate_limit_per_minute": self.mutation_rate_limit_per_minute,
            "max_concurrent_requests": self.max_concurrent_requests,
        }


def _home() -> Path:
    value = os.environ.get(ENV_HOME)
    return (Path(value) if value else Path.home() / "cofferdam").expanduser()


def _validated_base_url(value: str) -> str:
    """An origin, and nothing but an origin.

    A base URL carrying a path would let a crafted setting re-point every fixed
    suffix the internal client appends — ``/api/tasks`` under a base of
    ``http://host/evil/..`` is a different request from the one the code reads
    as. So the scheme, host and port are accepted and everything else is a
    startup failure.
    """
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise BridgeConfigError(
            "the internal base URL must be http or https, not " + repr(parts.scheme)
        )
    if not parts.hostname:
        raise BridgeConfigError("the internal base URL has no host")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise BridgeConfigError(
            "the internal base URL must be an origin with no path, query or fragment"
        )
    if parts.username or parts.password:
        raise BridgeConfigError(
            "the internal base URL must not carry credentials; the token file is "
            "the only place a credential belongs"
        )
    return f"{parts.scheme}://{parts.netloc}"


def load_bridge_config(
    home: Optional[Path] = None,
    *,
    bind_host: Optional[str] = None,
    bind_port: Optional[int] = None,
    internal_base_url: Optional[str] = None,
) -> BridgeConfig:
    """Build the effective configuration. Reads no secret and creates no file.

    Precedence, matching the workstation's: explicit argument > environment
    variable > built-in default.
    """
    root = Path(home).expanduser() if home is not None else _home()
    host = bind_host or os.environ.get(ENV_BIND_HOST) or DEFAULT_BIND_HOST
    raw_port = bind_port or os.environ.get(ENV_BIND_PORT) or DEFAULT_BIND_PORT
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        raise BridgeConfigError("the bridge port must be a number")
    if not 1 <= port <= 65535:
        raise BridgeConfigError("the bridge port is outside the valid range")

    base = (
        internal_base_url
        or os.environ.get(ENV_INTERNAL_BASE_URL)
        or DEFAULT_INTERNAL_BASE_URL
    )
    return BridgeConfig(
        home=root,
        bind_host=str(host),
        bind_port=port,
        internal_base_url=_validated_base_url(str(base)),
        external_key_path=root / "secrets" / "actions-bridge-key",
        internal_token_path=root / "secrets" / "actions-bridge-internal-token",
        state_dir=root / "state" / "actions-bridge",
    )


def read_secret_file(path: Path, *, what: str) -> str:
    """One credential, from one 0600 file, or a precise startup failure.

    Four refusals, in the order a reader would ask about them: is it there, is
    it a regular file, is it owner-only, and is there anything in it. Each names
    the path and the remedy and never the contents — an error message quoting a
    credential is the classic way one reaches a log.
    """
    try:
        info = path.lstat()
    except OSError:
        raise BridgeConfigError(
            f"the {what} file is missing: {path}. Create it with "
            "`python -m cofferdam.actions_bridge --generate-key` (external) or by "
            "starting the daemon with --enable-actions-bridge-caller (internal)."
        )
    if stat.S_ISLNK(info.st_mode):
        raise BridgeConfigError(
            f"the {what} file is a symlink: {path}. A credential must be a real "
            "file, so that its mode is the mode of the thing being read."
        )
    if not stat.S_ISREG(info.st_mode):
        raise BridgeConfigError(f"the {what} file is not a regular file: {path}")
    mode = stat.S_IMODE(info.st_mode)
    if mode & ~_ALLOWED_SECRET_MODE:
        raise BridgeConfigError(
            f"the {what} file is readable by more than its owner "
            f"({oct(mode)}): {path}. Refused rather than corrected — a secret "
            "that was briefly readable may already have been read. Generate a "
            "new one."
        )
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        raise BridgeConfigError(f"the {what} file could not be read: {path}")
    if not value:
        raise BridgeConfigError(f"the {what} file is empty: {path}")
    return value


def generate_external_key(config: BridgeConfig, *, force: bool = False) -> Path:
    """Write a new external key, 0600, and return its path. Never prints it.

    Refuses to overwrite unless asked, because rotating the key while a Custom
    GPT holds the old one breaks the GPT until somebody re-enters it — that
    should be a decision, not the result of running a command twice.
    """
    import secrets as _secrets

    path = config.external_key_path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(stat.S_IRWXU)
    except OSError:  # pragma: no cover - platform dependent
        pass
    if path.exists() and not force:
        raise BridgeConfigError(
            f"an external key already exists at {path}. Pass --force to replace "
            "it, and expect to re-enter the new value in the GPT editor."
        )
    # Written through a fresh descriptor opened 0600, rather than written and
    # then chmod-ed: between those two calls the file exists with the umask's
    # mode, and that window is exactly what the startup check refuses.
    handle = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(handle, (_secrets.token_urlsafe(KEY_BYTES) + "\n").encode("utf-8"))
    finally:
        os.close(handle)
    return path


__all__ = [
    "BridgeConfig",
    "BridgeConfigError",
    "DEFAULT_BIND_HOST",
    "DEFAULT_BIND_PORT",
    "DEFAULT_INTERNAL_BASE_URL",
    "generate_external_key",
    "load_bridge_config",
    "read_secret_file",
]
