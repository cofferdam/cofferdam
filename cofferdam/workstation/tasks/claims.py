"""Adapter-reported change claims, and the artifacts Cofferdam observed for them.

M2K PR1. This module adds the **claim side** of evidence. Cofferdam already had
an honest observation side — `git_observed`, `os_observed`, `cofferdam_action`,
all of them things it went and looked at — and no structured way to record the
other half of the sentence: *what the worker said it did*.

Two objects, not one
--------------------

A :class:`ChangeClaim` is a statement. "I modified ``src/foo.py``." It is
``adapter_reported`` forever, and nothing in this module can make it anything
else — ``source`` is not a field an adapter fills in, it is a constant.

An :class:`ArtifactRecord` is an observation. It exists only when Cofferdam
opened a file inside a verified project root and read its bytes, and the digest
and size on it describe **the bytes Cofferdam read**, not anything an adapter
said about them.

Keeping them apart is not tidiness, it is D-2026-08-11-6: *every field carries
its source kind*. One row holding both a claimed path and a computed SHA-256
would need one ``source`` column over two different provenances, and whichever
value it held would be a lie about the other field. So the claim carries
``adapter_reported``, the artifact carries ``os_observed``, and the link between
them is a foreign key rather than a merge.

What this module deliberately does not do
-----------------------------------------

**It does not compare.** A claim that ``src/foo.py`` was modified and a
``git_observed`` event saying the same path changed are two records that sit
beside each other, and nothing here notices they agree. Matching them is
evidence assembly, which is M2K PR2, and doing it here would mean a claim could
become verified as a side effect of being recorded — the exact promotion
D-2026-08-11-6 forbids.

**It produces no verdict.** No pass, no fail, no confidence, no risk level.
There is no column for one and no function that returns one.

**It executes nothing.** There is no command field on a claim, no argv, no
shell, no subprocess import in this file. D-2026-08-11-7 says executable text
comes from code-owned checks or host-owned definitions referenced by stable id,
and never from a request; the way to keep that true is to give the
adapter-reported path nowhere to put a command.

**It is not a file store.** A bounded preview of an allowlisted text type is
kept so a person can later see *what* was claimed without a filesystem read
endpoint existing. The file itself stays where it is, owned by the project.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from ..mind.documents import descriptor_resolution_supported, open_target
from ..mind.errors import ResolutionUnsupported, RoleUnavailable
from .errors import ProjectRootInvalid
from .identity import _ALPHABET, _encode  # noqa: F401  (shared id alphabet)
from .models import EVIDENCE_ADAPTER_REPORTED, EVIDENCE_OS_OBSERVED, bounded_line

# -- identifiers --------------------------------------------------------------
#
# Same construction as `task_id`, for the same three reasons: unpredictable, not
# derived from anything, sortable. See `identity.py`, which owns the alphabet.
#
# **Never derived from content.** An id built from the file's digest would put a
# fingerprint of the user's source into every log line and every future URL, and
# would additionally make two identical files share an id — which is wrong, since
# an artifact record is about one claim on one task at one moment.

CLAIM_ID_PREFIX = "chg_"
ARTIFACT_ID_PREFIX = "art_"

_TIME_BITS = 48
_RANDOM_BITS = 80
_ID_BODY_CHARS = (_TIME_BITS + _RANDOM_BITS) // 5 + 1  # 26

CLAIM_ID_CHARS = len(CLAIM_ID_PREFIX) + _ID_BODY_CHARS
ARTIFACT_ID_CHARS = len(ARTIFACT_ID_PREFIX) + _ID_BODY_CHARS

_ALPHABET_SET = frozenset(_ALPHABET)


def _new_id(prefix: str, now_ms: Optional[int] = None) -> str:
    stamp = int(time.time() * 1000) if now_ms is None else int(now_ms)
    stamp &= (1 << _TIME_BITS) - 1
    randomness = secrets.randbits(_RANDOM_BITS)
    return prefix + _encode((stamp << _RANDOM_BITS) | randomness, _ID_BODY_CHARS)


def new_claim_id(now_ms: Optional[int] = None) -> str:
    """A fresh claim id. Minted here; an adapter never supplies one."""
    return _new_id(CLAIM_ID_PREFIX, now_ms)


def new_artifact_id(now_ms: Optional[int] = None) -> str:
    """A fresh artifact id. **Server-minted, always.**

    An adapter may say what it thinks it produced; it may not name the handle
    Cofferdam will file it under. An adapter-chosen id would be a way to address
    another task's record, and an id derived from the path would be a filesystem
    location wearing an identifier's clothes.
    """
    return _new_id(ARTIFACT_ID_PREFIX, now_ms)


def _valid_id(value: object, prefix: str, length: int) -> bool:
    if not isinstance(value, str) or len(value) != length:
        return False
    if not value.startswith(prefix):
        return False
    return all(character in _ALPHABET_SET for character in value[len(prefix) :])


def valid_claim_id(value: object) -> bool:
    return _valid_id(value, CLAIM_ID_PREFIX, CLAIM_ID_CHARS)


def valid_artifact_id(value: object) -> bool:
    return _valid_id(value, ARTIFACT_ID_PREFIX, ARTIFACT_ID_CHARS)


# -- the closed operation vocabulary ------------------------------------------
#
# Closed on purpose. An open string would mean the evaluator in PR2 has to
# interpret whatever word a provider chose this week, and "changed" versus
# "edited" versus "touched" would become three cases that mean one thing.

CLAIM_CREATED = "created"
CLAIM_MODIFIED = "modified"
CLAIM_DELETED = "deleted"
CLAIM_RENAMED = "renamed"

CLAIM_OPERATIONS: Tuple[str, ...] = (
    CLAIM_CREATED,
    CLAIM_MODIFIED,
    CLAIM_DELETED,
    CLAIM_RENAMED,
)

# -- bounds -------------------------------------------------------------------
#
# Code-owned, and applied to what an adapter hands over rather than to what it
# promises. Every one of these is a refusal or an omission, never a truncation
# that would leave a stored path meaning a different file than the claimed one.

#: The most claims one adapter outcome may contribute. A worker that touched
#: more files than this made a claim Cofferdam records the first N of, and says
#: so with `claim_limit_exceeded` — silently keeping some and dropping the rest
#: would make the record look complete when it is not.
MAX_CLAIMS_PER_OUTCOME = 32

#: The most claims one task may accumulate across all its turns.
MAX_CLAIMS_PER_TASK = 256

#: A project-relative path. Long enough for real source trees, short enough that
#: a claim cannot become a payload.
MAX_CLAIM_PATH_CHARS = 512

#: One path segment. Guards against a single absurd component inside a legal
#: overall length.
MAX_CLAIM_SEGMENT_CHARS = 255

#: The adapter's own free label for what it did, kept as bounded untrusted
#: metadata. It is never parsed and never becomes the operation.
MAX_CLAIM_LABEL_CHARS = 120

#: The largest file Cofferdam will read to compute a digest. Above this the
#: artifact is recorded with `artifact_too_large` and **no digest**, because a
#: digest over a prefix is not a digest of the file and storing one would invite
#: exactly that misreading.
MAX_ARTIFACT_READ_BYTES = 1024 * 1024

#: The bounded text preview. This is the only file content that enters the
#: database, and it is a prefix of an allowlisted text type or nothing at all.
MAX_PREVIEW_BYTES = 4096

# -- reason codes -------------------------------------------------------------
#
# Closed and machine-readable, in the repository's `snake_case` style. A reason
# is what the record carries *instead of* an artifact; it is never an exception
# that reaches a client, and it never contains a path.

REASON_OK = "ok"
REASON_PATH_INVALID = "path_invalid"
REASON_PATH_ESCAPE = "path_escape"
REASON_PATH_DENIED_SENSITIVE = "path_denied_sensitive"
REASON_PROJECT_UNAVAILABLE = "project_unavailable"
REASON_CONTAINMENT_UNPROVEN = "containment_unproven"
REASON_ARTIFACT_MISSING = "artifact_missing"
REASON_ARTIFACT_NOT_REGULAR = "artifact_not_regular_file"
REASON_ARTIFACT_TOO_LARGE = "artifact_too_large"
REASON_ARTIFACT_UNREADABLE = "artifact_unreadable"
REASON_PREVIEW_UNSUPPORTED_TYPE = "preview_unsupported_type"
REASON_PREVIEW_OMITTED = "preview_omitted"
REASON_CLAIM_INVALID = "claim_invalid"
#: More claims arrived in one outcome than :data:`MAX_CLAIMS_PER_OUTCOME` allows.
REASON_CLAIM_LIMIT_EXCEEDED = "claim_limit_exceeded"
#: The task had already accumulated :data:`MAX_CLAIMS_PER_TASK` across its turns.
#: Its own code rather than a reuse of the one above, because "this report was
#: too long" and "this task has been reporting for a long time" are different
#: facts and an evaluator reading the summary should not have to guess which.
REASON_TASK_CLAIM_LIMIT_EXCEEDED = "task_claim_limit_exceeded"

#: Every reason a submission may fail to become a stored claim. Closed, and
#: disjoint from the artifact reasons above: these describe a claim that was
#: **not recorded**, those describe a claim that was recorded and whose bytes
#: were not read.
REJECTION_REASONS: Tuple[str, ...] = (
    REASON_CLAIM_INVALID,
    REASON_PATH_INVALID,
    REASON_PATH_ESCAPE,
    REASON_CLAIM_LIMIT_EXCEEDED,
    REASON_TASK_CLAIM_LIMIT_EXCEEDED,
)

ARTIFACT_REASONS: Tuple[str, ...] = (
    REASON_OK,
    REASON_PATH_INVALID,
    REASON_PATH_ESCAPE,
    REASON_PATH_DENIED_SENSITIVE,
    REASON_PROJECT_UNAVAILABLE,
    REASON_CONTAINMENT_UNPROVEN,
    REASON_ARTIFACT_MISSING,
    REASON_ARTIFACT_NOT_REGULAR,
    REASON_ARTIFACT_TOO_LARGE,
    REASON_ARTIFACT_UNREADABLE,
    REASON_PREVIEW_UNSUPPORTED_TYPE,
    REASON_PREVIEW_OMITTED,
)

# -- the code-owned secret-path deny list -------------------------------------
#
# D-2026-08-09-3 requires this **at record time**, not on read, and the
# distinction is the whole point: content that never entered the store cannot
# later be served by a surface nobody has written yet.
#
# Narrow on purpose. This is not a secret *scanner* — the projection sanitizer
# already does content shapes for a different boundary — it is a list of places
# whose contents are credentials by construction. A claim about one of these is
# still recorded; only its bytes are refused.
#
# **Nothing can override it.** It is not configuration, so an operator cannot
# widen it by editing a file; it is not a parameter, so a caller cannot pass one;
# and the adapter never sees it.

#: Exact basenames, matched case-insensitively. Conventions, not guesses: every
#: entry is a filename a well-known tool writes credentials into.
_DENIED_NAMES = frozenset(
    {
        ".env",
        ".envrc",
        ".netrc",
        ".pgpass",
        ".htpasswd",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "credentials",
        "credentials.json",
        "secrets.json",
        "service-account.json",
        "token",
        ".git-credentials",
        ".npmrc",
        ".pypirc",
        # HashiCorp Vault writes the login token here, verbatim.
        ".vault-token",
        # Rails conventions. The `.example`/`.sample` variants people commit are
        # *not* these names and stay allowed — see the tests.
        "database.yml",
        "secrets.yml",
        "secrets.yaml",
        # A file somebody named this is a file somebody put a secret in.
        "secret.txt",
        "secrets.txt",
    }
)

#: Extensions that are credential material whatever the file is called.
#:
#: `.tfstate` is here because Terraform state routinely contains plaintext
#: provider and database secrets, and `.p8` because it is a PKCS#8 private-key
#: container — which is what Apple's `AuthKey_*.p8` is, matched by its extension
#: rather than by a filename pattern that varies per account.
#:
#: `.env` as an *extension* (`local.env`, `prod.env`) is the same convention as
#: `.env` as a name.
_DENIED_SUFFIXES = frozenset(
    {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".jks",
        ".keystore",
        ".p8",
        ".tfstate",
        ".env",
    }
)

#: Directory names whose whole subtree is refused, matched case-insensitively
#: against **every** component.
#:
#: `.docker` holds `config.json` with registry auth; `.kube` holds a kubeconfig
#: carrying cluster certificates and tokens, and a cache that carries tokens
#: too. Both are dot-directories that tools own — a project's own Docker or
#: Kubernetes files live in `docker/` and `kube/` without the dot, and those
#: stay allowed.
_DENIED_DIRECTORIES = frozenset(
    {".ssh", ".gnupg", ".aws", ".cofferdam", ".docker", ".kube", "secrets"}
)

#: `.env` followed by a separator: `.env.local`, `.env-local`, `.env_production`.
#:
#: The separator is the whole point. Matching a bare `.env` prefix would deny
#: `.environment` and `.envoy`, which are words rather than environment files.
_ENV_PREFIX = ".env"
_ENV_SEPARATORS = (".", "-", "_")

#: Stripped once before the name and extension checks run again, so one rule
#: covers `.netrc.bak`, `.pgpass.old`, `private.pem.bak` and
#: `terraform.tfstate.backup` rather than four.
#:
#: Single strip, deliberately: it is predictable, and `notes.md.bak` still
#: reduces to `notes.md`, which is not denied.
_BACKUP_SUFFIXES = (".bak", ".backup", ".old", ".orig", ".save")


def _denied_basename(lowered: str) -> bool:
    """The name rules, applied to one already-lowercased basename."""
    if lowered in _DENIED_NAMES or lowered in _DENIED_DIRECTORIES:
        return True
    if lowered.startswith(_ENV_PREFIX):
        rest = lowered[len(_ENV_PREFIX) :]
        if rest[:1] in _ENV_SEPARATORS:
            return True
    if "." in lowered:
        if "." + lowered.rsplit(".", 1)[-1] in _DENIED_SUFFIXES:
            return True
    return False


def is_denied_path(relative: str) -> bool:
    """Whether this project-relative path is code-denied for content capture.

    Operates on the **claimed relative path**, before anything is opened, so a
    denied path never reaches a read at all — which is what D-2026-08-09-3 means
    by record time: content that never entered the store cannot be served later
    by a surface nobody has written yet.

    Conventions, not detection. This is not a scanner and does not look at
    content; it recognises the places well-known tools keep credentials. A file
    is denied when any directory component is a credential directory, or when
    its basename — or its basename with one backup extension removed — is a
    known credential name, a `.env` variant, or a credential extension.

    It takes no configuration and no caller argument. An adapter cannot widen or
    narrow it, and neither can a request.
    """
    segments = [segment for segment in str(relative).split("/") if segment]
    if not segments:
        return False
    for segment in segments[:-1]:
        if segment.lower() in _DENIED_DIRECTORIES:
            return True

    lowered = segments[-1].lower()
    if _denied_basename(lowered):
        return True

    # `private.pem.bak` is a private key with four characters after it. Strip one
    # backup extension and ask again; `notes.md.bak` becomes `notes.md` and is
    # still allowed, which is the boundary this rule is written to keep.
    for backup in _BACKUP_SUFFIXES:
        if lowered.endswith(backup):
            return _denied_basename(lowered[: -len(backup)])
    if lowered.endswith("~"):
        return _denied_basename(lowered[:-1])
    return False


# -- path validation ----------------------------------------------------------


class ClaimPathInvalid(Exception):
    """A claimed path that is not a plain project-relative name.

    Carries a closed reason code and never the offending path: a refusal that
    echoes the input is a way to describe the host's filesystem one attempt at a
    time, which is the rule `open_target` already follows.
    """

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def normalize_claim_path(value: object) -> str:
    """A claimed path, or a refusal. **Lexical authority only.**

    This is deliberately not "make it safe" — there is no rewriting here. A path
    that is not already a plain relative name is refused, because normalizing
    ``a/../b`` into ``b`` would mean recording a claim about a file the adapter
    did not name.

    Containment is *not* established here. This is the cheap lexical gate that
    runs before the project is even resolved; the real boundary is the
    descriptor-relative walk in :func:`observe_artifact`, which the kernel
    enforces with ``O_NOFOLLOW``.
    """
    if not isinstance(value, str) or not value:
        raise ClaimPathInvalid(REASON_PATH_INVALID)
    if len(value) > MAX_CLAIM_PATH_CHARS:
        raise ClaimPathInvalid(REASON_PATH_INVALID)

    # Control characters and NUL. Checked before anything splits the string, so
    # an embedded NUL cannot end a path early for one consumer and not another.
    for character in value:
        if character == "\x00" or unicodedata.category(character) in ("Cc", "Cf"):
            raise ClaimPathInvalid(REASON_PATH_INVALID)

    if "\\" in value:
        # A backslash is a legal filename character on Linux and a separator on
        # Windows. Refusing it keeps one claim from meaning two different paths
        # depending on who reads it.
        raise ClaimPathInvalid(REASON_PATH_INVALID)
    if value.startswith("/") or value.startswith("~"):
        raise ClaimPathInvalid(REASON_PATH_ESCAPE)
    # A Windows drive or UNC prefix reaching a Linux host is not a path, it is an
    # attempt at a different authority model.
    if len(value) >= 2 and value[1] == ":":
        raise ClaimPathInvalid(REASON_PATH_ESCAPE)

    segments = value.split("/")
    for segment in segments:
        if not segment or segment == ".":
            raise ClaimPathInvalid(REASON_PATH_INVALID)
        if segment == "..":
            raise ClaimPathInvalid(REASON_PATH_ESCAPE)
        if len(segment) > MAX_CLAIM_SEGMENT_CHARS:
            raise ClaimPathInvalid(REASON_PATH_INVALID)
    return value


# -- the digest ---------------------------------------------------------------
#
# The same discipline `mind/hashing.py` records and, as that module explains, a
# tag of our own rather than a shared frozen constant:
#
#     SHA256( tag || 8-byte-big-endian-length || bytes )
#
# Versioned, so a later change to what is hashed produces a different value
# visibly rather than making stored rows compare unequal for no visible reason.

TAG_ARTIFACT = b"cofferdam.evidence.artifact.v1"
LENGTH_PREFIX_WIDTH = 8
ARTIFACT_DIGEST_CHARS = 64


def artifact_digest(data: bytes) -> str:
    """The digest of exactly the bytes Cofferdam read.

    Not of a normalized form, not of a decoded string, and never of a prefix —
    a caller that could not read the whole file records no digest at all.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("artifact_digest requires bytes")
    hasher = hashlib.sha256()
    hasher.update(TAG_ARTIFACT)
    hasher.update(len(data).to_bytes(LENGTH_PREFIX_WIDTH, "big"))
    hasher.update(bytes(data))
    return hasher.hexdigest()


# -- the preview type allowlist -----------------------------------------------
#
# Small, code-owned, and by extension *plus* a decode check — the extension
# selects a candidate and the bytes get the final say. Nothing here sniffs
# magic numbers or imports a MIME library; an allowlist this short does not need
# one, and a dependency that guesses would be a new way to be wrong.

PREVIEW_TEXT_SUFFIXES: Tuple[str, ...] = (
    ".md",
    ".txt",
    ".py",
    ".js",
    ".ts",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".css",
    ".html",
    ".sh",
    ".sql",
    ".rst",
)


def _preview_candidate(relative: str) -> bool:
    lowered = relative.lower()
    return any(lowered.endswith(suffix) for suffix in PREVIEW_TEXT_SUFFIXES)


def _decode_preview(data: bytes) -> Optional[str]:
    """A bounded preview, or ``None`` if these bytes are not plainly text.

    **Strict.** ``errors="replace"`` would turn a binary file into a page of
    replacement characters and call it a preview, which is the "interpret
    arbitrary binary as UTF-8 and call it safe" mistake. If it does not decode,
    there is no preview.

    The cut is made on **bytes** and then re-decoded, so a multi-byte character
    straddling the boundary drops rather than becoming a partial sequence.
    """
    window = bytes(data[:MAX_PREVIEW_BYTES])
    for trim in range(0, 4):
        candidate = window[: len(window) - trim] if trim else window
        try:
            text = candidate.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if "\x00" in text:
            return None
        return text
    return None


# -- the records --------------------------------------------------------------


@dataclass(frozen=True)
class ChangeClaim:
    """One bounded statement by a worker that it changed something.

    ``source`` is a constant, not a parameter. There is no code path by which an
    adapter can construct one of these carrying ``git_observed``.
    """

    claim_id: str
    task_id: str
    turn_number: Optional[int]
    operation: str
    path: str
    to_path: Optional[str] = None
    adapter_label: Optional[str] = None
    reported_at: Optional[str] = None
    artifact_id: Optional[str] = None
    reason: str = REASON_OK

    #: Always. A claim is a claim forever — D-2026-08-11-6.
    source: str = EVIDENCE_ADAPTER_REPORTED

    @property
    def verified(self) -> bool:
        """Never true. Stated as a property so a reader does not have to infer it."""
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "task_id": self.task_id,
            "turn_number": self.turn_number,
            "operation": self.operation,
            "path": self.path,
            "to_path": self.to_path,
            "adapter_label": self.adapter_label,
            "reported_at": self.reported_at,
            "artifact_id": self.artifact_id,
            "reason": self.reason,
            "source": EVIDENCE_ADAPTER_REPORTED,
            # Said out loud rather than left to be derived, the same way
            # `EvidenceReference.to_dict` does it.
            "verified": False,
        }


@dataclass(frozen=True)
class ArtifactRecord:
    """What Cofferdam saw when it opened the claimed file itself.

    Every field here is ``os_observed``: Cofferdam opened a descriptor inside a
    verified root and read bytes. That is why this is a separate row from the
    claim that caused it — the claim is what somebody said, this is what the
    filesystem answered.

    ``digest`` and ``size_bytes`` are ``None`` together whenever the bytes were
    not read, and the reason says why. There is no partial digest.
    """

    artifact_id: str
    task_id: str
    claim_id: str
    path: str
    digest: Optional[str]
    size_bytes: Optional[int]
    preview: Optional[str]
    preview_truncated: bool
    reason: str
    observed_at: Optional[str] = None

    #: Always. These fields describe bytes this process read.
    source: str = EVIDENCE_OS_OBSERVED

    @property
    def verified(self) -> bool:
        """True only because ``os_observed`` is in the verified set — and only
        ever about *these* fields. It says nothing about the claim."""
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "task_id": self.task_id,
            "claim_id": self.claim_id,
            "path": self.path,
            "digest": self.digest,
            "size_bytes": self.size_bytes,
            "preview": self.preview,
            "preview_truncated": self.preview_truncated,
            "reason": self.reason,
            "observed_at": self.observed_at,
            "source": EVIDENCE_OS_OBSERVED,
        }


@dataclass(frozen=True)
class ClaimIngestion:
    """How much of one adapter's report actually became stored claims.

    **The point of this record is absence.** A stored claim set is only useful
    to a later evaluator if the evaluator can tell whether it is the *whole*
    set. Without this, thirty-two stored claims out of forty submitted look
    exactly like thirty-two out of thirty-two, and an evidence bundle built on
    the second reading would describe work that was never reported.

    So the counts are durable and the rejected submissions are not. There is no
    column here for a path, an operation or any other fragment of what was
    refused: a rejected path may be an absolute location, a traversal attempt or
    a credential file name, and keeping it *for reporting* would put exactly the
    material the deny list exists to exclude into the database by a second door.
    What survives is a count against a **closed, code-owned reason code**.

    ``accepted`` counts claims that became rows. A valid claim whose bytes could
    not be read is accepted — the claim was fine, the observation failed — and
    shows up in ``reason_counts`` under its artifact reason rather than as a
    rejection. That distinction is the milestone's, not a detail: "the worker
    claimed a file that is gone" and "the worker sent something that was not a
    claim" are different facts.

    **This is not a verdict.** It says nothing about whether the work was done,
    whether the claims were true, or whether anything matched an observation. It
    reports one thing: how complete the stored claim set is.
    """

    task_id: str
    turn_number: Optional[int]
    submitted: int
    accepted: int
    rejected: int
    truncated: bool
    reason_counts: Dict[str, int]
    recorded_at: Optional[str] = None

    @property
    def complete(self) -> bool:
        """Whether every submitted claim became a stored claim."""
        return self.rejected == 0 and not self.truncated

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "turn_number": self.turn_number,
            "submitted": self.submitted,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "truncated": self.truncated,
            "complete": self.complete,
            "reason_counts": dict(self.reason_counts),
            "recorded_at": self.recorded_at,
        }


@dataclass(frozen=True)
class ClaimSubmission:
    """What an adapter may hand over. Deliberately smaller than a `ChangeClaim`.

    There is no ``claim_id``, no ``artifact_id``, no ``source``, no ``digest``,
    no ``verified`` and no command field. An adapter that wanted to supply any
    of them has nowhere to put it, which is a stronger guarantee than validating
    it away afterwards.
    """

    operation: str
    path: str
    to_path: Optional[str] = None
    label: Optional[str] = None


def validate_submission(submission: object) -> Tuple[str, str, Optional[str], Optional[str]]:
    """Check one adapter submission, or raise :class:`ClaimPathInvalid`.

    Returns the normalized ``(operation, path, to_path, label)``.
    """
    if not isinstance(submission, ClaimSubmission):
        raise ClaimPathInvalid(REASON_CLAIM_INVALID)
    operation = submission.operation
    if not isinstance(operation, str) or operation not in CLAIM_OPERATIONS:
        raise ClaimPathInvalid(REASON_CLAIM_INVALID)
    path = normalize_claim_path(submission.path)

    to_path: Optional[str] = None
    if operation == CLAIM_RENAMED:
        # A rename is two paths and both are claims. Validated identically, and
        # carried in their own columns rather than smuggled through one text
        # field as "a -> b", which would be a parser waiting to be written.
        if submission.to_path is None:
            raise ClaimPathInvalid(REASON_CLAIM_INVALID)
        to_path = normalize_claim_path(submission.to_path)
    elif submission.to_path is not None:
        # A destination on a non-rename is a confused claim, not a harmless one.
        raise ClaimPathInvalid(REASON_CLAIM_INVALID)

    label = bounded_line(submission.label, MAX_CLAIM_LABEL_CHARS)
    return operation, path, to_path, label


# -- record-time observation --------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """The result of trying to look at one claimed path. Never raises upward."""

    digest: Optional[str]
    size_bytes: Optional[int]
    preview: Optional[str]
    preview_truncated: bool
    reason: str

    @property
    def captured(self) -> bool:
        return self.digest is not None


def _unreadable(reason: str) -> Observation:
    return Observation(
        digest=None,
        size_bytes=None,
        preview=None,
        preview_truncated=False,
        reason=reason,
    )


def observe_artifact(root: object, relative: str) -> Observation:
    """Open the claimed path inside the verified root and describe what is there.

    The containment guarantee is the kernel's, not this function's: resolution
    goes through :func:`~...mind.documents.open_target`, which opens the root
    and then every component below it relative to the descriptor above it with
    ``O_NOFOLLOW``. A symlink at any level — including a component swapped in
    after a check — is an error from ``open``, not a comparison made afterwards.

    **This never raises for an ordinary absence.** A claim about a deleted file
    is a legitimate claim, and a missing file is a reason code rather than a
    failure: whether the file *should* be there is evaluation, and evaluation is
    PR2's.
    """
    if is_denied_path(relative):
        # Checked again here, not only at the call site. This function is the one
        # that opens things, so the deny gate belongs where the read is.
        return _unreadable(REASON_PATH_DENIED_SENSITIVE)

    if not descriptor_resolution_supported():
        # Fail closed rather than reverting to a pathname walk, the rule
        # `mind/documents.py` states and the reason it states it.
        return _unreadable(REASON_CONTAINMENT_UNPROVEN)

    try:
        with open_target(Path(str(root)), relative) as target:
            # ``O_NONBLOCK`` matters and is easy to miss. Without it, opening a
            # FIFO with ``O_RDONLY`` **blocks in open() until a writer appears**
            # — so the ``S_ISREG`` check below would never be reached, and a
            # claim naming a named pipe would hang the recording path forever.
            # The flag makes the open return immediately for every file type,
            # after which the ``fstat`` decides. On a regular file it has no
            # effect at all.
            descriptor = os.open(
                target.name,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=target.parent_fd,
            )
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    # A directory, FIFO, socket or device. Refused before any
                    # read, because reading a FIFO blocks forever and reading a
                    # device is not something a claim should be able to ask for.
                    return _unreadable(REASON_ARTIFACT_NOT_REGULAR)
                if info.st_size > MAX_ARTIFACT_READ_BYTES:
                    return _unreadable(REASON_ARTIFACT_TOO_LARGE)
                with os.fdopen(descriptor, "rb") as stream:
                    descriptor = -1
                    data = stream.read(MAX_ARTIFACT_READ_BYTES + 1)
            finally:
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:  # pragma: no cover
                        pass
    except ResolutionUnsupported:
        return _unreadable(REASON_CONTAINMENT_UNPROVEN)
    except ProjectRootInvalid:
        return _unreadable(REASON_PROJECT_UNAVAILABLE)
    except RoleUnavailable:
        # One code for missing, a link, a link component, a directory or a path
        # that left the root — `open_target` deliberately does not distinguish
        # them, and inventing a distinction here would describe the filesystem.
        return _unreadable(REASON_ARTIFACT_MISSING)
    except OSError:
        return _unreadable(REASON_ARTIFACT_UNREADABLE)

    if len(data) > MAX_ARTIFACT_READ_BYTES:
        # The file grew between `fstat` and `read`. The digest would be of a
        # prefix, so there is no digest.
        return _unreadable(REASON_ARTIFACT_TOO_LARGE)

    digest = artifact_digest(data)
    size = len(data)

    if not _preview_candidate(relative):
        return Observation(digest, size, None, False, REASON_PREVIEW_UNSUPPORTED_TYPE)
    preview = _decode_preview(data)
    if preview is None:
        # An allowlisted extension holding bytes that are not text. The digest
        # and size still stand — they are facts about the bytes — and the
        # preview does not.
        return Observation(digest, size, None, False, REASON_PREVIEW_UNSUPPORTED_TYPE)
    truncated = size > len(preview.encode("utf-8"))
    return Observation(digest, size, preview, truncated, REASON_OK)


__all__ = [
    "ARTIFACT_DIGEST_CHARS",
    "ARTIFACT_ID_CHARS",
    "ARTIFACT_ID_PREFIX",
    "ARTIFACT_REASONS",
    "ArtifactRecord",
    "CLAIM_CREATED",
    "CLAIM_DELETED",
    "CLAIM_ID_CHARS",
    "CLAIM_ID_PREFIX",
    "CLAIM_MODIFIED",
    "CLAIM_OPERATIONS",
    "CLAIM_RENAMED",
    "ChangeClaim",
    "ClaimIngestion",
    "ClaimPathInvalid",
    "ClaimSubmission",
    "MAX_ARTIFACT_READ_BYTES",
    "MAX_CLAIMS_PER_OUTCOME",
    "MAX_CLAIMS_PER_TASK",
    "MAX_CLAIM_LABEL_CHARS",
    "MAX_CLAIM_PATH_CHARS",
    "MAX_CLAIM_SEGMENT_CHARS",
    "MAX_PREVIEW_BYTES",
    "Observation",
    "PREVIEW_TEXT_SUFFIXES",
    "REASON_ARTIFACT_MISSING",
    "REASON_ARTIFACT_NOT_REGULAR",
    "REASON_ARTIFACT_TOO_LARGE",
    "REASON_ARTIFACT_UNREADABLE",
    "REASON_CLAIM_INVALID",
    "REASON_CLAIM_LIMIT_EXCEEDED",
    "REASON_CONTAINMENT_UNPROVEN",
    "REASON_OK",
    "REASON_PATH_DENIED_SENSITIVE",
    "REASON_PATH_ESCAPE",
    "REASON_PATH_INVALID",
    "REASON_PREVIEW_OMITTED",
    "REASON_PREVIEW_UNSUPPORTED_TYPE",
    "REASON_PROJECT_UNAVAILABLE",
    "REASON_TASK_CLAIM_LIMIT_EXCEEDED",
    "REJECTION_REASONS",
    "TAG_ARTIFACT",
    "artifact_digest",
    "is_denied_path",
    "new_artifact_id",
    "new_claim_id",
    "normalize_claim_path",
    "observe_artifact",
    "valid_artifact_id",
    "valid_claim_id",
    "validate_submission",
]
