"""Native supervised sessions — Lane A.

The Claude architecture has two lanes and they meet only at the project
registry.

**Lane B** is delegated work: a person sends a prompt from their phone, Task
Core records a task, and an adapter runs a headless Claude that reports back.
That lane lives in :mod:`..tasks`, and Cofferdam owns every part of it.

**Lane A** is this package: a native, interactive Claude Remote Control host,
supervised as a systemd *user* service, which the person then drives themselves
from claude.ai/code or the mobile app. Cofferdam starts it, stops it, and says
whether the process is up. It never sees a word of what is typed into it.

That asymmetry is the design, not a limitation waiting to be fixed. There is no
supported API for reading a native session's content, and the three ways to get
one anyway — scraping the UI, parsing transcript files, injecting prompts — are
each forbidden permanently under D-2026-08-08-3. So this package supervises a
*process*, and every state it reports is derived from evidence a systemd user
manager actually produced.

What this package contains
--------------------------

- :mod:`.model` — the status shape and the six lifecycle states, with no field
  that could hold conversation content.
- :mod:`.units` — the one place a project id becomes a unit name.
- :mod:`.systemd` — three fixed ``systemctl --user`` commands.
- :mod:`.supervisor` — the capability gate, idempotency, and the registry seam.
- :mod:`.claude` — Lane A's complete vocabulary for the native product.
- :mod:`.host` — the entry point the shipped unit template starts.

Nothing here is wired to a route. This is the foundation PR; the daemon
read/control routes, URL capture and truthful auth states arrive in M2H PR2.
"""

from __future__ import annotations

from .errors import (
    RemoteControlError,
    RemoteControlNotEnabled,
    SessionProjectDisabled,
    SessionProjectUnknown,
)
from .model import (
    KIND_CLAUDE_REMOTE_CONTROL,
    LIVE_STATES,
    STATES,
    STATE_FAILED,
    STATE_RUNNING,
    STATE_STARTING,
    STATE_STOPPED,
    STATE_STOPPING,
    STATE_UNKNOWN,
    NativeSessionStatus,
    map_active_state,
)
from .supervisor import RemoteControlSupervisor
from .systemd import SystemdUserBackend
from .units import TEMPLATE_FILENAME, UNIT_TEMPLATE, unit_name

__all__ = [
    "KIND_CLAUDE_REMOTE_CONTROL",
    "LIVE_STATES",
    "STATES",
    "STATE_FAILED",
    "STATE_RUNNING",
    "STATE_STARTING",
    "STATE_STOPPED",
    "STATE_STOPPING",
    "STATE_UNKNOWN",
    "TEMPLATE_FILENAME",
    "UNIT_TEMPLATE",
    "NativeSessionStatus",
    "RemoteControlError",
    "RemoteControlNotEnabled",
    "RemoteControlSupervisor",
    "SessionProjectDisabled",
    "SessionProjectUnknown",
    "SystemdUserBackend",
    "map_active_state",
    "unit_name",
]
