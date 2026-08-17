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
``POST /api/tasks/{task_id}/followups``      yes   one more message to the same
                                                   session — never an answer to a
                                                   question, which has its own route
``GET  /api/tasks/{task_id}/result``         yes   the latest completed turn's
                                                   result, provider-neutral
``GET  /api/tasks/{id}/turns/{n}/evidence``  yes   one turn's derived evidence
                                                   bundle — claims, observations,
                                                   relationships. Device token
                                                   only; the bridge is refused
``POST /api/tasks/{task_id}/cancel``         yes   ask that task's adapter to stop
``GET  /api/tasks/{id}/clarifications``      yes   questions the agent is waiting on
``POST /api/tasks/{id}/clarifications/{qid}/answer``
                                             yes   answer one question — never a
                                                   tool approval, which has no route
``GET  /api/task-adapters``                  yes   registered adapters + capabilities
``GET  /api/task-projects``                  yes   configured projects, names only
``GET  /api/workspaces``                     yes   configured workspaces, names only
``GET  /api/workspace/current``              yes   what are we working on right now
``PUT  /api/workspace/active``               yes   switch or clear the active workspace
``PUT  /api/workspace/objective``            yes   set or clear the objective
``PUT  /api/workspace/context``              yes   bounded continuity fields
``GET  /api/workspace/objective-history``    yes   previous objectives, bounded
``GET  /api/mind``                           yes   which memory roles are readable now
``GET  /api/mind/documents/{scope}/{role}``  yes   one approved document's content
``GET  /api/mind/proposals``                 yes   queued memory changes, bounded
``GET  /api/mind/proposals/{id}``            yes   one proposal, with live staleness
``POST /api/mind/proposals``                 yes   queue a change — writes nothing
``POST /api/mind/proposals/{id}/accept``     yes   apply it, bound to its base hash
``POST /api/mind/proposals/{id}/reject``     yes   refuse it
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
from .config import (
    Config,
    load_config,
    load_or_create_actions_bridge_token,
    load_or_create_token,
)
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
from .runtime.identity import now_iso
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
from .tasks.assessment import AssessmentTooLarge
from .tasks.clarifications import (
    SOURCE_FUTURE_GPT_BRIDGE as ANSWER_SOURCE_ACTIONS_BRIDGE,
    SOURCE_WORKSTATION_PWA as ANSWER_SOURCE_WORKSTATION_PWA,
)
from .tasks.errors import (
    CODE_ADAPTER_FAILED as CODE_TASK_ADAPTER_FAILED,
    CODE_ADAPTER_NOT_PERMITTED,
    CODE_ADAPTER_UNKNOWN as CODE_TASK_ADAPTER_UNKNOWN,
    CODE_CANCEL_UNSUPPORTED,
    CODE_CLARIFICATION_CLOSED,
    CODE_CLARIFICATION_INVALID,
    CODE_CLARIFICATION_NOT_DELIVERED,
    CODE_CLARIFICATION_PENDING,
    CODE_CLARIFICATION_UNKNOWN,
    CODE_CLARIFICATION_UNSUPPORTED,
    CODE_FOLLOWUP_IN_FLIGHT,
    CODE_FOLLOWUP_INVALID,
    CODE_FOLLOWUP_NOT_WAITING,
    CODE_FOLLOWUP_UNSUPPORTED,
    CODE_RESULT_NOT_READY,
    CODE_SESSION_UNAVAILABLE,
    CODE_TURN_LIMIT_REACHED,
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
from .tasks.turns import (
    SOURCE_FUTURE_GPT_BRIDGE as FOLLOWUP_SOURCE_ACTIONS_BRIDGE,
    SOURCE_WORKSTATION_PWA as FOLLOWUP_SOURCE_WORKSTATION_PWA,
)
from .tasks.models import (
    BUCKETS,
    DEFAULT_EVENT_PAGE,
    DEFAULT_TASK_PAGE,
    MAX_EVENT_PAGE,
    MAX_TASK_PAGE,
    ORIGIN_CHATGPT_APP,
    ORIGIN_PWA,
    TASK_API_VERSION,
)
from .context import ContextBuilder
from .mind import MindService, MindStore
from .projectcontext import (
    REASON_CONTEXT_UNAVAILABLE as READ_CONTEXT_UNAVAILABLE,
    REASON_INVALID_PROJECT_ID as READ_INVALID_PROJECT_ID,
    REASON_PROJECTION_FAILED as READ_PROJECTION_FAILED,
    REASON_PROJECT_DISABLED as READ_PROJECT_DISABLED,
    REASON_PROJECT_NOT_FOUND as READ_PROJECT_NOT_FOUND,
    REASON_RESPONSE_TOO_LARGE as READ_RESPONSE_TOO_LARGE,
    REASON_WORKSPACE_AMBIGUOUS as READ_WORKSPACE_AMBIGUOUS,
    REASON_WORKSPACE_DISABLED as READ_WORKSPACE_DISABLED,
    REASON_WORKSPACE_NOT_ACTIVE as READ_WORKSPACE_NOT_ACTIVE,
    REASON_WORKSPACE_NOT_CONFIGURED as READ_WORKSPACE_NOT_CONFIGURED,
    ProjectContextService,
    ProjectContextUnavailable,
    serialize_project_context,
)
from .mind.errors import (
    CODE_APPLY_FAILED,
    CODE_RESOLUTION_UNSUPPORTED,
    CODE_TARGET_AUTHORITY_CHANGED,
    CODE_CONTENT_INVALID,
    CODE_GLOBAL_GRANT_MISSING,
    CODE_PROPOSAL_NOT_PENDING,
    CODE_PROPOSAL_STALE,
    CODE_PROPOSAL_UNKNOWN,
    CODE_PROPOSAL_WORKSPACE_CHANGED,
    CODE_REASON_INVALID,
    CODE_ROLE_INVALID,
    CODE_ROLE_UNAVAILABLE,
    CODE_ROLE_UNCONFIGURED,
    CODE_SCOPE_INVALID,
    CODE_SOURCE_INVALID,
    MindError,
)
from .workspace import (
    CODE_ACTIVE_WORKSPACE_UNSET,
    CODE_CONTEXT_FIELD_INVALID,
    CODE_TASK_NOT_IN_WORKSPACE,
    CODE_WORKSPACE_DISABLED,
    CODE_WORKSPACE_PROJECT_MISSING,
    CODE_WORKSPACE_UNKNOWN,
    WorkspaceError,
    WorkspaceService,
    WorkspaceStore,
)

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"

# A workspace request body is an id, a sentence, or a handful of short
# references. Two kilobytes is already far more than that shape needs, and the
# body is refused on length before it is parsed — the same posture the audio and
# overlay bodies use.
MAX_WORKSPACE_BODY_BYTES = 4 * 1024

# Refusal code -> HTTP status. 404 for a workspace that is not configured, 409
# for a world that is not in the right state (nothing active, the project gone),
# 422 for everything the request itself got wrong.
_WORKSPACE_STATUS = {
    CODE_WORKSPACE_UNKNOWN: 404,
    CODE_WORKSPACE_DISABLED: 409,
    CODE_WORKSPACE_PROJECT_MISSING: 409,
    CODE_ACTIVE_WORKSPACE_UNSET: 409,
    CODE_CONTEXT_FIELD_INVALID: 422,
    CODE_TASK_NOT_IN_WORKSPACE: 422,
}

# A mind request body carries a whole memory document, so it is the largest
# body this API accepts — and still bounded well below anything that could be an
# upload. The document bound itself is `MAX_DOCUMENT_BYTES` (512 KiB) and is
# enforced by the service; this is the outer limit on the envelope around it,
# with room for JSON escaping and the three short fields beside it.
MAX_MIND_BODY_BYTES = 1024 * 1024

# Refusal code -> HTTP status. 404 for something that is not there, 409 for a
# world that is not in the state the request assumed — no grant, a drifted
# document, an already-decided proposal — and 422 for what the request itself
# got wrong. `mind_apply_failed` is the only 500: the request was right, the
# state was right, and the disk refused.
_MIND_STATUS = {
    CODE_SCOPE_INVALID: 422,
    CODE_ROLE_INVALID: 422,
    CODE_ROLE_UNCONFIGURED: 404,
    CODE_ROLE_UNAVAILABLE: 409,
    CODE_GLOBAL_GRANT_MISSING: 409,
    CODE_CONTENT_INVALID: 422,
    CODE_REASON_INVALID: 422,
    CODE_SOURCE_INVALID: 422,
    CODE_PROPOSAL_UNKNOWN: 404,
    CODE_PROPOSAL_NOT_PENDING: 409,
    CODE_PROPOSAL_STALE: 409,
    CODE_PROPOSAL_WORKSPACE_CHANGED: 409,
    # The role now names a different document. A conflict rather than a bad
    # request: the request was right when it was made, and the world moved.
    CODE_TARGET_AUTHORITY_CHANGED: 409,
    CODE_APPLY_FAILED: 500,
    # Not a client error at all — this host cannot open memory documents safely,
    # so it does not open them. 501 rather than 500: nothing failed, the
    # capability is absent.
    CODE_RESOLUTION_UNSUPPORTED: 501,
}

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

#: Response headers for every route whose body carries **task content** — the
#: prompt somebody wrote, an agent's question, a turn's result, the event stream
#: that quotes all three.
#:
#: ``no-store`` rather than ``no-cache``: no-cache still permits writing the body
#: to disk and revalidating it later, which for this content means somebody's
#: private instruction to an agent sitting in a browser cache directory after the
#: sign-out that was supposed to remove it. It is also the correctness half —
#: these bodies change as turns complete and questions close, and a task screen
#: served from a cache would offer an answer box for a question that is already
#: superseded.
#:
#: Not applied to the routes that carry no task content: the adapter capability
#: list, the health endpoint and the registries say the same thing to everyone
#: and there is nothing private in them to keep out of a cache.
TASK_CONTENT_HEADERS = {"Cache-Control": "no-store"}

# -- internal callers ---------------------------------------------------------
#
# Which credential authenticated a task request. Two values, both code-owned,
# neither readable from a request: there is no header, query parameter or body
# field anywhere in this API that names a caller, and adding one would undo the
# only reason these exist.
#
# They are *not* a permission system. A caller does not carry scopes and cannot
# be widened by configuration; the surface each one can reach is decided by
# which FastAPI dependency a route was declared with, in this file, at import
# time. What a caller decides is a single thing: how the work it asks for is
# attributed in Cofferdam's durable provenance.

#: The private PWA and every other holder of the device token — the CLI, a test,
#: `curl` on the tailnet. One credential, one word, unchanged since M1.
CALLER_PWA = "pwa"
#: The M2I.5 Custom GPT Actions bridge, holding its own 0600 credential.
#: HTTP status per project-context refusal. Closed map, no default leak.
#:
#: 404 for "there is no such thing", 409 for "there is, and the host is not in a
#: state to answer", 422 for a malformed id, 500 only for the two internal
#: failures. A refusal never carries a path — see `projectcontext.py`.
_CONTEXT_STATUS = {
    READ_INVALID_PROJECT_ID: 422,
    READ_PROJECT_NOT_FOUND: 404,
    READ_PROJECT_DISABLED: 409,
    READ_WORKSPACE_NOT_CONFIGURED: 404,
    READ_WORKSPACE_AMBIGUOUS: 409,
    READ_WORKSPACE_DISABLED: 409,
    READ_WORKSPACE_NOT_ACTIVE: 409,
    READ_CONTEXT_UNAVAILABLE: 500,
    READ_PROJECTION_FAILED: 500,
    READ_RESPONSE_TOO_LARGE: 500,
}

CALLER_ACTIONS_BRIDGE = "actions_bridge"

#: Caller to the ``origin`` recorded on a task it creates. Both entries are
#: members of :data:`~.tasks.models.ORIGINS`, and the mapping is total: a caller
#: with no entry here would fall through to a ``.get`` default, which is how a
#: bridge-created task ends up labelled as somebody's phone.
ORIGIN_FOR_CALLER: Dict[str, str] = {
    CALLER_PWA: ORIGIN_PWA,
    CALLER_ACTIONS_BRIDGE: ORIGIN_CHATGPT_APP,
}

#: Caller to the ``source`` recorded on a clarification answer it submits.
ANSWER_SOURCE_FOR_CALLER: Dict[str, str] = {
    CALLER_PWA: ANSWER_SOURCE_WORKSTATION_PWA,
    CALLER_ACTIONS_BRIDGE: ANSWER_SOURCE_ACTIONS_BRIDGE,
}

#: Caller to the ``source`` recorded on a follow-up it sends.
FOLLOWUP_SOURCE_FOR_CALLER: Dict[str, str] = {
    CALLER_PWA: FOLLOWUP_SOURCE_WORKSTATION_PWA,
    CALLER_ACTIONS_BRIDGE: FOLLOWUP_SOURCE_ACTIONS_BRIDGE,
}

#: What every static asset says about its own freshness.
#:
#: ``no-cache`` does **not** mean "do not store" — that is ``no-store``, which is
#: for the task-content routes above. It means "store it, but ask before you use
#: it". Paired with the ``ETag`` and ``Last-Modified`` Starlette already sends,
#: an unchanged asset costs one conditional request and a 304 with no body, and a
#: changed one is picked up on the next load. Always.
#:
#: **This exists because a phone served a stale UI.** M2I PR4's follow-up draft
#: fix was verified in a real browser and then failed on a real phone, because
#: the asset carried no ``Cache-Control`` at all and iOS Safari is free to apply
#: heuristic freshness to a response that does not say otherwise. The fix was in
#: the file; the file was not on the phone. A UI change that cannot be trusted to
#: reach a device is a UI change that cannot be validated, and "tell the user to
#: clear their cache" is not a deployment mechanism.
#:
#: Chosen over versioned asset URLs deliberately. Versioning nine ``<script>``
#: and ``<link>`` references means a build step or template rewriting, and it
#: fails silently the first time somebody adds an asset and forgets to version
#: it. Revalidation is one header, applies to every file in the directory
#: including ones added later, and cannot drift.
STATIC_ASSET_HEADERS = {"Cache-Control": "no-cache"}


class RevalidatedStaticFiles(StaticFiles):
    """``StaticFiles`` that requires a revalidation before a cached copy is used.

    The header is applied to the 304 as well as the 200: a ``Not Modified``
    response refreshes the stored freshness metadata, and one that omitted the
    directive would hand the browser back a copy it may then use blind.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        for name, value in STATIC_ASSET_HEADERS.items():
            response.headers[name] = value
        return response

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
    # 404 for an unknown question, and the same answer whether it never existed
    # or belongs to another task — distinguishing them would let somebody learn
    # which question ids exist elsewhere by watching which refusal came back.
    CODE_CLARIFICATION_UNKNOWN: 404,
    # 409 rather than 422: the request was well formed and the *world* has moved
    # on. A client that gets this should reload the task, not retype.
    CODE_CLARIFICATION_CLOSED: 409,
    CODE_CLARIFICATION_INVALID: 422,
    CODE_CLARIFICATION_UNSUPPORTED: 422,
    # 502: Cofferdam accepted the answer and the provider did not take it. The
    # failure is downstream, and the client's answer was never the problem.
    CODE_CLARIFICATION_NOT_DELIVERED: 502,
    # 409 for all four M2I PR3 refusals, and the status is the same because the
    # sentence a client should act on is the same: the request was well formed
    # and the *world* is not in a state where it can happen. What differs is
    # what to do next, which is what the code carries.
    #
    # `task_result_not_ready` is emphatically not a 404. The task exists, and
    # answering "no such task" for one that is simply still working would send
    # somebody looking for a task they already have.
    CODE_RESULT_NOT_READY: 409,
    CODE_CLARIFICATION_PENDING: 409,
    CODE_SESSION_UNAVAILABLE: 409,
    CODE_FOLLOWUP_IN_FLIGHT: 409,
    CODE_TURN_LIMIT_REACHED: 409,
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
    workspaces=None,
    mind=None,
) -> FastAPI:
    """Build the application. Arguments are injectable for tests."""
    config = config or load_config()
    config.ensure_dirs()
    token = token or load_or_create_token(config)
    # ``None`` unless the host enabled the caller. Held in a closure beside the
    # device token rather than on ``app.state``: nothing should be able to read
    # a credential off the application object from inside a request handler.
    bridge_token = load_or_create_actions_bridge_token(config)
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
                enable_claude_agent_sdk_adapter=config.enable_claude_agent_sdk_adapter,
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

    # Workspaces and Working Context (M2J PR1). Constructed unconditionally and
    # harmless when unconfigured: a host with no `workspaces.json` has no
    # workspaces, every route below answers truthfully that none is active, and
    # nothing else in the service consults it. The database is created lazily on
    # the first write, so a host that never activates a workspace never gains a
    # file.
    #
    # It reads the project registry through a callable rather than holding one,
    # so an edit to `task-projects.json` is visible to a workspace read the same
    # way it is to Task Core. It holds Task Core itself only to *resolve* task
    # references — see `workspace/service.py` for why nothing here may store a
    # task's state.
    if workspaces is None:
        workspaces = WorkspaceService(
            config,
            WorkspaceStore(config),
            projects=lambda: tasks.projects,
            tasks=tasks,
        )

    # Cofferdam Mind (M2J PR2). Constructed unconditionally and, like the
    # workspace service, inert on a host that has configured nothing: no
    # `documents` mapping means no project mind, no `mind-grant.json` means no
    # global mind, and the proposal database is created on the first *write* so a
    # host that never proposes anything never gains a file.
    #
    # It is given the workspace service rather than the workspace store, so that
    # "which project is this workspace over, and is it usable" has exactly one
    # implementation. It is given no adapter, no Task Core handle and no provider
    # client, because reading and changing memory involves none of them.
    if mind is None:
        mind = MindService(config, MindStore(config), workspaces=workspaces)

    # M2J PR4. The Context Builder finally has a caller — and it is this one,
    # behind the egress policy, rather than anything that could return a pack.
    # Constructed unconditionally and inert on a host with no workspace: the read
    # refuses with `workspace_not_active` rather than erroring.
    project_context_service = ProjectContextService(
        config=config,
        workspaces=workspaces,
        projects=tasks.projects,
        builder=ContextBuilder(workspaces=workspaces, mind=mind),
    )

    # Classify any apply that was claimed and never finished, before the first
    # request is served. **Nothing is resumed**: recovery reads the durable row
    # and the document's own hash and records which of three things is true —
    # the bytes landed, they did not, or somebody else changed the file — and it
    # performs no canonical write of its own. A consequential operation continued
    # by a restart is one nobody authorized at the moment it happened, which is
    # the same reasoning `tasks.recover_after_restart` follows for a task whose
    # process is gone.
    #
    # Harmless on a host that has never proposed anything: the read does not
    # create the database, so there is nothing to classify and no file appears.
    mind.recover_after_restart()

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
            # Close every delegated session this process owns, on the way out.
            #
            # Only adapters that hold a process do anything here, and each closes
            # the children *it* launched — see `AdapterRegistry.shutdown`. There
            # is no process scan on this path and no signal to anything Cofferdam
            # did not start: a workstation stopping its own daemon must not
            # disturb a Claude Desktop window or a Remote Control session that
            # has nothing to do with it.
            await run_in_threadpool(tasks.adapters.shutdown)

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
    app.state.workspaces = workspaces
    app.state.mind = mind
    overlays = DisplayOverlayStore(config, inventory)
    app.state.display_overlays = overlays

    # -- auth ----------------------------------------------------------------

    def _token_matches(candidate: Optional[str]) -> bool:
        return bool(candidate) and _secrets.compare_digest(candidate, token)

    def _bridge_token_matches(candidate: Optional[str]) -> bool:
        # `bridge_token` is None on every deployment that has not enabled the
        # caller, and this returns False without comparing anything — so a
        # request cannot become the bridge by presenting an empty string.
        return (
            bool(candidate)
            and bridge_token is not None
            and _secrets.compare_digest(candidate, bridge_token)
        )

    def _presented(request: Request) -> Optional[str]:
        header = request.headers.get("authorization", "")
        return header[7:].strip() if header.lower().startswith("bearer ") else None

    async def require_token(request: Request) -> None:
        """The device token, and only the device token.

        Left exactly as it was, and that is the security property: this
        dependency guards every route in the file except the ten the Actions
        bridge is allowed to reach. The bridge's credential is refused here
        because nothing in this function has ever heard of it — not because a
        check rejects it, which is a promise a later refactor could lose.
        """
        if not _token_matches(_presented(request)):
            raise ApiError(
                code=CODE_UNAUTHORIZED,
                message="a valid device token is required",
                status_code=401,
            )

    async def require_task_caller(request: Request) -> None:
        """Either internal credential, on the bounded task surface only.

        This is the *whole* difference the M2I.5 bridge makes to the daemon: ten
        task routes accept a second 0600 credential, and record which one
        arrived. Nothing else changes — no new route, no widened body, no
        listener, and no way to reach `/api/actions`, `/api/remote-control`,
        `/api/registries` or the PWA with the bridge's key.

        The principal is stashed on the request rather than returned, because
        the routes that need it read it from one helper and the ones that do not
        never see it. It is derived here from the credential and is not
        touchable from a header, a query string or a body — there is no field
        anywhere in this API for a caller to name itself.
        """
        candidate = _presented(request)
        if _token_matches(candidate):
            request.state.caller = CALLER_PWA
            return
        if _bridge_token_matches(candidate):
            request.state.caller = CALLER_ACTIONS_BRIDGE
            return
        raise ApiError(
            code=CODE_UNAUTHORIZED,
            message="a valid device token is required",
            status_code=401,
        )

    async def require_context_caller(request: Request) -> None:
        """Either internal credential, on the **one** project-context read.

        Its own dependency rather than a reuse of ``require_task_caller`` (M2J
        PR4). The two admit the same pair of credentials and that is exactly why
        they must stay separate: sharing one would mean a later route added to
        the task surface silently became reachable with context authority, and a
        later widening of context authority silently reached the task surface.
        One dependency per bounded surface is what keeps "what can the bridge
        do?" answerable by reading a list of routes.

        What this grants the bridge credential, in full: one GET, for one
        project id, returning a `CloudContextProjection` that the host built.
        It grants **no** raw Mind read — `/api/mind*` still uses
        ``require_token`` and has never heard of this credential — no Working
        Context write, no workspace activation, no objective, no proposal, no
        task, and no filesystem authority of any kind. There is no field in this
        request for a path, a role, a policy or a redaction rule.
        """
        candidate = _presented(request)
        if _token_matches(candidate):
            request.state.caller = CALLER_PWA
            return
        if _bridge_token_matches(candidate):
            request.state.caller = CALLER_ACTIONS_BRIDGE
            return
        raise ApiError(
            code=CODE_UNAUTHORIZED,
            message="a valid device token is required",
            status_code=401,
        )

    def _caller(request: Request) -> str:
        """Which credential authenticated this request. Defaults to the PWA.

        The default is reached only by a route that used ``require_token`` and
        then asked anyway, which means the device token authenticated it — so
        the safe answer and the true answer are the same one.
        """
        return getattr(request.state, "caller", CALLER_PWA)

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

    @app.get("/api/tasks", dependencies=[Depends(require_task_caller)])
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

    #: M2K PR23. Acceptance criteria and criteria continuity are **authoring**
    #: fields: they say what a piece of work is required to achieve and how that
    #: requirement relates to the ones before it. That is host authority, and
    #: `require_task_caller` is not a fine enough boundary to hold it — the same
    #: dependency accepts the Actions bridge's own credential, so putting these
    #: names in a shared allowlist would hand a remote Custom GPT user the power
    #: to declare what its own work will be judged against.
    #:
    #: So the field list is per caller. The bridge's request shape is unchanged
    #: and `_task_body` refuses an unexpected key, which means a bridge that
    #: sends `criteria` gets the same refusal it got yesterday rather than a new
    #: capability. This is the pattern the detail route already uses for
    #: `prompt`, for the same reason and with the same shape.
    AUTHORING_FIELDS = frozenset({"criteria", "continuity"})

    def _authoring_fields(request: Request) -> frozenset:
        return AUTHORING_FIELDS if _caller(request) == CALLER_PWA else frozenset()

    @app.post("/api/tasks", dependencies=[Depends(require_task_caller)])
    async def create_task(request: Request) -> JSONResponse:
        """Start one task. The whole client vocabulary is five bounded fields.

        ``origin`` is **not** among them: it is assigned here from the
        authenticated request context, because a client choosing how its own
        request is later attributed is the opposite of what that field is for.
        Since M2I.5 that context has two possible answers rather than one, and
        the lookup is total — see :data:`ORIGIN_FOR_CALLER`.
        """
        payload = await _task_body(
            request,
            allowed={"project_id", "adapter_id", "prompt", "client_request_id", "title"}
            | _authoring_fields(request),
        )
        row, created = await _run_task(
            tasks.create_task,
            project_id=payload.get("project_id"),
            adapter_id=payload.get("adapter_id"),
            prompt=payload.get("prompt"),
            client_request_id=payload.get("client_request_id"),
            title=payload.get("title"),
            origin=ORIGIN_FOR_CALLER[_caller(request)],
            # M2K PR23. Absent stays absent: `.get` yields ``None`` for a key the
            # caller did not send, and the service writes a durable
            # `not_declared` for that — never a manufactured `root`. An omitted
            # declaration and an explicit one are different facts all the way
            # down, and this is the layer where they would be easiest to blur.
            criteria=payload.get("criteria"),
            continuity=payload.get("continuity"),
        )
        return JSONResponse(
            # 200 rather than 201 when an idempotency key matched: nothing was
            # created, and the status line is the cheapest place to say so.
            status_code=201 if created else 200,
            content={"task": tasks.snapshot(row).to_dict(), "created": created},
        )

    @app.get("/api/tasks/{task_id}", dependencies=[Depends(require_task_caller)])
    async def get_task(task_id: str, request: Request) -> JSONResponse:
        # `refresh_task`, not `get_task`. An adapter whose work happens inside a
        # process has to be *asked* what it saw, and opening the detail view is
        # when Cofferdam asks. For a synchronous adapter it is a no-op returning
        # the same row.
        row = await _run_task(tasks.refresh_task, task_id)
        payload = tasks.snapshot(row).to_dict()
        if _caller(request) == CALLER_PWA:
            # The detail view is the one place the prompt is published, and only
            # to the authenticated client that already sent it.
            #
            # The Actions bridge is not that client. It composed the prompt from
            # somebody's ChatGPT conversation and then sent it here; handing it
            # back would let a model provider read the task text out of
            # Cofferdam again, on a schedule, long after the turn that wrote it.
            # Withheld rather than redacted: the key is absent from the payload,
            # so there is nothing for a bridge to forward by accident.
            payload["prompt"] = row.prompt
        return JSONResponse(content={"task": payload}, headers=TASK_CONTENT_HEADERS)

    @app.get("/api/tasks/{task_id}/events", dependencies=[Depends(require_token)])
    async def get_task_events(
        task_id: str, after: int = 0, limit: int = DEFAULT_EVENT_PAGE
    ) -> JSONResponse:
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
        return JSONResponse(
            content={
                "task_id": row.task_id,
                "events": [event.to_dict() for event in events],
                "cursor": events[-1].sequence if events else after,
                "event_cursor": row.event_cursor,
            },
            headers=TASK_CONTENT_HEADERS,
        )

    @app.post("/api/tasks/{task_id}/followups", dependencies=[Depends(require_task_caller)])
    async def send_task_followup(task_id: str, request: Request) -> Dict[str, Any]:
        """Send one more message to the session this task already owns.

        Two fields, and what is **absent** is the surface. There is no field
        here for a provider session id, a model, a tool, an approval decision,
        an executable, a working directory, a flag, an environment or a
        permission mode — they are not validated and rejected, they are not
        there, and `_task_body` refuses an unexpected key rather than ignoring
        it. The session this reaches is found from the task's own id, server
        side, in the adapter that owns it.

        `source` is not a field either, for the same reason it is not one on the
        clarification answer route: a client choosing how its own message is
        later attributed is the opposite of what provenance is for. It is
        assigned here from the authenticated request context.
        """
        payload = await _task_body(
            request,
            allowed={"followup", "client_request_id"} | _authoring_fields(request),
        )
        row = await _run_task(
            tasks.send_followup,
            task_id,
            payload.get("followup"),
            client_request_id=payload.get("client_request_id"),
            source=FOLLOWUP_SOURCE_FOR_CALLER[_caller(request)],
            # M2K PR23, and the same rule: omission is `not_declared`, never an
            # inferred `extend`. A follow-up is exactly where guessing would be
            # most tempting and most wrong — `extend`, `replace` and `revise` are
            # not distinguishable by looking at the criteria, which is the whole
            # reason PR10 made the declaration explicit.
            criteria=payload.get("criteria"),
            continuity=payload.get("continuity"),
        )
        return {"task": tasks.snapshot(row).to_dict()}

    @app.get("/api/tasks/{task_id}/result", dependencies=[Depends(require_task_caller)])
    async def get_task_result(task_id: str) -> JSONResponse:
        """What this task produced, in the provider-neutral result shape.

        **The latest completed turn's result.** For a terminal task that is also
        the final result, and `task_terminal` in the body says which a caller is
        holding — both are fields rather than a rule somebody has to infer, and
        the payload carries `result_meaning` in words as well.

        A read and nothing else: no `refresh_task`, no adapter call, no state
        change. Asking what a task produced must not be able to change what it
        produced, and something polling this must not be able to drive an
        adapter by doing so.

        `no-store`, because the body carries the answer to somebody's private
        prompt and a task's result changes as turns complete.

        Since M2I.5 the Actions bridge reads this route with its own internal
        credential — and it is still not a proxy for it. The bridge publishes
        `sync_task`, which folds this result into one bounded snapshot, truncates
        the text to its own smaller limit and drops `provider_session_id`
        entirely. What travels to a model provider is a subset chosen in
        `cofferdam/actions_bridge/normalize.py`, not this payload.
        """
        result = await _run_task(tasks.get_result, task_id)
        return JSONResponse(
            content={"result": result.to_dict()},
            headers=TASK_CONTENT_HEADERS,
        )

    # -- evidence (M2K PR2) --------------------------------------------------
    #
    # One route, and both halves of its shape are deliberate.
    #
    # **Turn-qualified.** `/turns/{turn_number}/evidence` rather than a
    # task-level `/evidence`, because the entire point of schema v5 is that
    # Cofferdam now knows exactly which events belong to which turn. A
    # task-level endpoint would have to merge turns or silently pick one, and
    # either would give back the turn-scoped/task-scoped confusion the milestone
    # was built to end.
    #
    # **`require_token`, not `require_task_caller`.** The Actions bridge reads
    # ten task routes with its own credential; this is not an eleventh. That is
    # not a policy applied here — it is that `require_token` has never heard of
    # the bridge credential, so a bridge request arrives as an ordinary
    # unauthenticated one and gets 401. Evidence is where Cofferdam's honest
    # account of what a worker did and what the host observed lives, and
    # widening the bridge's reach is a decision to take on its own, in its own
    # PR, with its own review — not one to inherit by reusing a dependency.
    #
    # What is absent, and stays absent: no root selector, no path selector, no
    # policy selector, no artifact body, no filesystem read, no Git execution,
    # no provider call. There is no field in this request for any of them, which
    # is a stronger guarantee than validating them away.

    @app.get(
        "/api/tasks/{task_id}/turns/{turn_number}/evidence",
        dependencies=[Depends(require_token)],
    )
    async def get_turn_evidence(task_id: str, turn_number: int) -> JSONResponse:
        """One turn's derived evidence bundle. Read-only, and free of verdicts.

        Assembled on read from stored immutable facts — claims, ingestion
        summaries, the append-only evidence on events, and the schema-v5 turn
        bounds. Nothing here runs Git, opens a file, or asks a provider
        anything, so what this returns is what was *recorded*, not what the
        repository looks like now. Reading it a thousand times leaves the
        database byte-identical.

        The body distinguishes three things a reader must not conflate: what the
        worker **claimed**, what Cofferdam **observed**, and the **relationship**
        between them. A relationship of `path_agreed` means both named the same
        project-relative path — it does **not** mean the claimed operation was
        verified, and `operation_agreement` says `unknown` out loud rather than
        leaving the question unasked.

        Since M2K PR5 every observation also carries the **domain** that produced
        it — `worktree` for the index and working tree against the current HEAD,
        `committed_range` for what the turn committed since the boundary recorded
        before it started — and `committed_range` summarises that range's
        revisions, history relation, coverage and boundary quality. A path may
        appear in both domains, at two different moments, and neither reading may
        be collapsed into the other. This route gained no parameter for any of
        it: the body grew, the request did not.

        `generated_at` sits on the envelope beside the bundle, never inside it.
        It is presentation metadata for a person reading a response, it is not
        part of the bundle's identity, and it is not an input to
        `input_fingerprint` — which is what makes the fingerprint stable across
        reads.

        `no-store`, like every other task-content route: this describes somebody's
        private work.
        """
        if turn_number < 1:
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="a turn number starts at one",
                status_code=422,
            )
        bundle = await _run_task(tasks.evidence_bundle, task_id, turn_number)
        if bundle is None:
            raise ApiError(
                code=CODE_NOT_FOUND,
                message="that task has no such turn",
                status_code=404,
            )
        return JSONResponse(
            content={
                "evidence": bundle.to_dict(),
                # Outside the bundle, and labelled. See the docstring.
                "generated_at": now_iso(),
            },
            headers=TASK_CONTENT_HEADERS,
        )

    # The assessment view (M2K PR8): what this turn was required to do, and what
    # the deterministic evaluator made of it. Both facts have been durable since
    # PR6 and PR7 and completely invisible until now.
    #
    # **One route, not two.** Criteria and evaluation are one turn-qualified audit
    # question and a reader needs both or neither. Two routes would let a client
    # pair criteria read at one moment with an evaluation read at another and
    # draw a conclusion about the pair — which is exactly the window
    # `turn_assessment_inputs` holds one lock to close — and would leave two HTTP
    # contracts free to drift while describing one thing.
    #
    # **`require_token`, not `require_task_caller`**, and this is a deliberate
    # departure from the obvious choice. `require_task_caller` is what makes the
    # Actions bridge's ten task routes work: it accepts the bridge's own
    # credential. An assessment is Cofferdam's judgement about somebody's work
    # measured against what they asked for, which is further from the bridge's
    # business than evidence is — and the evidence route already set the
    # precedent for the same reason. `require_token` has never heard of the
    # bridge credential, so a bridge request arrives as an ordinary
    # unauthenticated one and gets 401. That is a stronger guarantee than a check
    # that rejects it, which a later refactor could lose.
    #
    # **GET only.** There is no POST, PUT, PATCH or DELETE on this path and no
    # rerun route anywhere: an evaluation is immutable, and a browser must not be
    # able to ask for another one. FastAPI answers the other verbs 405 because
    # nothing registered them.
    #
    # What is absent and stays absent: no aggregate, no pass/fail, no score, no
    # confidence, no risk, no evidence body, no claim relationships, no path
    # selector, no filesystem read, no Git execution, no provider call.

    @app.get(
        "/api/tasks/{task_id}/turns/{turn_number}/assessment",
        dependencies=[Depends(require_token)],
    )
    async def get_turn_assessment(task_id: str, turn_number: int) -> JSONResponse:
        """One turn's criteria and its deterministic evaluation. Read-only.

        Everything in the body was already stored: the criteria snapshot frozen
        before the worker was dispatched, and the evaluation written after the
        turn closed. This route assembles no judgement, runs no evaluator,
        triggers no recovery and writes nothing — reading it a thousand times
        leaves the database byte-identical.

        Three criteria states and four evaluation states are published as closed
        words rather than left to be inferred from a null. The distinctions
        matter and are easy to lose:

        * criteria ``legacy_unknown`` means the turn predates criteria
          persistence — **not** that it had none;
        * criteria ``not_provided`` means Cofferdam recorded, before dispatch,
          that none were supplied — **not** that everything passed;
        * evaluation ``not_recorded`` means a closed criteria-bearing turn has no
          record, which is an operational fact worth noticing — **not** a pass,
          and **not** an ``unverified`` criterion result.

        There is no aggregate anywhere in the response, and no code here or
        downstream computes one. A list of per-criterion results is not a verdict
        on the task.

        `no-store`, like every other task-content route: this describes somebody's
        private work.
        """
        if turn_number < 1:
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="a turn number starts at one",
                status_code=422,
            )
        try:
            assessment = await _run_task(
                tasks.turn_assessment, task_id, turn_number
            )
        except AssessmentTooLarge:
            # Fails closed. A trimmed audit view would look complete and would
            # not be, which is worse than an error that says so.
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="the assessment does not fit the response contract",
                status_code=500,
            )
        if assessment is None:
            raise ApiError(
                code=CODE_NOT_FOUND,
                message="that task has no such turn",
                status_code=404,
            )
        return JSONResponse(
            content={
                "assessment": assessment,
                # Outside the assessment and labelled, exactly as the evidence
                # route does it: a read clock is presentation metadata and is not
                # part of any stored identity.
                "generated_at": now_iso(),
            },
            headers=TASK_CONTENT_HEADERS,
        )

    @app.post("/api/tasks/{task_id}/finish", dependencies=[Depends(require_task_caller)])
    async def finish_task(task_id: str, request: Request) -> Dict[str, Any]:
        """Close a retained session on purpose, and complete the task.

        The honest way out of a turn that succeeded. Cancelling one would record
        it as stopped, which is false — and until this route existed, cancel was
        the only way to leave a task whose work was done.
        """
        await _task_body(request, allowed=set())
        row = await _run_task(tasks.finish_task, task_id)
        return {"task": tasks.snapshot(row).to_dict()}

    @app.post("/api/tasks/{task_id}/cancel", dependencies=[Depends(require_task_caller)])
    async def cancel_task(task_id: str, request: Request) -> Dict[str, Any]:
        """Ask this task's own adapter to stop it.

        Nothing here signals a process, matches one by name, or touches any task
        but the one named in the path — see ``TaskService.cancel_task``.
        """
        await _task_body(request, allowed=set())
        row = await _run_task(tasks.cancel_task, task_id)
        return {"task": tasks.snapshot(row).to_dict()}

    # -- clarification questions (M2I PR2) -----------------------------------
    #
    # Two operations, and what is *not* here is the point of the section.
    #
    # There is no approval route. Not a disabled one, not a stubbed one, not one
    # that always refuses — none. A tool approval is decided on a trusted surface
    # at the workstation, and the way that survives a future refactor is that
    # this API has no path a permission decision could travel on.
    #
    # There is no generic "answer a request" route either, which is the shape
    # somebody would reach for to avoid writing two similar handlers. One route
    # serving both categories would put the entire clarification/approval
    # distinction inside a single `if` — and that `if` is exactly the thing this
    # milestone exists to make structural.
    #
    # Since M2I.5 the Actions bridge reads and writes these two with its own
    # internal credential, and the absence above is what makes that safe rather
    # than alarming: there is no approval route for a bridge to reach, so the
    # question of whether a model provider may grant a permission never arrives
    # at a check that could be got wrong. The bridge publishes these as
    # `sync_task` and `submit_choice_answer`, narrower again — one option id, no
    # free text, and no `answer` field at all on the wire it exposes.

    @app.get(
        "/api/tasks/{task_id}/clarifications",
        dependencies=[Depends(require_task_caller)],
    )
    async def list_task_clarifications(task_id: str) -> JSONResponse:
        """The questions this task is waiting on. Bounded, normalized, no payload.

        `refresh_task` first, for the same reason the detail view does it: a
        question asked thirty seconds ago is sitting in an adapter's buffer until
        somebody asks, and a list that could not see it would send people to a
        task screen that says "needs you" with nothing to answer.

        The response carries no provider session id, no tool input and no raw
        provider payload — see `PendingClarification.to_dict`.

        `no-store` for the same reason the result route sets it: the body is a
        question an agent asked about somebody's private work, and a question
        that has since been answered or superseded must not come back out of a
        cache and be offered again.
        """
        row = await _run_task(tasks.refresh_task, task_id)
        pending = await run_in_threadpool(tasks.pending_clarifications, row.task_id)
        return JSONResponse(
            content={
                "version": TASK_API_VERSION,
                "task_id": row.task_id,
                "state": row.state,
                "waiting_reason": row.waiting_reason,
                "clarifications": [item.to_dict() for item in pending],
            },
            headers=TASK_CONTENT_HEADERS,
        )

    @app.post(
        "/api/tasks/{task_id}/clarifications/{question_id}/answer",
        dependencies=[Depends(require_task_caller)],
    )
    async def answer_task_clarification(
        task_id: str, question_id: str, request: Request
    ) -> Dict[str, Any]:
        """Answer one specific question on one specific task.

        The whole client vocabulary is two fields. `answer` is text a person
        typed; `option_ids` are Cofferdam's own identifiers, taken from the
        question this route is answering and checked against it.

        Everything else is **absent rather than validated**: there is no field
        here for a session id, a project, a path, a tool, a command, a permission
        mode or an allow/deny decision, and `_task_body` refuses an unexpected key
        rather than ignoring it. A body carrying an approval-shaped field is
        refused again, by name, in `ClarificationAnswer.from_request` — twice,
        because this is the one route where the difference between information
        and permission is the difference that matters.

        `source` is not among the fields either. It is assigned here from the
        authenticated request context, because a client choosing how its own
        answer is later attributed is the opposite of what provenance is for.
        """
        payload = await _task_body(request, allowed={"answer", "option_ids"})
        row = await _run_task(
            tasks.answer_clarification,
            task_id,
            question_id,
            payload,
            source=ANSWER_SOURCE_FOR_CALLER[_caller(request)],
        )
        return {"task": tasks.snapshot(row).to_dict()}

    @app.get("/api/task-adapters", dependencies=[Depends(require_token)])
    async def list_task_adapters() -> Dict[str, Any]:
        """Which adapters this build has, and what each one can do.

        On a default install this is an empty list, and that is the honest state
        of a foundation milestone rather than a fault: the adapter that does real
        work is the next one.
        """
        return {"adapters": tasks.adapters.describe()}

    @app.get("/api/task-projects", dependencies=[Depends(require_task_caller)])
    async def list_task_projects() -> Dict[str, Any]:
        """Where tasks may run, by name. **No filesystem path is published.**"""
        return tasks.projects.to_dict()

    # -- workspaces and Working Context (M2J PR1) ----------------------------
    #
    # Five routes, all on `require_token` — the private device-token surface —
    # and deliberately **not** on `require_task_caller`. The Actions bridge holds
    # a credential these have never heard of, so a bridge request arrives here as
    # a 401 rather than as a refusal a later change could relax. That is the same
    # structural argument D-2026-08-09-2 makes for the Remote Control routes, and
    # it matters here for a specific reason: `syncWorkspace` is recorded as an
    # M2J **PR4** Action, and until that is designed and reviewed the honest
    # state is that no external surface can read this at all.
    #
    # There is no create route. Workspaces are host-owned configuration, edited
    # in a text editor beside `task-projects.json`, for the reason projects have
    # no create route either: a registry that can be written over the network is
    # a registry that can grant access over the network. Nothing here writes
    # `workspaces.json`, and nothing auto-registers a workspace for an existing
    # project — D-2026-08-11-1's "suggested, never silently auto-created", taken
    # to its simplest honest form, which is that PR1 suggests nothing yet.
    #
    # No body anywhere accepts a path, a root, an adapter, a model, a provider or
    # a session id. The workspace *schema* refuses those by name at load; these
    # routes refuse them by allowlist at the door.

    async def _workspace_body(request: Request, allowed: set) -> Dict[str, Any]:
        """One bounded workspace request body, refusing anything unexpected.

        The allowlist is the surface, exactly as it is for task bodies. Unknown
        keys are a 422 rather than a silent drop, because the difference between
        "clear this field" and "I misspelled this field" is invisible otherwise —
        and one of those blanks somebody's objective.
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
                too_big = int(declared) > MAX_WORKSPACE_BODY_BYTES
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
        if len(raw) > MAX_WORKSPACE_BODY_BYTES:
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
                detail="this endpoint accepts only: " + ", ".join(sorted(allowed)),
            )
        return payload

    async def _run_workspace(operation, *args, **kwargs) -> Any:
        try:
            return await run_in_threadpool(operation, *args, **kwargs)
        except WorkspaceError as rejection:
            raise ApiError(
                code=rejection.code,
                message=rejection.message,
                status_code=_WORKSPACE_STATUS.get(rejection.code, 422),
                detail=rejection.detail,
            )
        except TaskError as rejection:
            # A task reference that could not be resolved. Surfaced with the task
            # code rather than re-labelled, so "no such task" reads the same here
            # as it does everywhere else in the product.
            raise ApiError(
                code=rejection.code,
                message=rejection.message,
                status_code=_TASK_STATUS.get(rejection.code, 422),
                detail=rejection.detail,
            )

    @app.get(
        "/api/projects/{project_id}/context",
        dependencies=[Depends(require_context_caller)],
    )
    async def project_context(project_id: str) -> JSONResponse:
        """Bounded, cloud-eligible project context. **The only egress read.**

        M2J PR4, and the first route in this product whose response is shaped to
        leave the host. What it returns is a serialized `CloudContextProjection`
        produced by the named policy `project_context_external_v1` — never a
        `LocalContextPack`, which the serializer refuses by type.

        The caller names one project and nothing else. There is no field here for
        a path, a root, a Mind role, a `source_ref`, a policy id, an allowlist or
        a redaction rule, because a caller able to name what it receives would be
        deciding its own permissions.

        **Read-only, and idempotent.** No task, no event, no Working Context
        revision, no proposal, no write of any kind. Repeating it is free and
        changes nothing, which is why it carries no idempotency key.

        `no-store` for the same reason `/api/workspace/current` does: the body
        carries the operator's own words about their own project.
        """
        try:
            resolved = await run_in_threadpool(
                project_context_service.project_context, project_id
            )
            payload = await run_in_threadpool(serialize_project_context, resolved)
        except ProjectContextUnavailable as unavailable:
            # The reason is a closed code; the message is code-owned text. The
            # original exception never reaches here as a string, because an
            # upstream message is where a path escapes a refusal.
            raise ApiError(
                code=unavailable.reason,
                message=unavailable.message,
                status_code=_CONTEXT_STATUS.get(unavailable.reason, 409),
            )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @app.get("/api/workspaces", dependencies=[Depends(require_token)])
    async def list_workspaces() -> Dict[str, Any]:
        """Every configured workspace, by name, with whether its project is usable.

        Names and ids only. A workspace never publishes a path, because it never
        holds one — the directory belongs to the project, and the project does not
        publish it either.
        """
        await run_in_threadpool(workspaces.reload_workspaces)
        return await run_in_threadpool(workspaces.list_workspaces)

    @app.get("/api/workspace/current", dependencies=[Depends(require_token)])
    async def workspace_current() -> JSONResponse:
        """What are we working on right now.

        Never an error for an ordinary state. No workspace configured, none
        active, the active one renamed out of the file, its project disabled —
        each is a `problem` word on a 200, because every one of them is a real
        state of a working host and a client has to render it.

        `no-store`, because the body carries somebody's objective and expected
        next step: their own words about their own work, which is the same class
        of content as a task result and gets the same header.
        """
        payload = await run_in_threadpool(workspaces.current)
        return JSONResponse(content=payload, headers=TASK_CONTENT_HEADERS)

    @app.put("/api/workspace/active", dependencies=[Depends(require_token)])
    async def put_workspace_active(request: Request) -> JSONResponse:
        """Switch the active workspace, or clear it.

        `workspace_id: null` deactivates without forgetting anything: each
        workspace keeps its own objective and references, so switching away and
        back finds them where they were left.
        """
        payload = await _workspace_body(request, allowed={"workspace_id"})
        if "workspace_id" not in payload:
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="workspace_id is required",
                status_code=422,
                detail="pass a configured workspace id, or null to deactivate",
            )
        target = payload["workspace_id"]
        if target is None:
            result = await _run_workspace(workspaces.deactivate)
        else:
            result = await _run_workspace(workspaces.activate, target)
        return JSONResponse(content=result, headers=TASK_CONTENT_HEADERS)

    @app.put("/api/workspace/objective", dependencies=[Depends(require_token)])
    async def put_workspace_objective(request: Request) -> JSONResponse:
        """Set or clear the current objective on the active workspace.

        Its own route rather than a field on the one below, because this write
        has a consequence the others do not: the objective it replaces is
        appended to history in the same transaction. A caller should be able to
        see from the URL that they are changing the thing with a record.

        `source` is **not** a client field. It is assigned here from the
        authenticated surface, for the reason task `origin` is: a caller
        describing its own provenance is the opposite of what provenance is for.
        """
        payload = await _workspace_body(request, allowed={"objective"})
        if "objective" not in payload:
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="objective is required",
                status_code=422,
                detail="pass a short objective, or null to clear it",
            )
        result = await _run_workspace(workspaces.set_objective, payload["objective"])
        return JSONResponse(content=result, headers=TASK_CONTENT_HEADERS)

    @app.put("/api/workspace/context", dependencies=[Depends(require_token)])
    async def put_workspace_context(request: Request) -> JSONResponse:
        """Update bounded continuity fields on the active workspace.

        A **partial** update: only the keys present are touched, `null` clears,
        and absence leaves alone. That distinction is why unknown keys are
        refused rather than ignored — a typo that silently did nothing would look
        exactly like a value that was accepted.

        `active_task_id` must name a task Task Core actually has, in *this*
        workspace's project. Nothing here changes a task; the reference is a
        pointer and Task Core stays the authority for everything about it.
        """
        payload = await _workspace_body(
            request,
            allowed={
                "active_task_id",
                "expected_next_step",
                "plan_checkpoint",
                "pending_decision_ref",
                "latest_evidence_ref",
            },
        )
        if not payload:
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message="nothing to update",
                status_code=422,
                detail="pass at least one field to set or clear",
            )
        result = await _run_workspace(workspaces.update_context, payload)
        return JSONResponse(content=result, headers=TASK_CONTENT_HEADERS)

    @app.get("/api/workspace/objective-history", dependencies=[Depends(require_token)])
    async def workspace_objective_history(limit: int = 20) -> JSONResponse:
        """Previous objectives for the active workspace, newest first, bounded."""
        result = await _run_workspace(workspaces.objective_history, limit=limit)
        return JSONResponse(content=result, headers=TASK_CONTENT_HEADERS)

    # -- Cofferdam Mind (M2J PR2) --------------------------------------------
    #
    # Seven routes, all on `require_token`, and the authorization boundary here
    # is the strongest statement in the file: **acceptance is the authority to
    # write durable memory**, and D-2026-08-11-4 says the planner and the Actions
    # bridge have no acceptance route *at all*. Not a refusal — an absence. The
    # bridge holds a credential these routes have never heard of, so a bridge
    # request is a 401 because nothing here can recognise it, which is the same
    # structural argument D-2026-08-09-2 makes for Remote Control and is a
    # promise a later refactor cannot quietly lose.
    #
    # **No route writes configuration.** There is no grant route, no role-mapping
    # route, no vault route. `mind-grant.json` and the `documents` map in
    # `workspaces.json` are edited in a text editor on the workstation, for the
    # reason there is no create route for a project: configuration that can be
    # written over the network is access that can be granted over the network.
    #
    # **No body or path segment anywhere carries a filesystem path.** `scope` and
    # `role` are matched against closed code-owned vocabularies before anything
    # is resolved, so request text never becomes a path component; the proposal
    # id is Cofferdam-minted. A payload never publishes a root, a vault location
    # or a file name.

    async def _mind_body(request: Request, allowed: set) -> Dict[str, Any]:
        """One bounded mind request body, refusing anything unexpected.

        The same allowlist-at-the-door shape the workspace and task bodies use.
        It matters more here: the accept and reject routes allow *no* fields at
        all, so a client that tried to send its own `base_hash` — that is, to
        supply the value the whole hash binding exists to have Cofferdam
        determine — is refused rather than having it ignored.
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
                too_big = int(declared) > MAX_MIND_BODY_BYTES
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
        if len(raw) > MAX_MIND_BODY_BYTES:
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
                    else "this endpoint accepts no body fields"
                ),
            )
        return payload

    async def _run_mind(operation, *args, **kwargs) -> Any:
        try:
            return await run_in_threadpool(operation, *args, **kwargs)
        except MindError as rejection:
            raise ApiError(
                code=rejection.code,
                message=rejection.message,
                status_code=_MIND_STATUS.get(rejection.code, 422),
                detail=rejection.detail,
            )
        except WorkspaceError as rejection:
            # Surfaced with the workspace code rather than re-labelled: "no
            # workspace is active" reads the same here as it does on the
            # workspace routes, and sending somebody to the wrong file to fix it
            # is the failure `WorkspaceProjectMissing` was given its own code to
            # avoid.
            raise ApiError(
                code=rejection.code,
                message=rejection.message,
                status_code=_WORKSPACE_STATUS.get(rejection.code, 422),
                detail=rejection.detail,
            )

    @app.get("/api/mind", dependencies=[Depends(require_token)])
    async def mind_overview() -> JSONResponse:
        """Which memory roles are readable right now, and what is queued.

        Never an error for an ordinary state. No workspace active, no grant, a
        role mapped to a file that is not there — each is a word in the payload
        on a 200, because each is a real state of a working host and a client has
        to render it. That is the posture `GET /api/workspace/current` set.

        Metadata only: a role, whether it is available, its size and hash. Never
        a path, and never the content.
        """
        payload = await run_in_threadpool(mind.available)
        return JSONResponse(content=payload, headers=TASK_CONTENT_HEADERS)

    @app.get("/api/mind/documents/{scope}/{role}", dependencies=[Depends(require_token)])
    async def mind_document(scope: str, role: str) -> JSONResponse:
        """One approved document's content.

        Both path segments are matched against closed vocabularies before
        anything is resolved, so neither can become a path component. `no-store`,
        because the body is somebody's own memory — the same class of content as
        a task result, and it gets the same header.

        This is a **local** read. Nothing in this milestone forwards it to a
        model, a worker, the Custom GPT or a browser; the egress projection
        D-2026-08-11-5 requires is later work and does not exist yet.
        """
        payload = await _run_mind(mind.read_document, scope, role)
        return JSONResponse(content=payload, headers=TASK_CONTENT_HEADERS)

    @app.get("/api/mind/proposals", dependencies=[Depends(require_token)])
    async def mind_proposals(state: Optional[str] = None, limit: int = 20) -> JSONResponse:
        """Queued memory changes, newest first, bounded, without their content."""
        payload = await _run_mind(mind.list_proposals, state=state, limit=limit)
        return JSONResponse(content=payload, headers=TASK_CONTENT_HEADERS)

    @app.get("/api/mind/proposals/{proposal_id}", dependencies=[Depends(require_token)])
    async def mind_proposal(proposal_id: str) -> JSONResponse:
        """One proposal, with its content and whether the target has drifted.

        `stale` is computed on this read rather than stored, because it is the
        fact somebody uses to decide whether to press Accept and a stored copy
        would be right for a few seconds and then quietly wrong.
        """
        payload = await _run_mind(mind.get_proposal, proposal_id)
        return JSONResponse(content=payload, headers=TASK_CONTENT_HEADERS)

    @app.post("/api/mind/proposals", status_code=201, dependencies=[Depends(require_token)])
    async def create_mind_proposal(request: Request) -> JSONResponse:
        """Queue an intended change to an approved document. **Writes nothing.**

        Four fields and no others. There is no `path`, no `workspace_id` — the
        active workspace is the context, exactly as it is for the objective — and
        no `source`: provenance is assigned from the authenticated surface, for
        the reason task `origin` is.
        """
        payload = await _mind_body(request, allowed={"scope", "role", "content", "reason"})
        missing = sorted({"scope", "role", "content", "reason"} - set(payload))
        if missing:
            raise ApiError(
                code=CODE_INVALID_PARAMS,
                message=missing[0] + " is required",
                status_code=422,
                detail="a proposal needs a scope, a role, the whole document, and one line saying why",
            )
        result = await _run_mind(
            mind.create_proposal,
            payload["scope"],
            payload["role"],
            payload["content"],
            payload["reason"],
        )
        return JSONResponse(content=result, status_code=201, headers=TASK_CONTENT_HEADERS)

    @app.post("/api/mind/proposals/{proposal_id}/accept", dependencies=[Depends(require_token)])
    async def accept_mind_proposal(proposal_id: str, request: Request) -> JSONResponse:
        """Apply a proposal, atomically, bound to the hash it was made against.

        **The only route in this product that writes canonical memory**, and the
        only credential that reaches it is the device token. It takes no body
        fields at all: everything about what is written was fixed when the
        proposal was created and reviewed, and a field here would be a way to
        change the reviewed thing at the moment of approval.

        A document that changed since then is a `409 mind_proposal_stale` and
        nothing is written.
        """
        await _mind_body(request, allowed=set())
        result = await _run_mind(mind.accept_proposal, proposal_id)
        return JSONResponse(content=result, headers=TASK_CONTENT_HEADERS)

    @app.post("/api/mind/proposals/{proposal_id}/reject", dependencies=[Depends(require_token)])
    async def reject_mind_proposal(proposal_id: str, request: Request) -> JSONResponse:
        """Refuse a proposal. Touches no document.

        A decided proposal is not decided again: rejecting one that was already
        applied, rejected or found stale is a `409` naming the state, the same
        convention `task_already_finished` uses. History is not rewritten.
        """
        await _mind_body(request, allowed=set())
        result = await _run_mind(mind.reject_proposal, proposal_id)
        return JSONResponse(content=result, headers=TASK_CONTENT_HEADERS)

    # -- native Remote Control (M2H, Lane A) ---------------------------------
    #
    # Four operations, all authenticated by the same device token as everything
    # else, all taking a registered project id and nothing else. There is no
    # parameter anywhere below for a path, a unit name, a systemctl verb, a
    # Claude flag, a model or an executable — the project registry is the only
    # thing that names a directory, and the argv is a constant in
    # sessions/claude.py.
    #
    # These are private-client operations and they stay on `require_token`. The
    # M2I.5 Actions bridge holds a credential these four have never heard of, so
    # its requests arrive here as 401 — not as a refusal some later change could
    # relax. Handing a Remote Control session URL to an external model provider
    # would give it a live interactive agent on this workstation, and the way
    # that is prevented is that the bridge cannot authenticate to ask.

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
        # The only response in Cofferdam that carries capability material, and
        # therefore the only one that says so in its headers.
        #
        # ``no-store`` rather than ``no-cache``: no-cache still permits writing
        # the body to disk and revalidating it later, which for a bearer URL
        # means a capability sitting in a browser cache directory after the
        # session it opened is gone. ``Pragma`` is the HTTP/1.0 spelling, kept
        # for intermediaries that only understand that one.
        #
        # ``Referrer-Policy`` is the one that matters for *this* payload's
        # eventual destination: the client navigates a new tab to the URL, and
        # without it the Cofferdam page address would travel in the ``Referer``
        # header of that navigation.
        return JSONResponse(
            content={"link": payload},
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Referrer-Policy": "no-referrer",
            },
        )

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
        app.mount("/", RevalidatedStaticFiles(directory=str(WEB_ROOT), html=True), name="web")

    return app
