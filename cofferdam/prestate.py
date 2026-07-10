"""Deterministic target pre-state descriptor (read-only).

The pre-state captures the target file's state at dry-run time, unambiguously,
so the executor (PR3c) can later detect any change. PR3a only *reads and
describes* it — nothing is written.

**Frozen descriptor contract (v1)** — the hashed field list:

* **absent** target → fields ``[b"absent"]``;
* **regular** file  → fields ``[b"regular", sha256(exact_bytes) (raw 32 bytes)]``.

An empty regular file is ``[b"regular", sha256(b"")]`` — provably distinct from
an absent target (``[b"absent"]``), which was the council's disambiguation
requirement. Mode/size are intentionally **not** bound in v0.1 (mode-change
diffs are already rejected by ``diffcheck``; mode binding is PR4). A non-regular
or symlink target never yields a descriptor — ``read_prestate`` fails closed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from . import hashing
from .repo_view import RepoReadError, RepoView


class PreStateError(Exception):
    """The target is not a describable pre-state (symlink / non-regular / I/O)."""


@dataclass(frozen=True)
class PreState:
    kind: str  # "absent" | "regular"
    content_sha256: Optional[bytes]  # raw 32 bytes for "regular"; None for "absent"

    def descriptor_fields(self) -> Tuple[bytes, ...]:
        if self.kind == "absent":
            return (hashing.PRESTATE_ABSENT,)
        if self.kind == "regular":
            assert self.content_sha256 is not None
            return (hashing.PRESTATE_REGULAR, self.content_sha256)
        raise PreStateError(f"unknown pre-state kind: {self.kind!r}")

    def hash_hex(self) -> str:
        return hashing.prestate_hash(self.descriptor_fields())


def prestate_from_bytes(data: Optional[bytes]) -> PreState:
    """Build a ``PreState`` from already-read bytes (``None`` == absent). Pure —
    used directly by tests with known vectors."""
    if data is None:
        return PreState(kind="absent", content_sha256=None)
    return PreState(kind="regular", content_sha256=hashlib.sha256(data).digest())


def read_prestate(repo_view: RepoView, parts: Sequence[str]) -> PreState:
    """Read the target through the read-only, bounded view and describe it.

    Fails closed (``PreStateError``) on a symlink, non-regular, over-size, or
    unreadable target. Reading never mutates.
    """
    try:
        data = repo_view.read_bytes(parts)
    except RepoReadError as exc:
        raise PreStateError(str(exc)) from exc
    return prestate_from_bytes(data)
