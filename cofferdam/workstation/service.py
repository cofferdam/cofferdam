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
``GET  /api/audio``                          yes   live audio snapshot
``GET  /api/audio/{resource_kind}``          yes   outputs or streams
``PUT  /api/audio/outputs/{id}/default``     yes   choose the default output
``PUT  /api/audio/outputs/{id}/volume``      yes   set that output's volume
``PUT  /api/audio/outputs/{id}/mute``        yes   mute or unmute that output
``PUT  /api/audio/streams/{id}/output``      yes   always refused on this host
``GET  /api/youtube/player``                 yes   dedicated player + queue state
``GET  /api/youtube/activity``               yes   phase of a slow player operation
``POST /api/youtube/player/open``            yes   open the one player on the host
``POST /api/youtube/player/{operation}``     yes   pause/resume/next/previous
``PUT  /api/youtube/player/volume``          yes   the *player's* volume, 0-100
``PUT  /api/youtube/player/mute``            yes   the *player's* mute
``DEL  /api/youtube/player/queue``           yes   clear the Cofferdam queue
``DEL  /api/youtube/player/queue/{id}``      yes   remove one queued video
``POST /api/media/searches/{sid}/results/{rid}/youtube/play``   yes  play it here
``POST /api/media/searches/{sid}/results/{rid}/youtube/queue``  yes  queue it here
``GET  /api/tasks``                          yes   agent task list, bounded
``POST /api/tasks``                          yes   create one task
``GET  /api/tasks/{task_id}``                yes   one task, with its prompt
``GET  /api/tasks/{task_id}/events``         yes   append-only history, paged
``POST /api/tasks/{task_id}/followups``      yes   answer a waiting task
``POST /api/tasks/{task_id}/cancel``         yes   ask that task's adapter to stop
``GET  /api/task-adapters``                  yes   registered adapters + capabilities
``GET  /api/task-projects``                  yes   configured projects, names only
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

**The audio routes (M2C) are the first ones that change the physical state of
the machine** — the volume in the room changes when they are called. They are
therefore the narrowest surface in this file. A client may send exactly three
kinds of value: a runtime ``resource_id`` in the path, an integer percentage,
and a boolean. There is no field for a node id, a device name, a PipeWire
property, a profile, a command, or a program, and those are absent from the
schemas rather than validated and rejected. Reads are ``GET`` and change
nothing; every change is a ``PUT`` with a JSON body, a bounded length, and a
strict field set.

**The Spotify routes (M2D) control someone else's account**, so the client's
vocabulary is narrower still: an opaque device handle, a search id, a result id,
an integer, and a boolean. There is no field for a Spotify URI, a track id, a
device id, an access token, an authorization code, or a redirect URI — a track
is named by *which search result it was*, and the server rebuilds the URI from
its own session. The OAuth callback is **not** served by this application: it is
a separate loopback-only listener that exists for a few minutes during
authorization and is unreachable from the tailnet. See
:mod:`cofferdam.workstation.spotifyplayer.callback`.

**The YouTube player routes (M2E) control a browser tab on this machine**, and
their vocabulary is narrower still: a search id, a result id, a queue item
handle, an integer and a boolean. There is no field for a YouTube URL, a watch
URL, a video id, an iframe source, a player command string, JavaScript, a
browser tab id, or an executable path. A video is named by *which search result
it was*, exactly as a Spotify track is, and Next is named by *which item of
Cofferdam's own queue it is* — never by anything YouTube suggested.

The player document is **not** served by this application either. It lives on a
second loopback-only listener that binds lazily, serves two fixed paths, carries
no token, and is unreachable from the tailnet. See
:mod:`cofferdam.workstation.youtubeplayer.endpoint` for that trust boundary and
what it is worth.

**The task routes (M2F) are the first that could run something on request**, and
their vocabulary is accordingly the most deliberate in this file. A client may
send a project id, an adapter id, a prompt, a short title, and an opaque retry
key. There is no field for a working directory, a filesystem path, an
executable, argv, an environment, a shell string, a pid, a systemd unit, an API
key, a callback URL, or an origin — none of those are validated and rejected,
they are simply absent, and a body carrying one is refused rather than filtered.

The prompt is *content for an adapter*, never an OS command: Task Core runs no
shell, no process and no model, and what a task actually does is entirely the
adapter's business. Where it runs is resolved server-side from a host-owned
project registry, so a phone names a project and never a path. See
:mod:`cofferdam.workstation.tasks` and ``docs/AGENT_TASK_CORE.md``.
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
from .audio import AudioActionExecutor, AudioActionRejected, AudioInventoryService
from .audio.actions import (
    REJECT_GRAPH_CHANGED,
    REJECT_INVALID_MUTE,
    REJECT_INVALID_VOLUME,
    REJECT_RESOURCE_CHANGED,
    REJECT_UNAVAILABLE,
    REJECT_UNSUPPORTED,
)
from .audio.actions import REJECT_UNKNOWN_RESOURCE as REJECT_AUDIO_UNKNOWN_RESOURCE
from .audio.models import AUDIO_RESOURCE_KINDS
from .browser_selection import PRODUCT_DEFAULT_BROWSER
from .sessions.errors import (
    CODE_BACKEND_REFUSED,
    CODE_BACKEND_UNAVAILABLE,
    CODE_LINK_UNAVAILABLE,
    CODE_NOT_ENABLED,
    CODE_STATE_UNAVAILABLE,
    RemoteControlError,
)
from .sessions.errors import CODE_PROJECT_DISABLED as CODE_RC_PROJECT_DISABLED
from .sessions.errors import CODE_PROJECT_ROOT_INVALID as CODE_RC_ROOT_INVALID
from .sessions.errors import CODE_PROJECT_UNKNOWN as CODE_RC_PROJECT_UNKNOWN
from .sessions.supervisor import RemoteControlSupervisor
from .spotifyplayer import AuthorizationRunner, SpotifyActionExecutor, SpotifyPlayerService
from .spotifyplayer.coldstart import DeviceRecovery, SpotifyLauncher
from .spotifyplayer.errors import (
    CODE_DEVICE_AMBIGUOUS,
    CODE_DEVICE_RESTRICTED,
    CODE_DEVICE_UNKNOWN,
    CODE_INVALID_VOLUME,
    CODE_LAUNCH_FAILED,
    CODE_NO_DEVICE_AFTER_LAUNCH,
    CODE_PLAYBACK_NOT_OBSERVED,
    CODE_MISSING_SCOPES,
    CODE_NOT_CONNECTED,
    CODE_NO_ACTIVE_DEVICE,
    CODE_PREMIUM_REQUIRED,
    CODE_PROVIDER_REJECTED,
    CODE_PROVIDER_UNAVAILABLE,
    CODE_RATE_LIMITED,
    CODE_RESULT_NOT_PLAYABLE,
    CODE_UNMUTE_UNKNOWN,
    CODE_VOLUME_UNSUPPORTED,
    SpotifyPlayerError,
)
from .youtubeplayer import PlayerService, YouTubeActionExecutor
from .youtubeplayer.errors import (
    CODE_BUSY as CODE_YOUTUBE_BUSY,
    CODE_COMMAND_NOT_ACKNOWLEDGED,
    CODE_EMBEDDING_REFUSED,
    CODE_EMBED_IDENTITY_REJECTED,
    CODE_INVALID_MUTE as CODE_YOUTUBE_INVALID_MUTE,
    CODE_INVALID_VOLUME as CODE_YOUTUBE_INVALID_VOLUME,
    CODE_LAUNCH_FAILED as CODE_YOUTUBE_LAUNCH_FAILED,
    CODE_MUTE_NOT_OBSERVED,
    CODE_NO_NEXT_ITEM,
    CODE_NO_PLAYER,
    CODE_NO_PREVIOUS_ITEM,
    CODE_PLAYER_ERROR,
    CODE_PLAYER_GONE,
    CODE_QUEUE_FULL,
    CODE_QUEUE_ITEM_UNKNOWN,
    CODE_REGISTRATION_TIMEOUT,
    CODE_RESULT_NOT_PLAYABLE as CODE_YOUTUBE_RESULT_NOT_PLAYABLE,
    CODE_TRANSPORT_NOT_OBSERVED,
    CODE_UNAVAILABLE as CODE_YOUTUBE_UNAVAILABLE,
    CODE_VIDEO_NOT_OBSERVED,
    CODE_VIDEO_UNAVAILABLE,
    CODE_VOLUME_NOT_OBSERVED,
    CODE_WRONG_PROVIDER,
    YouTubePlayerError,
)
from .media import KIND_NATIVE_APP, MAX_QUERY_LENGTH
from .media import catalogue as media_catalogue
from .mediasearch.errors import (
    CODE_RESULT_NOT_FOUND,
    CODE_SEARCH_EXPIRED,
    CODE_SEARCH_NOT_FOUND,
)
from .mediasearch.results import MAX_RESULTS, MEDIA_RESULT_MODEL_VERSION
from .mediasearch.service import MediaSearchService
from .mediasearch.sessions import SEARCH_SESSION_TTL_SECONDS
from .config import Config, load_config, load_or_create_token
from .errors import (
    CODE_ADAPTER_FAILED,
    CODE_CONFIGURATION_INVALID,
    CODE_INTERNAL,
    CODE_INVALID_PARAMS,
    CODE_NOT_FOUND,
    CODE_UNAUTHORIZED,
    AdapterError,
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
from .tasks import TaskService, TaskStore, build_registry as build_task_adapters
from .tasks.errors import (
    CODE_ADAPTER_FAILED as CODE_TASK_ADAPTER_FAILED,
    CODE_ADAPTER_NOT_PERMITTED,
    CODE_ADAPTER_UNKNOWN as CODE_TASK_ADAPTER_UNKNOWN,
    CODE_CANCEL_UNSUPPORTED,
    CODE_FOLLOWUP_INVALID,
    CODE_FOLLOWUP_NOT_WAITING,
    CODE_FOLLOWUP_UNSUPPORTED,
    CODE_IDEMPOTENCY_CONFLICT,
    CODE_ILLEGAL_TRANSITION as CODE_TASK_ILLEGAL_TRANSITION,
    CODE_PROJECT_DISABLED,
    CODE_PROJECT_ROOT_INVALID,
    CODE_PROJECT_UNKNOWN,
    CODE_PROMPT_INVALID,
    CODE_REQUEST_ID_INVALID,
    CODE_STORE_UNAVAILABLE,
    CODE_TASK_TERMINAL,
    CODE_TASK_UNKNOWN,
    TaskError,
)
from .tasks.lifecycle import IllegalTransition
from .tasks.models import (
    BUCKETS,
    DEFAULT_EVENT_PAGE,
    DEFAULT_TASK_PAGE,
    MAX_EVENT_PAGE,
    MAX_TASK_PAGE,
    ORIGIN_PWA,
    TASK_API_VERSION,
)

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

# An audio request body is one number or one boolean. Two kilobytes is already
# far more than that shape can need, and the body is refused on length before it
# is parsed.
MAX_AUDIO_BODY_BYTES = 2 * 1024

# Refusal code -> HTTP status. A resource that is no longer there is 404; one
# that changed underneath the request is 409, because retrying against a fresh
# snapshot is exactly the right response; a request that was wrong is 422; a
# capability this host does not have is 501, which distinguishes "not built" from
# "temporarily broken".
_AUDIO_STATUS = {
    REJECT_AUDIO_UNKNOWN_RESOURCE: 404,
    REJECT_RESOURCE_CHANGED: 409,
    REJECT_GRAPH_CHANGED: 409,
    REJECT_UNAVAILABLE: 503,
    REJECT_INVALID_VOLUME: 422,
    REJECT_INVALID_MUTE: 422,
    REJECT_UNSUPPORTED: 501,
}


# A Spotify request body is a handle, an integer, or a boolean.
MAX_SPOTIFY_BODY_BYTES = 2 * 1024

# Refusal code -> HTTP status. 409 for "the world moved, refresh and retry",
# 402 for the one case that is genuinely about the account's plan, 429 for
# provider rate limiting so a client can back off on the right signal.
_SPOTIFY_STATUS = {
    CODE_NOT_CONNECTED: 409,
    CODE_MISSING_SCOPES: 409,
    CODE_PREMIUM_REQUIRED: 402,
    CODE_NO_ACTIVE_DEVICE: 409,
    # M2D.1 cold-start recovery. 409 throughout: every one of these is "the
    # world is not in a state where this can happen yet", which a client fixes
    # by choosing something or trying again — not a malformed request.
    CODE_NO_DEVICE_AFTER_LAUNCH: 409,
    CODE_DEVICE_AMBIGUOUS: 409,
    CODE_LAUNCH_FAILED: 409,
    CODE_PLAYBACK_NOT_OBSERVED: 409,
    CODE_DEVICE_UNKNOWN: 404,
    CODE_DEVICE_RESTRICTED: 409,
    CODE_VOLUME_UNSUPPORTED: 409,
    CODE_INVALID_VOLUME: 422,
    CODE_UNMUTE_UNKNOWN: 409,
    CODE_RESULT_NOT_PLAYABLE: 422,
    CODE_RATE_LIMITED: 429,
    CODE_PROVIDER_REJECTED: 502,
    CODE_PROVIDER_UNAVAILABLE: 503,
}

# A YouTube player request body is a queue handle, an integer, or a boolean.
MAX_YOUTUBE_BODY_BYTES = 2 * 1024

# Refusal code -> HTTP status. 409 dominates for the same reason it does in the
# Spotify table: most of these mean "the world is not in a state where this can
# happen yet", which a client fixes by trying again or by opening the player —
# not by sending a different request. 422 is reserved for a request that was
# genuinely wrong, 501 for a host that cannot do this at all.
_YOUTUBE_STATUS = {
    CODE_YOUTUBE_UNAVAILABLE: 501,
    CODE_NO_PLAYER: 409,
    CODE_PLAYER_GONE: 409,
    CODE_YOUTUBE_LAUNCH_FAILED: 409,
    CODE_REGISTRATION_TIMEOUT: 504,
    CODE_COMMAND_NOT_ACKNOWLEDGED: 504,
    CODE_YOUTUBE_BUSY: 409,
    # Observation failures. A command was delivered and the player did not end
    # up in the state it asked for; retrying against fresh state is the right
    # response, which is what 409 tells a client.
    CODE_VIDEO_NOT_OBSERVED: 409,
    CODE_VOLUME_NOT_OBSERVED: 409,
    CODE_MUTE_NOT_OBSERVED: 409,
    CODE_TRANSPORT_NOT_OBSERVED: 409,
    # What YouTube itself refused. 422 rather than 5xx: nothing is broken, the
    # video simply cannot play here, and the answer is Open in YouTube.
    CODE_VIDEO_UNAVAILABLE: 422,
    CODE_EMBEDDING_REFUSED: 422,
    CODE_PLAYER_ERROR: 422,
    # YouTube refused the embed because the player page did not identify itself
    # — error 153. 409 rather than 422, because the request was not wrong and the
    # video is fine: the *player* is in a state this cannot happen from, and a
    # reloaded player answers the same request successfully. It is deliberately
    # not grouped with the three above, whose answer is "this video will never
    # play here".
    CODE_EMBED_IDENTITY_REJECTED: 409,
    # Queue and authority.
    CODE_QUEUE_FULL: 409,
    CODE_QUEUE_ITEM_UNKNOWN: 404,
    CODE_NO_NEXT_ITEM: 409,
    CODE_NO_PREVIOUS_ITEM: 409,
    CODE_YOUTUBE_RESULT_NOT_PLAYABLE: 422,
    CODE_WRONG_PROVIDER: 422,
    CODE_YOUTUBE_INVALID_VOLUME: 422,
    CODE_YOUTUBE_INVALID_MUTE: 422,
}

# The search-session refusals a Spotify result action can hit. Same codes the
# existing open-result path already returns, so the PWA has one thing to branch
# on — "that result list has expired, search again" — however the result was
# being used.
_MEDIA_SEARCH_STATUS = {
    CODE_SEARCH_NOT_FOUND: 404,
    CODE_SEARCH_EXPIRED: 409,
    CODE_RESULT_NOT_FOUND: 404,
}

# A task request body is a project id, an adapter id, a prompt and two short
# opaque strings. The prompt bound is 8000 characters, so 32 KB leaves generous
# room for multi-byte text without leaving room for anything else.
MAX_TASK_BODY_BYTES = 32 * 1024

# Refusal code -> HTTP status for Task Core.
#
# The split that matters here is 404/409/422. 404 is "there is no such thing".
# 409 is "the world is not in a state where this can happen" — a task that has
# finished, a follow-up to something that is not waiting, a transition the graph
# refuses. 422 is "the request itself was wrong", which is where every content
# and identifier problem lands. A client can distinguish "try again later" from
# "send something different" without reading the message.
#: Remote Control refusal codes to HTTP status. Same shape as _TASK_STATUS.
#:
#: 404 for "no such project" so an unknown id is indistinguishable from one the
#: caller may not use. 409 for states the world is in rather than the request
#: being malformed — turned off, capability not granted, no live link. 503 when
#: the machinery itself is unavailable, because that one is worth retrying and
#: the others are not.
_REMOTE_CONTROL_STATUS = {
    CODE_RC_PROJECT_UNKNOWN: 404,
    CODE_RC_PROJECT_DISABLED: 409,
    CODE_RC_ROOT_INVALID: 409,
    CODE_NOT_ENABLED: 409,
    CODE_LINK_UNAVAILABLE: 409,
    CODE_BACKEND_REFUSED: 502,
    CODE_BACKEND_UNAVAILABLE: 503,
    CODE_STATE_UNAVAILABLE: 503,
}

_TASK_STATUS = {
    CODE_TASK_UNKNOWN: 404,
    CODE_PROJECT_UNKNOWN: 404,
    CODE_TASK_ADAPTER_UNKNOWN: 404,
    CODE_PROJECT_DISABLED: 409,
    CODE_PROJECT_ROOT_INVALID: 409,
    CODE_ADAPTER_NOT_PERMITTED: 422,
    CODE_PROMPT_INVALID: 422,
    CODE_FOLLOWUP_INVALID: 422,
    CODE_REQUEST_ID_INVALID: 422,
    # Same key, different payload. 409, because both answers a server could
    # invent — the old task, or a second one — would be wrong.
    CODE_IDEMPOTENCY_CONFLICT: 409,
    CODE_TASK_ILLEGAL_TRANSITION: 409,
    CODE_TASK_TERMINAL: 409,
    CODE_FOLLOWUP_NOT_WAITING: 409,
    CODE_FOLLOWUP_UNSUPPORTED: 422,
    CODE_CANCEL_UNSUPPORTED: 422,
    CODE_TASK_ADAPTER_FAILED: 502,
    CODE_STORE_UNAVAILABLE: 503,
}


def _error_response(error: ApiError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content=error.to_payload())


def create_app(
    config: Optional[Config] = None,
    token: Optional[str] = None,
    adapter=None,
    inventory=None,
    media_search=None,
    audio=None,
    spotify=None,
    youtube_player=None,
    tasks=None,
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
    # The audio service gets the adapter for the same reason the inventory does:
    # its launch table is what turns a kernel-verified process behind a playback
    # stream into "this is Spotify". Without it every stream stays unclassified,
    # which is a degradation and not a failure.
    audio = audio or AudioInventoryService(adapter=adapter)
    audio_actions = AudioActionExecutor(audio)

    def _on_event(event: str, record: ActionRecord) -> None:
        hub.broadcast_threadsafe(event, record.to_dict())

    # M2B3A.1. One instance per service, so its search sessions outlive a single
    # request and die with the process — which is the intended lifetime, since a
    # session is a short-lived memory of what someone was looking for.
    media_search = media_search or MediaSearchService(config)
    # The Spotify player reuses the catalogue credential store for the client id
    # only — PKCE needs no secret, so the secret in that file never enters the
    # authorization path — and the *existing* search sessions as the sole
    # authority for what a "track" is. No second catalogue search exists.
    spotify = spotify or SpotifyPlayerService(config, media_search.credentials)
    # Cold-start recovery (M2D.1). The launcher is the *existing* allowlisted
    # application path — the same one the Media panel's "Open" button uses — so
    # playback gains no new way to start a process and constructs no command of
    # its own. Recovery is what makes "press Play with Spotify closed" work.
    spotify_recovery = DeviceRecovery(spotify, SpotifyLauncher(adapter))
    spotify_actions = SpotifyActionExecutor(
        spotify, media_search.sessions, recovery=spotify_recovery
    )
    spotify_authorize = AuthorizationRunner(spotify, adapter)

    # The YouTube dedicated player (M2E). It takes the *existing* search sessions
    # as the sole authority for what a video is — no second YouTube catalogue
    # exists — and the existing allowlisted browser launcher for opening its one
    # player tab. Its loopback listener is not bound here: it binds lazily the
    # first time a player is actually opened, so a host where nobody uses this
    # never opens a socket.
    youtube_player = youtube_player or PlayerService(adapter)
    youtube_actions = YouTubeActionExecutor(youtube_player, media_search.sessions)

    # Agent Task Core (M2F). The adapter table is built from *server-side*
    # configuration and nothing else: on a default install it is empty, so the
    # task system is fully present with nothing registered to run in it. The
    # validation adapter appears only when the host was explicitly configured to
    # allow it, and there is no route that changes that.
    if tasks is None:
        tasks = TaskService(
            config,
            TaskStore(config),
            build_task_adapters(
                enable_validation_adapter=config.enable_validation_task_adapter,
                enable_claude_code_adapter=config.enable_claude_code_adapter,
            ),
            # The audit hook takes ids and outcome words only — see
            # store.record_task_event for why there is no content parameter.
            audit=store.record_task_event,
        )
        # Settle anything the database still believes is unfinished, before the
        # first request can read it. Nothing is resumed: a row saying "running"
        # describes a process that no longer exists, and reporting it as running
        # would be the first lie the whole milestone exists to prevent.
        tasks.recover_after_restart()

    # Native Remote Control (M2H, Lane A). Constructed unconditionally because
    # it holds no process and starts nothing: every operation is refused unless
    # the named project is registered, enabled, and — for start — has explicitly
    # set `remote_control_enabled`. It shares the project registry with Task
    # Core and nothing else; Lane B's task lifecycle is untouched by it.
    remote_control = RemoteControlSupervisor(
        lambda: tasks.projects,
        config=config,
    )

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
    app.state.audio = audio
    app.state.audio_actions = audio_actions
    app.state.spotify = spotify
    app.state.spotify_actions = spotify_actions
    app.state.spotify_authorize = spotify_authorize
    app.state.spotify_recovery = spotify_recovery
    app.state.youtube_player = youtube_player
    app.state.youtube_actions = youtube_actions
    app.state.tasks = tasks
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
                # Task health is deliberately *not* here. It would be a second
                # place to ask "which adapters exist", and /api/task-adapters
                # already answers that authoritatively — a status payload that
                # duplicates another route's answer is one that can disagree
                # with it. See TaskService.health for what is available.
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

    # -- audio (M2C: the first routes that change the physical machine) -------
    #
    # Reads are GET and change nothing. Changes are PUT, because each one sets a
    # named property of a named resource to a supplied value — the shape PUT
    # describes. There is no GET that mutates, and no mutation reachable without
    # a body.

    @app.get("/api/audio", dependencies=[Depends(require_token)])
    async def audio_snapshot(refresh: bool = False) -> Dict[str, Any]:
        """One observation of this machine's audio: outputs, streams, defaults.

        Reading the graph shells out to ``pw-dump``, so it runs in a worker
        thread rather than blocking the event loop.
        """
        snapshot = await run_in_threadpool(audio.snapshot, refresh)
        return snapshot.to_dict()

    @app.get("/api/audio/{resource_kind}", dependencies=[Depends(require_token)])
    async def audio_collection(resource_kind: str, refresh: bool = False) -> Dict[str, Any]:
        """One collection, served with the snapshot header it belongs to.

        The header carries the graph identity, which is what tells a client
        whether the resource ids it is holding still mean anything.
        """
        if resource_kind not in AUDIO_RESOURCE_KINDS:
            raise ApiError(
                code=CODE_NOT_FOUND,
                message="unknown audio resource kind",
                status_code=404,
                detail="known kinds: " + ", ".join(AUDIO_RESOURCE_KINDS),
            )
        snapshot, collection = await run_in_threadpool(audio.collection, resource_kind, refresh)
        return {
            "version": snapshot.version,
            "observed_at": snapshot.observed_at,
            "host": dict(snapshot.host),
            "boot": dict(snapshot.boot),
            "graph": dict(snapshot.graph),
            "backend": snapshot.backend,
            "default_output_resource_id": snapshot.default_output_resource_id,
            "collection": collection.to_dict(),
        }

    async def _audio_body(request: Request, allowed: set) -> Dict[str, Any]:
        """Read a bounded JSON body, or refuse before parsing anything.

        ``allowed`` is the complete set of acceptable keys for the route. A body
        carrying anything else is refused rather than filtered: a request with a
        ``node_id``, a ``command`` or a ``sink`` field is a client trying to
        address the backend directly, and silently dropping it would teach that
        client the attempt was fine.
        """
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
                too_big = int(declared) > MAX_AUDIO_BODY_BYTES
            except ValueError:
                raise ApiError(
                    code=CODE_INVALID_PARAMS, message="invalid Content-Length", status_code=400
                )
            if too_big:
                raise ApiError(
                    code=CODE_INVALID_PARAMS,
                    message="the request body is too large",
                    status_code=413,
                )
        raw = await request.body()
        # Checked again after reading: Content-Length is a claim, not a fact.
        if len(raw) > MAX_AUDIO_BODY_BYTES:
            raise ApiError(
                code=CODE_INVALID_PARAMS, message="the request body is too large", status_code=413
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
        unexpected = sorted(set(payload) - allowed)
        if unexpected:
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="unexpected field: " + unexpected[0],
                status_code=422,
                detail="this endpoint accepts only: " + ", ".join(sorted(allowed))
                if allowed
                else "this endpoint accepts no fields",
            )
        return payload

    async def _run_audio(name: str, operation, resource_id: str, *args) -> Dict[str, Any]:
        """Run one audio action, auditing the outcome either way.

        ``name`` is passed explicitly so a refused action — which never reaches
        the executor's result envelope — is still audited under the operation
        the user actually attempted.
        """
        try:
            result = await run_in_threadpool(operation, resource_id, *args)
        except AudioActionRejected as rejection:
            store.record_audio_event(name, resource_id, rejection.code)
            raise ApiError(
                code=rejection.code,
                message=rejection.message,
                status_code=_AUDIO_STATUS.get(rejection.code, 422),
                detail=rejection.detail,
            )
        # The audit records the *observed* outcome, not the fact that a command
        # was issued: an action that ran and did not take effect is recorded as
        # the failure it was.
        outcome = result.get("outcome")
        store.record_audio_event(
            name,
            resource_id,
            "ok" if outcome == "applied" else str(outcome),
            (result.get("output") or {}).get("device_type"),
        )
        return result

    @app.put("/api/audio/outputs/{resource_id}/default", dependencies=[Depends(require_token)])
    async def put_audio_default(resource_id: str, request: Request) -> Dict[str, Any]:
        """Make one discovered output the default for new sound.

        The body carries no fields — the resource is in the path and the
        operation is the route. It is still read and validated so that a client
        sending something is told, rather than having it quietly ignored.
        """
        await _audio_body(request, allowed=set())
        return await _run_audio(
            "set_default_audio_output", audio_actions.set_default_output, resource_id
        )

    @app.put("/api/audio/outputs/{resource_id}/volume", dependencies=[Depends(require_token)])
    async def put_audio_volume(resource_id: str, request: Request) -> Dict[str, Any]:
        """Set one output's volume, as a whole percentage from 0 to 100."""
        payload = await _audio_body(request, allowed={"volume_percent"})
        if "volume_percent" not in payload:
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="volume_percent is required",
                status_code=422,
            )
        return await _run_audio(
            "set_output_volume",
            audio_actions.set_output_volume,
            resource_id,
            payload["volume_percent"],
        )

    @app.put("/api/audio/outputs/{resource_id}/mute", dependencies=[Depends(require_token)])
    async def put_audio_mute(resource_id: str, request: Request) -> Dict[str, Any]:
        """Mute or unmute one output."""
        payload = await _audio_body(request, allowed={"muted"})
        if "muted" not in payload:
            raise ApiError(
                code=CODE_INVALID_PARAMS, message="muted is required", status_code=422
            )
        return await _run_audio(
            "set_output_mute", audio_actions.set_output_mute, resource_id, payload["muted"]
        )

    @app.put("/api/audio/streams/{resource_id}/output", dependencies=[Depends(require_token)])
    async def put_audio_stream_output(resource_id: str, request: Request) -> Dict[str, Any]:
        """Move a playing stream to another output — refused on this backend.

        The route exists so the refusal is a documented ``501`` with a reason a
        person can read, rather than a ``404`` that looks like a bug. See
        :mod:`cofferdam.workstation.audio.actions` for why it is not implemented.
        """
        payload = await _audio_body(request, allowed={"output_resource_id"})
        try:
            return await run_in_threadpool(
                audio_actions.move_stream, resource_id, payload.get("output_resource_id")
            )
        except AudioActionRejected as rejection:
            store.record_audio_event("move_audio_stream", resource_id, rejection.code)
            raise ApiError(
                code=rejection.code,
                message=rejection.message,
                status_code=_AUDIO_STATUS.get(rejection.code, 422),
                detail=rejection.detail,
            )


    # -- Spotify playback (M2D: control of the user's own account) ------------
    #
    # Narrower than any surface above it. A client may send an opaque device
    # handle, a search id, a result id, an integer and a boolean. It may not
    # send a Spotify URI, a track id, a device id, a token, an authorization
    # code, or a redirect URI: none of those is a field in any schema here.

    async def _spotify_body(request: Request, allowed: set) -> Dict[str, Any]:
        """A bounded JSON body with a strict field set, or a refusal."""
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
                too_big = int(declared) > MAX_SPOTIFY_BODY_BYTES
            except ValueError:
                raise ApiError(
                    code=CODE_INVALID_PARAMS, message="invalid Content-Length", status_code=400
                )
            if too_big:
                raise ApiError(
                    code=CODE_INVALID_PARAMS,
                    message="the request body is too large",
                    status_code=413,
                )
        raw = await request.body()
        if len(raw) > MAX_SPOTIFY_BODY_BYTES:
            raise ApiError(
                code=CODE_INVALID_PARAMS, message="the request body is too large", status_code=413
            )
        if not raw:
            payload: Any = {}
        else:
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
        unexpected = sorted(set(payload) - allowed)
        if unexpected:
            # Refused, not ignored. A body carrying `uri`, `device_id`,
            # `access_token` or `code` is a client trying to address Spotify
            # directly, and dropping the field silently would teach it that the
            # attempt was acceptable.
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="unexpected field: " + unexpected[0],
                status_code=422,
                detail=(
                    "this endpoint accepts only: " + ", ".join(sorted(allowed))
                    if allowed
                    else "this endpoint accepts no fields"
                ),
            )
        return payload

    async def _run_spotify(name: str, operation, *args) -> Dict[str, Any]:
        """Run one Spotify action, auditing the operation and its outcome only.

        The audit deliberately records no track, artist, album or query — see
        store.record_spotify_event.
        """
        try:
            result = await run_in_threadpool(operation, *args)
        except SpotifyPlayerError as rejection:
            store.record_spotify_event(name, rejection.code)
            raise ApiError(
                code=rejection.code,
                message=rejection.message,
                status_code=_SPOTIFY_STATUS.get(rejection.code, 422),
                detail=rejection.detail,
            )
        except AdapterError as rejection:
            # The search-session refusals: expired session, unknown result, and
            # the cross-provider check that stops a YouTube result being routed
            # into the Spotify player. They come from the *existing* media-search
            # layer — this milestone deliberately built no second one — and they
            # already carry the right codes, so they are mapped rather than
            # rewritten. Without this they would fall through to the catch-all
            # handler and reach the phone as "internal error", which would send
            # someone hunting for a bug instead of pressing Search again.
            code = getattr(rejection, "code", CODE_ADAPTER_FAILED)
            store.record_spotify_event(name, code)
            raise ApiError(
                code=code,
                message=rejection.message,
                status_code=_MEDIA_SEARCH_STATUS.get(code, 409),
                detail=rejection.detail,
            )
        outcome = result.get("outcome")
        store.record_spotify_event(
            name,
            "ok" if outcome in ("applied", "accepted_by_provider") else str(outcome),
            correlation_id=result.get("correlation_id"),
        )
        return result

    @app.get("/api/spotify/playback", dependencies=[Depends(require_token)])
    async def spotify_playback(refresh: bool = False) -> Dict[str, Any]:
        """Connection status, playback state, and Connect devices.

        Reading talks to Spotify, so it runs in a worker thread. It changes
        nothing: no GET in this group has a side effect.
        """
        snapshot = await run_in_threadpool(spotify.snapshot, refresh)
        payload = snapshot.to_dict()
        payload["authorization"] = spotify_authorize.status()
        return payload

    @app.get("/api/spotify/activity", dependencies=[Depends(require_token)])
    async def spotify_activity() -> Dict[str, Any]:
        """What a long Spotify operation is doing right now. No provider call.

        Cold-start recovery can take twenty seconds — open Spotify, wait for its
        device, transfer to it, start the track, confirm it started — and a phone
        showing a spinner for that long is indistinguishable from a phone that has
        hung. This is what the PWA polls meanwhile.

        Deliberately free: it reads one in-memory record and touches neither
        Spotify nor the filesystem, so watching a slow operation cannot make the
        rate limit that operation is already fighting any worse. It carries a
        phase from a closed vocabulary, a correlation id, and an elapsed time —
        no track, no device, no account.
        """
        return spotify_actions.activity.snapshot()

    @app.post("/api/spotify/authorize", dependencies=[Depends(require_token)])
    async def spotify_start_authorization(request: Request) -> Dict[str, Any]:
        """Begin one PKCE attempt and open the page in Opera on the workstation.

        Returns immediately with an instruction for the phone; the attempt runs
        in the background and times out on its own. The authorization URL is not
        returned — it can only be completed on the workstation, and handing it to
        a phone would invite a failure that looks like Cofferdam's fault.
        """
        await _spotify_body(request, allowed=set())
        try:
            return await run_in_threadpool(spotify_authorize.start)
        except SpotifyPlayerError as rejection:
            store.record_spotify_event("spotify_authorize", rejection.code)
            raise ApiError(
                code=rejection.code,
                message=rejection.message,
                status_code=_SPOTIFY_STATUS.get(rejection.code, 409),
                detail=rejection.detail,
            )

    @app.delete("/api/spotify/authorize", dependencies=[Depends(require_token)])
    async def spotify_cancel_authorization() -> Dict[str, Any]:
        """Cancel a pending attempt, so a remote user is never stuck waiting."""
        cancelled = await run_in_threadpool(spotify_authorize.cancel)
        return {"cancelled": bool(cancelled), "authorization": spotify_authorize.status()}

    @app.post("/api/spotify/disconnect", dependencies=[Depends(require_token)])
    async def spotify_disconnect(request: Request) -> Dict[str, Any]:
        """Remove the local authorization. This does not revoke at Spotify."""
        await _spotify_body(request, allowed=set())
        return await _run_spotify("spotify_disconnect", spotify_actions.disconnect)

    _SPOTIFY_TRANSPORT = {
        "pause": lambda: spotify_actions.pause(),
        "resume": lambda: spotify_actions.resume(),
        "next": lambda: spotify_actions.skip(True),
        "previous": lambda: spotify_actions.skip(False),
    }

    @app.post("/api/spotify/player/{operation}", dependencies=[Depends(require_token)])
    async def spotify_transport(operation: str, request: Request) -> Dict[str, Any]:
        """Pause, resume, next, previous — each re-read and verified after."""
        if operation not in _SPOTIFY_TRANSPORT:
            raise ApiError(
                code=CODE_NOT_FOUND,
                message="unknown player operation",
                status_code=404,
                detail="known operations: " + ", ".join(sorted(_SPOTIFY_TRANSPORT)),
            )
        await _spotify_body(request, allowed=set())
        return await _run_spotify(
            "spotify_" + operation, _SPOTIFY_TRANSPORT[operation]
        )

    @app.put("/api/spotify/player/volume", dependencies=[Depends(require_token)])
    async def spotify_volume(request: Request) -> Dict[str, Any]:
        """Set Spotify's own device volume. Separate from the computer's."""
        payload = await _spotify_body(request, allowed={"volume_percent", "device_resource_id"})
        if "volume_percent" not in payload:
            raise ApiError(
                code=CODE_INVALID_PARAMS, message="volume_percent is required", status_code=422
            )
        return await _run_spotify(
            "spotify_set_volume",
            spotify_actions.set_volume,
            payload["volume_percent"],
            payload.get("device_resource_id"),
        )

    @app.put("/api/spotify/player/mute", dependencies=[Depends(require_token)])
    async def spotify_mute(request: Request) -> Dict[str, Any]:
        """Mute or unmute Spotify — which means volume zero, and says so."""
        payload = await _spotify_body(request, allowed={"muted", "device_resource_id"})
        if "muted" not in payload:
            raise ApiError(code=CODE_INVALID_PARAMS, message="muted is required", status_code=422)
        return await _run_spotify(
            "spotify_set_mute",
            spotify_actions.set_muted,
            payload["muted"],
            payload.get("device_resource_id"),
        )

    @app.put("/api/spotify/player/device", dependencies=[Depends(require_token)])
    async def spotify_transfer(request: Request) -> Dict[str, Any]:
        """Move Spotify playback to another Connect device.

        This does not change this computer's audio output; that is the Computer
        Audio panel, and the response says so explicitly.
        """
        payload = await _spotify_body(request, allowed={"device_resource_id", "play"})
        if "device_resource_id" not in payload:
            raise ApiError(
                code=CODE_INVALID_PARAMS, message="device_resource_id is required", status_code=422
            )
        play = payload.get("play", False)
        if not isinstance(play, bool):
            raise ApiError(
                code=CODE_INVALID_PARAMS, message="play must be true or false", status_code=422
            )
        return await _run_spotify(
            "spotify_transfer_playback",
            spotify_actions.transfer,
            payload["device_resource_id"],
            play,
        )

    @app.post(
        "/api/media/searches/{search_id}/results/{result_id}/spotify/play",
        dependencies=[Depends(require_token)],
    )
    async def spotify_play_result(
        search_id: str, result_id: str, request: Request
    ) -> Dict[str, Any]:
        """Play the exact track behind a verified Spotify search result.

        The track is named by *which result it was*. The server rebuilds the
        Spotify URI from its own search session, so there is no request field
        for a URI or a track id to validate.
        """
        payload = await _spotify_body(request, allowed={"device_resource_id"})
        return await _run_spotify(
            "spotify_play_search_result",
            spotify_actions.play_search_result,
            search_id,
            result_id,
            payload.get("device_resource_id"),
        )

    @app.post(
        "/api/media/searches/{search_id}/results/{result_id}/spotify/queue",
        dependencies=[Depends(require_token)],
    )
    async def spotify_queue_result(
        search_id: str, result_id: str, request: Request
    ) -> Dict[str, Any]:
        """Add the exact track behind a verified result to the Spotify queue."""
        payload = await _spotify_body(request, allowed={"device_resource_id"})
        return await _run_spotify(
            "spotify_queue_search_result",
            spotify_actions.queue_search_result,
            search_id,
            result_id,
            payload.get("device_resource_id"),
        )

    # -- YouTube dedicated player (M2E) --------------------------------------
    #
    # The narrowest write surface in this file. A client may send exactly four
    # kinds of value: a search id and a result id it was given, a queue item
    # handle it was given, one integer, and one boolean. There is no field for a
    # YouTube URL, a watch URL, a video id, an iframe source, a player command
    # string, JavaScript, a browser tab id, or an executable path — and those
    # are *absent from the schemas* rather than validated and rejected.
    #
    # The player itself is not reachable from here. It talks to a separate
    # loopback-only listener that is never bound to the tailnet; see
    # cofferdam/workstation/youtubeplayer/endpoint.py for that trust boundary.

    async def _youtube_body(request: Request, allowed: set) -> Dict[str, Any]:
        """A bounded JSON body with a strict field set, or a refusal."""
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
                too_big = int(declared) > MAX_YOUTUBE_BODY_BYTES
            except ValueError:
                raise ApiError(
                    code=CODE_INVALID_PARAMS, message="invalid Content-Length", status_code=400
                )
            if too_big:
                raise ApiError(
                    code=CODE_INVALID_PARAMS,
                    message="the request body is too large",
                    status_code=413,
                )
        raw = await request.body()
        # Checked again after reading: Content-Length is a claim, not a fact.
        if len(raw) > MAX_YOUTUBE_BODY_BYTES:
            raise ApiError(
                code=CODE_INVALID_PARAMS, message="the request body is too large", status_code=413
            )
        if not raw:
            payload: Any = {}
        else:
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
        unexpected = sorted(set(payload) - allowed)
        if unexpected:
            # Refused, not ignored. A body carrying `video_id`, `url`, `command`
            # or `script` is a client trying to address the player directly, and
            # dropping the field silently would teach it the attempt was fine.
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="unexpected field: " + unexpected[0],
                status_code=422,
                detail=(
                    "this endpoint accepts only: " + ", ".join(sorted(allowed))
                    if allowed
                    else "this endpoint accepts no fields"
                ),
            )
        return payload

    async def _run_youtube(name: str, operation, *args) -> Dict[str, Any]:
        """Run one player action, auditing the operation and its outcome only.

        The audit deliberately records no video, title, channel or queue content
        — see store.record_youtube_event.
        """
        try:
            result = await run_in_threadpool(operation, *args)
        except YouTubePlayerError as rejection:
            store.record_youtube_event(name, rejection.code)
            raise ApiError(
                code=rejection.code,
                message=rejection.message,
                status_code=_YOUTUBE_STATUS.get(rejection.code, 422),
                detail=rejection.detail,
            )
        except AdapterError as rejection:
            # The search-session refusals — expired session, unknown result, and
            # the cross-provider check that stops a Spotify result reaching the
            # YouTube player. They come from the *existing* media-search layer
            # and already carry the right codes, so they are mapped rather than
            # rewritten; without this they would reach the phone as "internal
            # error" and send someone hunting for a bug instead of searching
            # again.
            code = getattr(rejection, "code", CODE_ADAPTER_FAILED)
            store.record_youtube_event(name, code)
            raise ApiError(
                code=code,
                message=rejection.message,
                status_code=_MEDIA_SEARCH_STATUS.get(code, 409),
                detail=rejection.detail,
            )
        outcome = result.get("outcome")
        store.record_youtube_event(
            name,
            "ok" if outcome in ("applied", "queued") else str(outcome),
            correlation_id=result.get("correlation_id"),
        )
        return result

    @app.get("/api/youtube/player", dependencies=[Depends(require_token)])
    async def youtube_player_state() -> Dict[str, Any]:
        """Connection state, current video, playback, volume and the queue.

        Reads in-memory state only: no provider call, no browser inspection, no
        process scan. It changes nothing — no GET in this group has a side
        effect, and in particular reading this never opens a player.
        """
        return youtube_player.snapshot().to_dict()

    @app.get("/api/youtube/activity", dependencies=[Depends(require_token)])
    async def youtube_activity() -> Dict[str, Any]:
        """What a long player operation is doing right now. Free to poll.

        Opening the player can take twenty seconds — launch Opera, wait for the
        tab, wait for the official API script, load the video, confirm it — and
        a phone showing a spinner for that long is indistinguishable from a
        phone that has hung. This is what the PWA polls meanwhile: one in-memory
        record, a phase from a closed vocabulary, and an elapsed time. No video,
        no title, no queue.
        """
        return youtube_actions.activity.snapshot()

    @app.post("/api/youtube/player/open", dependencies=[Depends(require_token)])
    async def youtube_open_player(request: Request) -> Dict[str, Any]:
        """Open the dedicated player on the workstation, if one is not open.

        Idempotent by design: with a player already connected this opens nothing
        and says so, rather than adding a tab.
        """
        await _youtube_body(request, allowed=set())
        return await _run_youtube("youtube_open_player", youtube_actions.open_player)

    _YOUTUBE_TRANSPORT = {
        "pause": lambda: youtube_actions.pause(),
        "resume": lambda: youtube_actions.resume(),
        "next": lambda: youtube_actions.skip(True),
        "previous": lambda: youtube_actions.skip(False),
    }

    @app.post("/api/youtube/player/{operation}", dependencies=[Depends(require_token)])
    async def youtube_transport(operation: str, request: Request) -> Dict[str, Any]:
        """Pause, resume, next, previous — each re-read and verified after.

        Next and Previous move through the **Cofferdam** queue. They never
        consult YouTube's suggestions, and with nothing queued they refuse
        rather than playing whatever would have come next.
        """
        if operation not in _YOUTUBE_TRANSPORT:
            raise ApiError(
                code=CODE_NOT_FOUND,
                message="unknown player operation",
                status_code=404,
                detail="known operations: " + ", ".join(sorted(_YOUTUBE_TRANSPORT)),
            )
        await _youtube_body(request, allowed=set())
        return await _run_youtube("youtube_" + operation, _YOUTUBE_TRANSPORT[operation])

    @app.put("/api/youtube/player/volume", dependencies=[Depends(require_token)])
    async def youtube_volume(request: Request) -> Dict[str, Any]:
        """Set the YouTube player's own volume. Not this computer's speaker.

        Out-of-range values are refused, never clamped, and the response carries
        the volume the *player* reported afterwards rather than the one that was
        asked for.
        """
        payload = await _youtube_body(request, allowed={"volume_percent"})
        if "volume_percent" not in payload:
            raise ApiError(
                code=CODE_INVALID_PARAMS, message="volume_percent is required", status_code=422
            )
        return await _run_youtube(
            "youtube_set_volume", youtube_actions.set_volume, payload["volume_percent"]
        )

    @app.put("/api/youtube/player/mute", dependencies=[Depends(require_token)])
    async def youtube_mute(request: Request) -> Dict[str, Any]:
        """Mute or unmute the YouTube player through the official player API.

        This is a real mute — the IFrame Player API publishes ``mute()`` and
        ``unMute()`` and preserves the volume across them — so the field is
        plainly ``muted``. It does not touch Computer Audio.
        """
        payload = await _youtube_body(request, allowed={"muted"})
        if "muted" not in payload:
            raise ApiError(code=CODE_INVALID_PARAMS, message="muted is required", status_code=422)
        return await _run_youtube(
            "youtube_set_mute", youtube_actions.set_muted, payload["muted"]
        )

    @app.delete("/api/youtube/player/queue", dependencies=[Depends(require_token)])
    async def youtube_clear_queue() -> Dict[str, Any]:
        """Empty the Cofferdam queue. Does not stop what is playing."""
        return await _run_youtube("youtube_clear_queue", youtube_actions.clear_queue)

    @app.delete(
        "/api/youtube/player/queue/{queue_item_id}", dependencies=[Depends(require_token)]
    )
    async def youtube_remove_queue_item(queue_item_id: str) -> Dict[str, Any]:
        """Remove one queued video by the handle the server issued."""
        return await _run_youtube(
            "youtube_remove_queue_item", youtube_actions.remove_queue_item, queue_item_id
        )

    @app.post(
        "/api/media/searches/{search_id}/results/{result_id}/youtube/play",
        dependencies=[Depends(require_token)],
    )
    async def youtube_play_result(
        search_id: str, result_id: str, request: Request
    ) -> Dict[str, Any]:
        """Play the exact video behind a verified YouTube result.

        The video is named by *which result it was*. The server resolves it from
        its own search session, so there is no request field for a video id or a
        URL to validate. One press is enough: with no player open this opens
        one, waits for it, and continues — it never falls back to a normal watch
        tab, which remains the explicit *Open in YouTube* action.
        """
        await _youtube_body(request, allowed=set())
        return await _run_youtube(
            "youtube_play_search_result",
            youtube_actions.play_search_result,
            search_id,
            result_id,
        )

    @app.post(
        "/api/media/searches/{search_id}/results/{result_id}/youtube/queue",
        dependencies=[Depends(require_token)],
    )
    async def youtube_queue_result(
        search_id: str, result_id: str, request: Request
    ) -> Dict[str, Any]:
        """Add the exact video behind a verified result to the Cofferdam queue.

        Sends no command to the player at all, which is what makes "adding
        something does not interrupt what is playing" structural rather than a
        behaviour to be careful about.
        """
        await _youtube_body(request, allowed=set())
        return await _run_youtube(
            "youtube_queue_search_result",
            youtube_actions.queue_search_result,
            search_id,
            result_id,
        )

    # -- agent tasks (M2F) ---------------------------------------------------

    async def _task_body(request: Request, allowed: set) -> Dict[str, Any]:
        """Read one bounded task request body, refusing anything unexpected.

        The allowlist is the surface. There is deliberately no field for a
        working directory, an executable, argv, an environment, a token, a
        callback URL, a pid or a unit name — those are not validated and
        rejected, they are *absent*, and this check is what turns sending one
        into a refusal rather than a silently ignored key.
        """
        content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type and content_type != "application/json":
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="this endpoint accepts application/json only",
                status_code=415,
            )
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                too_big = int(declared) > MAX_TASK_BODY_BYTES
            except ValueError:
                raise ApiError(
                    code=CODE_INVALID_PARAMS, message="invalid Content-Length", status_code=400
                )
            if too_big:
                raise ApiError(
                    code=CODE_INVALID_PARAMS,
                    message="the request body is too large",
                    status_code=413,
                )
        raw = await request.body()
        # Checked again after reading: Content-Length is a claim, not a fact.
        if len(raw) > MAX_TASK_BODY_BYTES:
            raise ApiError(
                code=CODE_INVALID_PARAMS, message="the request body is too large", status_code=413
            )
        if not raw:
            payload: Any = {}
        else:
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
        unexpected = sorted(set(payload) - allowed)
        if unexpected:
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="unexpected field: " + unexpected[0],
                status_code=422,
                detail=(
                    "this endpoint accepts only: " + ", ".join(sorted(allowed))
                    if allowed
                    else "this endpoint accepts no fields"
                ),
            )
        return payload

    async def _run_task(operation, *args, **kwargs) -> Any:
        try:
            return await run_in_threadpool(operation, *args, **kwargs)
        except TaskError as rejection:
            raise ApiError(
                code=rejection.code,
                message=rejection.message,
                status_code=_TASK_STATUS.get(rejection.code, 422),
                detail=rejection.detail,
            )
        except IllegalTransition as rejection:
            # The graph refused a move. 409 rather than 422: the request was
            # well formed and the *world* is not in a state where it can happen.
            raise ApiError(
                code=CODE_TASK_ILLEGAL_TRANSITION,
                message="that is not something this task can do now",
                status_code=409,
                detail=rejection.reason,
            )

    @app.get("/api/tasks", dependencies=[Depends(require_token)])
    async def list_tasks(
        bucket: Optional[str] = None, limit: int = DEFAULT_TASK_PAGE
    ) -> Dict[str, Any]:
        """The task list. Bounded, newest first, and without task content.

        ``include_content=False`` on every row: a list is a list of rows, and
        carrying each task's full result so a phone can render one line of
        activity would put every answer on the wire on every poll.
        """
        if bucket is not None and bucket not in BUCKETS:
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="unknown task filter",
                status_code=422,
                detail="expected one of: " + ", ".join(BUCKETS),
            )
        rows = await run_in_threadpool(
            tasks.list_tasks,
            bucket=bucket,
            limit=max(1, min(int(limit), MAX_TASK_PAGE)),
        )
        counts = await run_in_threadpool(tasks.store.counts_by_state)
        return {
            "version": TASK_API_VERSION,
            "tasks": [
                tasks.snapshot(row).to_dict(include_content=False) for row in rows
            ],
            "counts": counts,
        }

    @app.post("/api/tasks", dependencies=[Depends(require_token)])
    async def create_task(request: Request) -> JSONResponse:
        """Start one task. The whole client vocabulary is five bounded fields.

        ``origin`` is **not** among them: it is assigned here from the
        authenticated request context, because a client choosing how its own
        request is later attributed is the opposite of what that field is for.
        """
        payload = await _task_body(
            request,
            allowed={"project_id", "adapter_id", "prompt", "client_request_id", "title"},
        )
        row, created = await _run_task(
            tasks.create_task,
            project_id=payload.get("project_id"),
            adapter_id=payload.get("adapter_id"),
            prompt=payload.get("prompt"),
            client_request_id=payload.get("client_request_id"),
            title=payload.get("title"),
            origin=ORIGIN_PWA,
        )
        return JSONResponse(
            # 200 rather than 201 when an idempotency key matched: nothing was
            # created, and the status line is the cheapest place to say so.
            status_code=201 if created else 200,
            content={"task": tasks.snapshot(row).to_dict(), "created": created},
        )

    @app.get("/api/tasks/{task_id}", dependencies=[Depends(require_token)])
    async def get_task(task_id: str) -> Dict[str, Any]:
        # `refresh_task`, not `get_task`. An adapter whose work happens inside a
        # process has to be *asked* what it saw, and opening the detail view is
        # when Cofferdam asks. For a synchronous adapter it is a no-op returning
        # the same row.
        row = await _run_task(tasks.refresh_task, task_id)
        payload = tasks.snapshot(row).to_dict()
        # The detail view is the one place the prompt is published, and only to
        # the authenticated client that already sent it.
        payload["prompt"] = row.prompt
        return {"task": payload}

    @app.get("/api/tasks/{task_id}/events", dependencies=[Depends(require_token)])
    async def get_task_events(
        task_id: str, after: int = 0, limit: int = DEFAULT_EVENT_PAGE
    ) -> Dict[str, Any]:
        """Events after a sequence cursor. Bounded, and never an offset scan."""
        if after < 0:
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="after must not be negative",
                status_code=422,
            )
        row = await _run_task(tasks.get_task, task_id)
        events = await run_in_threadpool(
            tasks.store.events,
            row.task_id,
            after=after,
            limit=max(1, min(int(limit), MAX_EVENT_PAGE)),
        )
        return {
            "task_id": row.task_id,
            "events": [event.to_dict() for event in events],
            "cursor": events[-1].sequence if events else after,
            "event_cursor": row.event_cursor,
        }

    @app.post("/api/tasks/{task_id}/followups", dependencies=[Depends(require_token)])
    async def send_task_followup(task_id: str, request: Request) -> Dict[str, Any]:
        payload = await _task_body(request, allowed={"followup", "client_request_id"})
        row = await _run_task(
            tasks.send_followup,
            task_id,
            payload.get("followup"),
            client_request_id=payload.get("client_request_id"),
        )
        return {"task": tasks.snapshot(row).to_dict()}

    @app.post("/api/tasks/{task_id}/finish", dependencies=[Depends(require_token)])
    async def finish_task(task_id: str, request: Request) -> Dict[str, Any]:
        """Close a retained session on purpose, and complete the task.

        The honest way out of a turn that succeeded. Cancelling one would record
        it as stopped, which is false — and until this route existed, cancel was
        the only way to leave a task whose work was done.
        """
        await _task_body(request, allowed=set())
        row = await _run_task(tasks.finish_task, task_id)
        return {"task": tasks.snapshot(row).to_dict()}

    @app.post("/api/tasks/{task_id}/cancel", dependencies=[Depends(require_token)])
    async def cancel_task(task_id: str, request: Request) -> Dict[str, Any]:
        """Ask this task's own adapter to stop it.

        Nothing here signals a process, matches one by name, or touches any task
        but the one named in the path — see ``TaskService.cancel_task``.
        """
        await _task_body(request, allowed=set())
        row = await _run_task(tasks.cancel_task, task_id)
        return {"task": tasks.snapshot(row).to_dict()}

    @app.get("/api/task-adapters", dependencies=[Depends(require_token)])
    async def list_task_adapters() -> Dict[str, Any]:
        """Which adapters this build has, and what each one can do.

        On a default install this is an empty list, and that is the honest state
        of a foundation milestone rather than a fault: the adapter that does real
        work is the next one.
        """
        return {"adapters": tasks.adapters.describe()}

    @app.get("/api/task-projects", dependencies=[Depends(require_token)])
    async def list_task_projects() -> Dict[str, Any]:
        """Where tasks may run, by name. **No filesystem path is published.**"""
        return tasks.projects.to_dict()

    # -- native Remote Control (M2H, Lane A) ---------------------------------
    #
    # Four operations, all authenticated by the same device token as everything
    # else, all taking a registered project id and nothing else. There is no
    # parameter anywhere below for a path, a unit name, a systemctl verb, a
    # Claude flag, a model or an executable — the project registry is the only
    # thing that names a directory, and the argv is a constant in
    # sessions/claude.py.
    #
    # These are private-client operations. The future Custom GPT Actions bridge
    # (M2I.5) exposes a bounded, separately-chosen set of Actions and must never
    # include these: handing a session URL to an external model provider would
    # give it a live interactive agent on this workstation.

    async def _run_remote_control(operation, *args, **kwargs) -> Any:
        try:
            return await run_in_threadpool(operation, *args, **kwargs)
        except RemoteControlError as rejection:
            raise ApiError(
                code=rejection.code,
                message=rejection.message,
                status_code=_REMOTE_CONTROL_STATUS.get(rejection.code, 422),
                detail=rejection.detail,
            )

    @app.get("/api/remote-control/{project_id}", dependencies=[Depends(require_token)])
    async def get_remote_control(project_id: str) -> Dict[str, Any]:
        """Lifecycle state for one project's native host. **Never the URL.**

        ``url_available`` says whether a link exists; retrieving one is a
        separate authenticated call, because this payload is polled, cached and
        rendered, and a capability URL must be in none of those.
        """
        status = await _run_remote_control(remote_control.status, project_id)
        return {"session": status.to_dict()}

    @app.post("/api/remote-control/{project_id}/start", dependencies=[Depends(require_token)])
    async def start_remote_control(project_id: str, request: Request) -> Dict[str, Any]:
        """Bring the host up, or report that it already is.

        Idempotent: a host that is already running is returned unchanged rather
        than started twice — two Remote Control servers in one project directory
        is not a harmless duplicate.
        """
        await _task_body(request, allowed=set())
        store.record_remote_control_event(
            "remote_control.start_requested", "requested", project_id=project_id
        )
        try:
            status = await _run_remote_control(remote_control.start, project_id)
        except ApiError as rejection:
            store.record_remote_control_event(
                "remote_control.start_failed", rejection.code, project_id=project_id
            )
            raise
        store.record_remote_control_event(
            "remote_control.start_succeeded",
            "ok",
            project_id=project_id,
            unit=status.unit,
            generation=status.generation,
            state=status.state,
        )
        return {"session": status.to_dict()}

    @app.post("/api/remote-control/{project_id}/stop", dependencies=[Depends(require_token)])
    async def stop_remote_control(project_id: str, request: Request) -> Dict[str, Any]:
        """Take the host down, or report that it is already down.

        Deliberately does **not** require ``remote_control_enabled`` to still be
        set: revoking the capability on a project whose host is running must not
        strand a live agent with no supervised way to stop it.
        """
        await _task_body(request, allowed=set())
        store.record_remote_control_event(
            "remote_control.stop_requested", "requested", project_id=project_id
        )
        try:
            status = await _run_remote_control(remote_control.stop, project_id)
        except ApiError as rejection:
            store.record_remote_control_event(
                "remote_control.stop_failed", rejection.code, project_id=project_id
            )
            raise
        store.record_remote_control_event(
            "remote_control.stop_succeeded",
            "ok",
            project_id=project_id,
            unit=status.unit,
            state=status.state,
        )
        return {"session": status.to_dict()}

    @app.get("/api/remote-control/{project_id}/link", dependencies=[Depends(require_token)])
    async def get_remote_control_link(project_id: str) -> Dict[str, Any]:
        """The current session URL. The only route that returns one.

        Refuses whenever the link is not currently live — never started, host
        stopped, link not yet reported, or a previous generation — and the
        refusal is the same in all four cases, because "there isn't one" is the
        honest answer to each and distinguishing them would describe the host's
        internal state to whoever asked.

        The audit line records that a link was retrieved and for which
        generation. It cannot record the URL: ``record_remote_control_event``
        has no parameter that accepts one.
        """
        payload = await _run_remote_control(remote_control.link, project_id)
        store.record_remote_control_event(
            "remote_control.link_retrieved",
            "ok",
            project_id=project_id,
            generation=payload.get("generation"),
        )
        return {"link": payload}

    # Two events named in the M2H PR2 plan are deliberately **not** emitted
    # here, because emitting them would mean inventing the evidence:
    #
    # ``remote_control.url_discovered``
    #     Gated on a confirmed capture format, and
    #     :data:`~.sessions.links.LINK_FORMAT_CONFIRMED` is ``False``. The live
    #     PTY spike never reached a session URL — the CLI stops at its own
    #     consent prompt first — so there is nothing to announce, and an event
    #     saying a URL was discovered would be the first lie in the chain.
    #
    # ``remote_control.process_exited``
    #     The process that exits runs in ``cofferdam-rc@<project>.service``, a
    #     different unit from this daemon, and nothing here observes its exit:
    #     status is a poll, not a subscription. The honest options are to have
    #     the host write its own audit record — a second writer into the action
    #     store from another unit, which is a design decision, not a follow-up —
    #     or to leave the exit where systemd already records it truthfully, in
    #     the journal. This build does the latter.

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
