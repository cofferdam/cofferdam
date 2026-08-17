"""What was actually there when the worker stopped. Observed once, then frozen.

M2K PR13 established the gap this module begins to close. Every acceptance
predicate Cofferdam can express is a **turn-change** observation — *the worker
did X during this turn* — and none of them asks *does the project now satisfy X*.
So a requirement inherited across turns has no answerable current status: its old
result is stale, and re-asking a change question at a later turn is a category
error that reports a correct, untouched file as a failure.

The missing primitive is an immutable observation of **effective post-worker
repository state**. This module is that primitive, and only that.

What "final state" means here
-----------------------------

    the state of a bounded set of paths, under the authoritative project root,
    on the **working tree filesystem**, at Cofferdam's post-worker observation
    boundary, before the turn is durably closed.

It is emphatically **not** the committed HEAD tree. A worker that deletes
``foo.py`` without committing has left a project in which ``foo.py`` is gone,
and a HEAD-only probe would call it present. A worker that creates ``bar.py``
without committing has left a project in which ``bar.py`` exists, and a HEAD-only
probe would call it absent. Both readings would be wrong about the thing anybody
actually cares about, so the working tree is the authority.

It is also not the **index**. ``git rm --cached foo.py`` leaves the index without
``foo.py`` and the filesystem with it, and the question "does this path exist in
the effective project workspace" is answered by the filesystem. The index is a
staging intention, not a state of the project, and this build does not record it.

The HEAD revision *is* recorded, as an **audit anchor**: it says which committed
revision this observation sat alongside. It is never the authority for existence,
and a worktree result that disagrees with HEAD is not a contradiction — the two
describe different things. Tests pin exactly that.

What it does not do
-------------------

**No content.** No bytes, no hashes, no previews, no sizes, no permissions beyond
what the safe lookup itself requires, no directory listings, no metadata beyond
existence and a bounded kind. A path-state observation that carried content would
be a second artifact surface arriving without a review.

**No new predicate, and no reinterpretation of the old ones.** Observing that
``foo.py`` is present does **not** turn ``path_operation(foo.py, created)`` into a
satisfied criterion. That criterion asks what the worker did; this observation
says what is there. Nothing in this build joins them, ``EVALUATOR_VERSION`` does
not move, and the criterion vocabulary is untouched.

**No aggregate, no binding layer, no `AGGREGATOR_VERSION`.** This is the evidence
substrate the later layers will need, delivered on its own so it can be reviewed
on its own.

Observed at the boundary, never on read
---------------------------------------

The observation happens once, after the worker returns and before the turn is
durably closed, and the result is stored. A later read returns the stored row and
touches nothing. That is not an optimisation: if reading meant *go and look now*,
then repository drift would silently change historical answers, an audit could not
be reproduced, and a remote read would become a live probe of the user's
filesystem.

Containment is the kernel's, not this module's
----------------------------------------------

Resolution opens the verified root and then every component below it **relative to
the descriptor above it** with ``O_NOFOLLOW`` — the same discipline
:func:`~.claims.observe_artifact` uses and for the same reason. An intermediate
symlink is refused by the kernel rather than by a comparison made afterwards, so
``repo/external -> /home/user/secrets`` cannot be walked through to observe
``external/private.txt``. A final-component symlink **is** observable, as itself,
without following it — a broken symlink is a present symlink, not an absent path.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..mind.documents import descriptor_resolution_supported
from .claims import is_denied_path, normalize_claim_path
from .errors import ProjectRootInvalid
from .projects import verify_root

#: Bumped when the **meaning** of an effective path-state observation changes: a
#: different authority (index rather than worktree), a different kind
#: vocabulary, a different symlink rule, a different stability guarantee.
#:
#: Distinct from :data:`~.store.SCHEMA_VERSION` (table shape),
#: :data:`~.evidence.ASSEMBLER_VERSION` (how a bundle is built),
#: :data:`~.evaluation.EVALUATOR_VERSION` (how a criterion is decided),
#: :data:`~.criteria.CRITERIA_MODEL_VERSION`,
#: :data:`~.continuity.CONTINUITY_MODEL_VERSION`,
#: :data:`~.lineage.RESOLVER_VERSION`, and the future binding and aggregate
#: versions. Seven things that move for seven reasons; a reader must be able to
#: tell which one did.
FINAL_STATE_OBSERVER_VERSION = 1

#: How many distinct paths one observation may look at.
#:
#: Deliberately **not** derived from PR6's 32-criteria-per-snapshot bound: an
#: active set accumulates across turns, so a long `extend` chain can carry far
#: more than one snapshot's worth, and each criterion may contribute two paths.
#: This is a bound on *filesystem work at a turn boundary*, chosen for that, and
#: generous against any plausible requirement set.
#:
#: Over the bound is refused as :data:`REASON_TARGET_LIMIT_EXCEEDED`, never
#: truncated. A partial path set presented as an observation would be the silent
#: reduction every bounded surface in this milestone refuses.
MAX_FINAL_STATE_TARGETS = 256

#: How many times the two-pass stability check may be attempted before the
#: observation is refused as unstable. Bounded, because a turn boundary must
#: finish.
MAX_STABILITY_ATTEMPTS = 3


# -- per-path vocabulary ------------------------------------------------------

#: A filesystem object exists at this path. :data:`PATH_KINDS` says what sort.
PATH_PRESENT = "present"

#: The safe anchored lookup completed and determined there is no object here.
#: A **positive machine observation**, not a failure to look — which is exactly
#: why an IO error, a permission refusal or a symlink refusal must never be
#: recorded as this.
PATH_ABSENT = "absent"

#: The lookup could not be completed safely. A closed reason says why.
PATH_UNAVAILABLE = "unavailable"

PATH_STATES: Tuple[str, ...] = (PATH_PRESENT, PATH_ABSENT, PATH_UNAVAILABLE)

KIND_FILE = "file"
KIND_DIRECTORY = "directory"
#: The link object itself, never its target. A broken symlink is still one of
#: these: the path exists, and what it points at is a different question this
#: build does not ask.
KIND_SYMLINK = "symlink"
#: A socket, FIFO, device or anything else. Named rather than refused, because
#: "something is there" is the fact being recorded and its exact species is not
#: something a path-state observation needs to enumerate.
KIND_OTHER = "other"

PATH_KINDS: Tuple[str, ...] = (KIND_FILE, KIND_DIRECTORY, KIND_SYMLINK, KIND_OTHER)


# -- observation-level vocabulary ---------------------------------------------

#: Every target path was observed as present or absent.
OBSERVATION_COMPLETE = "complete"

#: The targets were known and looked at, and at least one could not be observed
#: safely. The paths that *were* observed are stored, because they are real
#: facts worth auditing — but a consumer must never read this as complete.
OBSERVATION_INCOMPLETE = "incomplete"

#: No meaningful path set exists. The lineage was unavailable, the target set
#: exceeded the bound, the boundary was lost, or observation would not settle.
#: Carries no paths.
OBSERVATION_UNAVAILABLE = "unavailable"

STORED_OBSERVATION_STATES: Tuple[str, ...] = (
    OBSERVATION_COMPLETE,
    OBSERVATION_INCOMPLETE,
    OBSERVATION_UNAVAILABLE,
)

#: No row at all. The turn ran before final-state observation existed, or the
#: process died before the boundary could be recorded. Never stored — the store
#: returns it for absence, the same three-way shape criteria and continuity use.
#:
#: The two causes are deliberately **not** distinguished, and that is safe
#: because they mean the same thing to every consumer: nothing was recorded, so
#: nothing may be assumed. What must never happen is the third option — going and
#: looking at the repository now and calling the answer historical.
OBSERVATION_LEGACY_UNKNOWN = "legacy_unknown"

OBSERVATION_STATES: Tuple[str, ...] = STORED_OBSERVATION_STATES + (
    OBSERVATION_LEGACY_UNKNOWN,
)


# -- closed reasons -----------------------------------------------------------

#: The active criteria for this turn could not be resolved, so there is no
#: defensible target set. Never replaced by "the current snapshot's paths": that
#: would be a guessed requirement set wearing an observation's clothes.
REASON_LINEAGE_UNAVAILABLE = "lineage_unavailable"

#: More distinct target paths than :data:`MAX_FINAL_STATE_TARGETS`.
REASON_TARGET_LIMIT_EXCEEDED = "target_limit_exceeded"

#: A component of the path is a symlink. Refused rather than followed, whether
#: it points inside the project or outside it — the rule is about traversal, not
#: about where the link happens to land, because a link that is safe today can be
#: repointed tomorrow.
REASON_SYMLINK_TRAVERSAL_REFUSED = "symlink_traversal_refused"

#: The path is not one Cofferdam will look at: it fails the shared lexical gate
#: or is on the sensitive deny list.
REASON_UNSAFE_PATH = "unsafe_path"

REASON_PERMISSION_DENIED = "permission_denied"
REASON_OBSERVATION_IO_ERROR = "observation_io_error"

#: The path set kept changing underneath the observation. Bounded retries were
#: exhausted; no optimistic result is produced after detected instability.
REASON_OBSERVATION_UNSTABLE = "observation_unstable"

#: The post-worker boundary was lost — the project root is gone or unusable at
#: the moment of observation. **Never** repaired by observing later: a filesystem
#: read taken after the boundary is not the boundary.
REASON_POST_WORKER_BOUNDARY_LOST = "post_worker_boundary_lost"

#: The platform cannot resolve a path without a pathname race. Fails closed
#: rather than degrading to an unanchored walk, which is
#: :mod:`~...mind.documents`'s rule and reason.
REASON_CONTAINMENT_UNPROVEN = "containment_unproven"

REASON_PROJECT_UNAVAILABLE = "project_unavailable"

REASONS: Tuple[str, ...] = (
    REASON_LINEAGE_UNAVAILABLE,
    REASON_TARGET_LIMIT_EXCEEDED,
    REASON_SYMLINK_TRAVERSAL_REFUSED,
    REASON_UNSAFE_PATH,
    REASON_PERMISSION_DENIED,
    REASON_OBSERVATION_IO_ERROR,
    REASON_OBSERVATION_UNSTABLE,
    REASON_POST_WORKER_BOUNDARY_LOST,
    REASON_CONTAINMENT_UNPROVEN,
    REASON_PROJECT_UNAVAILABLE,
)

#: Reasons that describe one path rather than the whole observation.
PATH_REASONS: Tuple[str, ...] = (
    REASON_SYMLINK_TRAVERSAL_REFUSED,
    REASON_UNSAFE_PATH,
    REASON_PERMISSION_DENIED,
    REASON_OBSERVATION_IO_ERROR,
    REASON_CONTAINMENT_UNPROVEN,
    REASON_PROJECT_UNAVAILABLE,
)


# -- shapes -------------------------------------------------------------------


@dataclass(frozen=True)
class PathObservation:
    """One target path, as the machine found it.

    ``kind`` is set exactly when the state is ``present``; ``reason`` exactly when
    it is ``unavailable``. ``absent`` carries neither, because it is a complete
    answer on its own.
    """

    ordinal: int
    path: str
    state: str
    kind: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class FinalStateObservation:
    """One turn's effective post-worker path state, or its absence.

    One shape for all four states, so a reader never branches on ``None`` before
    it can ask what happened. A ``legacy_unknown`` observation carries no
    identity, no fingerprint and no paths, because none were ever recorded.

    Deliberately **not** published anywhere. There is no ``to_dict``, no route
    and no bridge operation in PR14: this is machine evidence for a layer that
    does not exist yet, and a serializer written before anything needs one is how
    an internal shape becomes a contract by accident.
    """

    task_id: str
    turn_number: int
    state: str
    observation_id: Optional[str] = None
    observer_version: Optional[int] = None
    limitation_reason: Optional[str] = None
    #: The PR11 resolved-active-criteria fingerprint that selected these targets.
    #: Audit of *why these paths*, not of what was found.
    lineage_fingerprint: Optional[str] = None
    #: Audit anchor only. Never the authority for effective existence.
    head_revision: Optional[str] = None
    path_count: int = 0
    fingerprint: Optional[str] = None
    recorded_at: Optional[str] = None
    paths: Tuple[PathObservation, ...] = ()

    @property
    def recorded(self) -> bool:
        """Whether Cofferdam wrote anything about final state for this turn."""
        return self.state in STORED_OBSERVATION_STATES

    @property
    def complete(self) -> bool:
        """Whether every target path was observed. Never true for ``incomplete``."""
        return self.state == OBSERVATION_COMPLETE


# -- target selection ---------------------------------------------------------


def target_paths(active: Sequence) -> Tuple[str, ...]:
    """The paths a resolved active criteria set names, in deterministic order.

    Pure. ``active`` is a sequence of :class:`~.lineage.ActiveCriterion`, already
    in the resolver's deterministic order, and this walks it in that order taking
    ``path`` and then ``to_path`` from each criterion that carries them.

    **Exact deduplication only.** Two criteria naming the identical normalized
    path produce one target, because looking twice at one path is waste. Nothing
    else is collapsed: similar paths, matching basenames, equal fingerprints and
    matching descriptions are all things unrelated criteria can share, and
    treating any of them as equivalent would be the inference this milestone
    refuses everywhere else.

    A ``manual`` criterion contributes nothing — it has no path — and that is not
    a gap: a manual requirement is undecidable by machine at every turn.
    """
    ordered: List[str] = []
    seen = set()
    for entry in active:
        criterion = getattr(entry, "criterion", entry)
        for value in (
            getattr(criterion, "path", None),
            getattr(criterion, "to_path", None),
        ):
            if not value or value in seen:
                continue
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


# -- the observer -------------------------------------------------------------
#
# The one impure thing in this module. It reads the filesystem and nothing else:
# no Git, no subprocess, no shell, no network, no provider, no database.


def _kind(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return KIND_SYMLINK
    if stat.S_ISDIR(mode):
        return KIND_DIRECTORY
    if stat.S_ISREG(mode):
        return KIND_FILE
    return KIND_OTHER


def _blocked(parent_fd: int, segment: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Why an intermediate component could not be opened as a directory.

    ``ENOTDIR`` is genuinely ambiguous here and getting it wrong is the whole
    security question. With ``O_NOFOLLOW`` the kernel reports ``ENOTDIR`` both for
    *a regular file where a directory was expected* — in which case the target
    path really cannot exist — and for *a symlink to a directory*, because the
    link itself is not a directory. ``repo/external -> /outside`` hits the second
    case, and calling it ``absent`` would answer a question about a path
    Cofferdam is not allowed to look at.

    So the component is ``lstat``-ed to see which it was. A symlink is refused; a
    non-directory is a real absence.
    """
    try:
        info = os.lstat(segment, dir_fd=parent_fd)
    except OSError:
        # It vanished between the open and the check. Nothing is there now, and
        # that is an honest absence rather than an error.
        return PATH_ABSENT, None, None
    if stat.S_ISLNK(info.st_mode):
        return PATH_UNAVAILABLE, None, REASON_SYMLINK_TRAVERSAL_REFUSED
    return PATH_ABSENT, None, None


def observe_path(root: Path, relative: str) -> Tuple[str, Optional[str], Optional[str]]:
    """One path's effective state as ``(state, kind, reason)``. Never raises.

    Containment is the kernel's: the verified root is opened, then every
    intermediate component relative to the descriptor above it with
    ``O_NOFOLLOW``, so no symlink is ever traversed and ``..`` cannot appear
    because the lexical gate already refused it. The final component is
    ``lstat``-ed rather than opened — that is what lets a symlink be observed as
    itself, and what stops a FIFO from blocking a turn boundary.
    """
    try:
        relative = normalize_claim_path(relative)
    except Exception:
        return PATH_UNAVAILABLE, None, REASON_UNSAFE_PATH
    if is_denied_path(relative):
        return PATH_UNAVAILABLE, None, REASON_UNSAFE_PATH
    if not descriptor_resolution_supported() or os.stat not in os.supports_dir_fd:
        return PATH_UNAVAILABLE, None, REASON_CONTAINMENT_UNPROVEN
    try:
        verified = verify_root(Path(root))
    except ProjectRootInvalid:
        return PATH_UNAVAILABLE, None, REASON_PROJECT_UNAVAILABLE
    except OSError:
        return PATH_UNAVAILABLE, None, REASON_PROJECT_UNAVAILABLE

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    segments = relative.split("/")
    descriptors: List[int] = []
    try:
        try:
            descriptors.append(os.open(str(verified), flags))
        except PermissionError:
            return PATH_UNAVAILABLE, None, REASON_PERMISSION_DENIED
        except OSError:
            return PATH_UNAVAILABLE, None, REASON_PROJECT_UNAVAILABLE

        for segment in segments[:-1]:
            try:
                descriptors.append(os.open(segment, flags, dir_fd=descriptors[-1]))
            except FileNotFoundError:
                # A missing directory on the way down means the target cannot be
                # there. A real absence, not a failure to look.
                return PATH_ABSENT, None, None
            except PermissionError:
                return PATH_UNAVAILABLE, None, REASON_PERMISSION_DENIED
            except OSError as failure:
                if failure.errno in (errno.ENOTDIR, errno.ELOOP):
                    return _blocked(descriptors[-1], segment)
                return PATH_UNAVAILABLE, None, REASON_OBSERVATION_IO_ERROR

        try:
            info = os.lstat(segments[-1], dir_fd=descriptors[-1])
        except (FileNotFoundError, NotADirectoryError):
            return PATH_ABSENT, None, None
        except PermissionError:
            return PATH_UNAVAILABLE, None, REASON_PERMISSION_DENIED
        except OSError:
            return PATH_UNAVAILABLE, None, REASON_OBSERVATION_IO_ERROR
        return PATH_PRESENT, _kind(info.st_mode), None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:  # pragma: no cover - defensive
                pass


def _pass(root: Path, paths: Sequence[str]):
    return tuple(observe_path(root, path) for path in paths)


def observe_paths(root: Path, paths: Sequence[str]):
    """Every target path, twice, and only an answer both passes agree on.

    Returns ``(observations, limitation)``. ``limitation`` is
    :data:`REASON_OBSERVATION_UNSTABLE` when the passes never agreed.

    **What this does and does not promise.** Cofferdam's dispatch lock keeps its
    own workers out, but a person or an unrelated process can touch the project
    at any moment, and no filesystem gives an atomic multi-path snapshot. So the
    guarantee is bounded and stated rather than implied: the set is read twice,
    and a disagreement means something moved and the read is retried, up to
    :data:`MAX_STABILITY_ATTEMPTS`. After that the observation is refused rather
    than reported optimistically.

    **The honest limitation**: v1 observes existence and kind. A file whose
    *contents* changed between the two passes looks identical to both, and this
    check will not notice. That is not a hole in the stability logic — it is the
    scope of the observation, and a content-level guarantee would need content
    evidence, which PR14 deliberately does not collect.
    """
    previous = _pass(root, paths)
    for _ in range(MAX_STABILITY_ATTEMPTS - 1):
        current = _pass(root, paths)
        if current == previous:
            return current, None
        previous = current
    return (), REASON_OBSERVATION_UNSTABLE


# -- the fingerprint ----------------------------------------------------------

TAG_FINGERPRINT = b"cofferdam.evidence.finalstate.v1"
LENGTH_PREFIX_WIDTH = 8
FINGERPRINT_CHARS = 64


class _Fingerprint:
    """The domain-tagged, length-prefixed construction the rest of M2K uses."""

    __slots__ = ("_hasher",)

    def __init__(self) -> None:
        self._hasher = hashlib.sha256()
        self._hasher.update(TAG_FINGERPRINT)

    def field(self, value: object) -> "_Fingerprint":
        if value is None:
            data = b"\x00none"
        elif isinstance(value, bool):
            data = b"\x00bool:" + (b"1" if value else b"0")
        elif isinstance(value, int):
            data = b"\x00int:" + str(value).encode("utf-8")
        else:
            data = b"\x00str:" + str(value).encode("utf-8")
        self._hasher.update(len(data).to_bytes(LENGTH_PREFIX_WIDTH, "big"))
        self._hasher.update(data)
        return self

    def hexdigest(self) -> str:
        return self._hasher.hexdigest()


def final_state_fingerprint(
    task_id: str,
    turn_number: int,
    state: str,
    limitation_reason: Optional[str],
    lineage_fingerprint: Optional[str],
    head_revision: Optional[str],
    paths: Sequence[PathObservation],
) -> str:
    """A stable hash of exactly what was observed, and of why those paths.

    What is in it
    -------------

    The observer version; the task and turn; the observation state and its
    limitation; the lineage fingerprint that selected the targets; the HEAD
    revision anchor; and every path result in stored order — ordinal, path,
    state, kind and reason.

    The lineage fingerprint is in because *which paths were looked at* is half of
    what an observation asserts. Two turns that found the same three paths present
    are not the same fact if one of them was answering a different requirement
    set. The HEAD anchor is in for the same reason and with the same caveat as
    everywhere else: it is context, not authority.

    What is deliberately **not** in it
    ----------------------------------

    * **The observation id.** Minted per row, carrying a clock and randomness.
    * **``recorded_at``, or any clock.** It must survive a restart unchanged.
    * **Database rowids or insertion order.** The order hashed is the stored
      ordinal.
    * **The absolute project root, or any host path.** A deployment that moves
      must not move a stored fingerprint, and every path here is
      project-relative by construction.
    * **Provider or session identifiers.**
    """
    digest = _Fingerprint()
    digest.field("cofferdam.evidence.finalstate")
    digest.field(FINAL_STATE_OBSERVER_VERSION)
    digest.field(task_id)
    digest.field(turn_number)
    digest.field(state)
    digest.field(limitation_reason)
    digest.field(lineage_fingerprint)
    digest.field(head_revision)
    digest.field(len(paths))
    for observation in sorted(paths, key=lambda item: item.ordinal):
        digest.field(observation.ordinal)
        digest.field(observation.path)
        digest.field(observation.state)
        digest.field(observation.kind)
        digest.field(observation.reason)
    return digest.hexdigest()


def verify_final_state_fingerprint(observation: "FinalStateObservation") -> bool:
    """Whether a stored observation still hashes to the fingerprint it carries.

    M2K PR18 needs this and PR14 did not: PR14 wrote the fingerprint and nothing
    read it back, so a row edited outside the service — every field of it, paths
    included — would have been consumed as authority on the strength of a string
    nobody recomputed. Recomputing is what makes the fingerprint evidence rather
    than decoration.

    **Pure, and the same algorithm.** It calls :func:`final_state_fingerprint`
    on the stored fields rather than re-deriving the construction, because two
    implementations of one hash is how they drift. Nothing here observes a path,
    opens a repository or touches a database.

    Returns ``False`` — never raises, and never repairs — when:

    * the observation was never recorded (``legacy_unknown``), so there is no
      fingerprint to verify and no fields to verify it against;
    * it carries no fingerprint at all;
    * its ``observer_version`` is not :data:`FINAL_STATE_OBSERVER_VERSION`. The
      hash binds *this module's* observer version, so recomputing it for a row
      written under other semantics would compare two different things and call
      the disagreement corruption. A caller must decide what an unsupported
      observer version means **before** asking this, and say so in its own
      vocabulary.
    """
    if observation.state not in STORED_OBSERVATION_STATES:
        return False
    if not observation.fingerprint:
        return False
    if observation.observer_version != FINAL_STATE_OBSERVER_VERSION:
        return False
    return observation.fingerprint == final_state_fingerprint(
        observation.task_id,
        int(observation.turn_number),
        observation.state,
        observation.limitation_reason,
        observation.lineage_fingerprint,
        observation.head_revision,
        observation.paths,
    )


__all__ = [
    "FINAL_STATE_OBSERVER_VERSION",
    "FINGERPRINT_CHARS",
    "KIND_DIRECTORY",
    "KIND_FILE",
    "KIND_OTHER",
    "KIND_SYMLINK",
    "MAX_FINAL_STATE_TARGETS",
    "MAX_STABILITY_ATTEMPTS",
    "OBSERVATION_COMPLETE",
    "OBSERVATION_INCOMPLETE",
    "OBSERVATION_LEGACY_UNKNOWN",
    "OBSERVATION_STATES",
    "OBSERVATION_UNAVAILABLE",
    "PATH_ABSENT",
    "PATH_KINDS",
    "PATH_PRESENT",
    "PATH_REASONS",
    "PATH_STATES",
    "PATH_UNAVAILABLE",
    "REASONS",
    "REASON_CONTAINMENT_UNPROVEN",
    "REASON_LINEAGE_UNAVAILABLE",
    "REASON_OBSERVATION_IO_ERROR",
    "REASON_OBSERVATION_UNSTABLE",
    "REASON_PERMISSION_DENIED",
    "REASON_POST_WORKER_BOUNDARY_LOST",
    "REASON_PROJECT_UNAVAILABLE",
    "REASON_SYMLINK_TRAVERSAL_REFUSED",
    "REASON_TARGET_LIMIT_EXCEEDED",
    "REASON_UNSAFE_PATH",
    "STORED_OBSERVATION_STATES",
    "TAG_FINGERPRINT",
    "FinalStateObservation",
    "PathObservation",
    "final_state_fingerprint",
    "observe_path",
    "observe_paths",
    "target_paths",
    "verify_final_state_fingerprint",
]
