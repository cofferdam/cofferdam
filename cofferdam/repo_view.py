"""Read-only repo-state view (minimal, injected).

Path classification must not read the filesystem directly: it asks a
``RepoView`` for the *type* of a path relative to the whitelisted root.
Injecting this keeps classification a pure function of explicit inputs and lets
tests supply a fake state. The view is strictly **read-only** — it never
creates, writes, or mutates anything.

PR2a needs only path typing (to block symlink components and non-regular
targets). Content reading is intentionally absent; it is not added until a
later version needs it. Real-path canonicalization bound into an approval hash
is PR3, not here.
"""

from __future__ import annotations

import os
import stat
from enum import Enum
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable


class PathType(str, Enum):
    MISSING = "missing"
    REGULAR = "regular"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    OTHER = "other"


@runtime_checkable
class RepoView(Protocol):
    """The minimal read-only contract the path checks depend on."""

    def path_type(self, parts: Sequence[str]) -> PathType:
        """Classify ``parts`` (normalized components, relative to the root)
        **without following symlinks**. Must not mutate anything."""
        ...


class FilesystemRepoView:
    """A concrete read-only ``RepoView`` rooted at a real directory.

    Uses ``lstat`` so a symlink is reported as ``SYMLINK`` rather than followed.
    Performs no writes. Any error (including a path that escapes the root) is
    reported fail-closed as ``MISSING`` rather than raised.
    """

    def __init__(self, root: "os.PathLike[str] | str") -> None:
        self._root = Path(root)

    def path_type(self, parts: Sequence[str]) -> PathType:
        try:
            target = self._root
            for part in parts:
                target = target / part
            mode = os.lstat(target).st_mode
        except (OSError, ValueError):
            return PathType.MISSING
        if stat.S_ISLNK(mode):
            return PathType.SYMLINK
        if stat.S_ISDIR(mode):
            return PathType.DIRECTORY
        if stat.S_ISREG(mode):
            return PathType.REGULAR
        return PathType.OTHER
