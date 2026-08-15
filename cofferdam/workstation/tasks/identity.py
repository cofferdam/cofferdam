"""Task identifiers: sortable, random, opaque, and never derived from content.

Three properties, in the order they matter.

**Unpredictable.** A task id is a handle an authenticated client holds and uses
in a URL path. It is not a capability on its own — every route re-checks the
token — but an id a third party could *guess* would still be a way to probe
whether a particular task exists. 80 bits of randomness is well past what that
needs.

**Not derived from anything.** Not the prompt, not a hash of it, not the pid,
not the project, not a counter. A prompt-derived id would put a fingerprint of
somebody's private text into every URL, every log line and every audit record
that carries the id — the one place the privacy rules say content must never
reach. A pid-derived id would additionally be wrong: pids are reused within
hours, and the whole point of a durable task is that it outlives the process.

**Sortable, without paying for it in randomness.** The first 48 bits are the
creation time in milliseconds, so ids sort by age in a database index and in a
listing. That is a convenience and it is *added* to the random part, never taken
out of it: the timestamp reveals only when a task was created, which the record
already says in plain text.

The encoding is Crockford base32, lowercase, which gives an id that survives
being read aloud, double-clicked, and pasted into a shell without quoting.
"""

from __future__ import annotations

import secrets
import time
from typing import Optional

#: Crockford's alphabet: no I, L, O or U, so nothing reads as a digit and no
#: accidental word appears in an id.
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"

TASK_ID_PREFIX = "task_"
#: 48 bits of time + 80 bits of randomness = 128 bits = 26 base32 characters.
_TIME_BITS = 48
_RANDOM_BITS = 80
TASK_ID_BODY_CHARS = (_TIME_BITS + _RANDOM_BITS) // 5 + 1  # 26
TASK_ID_CHARS = len(TASK_ID_PREFIX) + TASK_ID_BODY_CHARS

CORRELATION_PREFIX = "tcor-"
_CORRELATION_BYTES = 8
CORRELATION_ID_CHARS = len(CORRELATION_PREFIX) + _CORRELATION_BYTES * 2

_ALPHABET_SET = frozenset(_ALPHABET)


def _encode(value: int, length: int) -> str:
    digits = []
    for _ in range(length):
        digits.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(digits))


def new_task_id(now_ms: Optional[int] = None) -> str:
    """A fresh task id. Never reused, because it is never chosen.

    ``now_ms`` exists for tests that need two ids from the same millisecond; it
    cannot make two ids collide, since the low 80 bits are drawn fresh either
    way.
    """
    stamp = int(time.time() * 1000) if now_ms is None else int(now_ms)
    stamp &= (1 << _TIME_BITS) - 1
    randomness = secrets.randbits(_RANDOM_BITS)
    return TASK_ID_PREFIX + _encode((stamp << _RANDOM_BITS) | randomness, TASK_ID_BODY_CHARS)


def valid_task_id(value: object) -> bool:
    """Shape check for an id arriving in a URL path.

    Not a security boundary — the store's lookup is — but it keeps a malformed
    path from reaching a query at all, and it is what makes "the client cannot
    invent an id shape" testable.
    """
    if not isinstance(value, str) or len(value) != TASK_ID_CHARS:
        return False
    if not value.startswith(TASK_ID_PREFIX):
        return False
    return all(character in _ALPHABET_SET for character in value[len(TASK_ID_PREFIX) :])


EVALUATION_ID_PREFIX = "evl_"
EVALUATION_ID_BODY_CHARS = TASK_ID_BODY_CHARS
EVALUATION_ID_CHARS = len(EVALUATION_ID_PREFIX) + EVALUATION_ID_BODY_CHARS


def new_evaluation_id(now_ms: Optional[int] = None) -> str:
    """A fresh evaluation id (M2K PR7). **Server-minted, always.**

    Lives here rather than in :mod:`.evaluation` for a reason worth stating: that
    module is a *pure* evaluator and is held to it structurally — it may not
    import ``time`` or ``secrets``, because a clock and a random source are
    exactly the two things that would let a "deterministic" judgement stop being
    one. Minting an identifier needs both, so it happens outside the purity
    boundary, and the id is never an input to the evaluation fingerprint.

    Same construction as :func:`new_task_id`, for the same three reasons, and
    with the same rule: no caller, adapter or worker supplies one.
    """
    stamp = int(time.time() * 1000) if now_ms is None else int(now_ms)
    stamp &= (1 << _TIME_BITS) - 1
    randomness = secrets.randbits(_RANDOM_BITS)
    return EVALUATION_ID_PREFIX + _encode(
        (stamp << _RANDOM_BITS) | randomness, EVALUATION_ID_BODY_CHARS
    )


def valid_evaluation_id(value: object) -> bool:
    if not isinstance(value, str) or len(value) != EVALUATION_ID_CHARS:
        return False
    if not value.startswith(EVALUATION_ID_PREFIX):
        return False
    return all(
        character in _ALPHABET_SET for character in value[len(EVALUATION_ID_PREFIX) :]
    )


def new_correlation_id() -> str:
    """A random handle for one operation, meaningless on its own.

    The same device the Spotify and YouTube milestones use, and safe for the
    same reason: it carries nothing about the task, so it can appear in an audit
    record where the prompt never can, and still line up a refusal with the
    operation that produced it.
    """
    return CORRELATION_PREFIX + secrets.token_hex(_CORRELATION_BYTES)


def valid_correlation_id(value: object) -> bool:
    if not isinstance(value, str) or len(value) != CORRELATION_ID_CHARS:
        return False
    if not value.startswith(CORRELATION_PREFIX):
        return False
    return all(c in "0123456789abcdef" for c in value[len(CORRELATION_PREFIX) :])


__all__ = [
    "CORRELATION_ID_CHARS",
    "CORRELATION_PREFIX",
    "EVALUATION_ID_CHARS",
    "EVALUATION_ID_PREFIX",
    "TASK_ID_CHARS",
    "TASK_ID_PREFIX",
    "new_correlation_id",
    "new_evaluation_id",
    "new_task_id",
    "valid_evaluation_id",
    "valid_correlation_id",
    "valid_task_id",
]
