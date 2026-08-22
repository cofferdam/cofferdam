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
ENV_VALIDATION_TASK_ADAPTER = "COFFERDAM_ENABLE_VALIDATION_TASK_ADAPTER"
ENV_CLAUDE_CODE_ADAPTER = "COFFERDAM_ENABLE_CLAUDE_CODE_ADAPTER"
ENV_CLAUDE_AGENT_SDK_ADAPTER = "COFFERDAM_ENABLE_CLAUDE_AGENT_SDK_ADAPTER"
ENV_ACTIONS_BRIDGE_CALLER = "COFFERDAM_ENABLE_ACTIONS_BRIDGE_CALLER"
ENV_DEVELOPMENT_PLANNER = "COFFERDAM_ENABLE_DEVELOPMENT_PLANNER"

#: Off, and it stays off unless somebody with access to this machine turns it
#: on. The validation task adapter is a lifecycle exerciser for a validation
#: runtime; a default-on test adapter would be a surface nobody asked for on
#: every install. See ``docs/AGENT_TASK_CORE.md``.
DEFAULT_ENABLE_VALIDATION_TASK_ADAPTER = False

#: Off, and this is the most consequential default in the file. The Claude
#: Code adapter launches a real process that reads and edits real files in an
#: approved project. It is enabled by a deliberate host-owned decision — this
#: flag, a config key, or an environment variable in the unit — and by nothing
#: a client can send. See ``docs/CLAUDE_CODE_ADAPTER.md``.
DEFAULT_ENABLE_CLAUDE_CODE_ADAPTER = False

#: Off, and a **separate** switch from the one above rather than a replacement
#: for it. The Agent SDK adapter is a second transport to the same agent, and
#: turning it on must not silently retire the one that was validated live from a
#: phone against this host. A workstation may run neither, either, or both; a
#: project still has to list an adapter before a task may use it. See
#: ``docs/CLAUDE_AGENT_SDK_ADAPTER.md``.
DEFAULT_ENABLE_CLAUDE_AGENT_SDK_ADAPTER = False

#: Off, and it is a **caller** switch rather than an adapter one — the only one
#: in this file. It does not register anything, run anything or open a port. All
#: it does is decide whether a *second* internal credential exists at all, and
#: therefore whether the M2I.5 Actions bridge has anything to authenticate with.
#:
#: Off means the file is never generated, `load_actions_bridge_token` returns
#: ``None``, and the daemon knows exactly one credential — which is the state
#: every existing deployment is in and stays in until somebody changes it here.
#: See ``docs/ACTIONS_BRIDGE.md``.
DEFAULT_ENABLE_ACTIONS_BRIDGE_CALLER = False

#: Off, and off for a reason none of the others have: this one spends money.
#:
#: The development planner invokes a cloud model on the operator's own
#: subscription. Every other switch in this file decides whether something can
#: run locally; this one decides whether a remote caller can cause a billable
#: call. So it is a deliberate host-owned decision — this flag, a config key,
#: or an environment variable in the unit — and nothing a client can send.
#:
#: Off means the development request route refuses, no planner database is
#: created, and the operations read surface answers exactly as it does today.
#: See ``docs/M2L_CLOUD_PLANNER.md``.
DEFAULT_ENABLE_DEVELOPMENT_PLANNER = False


@dataclass(frozen=True)
class Config:
    home: Path
    bind_host: str
    bind_port: int
    adapter_name: str
    max_action_records: int
    max_screenshots: int
    #: Server-side only. There is no route, header or request body that reaches
    #: this field: it is set by a command-line flag, by ``config.json`` on the
    #: host, or by an environment variable in the unit file.
    enable_validation_task_adapter: bool = DEFAULT_ENABLE_VALIDATION_TASK_ADAPTER
    #: Server-side only, exactly like the field above and for stronger
    #: reasons. No route, header or request body reaches this.
    enable_claude_code_adapter: bool = DEFAULT_ENABLE_CLAUDE_CODE_ADAPTER
    #: Server-side only. Independent of the field above: neither implies the
    #: other, and neither turns the other off.
    enable_claude_agent_sdk_adapter: bool = DEFAULT_ENABLE_CLAUDE_AGENT_SDK_ADAPTER
    #: Server-side only, and the narrowest of the four. It grants no adapter and
    #: no route: it decides whether a second internal credential exists, which a
    #: bounded set of task routes may then recognise as the Actions bridge.
    enable_actions_bridge_caller: bool = DEFAULT_ENABLE_ACTIONS_BRIDGE_CALLER
    #: Server-side only, and the only switch here that authorises spending.
    #: It grants permission to *plan*: no worker, no dispatch, no approval.
    enable_development_planner: bool = DEFAULT_ENABLE_DEVELOPMENT_PLANNER

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
    def actions_bridge_token_path(self) -> Path:
        """The **second** internal credential, for the M2I.5 Actions bridge.

        A separate file rather than a scope claim inside the first one, because
        the property that matters is revocability: deleting this file removes
        the bridge's access to the daemon and leaves the phone's device token
        working. One file holding both would make that a parsing question.

        This is *not* the key the Custom GPT holds. That one lives beside the
        bridge process and never appears here — see
        ``cofferdam/actions_bridge/config.py``.
        """
        return self.secrets_dir / "actions-bridge-internal-token"

    @property
    def actions_path(self) -> Path:
        return self.state_dir / "actions.json"

    @property
    def tasks_dir(self) -> Path:
        """Where the durable task database lives.

        Its own directory under ``state/`` rather than a bare file, so the
        database and its WAL/shm siblings stay together and can be permissioned
        as a unit. It holds task content — prompts, follow-ups, results — and no
        secrets; see ``docs/AGENT_TASK_CORE.md`` for the privacy treatment.
        """
        return self.state_dir / "tasks"

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


def _as_bool(value: Any, default: bool) -> bool:
    """Strict truthiness for a switch that turns on a validation surface.

    Only the obvious affirmatives count. Anything else — including an empty
    string, a typo, or a JSON ``null`` — leaves the switch off, because the
    direction a misreading should fall is the one where less exists.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


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
        enable_validation_task_adapter=_as_bool(
            _pick(
                ENV_VALIDATION_TASK_ADAPTER,
                overrides,
                "enable_validation_task_adapter",
                DEFAULT_ENABLE_VALIDATION_TASK_ADAPTER,
            ),
            DEFAULT_ENABLE_VALIDATION_TASK_ADAPTER,
        ),
        enable_claude_code_adapter=_as_bool(
            _pick(
                ENV_CLAUDE_CODE_ADAPTER,
                overrides,
                "enable_claude_code_adapter",
                DEFAULT_ENABLE_CLAUDE_CODE_ADAPTER,
            ),
            DEFAULT_ENABLE_CLAUDE_CODE_ADAPTER,
        ),
        enable_claude_agent_sdk_adapter=_as_bool(
            _pick(
                ENV_CLAUDE_AGENT_SDK_ADAPTER,
                overrides,
                "enable_claude_agent_sdk_adapter",
                DEFAULT_ENABLE_CLAUDE_AGENT_SDK_ADAPTER,
            ),
            DEFAULT_ENABLE_CLAUDE_AGENT_SDK_ADAPTER,
        ),
        enable_actions_bridge_caller=_as_bool(
            _pick(
                ENV_ACTIONS_BRIDGE_CALLER,
                overrides,
                "enable_actions_bridge_caller",
                DEFAULT_ENABLE_ACTIONS_BRIDGE_CALLER,
            ),
            DEFAULT_ENABLE_ACTIONS_BRIDGE_CALLER,
        ),
        enable_development_planner=_as_bool(
            _pick(
                ENV_DEVELOPMENT_PLANNER,
                overrides,
                "enable_development_planner",
                DEFAULT_ENABLE_DEVELOPMENT_PLANNER,
            ),
            DEFAULT_ENABLE_DEVELOPMENT_PLANNER,
        ),
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


def load_or_create_actions_bridge_token(config: Config) -> Optional[str]:
    """The Actions bridge's internal credential, or ``None`` when it is off.

    Three differences from :func:`load_or_create_token`, each deliberate.

    **There is no environment override.** The device token has one because a
    systemd ``EnvironmentFile=`` is a documented way to supply it. This
    credential is read by two processes on the same machine from the same 0600
    file, and an env var would put a second copy of it in a place that is
    visible in ``/proc`` and easy to inherit into a child.

    **It is generated only when the caller is enabled**, so a deployment that
    has not turned this on has no such file — which is a stronger statement than
    a file nobody uses.

    **It is never announced**, not even on stderr. Nobody has to copy this one
    anywhere by hand: the bridge reads the same path.
    """
    if not config.enable_actions_bridge_caller:
        return None

    config.ensure_dirs()
    path = config.actions_bridge_token_path
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        return existing

    token = secrets.token_urlsafe(TOKEN_BYTES)
    path.write_text(token + "\n", encoding="utf-8")
    _restrict(path)
    return token
