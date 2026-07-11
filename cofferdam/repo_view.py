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
    size bound, a symlink, a non-regular file, an escape of the repository root,
    or an I/O error). Callers treat this as fail-closed."""


class _ContainmentError(Exception):
    """**Internal only** (not exported, not a supported API): the requested
    ``parts`` would escape the canonical repository root — lexically (``..`` /
    absolute / drive / UNC / device / embedded-separator component), through an
    intermediate symlink/reparse component, or by real-path resolution. It never
    carries the offending outside path. ``path_type`` swallows it into
    ``MISSING``; ``read_bytes`` re-raises it as a fixed ``RepoReadError``."""


class _MissingTarget(Exception):
    """**Internal only** (not exported, not a supported API): an *intermediate*
    path component was observed absent via ``FileNotFoundError`` during the
    containment walk. The repository-relative target is therefore **absent** for
    this operation and resolution terminates immediately — no deeper
    ``realpath`` / ``lstat`` / ``open`` is performed on any component below the
    missing one, and the final target is never inspected.

    This is the **ordinary absent contract**, deliberately kept distinct from a
    containment violation even though ``path_type`` maps both to ``MISSING``:
    ``read_bytes`` maps ``_MissingTarget`` to ``None`` (absent, so dry-run can
    still propose creating a file beneath a not-yet-existing directory), whereas
    it maps ``_ContainmentError`` to a fail-closed ``RepoReadError``."""


def _is_symlink_or_reparse(info: "os.stat_result") -> bool:
    """True for a POSIX symlink or a Windows junction/reparse point (which is not
    ``S_ISLNK``). Mirrors ``canonicalize._is_symlink_or_reparse``; kept local to
    avoid a circular import (``paths`` imports ``repo_view``). See PR02a plan
    §5c — a shared primitive is deferred technical debt."""
    if stat.S_ISLNK(info.st_mode):
        return True
    attrs = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attrs and reparse and (attrs & reparse))


def _validate_component(part: object) -> None:
    """Fail-closed lexical check of a single, atomic, repository-relative path
    component — **before** any join or filesystem access. Rejects ``.`` / ``..`` /
    empty, and any component containing ``/`` or ``\\`` (embedded separator, so
    a "component" can never traverse), or ``:`` (Windows drive-qualified/
    drive-relative, alternate-data-stream, and device forms). This duplicates
    ``paths.normalize_target`` deliberately: ``FilesystemRepoView`` is a public
    class and must self-defend, because the "every caller normalizes first"
    invariant is not structurally guaranteed (PR02a plan §5a). The offending text
    is never included in the raised error."""
    if not isinstance(part, str) or part == "" or part == "." or part == "..":
        raise _ContainmentError
    if "/" in part or "\\" in part or ":" in part:
        raise _ContainmentError
    # Reject C0 controls (incl. NUL/TAB/LF/CR/ESC) and DEL — parity with
    # ``normalize_target._has_control``. A NUL would truncate an OS path; other
    # controls are terminal-unsafe. Rejected here, before any join/lstat/open.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in part):
        raise _ContainmentError


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

    **Root containment is enforced, not merely assumed.** Every ``parts`` request
    is confined to the canonical repository root by ``_contained_target``:
    each component is validated lexically (no ``.`` / ``..`` / absolute / drive /
    UNC / device / embedded-separator forms) **before** any filesystem access,
    every *intermediate* component is ``lstat``-checked and a symlink/reparse
    component fails closed (whether it points inside or outside the root), and a
    belt-and-suspenders real-path check requires the resolved parent to stay
    beneath the root. A *final*-component symlink is still reported as
    ``SYMLINK`` by ``path_type`` (never followed); ``read_bytes`` rejects it. An
    escape is reported fail-closed — ``path_type`` → ``MISSING``, ``read_bytes``
    → ``RepoReadError`` — and the offending outside path is never disclosed.

    An *intermediate* component that is genuinely absent (``FileNotFoundError``
    from its ``lstat``) terminates resolution immediately as **missing**: no
    deeper component is inspected, so ``path_type`` → ``MISSING`` and
    ``read_bytes`` → ``None`` (the ordinary absent contract — distinct from a
    containment violation, which is an error for ``read_bytes``). A missing
    *final* component is likewise ``MISSING`` / ``None``.

    Uses ``lstat`` so a symlink is reported rather than followed. Performs no
    writes.

    **Accepted TOCTOU residual (v0.1, local-account trust — PR02a plan §5d):** a
    concurrently-running local process with write access under the same trusted
    OS account could replace a validated regular file (or a checked directory
    component) with a symlink between the ``lstat`` check and the ``open`` /
    next-``lstat``, bypassing the type check. This is within the v0.1 threat
    model (such an attacker already controls the repository root and the local
    account, which are part of the trust base). The check is **check-then-use,
    not race-free.** Descriptor-relative ``openat`` / ``O_NOFOLLOW`` traversal is
    deferred to PR4.
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

    def _contained_target(self, parts: Sequence[str]) -> Path:
        """Return the joined target ``Path`` **only** if ``parts`` stays inside
        the canonical root; otherwise raise ``_ContainmentError`` (fail-closed)
        or ``_MissingTarget`` (an absent intermediate — ordinary missing state).

        Order (PR02a plan §5a), with no filesystem access until step 2:
        1. lexical validation of every component (``_validate_component``);
        2. an ``lstat`` walk of every *intermediate* component — an intermediate
           symlink/reparse fails closed (``_ContainmentError``); a missing
           intermediate (``FileNotFoundError``) terminates resolution
           immediately as ``_MissingTarget`` — no deeper component is inspected
           and neither step 3's ``realpath`` nor the caller's final ``lstat`` /
           ``open`` runs below the absent component;
        3. a real-path check of the *parent* (never the final component, so a
           final symlink is preserved): the resolved parent must be at or beneath
           the root.
        """
        for part in parts:
            _validate_component(part)  # step 1 — before any join / FS access
        if not parts:
            return self._root  # the root itself; classified by the caller

        # step 2 — reject an intermediate symlink/reparse component.
        walked = self._root
        for part in parts[:-1]:
            walked = walked / part
            try:
                info = os.lstat(walked)
            except FileNotFoundError:
                # Absent intermediate: the target is missing for this operation.
                # Terminate NOW — inspect no deeper component, compute no deeper
                # realpath, and never open the final target. Nothing below an
                # absent path can be resolved, so continuing (the old ``break``)
                # would have let a later realpath/lstat/open reach a deeper
                # component that a caller mocked this lstat to hide.
                raise _MissingTarget
            except OSError:
                # Any *other* stat failure (PermissionError, NotADirectoryError,
                # transient/reparse I/O, ...) means we CANNOT prove the component
                # is not a symlink escape — fail closed rather than treat
                # "uninspectable" as "proven missing" and let step 3's realpath
                # (or a later open) traverse it. No raw path/OS text is carried.
                raise _ContainmentError
            if _is_symlink_or_reparse(info):
                raise _ContainmentError

        # step 3 — belt-and-suspenders: the resolved PARENT must stay under root
        # (computed on parts[:-1] so a final-component symlink is never followed).
        parent = self._root
        for part in parts[:-1]:
            parent = parent / part
        try:
            parent_real = Path(os.path.realpath(parent))
        except (OSError, ValueError):
            raise _ContainmentError
        if parent_real != self._root:
            try:
                parent_real.relative_to(self._root)
            except ValueError:
                raise _ContainmentError

        return self._root.joinpath(*parts)

    def path_type(self, parts: Sequence[str]) -> PathType:
        try:
            target = self._contained_target(parts)
        except _MissingTarget:
            return PathType.MISSING  # absent intermediate → missing, no deeper access
        except _ContainmentError:
            return PathType.MISSING  # escape → fail-closed, no outside disclosure
        try:
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

    def read_bytes(self, parts: Sequence[str]) -> Optional[bytes]:
        try:
            target = self._contained_target(parts)
        except _MissingTarget:
            # Absent intermediate → ordinary absent contract: return None (no
            # deeper lstat/open). read_prestate maps None to an "absent"
            # pre-state, so a dry-run can still create a file beneath a
            # not-yet-existing directory.
            return None
        except _ContainmentError:
            # Fixed category; never chain the OS error or leak the outside path.
            raise RepoReadError("target escapes the repository root")
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
