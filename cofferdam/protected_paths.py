"""Protected-path tables (data, kept separate from the matching logic).

The list is declarative so it can evolve without touching guard logic. It ships
as Python data rather than a TOML file so the trust core stays
standard-library-only on the supported runtime floor (a TOML parser is not in
the stdlib on that floor).

Tier 1 = **unconditional BLOCK** (never writable via Cofferdam in v0.1). Tier 2
= **NEEDS_APPROVAL + HIGH** (writable but dangerous). All comparisons are on
already casefolded, NFC-normalized path components.

Known residual gap (documented, not solved): the list is static, so
ecosystem-new vectors (``.devcontainer/``, ``mise.toml``, ``.husky/``, Renovate
config, ...) are unprotected until added. See ``THREAT-MODEL.md``.
"""

from __future__ import annotations

import fnmatch
from typing import Sequence

# Tier 1 — any of these appearing as a path component blocks unconditionally.
_TIER1_ANY_COMPONENT = frozenset({".git", ".hg", ".svn", ".cofferdam"})

# Tier 1 — directory prefixes (a run of components at the path root).
_TIER1_PREFIXES = (
    (".github", "workflows"),
    (".circleci",),
)

# Tier 1 — exact final-component filenames: arbitrary-code-execution / CI /
# install-time-executing vectors. BLOCKED, never left to human judgment.
_TIER1_FILENAMES = frozenset({
    ".pre-commit-config.yaml",
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
    "setup.py",
    "makefile",
    "gnumakefile",
    "justfile",
    "pyproject.toml",
    "package.json",
    # Cofferdam's own control surface (a proposal must not edit its own judges).
    "cofferdam.toml",
    ".cofferdam.toml",
    "protected_paths.toml",
})

# Tier 2 — final-component glob patterns: secrets / sensitive config.
_TIER2_FILE_GLOBS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "*.pfx",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "secrets*",
    ".gitattributes",
)

# Tier 2 — any of these appearing as a component (credential directories).
_TIER2_ANY_COMPONENT = frozenset({".aws", ".ssh", ".gnupg"})


def is_tier1(fold: Sequence[str]) -> bool:
    """True if the casefolded components hit the unconditional-BLOCK list."""
    if any(component in _TIER1_ANY_COMPONENT for component in fold):
        return True
    for prefix in _TIER1_PREFIXES:
        if tuple(fold[: len(prefix)]) == prefix:
            return True
    if fold and fold[-1] in _TIER1_FILENAMES:
        return True
    return False


def is_tier2(fold: Sequence[str]) -> bool:
    """True if the casefolded components hit the elevated-scrutiny list."""
    if any(component in _TIER2_ANY_COMPONENT for component in fold):
        return True
    if fold:
        last = fold[-1]
        for pattern in _TIER2_FILE_GLOBS:
            if fnmatch.fnmatchcase(last, pattern):
                return True
    return False
