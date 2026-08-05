"""The only place Cofferdam talks to the public internet.

Everything about this module is a constraint rather than a convenience, because
it is the one component that sends a request somewhere Cofferdam does not
control.

**Fixed hosts.** :data:`ALLOWED_HOSTS` is a code-owned allowlist. A caller
passes a host from that tuple plus a path; there is no parameter anywhere that
accepts a full URL, so no provider response and no client request can ever
redirect this code at a host of its choosing.

**No redirects, at all.** ``http.client`` does not follow them and nothing here
adds that. A 3xx is a failure, not a hop. This is the single most important line
in the file: a followed redirect is exactly how an allowlist of hosts becomes an
allowlist of *first* hosts, and how a request carrying an ``Authorization``
header ends up delivering it somewhere else. It also makes the "cannot reach a
private address" property structural — there is no second connection to make.

**Always TLS, always verified.** :func:`ssl.create_default_context` with
hostname checking and certificate verification on, and no parameter to weaken
either. Plain HTTP is not reachable from here: the connection class is
``HTTPSConnection`` and the port is fixed at 443.

**Bounded in every dimension.** Connect/read timeout, response byte cap read
with an explicit ``read(n+1)`` so an over-long body is *detected* rather than
silently truncated into malformed JSON, bounded header count, and no retry loop.

**No proxy.** ``http.client`` does not consult ``http_proxy``/``https_proxy``,
which is a further reason to prefer it here over a library that does. A proxy
chosen by the environment would defeat the host allowlist.

Stdlib only, so the component with the widest blast radius adds no dependency
and stays exercised on the stdlib-only CI path.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple
from urllib.parse import urlencode

# Official provider hosts. A host not in this tuple cannot be reached by any
# code path in this package.
SPOTIFY_ACCOUNTS_HOST = "accounts.spotify.com"
SPOTIFY_API_HOST = "api.spotify.com"
YOUTUBE_API_HOST = "www.googleapis.com"

ALLOWED_HOSTS: Tuple[str, ...] = (
    SPOTIFY_ACCOUNTS_HOST,
    SPOTIFY_API_HOST,
    YOUTUBE_API_HOST,
)

HTTPS_PORT = 443

# Generous enough for a slow mobile uplink to a provider, short enough that a
# phone request never feels hung and the daemon never parks a worker thread.
CONNECT_TIMEOUT_SECONDS = 6.0
READ_TIMEOUT_SECONDS = 8.0

# A search response for five items is a few kilobytes. This cap is two orders of
# magnitude above that and still small enough that a hostile or broken provider
# cannot exhaust memory.
MAX_RESPONSE_BYTES = 512 * 1024

# Retries are deliberately absent as a loop. A caller may decide to make one
# further attempt for one specific, known-safe case (an expired token); there is
# no general retry, and nothing here ever sleeps.
MAX_ATTEMPTS = 1


class TransportError(Exception):
    """A transport-level failure, already reduced to a safe short reason.

    Carries no response body, no header block, and no request headers — the
    request headers are where the credential is.
    """

    def __init__(self, reason: str, *, timeout: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.timeout = timeout


@dataclass(frozen=True)
class Response:
    """A bounded provider response.

    ``headers`` is filtered down to the few this package acts on, so nothing
    incidental from a provider travels further into the process.
    """

    status: int
    body: bytes
    headers: Mapping[str, str]

    def json(self) -> object:
        """Decode JSON, or raise :class:`TransportError` with a constant reason.

        The decoder's own message quotes the offending input, so it is discarded:
        a provider response must never be able to write its own text into an
        error that might be shown or recorded.
        """
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise TransportError("the provider response was not valid JSON") from None

    @property
    def retry_after_seconds(self) -> Optional[int]:
        """``Retry-After`` as a bounded integer, when the provider sent one.

        Clamped to a day and floored at zero. It is returned for a human to
        read; no code path in Cofferdam sleeps on it.
        """
        raw = self.headers.get("retry-after")
        if not raw:
            return None
        try:
            seconds = int(str(raw).strip())
        except (TypeError, ValueError):
            # The HTTP-date form is legal but not worth parsing here; the caller
            # copes fine without a number.
            return None
        return max(0, min(seconds, 86400))


# Response headers this package is willing to look at.
_KEPT_HEADERS = ("retry-after", "content-type")


def _context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    # Both are already the defaults. Set explicitly so that a future edit
    # loosening them is a visible change to this line rather than a silent
    # inheritance of someone else's context.
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def request(
    host: str,
    path: str,
    *,
    method: str = "GET",
    query: Optional[Mapping[str, str]] = None,
    headers: Optional[Mapping[str, str]] = None,
    body: Optional[bytes] = None,
) -> Response:
    """One HTTPS request to an allowlisted host. No redirects, no retries.

    ``host`` must be in :data:`ALLOWED_HOSTS` and ``path`` must be an absolute
    path built by the calling adapter from its own constants. Neither is ever
    assembled from client input: query *values* come from validated user text
    and are percent-encoded here, but the host, path, and parameter names are
    the adapter's.
    """
    if host not in ALLOWED_HOSTS:
        # Structural, not defensive: no caller can construct this, and if one
        # ever could it must fail rather than dial.
        raise TransportError("that host is not an allowlisted provider endpoint")
    if not path.startswith("/"):
        raise TransportError("provider paths must be absolute")

    target = path
    if query:
        target = path + "?" + urlencode(dict(query))

    connection = http.client.HTTPSConnection(
        host,
        HTTPS_PORT,
        timeout=CONNECT_TIMEOUT_SECONDS,
        context=_context(),
    )
    try:
        connection.request(method, target, body=body, headers=dict(headers or {}))
        # Applied after connect so a provider that accepts the connection and
        # then stalls is still bounded.
        if connection.sock is not None:
            connection.sock.settimeout(READ_TIMEOUT_SECONDS)
        raw = connection.getresponse()

        # read(n + 1): reading exactly the cap cannot distinguish "exactly at
        # the limit" from "truncated here", and a truncated body would be
        # handed to json.loads as if it were complete.
        payload = raw.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise TransportError("the provider response exceeded the size limit")

        status = raw.status
        if 300 <= status < 400:
            # Never followed. See the module docstring.
            raise TransportError("the provider attempted a redirect, which is not followed")

        kept = {
            name: raw.getheader(name)
            for name in _KEPT_HEADERS
            if raw.getheader(name) is not None
        }
        return Response(status=status, body=payload, headers=kept)
    except TransportError:
        raise
    except socket.timeout:
        raise TransportError("the provider did not respond in time", timeout=True) from None
    except ssl.SSLError:
        # Deliberately not detailed: certificate diagnostics belong in the
        # host's own tooling, not in an API response.
        raise TransportError("the secure connection to the provider failed") from None
    except (http.client.HTTPException, OSError) as exc:
        if isinstance(exc, socket.timeout):  # pragma: no cover - covered above
            raise TransportError("the provider did not respond in time", timeout=True) from None
        raise TransportError("the provider could not be reached") from None
    finally:
        try:
            connection.close()
        except Exception:  # pragma: no cover - best effort
            pass


def form_body(fields: Mapping[str, str]) -> Tuple[bytes, Dict[str, str]]:
    """Encode a form body and its content-type header."""
    encoded = urlencode(dict(fields)).encode("ascii")
    return encoded, {
        "Content-Type": "application/x-www-form-urlencoded",
        "Content-Length": str(len(encoded)),
    }


__all__ = [
    "ALLOWED_HOSTS",
    "CONNECT_TIMEOUT_SECONDS",
    "MAX_ATTEMPTS",
    "MAX_RESPONSE_BYTES",
    "READ_TIMEOUT_SECONDS",
    "SPOTIFY_ACCOUNTS_HOST",
    "SPOTIFY_API_HOST",
    "YOUTUBE_API_HOST",
    "Response",
    "TransportError",
    "form_body",
    "request",
]
