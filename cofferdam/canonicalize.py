"""Read-only canonical target resolution.

Resolves an already-normalized target path (from ``paths.normalize_target``) to
its **canonical real absolute path** under the whitelisted repository root, and
produces a stable, platform-neutral byte serialization for hashing. Read-only:
it only stats the filesystem; it never creates, edits, deletes, chmods,
renames, or replaces anything.

Fail-closed policy (PR3a): reject a target whose resolution would traverse a
**symlink / junction / reparse point** in any existing component, reject any
**escape** of the repository root, and reject an existing **non-regular** target
(directory / device / socket / FIFO). Following symlinks to apply is a PR3c
concern; PR3a refuses them outright, consistent with the PR2 guard.

Two representations are kept deliberately separate:

* ``real_path`` — the platform-native ``Path`` for later filesystem operations;
* ``path_bytes`` — the platform-neutral serialization used **only** for hashing:
  the NFC-normalized, POSIX-slash form of the real absolute path, UTF-8 encoded.

Residual (documented, PR4 hardening): case-insensitive filesystems and
macOS-NFD / Windows-subst-drive edges can make two spellings of the same file
serialize differently. v0.1 approvals are short-lived (5-min TTL) and
machine-specific, so this manifests as a fail-closed re-approval, never as a
wrong apply.
"""

from __future__ import annotations

import os
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .paths import NormalizedPath


class CanonicalizationError(Exception):
    """The target cannot be canonicalized safely (symlink/reparse, escape, or
    unsupported existing type). Callers fail closed."""


@dataclass(frozen=True)
class CanonicalTarget:
    real_path: Path   # platform-native absolute real path (for FS ops, PR3c)
    path_bytes: bytes  # platform-neutral serialization for hashing (NFC, POSIX)


def _is_symlink_or_reparse(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    # Windows junctions/reparse points are not S_ISLNK; detect the reparse flag.
    attrs = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attrs and reparse and (attrs & reparse))


def canonicalize_target(repo_root: "os.PathLike[str] | str", normalized: NormalizedPath) -> CanonicalTarget:
    """Resolve ``normalized`` under ``repo_root`` to a ``CanonicalTarget``.

    ``normalized`` must already be a lexically-safe relative path (no ``..``,
    absolute, drive, UNC, etc. — enforced by ``paths.normalize_target``).
    """
    try:
        root_real = Path(os.path.realpath(repo_root))
    except (OSError, ValueError) as exc:
        raise CanonicalizationError("repository root is not resolvable") from exc

    # Walk each component; reject any existing symlink/reparse component so the
    # resolution cannot be redirected outside the root.
    current = root_real
    for part in normalized.parts:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            # A missing component is fine (new-file create); nothing below an
            # absent path can be a symlink at observation time.
            continue
        except (OSError, ValueError) as exc:
            raise CanonicalizationError("cannot stat target component") from exc
        if _is_symlink_or_reparse(info):
            raise CanonicalizationError("target traverses a symlink or reparse point")

    # No symlink components exist, so realpath only normalizes (never redirects).
    try:
        real = Path(os.path.realpath(current))
    except (OSError, ValueError) as exc:
        raise CanonicalizationError("target is not resolvable") from exc

    # Containment: the resolved target must live under the resolved root.
    try:
        real.relative_to(root_real)
    except ValueError:
        raise CanonicalizationError("target escapes the repository root")

    # If the target exists, it must be a regular file (never a dir/device/etc.).
    try:
        final_info = os.lstat(real)
    except FileNotFoundError:
        final_info = None
    except (OSError, ValueError) as exc:
        raise CanonicalizationError("cannot stat resolved target") from exc
    if final_info is not None:
        if _is_symlink_or_reparse(final_info):
            raise CanonicalizationError("resolved target is a symlink or reparse point")
        if not stat.S_ISREG(final_info.st_mode):
            raise CanonicalizationError("resolved target is not a regular file")

    path_bytes = unicodedata.normalize("NFC", real.as_posix()).encode("utf-8")
    return CanonicalTarget(real_path=real, path_bytes=path_bytes)


def canonical_root_bytes(repo_root: "os.PathLike[str] | str") -> bytes:
    """The platform-neutral serialization of the canonical real repository root,
    used to derive ``repo_root_id``. Same encoding as ``CanonicalTarget.path_bytes``."""
    try:
        root_real = Path(os.path.realpath(repo_root))
    except (OSError, ValueError) as exc:
        raise CanonicalizationError("repository root is not resolvable") from exc
    return unicodedata.normalize("NFC", root_real.as_posix()).encode("utf-8")
