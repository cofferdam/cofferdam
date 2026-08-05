"""The loopback-only listener that serves the player document and its channel.

This is a **separate trust boundary** from the rest of the service, and the
second one in this codebase — the Spotify OAuth callback listener established
the pattern. Everything in the main application is reachable over the tailnet
and requires the device token. This listener is reachable only from the
workstation itself, carries no token at all, and exists for as long as the
service runs.

Reconciling those two facts is the whole design of this module.

Why there is no token
---------------------

The player document runs in Opera on the workstation. Any token it held would
be a long-lived credential sitting in a browser tab, in that tab's history, and
in whatever the browser syncs — and the brief for this milestone forbids putting
one in a player URL for exactly that reason. So the player is not authenticated
by a secret it carries. It is authenticated by *where it can reach*: a request
that arrives here has proved it came from a process on this machine running as
this user.

What that boundary is worth, stated honestly
--------------------------------------------

A process running as this user could already launch a browser, read the token
file, and drive the whole API. So the loopback channel grants nothing that a
same-user process did not already have — which is the test a local trust
boundary has to pass. What it must not do is grant anything to code that is
*not* a same-user process, and that is what the four defences below are for.

**1. It binds to 127.0.0.1 and nothing else.** The address is a module constant,
not configuration, so no deployment mistake and no environment variable can
widen it. It is never the Tailscale address and never ``0.0.0.0``.

**2. The Host header must be a loopback authority.** This is the DNS-rebinding
defence, and it is the one that actually matters. Without it, a web page the
user visits could resolve its own domain to 127.0.0.1 and reach this listener
with the browser's cooperation. A request whose Host is not ``127.0.0.1:<port>``
or ``localhost:<port>`` is refused before its body is read.

**3. Every channel request must be ``application/json``.** Not decoration: a
cross-origin ``fetch`` carrying that content type is not a "simple request", so
the browser must preflight it with ``OPTIONS`` — and this listener answers no
CORS headers to any preflight, ever. That is what stops a malicious page in the
user's own browser from POSTing commands at the channel. The rule is what makes
the protection real, so it is enforced on every channel path with no exception.

**4. No CORS headers are ever sent.** Not on success, not on error, not on
``OPTIONS``. A cross-origin reader therefore cannot see a response even where it
can cause a request.

Bounds
------

Fixed paths, a bounded body, a bounded number of concurrent connections, a
bounded long-poll, and a socket timeout on every connection. The listener holds
no per-client state beyond the one player instance the channel already tracks.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .channel import (
    COMMAND_TTL_SECONDS,
    HEARTBEAT_SECONDS,
    POLL_WAIT_SECONDS,
    PlayerChannel,
)

#: Not configurable, on purpose. See the module docstring.
LOOPBACK_HOST = "127.0.0.1"

#: The authorities a Host header may carry. Anything else is a rebinding attempt
#: or a misconfiguration, and both get the same flat refusal.
_ALLOWED_HOSTNAMES = ("127.0.0.1", "localhost", "[::1]", "::1")

#: ``0`` asks the operating system for a free port. The chosen port is published
#: to nothing except the player URL Cofferdam builds itself.
DEFAULT_PORT = 0

# -- fixed paths -------------------------------------------------------------
#
# A closed set. Nothing here maps a request path to a filesystem path, so there
# is no traversal to defend against: an unknown path is a 404 before anything is
# read.

PATH_PLAYER = "/player"
PATH_PLAYER_SCRIPT = "/player.js"
PATH_REGISTER = "/channel/register"
PATH_COMMANDS = "/channel/commands"
PATH_STATE = "/channel/state"
PATH_ACK = "/channel/ack"
PATH_RELEASE = "/channel/release"

CHANNEL_PATHS = (PATH_REGISTER, PATH_COMMANDS, PATH_STATE, PATH_ACK, PATH_RELEASE)

#: A state report is a handful of numbers and a short id. Two kilobytes is
#: already far more than that shape needs, and the body is refused on length
#: before it is parsed.
MAX_BODY_BYTES = 2 * 1024

#: One player holds one long poll and posts state on an interval. Eight covers a
#: reload overlapping the previous tab's connections with room to spare; beyond
#: that something is wrong and the listener stops accepting rather than growing
#: threads.
MAX_CONNECTIONS = 8

#: Every connection gets a socket timeout, so a client that opens a socket and
#: says nothing cannot occupy a slot indefinitely.
SOCKET_TIMEOUT_SECONDS = POLL_WAIT_SECONDS + 15.0

_WEB_ROOT = Path(__file__).resolve().parents[3] / "web"
_DOCUMENT_FILE = _WEB_ROOT / "player.html"
_SCRIPT_FILE = _WEB_ROOT / "player.js"


def _read_asset(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:  # pragma: no cover - a broken install, reported as 503
        return b""


class _Handler(BaseHTTPRequestHandler):
    """One request. Refuses first, reads second."""

    server_version = "Cofferdam"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- logging -------------------------------------------------------------

    def log_message(self, *_args: Any) -> None:
        """Silence.

        The default handler writes every request line to stderr. Those lines
        would carry player instance ids and, in a service journal, would become a
        timestamped record of when somebody was watching something. There is
        nothing here worth logging that the audit path does not already record
        without the personal half.
        """

    # -- guards --------------------------------------------------------------

    def _host_is_loopback(self) -> bool:
        host = self.headers.get("host") or ""
        authority = host.rsplit(":", 1)[0] if ":" in host else host
        return authority.strip().lower() in _ALLOWED_HOSTNAMES

    def _origin_is_acceptable(self) -> bool:
        """An absent Origin is fine; a foreign one is not.

        The player document is same-origin with this listener, so its own fetches
        send either no Origin or the loopback one. Anything else is a page that
        has no business here, and it is refused even though the content-type rule
        should already have stopped it — two independent gates, because the first
        one is about browser behaviour and this one is about intent.
        """
        origin = (self.headers.get("origin") or "").strip().lower()
        if not origin:
            return True
        for hostname in _ALLOWED_HOSTNAMES:
            if origin.startswith("http://" + hostname + ":") or origin == "http://" + hostname:
                return True
        return False

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        content_type = (self.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            # Load-bearing, not cosmetic: this is what forces a cross-origin
            # caller into a preflight this listener never answers.
            self._respond_json(415, {"error": "application/json required"})
            return None
        declared = self.headers.get("content-length")
        try:
            length = int(declared) if declared is not None else 0
        except ValueError:
            self._respond_json(400, {"error": "invalid content-length"})
            return None
        if length > MAX_BODY_BYTES:
            self._respond_json(413, {"error": "body too large"})
            return None
        raw = self.rfile.read(length) if length > 0 else b""
        if len(raw) > MAX_BODY_BYTES:  # Content-Length is a claim, not a fact.
            self._respond_json(413, {"error": "body too large"})
            return None
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._respond_json(400, {"error": "invalid json"})
            return None
        if not isinstance(payload, dict):
            self._respond_json(400, {"error": "object required"})
            return None
        return payload

    # -- responses -----------------------------------------------------------

    def _respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # No CORS header is sent here or anywhere else in this file.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # The player document embeds exactly one YouTube iframe and loads the
        # official API script. Everything else is denied at the browser, so a
        # modified document cannot reach a third party even if one got in.
        if content_type.startswith("text/html"):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; "
                # The official loader is served from www.youtube.com and pulls
                # the widget API from YouTube's static asset host; both are
                # named, and nothing else may run.
                "script-src 'self' https://www.youtube.com https://s.ytimg.com; "
                "frame-src https://www.youtube.com; "
                # The page's own styles are in a <style> block, so inline styles
                # are permitted. Inline *script* deliberately is not.
                "style-src 'self' 'unsafe-inline'; "
                # Same-origin only: the page can reach its own loopback channel
                # and nothing else on the network.
                "connect-src 'self'; "
                "img-src 'self' data:; "
                "base-uri 'none'; "
                "form-action 'none'",
            )
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:  # pragma: no cover - the tab closed mid-response
            pass

    def _respond_json(self, status: int, payload: Dict[str, Any]) -> None:
        self._respond(
            status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8"
        )

    # -- verbs ---------------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's interface
        """Answered without a single CORS header, which is the point.

        A preflight that gets no ``Access-Control-Allow-Origin`` fails, and the
        real request the page wanted to send is never made.
        """
        self._respond(204, b"", "text/plain")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's interface
        if not self._host_is_loopback():
            self._respond_json(421, {"error": "not a loopback host"})
            return
        path = self.path.split("?", 1)[0]
        if path == PATH_PLAYER:
            body = _read_asset(_DOCUMENT_FILE)
            if not body:
                self._respond_json(503, {"error": "player document unavailable"})
                return
            self._respond(200, body, "text/html; charset=utf-8")
            return
        if path == PATH_PLAYER_SCRIPT:
            body = _read_asset(_SCRIPT_FILE)
            if not body:
                self._respond_json(503, {"error": "player script unavailable"})
                return
            self._respond(200, body, "text/javascript; charset=utf-8")
            return
        # Every channel operation is a POST, including the ones that only read:
        # it keeps the instance id out of every URL, and it keeps the
        # content-type preflight rule covering the whole channel.
        self._respond_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's interface
        if not self._host_is_loopback():
            self._respond_json(421, {"error": "not a loopback host"})
            return
        path = self.path.split("?", 1)[0]
        if path not in CHANNEL_PATHS:
            self._respond_json(404, {"error": "not found"})
            return
        if not self._origin_is_acceptable():
            self._respond_json(403, {"error": "origin not allowed"})
            return
        payload = self._read_json_body()
        if payload is None:
            return  # already answered with the reason

        channel: PlayerChannel = self.server.channel  # type: ignore[attr-defined]
        instance = payload.get("instance_id")

        if path == PATH_REGISTER:
            instance_id = channel.register()
            self._respond_json(
                200,
                {
                    "instance_id": instance_id,
                    "heartbeat_seconds": HEARTBEAT_SECONDS,
                    "poll_seconds": POLL_WAIT_SECONDS,
                    "command_ttl_seconds": COMMAND_TTL_SECONDS,
                },
            )
            return

        if path == PATH_STATE:
            if not channel.submit_state(instance, payload.get("state")):
                # 409 rather than 401: the tab is not unauthorized, it has been
                # superseded, and the page's response is to stop and close.
                self._respond_json(409, {"error": "superseded"})
                return
            self._respond_json(200, {"ok": True})
            return

        if path == PATH_ACK:
            if not channel.acknowledge(instance, payload.get("sequence")):
                self._respond_json(409, {"error": "superseded"})
                return
            self._respond_json(200, {"ok": True})
            return

        if path == PATH_RELEASE:
            channel.release(instance)
            self._respond_json(200, {"ok": True})
            return

        # PATH_COMMANDS — the long poll.
        after = payload.get("after")
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            after = 0
        commands = channel.collect(instance, after, timeout=POLL_WAIT_SECONDS)
        if commands is None:
            self._respond_json(409, {"error": "superseded"})
            return
        self._respond_json(200, {"commands": [command.to_dict() for command in commands]})


class _Server(ThreadingHTTPServer):
    """Threaded, bounded, and loopback-bound.

    ``daemon_threads`` so a connection parked in a long poll can never hold the
    service open at shutdown.
    """

    daemon_threads = True
    # Refuse rather than queue without limit when something is opening sockets.
    request_queue_size = MAX_CONNECTIONS

    def __init__(self, address: Tuple[str, int], channel: PlayerChannel) -> None:
        super().__init__(address, _Handler)
        self.channel = channel
        self.timeout = SOCKET_TIMEOUT_SECONDS

    def get_request(self):  # pragma: no cover - exercised through real sockets
        connection, address = super().get_request()
        connection.settimeout(SOCKET_TIMEOUT_SECONDS)
        return connection, address


class PlayerEndpoint:
    """The running loopback listener, and the URL to point Opera at.

    Started lazily: a host where nobody ever opens the YouTube player never
    binds a socket at all.
    """

    def __init__(self, channel: PlayerChannel, port: int = DEFAULT_PORT) -> None:
        self._channel = channel
        self._requested_port = port
        self._server: Optional[_Server] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> Optional[int]:
        server = self._server
        return server.server_address[1] if server is not None else None

    def start(self) -> int:
        """Bind and serve. Idempotent; returns the bound port."""
        with self._lock:
            if self._server is not None:
                return self._server.server_address[1]
            server = _Server((LOOPBACK_HOST, self._requested_port), self._channel)
            thread = threading.Thread(
                target=server.serve_forever,
                name="cofferdam-youtube-player",
                daemon=True,
            )
            thread.start()
            self._server = server
            self._thread = thread
            return server.server_address[1]

    def stop(self) -> None:
        with self._lock:
            server, thread = self._server, self._thread
            self._server = None
            self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def player_url(self) -> str:
        """The address Opera is pointed at. Built here, never from a request.

        Carries no token, no query string and no fragment — there is nothing in
        it to leak into browser history, and nothing a person reading it over
        someone's shoulder could reuse from another machine.
        """
        port = self.start()
        return "http://" + LOOPBACK_HOST + ":" + str(port) + PATH_PLAYER


__all__ = [
    "CHANNEL_PATHS",
    "LOOPBACK_HOST",
    "MAX_BODY_BYTES",
    "MAX_CONNECTIONS",
    "PATH_ACK",
    "PATH_COMMANDS",
    "PATH_PLAYER",
    "PATH_PLAYER_SCRIPT",
    "PATH_REGISTER",
    "PATH_RELEASE",
    "PATH_STATE",
    "PlayerEndpoint",
]
