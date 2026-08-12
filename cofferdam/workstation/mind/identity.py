"""Proposal identifiers: sortable, random, opaque, never derived from content.

The same construction as :mod:`..tasks.identity`, and the same three reasons —
restated here rather than shared, because they land differently on this object.

**Unpredictable.** A proposal id is a handle that appears in the URL of the
route that *writes durable memory*. Every route re-checks the device token, so
the id is not a capability on its own, but an id somebody could guess would be a
way to probe whether a particular pending change exists. 80 bits is well past
what that needs.

**Not derived from anything.** Not the content, not its hash, not the role, not
the workspace. A content-derived id would put a fingerprint of somebody's draft
into every URL and every log line that carries the id — and the draft is exactly
the class of content the privacy rules keep out of those places.

**Sortable, without paying for it in randomness.** The first 48 bits are the
creation time in milliseconds, so proposals sort by age in an index and in a
listing. The timestamp reveals only when a proposal was made, which the record
already says in plain text.

The prefix is ``mprop_`` rather than ``prop_``: the Trust Core has a
:mod:`cofferdam.proposal` of its own, and two unrelated objects called
"proposal" in one repository should at least be distinguishable at a glance in a
log line.
"""

from __future__ import annotations

import secrets
import time
from typing import Optional

#: Crockford's alphabet: no I, L, O or U, so nothing reads as a digit and no
#: accidental word appears in an id.
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"

PROPOSAL_ID_PREFIX = "mprop_"
_TIME_BITS = 48
_RANDOM_BITS = 80
PROPOSAL_ID_BODY_CHARS = (_TIME_BITS + _RANDOM_BITS) // 5 + 1  # 26
PROPOSAL_ID_CHARS = len(PROPOSAL_ID_PREFIX) + PROPOSAL_ID_BODY_CHARS

_ALPHABET_SET = frozenset(_ALPHABET)


def _encode(value: int, length: int) -> str:
    digits = []
    for _ in range(length):
        digits.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(digits))


def new_proposal_id(now_ms: Optional[int] = None) -> str:
    """A fresh proposal id. Never reused, because it is never chosen."""
    stamp = int(time.time() * 1000) if now_ms is None else int(now_ms)
    stamp &= (1 << _TIME_BITS) - 1
    randomness = secrets.randbits(_RANDOM_BITS)
    return PROPOSAL_ID_PREFIX + _encode(
        (stamp << _RANDOM_BITS) | randomness, PROPOSAL_ID_BODY_CHARS
    )


def valid_proposal_id(value: object) -> bool:
    """Shape check for an id arriving in a URL path.

    Not the security boundary — the store's lookup is — but it keeps a malformed
    path from reaching a query at all, and it is what makes "the client cannot
    invent an id shape" testable.
    """
    if not isinstance(value, str) or len(value) != PROPOSAL_ID_CHARS:
        return False
    if not value.startswith(PROPOSAL_ID_PREFIX):
        return False
    return all(character in _ALPHABET_SET for character in value[len(PROPOSAL_ID_PREFIX) :])


__all__ = [
    "PROPOSAL_ID_CHARS",
    "PROPOSAL_ID_PREFIX",
    "new_proposal_id",
    "valid_proposal_id",
]
