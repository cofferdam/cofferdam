"""Deterministic, test-only repo-view doubles.

Not a test module (underscore prefix → not collected) and **never imported by
production code**. ``InMemoryRepoView`` satisfies the read-only ``RepoView``
contract with no mutation API and the same size/type/symlink rejection rules as
the production ``FilesystemRepoView``. It is not a general editable virtual
filesystem: state is fixed at construction.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional, Sequence, Union

from cofferdam.repo_view import MAX_READ_BYTES, PathType, RepoReadError


class _Special:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<{self.name}>"


DIRECTORY = _Special("directory")
SYMLINK = _Special("symlink")
OTHER = _Special("other")

Entry = Union[bytes, _Special]


class InMemoryRepoView:
    """Read-only view over a fixed mapping of path-parts -> entry.

    Entry is ``bytes`` (a regular file), or one of the ``DIRECTORY`` / ``SYMLINK``
    / ``OTHER`` sentinels. A missing key is an absent path. There is deliberately
    no method to add, change, or remove entries after construction.
    """

    def __init__(
        self,
        entries: Optional[Mapping[Sequence[str], Entry]] = None,
        root: str = "/memory/repo",
    ) -> None:
        # Defensive copy: externally-held mutable mappings cannot change
        # observations after construction.
        frozen = {}
        for key, value in (entries or {}).items():
            frozen[tuple(key)] = value
        self._entries = MappingProxyType(frozen)
        # Synthetic, read-only root (test-only; not a real filesystem path).
        self._root = Path(root)

    def root_real_path(self) -> Path:
        return self._root

    def root_bytes(self) -> bytes:
        return unicodedata.normalize("NFC", self._root.as_posix()).encode("utf-8")

    def path_type(self, parts: Sequence[str]) -> PathType:
        entry = self._entries.get(tuple(parts))
        if entry is None:
            return PathType.MISSING
        if entry is DIRECTORY:
            return PathType.DIRECTORY
        if entry is SYMLINK:
            return PathType.SYMLINK
        if entry is OTHER:
            return PathType.OTHER
        return PathType.REGULAR

    def read_bytes(self, parts: Sequence[str]) -> Optional[bytes]:
        entry = self._entries.get(tuple(parts))
        if entry is None:
            return None
        if entry is SYMLINK:
            raise RepoReadError("target is a symlink")
        if entry is DIRECTORY or entry is OTHER:
            raise RepoReadError("target is not a regular file")
        assert isinstance(entry, (bytes, bytearray))
        if len(entry) > MAX_READ_BYTES:
            raise RepoReadError("target exceeds the maximum read size")
        return bytes(entry)
