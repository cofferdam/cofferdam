"""Injectable wall-clock abstraction (non-authority).

Supplies UTC epoch-second time for approval expiry checks. Production uses
``SystemClock`` (the real wall clock). Deterministic tests inject a fake clock
through the test-only double in ``tests/_approval_doubles.py``.

**Wall-clock only.** There is no monotonic cross-process claim: an approval is
created in one process and consumed/checked in another (or after a restart), so
only the persisted wall-clock ``created_at``/``expires_at`` are comparable. A
system clock rollback into a still-valid interval can therefore extend an
approval's practical lifetime beyond its TTL — a documented v0.1 limitation
(see ``SECURITY.md``); the only rollback that is detected here is ``now`` before
``created_at``.

No supported production entry point accepts a caller-selected clock — the clock
parameter on the approval functions exists only for the test double and for the
later PR3c internal wiring, which itself closes over ``SystemClock``.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Minimal read-only time source: whole UTC seconds since the epoch."""

    def now_epoch(self) -> int:
        ...


class SystemClock:
    """The production clock: real UTC wall-clock time, truncated to seconds."""

    def now_epoch(self) -> int:
        return int(time.time())
