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
``GET  /api/media/providers``                yes   media catalogue + availability
``GET  /api/media/diagnostics``              yes   provider credential *status words*
``POST /api/actions/open-media-provider``    yes   convenience: open_media_provider
``POST /api/actions/search-media-provider``  yes   convenience: search_media_provider
``POST /api/media/providers/{id}/results/search``          yes  official catalogue search
``POST /api/media/searches/{sid}/results/{rid}/open``      yes  open a resolved result
``GET  /api/screenshots/{action_id}``        yes   PNG artifact
``GET  /api/registries``                     yes   registry load/validation status
``GET  /api/registries/{registry_name}``     yes   one validated registry
``GET  /api/runtime``                        yes   live runtime snapshot
``GET  /api/runtime/{resource_kind}``        yes   one slice of that snapshot
``WS   /ws``                                 yes   live events
``GET  /`` and static assets                 no    the PWA shell itself
===========================================  ====  =============================

``/healthz`` is intentionally unauthenticated and intentionally empty of host
detail: it exists so systemd and (later) Guardian can probe liveness without a
token. The PWA shell is public because the token is entered *into* it; every
route that reveals or changes host state requires the token — the registries
included, since they describe the user's machine and household.

**The registry routes are read-only, and that is a deliberate boundary rather
than an unfinished feature.** M2A ships no ``POST``/``PUT``/``PATCH``/
``DELETE`` for registries: nothing reachable over the network can create or
change the configuration that decides which applications exist and which
domains a browser profile may open. Editing is a text editor and a service
that re-reads the file. Bringing write access inside the API is its own
milestone, with its own review.

**The runtime routes (M2B) are read-only for a second, separate reason.** They
report what the machine currently *is* — connected displays, running processes,
application instances — and observing is the whole contract. Nothing under
``/api/runtime`` starts, stops, moves, reconfigures, or terminates anything;
process and window control is a later milestone with its own identity
re-verification rules. They are also fully authenticated: an inventory of a
person's machine is exactly the kind of thing an unauthenticated endpoint must
never hand out.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
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
    ACTION_FIND_MEDIA_RESULTS,
    ACTION_OPEN_APPLICATION,
    ACTION_OPEN_MEDIA_PROVIDER,
    ACTION_OPEN_MEDIA_RESULT,
    ACTION_OPEN_URL,
    ACTION_SEARCH_MEDIA_PROVIDER,
    ACTION_TAKE_SCREENSHOT,
    ACTION_NAMES,
    ActionExecutor,
    ActionRecord,
    ActionRequest,
    validate_action,
)
from .adapters import select_adapter
from .browser_selection import PRODUCT_DEFAULT_BROWSER
from .media import KIND_NATIVE_APP, MAX_QUERY_LENGTH
from .media import catalogue as media_catalogue
from .mediasearch.results import MAX_RESULTS, MEDIA_RESULT_MODEL_VERSION
from .mediasearch.service import MediaSearchService
from .mediasearch.sessions import SEARCH_SESSION_TTL_SECONDS
from .config import Config, load_config, load_or_create_token
from .errors import (
    CODE_CONFIGURATION_INVALID,
    CODE_INTERNAL,
    CODE_INVALID_PARAMS,
    CODE_NOT_FOUND,
    CODE_UNAUTHORIZED,
    ApiError,
)
from .events import STATUS_REFRESH_SECONDS, TOKEN_SUBPROTOCOL, EventHub
from .registries import REGISTRY_NAMES, SUPPORTED_VERSION, load_registries
from .runtime import RESOURCE_KINDS, RuntimeInventoryService
from .runtime.overlay_store import (
    REJECT_BUSY,
    REJECT_INVALID_DOCUMENT,
    REJECT_REGISTRY_UNREADABLE,
    REJECT_WRITE_FAILED,
    DisplayOverlayStore,
)
from .runtime.overlay_writes import (
    REJECT_AMBIGUOUS_ALIAS,
    REJECT_AMBIGUOUS_DEVICE,
    REJECT_AMBIGUOUS_IDENTITY,
    REJECT_NOT_LABELLED,
    REJECT_NO_DEVICE,
    REJECT_UNKNOWN_RESOURCE,
    OverlayWriteRejected,
)
from .store import ActionStore, screenshot_path

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"

# An overlay is a short label and a handful of aliases. Anything larger is
# not a naming request, so the body is capped well below the registry file
# limit and refused before it is parsed.
MAX_OVERLAY_BODY_BYTES = 8 * 1024

# Refusal code -> HTTP status. 404 for a resource that is not there, 409 for
# a conflict the user can resolve, 503 for a transient lock, 422 for
# everything the request itself got wrong.
_OVERLAY_STATUS = {
    REJECT_UNKNOWN_RESOURCE: 404,
    REJECT_NOT_LABELLED: 404,
    REJECT_AMBIGUOUS_IDENTITY: 409,
    REJECT_AMBIGUOUS_ALIAS: 409,
    REJECT_AMBIGUOUS_DEVICE: 409,
    REJECT_NO_DEVICE: 409,
    REJECT_REGISTRY_UNREADABLE: 409,
    REJECT_INVALID_DOCUMENT: 422,
    REJECT_BUSY: 503,
    REJECT_WRITE_FAILED: 500,
}


def _error_response(error: ApiError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content=error.to_payload())


def create_app(
    config: Optional[Config] = None,
    token: Optional[str] = None,
    adapter=None,
    inventory=None,
    media_search=None,
) -> FastAPI:
    """Build the application. Arguments are injectable for tests."""
    config = config or load_config()
    config.ensure_dirs()
    token = token or load_or_create_token(config)
    adapter = adapter or select_adapter(config.adapter_name, config)

    store = ActionStore(config)
    hub = EventHub()
    # The inventory service is given the adapter (for the launch table that
    # maps a discovered process to an application definition) and a registry
    # loader (for the optional display labels). It reads both; it writes
    # neither, and a failure in either leaves discovery working and unmapped.
    inventory = inventory or RuntimeInventoryService(
        adapter=adapter, registry_loader=lambda: load_registries(config)
    )

    def _on_event(event: str, record: ActionRecord) -> None:
        hub.broadcast_threadsafe(event, record.to_dict())

    # M2B3A.1. One instance per service, so its search sessions outlive a single
    # request and die with the process — which is the intended lifetime, since a
    # session is a short-lived memory of what someone was looking for.
    media_search = media_search or MediaSearchService(config)

    executor = ActionExecutor(
        adapter, store, config, on_event=_on_event, media_search=media_search
    )

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
    app.state.inventory = inventory
    app.state.media_search = media_search
    overlays = DisplayOverlayStore(config, inventory)
    app.state.display_overlays = overlays

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
                # The milestone this build implements. It says nothing about
                # validation: M1's post-reboot gate is tracked in STATUS.md and
                # is still open.
                "milestone": "M2B",
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

    # -- media providers (M2B3A) ---------------------------------------------

    @app.get("/api/media/providers", dependencies=[Depends(require_token)])
    async def list_media_providers() -> Dict[str, Any]:
        """The code-owned media catalogue, plus what this host can honour.

        ``available`` is the honest per-provider answer and is computed from the
        adapter's live capability list, not from the catalogue: Spotify is
        available only where the application is actually launchable, and a web
        service only where its browser is. It is a statement about whether the
        button would work — **never** about whether anything is running or
        playing. Running instances are runtime inventory, and this is not that.
        """
        applications = await run_in_threadpool(adapter.available_applications)
        # M2B3A.1. Reading the credential *file* is I/O, so it runs off the
        # event loop. What comes back is a tuple of provider ids — never a
        # credential, not even briefly, and not even inside this closure.
        configured = await run_in_threadpool(media_search.configured_providers)
        providers = []
        for entry in media_catalogue(configured):
            needed = entry["application_key"] if entry["kind"] == KIND_NATIVE_APP else entry["browser_key"]
            available = needed in applications
            providers.append(
                dict(
                    entry,
                    available=available,
                    unavailable_reason=None
                    if available
                    else f"{needed} is not installed on this host, or cannot be launched from this session",
                    # A separate axis from ``available``: Spotify can be
                    # perfectly launchable while its official search is not set
                    # up, and the phone must say those two things differently.
                    structured_search_configured=entry["id"] in configured,
                )
            )
        return {
            "default_browser": PRODUCT_DEFAULT_BROWSER,
            "max_query_length": MAX_QUERY_LENGTH,
            "max_results": MAX_RESULTS,
            "result_model_version": MEDIA_RESULT_MODEL_VERSION,
            "search_session_ttl_seconds": SEARCH_SESSION_TTL_SECONDS,
            "providers": providers,
        }

    @app.get("/api/media/diagnostics", dependencies=[Depends(require_token)])
    async def media_diagnostics() -> Dict[str, Any]:
        """Whether each provider's official search is configured — and no more.

        One status word per provider from a closed vocabulary
        (``configured``/``missing``/``invalid``/…). It deliberately does **not**
        return, and has no way to return, a credential value, a prefix, a
        length, a hash, or the path of the credential file. The setup
        documentation names that path; an API response is not the place to
        publish a host's filesystem layout.
        """
        return await run_in_threadpool(media_search.diagnostics)

    @app.post("/api/actions/open-media-provider", dependencies=[Depends(require_token)])
    async def run_open_media_provider(params: Dict[str, Any]) -> JSONResponse:
        return await _run(ACTION_OPEN_MEDIA_PROVIDER, params)

    @app.post("/api/actions/search-media-provider", dependencies=[Depends(require_token)])
    async def run_search_media_provider(params: Dict[str, Any]) -> JSONResponse:
        return await _run(ACTION_SEARCH_MEDIA_PROVIDER, params)

    # -- official-provider results (M2B3A.1) ---------------------------------
    #
    # Two routes, shaped so the client never names a destination. Search takes a
    # provider and a phrase; open takes a search id and a result id, both issued
    # by this server. No route here accepts a URL, a URI, or a video id.

    @app.post(
        "/api/media/providers/{provider_id}/results/search",
        dependencies=[Depends(require_token)],
    )
    async def find_media_results(provider_id: str, params: Dict[str, Any]) -> JSONResponse:
        # The path segment is merged into the typed params rather than trusted
        # on its own: it still goes through the same allowlist validator as
        # every other provider id in the product.
        body = dict(params or {})
        body["provider_id"] = provider_id
        return await _run(ACTION_FIND_MEDIA_RESULTS, body)

    @app.post(
        "/api/media/searches/{search_id}/results/{result_id}/open",
        dependencies=[Depends(require_token)],
    )
    async def open_media_result(
        search_id: str, result_id: str, params: Dict[str, Any]
    ) -> JSONResponse:
        """Open one result, resolved by the server from its own search session.

        ``result_id`` may be the literal ``first`` to take index 0 — the
        explicit "Open first result" action. That spelling exists so the intent
        is visible in the request line, and it is translated into the typed
        ``open_first`` flag rather than smuggled through as a result id.
        """
        body = dict(params or {})
        body["search_id"] = search_id
        if result_id == "first":
            body["open_first"] = True
            body.pop("result_id", None)
        else:
            body["result_id"] = result_id
        return await _run(ACTION_OPEN_MEDIA_RESULT, body)

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

    # -- registries (read-only) ----------------------------------------------

    @app.get("/api/registries", dependencies=[Depends(require_token)])
    async def list_registries() -> Dict[str, Any]:
        """Per-registry load and validation status. Never a filesystem path.

        A machine with no registry files reports six empty, valid version-1
        registries — "not configured" is a normal state, not an error.
        """
        registries = await run_in_threadpool(load_registries, config)
        return {
            "supported_version": SUPPORTED_VERSION,
            "registries": registries.summary(),
        }

    @app.get("/api/registries/{registry_name}", dependencies=[Depends(require_token)])
    async def get_registry(registry_name: str) -> Dict[str, Any]:
        if registry_name not in REGISTRY_NAMES:
            # The requested name is not echoed back: it is arbitrary request
            # text, and a 404 does not need to repeat it to be useful.
            raise ApiError(
                code=CODE_NOT_FOUND,
                message="unknown registry",
                status_code=404,
                detail="known registries: " + ", ".join(REGISTRY_NAMES),
            )
        registries = await run_in_threadpool(load_registries, config)
        load = registries.load(registry_name)
        if not load.ok:
            # ``describe()`` is assembled from a code-owned vocabulary plus a
            # structural location — no file content, no path, no exception text.
            raise ApiError(
                code=CODE_CONFIGURATION_INVALID,
                message="this registry's local configuration is invalid",
                status_code=500,
                detail=load.error.describe() if load.error else None,
            )
        payload = load.registry.to_dict()
        payload["source"] = load.registry.source
        return payload

    # -- runtime inventory (read-only, M2B) ----------------------------------

    @app.get("/api/runtime", dependencies=[Depends(require_token)])
    async def runtime_snapshot(refresh: bool = False) -> Dict[str, Any]:
        """One observation of this machine: displays, applications, processes, windows.

        ``refresh=true`` bypasses the few-second cache. It is the only knob, it
        costs one process scan, and it exists for the PWA's refresh button —
        everything else reads the shared snapshot so a client can never see
        displays from one instant beside processes from another.

        Collecting walks ``/proc`` and queries the session bus, so it runs in a
        worker thread rather than blocking the event loop.
        """
        snapshot = await run_in_threadpool(inventory.snapshot, refresh)
        return snapshot.to_dict()

    @app.get("/api/runtime/{resource_kind}", dependencies=[Depends(require_token)])
    async def runtime_collection(resource_kind: str, refresh: bool = False) -> Dict[str, Any]:
        """One collection, served with the snapshot header it belongs to.

        The header is not padding: a list of processes is uninterpretable
        without the boot it was read in, and a list of displays is
        uninterpretable without the graphical session. A client that caches a
        collection needs both to know when to throw it away.
        """
        if resource_kind not in RESOURCE_KINDS:
            # The requested kind is arbitrary request text and is not echoed
            # back, matching the registry routes.
            raise ApiError(
                code=CODE_NOT_FOUND,
                message="unknown runtime resource kind",
                status_code=404,
                detail="known kinds: " + ", ".join(RESOURCE_KINDS),
            )
        snapshot, collection = await run_in_threadpool(
            inventory.collection, resource_kind, refresh
        )
        return {
            "version": snapshot.version,
            "observed_at": snapshot.observed_at,
            "host": dict(snapshot.host),
            "boot": dict(snapshot.boot),
            "session": dict(snapshot.session),
            "collection": collection.to_dict(),
        }

    # -- display overlays (the only write path into configuration, M2B2) -----
    #
    # Everything above this point is read-only. These two routes are the first
    # thing reachable over the network that changes a file on disk, so the
    # constraints are deliberately narrow: authenticated, JSON only, bounded,
    # addressed by a *runtime* resource id, and never trusting the client for
    # the persistent key. See runtime/overlay_writes.py for the reasoning.

    async def _overlay_body(request: Request) -> Dict[str, Any]:
        """Read a bounded JSON body, or refuse before parsing anything."""
        content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="this endpoint accepts application/json only",
                status_code=415,
            )
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                too_big = int(declared) > MAX_OVERLAY_BODY_BYTES
            except ValueError:
                raise ApiError(
                    code=CODE_INVALID_PARAMS,
                    message="invalid Content-Length",
                    status_code=400,
                )
            if too_big:
                raise ApiError(
                    code=CODE_INVALID_PARAMS,
                    message="the request body is too large",
                    status_code=413,
                )
        raw = await request.body()
        # Checked again after reading: Content-Length is a claim, not a fact.
        if len(raw) > MAX_OVERLAY_BODY_BYTES:
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="the request body is too large",
                status_code=413,
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="the request body is not valid JSON",
                status_code=400,
            )
        if not isinstance(payload, dict):
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="the request body must be a JSON object",
                status_code=400,
            )
        # Unknown fields are refused rather than ignored. A request carrying
        # `edid_sha256`, `id` or `registry` is a caller trying to choose the
        # storage key, and silently dropping it would teach them it worked.
        unexpected = sorted(set(payload) - {"label", "aliases"})
        if unexpected:
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="unexpected field: " + unexpected[0],
                status_code=422,
                detail="only 'label' and 'aliases' may be sent; the persistent identity is "
                "derived by the service from live discovery and cannot be supplied",
            )
        return payload

    @app.put("/api/runtime/displays/{resource_id}/overlay", dependencies=[Depends(require_token)])
    async def put_display_overlay(resource_id: str, request: Request) -> Dict[str, Any]:
        """Create or replace the user's name for one discovered display."""
        payload = await _overlay_body(request)
        try:
            result = await run_in_threadpool(
                overlays.save, resource_id, payload.get("label"), payload.get("aliases")
            )
        except OverlayWriteRejected as rejection:
            store.record_overlay_event("overlay_updated", resource_id, rejection.code)
            raise ApiError(
                code=rejection.code,
                message=rejection.message,
                status_code=_OVERLAY_STATUS.get(rejection.code, 422),
                detail=rejection.detail,
            )
        store.record_overlay_event("overlay_updated", resource_id, "ok")
        return result

    @app.delete("/api/runtime/displays/{resource_id}/overlay", dependencies=[Depends(require_token)])
    async def delete_display_overlay(resource_id: str) -> Dict[str, Any]:
        """Remove the user's name, leaving the hardware identity as the title."""
        try:
            result = await run_in_threadpool(overlays.delete, resource_id)
        except OverlayWriteRejected as rejection:
            store.record_overlay_event("overlay_removed", resource_id, rejection.code)
            raise ApiError(
                code=rejection.code,
                message=rejection.message,
                status_code=_OVERLAY_STATUS.get(rejection.code, 422),
                detail=rejection.detail,
            )
        store.record_overlay_event("overlay_removed", resource_id, "ok")
        return result

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
