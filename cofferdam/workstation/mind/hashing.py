"""The base-content hash a proposal is bound to.

Why this is here rather than imported from the Trust Core
---------------------------------------------------------

The Trust Core has exactly this primitive already — a domain-tagged,
length-prefixed SHA-256 in ``cofferdam/hashing.py``, whose serialization is a
**frozen contract** its ledger and audit chain depend on. Importing it would be
the shortest path and it is deliberately not taken. D-2026-08-11-4 adopts the
Trust Core's *posture* — fail-closed, hash-bound, single-use — "without
importing its machinery yet", and the reason that sentence is in a decision
record rather than a comment is that the coupling runs the wrong way: a memory
document is not an approval, and binding this milestone's on-disk hashes to a
contract frozen for a different purpose would mean either module's evolution
became the other's migration.

So the discipline is copied and the tag is our own. Same shape, same reasoning,
no shared frozen constant:

    SHA256( tag || 8-byte-big-endian-length || bytes )

The length prefix is not decoration on a single-field hash today. It is what
keeps this function extensible: the day a second field is bound in — a role, a
revision — the boundary between fields is already unambiguous, and the version
in the tag says which shape produced a stored value.

What it hashes
--------------

**The file's exact bytes, as they are on disk.** Not a normalized form, not a
decoded string, not a canonicalized Markdown tree. A person edits these files in
a text editor; if Cofferdam normalized before hashing, then a change that
Cofferdam considered cosmetic would let a stale proposal apply on top of an edit
somebody made — which is the one thing the base hash exists to prevent.
"""

from __future__ import annotations

import hashlib
from typing import Any

#: Versioned on purpose. A stored `base_hash` is a durable value, so a change to
#: what is hashed must produce a *different* tag rather than silently making old
#: rows compare unequal for a reason nobody can see.
TAG_DOCUMENT = b"cofferdam.mind.document.v1"

LENGTH_PREFIX_WIDTH = 8

#: Lowercase hex SHA-256.
DOCUMENT_HASH_CHARS = 64

_HEX = frozenset("0123456789abcdef")


def document_hash(data: bytes) -> str:
    """The bound hash of one document's exact bytes."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("document_hash requires bytes")
    hasher = hashlib.sha256()
    hasher.update(TAG_DOCUMENT)
    hasher.update(len(data).to_bytes(LENGTH_PREFIX_WIDTH, "big"))
    hasher.update(bytes(data))
    return hasher.hexdigest()


def valid_document_hash(value: Any) -> bool:
    """Shape check for a hash read back out of the database.

    Not a security boundary — the comparison is — but a stored value that is not
    a hash should fail closed at the point it is read rather than compare unequal
    and be reported as ordinary drift.
    """
    return (
        isinstance(value, str)
        and len(value) == DOCUMENT_HASH_CHARS
        and all(character in _HEX for character in value)
    )


__all__ = [
    "DOCUMENT_HASH_CHARS",
    "LENGTH_PREFIX_WIDTH",
    "TAG_DOCUMENT",
    "document_hash",
    "valid_document_hash",
]
