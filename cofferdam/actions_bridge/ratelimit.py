"""Two token buckets and a concurrency gate. Small, and deliberately dumb.

The threat this defends against is not an attacker with the key — somebody
holding the external key can create tasks, and rate limiting only slows that
down. It is the *ordinary* failure: a retry storm. OpenAI's own production notes
say ChatGPT reduces its request frequency after repeated 429s, which makes a
correct 429 the cheapest way to stop a loop that would otherwise reach an agent
several times.

Two buckets, because reads and writes are not the same risk. A ``sync_task``
costs three loopback calls and returns text somebody has already produced. A
``create_task`` starts an agent, which costs real model usage and changes files
in a project. The tighter mutation bucket means a caller can poll freely and
still cannot start twenty tasks in a minute.

The concurrency gate refuses rather than queues. A request ChatGPT abandoned at
45 seconds is work nobody will read, and holding it open occupies a slot that a
live request needs.

Monotonic time throughout: a clock adjustment must not hand out a minute's worth
of tokens, and ``time.monotonic`` is the one clock that cannot move backwards.
"""

from __future__ import annotations

import threading
import time
from typing import Optional


class TokenBucket:
    """A bucket of ``burst`` tokens refilling at ``per_minute`` per minute."""

    def __init__(self, *, per_minute: int, burst: int) -> None:
        if per_minute <= 0 or burst <= 0:
            raise ValueError("a rate limit must be positive")
        self._rate = per_minute / 60.0
        self._burst = float(burst)
        self._tokens = float(burst)
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def take(self) -> bool:
        """One token, or ``False``. Never blocks and never sleeps."""
        with self._lock:
            now = time.monotonic()
            elapsed = max(0.0, now - self._updated)
            self._updated = now
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            if self._tokens < 1.0:
                return False
            self._tokens -= 1.0
            return True

    def retry_after_seconds(self) -> int:
        """A whole number of seconds until one token exists. At least one.

        Published in a ``Retry-After`` header. Rounded up rather than down: a
        header that says zero invites an immediate retry that will also fail.
        """
        with self._lock:
            if self._tokens >= 1.0:
                return 1
            missing = 1.0 - self._tokens
            return max(1, int(missing / self._rate) + 1)


class ConcurrencyGate:
    """At most ``limit`` requests in flight. A context manager that may refuse."""

    def __init__(self, *, limit: int) -> None:
        if limit <= 0:
            raise ValueError("a concurrency limit must be positive")
        self._semaphore = threading.BoundedSemaphore(limit)

    def acquire(self) -> bool:
        return self._semaphore.acquire(blocking=False)

    def release(self) -> None:
        try:
            self._semaphore.release()
        except ValueError:  # pragma: no cover - a double release is a bug
            pass


class RateLimiter:
    """The pair of buckets and the gate, as one object a route can ask."""

    def __init__(
        self,
        *,
        per_minute: int,
        burst: int,
        mutation_per_minute: int,
        mutation_burst: int,
        max_concurrent: int,
    ) -> None:
        self._all = TokenBucket(per_minute=per_minute, burst=burst)
        self._mutations = TokenBucket(
            per_minute=mutation_per_minute, burst=mutation_burst
        )
        self._gate = ConcurrencyGate(limit=max_concurrent)

    def check(self, *, mutation: bool) -> Optional[int]:
        """``None`` when allowed, or the ``Retry-After`` seconds when not.

        The general bucket is charged first and the mutation bucket second, so a
        refused mutation has still cost a general token — a caller cannot probe
        the mutation limit for free.
        """
        if not self._all.take():
            return self._all.retry_after_seconds()
        if mutation and not self._mutations.take():
            return self._mutations.retry_after_seconds()
        return None

    def enter(self) -> bool:
        return self._gate.acquire()

    def leave(self) -> None:
        self._gate.release()


__all__ = ["ConcurrencyGate", "RateLimiter", "TokenBucket"]
