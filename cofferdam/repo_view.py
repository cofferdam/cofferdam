"""Read-only repo-state view (minimal, injected).

Path classification must not read the filesystem directly: it asks a
``RepoView`` for the *type* of a path relative to the whitelisted root.
Injecting this keeps classification a pure function of explicit inputs and lets
tests supply a fake state. The view is strictly **read-only** — it never
creates, writes, or mutates anything.

PR2a needed only path typing (to block symlink components and non-regular
targets). PR3a adds a **bounded, read-only** ``read_bytes`` for pre-state
hashing. The view is still strictly read-only — it never creates, writes, or
mutates anything, and it never follows symlinks.
"""

from __future__ import annotations

import os
import stat
import unicodedata
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol, Sequence, runtime_checkable

# Maximum bytes a single target file may have for pre-state reading (PR3a
# decision 11 / MC2). Reading fails closed *before* allocating past this.
MAX_READ_BYTES = 10 * 1024 * 1024


def _canonical_root_bytes(real_root: Path) -> bytes:
    """Platform-neutral serialization of a canonical real root: NFC-normalized,
    POSIX-slash, UTF-8. Same contract used for target paths."""
    return unicodedata.normalize("NFC", real_root.as_posix()).encode("utf-8")


class PathType(str, Enum):
    MISSING = "missing"
    REGULAR = "regular"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    OTHER = "other"


class RepoReadError(Exception):
    """Raised by ``read_bytes`` when a target cannot be read safely (over the
    size bound, a symlink, a non-regular file, or an I/O error). Callers treat
    this as fail-closed."""


@runtime_checkable
class RepoView(Protocol):
    """The minimal read-only contract the trust core depends on.

    The view is the **single authoritative source of the repository root**: the
    same canonical root is used for target resolution, pre-state reading,
    canonical-path hashing, and ``repo_root_id``. There is no separate,
    independently-supplied root — a caller cannot hash root A while reading from
    root B.
    """

    def root_real_path(self) -> Path:
        """The canonical, absolute, real-path-resolved repository root. Read-only."""
        ...

    def root_bytes(self) -> bytes:
        """The platform-neutral serialization of the canonical root (for
        ``repo_root_id``). Deterministic; distinct roots yield distinct bytes."""
        ...

    def path_type(self, parts: Sequence[str]) -> PathType:
        """Classify ``parts`` (normalized components, relative to the root)
        **without following symlinks**. Must not mutate anything."""
        ...

    def read_bytes(self, parts: Sequence[str]) -> Optional[bytes]:
        """Return the exact bytes of the regular file at ``parts``, or ``None``
        if it is **absent**. Read-only, **never follows symlinks**, and bounded
        by ``MAX_READ_BYTES``. Raises ``RepoReadError`` for a symlink target, a
        non-regular target, an over-limit target, or any I/O error (fail-closed).
        Never mutates anything."""
        ...


class FilesystemRepoView:
    """A concrete read-only ``RepoView`` rooted at a real directory.

    Uses ``lstat`` so a symlink is reported as ``SYMLINK`` rather than followed.
    Performs no writes. Any error (including a path that escapes the root) is
    reported fail-closed as ``MISSING`` rather than raised.
    """

    def __init__(self, root: "os.PathLike[str] | str") -> None:
        # Canonicalize + validate the root once, at construction, so it is the
        # single authoritative source. A symlinked root is resolved to its real
        # target; the resolved root must exist and be a directory.
        try:
            real = Path(os.path.realpath(root))
        except (OSError, ValueError) as exc:
            raise ValueError("repository root is not resolvable") from exc
        if not real.is_dir():
            raise ValueError("repository root does not exist or is not a directory")
        self._root = real

    def root_real_path(self) -> Path:
        return self._root

    def root_bytes(self) -> bytes:
        return _canonical_root_bytes(self._root)

    def _resolve(self, parts: Sequence[str]) -> Path:
        target = self._root
        for part in parts:
            target = target / part
        return target

    def path_type(self, parts: Sequence[str]) -> PathType:
        try:
            mode = os.lstat(self._resolve(parts)).st_mode
        except (OSError, ValueError):
            return PathType.MISSING
        if stat.S_ISLNK(mode):
            return PathType.SYMLINK
        if stat.S_ISDIR(mode):
            return PathType.DIRECTORY
        if stat.S_ISREG(mode):
            return PathType.REGULAR
        return PathType.OTHER

    def read_bytes(self, parts: Sequence[str]) -> Optional[bytes]:
        target = self._resolve(parts)
        try:
            info = os.lstat(target)  # lstat: never follows a symlink
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise RepoReadError("stat failed") from exc
        mode = info.st_mode
        if stat.S_ISLNK(mode):
            raise RepoReadError("target is a symlink")
        if not stat.S_ISREG(mode):
            raise RepoReadError("target is not a regular file")
        if info.st_size > MAX_READ_BYTES:
            # Reject on the stat size *before* opening/allocating.
            raise RepoReadError("target exceeds the maximum read size")
        try:
            with open(target, "rb") as handle:
                # Read one byte past the bound to catch a file that grew between
                # stat and open; still bounded (never an unbounded read).
                data = handle.read(MAX_READ_BYTES + 1)
        except (OSError, ValueError) as exc:
            raise RepoReadError("read failed") from exc
        if len(data) > MAX_READ_BYTES:
            raise RepoReadError("target exceeds the maximum read size")
        return data
