"""The Cofferdam workstation FastAPI application.

Surface:

===========================================  ====  =============================
route                                        auth  purpose
===========================================  ====  =============================
``GET  /healthz``                            no    liveness (no host data)
``GET  /api/status``                         yes   host + service status
``GET  /api/actions``                        yes   recent action records
``POST /api/actions``                        yes   run a typed action
``POST /api/actions/screenshot``             yes   convenience: take_screenshot
``POST /api/actions/open-application``       yes   convenience: open_application
``POST /api/actions/open-url``               yes   convenience: open_url
``GET  /api/screenshots/{action_id}``        yes   PNG artifact
``WS   /ws``                                 yes   live events
``GET  /`` and static assets                 no    the PWA shell itself
===========================================  ====  =============================

``/healthz`` is intentionally unauthenticated and intentionally empty of host
detail: it exists so systemd and (later) Guardian can probe liveness without a
token. The PWA shell is public because the token is entered *into* it; every
route that reveals or changes host state requires the token.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets as _secrets
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import WORKSTATION_API_VERSION
from .actions import (
    ACTION_OPEN_APPLICATION,
    ACTION_OPEN_URL,
    ACTION_TAKE_SCREENSHOT,
    ACTION_NAMES,
    ActionExecutor,
    ActionRecord,
    ActionRequest,
    validate_action,
)
from .adapters import select_adapter
from .config import Config, load_config, load_or_create_token
from .errors import (
    CODE_INTERNAL,
    CODE_NOT_FOUND,
    CODE_UNAUTHORIZED,
    ApiError,
)
from .events import STATUS_REFRESH_SECONDS, TOKEN_SUBPROTOCOL, EventHub
from .store import ActionStore, screenshot_path

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"


def _error_response(error: ApiError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content=error.to_payload())


def create_app(
    config: Optional[Config] = None,
    token: Optional[str] = None,
    adapter=None,
) -> FastAPI:
    """Build the application. Arguments are injectable for tests."""
    config = config or load_config()
    config.ensure_dirs()
    token = token or load_or_create_token(config)
    adapter = adapter or select_adapter(config.adapter_name, config)

    store = ActionStore(config)
    hub = EventHub()

    def _on_event(event: str, record: ActionRecord) -> None:
        hub.broadcast_threadsafe(event, record.to_dict())

    executor = ActionExecutor(adapter, store, config, on_event=_on_event)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        hub.bind_loop(asyncio.get_running_loop())
        task = asyncio.create_task(_status_loop())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="Cofferdam workstation",
        version=WORKSTATION_API_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.adapter = adapter
    app.state.store = store
    app.state.hub = hub
    app.state.executor = executor

    # -- auth ----------------------------------------------------------------

    def _token_matches(candidate: Optional[str]) -> bool:
        return bool(candidate) and _secrets.compare_digest(candidate, token)

    async def require_token(request: Request) -> None:
        header = request.headers.get("authorization", "")
        candidate = header[7:].strip() if header.lower().startswith("bearer ") else None
        if not _token_matches(candidate):
            raise ApiError(
                code=CODE_UNAUTHORIZED,
                message="a valid device token is required",
                status_code=401,
            )

    @app.exception_handler(ApiError)
    async def _api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc)

    @app.exception_handler(Exception)
    async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        return _error_response(
            ApiError(
                code=CODE_INTERNAL,
                message="internal error",
                status_code=500,
                detail=type(exc).__name__,
            )
        )

    async def _status_loop() -> None:
        while True:
            try:
                await asyncio.sleep(STATUS_REFRESH_SECONDS)
                if hub.client_count:
                    await hub.broadcast("status", await _status_payload())
            except asyncio.CancelledError:  # pragma: no cover - shutdown path
                return
            except Exception:  # a status hiccup must never kill the loop
                continue

    async def _status_payload() -> Dict[str, Any]:
        host = await run_in_threadpool(adapter.host_status)
        return {
            "service": {
                "api_version": WORKSTATION_API_VERSION,
                "milestone": "M1",
                "actions": list(ACTION_NAMES),
                "event_clients": hub.client_count,
            },
            "host": host.to_dict(),
            "applications": await run_in_threadpool(adapter.available_applications),
        }

    # -- routes --------------------------------------------------------------

    @app.get("/healthz")
    async def healthz() -> Dict[str, Any]:
        return {"status": "ok", "api_version": WORKSTATION_API_VERSION}

    @app.get("/api/status", dependencies=[Depends(require_token)])
    async def api_status() -> Dict[str, Any]:
        return await _status_payload()

    @app.get("/api/actions", dependencies=[Depends(require_token)])
    async def list_actions() -> Dict[str, Any]:
        return {"actions": store.recent()}

    async def _run(action: str, raw_params: Dict[str, Any]) -> JSONResponse:
        params = validate_action(action, raw_params)
        record = await run_in_threadpool(executor.execute, action, params)
        status_code = 200 if record.status == "succeeded" else 502
        return JSONResponse(status_code=status_code, content=record.to_dict())

    @app.post("/api/actions", dependencies=[Depends(require_token)])
    async def run_action(request: ActionRequest) -> JSONResponse:
        return await _run(request.action, request.params)

    @app.post("/api/actions/screenshot", dependencies=[Depends(require_token)])
    async def run_screenshot() -> JSONResponse:
        return await _run(ACTION_TAKE_SCREENSHOT, {})

    @app.post("/api/actions/open-application", dependencies=[Depends(require_token)])
    async def run_open_application(params: Dict[str, Any]) -> JSONResponse:
        return await _run(ACTION_OPEN_APPLICATION, params)

    @app.post("/api/actions/open-url", dependencies=[Depends(require_token)])
    async def run_open_url(params: Dict[str, Any]) -> JSONResponse:
        return await _run(ACTION_OPEN_URL, params)

    @app.get("/api/screenshots/{action_id}", dependencies=[Depends(require_token)])
    async def get_screenshot(action_id: str) -> Response:
        # action_id comes from our own records; reject anything not hex so a
        # path component can never traverse out of the screenshots directory.
        if not action_id or len(action_id) > 64 or not all(c in "0123456789abcdef" for c in action_id):
            raise ApiError(code=CODE_NOT_FOUND, message="screenshot not found", status_code=404)
        path = screenshot_path(config, action_id)
        if not path.is_file():
            raise ApiError(code=CODE_NOT_FOUND, message="screenshot not found", status_code=404)
        return FileResponse(path, media_type="image/png")

    # -- live events ---------------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        offered = list(websocket.scope.get("subprotocols") or [])
        candidate: Optional[str] = None
        accept_subprotocol: Optional[str] = None
        if TOKEN_SUBPROTOCOL in offered:
            index = offered.index(TOKEN_SUBPROTOCOL)
            if index + 1 < len(offered):
                candidate = offered[index + 1]
                accept_subprotocol = TOKEN_SUBPROTOCOL
        if candidate is None:
            candidate = websocket.query_params.get("token")

        if not _token_matches(candidate):
            # Close before accepting: an unauthenticated socket is never upgraded.
            await websocket.close(code=4401)
            return

        await websocket.accept(subprotocol=accept_subprotocol)
        await hub.register(websocket)
        try:
            await websocket.send_json(
                {
                    "event": "hello",
                    "data": {
                        "api_version": WORKSTATION_API_VERSION,
                        "adapter": {"name": adapter.name, "stub": bool(adapter.stub)},
                        "recent_actions": store.recent(10),
                    },
                }
            )
            while True:
                message = await websocket.receive_text()
                if message == "ping":
                    await websocket.send_json({"event": "heartbeat", "data": {}})
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            await hub.unregister(websocket)

    # -- the PWA -------------------------------------------------------------

    if WEB_ROOT.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_ROOT), html=True), name="web")

    return app
