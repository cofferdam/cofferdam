"""Domain-separated, length-prefixed hashing utilities (pure).

These are the trust root's binding primitives. Every hash is computed over a
canonical byte structure that begins with a **fixed domain tag** and encodes
each variable field as a **length prefix** (fixed-width big-endian) followed by
the field bytes. Length-prefixing removes field-boundary ambiguity, so
``path="a", patch="bc"`` can never produce the same bytes as ``path="ab",
patch="c"``.

Nothing here touches the filesystem, the network, a subprocess, the clock, the
environment, or any mutable global state — the functions are pure and their
outputs are frozen by the known test vectors in ``tests/test_hashing.py``.

**Frozen serialization contract (v1) — do not change without a new tag/version;**
these constants are the contract PR3b's ledger and PR3d's audit chain rely on:

* algorithm: SHA-256, lowercase hex output;
* length prefix: unsigned **8-byte big-endian** byte count;
* the domain tag is a fixed constant prefix (not length-prefixed);
* ``bound_hash`` is **binding only, never authorization** — there is no nonce,
  no ``approval_id`` (``= SHA256(bound_hash || nonce)``), and no approval record
  anywhere in this module (all PR3b).
"""

from __future__ import annotations

import hashlib
from typing import Sequence

# --- frozen constants (the serialization contract; not illustrative) ---------

LENGTH_PREFIX_WIDTH = 8  # bytes, unsigned big-endian

TAG_PATH = b"cofferdam.path.v1"
TAG_PATCH = b"cofferdam.patch.v1"
TAG_PRESTATE = b"cofferdam.prestate.v1"
TAG_REPO_ROOT = b"cofferdam.reporoot.v1"
# Binding-only: this hash commits to *what* a change is; it is never
# authorization (no nonce, no approval_id). The tag says "binding", not
# "approval", to avoid PR3b semantic confusion.
TAG_BOUND = b"cofferdam.binding.v1"

# Pre-state descriptor kind sentinels (see prestate.py).
PRESTATE_ABSENT = b"absent"
PRESTATE_REGULAR = b"regular"

_MAX_FIELD = (1 << (LENGTH_PREFIX_WIDTH * 8)) - 1


def length_prefix(field: bytes) -> bytes:
    """Return ``len(field)`` as an 8-byte big-endian prefix followed by ``field``."""
    if not isinstance(field, (bytes, bytearray)):
        raise TypeError("length_prefix requires bytes")
    n = len(field)
    if n > _MAX_FIELD:
        raise ValueError("field too large to length-prefix")
    return n.to_bytes(LENGTH_PREFIX_WIDTH, "big") + bytes(field)


def domain_digest(tag: bytes, fields: Sequence[bytes]) -> str:
    """``SHA256(tag || lp(f0) || lp(f1) || ...)`` as lowercase hex."""
    hasher = hashlib.sha256()
    hasher.update(tag)
    for field in fields:
        hasher.update(length_prefix(field))
    return hasher.hexdigest()


def canonical_path_hash(path_bytes: bytes) -> str:
    return domain_digest(TAG_PATH, [path_bytes])


def patch_hash(patch_bytes: bytes) -> str:
    return domain_digest(TAG_PATCH, [patch_bytes])


def prestate_hash(descriptor_fields: Sequence[bytes]) -> str:
    """Hash a pre-state descriptor. ``descriptor_fields`` is the frozen field
    list built by ``prestate.PreState`` (kind, and for a regular file the raw
    32-byte content digest)."""
    return domain_digest(TAG_PRESTATE, descriptor_fields)


def repo_root_id(root_path_bytes: bytes) -> str:
    """Machine/repo-specific root identity (decision 9). Deterministic for the
    same canonical root path; different roots do not collide. No secrets."""
    return domain_digest(TAG_REPO_ROOT, [root_path_bytes])


def bound_hash(
    canonical_path_hash_hex: str,
    patch_hash_hex: str,
    prestate_hash_hex: str,
    repo_root_id_hex: str,
) -> str:
    """The tri-part binding hash over the component hashes + repo-root identity.

    **Binding only — never authorization.** Any change to the path, patch bytes,
    pre-state, or root yields a different value. The component hashes are the
    lowercase-hex strings above, encoded ASCII and length-prefixed.
    """
    return domain_digest(
        TAG_BOUND,
        [
            canonical_path_hash_hex.encode("ascii"),
            patch_hash_hex.encode("ascii"),
            prestate_hash_hex.encode("ascii"),
            repo_root_id_hex.encode("ascii"),
        ],
    )
