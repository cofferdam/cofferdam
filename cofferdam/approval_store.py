"""Append-only, single-use approval ledger — persistence, locking, path safety.

This is the on-disk half of the PR3b authorization-state layer. It owns the
fixed, protected state files under the repository's own ``.cofferdam/`` workspace
and the exact append/fsync/lock protocol that makes single-use consumption
atomic and crash-consistent. It contains **no authority logic**: it generates no
identifiers, mints nothing, and gates nothing — it validates, reads, folds
(via ``approval``), and appends canonical lines. All authority decisions live in
``approval``; the mint itself is PR3c1.

Fixed paths, derived solely from the repo view (never caller-controlled):

* ``<repo_root>/.cofferdam/``            (dir, POSIX 0700)
* ``<repo_root>/.cofferdam/approvals.jsonl``  (ledger, POSIX 0600)
* ``<repo_root>/.cofferdam/approvals.lock``   (lock file, 1 byte 0x00, POSIX 0600)

Integrity model: **ledger integrity is the authorization property.** There is no
per-record MAC (no key exists, and an actor who can write the file can recompute
any MAC). Integrity rests on file permissions + append-only + a fail-closed fold
+ the host assumption (the agent cannot directly edit ``.cofferdam/`` or run
arbitrary in-process Python). See ``SECURITY.md``.

**Thread-safety:** an ``ApprovalStore`` is *not* thread-safe within a process;
use one per operation. The OS advisory lock serializes cross-thread/cross-process
access. Advisory locks bind only cooperating processes and may be unenforced on
some network/overlay mounts (a best-effort warning is emitted).
"""

from __future__ import annotations

import os
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

from . import approval
from .approval import LedgerError

# Caps (fail-closed on read; append refuses to exceed).
MAX_LINE_BYTES = 4096
MAX_LEDGER_BYTES = 4 * 1024 * 1024
MAX_ENTRIES = 10_000

LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_RETRY_SLEEP = 0.02
_LOCK_BYTE = b"\x00"

_WINDOWS = os.name == "nt"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRFLAG = getattr(os, "O_DIRECTORY", 0)
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)

if _WINDOWS:  # pragma: no cover - platform-specific
    import msvcrt
else:
    import fcntl


def _is_symlink_or_reparse(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    attrs = getattr(info, "st_file_attributes", 0)
    return bool(_REPARSE and attrs and (attrs & _REPARSE))


def _lstat_or_none(path: Path) -> Optional[os.stat_result]:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise LedgerError("cannot stat state path") from exc


class _OsLocker:
    """Exclusive advisory lock via ``fcntl.flock`` (POSIX) / ``msvcrt.locking``
    (Windows, CPython only), with a bounded non-blocking retry. Released by the
    OS on process death, so there is no stale lock."""

    def acquire(self, fd: int, deadline: float) -> None:
        while True:
            try:
                if _WINDOWS:  # pragma: no cover - platform-specific
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise LedgerError("could not acquire the approval lock (timeout)")
                time.sleep(_LOCK_RETRY_SLEEP)

    def release(self, fd: int) -> None:
        if _WINDOWS:  # pragma: no cover - platform-specific
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)


class _ApprovalStore:
    """Persistence + locking for the approval ledger.

    **Unsupported internal infrastructure — NOT a supported public API and NOT a
    mint.** Constructing this class and calling its append methods is not a human
    approval boundary and does not represent one; it exists only so that PR3c1's
    (future) human-mediated, TTY-gated mint and PR3b's consume/lookup can persist
    and read records. External/integrator code must not construct or use it — the
    supported surface is ``approval.find_valid_approval`` / ``consume_approval``
    (which take only a ``RepoView``) and the read-only ``approval-status`` CLI.
    PR3c1 will own the only supported production mint path. The write methods
    (``_append_approval``/``_append_consumption``) bear no authority: they merely
    serialize an already-validated record; authority comes solely from a valid,
    active, unconsumed record existing in the protected ledger.

    Not thread-safe within a process; the OS advisory lock serializes access."""

    def __init__(self, repo_view, *, locker: Optional[_OsLocker] = None,
                 lock_timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
        root = Path(repo_view.root_real_path())
        self._dir = root / ".cofferdam"
        self._ledger = self._dir / "approvals.jsonl"
        self._lock_path = self._dir / "approvals.lock"
        # Non-authority test seams (not exposed through any production entry point).
        self._locker = locker or _OsLocker()
        self._lock_timeout = lock_timeout
        self._lock_self_check_done = False

    # -- existence -----------------------------------------------------------

    def state_exists(self) -> bool:
        """True iff both the state dir and the ledger are present. Presence only;
        deep type/permission validation happens on lock/read (and fails closed)."""
        return os.path.lexists(self._dir) and os.path.lexists(self._ledger)

    # -- locking -------------------------------------------------------------

    @contextmanager
    def lock(self, *, create: bool):
        """Hold the exclusive lock across the whole critical section. ``create``
        is True only for the (PR3c1/test) append path; reads use ``create=False``
        and never create state."""
        self._ensure_dir(create)
        self._ensure_lockfile(create)
        fd = os.open(self._lock_path, os.O_RDWR | _NOFOLLOW)
        try:
            self._validate_regular_fd(fd)
            self._lock_self_check(fd)
            deadline = time.monotonic() + self._lock_timeout
            self._locker.acquire(fd, deadline)
            try:
                yield self
            finally:
                self._locker.release(fd)
        finally:
            os.close(fd)

    def _lock_self_check(self, fd: int) -> None:
        # Best-effort: warn (once) if advisory locking is not honored on this
        # mount (some NFS<v4 / FUSE / container bind mounts). Not a hard failure.
        if self._lock_self_check_done or _WINDOWS:
            return
        self._lock_self_check_done = True
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            print(
                "cofferdam: warning: advisory file locking may be unsupported on "
                "this filesystem; single-use guarantees are weakened here.",
                file=sys.stderr,
            )

    # -- read ----------------------------------------------------------------

    def read_entries(self) -> List[dict]:
        """Read + strictly parse the whole ledger (caller holds the lock). A
        missing ledger yields ``[]`` (no approvals). Any malformed non-final
        line, oversize, bad encoding, or a torn (non-LF-terminated) final
        record invalidates the whole ledger (fail-closed) — a torn final
        record is never ignored, so a crash-torn consumption cannot resurrect
        a consumed approval."""
        info = _lstat_or_none(self._ledger)
        if info is None:
            return []
        if _is_symlink_or_reparse(info):
            raise LedgerError("ledger is a symlink/reparse point")
        if not stat.S_ISREG(info.st_mode):
            raise LedgerError("ledger is not a regular file")
        fd = os.open(self._ledger, os.O_RDONLY | _NOFOLLOW)
        try:
            self._validate_regular_fd(fd)
            data = self._read_bounded(fd)
        finally:
            os.close(fd)
        return self._parse_ledger_bytes(data)

    def _read_bounded(self, fd: int) -> bytes:
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_LEDGER_BYTES:
                raise LedgerError("ledger exceeds the maximum size")
            chunks.append(chunk)
        return b"".join(chunks)

    def _parse_ledger_bytes(self, data: bytes) -> List[dict]:
        if data == b"":
            return []
        # A non-empty ledger MUST end with a complete, LF-terminated record. A
        # missing trailing newline means the last append was torn (crash / disk
        # failure / partial write). We FAIL CLOSED rather than silently dropping
        # it: a torn final *consumption* line would otherwise disappear on the
        # next read and resurrect a consumed approval (single-use bypass). No
        # automatic truncation or repair happens here.
        if not data.endswith(b"\n"):
            raise LedgerError("ledger ends with a torn (non-terminated) record")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LedgerError("ledger is not valid UTF-8") from exc
        lines = text[:-1].split("\n")  # every line is now a complete record
        if len(lines) > MAX_ENTRIES:
            raise LedgerError("ledger exceeds the maximum entry count")
        entries: List[dict] = []
        for line in lines:
            if len((line + "\n").encode("utf-8")) > MAX_LINE_BYTES:
                raise LedgerError("ledger line exceeds the maximum size")
            entries.append(approval.parse_entry_line(line))
        return entries

    # -- append (package-internal; non-authority) ----------------------------

    def _append_approval(self, entry: dict) -> None:
        """Append a validated ``approval`` entry (caller holds the lock). Called
        only by PR3c1's (future) mint and by internal test helpers — this is NOT
        a supported production API and does not constitute an approval boundary."""
        self._append(approval.validate_approval_entry(entry))

    def _append_consumption(self, entry: dict) -> None:
        """Append a validated ``consumption`` entry (caller holds the lock)."""
        self._append(approval.validate_consumption_entry(entry))

    def _append(self, entry: dict) -> None:
        line_bytes = (approval.canonical_line(entry) + "\n").encode("utf-8")
        if len(line_bytes) > MAX_LINE_BYTES:
            raise LedgerError("entry line exceeds the maximum size")
        created = not os.path.lexists(self._ledger)
        if not created:
            info = os.lstat(self._ledger)
            if _is_symlink_or_reparse(info):
                raise LedgerError("ledger is a symlink/reparse point")
            if not stat.S_ISREG(info.st_mode):
                raise LedgerError("ledger is not a regular file")
        fd = os.open(self._ledger, os.O_WRONLY | os.O_APPEND | os.O_CREAT | _NOFOLLOW, 0o600)
        try:
            if created and not _WINDOWS:
                os.fchmod(fd, 0o600)
            self._validate_regular_fd(fd)
            size = os.fstat(fd).st_size
            if size + len(line_bytes) > MAX_LEDGER_BYTES:
                raise LedgerError("append would exceed the maximum ledger size")
            self._write_all(fd, line_bytes)
            os.fsync(fd)  # durability barrier only after every byte is written
        finally:
            os.close(fd)
        if created:
            self._fsync_dir()

    @staticmethod
    def _write_all(fd: int, data: bytes) -> None:
        """Write *all* of ``data``, looping over partial writes and retrying
        ``InterruptedError``. A zero-byte write fails closed. Any other error
        propagates (no success is ever reported for a partial/failed write)."""
        view = memoryview(data)
        total = 0
        while total < len(data):
            try:
                n = os.write(fd, view[total:])
            except InterruptedError:  # pragma: no cover - timing-dependent
                continue
            if n <= 0:
                raise LedgerError("zero-byte write to the ledger")
            total += n

    # -- state directory / lock-file creation + validation -------------------

    def _ensure_dir(self, create: bool) -> None:
        info = _lstat_or_none(self._dir)
        created = False
        if info is None:
            if not create:
                raise LedgerError("approval state directory does not exist")
            os.mkdir(self._dir, 0o700)
            if not _WINDOWS:
                os.chmod(self._dir, 0o700)  # defeat umask explicitly
            created = True
            info = os.lstat(self._dir)
        if _is_symlink_or_reparse(info):
            raise LedgerError("state directory is a symlink/reparse point")
        if not stat.S_ISDIR(info.st_mode):
            raise LedgerError("state path exists but is not a directory")
        if not _WINDOWS:
            dfd = os.open(self._dir, os.O_RDONLY | _NOFOLLOW | _DIRFLAG)
            try:
                dst = os.fstat(dfd)
                if dst.st_mode & 0o077:
                    raise LedgerError("state directory permissions are too broad")
                if dst.st_uid != os.geteuid():
                    raise LedgerError("state directory owner mismatch")
            finally:
                os.close(dfd)
        if created:
            self._fsync_dir()

    def _ensure_lockfile(self, create: bool) -> None:
        info = _lstat_or_none(self._lock_path)
        if info is None:
            if not create:
                raise LedgerError("approval lock file does not exist")
            fd = os.open(self._lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
            try:
                if not _WINDOWS:
                    os.fchmod(fd, 0o600)
                os.write(fd, _LOCK_BYTE)
                os.fsync(fd)
            finally:
                os.close(fd)
            self._fsync_dir()
            info = os.lstat(self._lock_path)
        if _is_symlink_or_reparse(info):
            raise LedgerError("lock file is a symlink/reparse point")
        if not stat.S_ISREG(info.st_mode):
            raise LedgerError("lock path exists but is not a regular file")

    def _fsync_dir(self) -> None:
        if _WINDOWS:  # no directory-fsync equivalent
            return
        try:
            dfd = os.open(self._dir, os.O_RDONLY | _DIRFLAG)
        except OSError:  # pragma: no cover - unusual platforms
            return
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    def _validate_regular_fd(self, fd: int) -> None:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise LedgerError("expected a regular file")
        if not _WINDOWS:
            if st.st_mode & 0o177:
                raise LedgerError("state file permissions are too broad")
            if st.st_uid != os.geteuid():
                raise LedgerError("state file owner mismatch")
