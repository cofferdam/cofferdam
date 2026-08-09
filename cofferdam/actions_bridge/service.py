"""The Actions bridge application: eight operations and one health check.

This process is **not** the Cofferdam daemon, not the PWA, and not a proxy. It
speaks its own ``/v1`` vocabulary, and every response is assembled in
:mod:`~cofferdam.actions_bridge.normalize` from named fields. There is no route
here that forwards a path, a method, a header or a query string to Cofferdam;
the only way out of this process is the ten fixed methods on
:class:`~cofferdam.actions_bridge.internal.InternalTaskClient`.

The eight Actions
-----------------

===========================  =======================================
``listProjects``             where a task may run
``createTask``               start one
``listRecentTasks``          recover a lost task reference
``syncTask``                 one bounded snapshot: state, question, result
``submitChoiceAnswer``       exactly one option id
``sendFollowup``             one more message into the same live task
``cancelTask``               stop it
``finishTask``               close a session whose work is done
===========================  =======================================

What has no route, and never will
---------------------------------

No approval. No tool decision. No permission mode, model, budget, tool list or
effort. No shell. No path, and therefore no file read, no artifact browse and no
repository listing. No transcript, no event stream, no provider session id, no
Remote Control. Several of these exist behind the daemon's own credential and
are unreachable from here because :func:`require_bridge_key` authenticates
against a *different* secret than the one the daemon's other routes accept.

Body handling
-------------

Every mutation body goes through :func:`_bridge_body`, which refuses an unknown
field rather than ignoring it. The allowlist is the surface — the same rule as
``_task_body`` in the workstation service, for the same reason: a field that is
absent cannot be smuggled, and a field that is ignored eventually gets read.
"""

from __future__ import annotations

import contextlib
import json
import time
from typing import Any, Dict, Optional, Set

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .config import BridgeConfig, load_bridge_config, read_secret_file
from .errors import (
    CODE_IDEMPOTENCY_CONFLICT,
    CODE_INTERNAL,
    CODE_INVALID_REQUEST,
    CODE_NOT_FOUND,
    CODE_RATE_LIMITED,
    CODE_REQUEST_IN_FLIGHT,
    CODE_TOO_LARGE,
    CODE_UNAUTHORIZED,
    BridgeError,
    status_for,
)
from .idempotency import (
    IdempotencyConflict,
    IdempotencyStore,
    RequestInFlight,
    body_digest,
    valid_request_id,
)
from .internal import (
    InternalTaskClient,
    valid_option_id,
    valid_project_id,
    valid_task_id,
)
from .limits import (
    BRIDGE_API_VERSION,
    MAX_EXPECTED_OUTPUT_CHARS,
    MAX_FOLLOWUP_TEXT_CHARS,
    MAX_HEADER_BYTES,
    MAX_HEADER_COUNT,
    MAX_RESPONSE_BYTES,
    MAX_TASK_TEXT_CHARS,
    MAX_TITLE_CHARS,
)
from .normalize import (
    adapter_for_project,
    clarification_view,
    created_task_view,
    display_ref,
    mutation_view,
    projects_view,
    recent_tasks_view,
    task_snapshot_view,
)
from .observe import get_logger, log_request, new_request_id
from .ratelimit import RateLimiter

#: Applied to every authenticated response. ``no-store`` because a body may
#: carry a question or a result; ``nosniff`` because a JSON body that a browser
#: decides is HTML is a JSON body that can run.
#:
#: There is no CORS header anywhere in this file. GPT Actions are called
#: server-to-server from OpenAI's infrastructure, not from a browser, so a
#: permissive ``Access-Control-Allow-Origin`` would grant page JavaScript on any
#: site a capability the contract never needed.
SAFE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

#: The composed heading under which an expected-output summary is appended to a
#: task instruction. Code-owned and constant: it is the only text the bridge
#: itself adds to anything a caller sends, and it must not be a template a
#: caller can influence.
EXPECTED_OUTPUT_HEADING = "\n\nExpected output:\n"

# -- operation ids ------------------------------------------------------------
#
# One tuple, read by the logger, by the OpenAPI test and by nothing else. If a
# route's operationId is not in here the schema test fails, which is what stops
# a route existing that the published contract does not describe.

OP_HEALTH = "bridgeHealth"
OP_LIST_PROJECTS = "listProjects"
OP_CREATE_TASK = "createTask"
OP_LIST_RECENT = "listRecentTasks"
OP_SYNC = "syncTask"
OP_ANSWER = "submitChoiceAnswer"
OP_FOLLOWUP = "sendFollowup"
OP_CANCEL = "cancelTask"
OP_FINISH = "finishTask"

OPERATION_IDS = (
    OP_HEALTH,
    OP_LIST_PROJECTS,
    OP_CREATE_TASK,
    OP_LIST_RECENT,
    OP_SYNC,
    OP_ANSWER,
    OP_FOLLOWUP,
    OP_CANCEL,
    OP_FINISH,
)

#: The operations that change something.
#:
#: Not read by the rate limiter — that charges the tighter bucket by HTTP
#: method, which cannot fall out of step with the routes. This tuple exists so
#: the OpenAPI conformance test can assert that exactly these five carry
#: ``x-openai-isConsequential: true``: the schema and the code must agree about
#: which Actions ChatGPT should confirm before calling, and a mismatch would
#: silently remove a confirmation prompt from something that starts an agent.
MUTATIONS = frozenset({OP_CREATE_TASK, OP_ANSWER, OP_FOLLOWUP, OP_CANCEL, OP_FINISH})


def _error_response(error: BridgeError, headers: Optional[Dict[str, str]] = None):
    return JSONResponse(
        status_code=error.status_code,
        content=error.to_payload(),
        headers={**SAFE_HEADERS, **(headers or {})},
    )


def _ok(payload: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    """A bounded success response.

    The size check is a backstop, not the mechanism — every field is already
    bounded by :mod:`~cofferdam.actions_bridge.normalize`. It fires as a 500
    rather than by truncating the JSON, because half a document is worse than an
    error: ChatGPT would parse it as a failure anyway, having first shown a
    model something that looked like data.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_RESPONSE_BYTES:
        raise BridgeError(
            code=CODE_INTERNAL,
            message="internal error",
            status_code=500,
            detail="response exceeded the bridge bound",
        )
    return JSONResponse(status_code=status_code, content=payload, headers=SAFE_HEADERS)


def create_bridge_app(
    config: Optional[BridgeConfig] = None,
    *,
    external_key: Optional[str] = None,
    internal_client: Optional[InternalTaskClient] = None,
) -> FastAPI:
    """Build the bridge application.

    ``external_key`` and ``internal_client`` are injectable so tests can run the
    whole surface without a daemon and without a real secret on disk. In
    production both are ``None`` and both come from 0600 files whose mode is
    checked before they are read — see
    :func:`~cofferdam.actions_bridge.config.read_secret_file`.
    """
    import secrets as _secrets

    config = config or load_bridge_config()

    if external_key is None:
        external_key = read_secret_file(
            config.external_key_path, what="Actions bridge external key"
        )
    if internal_client is None:
        internal_token = read_secret_file(
            config.internal_token_path, what="Cofferdam internal bridge token"
        )
        internal_client = InternalTaskClient(
            base_url=config.internal_base_url,
            token=internal_token,
            timeout=config.upstream_timeout_seconds,
        )
        # Dropped immediately. The client holds it privately; nothing else in
        # this function needs it, and a name still bound in the enclosing scope
        # is a name a later closure could capture.
        del internal_token

    store = IdempotencyStore(config.idempotency_path)
    limiter = RateLimiter(
        per_minute=config.rate_limit_per_minute,
        burst=config.rate_limit_burst,
        mutation_per_minute=config.mutation_rate_limit_per_minute,
        mutation_burst=config.mutation_rate_limit_burst,
        max_concurrent=config.max_concurrent_requests,
    )
    logger = get_logger()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        """Nothing to start; two things to release.

        The bridge holds no background task, no poller and no timer — it does
        work only when asked. What it does own is an HTTP connection pool to the
        daemon and one SQLite handle, and both are closed here so a restart does
        not leave either behind.
        """
        try:
            yield
        finally:
            for closer in (getattr(internal_client, "close", None), store.close):
                try:
                    if closer is not None:
                        closer()
                except Exception:  # pragma: no cover - tidying must not raise
                    continue

    app = FastAPI(
        title="Cofferdam Actions bridge",
        version=str(BRIDGE_API_VERSION),
        lifespan=lifespan,
        # No interactive docs and no served schema. The published contract is
        # the reviewed file in docs/custom-gpt/openapi.yaml; a second, generated
        # one on a reachable origin would be a second description of this
        # surface that nobody reviews and that could disagree with the first.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config = config
    app.state.idempotency = store

    # -- authentication ------------------------------------------------------

    async def require_bridge_key(request: Request) -> None:
        """The external key, from an ``Authorization: Bearer`` header only.

        Three refusals worth naming.

        **A key in a query string is not read.** Not compared and rejected —
        never looked at. Query strings reach access logs, browser history and
        referrer headers, and a bridge that accepted one there would make those
        places credential stores.

        **The comparison is constant-time**, so a caller cannot learn the key one
        byte at a time from response timing.

        **The 401 says nothing about which half was wrong.** "No credential" and
        "wrong credential" produce the same body, because the difference is only
        useful to somebody who does not have it.
        """
        header = request.headers.get("authorization", "")
        candidate = (
            header[7:].strip() if header.lower().startswith("bearer ") else None
        )
        if not candidate or not _secrets.compare_digest(candidate, external_key):
            raise BridgeError(
                code=CODE_UNAUTHORIZED,
                message="A valid Cofferdam bridge key is required.",
                status_code=status_for(CODE_UNAUTHORIZED),
            )

    # -- envelope ------------------------------------------------------------

    @app.exception_handler(BridgeError)
    async def _bridge_error_handler(request: Request, exc: BridgeError):
        _finish(request, status=exc.status_code, error_code=exc.code)
        return _error_response(exc)

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception):
        """Every unexpected failure, as an empty 500.

        Unlike the workstation's handler, this one does not put the exception
        *type* in ``detail``. On the private API that is a useful hint to the
        person who owns the machine; here the reader is a model provider, and a
        type name is a small, free fact about what this process is built from.
        """
        _finish(request, status=500, error_code=CODE_INTERNAL)
        return _error_response(
            BridgeError(
                code=CODE_INTERNAL, message="Internal bridge error.", status_code=500
            )
        )

    # -- per-request bookkeeping ---------------------------------------------

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        """Header bounds, rate limit, concurrency, timing and the log line.

        Ordered cheapest-first: header checks cost nothing, the rate limiter
        costs a lock, and the concurrency gate holds a slot for the whole
        request. A caller that trips the first never reaches the third.
        """
        request.state.request_id = new_request_id()
        request.state.operation = "-"
        request.state.display_ref = None
        request.state.replayed = False
        request.state.started = time.monotonic()
        request.state.logged = False

        if len(request.headers) > MAX_HEADER_COUNT:
            return _reject(request, CODE_TOO_LARGE, "Too many request headers.")
        for name, value in request.headers.items():
            if len(name) + len(value) > MAX_HEADER_BYTES:
                return _reject(request, CODE_TOO_LARGE, "A request header is too large.")

        # The health check is outside both buckets. It is unauthenticated and
        # returns four constant fields; rate limiting it would mean a tunnel
        # health probe could exhaust the budget a real Action needs.
        if request.url.path == "/v1/health":
            response = await call_next(request)
            _finish(request, status=response.status_code)
            return _apply(response)

        mutation = request.method in ("POST", "PUT", "PATCH", "DELETE")
        retry_after = limiter.check(mutation=mutation)
        if retry_after is not None:
            return _reject(
                request,
                CODE_RATE_LIMITED,
                "Too many requests. Wait a moment and sync rather than resending.",
                headers={"Retry-After": str(retry_after)},
            )
        if not limiter.enter():
            return _reject(
                request,
                CODE_RATE_LIMITED,
                "The bridge is busy. Try again in a moment.",
                headers={"Retry-After": "2"},
            )
        try:
            response = await call_next(request)
        finally:
            limiter.leave()
        _finish(request, status=response.status_code)
        return _apply(response)

    def _apply(response):
        for key, value in SAFE_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    def _reject(
        request: Request,
        code: str,
        message: str,
        *,
        headers: Optional[Dict[str, str]] = None,
    ):
        _finish(request, status=status_for(code), error_code=code)
        return _error_response(
            BridgeError(code=code, message=message, status_code=status_for(code)),
            headers=headers,
        )

    def _finish(
        request: Request, *, status: int, error_code: Optional[str] = None
    ) -> None:
        """Emit the one log line for this request, at most once."""
        if getattr(request.state, "logged", False):
            return
        request.state.logged = True
        started = getattr(request.state, "started", None)
        duration = int((time.monotonic() - started) * 1000) if started else 0
        log_request(
            logger,
            request_id=getattr(request.state, "request_id", "-"),
            operation=getattr(request.state, "operation", "-"),
            status=status,
            duration_ms=duration,
            display_ref=getattr(request.state, "display_ref", None),
            replayed=bool(getattr(request.state, "replayed", False)),
            error_code=error_code,
        )

    def _mark(request: Request, operation: str) -> None:
        request.state.operation = operation

    def _note(request: Request, task_id: Any, *, replayed: bool = False) -> None:
        request.state.display_ref = display_ref(task_id)
        request.state.replayed = replayed

    # -- request bodies ------------------------------------------------------

    async def _bridge_body(request: Request, *, allowed: Set[str]) -> Dict[str, Any]:
        """One bounded JSON object, refusing anything unexpected.

        The allowlist is the surface. There is no field anywhere below for a
        path, a working directory, an executable, an environment, a model, a
        tool, a permission mode, a budget, a provider session id or an approval
        — they are not validated and rejected, they are absent, and an
        unexpected key is a refusal rather than a silently dropped one.
        """
        content_type = (
            (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        )
        if content_type and content_type != "application/json":
            raise BridgeError(
                code=CODE_INVALID_REQUEST,
                message="This operation accepts application/json only.",
                status_code=415,
            )
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                oversized = int(declared) > config.max_body_bytes
            except ValueError:
                raise BridgeError(
                    code=CODE_INVALID_REQUEST,
                    message="Invalid Content-Length.",
                    status_code=400,
                )
            if oversized:
                raise BridgeError(
                    code=CODE_TOO_LARGE,
                    message="The request body is too large.",
                    status_code=status_for(CODE_TOO_LARGE),
                )
        raw = await request.body()
        # Checked again after reading: Content-Length is a claim, not a fact.
        if len(raw) > config.max_body_bytes:
            raise BridgeError(
                code=CODE_TOO_LARGE,
                message="The request body is too large.",
                status_code=status_for(CODE_TOO_LARGE),
            )
        if not raw:
            payload: Any = {}
        else:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                # Covers malformed Unicode as well as malformed JSON: a body
                # that is not valid UTF-8 never becomes a str at all.
                raise BridgeError(
                    code=CODE_INVALID_REQUEST,
                    message="The request body is not valid JSON.",
                    status_code=400,
                )
        if not isinstance(payload, dict):
            raise BridgeError(
                code=CODE_INVALID_REQUEST,
                message="The request body must be a JSON object.",
                status_code=400,
            )
        unexpected = sorted(set(payload) - allowed)
        if unexpected:
            raise BridgeError(
                code=CODE_INVALID_REQUEST,
                message="Unexpected field: " + unexpected[0],
                status_code=status_for(CODE_INVALID_REQUEST),
                detail="This operation accepts only: " + ", ".join(sorted(allowed)),
            )
        return payload

    def _required_text(payload: Dict[str, Any], key: str, limit: int) -> str:
        """Bounded user text, refused rather than truncated when too long.

        Control characters other than tab and newline are refused too, matching
        Task Core's rule: a task instruction is prose, and anything else in it
        arrived by mistake or on purpose.
        """
        value = payload.get(key)
        if not isinstance(value, str):
            raise BridgeError(
                code=CODE_INVALID_REQUEST,
                message=f"'{key}' is required and must be text.",
                status_code=status_for(CODE_INVALID_REQUEST),
            )
        stripped = value.strip()
        if not stripped:
            raise BridgeError(
                code=CODE_INVALID_REQUEST,
                message=f"'{key}' cannot be empty.",
                status_code=status_for(CODE_INVALID_REQUEST),
            )
        if len(stripped) > limit:
            raise BridgeError(
                code=CODE_INVALID_REQUEST,
                message=(
                    f"'{key}' must be under {limit} characters. Summarise rather "
                    "than pasting a conversation."
                ),
                status_code=status_for(CODE_INVALID_REQUEST),
            )
        for character in stripped:
            code_point = ord(character)
            if character in ("\t", "\n"):
                continue
            if code_point < 0x20 or code_point == 0x7F or 0x80 <= code_point <= 0x9F:
                raise BridgeError(
                    code=CODE_INVALID_REQUEST,
                    message=f"'{key}' contains a control character.",
                    status_code=status_for(CODE_INVALID_REQUEST),
                )
        return stripped

    def _optional_text(
        payload: Dict[str, Any], key: str, limit: int
    ) -> Optional[str]:
        if payload.get(key) is None:
            return None
        return _required_text(payload, key, limit)

    def _request_key(payload: Dict[str, Any]) -> str:
        value = payload.get("client_request_id")
        if not valid_request_id(value):
            raise BridgeError(
                code=CODE_INVALID_REQUEST,
                message=(
                    "'client_request_id' must be 8-64 characters of letters, "
                    "digits, dot, dash, colon or underscore. Reuse it on a retry "
                    "and change it for a genuinely new request."
                ),
                status_code=status_for(CODE_INVALID_REQUEST),
            )
        return value

    def _task_id(value: Any) -> str:
        if not valid_task_id(value):
            # 404 rather than 422. A malformed id and an id for a task that does
            # not exist are the same answer to a caller who should not be able
            # to tell whether a given handle is real.
            raise BridgeError(
                code=CODE_NOT_FOUND,
                message=(
                    "No such task. Call listRecentTasks to get a current task_id "
                    "rather than reconstructing one."
                ),
                status_code=status_for(CODE_NOT_FOUND),
            )
        return value

    # -- the idempotent mutation wrapper -------------------------------------

    def _claim(operation: str, scope: str, request_key: str, payload: Dict[str, Any]):
        try:
            return store.claim(
                operation=operation,
                scope=scope,
                request_id=request_key,
                digest=body_digest(payload),
            )
        except IdempotencyConflict:
            raise BridgeError(
                code=CODE_IDEMPOTENCY_CONFLICT,
                message=(
                    "That client_request_id was already used for a different "
                    "request. Use a new one for a new request."
                ),
                status_code=status_for(CODE_IDEMPOTENCY_CONFLICT),
            )
        except RequestInFlight:
            raise BridgeError(
                code=CODE_REQUEST_IN_FLIGHT,
                message=(
                    "That request is already being processed. Sync the task "
                    "rather than sending it again."
                ),
                status_code=status_for(CODE_REQUEST_IN_FLIGHT),
            )

    # -- health ---------------------------------------------------------------

    @app.get("/v1/health")
    async def bridge_health(request: Request):
        """Four constant fields, unauthenticated, and nothing derived from state.

        Unauthenticated on purpose and justified narrowly: a tunnel has to be
        verifiable *before* a key is entered into the GPT editor, and an
        operator staring at a 401 cannot tell a broken tunnel from a wrong key.

        It reveals that a Cofferdam bridge is listening. It does not say whether
        the daemon is up, how many tasks exist, what the host is called, what
        version of anything is installed, or how long it has been running —
        every one of which would be a free fact for somebody scanning.
        """
        _mark(request, OP_HEALTH)
        return _ok(
            {
                "status": "ok",
                "service": "cofferdam-actions-bridge",
                "version": BRIDGE_API_VERSION,
                "authenticated_operations": len(OPERATION_IDS) - 1,
            }
        )

    # -- 1. list_projects -----------------------------------------------------

    @app.get("/v1/projects", dependencies=[Depends(require_bridge_key)])
    async def list_projects(request: Request):
        """Projects eligible for delegated tasks. Names, never paths."""
        _mark(request, OP_LIST_PROJECTS)
        payload = await run_in_threadpool(internal_client.list_projects)
        return _ok(projects_view(payload))

    # -- 2. create_task -------------------------------------------------------

    @app.post("/v1/tasks", dependencies=[Depends(require_bridge_key)])
    async def create_task(request: Request):
        """Start one task in one registered project.

        Five fields, and the list of what is absent is longer than the list of
        what is present: no path, no working directory, no adapter flag, no
        model, no effort, no tool list, no permission mode, no budget, no
        environment, no executable, no MCP configuration, no hook, no metadata
        blob. The adapter is chosen by Cofferdam from the project's own registry
        entry — the bridge does not send ``adapter_id`` at all.

        ``expected_output`` is appended to the instruction under a fixed heading
        this module owns. It is the only text the bridge composes, it counts
        against the same total bound, and there is no way for a caller to
        influence the heading itself.
        """
        _mark(request, OP_CREATE_TASK)
        payload = await _bridge_body(
            request,
            allowed={
                "project_id",
                "task_text",
                "client_request_id",
                "title",
                "expected_output",
            },
        )
        project_id = payload.get("project_id")
        if not valid_project_id(project_id):
            raise BridgeError(
                code=CODE_NOT_FOUND,
                message=(
                    "No such project. Call listProjects and use a project_id "
                    "from that list."
                ),
                status_code=status_for(CODE_NOT_FOUND),
            )
        # Resolved from the host's own registry before anything else happens.
        # This is both the eligibility check and the adapter decision, and it
        # costs one extra internal read on the one operation that starts work.
        registry = await run_in_threadpool(internal_client.list_projects)
        adapter_id = adapter_for_project(registry, project_id)
        if adapter_id is None:
            raise BridgeError(
                code=CODE_NOT_FOUND,
                message=(
                    "No such project, or it is not set up to accept delegated "
                    "tasks. Call listProjects and use a project_id from that list."
                ),
                status_code=status_for(CODE_NOT_FOUND),
            )

        task_text = _required_text(payload, "task_text", MAX_TASK_TEXT_CHARS)
        expected = _optional_text(payload, "expected_output", MAX_EXPECTED_OUTPUT_CHARS)
        title = _optional_text(payload, "title", MAX_TITLE_CHARS)
        request_key = _request_key(payload)

        prompt = task_text
        if expected:
            prompt = task_text + EXPECTED_OUTPUT_HEADING + expected
            if len(prompt) > MAX_TASK_TEXT_CHARS:
                raise BridgeError(
                    code=CODE_INVALID_REQUEST,
                    message=(
                        "task_text and expected_output together must be under "
                        f"{MAX_TASK_TEXT_CHARS} characters."
                    ),
                    status_code=status_for(CODE_INVALID_REQUEST),
                )

        fresh, existing_task_id = _claim(
            OP_CREATE_TASK, "project:" + project_id, request_key, payload
        )
        if not fresh:
            # Replayed. The current state is re-read rather than replayed from a
            # stored response, so a caller retrying two minutes later learns
            # where the task is now.
            snapshot = await run_in_threadpool(
                internal_client.get_task, existing_task_id
            )
            _note(request, existing_task_id, replayed=True)
            view = created_task_view(snapshot.get("task"), created=False)
            view["replayed"] = True
            return _ok(view, status_code=200)

        try:
            result = await run_in_threadpool(
                internal_client.create_task,
                project_id=project_id,
                # Read from the project's own registry entry a moment ago, not
                # from the request. There is no adapter field on this Action —
                # see `normalize.adapter_for_project` for who decides and why.
                adapter_id=adapter_id,
                prompt=prompt,
                client_request_id=request_key,
                title=title,
            )
        except BaseException:
            store.release(
                operation=OP_CREATE_TASK,
                scope="project:" + project_id,
                request_id=request_key,
            )
            raise

        snapshot = result.get("task") if isinstance(result, dict) else None
        task_id = snapshot.get("task_id") if isinstance(snapshot, dict) else None
        created = bool(result.get("created"))
        store.settle(
            operation=OP_CREATE_TASK,
            scope="project:" + project_id,
            request_id=request_key,
            task_id=task_id,
        )
        _note(request, task_id, replayed=not created)
        view = created_task_view(snapshot, created=created)
        view["replayed"] = not created
        return _ok(view, status_code=201 if created else 200)

    # -- 3. list_recent_tasks -------------------------------------------------

    @app.get("/v1/tasks", dependencies=[Depends(require_bridge_key)])
    async def list_recent_tasks(
        request: Request,
        limit: int = Query(default=None, ge=1, le=100),
    ):
        """A bounded, deterministically ordered recent list. No task content.

        This is the recovery Action: a conversation that has lost which task it
        was talking about calls it and reads a current ``task_id`` back, rather
        than a model reconstructing one from a display reference it remembers.
        """
        _mark(request, OP_LIST_RECENT)
        bounded = min(limit or config.default_recent_tasks, config.max_recent_tasks)
        payload = await run_in_threadpool(internal_client.list_tasks, limit=bounded)
        return _ok(recent_tasks_view(payload, limit=bounded))

    # -- 4. sync_task ---------------------------------------------------------

    @app.get("/v1/tasks/{task_id}", dependencies=[Depends(require_bridge_key)])
    async def sync_task(request: Request, task_id: str):
        """One bounded snapshot: state, open question, latest result.

        Up to three internal reads folded into one response. The clarification
        read happens only when the task says it is waiting on one, and the
        result read only when there could be a result — a task that has not
        produced anything is not asked whether it has.

        This is the Action that carries the product's one honest limitation:
        **the bridge cannot push anything into a ChatGPT conversation.** Somebody
        has to take a turn for this to be called. See
        ``docs/custom-gpt/INSTRUCTIONS.md``.
        """
        _mark(request, OP_SYNC)
        checked = _task_id(task_id)
        _note(request, checked)

        payload = await run_in_threadpool(internal_client.get_task, checked)
        snapshot = payload.get("task") if isinstance(payload, dict) else None
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        state = snapshot.get("state")
        waiting_reason = snapshot.get("waiting_reason")

        question = None
        if waiting_reason == "clarification":
            pending = await run_in_threadpool(
                internal_client.list_clarifications, checked
            )
            entries = (
                pending.get("clarifications") if isinstance(pending, dict) else None
            )
            if isinstance(entries, list) and entries:
                # The first open question, and only the first. Task Core bounds a
                # task to eight pending questions but a provider turn asks one;
                # publishing a list would invite a model to answer them in an
                # order nobody chose.
                question = clarification_view(entries[0])

        result = None
        if state in ("completed", "failed", "cancelled", "interrupted", "ready_for_followup"):
            try:
                payload_result = await run_in_threadpool(
                    internal_client.get_result, checked
                )
            except BridgeError as refusal:
                # "Not ready" is an answer, not a failure: a task can reach a
                # terminal state between the two reads above. Anything else is
                # a real refusal and is re-raised.
                if refusal.detail != "task_result_not_ready":
                    raise
                payload_result = None
            if isinstance(payload_result, dict):
                candidate = payload_result.get("result")
                result = candidate if isinstance(candidate, dict) else None

        return _ok(
            task_snapshot_view(snapshot, clarification=question, result=result)
        )

    # -- 5. submit_choice_answer ----------------------------------------------

    @app.post(
        "/v1/tasks/{task_id}/answer", dependencies=[Depends(require_bridge_key)]
    )
    async def submit_choice_answer(request: Request, task_id: str):
        """Exactly one ``option_id``, for one question, on one task.

        **There is no text field on this route.** Not an empty one, not an
        optional one, not one that is validated and refused — none. Task Core's
        own answer route accepts free text because a person at the PWA types it;
        the bridge does not offer that channel, so prose a model composed cannot
        reach a waiting agent through a question's answer slot.

        There is likewise no ``option_ids`` array. One question, one choice: an
        array would be a shape a model could fill with two, and the refusal would
        arrive from Cofferdam rather than from the contract.

        The display number a person said — "answer 2" — is **not** accepted. The
        Custom GPT maps that number to the ``option_id`` it received from
        ``syncTask``, and this route takes the id. A route that accepted an
        index would be a route where a stale list silently answers the wrong
        question.
        """
        _mark(request, OP_ANSWER)
        checked = _task_id(task_id)
        _note(request, checked)
        payload = await _bridge_body(
            request, allowed={"question_id", "option_id", "client_request_id"}
        )
        question_id = payload.get("question_id")
        option_id = payload.get("option_id")
        if not isinstance(question_id, str) or not question_id:
            raise BridgeError(
                code=CODE_INVALID_REQUEST,
                message="'question_id' is required. Take it from syncTask.",
                status_code=status_for(CODE_INVALID_REQUEST),
            )
        if not valid_option_id(option_id):
            # The message names the mistake rather than describing the grammar,
            # because the mistake is predictable: the user said "answer 2" and
            # the model sent 2. A display number cannot be an option id — they
            # begin with a letter — so this is catchable here, with an
            # instruction, instead of arriving as a refusal from Cofferdam.
            raise BridgeError(
                code=CODE_INVALID_REQUEST,
                message=(
                    "'option_id' must be one of the option_id values syncTask "
                    "returned for this question - not a display number, not the "
                    "option label, and not a value you composed."
                ),
                status_code=status_for(CODE_INVALID_REQUEST),
            )
        request_key = _request_key(payload)

        fresh, _ = _claim(OP_ANSWER, "task:" + checked, request_key, payload)
        if not fresh:
            snapshot = await run_in_threadpool(internal_client.get_task, checked)
            _note(request, checked, replayed=True)
            return _ok(mutation_view(snapshot.get("task"), replayed=True))

        try:
            result = await run_in_threadpool(
                internal_client.answer_clarification,
                task_id=checked,
                question_id=question_id,
                option_id=option_id,
            )
        except BaseException:
            store.release(
                operation=OP_ANSWER, scope="task:" + checked, request_id=request_key
            )
            raise
        store.settle(
            operation=OP_ANSWER,
            scope="task:" + checked,
            request_id=request_key,
            task_id=checked,
        )
        return _ok(mutation_view(result.get("task"), replayed=False))

    # -- 6. send_followup -----------------------------------------------------

    @app.post(
        "/v1/tasks/{task_id}/followup", dependencies=[Depends(require_bridge_key)]
    )
    async def send_followup(request: Request, task_id: str):
        """One more message into the session this task already owns.

        Three fields. No provider session id — the session is found from the
        task's own id, on the host, by the adapter that owns it. No prompt
        wrapper: unlike ``createTask``, nothing is composed around this text,
        because a follow-up lands mid-conversation and an inserted heading would
        be words the agent reads as the user's.

        Task Core remains the authority on whether this is legal. It refuses
        while a question is open, refuses after a terminal state, refuses when
        the session is gone, and refuses a second turn while one is in flight —
        and each of those becomes a bounded ``not_allowed_now`` here.
        """
        _mark(request, OP_FOLLOWUP)
        checked = _task_id(task_id)
        _note(request, checked)
        payload = await _bridge_body(
            request, allowed={"followup_text", "client_request_id"}
        )
        text = _required_text(payload, "followup_text", MAX_FOLLOWUP_TEXT_CHARS)
        request_key = _request_key(payload)

        fresh, _ = _claim(OP_FOLLOWUP, "task:" + checked, request_key, payload)
        if not fresh:
            snapshot = await run_in_threadpool(internal_client.get_task, checked)
            _note(request, checked, replayed=True)
            return _ok(mutation_view(snapshot.get("task"), replayed=True))

        try:
            result = await run_in_threadpool(
                internal_client.send_followup,
                task_id=checked,
                followup=text,
                # The same key travels to Task Core, which has its own
                # idempotency on this route. Two independent guards on one
                # retry: the bridge's stops a second call, and Task Core's
                # stops a second turn if one ever gets past the first.
                client_request_id=request_key,
            )
        except BaseException:
            store.release(
                operation=OP_FOLLOWUP, scope="task:" + checked, request_id=request_key
            )
            raise
        store.settle(
            operation=OP_FOLLOWUP,
            scope="task:" + checked,
            request_id=request_key,
            task_id=checked,
        )
        return _ok(mutation_view(result.get("task"), replayed=False))

    # -- 7. cancel_task -------------------------------------------------------

    @app.post(
        "/v1/tasks/{task_id}/cancel", dependencies=[Depends(require_bridge_key)]
    )
    async def cancel_task(request: Request, task_id: str):
        """Ask this task's own adapter to stop it.

        Two fields, and an optional reason category from a closed list that is
        recorded nowhere and sent nowhere — it exists so a model has somewhere to
        put "why" other than inventing a field, and it is deliberately an enum
        rather than text so it cannot become a channel.

        Nothing here names a signal, a pid, a process group or a stop mode.
        Cancellation goes through Task Core, which re-verifies process identity
        before it signals anything and leaves a task ``cancelling`` rather than
        claiming ``cancelled`` if it cannot.
        """
        _mark(request, OP_CANCEL)
        checked = _task_id(task_id)
        _note(request, checked)
        payload = await _bridge_body(
            request, allowed={"client_request_id", "reason"}
        )
        reason = payload.get("reason")
        if reason is not None and reason not in CANCEL_REASONS:
            raise BridgeError(
                code=CODE_INVALID_REQUEST,
                message="'reason' must be one of: " + ", ".join(sorted(CANCEL_REASONS)),
                status_code=status_for(CODE_INVALID_REQUEST),
            )
        request_key = _request_key(payload)

        fresh, _ = _claim(OP_CANCEL, "task:" + checked, request_key, payload)
        if not fresh:
            snapshot = await run_in_threadpool(internal_client.get_task, checked)
            _note(request, checked, replayed=True)
            return _ok(mutation_view(snapshot.get("task"), replayed=True))

        try:
            result = await run_in_threadpool(internal_client.cancel_task, checked)
        except BaseException:
            store.release(
                operation=OP_CANCEL, scope="task:" + checked, request_id=request_key
            )
            raise
        store.settle(
            operation=OP_CANCEL,
            scope="task:" + checked,
            request_id=request_key,
            task_id=checked,
        )
        return _ok(mutation_view(result.get("task"), replayed=False))

    # -- 8. finish_task -------------------------------------------------------

    @app.post(
        "/v1/tasks/{task_id}/finish", dependencies=[Depends(require_bridge_key)]
    )
    async def finish_task(request: Request, task_id: str):
        """Close a retained session on purpose, and complete the task.

        The honest way out of a turn that succeeded. It releases the provider
        session through Task Core; it deletes no history and no result, and it
        touches no task but the one named in the path.
        """
        _mark(request, OP_FINISH)
        checked = _task_id(task_id)
        _note(request, checked)
        payload = await _bridge_body(request, allowed={"client_request_id"})
        request_key = _request_key(payload)

        fresh, _ = _claim(OP_FINISH, "task:" + checked, request_key, payload)
        if not fresh:
            snapshot = await run_in_threadpool(internal_client.get_task, checked)
            _note(request, checked, replayed=True)
            return _ok(mutation_view(snapshot.get("task"), replayed=True))

        try:
            result = await run_in_threadpool(internal_client.finish_task, checked)
        except BaseException:
            store.release(
                operation=OP_FINISH, scope="task:" + checked, request_id=request_key
            )
            raise
        store.settle(
            operation=OP_FINISH,
            scope="task:" + checked,
            request_id=request_key,
            task_id=checked,
        )
        return _ok(mutation_view(result.get("task"), replayed=False))

    return app


#: A closed list, and the whole vocabulary a caller may give for stopping a
#: task. Free text here would be a field that reaches a log or an audit record
#: carrying whatever a model decided to write.
CANCEL_REASONS = frozenset(
    {
        "user_changed_mind",
        "wrong_project",
        "wrong_instruction",
        "taking_too_long",
        "no_longer_needed",
    }
)


__all__ = [
    "CANCEL_REASONS",
    "MUTATIONS",
    "OPERATION_IDS",
    "SAFE_HEADERS",
    "create_bridge_app",
]
