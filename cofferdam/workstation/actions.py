"""Typed actions: schemas, registry, and the executor.

**Callers submit typed actions, never commands.** Every schema forbids unknown
fields, the action name must be a registered key, and no field anywhere accepts
a command, argument vector, executable path, or shell string. Adding a
capability means adding a schema plus an adapter method — never a passthrough.

Each execution produces an :class:`ActionRecord` with a service-generated
``action_id``, ISO-8601 UTC ``started_at``/``finished_at``, a terminal
``status``, and either a ``result`` or a bounded structured ``error``.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Type

from pydantic import BaseModel, ConfigDict, field_validator

from .adapters.base import APPLICATION_KEYS, BROWSER_KEYS, HostAdapter
from .errors import (
    CODE_INVALID_PARAMS,
    CODE_UNKNOWN_ACTION,
    ApiError,
    AdapterError,
    MediaQueryInvalid,
    bounded_detail,
)
from .media import PROVIDER_IDS
from .registries import is_valid_id as is_valid_registry_id

ACTION_TAKE_SCREENSHOT = "take_screenshot"
ACTION_OPEN_APPLICATION = "open_application"
ACTION_OPEN_URL = "open_url"
# M2B3A
ACTION_OPEN_MEDIA_PROVIDER = "open_media_provider"
ACTION_SEARCH_MEDIA_PROVIDER = "search_media_provider"

STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

MAX_URL_LENGTH = 2048
ALLOWED_URL_SCHEMES = ("http", "https")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# parameter schemas
# ---------------------------------------------------------------------------


class _Params(BaseModel):
    # extra="forbid" is what makes "no arbitrary shell" structural: a field like
    # {"command": "rm -rf /"} is rejected before any adapter is reached.
    model_config = ConfigDict(extra="forbid")


class TakeScreenshotParams(_Params):
    """No parameters in M1 (per-display targeting is M2)."""


class OpenApplicationParams(_Params):
    application: str

    @field_validator("application")
    @classmethod
    def _allowlisted(cls, value: str) -> str:
        # Exact match only — no stripping, no case folding. The UI sends keys
        # verbatim from /api/status, so anything else is a malformed or hostile
        # request and fails closed rather than being normalized into a match.
        if value not in APPLICATION_KEYS:
            raise ValueError(f"application must be one of: {', '.join(APPLICATION_KEYS)}")
        return value


class OpenUrlParams(_Params):
    url: str
    # M2A. Optional and additive: a request carrying only ``url`` keeps the
    # pre-M2A behaviour exactly. This selects a *semantic profile* by its stable
    # registry id — never a browser, a binary, or a profile directory.
    browser_profile_id: Optional[str] = None
    # M2B3A. Selects a browser directly, from the code-owned browser allowlist,
    # for a machine that has configured no profiles. Still a logical key: the
    # adapter owns which program that means. Mutually exclusive with
    # ``browser_profile_id``, which ``select_browser`` enforces.
    browser_id: Optional[str] = None

    @field_validator("browser_id")
    @classmethod
    def _known_browser(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value not in BROWSER_KEYS:
            raise ValueError(f"browser_id must be one of: {', '.join(BROWSER_KEYS)}")
        return value

    @field_validator("browser_profile_id")
    @classmethod
    def _known_shape(cls, value: Optional[str]) -> Optional[str]:
        # Shape only; existence is checked against the registry at execution
        # time, where a missing or disabled profile fails closed.
        if value is None:
            return None
        if not is_valid_registry_id(value):
            raise ValueError("browser_profile_id must be a registry id (lowercase kebab-case)")
        return value

    @field_validator("url")
    @classmethod
    def _safe_url(cls, value: str) -> str:
        from urllib.parse import urlparse

        candidate = value.strip()
        if not candidate:
            raise ValueError("url must not be empty")
        if len(candidate) > MAX_URL_LENGTH:
            raise ValueError(f"url must be at most {MAX_URL_LENGTH} characters")
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in candidate):
            raise ValueError("url must not contain control characters")
        parsed = urlparse(candidate)
        if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
            raise ValueError("url scheme must be http or https")
        if not parsed.netloc:
            raise ValueError("url must include a host")
        return candidate


def _allowlisted_provider_id(value: str) -> str:
    """Exact match against the catalogue, like ``open_application``.

    Shared by both media schemas as a plain function rather than through a
    common base class: every schema in :data:`PARAM_SCHEMAS` inherits
    ``_Params`` *directly*, which is what
    ``tests/test_workstation_no_shell.py`` checks by parsing this file. A
    schema hierarchy would still forbid extras, but it would make that
    structural check depend on following inheritance — so the shared thing here
    is the rule, not the base class.
    """
    if value not in PROVIDER_IDS:
        raise ValueError(f"provider_id must be one of: {', '.join(PROVIDER_IDS)}")
    return value


class OpenMediaProviderParams(_Params):
    """Open a media provider's application or home page.

    One field, and it is an id from a code-owned allowlist. There is no URL
    here, and deliberately no way to add one: where a provider opens is a
    constant in :mod:`~cofferdam.workstation.media`, not something a caller
    contributes to.
    """

    provider_id: str

    @field_validator("provider_id")
    @classmethod
    def _allowlisted(cls, value: str) -> str:
        return _allowlisted_provider_id(value)


class SearchMediaProviderParams(_Params):
    """Open a provider's own search results for a bounded phrase.

    ``query`` is plain human text and nothing else. It is length-bounded and
    control-character-free here, then percent-encoded by the media catalogue
    into a route the catalogue owns — so it can never introduce a parameter, a
    path segment, a fragment, or a second URL.
    """

    provider_id: str
    query: str

    @field_validator("provider_id")
    @classmethod
    def _allowlisted(cls, value: str) -> str:
        return _allowlisted_provider_id(value)

    @field_validator("query")
    @classmethod
    def _bounded_text(cls, value: str) -> str:
        # The catalogue owns the rule so the schema and the launch path cannot
        # drift apart; re-stating it here would give two answers to one question.
        from .media import validate_query

        try:
            return validate_query(value)
        except MediaQueryInvalid as exc:
            raise ValueError(exc.message) from exc


PARAM_SCHEMAS: Dict[str, Type[_Params]] = {
    ACTION_TAKE_SCREENSHOT: TakeScreenshotParams,
    ACTION_OPEN_APPLICATION: OpenApplicationParams,
    ACTION_OPEN_URL: OpenUrlParams,
    ACTION_OPEN_MEDIA_PROVIDER: OpenMediaProviderParams,
    ACTION_SEARCH_MEDIA_PROVIDER: SearchMediaProviderParams,
}

ACTION_NAMES = tuple(PARAM_SCHEMAS)


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    params: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------


@dataclass
class ActionRecord:
    action_id: str
    action: str
    status: str
    started_at: str
    params: Dict[str, Any] = field(default_factory=dict)
    finished_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    stub: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# executor
# ---------------------------------------------------------------------------


def validate_action(action: str, raw_params: Dict[str, Any]) -> _Params:
    """Resolve and validate an action + params, or raise :class:`ApiError`."""
    schema = PARAM_SCHEMAS.get(action)
    if schema is None:
        raise ApiError(
            code=CODE_UNKNOWN_ACTION,
            message=f"unknown action: {action}",
            status_code=400,
            detail=f"known actions: {', '.join(ACTION_NAMES)}",
        )
    try:
        return schema(**(raw_params or {}))
    except Exception as exc:  # pydantic ValidationError and anything it raises
        raise ApiError(
            code=CODE_INVALID_PARAMS,
            message=f"invalid parameters for {action}",
            status_code=422,
            detail=_first_validation_message(exc),
        ) from exc


def _first_validation_message(exc: Exception) -> Optional[str]:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            items = errors()
        except Exception:  # pragma: no cover
            items = []
        if items:
            first = items[0]
            location = ".".join(str(part) for part in first.get("loc", ()))
            message = first.get("msg", "invalid value")
            return bounded_detail(f"{location}: {message}" if location else message)
    return bounded_detail(exc)


class ActionExecutor:
    """Runs typed actions through the host adapter and records the outcome.

    Adapter calls are synchronous; the service runs :meth:`execute` in a worker
    thread. ``on_event`` receives ``(event_name, record)`` for live broadcast.
    """

    def __init__(
        self,
        adapter: HostAdapter,
        store,
        config,
        on_event: Optional[Callable[[str, ActionRecord], None]] = None,
    ) -> None:
        self._adapter = adapter
        self._store = store
        self._config = config
        self._on_event = on_event

    def _emit(self, event: str, record: ActionRecord) -> None:
        if self._on_event is not None:
            self._on_event(event, record)

    def execute(self, action: str, params: _Params) -> ActionRecord:
        record = ActionRecord(
            action_id=uuid.uuid4().hex,
            action=action,
            status=STATUS_RUNNING,
            started_at=_utc_now(),
            params=params.model_dump(),
            stub=bool(getattr(self._adapter, "stub", False)),
        )
        self._store.add(record.to_dict())
        self._emit("action_started", record)

        try:
            record.result = self._dispatch(action, params, record.action_id)
            record.status = STATUS_SUCCEEDED
        except AdapterError as exc:
            record.status = STATUS_FAILED
            record.error = {"code": exc.code, "message": exc.message, "detail": exc.detail}
        except Exception as exc:  # never leak an unbounded traceback to a client
            record.status = STATUS_FAILED
            record.error = {
                "code": "internal_error",
                "message": "action failed unexpectedly",
                "detail": bounded_detail(type(exc).__name__),
            }

        record.finished_at = _utc_now()
        self._store.update(record.action_id, record.to_dict())
        self._emit("action_finished", record)
        return record

    def _dispatch(self, action: str, params: _Params, action_id: str) -> Dict[str, Any]:
        if action == ACTION_TAKE_SCREENSHOT:
            from .store import prune_screenshots, screenshot_path

            shot = self._adapter.take_screenshot()
            path = screenshot_path(self._config, action_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(shot.png_bytes)
            prune_screenshots(self._config)
            return {
                "screenshot_url": f"/api/screenshots/{action_id}",
                "bytes": len(shot.png_bytes),
                "tool": shot.tool,
                "width": shot.width,
                "height": shot.height,
            }

        if action == ACTION_OPEN_APPLICATION:
            launch = self._adapter.open_application(params.application)  # type: ignore[attr-defined]
            return {"application": launch.application, "pid": launch.pid, "detail": launch.detail}

        if action == ACTION_OPEN_URL:
            return self._open_url(
                params.url,  # type: ignore[attr-defined]
                browser_profile_id=params.browser_profile_id,  # type: ignore[attr-defined]
                browser_id=params.browser_id,  # type: ignore[attr-defined]
            )

        if action in (ACTION_OPEN_MEDIA_PROVIDER, ACTION_SEARCH_MEDIA_PROVIDER):
            return self._media(action, params)

        raise ApiError(code=CODE_UNKNOWN_ACTION, message=f"unknown action: {action}", status_code=400)

    def _open_url(
        self,
        url: str,
        *,
        browser_profile_id: Optional[str] = None,
        browser_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from .browser_selection import select_browser

        # Browser resolution and domain policy are settled *before* any launch,
        # so a refused URL never reaches a browser at all.
        choice = select_browser(
            self._config,
            url,
            browser_profile_id,
            self._adapter.available_applications(),
            browser_id=browser_id,
        )
        launch = self._adapter.open_url(url, application=choice.application_key)
        result: Dict[str, Any] = {
            "url": url,
            "application": launch.application,
            "pid": launch.pid,
            "detail": launch.detail,
        }
        result.update(choice.to_result())
        return result

    # -- media providers (M2B3A) --------------------------------------------

    def _media(self, action: str, params: _Params) -> Dict[str, Any]:
        """Open or search one allowlisted media provider.

        The whole target is built here from the catalogue: the provider is
        resolved from the allowlist, and for a search the phrase is encoded by
        the catalogue's own builder. No part of the destination comes from the
        request beyond the id and the words themselves.

        Every result says :data:`~cofferdam.workstation.media.PLAYBACK_NOT_STARTED`
        because that is what happened. A launch that was accepted and confirmed
        is the entire claim; nothing in this build starts, selects, or controls
        playback, and a result that merely said "succeeded" would be read as
        though something were playing.
        """
        from .media import (
            KIND_NATIVE_APP,
            MEDIA_ACTION_OPEN,
            MEDIA_ACTION_SEARCH,
            PLAYBACK_NOT_STARTED,
            TARGET_APPLICATION_URI,
            get_provider,
        )

        provider = get_provider(params.provider_id)  # type: ignore[attr-defined]
        searching = action == ACTION_SEARCH_MEDIA_PROVIDER

        result: Dict[str, Any] = {
            "provider_id": provider.id,
            "provider_name": provider.name,
            "provider_kind": provider.kind,
            "media_action": MEDIA_ACTION_SEARCH if searching else MEDIA_ACTION_OPEN,
            # Said on every media result, success included.
            "playback": PLAYBACK_NOT_STARTED,
            "playback_started": False,
        }

        if searching:
            # Raises MediaSearchUnsupported for a provider with no route we can
            # build — before anything opens, so an unsupported search never
            # becomes "we opened the home page and called it a search".
            target = provider.search_target(params.query)  # type: ignore[attr-defined]
            result["query"] = params.query  # type: ignore[attr-defined]
            result["note"] = (
                f"Opened {provider.name}'s search results for your words. "
                "Nothing was selected and nothing is playing."
            )
            if target.kind == TARGET_APPLICATION_URI:
                launch = self._adapter.open_application_uri(
                    target.application_key, target.value
                )
                result.update(
                    {
                        "application": launch.application,
                        "pid": launch.pid,
                        "detail": launch.detail,
                        "opened_in": "application",
                    }
                )
                return result
            result.update(self._open_url(target.value, browser_id=provider.browser_key))
            result["opened_in"] = "browser"
            return result

        if provider.kind == KIND_NATIVE_APP:
            launch = self._adapter.open_application(provider.application_key)
            result.update(
                {
                    "application": launch.application,
                    "pid": launch.pid,
                    "detail": launch.detail,
                    "opened_in": "application",
                    "note": f"Started the installed {provider.name} application.",
                }
            )
            return result

        target = provider.open_target()
        result.update(self._open_url(target.value, browser_id=provider.browser_key))
        result["opened_in"] = "browser"
        result["note"] = (
            f"Opened {provider.name}. You may need to sign in, and nothing is playing yet."
        )
        return result
