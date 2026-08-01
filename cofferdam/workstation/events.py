"""Live event channel (WebSocket).

One multiplexed stream to every connected client. Events:

* ``hello``            — sent on accept: API version, adapter, recent actions.
* ``action_started``   — an action began (record attached).
* ``action_finished``  — an action reached a terminal state (record attached).
* ``status``           — periodic host status refresh.
* ``heartbeat``        — keepalive so clients can detect a dead link.

Authentication is enforced **before** accept, so an unauthenticated socket is
never upgraded. Tokens are accepted through the ``Sec-WebSocket-Protocol``
subprotocol (preferred — browsers cannot set headers on a WebSocket, and this
keeps the token out of URLs and access logs) or, as a fallback for clients that
cannot negotiate subprotocols, a ``token`` query parameter.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Dict, Set

from starlette.websockets import WebSocket, WebSocketState

TOKEN_SUBPROTOCOL = "cofferdam-token"
HEARTBEAT_SECONDS = 20.0
STATUS_REFRESH_SECONDS = 5.0


class EventHub:
    """Fan-out to all connected clients; slow/broken sockets are dropped."""

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def register(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.add(websocket)

    async def unregister(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def broadcast(self, event: str, payload: Dict[str, Any]) -> None:
        message = {"event": event, "data": payload}
        async with self._lock:
            targets = list(self._clients)
        dead = []
        for client in targets:
            if client.client_state is not WebSocketState.CONNECTED:
                dead.append(client)
                continue
            try:
                await client.send_json(message)
            except Exception:
                dead.append(client)
        if dead:
            async with self._lock:
                for client in dead:
                    self._clients.discard(client)

    def broadcast_threadsafe(self, event: str, payload: Dict[str, Any]) -> None:
        """Broadcast from a worker thread (the action executor runs there)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            asyncio.run_coroutine_threadsafe(self.broadcast(event, payload), loop)
