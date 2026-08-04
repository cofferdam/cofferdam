"""Runtime configuration, workspace layout, and the device token.

Layout under ``COFFERDAM_HOME`` (default ``~/cofferdam``) — mirrors the split in
``DESIGN.md`` so the Guardian/A-B milestones can slot in without moving data::

    <home>/config.json            optional overrides (no secrets)
    <home>/config/registries/     versioned semantic registries (M2A, no secrets)
    <home>/secrets/token          the device token (0600, never in git)
    <home>/state/actions.json     bounded recent-action records
    <home>/screenshots/<id>.png   bounded screenshot artifacts
    <home>/logs/                  runtime logs

``config.json`` holds *runtime* knobs (bind address, limits). ``config/registries/``
holds *semantic machine configuration* — which devices, displays, applications,
browser profiles, agent profiles and route templates this machine knows about.
Those are validated JSON documents rather than environment variables because
they carry structure, stable IDs, and cross-references that an env var cannot
express or validate. They are machine-specific and never live in the Git
repository; committed placeholders live under ``examples/registries/``.

Precedence: environment variable > ``config.json`` > built-in default.

**The token is never written to source control, never logged, and never
returned by any endpoint.** It is generated on first start if absent.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 7101
DEFAULT_ADAPTER = "auto"
DEFAULT_MAX_ACTION_RECORDS = 50
DEFAULT_MAX_SCREENSHOTS = 20
TOKEN_BYTES = 32

ENV_HOME = "COFFERDAM_HOME"
ENV_BIND_HOST = "COFFERDAM_BIND_HOST"
ENV_BIND_PORT = "COFFERDAM_BIND_PORT"
ENV_ADAPTER = "COFFERDAM_ADAPTER"
ENV_TOKEN = "COFFERDAM_TOKEN"


@dataclass(frozen=True)
class Config:
    home: Path
    bind_host: str
    bind_port: int
    adapter_name: str
    max_action_records: int
    max_screenshots: int

    @property
    def secrets_dir(self) -> Path:
        return self.home / "secrets"

    @property
    def config_dir(self) -> Path:
        return self.home / "config"

    @property
    def registries_dir(self) -> Path:
        return self.config_dir / "registries"

    def registry_path(self, registry_name: str) -> Path:
        """Path of one registry file.

        ``registry_name`` is only ever one of the code-owned names in
        :data:`cofferdam.workstation.registries.REGISTRY_NAMES`; callers resolve
        it against that tuple before asking, so no request text becomes a path
        component.
        """
        return self.registries_dir / f"{registry_name}.json"

    @property
    def state_dir(self) -> Path:
        return self.home / "state"

    @property
    def screenshots_dir(self) -> Path:
        return self.home / "screenshots"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def token_path(self) -> Path:
        return self.secrets_dir / "token"

    @property
    def actions_path(self) -> Path:
        return self.state_dir / "actions.json"

    def ensure_dirs(self) -> None:
        for path in (
            self.home,
            self.secrets_dir,
            self.state_dir,
            self.screenshots_dir,
            self.logs_dir,
            self.registries_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        _restrict(self.secrets_dir, directory=True)


def _restrict(path: Path, directory: bool = False) -> None:
    """Best-effort owner-only permissions (POSIX; a no-op-ish on Windows)."""
    try:
        path.chmod(stat.S_IRWXU if directory else (stat.S_IRUSR | stat.S_IWUSR))
    except OSError:  # pragma: no cover - platform dependent
        pass


def _file_overrides(home: Path) -> dict:
    config_path = home / "config.json"
    try:
        raw = config_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _pick(env_name: str, file_values: dict, key: str, default: Any) -> Any:
    env_value = os.environ.get(env_name)
    if env_value is not None and env_value != "":
        return env_value
    if key in file_values:
        return file_values[key]
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def load_config(home: Optional[Path] = None) -> Config:
    """Build the effective configuration. Does not create directories."""
    if home is None:
        home_value = os.environ.get(ENV_HOME)
        home = Path(home_value) if home_value else Path.home() / "cofferdam"
    home = Path(home).expanduser()
    overrides = _file_overrides(home)
    return Config(
        home=home,
        bind_host=str(_pick(ENV_BIND_HOST, overrides, "bind_host", DEFAULT_BIND_HOST)),
        bind_port=_as_int(_pick(ENV_BIND_PORT, overrides, "bind_port", DEFAULT_BIND_PORT), DEFAULT_BIND_PORT),
        adapter_name=str(_pick(ENV_ADAPTER, overrides, "adapter", DEFAULT_ADAPTER)),
        max_action_records=_as_int(overrides.get("max_action_records"), DEFAULT_MAX_ACTION_RECORDS),
        max_screenshots=_as_int(overrides.get("max_screenshots"), DEFAULT_MAX_SCREENSHOTS),
    )


def load_or_create_token(config: Config) -> str:
    """Return the device token, generating and persisting one on first run.

    Precedence: ``COFFERDAM_TOKEN`` (useful for systemd ``EnvironmentFile=``)
    then ``<home>/secrets/token``. A generated token is written 0600 and
    announced on **stderr only** — it is never logged during normal operation
    and never leaves through the API.
    """
    env_token = os.environ.get(ENV_TOKEN)
    if env_token:
        return env_token.strip()

    config.ensure_dirs()
    try:
        existing = config.token_path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        return existing

    token = secrets.token_urlsafe(TOKEN_BYTES)
    config.token_path.write_text(token + "\n", encoding="utf-8")
    _restrict(config.token_path)
    return token
