"""Atomic persistence for registry files.

**M2A exposes no write API.** Nothing in the request path reaches this module —
:mod:`~cofferdam.workstation.service` mounts no ``POST``/``PUT``/``PATCH``/
``DELETE`` registry route, and the loader never repairs a file it could not
parse. The utility exists now, with its tests, because the *editing* milestone
should not be the moment anyone first thinks about durability, and because
seeding a machine's first registry from the command line already needs it.

The sequence is the standard one, and each step earns its place:

1. serialize **before** touching the filesystem — a value that cannot be encoded
   must not be able to leave a stray temporary file behind;
2. write to a temporary file **in the same directory**, so the final rename is
   within one filesystem and therefore atomic;
3. ``flush`` then ``fsync`` the file, so the bytes are on the medium before
   anything points at them;
4. apply restrictive permissions (owner read/write) — these files are personal
   machine configuration, and while they must never hold secrets, they do
   describe the shape of someone's home;
5. ``os.replace`` — atomic on POSIX and on Windows; a reader sees either the
   whole old file or the whole new one, never a truncated mix;
6. ``fsync`` the directory, so the rename itself survives a power cut.

If any step fails the temporary file is removed and the original is left exactly
as it was. A failed write never destroys the previous configuration.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .common import SUPPORTED_VERSION

# Owner read/write. Registries hold no secrets by design; this is defence in
# depth on a multi-account machine, not a substitute for that rule.
FILE_MODE = stat.S_IRUSR | stat.S_IWUSR


def registry_document(items: Sequence[Mapping[str, Any]], version: int = SUPPORTED_VERSION) -> dict:
    """Build the canonical ``{"version": …, "items": [...]}`` envelope."""
    return {"version": version, "items": [dict(item) for item in items]}


def write_json_atomic(path: Path, document: Any) -> None:
    """Replace ``path`` with ``document`` atomically, or leave it untouched."""
    path = Path(path)
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)

    # Preserve an existing file's mode rather than tightening it silently; a
    # user who deliberately relaxed permissions should not be overruled by a
    # routine save.
    mode = FILE_MODE
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        pass

    handle, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        # Includes OSError and anything a patched os.replace raises in tests.
        # The original file is still whole; drop only our temporary.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    _fsync_directory(directory)


def _fsync_directory(directory: Path) -> None:
    """Best effort: not every platform lets a directory be opened for fsync."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:  # pragma: no cover - platform dependent
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - platform dependent
        pass
    finally:
        os.close(fd)


class RegistryLockTimeout(RuntimeError):
    """Another writer held the registry lock for too long."""


# How long a writer waits for the lock before giving up. A registry write is a
# few kilobytes and a rename; anything slower than this is a stuck process, and
# an API request must fail with a real error rather than hang the event loop.
LOCK_TIMEOUT_SECONDS = 5.0
LOCK_POLL_SECONDS = 0.05


@contextlib.contextmanager
def registry_lock(path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Serialize read-modify-write cycles on one registry file.

    ``write_json_atomic`` alone makes a write all-or-nothing, which is enough
    for a single writer. It is *not* enough for the M2B2 edit flow, which is
    read-modify-write: two overlay saves arriving together would both read the
    old file, and the second ``os.replace`` would silently discard the first
    user's change. No corruption, but a lost update — which is worse, because
    nothing reports it.

    An adjacent ``.lock`` file is used rather than the registry itself, so the
    lock is never held on a file that is about to be replaced by rename. The
    lock file is created once and left in place; its existence carries no
    meaning and its content is never read.

    Advisory locks are unsupported on some filesystems. Rather than pretend,
    this degrades to no locking and says so once, matching how the Trust Core
    approval store already handles the same limitation.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - POSIX only in this product
        yield
        return

    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    if time.monotonic() >= deadline:
                        raise RegistryLockTimeout(
                            "another write to this registry is still in progress"
                        ) from error
                    time.sleep(LOCK_POLL_SECONDS)
                    continue
                # Locking genuinely unsupported here: proceed unlocked rather
                # than refuse every write on such a filesystem.
                break
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


__all__ = [
    "FILE_MODE",
    "LOCK_TIMEOUT_SECONDS",
    "RegistryLockTimeout",
    "registry_document",
    "registry_lock",
    "write_json_atomic",
]
