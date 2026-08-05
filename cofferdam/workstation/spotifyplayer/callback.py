"""The temporary loopback listener that receives Spotify's redirect.

This is a **separate trust boundary** from the rest of the service. Everything
else in Cofferdam is reachable over the tailnet and requires the device token;
this listener is reachable only from the workstation itself, requires no token,
and exists for at most a few minutes.

Those two facts have to be reconciled, and the reconciliation is the design:

* it binds to ``127.0.0.1`` and nothing else — never ``0.0.0.0``, never the
  Tailscale address. The bind address is a module constant, not configuration,
  so no deployment mistake can widen it;
* it serves exactly one path, ``/callback``, and answers everything else with
  404 without reading a query string;
* it stops on success, on failure, and on timeout, whichever comes first;
* it authorises nothing by itself. It hands the code and state to the caller,
  which validates the state against the live attempt before exchanging
  anything. A request that reaches this listener has proved only that it came
  from this machine.

The page a person actually sees says "you can close this tab" and carries no
token, no code, no account name — the browser tab is the least private surface
in the whole flow, it stays open in Opera's history, and a screenshot of it
should be worth nothing.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from .oauth import CALLBACK_HOST, CALLBACK_PATH, CALLBACK_PORT

# A callback URL is short. Anything longer is not one, and reading it would be
# doing work on behalf of something that has already failed.
MAX_REQUEST_LINE = 8 * 1024

SUCCESS_HTML = (
    "<!doctype html><meta charset=utf-8>"
    "<title>Cofferdam</title>"
    "<body style=\"font-family:system-ui;margin:3rem;line-height:1.5\">"
    "<h1>Spotify connected</h1>"
    "<p>Cofferdam can now control your Spotify playback.</p>"
    "<p>You can close this tab and go back to your phone.</p>"
)

FAILURE_HTML = (
    "<!doctype html><meta charset=utf-8>"
    "<title>Cofferdam</title>"
    "<body style=\"font-family:system-ui;margin:3rem;line-height:1.5\">"
    "<h1>Spotify not connected</h1>"
    "<p>Authorization did not complete. Nothing was changed.</p>"
    "<p>You can close this tab and try again from your phone.</p>"
)


@dataclass(frozen=True)
class CallbackResult:
    """What arrived at the loopback listener.

    ``code`` is the provider-standard temporary authorization code — the one
    value the flow requires to travel in a URL. It is carried here and consumed
    immediately; it is never logged, never audited, and never returned to a
    client.
    """

    code: Optional[str]
    state: Optional[str]
    error: Optional[str]

    @property
    def ok(self) -> bool:
        return bool(self.code) and not self.error


class _Handler(BaseHTTPRequestHandler):
    # Set by the server instance.
    server_version = "Cofferdam"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's interface
        if len(self.path) > MAX_REQUEST_LINE:
            self._respond(414, FAILURE_HTML)
            return

        split = urlsplit(self.path)
        if split.path != CALLBACK_PATH:
            # Not the callback. No query parsing, no state, no record kept.
            self._respond(404, FAILURE_HTML)
            return

        params = parse_qs(split.query, keep_blank_values=False)
        result = CallbackResult(
            code=_single(params.get("code")),
            state=_single(params.get("state")),
            error=_single(params.get("error")),
        )
        accepted = self.server.deliver(result)  # type: ignore[attr-defined]
        self._respond(200 if accepted else 400, SUCCESS_HTML if accepted else FAILURE_HTML)

    def do_POST(self) -> None:  # noqa: N802 - the callback is a redirect; POST is not it
        self._respond(405, FAILURE_HTML)

    def _respond(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        # This page must never be cached or indexed: it is transient and its URL
        # carried an authorization code.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt: str, *args) -> None:
        """Silence the default access log.

        ``BaseHTTPRequestHandler`` writes the full request line to stderr, and
        the request line of this particular request contains the authorization
        code. The default behaviour would put a credential into the daemon's
        log by accident, which is exactly the thing this milestone is careful
        about. Nothing here logs.
        """
        return


def _single(values) -> Optional[str]:
    """One value, or nothing. A repeated parameter is not a valid callback."""
    if not values or len(values) != 1:
        return None
    value = values[0]
    return value if isinstance(value, str) and value else None


class _Server(HTTPServer):
    allow_reuse_address = True

    def __init__(self, address, handler, on_result) -> None:
        super().__init__(address, handler)
        self._on_result = on_result

    def deliver(self, result: CallbackResult) -> bool:
        return bool(self._on_result(result))


class CallbackListener:
    """A loopback HTTP listener that lives only as long as one attempt.

    Used as a context manager so the socket is closed on every path out —
    success, failure, timeout, or an exception while opening the browser.
    """

    def __init__(self, host: str = CALLBACK_HOST, port: int = CALLBACK_PORT) -> None:
        if host != CALLBACK_HOST:
            # Structural. No caller can reach this, and if one ever could it
            # must fail rather than open an authorization endpoint to a network.
            raise ValueError("the Spotify callback listener binds to loopback only")
        self._host = host
        self._port = port
        self._server: Optional[_Server] = None
        self._thread: Optional[threading.Thread] = None
        self._received = threading.Event()
        self._result: Optional[CallbackResult] = None
        self._validator = None

    @property
    def bound_address(self) -> Optional[Tuple[str, int]]:
        return self._server.server_address if self._server else None

    def start(self, validator) -> None:
        """Begin listening. ``validator(result)`` decides whether to accept.

        The validator runs on the listener thread and returns ``True`` only for
        a callback whose state matches the live attempt, which is what makes an
        unrelated or replayed request produce a failure page and leave the
        attempt alone.
        """
        self._validator = validator
        self._server = _Server((self._host, self._port), _Handler, self._deliver)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="cofferdam-spotify-callback", daemon=True
        )
        self._thread.start()

    def _deliver(self, result: CallbackResult) -> bool:
        if self._received.is_set():
            # Already satisfied. A second delivery is a replay and is refused
            # without touching the stored result.
            return False
        accepted = bool(self._validator and self._validator(result))
        if accepted:
            self._result = result
            self._received.set()
        return accepted

    def wait(self, timeout_seconds: float) -> Optional[CallbackResult]:
        """Block until an accepted callback arrives, or the timeout passes."""
        if self._received.wait(timeout_seconds):
            return self._result
        return None

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def __enter__(self) -> "CallbackListener":
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()


__all__ = ["CallbackListener", "CallbackResult", "FAILURE_HTML", "MAX_REQUEST_LINE", "SUCCESS_HTML"]
