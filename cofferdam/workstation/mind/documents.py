"""Turning an approved role into real bytes, and back, without leaving the root.

Three operations and nothing else: resolve, read, replace. Every one of them
starts from a root the host configured and a relative name the host configured,
and none of them takes anything a caller sent.

Resolution is descriptor-relative
---------------------------------

The obvious implementation walks the path with ``lstat``, decides it is safe, and
then opens it **by name**. That is two views of the filesystem with a gap between
them, and the gap is usable: an intermediate directory replaced between the check
and the open sends the open somewhere else entirely, and every later guarantee —
containment, the base hash, the atomic replace — is then about the wrong file.

So the check and the use are made the same act. A trusted descriptor is opened
on the verified root, each component below it is opened **relative to the
descriptor above it** with ``O_NOFOLLOW`` (and ``O_DIRECTORY`` where a directory
is required), and the final file is opened relative to its verified parent. From
that point the parent descriptor is held for the whole operation, so the read,
the hash, the temporary file and the rename all happen inside the directory that
was verified — not inside whatever that path resolves to a moment later.

``O_NOFOLLOW`` on every component is what makes a symlink a refusal rather than a
redirection, and it is enforced by the kernel at open time rather than by a
comparison this code performs afterwards.

**No pathname fallback.** If the platform cannot do descriptor-relative opens,
resolution fails closed rather than quietly reverting to the racy version. A
weaker guarantee that looks identical from the outside is worse than a refusal.
The supported production host is Ubuntu, where every primitive below is present.

The write rule
--------------

The temporary file is created **relative to the verified parent descriptor**,
`O_CREAT|O_EXCL|O_NOFOLLOW`, mode 0600; the target's own mode is copied onto it
with ``fchmod`` before the rename, so the content is never briefly more readable
than the file it replaces. ``os.rename`` with ``src_dir_fd`` and ``dst_dir_fd``
then swaps it in — atomically, and within one directory, so it cannot become a
copy across a filesystem boundary. The parent descriptor is ``fsync``ed after.

Nothing here runs a program. There is no ``git``, no shell, no ``shutil``, no
``subprocess`` — the file is written by this process, with literal arguments, or
not at all. On any failure the temporary file is removed and **the target is
byte-identical to what it was**, which is the property that lets a failed apply
leave the proposal decidable instead of in an unknown state.

What is deliberately absent
---------------------------

No delete. No rename of a canonical document. No move. No ``mkdir``. No
recursion. Not refused — *absent*: there is no function here that removes or
creates a canonical path, so there is nothing for a caller, a route, or a later
planner to reach.
"""

from __future__ import annotations

import errno
import os
import secrets
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Tuple

from ..tasks.errors import ProjectRootInvalid
from ..tasks.projects import verify_root
from .errors import ApplyFailed, ResolutionUnsupported, RoleUnavailable
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

_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _detect_descriptor_support() -> bool:
    """Whether this interpreter and platform can resolve without a pathname race.

    Every primitive is required. A partial capability would give a walk that is
    safe for three components and racy for the fourth, which is the shape of
    guarantee that reads as safe and is not.
    """
    return bool(
        _O_DIRECTORY
        and _O_NOFOLLOW
        and os.open in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
        # `os.replace` is not in `supports_dir_fd` on CPython/Linux; `os.rename`
        # is, and on POSIX the two are the same `rename(2)` with the same atomic
        # overwrite. The difference is Windows-only, and Windows has no dir_fd
        # support at all, so it fails this check on the line above.
        and os.rename in os.supports_dir_fd
    )


#: Evaluated once, at import. This is a property of the platform and the
#: interpreter, not of any particular call, and it cannot change while the
#: process runs — so re-deriving it per resolution would be waste, and worse,
#: would make the capability answer depend on whatever the module's `os`
#: attributes happen to be at that instant rather than on what the kernel can do.
_DESCRIPTOR_RESOLUTION_SUPPORTED = _detect_descriptor_support()


def descriptor_resolution_supported() -> bool:
    """Whether this platform can resolve a target without a pathname race."""
    return _DESCRIPTOR_RESOLUTION_SUPPORTED


@dataclass(frozen=True)
class DocumentState:
    """One document as it is on disk at a stated instant.

    **There is no path on this object.** Earlier revisions carried one so the
    caller could re-open the file; the caller now works through a held
    descriptor instead, which is both safer and removes the last structure in
    this package that could leak a location into a payload by accident.
    """

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


class ResolvedTarget:
    """An open, verified handle on one approved document's **parent directory**.

    Held for the whole operation. Everything done through it — reading, hashing,
    writing, renaming — happens relative to :attr:`parent_fd`, so no step
    re-resolves a pathname and no step can be sent somewhere else by a directory
    swapped in afterwards.

    :attr:`name` is the final component only, and it never leaves this module.
    """

    __slots__ = ("parent_fd", "name")

    def __init__(self, parent_fd: int, name: str) -> None:
        self.parent_fd = parent_fd
        self.name = name

    def _open_file(self) -> int:
        try:
            return os.open(
                self.name, os.O_RDONLY | _O_NOFOLLOW, dir_fd=self.parent_fd
            )
        except OSError as failure:
            raise _read_failure(failure)

    def read(self) -> Tuple[bytes, float]:
        """The document's exact bytes and modification time, from one descriptor.

        Both come from the same ``fstat``/``read`` pair rather than a read plus a
        separate ``stat`` by name, so they describe one file at one instant.
        """
        descriptor = self._open_file()
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
                _close(descriptor)

    def inspect(self) -> DocumentState:
        data, modified = self.read()
        return DocumentState(
            size=len(data), content_hash=document_hash(data), modified_at=modified
        )

    def mode(self) -> Optional[int]:
        """The target's own permission bits, or ``None`` if they cannot be read."""
        try:
            descriptor = os.open(
                self.name, os.O_RDONLY | _O_NOFOLLOW, dir_fd=self.parent_fd
            )
        except OSError:
            return None
        try:
            return stat.S_IMODE(os.fstat(descriptor).st_mode)
        except OSError:  # pragma: no cover - it was just opened
            return None
        finally:
            _close(descriptor)

    def replace(self, data: bytes) -> None:
        """Replace the document's whole content atomically, or change nothing.

        Every step is relative to :attr:`parent_fd`: the temporary file is
        created there, the rename happens there, and the directory ``fsync``
        happens on that descriptor. There is no pathname in this method, so
        there is nothing for a directory swap to redirect.
        """
        mode = self.mode()
        temporary = _TEMP_PREFIX + secrets.token_hex(8) + _TEMP_SUFFIX
        descriptor = None
        created = False
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW,
                0o600,
                dir_fd=self.parent_fd,
            )
            created = True
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None  # the context manager owns it now
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # Widened to the target's own mode only after the bytes are in, so
            # the content is never briefly more readable than the file it is
            # about to replace.
            _chmod_at(self.parent_fd, temporary, mode if mode is not None else _FALLBACK_FILE_MODE)
            os.rename(
                temporary,
                self.name,
                src_dir_fd=self.parent_fd,
                dst_dir_fd=self.parent_fd,
            )
            created = False
        except OSError as failure:
            raise ApplyFailed(type(failure).__name__)
        finally:
            if descriptor is not None:  # pragma: no cover - only if fdopen failed
                _close(descriptor)
            if created:
                # The rename did not happen, so this is ours to remove. Leaving
                # it would drop a stray dotfile into somebody's repository or
                # vault every time a disk filled up.
                try:
                    os.unlink(temporary, dir_fd=self.parent_fd)
                except OSError:
                    pass

        _fsync_descriptor(self.parent_fd)


def _read_failure(failure: OSError) -> RoleUnavailable:
    """Map an open failure to a truthful refusal, without naming the path."""
    code = getattr(failure, "errno", None)
    if code == errno.ELOOP:
        # What O_NOFOLLOW raises on a symlink.
        return RoleUnavailable(
            "the document is reached through a link, which is not accepted"
        )
    if code in (errno.ENOENT, errno.ENOTDIR):
        return RoleUnavailable("the document does not exist")
    return RoleUnavailable("the document cannot be read")


def _close(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:  # pragma: no cover - already closed
        pass


def _chmod_at(parent_fd: int, name: str, mode: int) -> None:
    """``chmod`` relative to a directory descriptor, without following links.

    ``os.chmod`` supports ``dir_fd`` on Linux; where it does not, the file is
    re-opened relative to the same descriptor and ``fchmod``-ed, which is the
    same guarantee by a different call. Both stay inside the verified parent.
    """
    if os.chmod in os.supports_dir_fd:
        os.chmod(name, mode, dir_fd=parent_fd, follow_symlinks=False)
        return
    descriptor = os.open(name, os.O_RDONLY | _O_NOFOLLOW, dir_fd=parent_fd)
    try:
        os.fchmod(descriptor, mode)
    finally:
        _close(descriptor)


def _fsync_descriptor(descriptor: int) -> None:
    """Make the rename itself durable, where the platform can.

    Best effort: the content was already fsynced before the rename, so a failure
    here costs the *ordering* guarantee after a crash and never the content.
    Raising would report a failure for a write that succeeded.
    """
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover - not supported on every filesystem
        pass


@contextmanager
def open_target(root: Path, relative: str) -> Iterator[ResolvedTarget]:
    """Open the verified parent of one approved document, or refuse.

    Raises :class:`~.errors.ResolutionUnsupported` where the platform cannot do
    this safely, and :class:`~.errors.RoleUnavailable` for every way the target
    can fail to be a readable regular file inside ``root`` — missing, a
    directory, a device, a link, a link *component*, or a path that leaves the
    root. One code for all of the latter on purpose: telling a client which one
    it was would describe the host's filesystem to it, one refusal at a time.

    The descriptor is held until the caller is done, which is what lets an
    apply hash the file and then replace it with no pathname resolved in
    between.
    """
    if not descriptor_resolution_supported():
        # Fail closed rather than falling back to a pathname walk. A weaker
        # guarantee that looks identical from the outside is worse than a
        # refusal, because nothing downstream would know it had been weakened.
        raise ResolutionUnsupported()

    # The root is still checked lexically and with `lstat` first. It is not
    # redundant with the descriptor walk: it is what produces a *useful* refusal
    # for a misconfigured root ("does not exist", "reached through a link")
    # rather than a bare open failure, and it is the same helper the project
    # registry uses, so a vault root and a project root cannot drift apart.
    try:
        verified = verify_root(Path(root))
    except ProjectRootInvalid as rejection:
        raise RoleUnavailable(str(rejection))

    segments = str(relative).split("/")
    if not segments or any(not segment or segment in (".", "..") for segment in segments):
        # Defence in depth: the loaders already refuse these lexically, and a
        # segment reaching here would mean a stored mapping bypassed them.
        raise RoleUnavailable("the document name is not a plain relative name")

    open_descriptors = []
    try:
        try:
            current = os.open(str(verified), os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
        except OSError as failure:
            raise _read_failure(failure)
        open_descriptors.append(current)

        # Every directory between the root and the document, opened relative to
        # the one above it. A symlink at any level is an ELOOP from the kernel,
        # not a comparison this code makes afterwards.
        for segment in segments[:-1]:
            try:
                current = os.open(
                    segment,
                    os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW,
                    dir_fd=open_descriptors[-1],
                )
            except OSError as failure:
                raise _read_failure(failure)
            open_descriptors.append(current)

        target = ResolvedTarget(parent_fd=open_descriptors[-1], name=segments[-1])

        # Prove the final component is an ordinary file *now*, through the same
        # descriptor the caller will use. A directory, a device or a symlink is
        # refused here rather than at first read.
        descriptor = target._open_file()
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise RoleUnavailable("the document is not an ordinary file")
        finally:
            _close(descriptor)

        yield target
    finally:
        for descriptor in reversed(open_descriptors):
            _close(descriptor)


def inspect_document(root: Path, relative: str) -> DocumentState:
    """Resolve and hash one document. Convenience for the read-only paths."""
    with open_target(root, relative) as target:
        return target.inspect()


def read_document(root: Path, relative: str) -> bytes:
    """The document's exact bytes, bounded."""
    with open_target(root, relative) as target:
        return target.read()[0]


__all__ = [
    "MAX_DOCUMENT_BYTES",
    "DocumentState",
    "ResolvedTarget",
    "descriptor_resolution_supported",
    "inspect_document",
    "open_target",
    "read_document",
]
