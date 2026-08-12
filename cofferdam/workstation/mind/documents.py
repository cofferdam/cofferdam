"""Turning an approved role into real bytes, and back, without leaving the root.

Three operations and nothing else: resolve, read, replace. Every one of them
starts from a root the host configured and a relative name the host configured,
and none of them takes anything a caller sent.

The resolution rule
-------------------

The root is re-verified with :func:`~..tasks.projects.verify_root` — reused
rather than reimplemented, so the vault and a project root are checked by the
same code — and then **every component below it is walked with ``lstat``**
before anything is opened. ``realpath`` alone is not enough and the reason is
specific: it would happily follow a link out of the vault and report success,
because the path it returns *is* a real file. The walk asks a different question
— is any step of this a link — and only once the answer is no is the resolution
trusted and compared back to where it should have landed.

Re-verified at **use**, never cached. A directory can be deleted, replaced by a
symlink, or swapped for a file between one request and the next, and the check
that matters is the one closest to the read. That is the posture
``task-projects.json`` already takes before every task.

The write rule
--------------

``mkstemp`` in the **target's own directory** → mode copied from the file being
replaced → write → ``flush`` → ``fsync`` → ``os.replace`` → a best-effort
``fsync`` of the directory. The temporary file is in the same directory because
``os.replace`` is only atomic within one filesystem; a temp file in ``/tmp``
would silently become a copy-then-delete across a mount boundary, which is the
non-atomic case this protocol exists to avoid.

Nothing here runs a program. There is no ``git``, no shell, no ``shutil``, no
``subprocess`` — the file is written by this process, with literal arguments, or
not at all. On any failure the temporary file is removed and **the target is
byte-identical to what it was**, which is the property that lets a failed apply
leave the proposal pending instead of in an unknown state.

What is deliberately absent
---------------------------

No delete. No rename. No move. No ``mkdir``. No recursion. Not refused —
*absent*: there is no function here that removes or creates a path, so there is
nothing for a caller, a route, or a later planner to reach.
"""

from __future__ import annotations

import errno
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from ..tasks.errors import ProjectRootInvalid
from ..tasks.projects import verify_root
from .errors import ApplyFailed, RoleUnavailable
from .hashing import document_hash

#: A memory document is a document a person reads. Half a megabyte is far past
#: any of Cofferdam's own — `DECISIONS.md` is the largest at well under 100 KB —
#: and small enough that a read cannot become a way to pull an arbitrary large
#: file through the API by pointing a role at it.
MAX_DOCUMENT_BYTES = 512 * 1024

#: Owner-only, used only when a target's own mode cannot be read. An existing
#: file keeps its own permissions: these are the user's documents, in the user's
#: repository or vault, and Cofferdam narrowing them on every write would be a
#: change nobody asked for that shows up as "why can my editor not save this".
_FALLBACK_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR

_TEMP_PREFIX = ".cofferdam-mind-"
_TEMP_SUFFIX = ".tmp"


@dataclass(frozen=True)
class DocumentState:
    """One document as it is on disk at a stated instant.

    Never published as-is: :attr:`path` stays on the host. The service copies
    the safe fields — size, hash, timestamp — into the payload and leaves this
    object behind.
    """

    path: Path
    size: int
    content_hash: str
    modified_at: float

    @property
    def modified_iso(self) -> str:
        from datetime import datetime, timezone

        return (
            datetime.fromtimestamp(self.modified_at, tz=timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )


def resolve_document(root: Path, relative: str) -> Path:
    """The real file for an approved mapping, or a refusal. Touches nothing.

    Raises :class:`~.errors.RoleUnavailable` for every way this can fail to be a
    readable regular file inside ``root`` — missing, a directory, a device, a
    link, a link *component*, or a path that resolves somewhere else. One code
    for all of them on purpose: telling a client which one it was would describe
    the host's filesystem to it, one refusal at a time.
    """
    try:
        verified = verify_root(Path(root))
    except ProjectRootInvalid as rejection:
        raise RoleUnavailable(str(rejection))

    expected = verified
    info = None
    try:
        for segment in str(relative).split("/"):
            expected = expected / segment
            info = os.lstat(expected)
            if stat.S_ISLNK(info.st_mode):
                raise RoleUnavailable(
                    "the document is reached through a link, which is not accepted"
                )
    except RoleUnavailable:
        raise
    except FileNotFoundError:
        raise RoleUnavailable("the document does not exist")
    except OSError:
        raise RoleUnavailable("the document cannot be read")

    if info is None or not stat.S_ISREG(info.st_mode):  # pragma: no cover - empty name
        raise RoleUnavailable("the document is not an ordinary file")

    try:
        resolved = Path(os.path.realpath(expected))
    except OSError:  # pragma: no cover - platform dependent
        raise RoleUnavailable("the document cannot be read")
    if resolved != expected:
        # Reachable when a component is a mount trick or the path normalizes
        # differently. Either way the file in use would not be the file that was
        # configured, and reading it is not something to guess about.
        raise RoleUnavailable("the document does not resolve to itself")
    if not os.access(expected, os.R_OK):
        raise RoleUnavailable("the document cannot be read")
    return expected


def _open_document(path: Path) -> int:
    """Open a document for reading, refusing a symlink at the final component.

    ``O_NOFOLLOW`` is the point. :func:`resolve_document` has already walked the
    path with ``lstat`` and found no link — but that check and this open are two
    separate syscalls, and between them the final component can be replaced. The
    flag closes that window by making the *open itself* refuse a link, so the
    file that gets read is a file that was not a link at the moment it was
    opened, rather than one that was not a link a moment earlier.

    It does not make the whole path atomic — an intermediate directory can still
    be swapped, which needs an ``openat`` walk — and it does not need to: the
    remaining window has the same shape as the project-root boundary Task Core
    already documents, and winning it requires write access to the vault as the
    user who owns it.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        return os.open(path, flags)
    except OSError as failure:
        # ELOOP is what O_NOFOLLOW raises on a symlink. Named separately so the
        # message is the truthful one rather than the generic read failure.
        if getattr(failure, "errno", None) == errno.ELOOP:
            raise RoleUnavailable(
                "the document is reached through a link, which is not accepted"
            )
        raise RoleUnavailable("the document cannot be read")


def read_document(path: Path) -> bytes:
    """The document's exact bytes, bounded.

    The size is checked from the open descriptor rather than from a prior
    ``stat`` of the name, so the thing measured is the thing read.
    """
    return _read_open_document(path)[0]


def _read_open_document(path: Path) -> Tuple[bytes, float]:
    """The bytes and the modification time, from **one** descriptor.

    Both come from the same ``fstat``/``read`` pair rather than from a second
    ``stat`` by name, so they describe one file at one instant instead of two
    lookups that a rename could land between.
    """
    descriptor = _open_document(path)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise RoleUnavailable("the document is not an ordinary file")
        if info.st_size > MAX_DOCUMENT_BYTES:
            raise RoleUnavailable("the document is larger than this version reads")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1  # the context manager owns it now
            data = stream.read(MAX_DOCUMENT_BYTES + 1)[:MAX_DOCUMENT_BYTES]
        return data, info.st_mtime
    except RoleUnavailable:
        raise
    except OSError:
        raise RoleUnavailable("the document cannot be read")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:  # pragma: no cover - already closed
                pass


def inspect_document(root: Path, relative: str) -> DocumentState:
    """Resolve and hash one document in a single pass."""
    path = resolve_document(root, relative)
    data, modified = _read_open_document(path)
    return DocumentState(
        path=path,
        size=len(data),
        content_hash=document_hash(data),
        modified_at=modified,
    )


def replace_document(path: Path, data: bytes) -> None:
    """Replace a document's whole content atomically, or change nothing.

    The mode is taken from the file being replaced. ``mkstemp`` creates the
    temporary file 0600, so the content is never briefly world-readable even
    when the target is more permissive — the widening happens after the bytes
    are in, and only up to what the target already was.
    """
    directory = path.parent
    try:
        mode: Optional[int] = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        mode = None

    handle = None
    temporary = None
    try:
        handle, temporary = tempfile.mkstemp(
            dir=str(directory), prefix=_TEMP_PREFIX, suffix=_TEMP_SUFFIX
        )
        with os.fdopen(handle, "wb") as stream:
            handle = None  # the context manager owns it now
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode if mode is not None else _FALLBACK_FILE_MODE)
        os.replace(temporary, path)
        temporary = None
    except OSError as failure:
        raise ApplyFailed(type(failure).__name__)
    finally:
        if handle is not None:  # pragma: no cover - only if fdopen itself failed
            try:
                os.close(handle)
            except OSError:
                pass
        if temporary is not None:
            # The replace did not happen, so this is ours to remove. Leaving it
            # would drop a stray dotfile into somebody's repository or vault
            # every time a disk filled up.
            try:
                os.unlink(temporary)
            except OSError:
                pass

    _fsync_directory(directory)


def _fsync_directory(directory: Path) -> None:
    """Make the rename itself durable, where the platform can.

    Best effort: the content was already fsynced before the replace, so a
    failure here costs the *ordering* guarantee after a crash and never the
    content. Raising would report a failure for a write that succeeded.
    """
    try:
        descriptor = os.open(str(directory), os.O_RDONLY)
    except OSError:  # pragma: no cover - platform dependent
        return
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover - not supported on every filesystem
        pass
    finally:
        os.close(descriptor)


__all__ = [
    "MAX_DOCUMENT_BYTES",
    "DocumentState",
    "inspect_document",
    "read_document",
    "replace_document",
    "resolve_document",
]
