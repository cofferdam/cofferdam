"""Read-only D-Bus queries against the user's own session bus.

Cofferdam talks to the desktop through **published D-Bus interfaces**, which is
the semantic-interface rule (D-2026-08-04-7) applied to discovery: an interface
the desktop documents and versions, not a screen scrape and not an eval hook.

Three constraints shape this module:

* **Read-only by construction.** :func:`call_method` is only ever handed method
  names from a caller's own fixed table, and the callers here name only getters.
  Nothing in this package calls ``ApplyMonitorsConfig`` or any other mutator —
  a display-configuration change is not discovery.
* **The session's bus, not ours.** A service started by lingering before anyone
  logged in has no ``DBUS_SESSION_BUS_ADDRESS``. The address is taken from the
  systemd user manager's *live* environment, the same source the launch path
  uses, so discovery reaches the session that exists now rather than the one
  that existed at service start.
* **Bounded.** Every call has a timeout and a response-size ceiling. Mutter's
  ``GetCurrentState`` alone returns every mode of every monitor, which on a
  laptop with a dock is already tens of kilobytes.

``busctl --json=short`` is used rather than ``gdbus``: it emits JSON, so the
reply is parsed by :mod:`json` instead of by a hand-written GVariant text
reader. Fewer bytes of parser is fewer bytes that can be wrong about a device's
own description of itself.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Mapping, Optional, Sequence

from ..adapters.base import run_fixed
from ..errors import AdapterError

BUSCTL = "busctl"

# Variables ``busctl --user`` needs to find the session bus. Deliberately not
# the display variables: nothing here draws anything.
_BUS_ENVIRONMENT_KEYS = ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")

CALL_TIMEOUT_SECONDS = 5

# 4 MiB. Far above any real reply, far below "a runaway service ate memory".
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class DbusUnavailable(Exception):
    """The call could not be made, or the reply could not be believed.

    Carries a bounded, code-owned reason. It never carries the raw stderr of the
    call: that text can name a bus address and a socket path, and a discovery
    failure does not need either to be actionable.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def bus_environment(session_environment: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Environment for a ``busctl`` child: ours, plus the session's bus address.

    A variable the session does not publish is *removed* rather than left at our
    own stale value — pointing ``busctl`` at a bus socket from a session that
    has ended is how a query starts failing in a way that looks like "no
    displays" instead of "wrong session".
    """
    environment = dict(os.environ)
    published = session_environment or {}
    for key in _BUS_ENVIRONMENT_KEYS:
        value = published.get(key)
        if value:
            environment[key] = value
        else:
            environment.pop(key, None)
    return environment


def call_method(
    destination: str,
    object_path: str,
    interface: str,
    method: str,
    *,
    session_environment: Optional[Mapping[str, str]] = None,
    timeout: int = CALL_TIMEOUT_SECONDS,
) -> Sequence[Any]:
    """Call a no-argument D-Bus method and return its decoded reply fields.

    Every argument is supplied by the calling module from its own constants; no
    request text, registry value, or overlay ever reaches this function, so a
    caller can never be steered onto a different interface.
    """
    argv = [
        BUSCTL,
        "--user",
        "--json=short",
        "call",
        destination,
        object_path,
        interface,
        method,
    ]
    try:
        completed = run_fixed(
            argv, timeout=timeout, env=bus_environment(session_environment)
        )
    except AdapterError:
        raise DbusUnavailable(
            "the session bus could not be queried (busctl is not available or did not respond)"
        ) from None

    if completed.returncode != 0:
        raise DbusUnavailable(
            "the desktop service " + destination + " did not answer on the session bus"
        )

    if len(completed.stdout) > MAX_RESPONSE_BYTES:
        raise DbusUnavailable(
            "the reply from " + destination + " was larger than this build will parse"
        )

    try:
        payload = json.loads(completed.stdout.decode("utf-8", "replace"))
    except ValueError:
        raise DbusUnavailable("the reply from " + destination + " was not valid JSON") from None

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise DbusUnavailable("the reply from " + destination + " had an unexpected shape")
    return data


def variant_value(entry: Any) -> Any:
    """Unwrap ``busctl``'s ``{"type": ..., "data": ...}`` variant envelope.

    Values inside an ``a{sv}`` property dictionary arrive wrapped; values inside
    a fixed struct do not. Callers pass either without having to know which.
    """
    if isinstance(entry, dict) and "data" in entry and "type" in entry:
        return entry["data"]
    return entry


def variant_map(properties: Any) -> Dict[str, Any]:
    """Unwrap a whole ``a{sv}`` dictionary, tolerating anything unexpected."""
    if not isinstance(properties, dict):
        return {}
    return {key: variant_value(value) for key, value in properties.items()}


__all__ = [
    "BUSCTL",
    "CALL_TIMEOUT_SECONDS",
    "DbusUnavailable",
    "bus_environment",
    "call_method",
    "variant_map",
    "variant_value",
]
