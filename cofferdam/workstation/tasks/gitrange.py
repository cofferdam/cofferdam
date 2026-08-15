"""Committed-work observations between a stored PR4 boundary and a stable HEAD.

Why this module exists
----------------------

PR3 observes the index and working tree *relative to the current HEAD*. PR4
records, before the worker is allowed to start, the revision the repository stood
at. Between them they leave one question open, and it is the question that
matters most once a worker starts committing its own work:

    what did the repository gain between the recorded boundary and now?

PR3 cannot answer it — after the worker commits, its work is *in* HEAD, so the
status output is empty and correct and useless. PR4 records the boundary but
consumes nothing. This module reads the range.

What a range is, and what it is not
-----------------------------------

``git diff <baseline> <target>`` is a **tree comparison**, not a history. It
answers "how do these two trees differ", and a caller who labels that answer
"what the worker committed" has made a claim the command never made. The two
coincide only when the baseline is genuinely an ancestor of the target.

That is not a theoretical worry. On a repository where the baseline was recorded
on one branch and the target is on another, the tree diff reports the other
branch's files as **deleted** — by a worker that deleted nothing. A hard reset
backwards produces the same shape. So this module establishes the history
relation *first*, with :data:`GIT_IS_ANCESTOR`, and refuses to diff at all when
the relation does not hold. A diverged history is recorded as
:data:`ANCESTRY_DIVERGED` with no changes, which is an honest absence; inventing
a change list there would manufacture worker history out of somebody's branch
switch.

The record grammar is not PR3's
-------------------------------

``git status --porcelain=v1 -z`` and ``git diff --name-status -z`` are different
formats, and the difference is a silent data corruption if it is assumed away:

* porcelain packs ``XY`` and the path into **one** NUL-terminated field;
  ``--name-status`` makes the status its own field and the path the next one.
* porcelain, under ``-z``, emits a rename as **destination then source**;
  ``--name-status`` emits **source then destination**.

Reusing PR3's parser would therefore swap the source and destination of every
rename while looking entirely correct. Both grammars were measured against the
installed Git (2.53) rather than read from documentation, and
``test_git_range_capture.py`` keeps them measured.

Configuration is not allowed a vote
-----------------------------------

Rename detection is a *repository config* setting. With ``diff.renames=false`` a
move reports as an add plus a delete, which is a different set of machine facts
about the same event. Every behaviour that config could change is therefore
pinned on the argv — ``--find-renames``, ``--no-ext-diff``, ``--no-textconv`` —
so the observation describes the repository rather than the repository's
preferences. ``--no-ext-diff`` and ``--no-textconv`` additionally matter for a
second reason: both can name a **program**, and a probe that let a repository
choose a helper to execute would be running project-controlled code.

Machine-owned, like PR4
-----------------------

Both revisions come from durable machine facts: the baseline from the PR4 row,
the target from this module's own ``rev-parse``. Neither is ever a revspec. The
adapter, the prompt, the follow-up text and the API caller choose nothing —
not the root, not either revision, not the rename threshold, not the history
policy. :func:`_valid_revision` refuses anything that is not a resolved object
id, so ``HEAD~5``, a branch name and ``--output=/tmp/x`` are all rejected by
shape before they could reach an argv.

The limit this cannot exceed
----------------------------

A range is *machine-observed change between two recorded points*. It is not
proof of causation, and PR4's boundary may itself have been dirty — in which
case changes that existed before the worker ran can be committed inside this
range and are indistinguishable from the worker's own. That is why the boundary
quality travels with the observation and why the assembler refuses to derive a
conflict from a dirty boundary. Nothing built on this may say "the worker did
this" in those words.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from .gitbaseline import (
    CAPTURE_CAPTURED,
    COVERAGE_COMPLETE as BASELINE_COVERAGE_COMPLETE,
    GitBaseline,
    HEAD_PRESENT,
    WORKTREE_CLEAN,
    WORKTREE_DIRTY,
)
from .models import (
    CHANGE_CREATED,
    CHANGE_DELETED,
    CHANGE_MODIFIED,
    CHANGE_RENAMED,
    CHANGE_UNKNOWN,
    EVIDENCE_ARTIFACT,
    EVIDENCE_COMMIT,
    EVIDENCE_FILE,
    EVIDENCE_GIT_OBSERVED,
    MAX_EVIDENCE_ITEMS,
    OBSERVATION_DOMAIN_COMMITTED_RANGE,
    EvidenceReference,
)

# -- capture state -----------------------------------------------------------

#: The range was read and can be relied on.
RANGE_CAPTURED = "captured"
#: No usable range. ``reason`` says which wall it hit. Terminal and honest.
RANGE_UNAVAILABLE = "unavailable"

RANGE_CAPTURE_STATES: Tuple[str, ...] = (RANGE_CAPTURED, RANGE_UNAVAILABLE)

# -- history relation --------------------------------------------------------
#
# The whole safety argument of this module is in this vocabulary. It is a
# separate dimension from capture state: a diverged history is a *successful*
# reading of the repository that establishes the range is not meaningful.

#: The baseline is an ancestor of the target. Normal committed-since semantics.
ANCESTRY_LINEAR = "linear"
#: Baseline and target are the same commit. A complete range of zero changes —
#: which is a fact, not a failure, and must not be reported as unavailable.
ANCESTRY_IDENTICAL = "identical"
#: The baseline is **not** an ancestor: a branch switch, a reset, a rebase, or a
#: rewritten history. A tree diff here would describe somebody else's commits as
#: this worker's, so no diff is run and no changes are recorded.
ANCESTRY_DIVERGED = "diverged"
#: The relation could not be established — most often because the baseline
#: object is not in this repository. Distinguished from ``diverged`` because
#: "the history moved" and "I could not look" are different facts.
ANCESTRY_UNKNOWN = "unknown"

ANCESTRIES: Tuple[str, ...] = (
    ANCESTRY_LINEAR,
    ANCESTRY_IDENTICAL,
    ANCESTRY_DIVERGED,
    ANCESTRY_UNKNOWN,
)

#: The only two relations under which a range may be interpreted as
#: "committed since the baseline".
RANGE_ELIGIBLE_ANCESTRIES: Tuple[str, ...] = (ANCESTRY_LINEAR, ANCESTRY_IDENTICAL)

# -- coverage ----------------------------------------------------------------

RANGE_COVERAGE_COMPLETE = "complete"
RANGE_COVERAGE_INCOMPLETE = "incomplete"
RANGE_COVERAGE_UNAVAILABLE = "unavailable"

RANGE_COVERAGES: Tuple[str, ...] = (
    RANGE_COVERAGE_COMPLETE,
    RANGE_COVERAGE_INCOMPLETE,
    RANGE_COVERAGE_UNAVAILABLE,
)

# -- reasons -----------------------------------------------------------------
#
# Closed and code-owned, for PR4's reasons: this value is persisted, read back
# and rendered, and Git's stderr carries absolute host paths.

REASON_BASELINE_UNAVAILABLE = "baseline_unavailable"
REASON_HISTORY_DIVERGED = "history_diverged"
REASON_TARGET_UNSTABLE = "target_unstable"
REASON_OUTPUT_TRUNCATED = "output_truncated"
REASON_MALFORMED_RECORD = "malformed_record"
REASON_NOT_A_REPOSITORY = "not_a_repository"
REASON_PROBE_FAILED = "probe_failed"
REASON_PROBE_TIMEOUT = "probe_timeout"
#: More changed paths than one event's evidence list can carry. Distinct from
#: ``output_truncated``, which is Git's output hitting the probe's own bound:
#: this one is the *record* being too small for what the probe read, and a reader
#: deserves to know which of the two happened.
REASON_EVIDENCE_BUDGET = "evidence_budget_exceeded"
#: A path Git reported that this build will not record as a project-relative
#: name. Recorded rather than dropped in silence: a path the observation does not
#: mention is indistinguishable from a path that did not change, which is the
#: inference this milestone exists to prevent.
REASON_UNSAFE_PATH = "unsafe_path_refused"

RANGE_REASONS: Tuple[str, ...] = (
    REASON_BASELINE_UNAVAILABLE,
    REASON_HISTORY_DIVERGED,
    REASON_TARGET_UNSTABLE,
    REASON_OUTPUT_TRUNCATED,
    REASON_MALFORMED_RECORD,
    REASON_NOT_A_REPOSITORY,
    REASON_PROBE_FAILED,
    REASON_PROBE_TIMEOUT,
    REASON_EVIDENCE_BUDGET,
    REASON_UNSAFE_PATH,
)

# -- boundary quality --------------------------------------------------------
#
# **The load-bearing limit of this whole domain**, and a separate dimension from
# every state above: those describe how well the *range* could be read, this
# describes what the range is a range *from*.
#
# PR4 records whether the repository was already dirty before the worker was
# allowed to start. When it was, a path in this range may have been changed by
# somebody else before the turn began and merely committed inside it — the
# revision boundary is real, but it is not a clean causal before-and-after of the
# worker. So a dirty or unestablished boundary may contribute machine-observed
# change and may **not** contribute a contradiction: `_group_agreement` refuses
# to derive `operation_agreement = false` from one, because the mismatch it would
# be reporting could be entirely the work of a state that predates the turn.

#: HEAD was recorded, the tree was clean, and the status read was whole. The only
#: quality under which the range is a clean worker boundary.
BOUNDARY_CLEAN_COMPLETE = "clean_complete"
#: The tree already carried changes before the worker started.
BOUNDARY_DIRTY = "dirty"
#: A revision was recorded but its cleanliness was not established — a truncated
#: or unreadable status. Not "clean", and not "no boundary" either.
BOUNDARY_INCOMPLETE = "incomplete"
#: No usable boundary: no PR4 row at all, a failed capture, or an unborn HEAD.
BOUNDARY_UNAVAILABLE = "unavailable"

BOUNDARY_QUALITIES: Tuple[str, ...] = (
    BOUNDARY_CLEAN_COMPLETE,
    BOUNDARY_DIRTY,
    BOUNDARY_INCOMPLETE,
    BOUNDARY_UNAVAILABLE,
)

# -- change kinds ------------------------------------------------------------
#
# Deliberately the same words PR3 uses for the same concepts, so one vocabulary
# describes a path change whichever domain observed it — plus two that only a
# range can prove.

RANGE_CREATED = "created"
RANGE_MODIFIED = "modified"
RANGE_DELETED = "deleted"
RANGE_RENAMED = "renamed"
RANGE_COPIED = "copied"
RANGE_TYPE_CHANGED = "type_changed"

RANGE_CHANGE_KINDS: Tuple[str, ...] = (
    RANGE_CREATED,
    RANGE_MODIFIED,
    RANGE_DELETED,
    RANGE_RENAMED,
    RANGE_COPIED,
    RANGE_TYPE_CHANGED,
)

#: The status letter Git puts in the first field, mapped to our vocabulary.
#: ``R`` and ``C`` carry a similarity score (``R100``), so only the first
#: character selects. ``U`` (unmerged) is deliberately absent: a conflicted path
#: is not a committed change and this build refuses rather than guesses.
_STATUS_LETTERS = {
    "A": RANGE_CREATED,
    "M": RANGE_MODIFIED,
    "D": RANGE_DELETED,
    "R": RANGE_RENAMED,
    "C": RANGE_COPIED,
    "T": RANGE_TYPE_CHANGED,
}

#: The two statuses that carry a second path field.
_TWO_PATH_LETTERS = ("R", "C")

# -- the probe ---------------------------------------------------------------

GIT_IS_REPO: Tuple[str, ...] = ("git", "rev-parse", "--is-inside-work-tree")
GIT_OBJECT_FORMAT: Tuple[str, ...] = ("git", "rev-parse", "--show-object-format")
GIT_HEAD: Tuple[str, ...] = ("git", "rev-parse", "--verify", "--quiet", "HEAD")

#: Ancestry. Exit 0 means ancestor, exit 1 means not, exit 128 means an object
#: could not be read — three outcomes, and collapsing 128 into 1 would report a
#: missing baseline as a rewritten history.
GIT_IS_ANCESTOR: Tuple[str, ...] = ("git", "merge-base", "--is-ancestor")

#: The range itself. Every flag here disables something a repository's own
#: configuration would otherwise decide:
#:
#: ``--find-renames`` because ``diff.renames=false`` turns a move into an add
#: plus a delete; ``--no-ext-diff`` and ``--no-textconv`` because both can name
#: a program to run, and a probe must not execute a helper the project chose.
#: ``-z`` because paths are then raw rather than quoted, and ``--name-status``
#: because nothing here wants a patch — only which paths changed and how.
GIT_DIFF_NAME_STATUS: Tuple[str, ...] = (
    "git", "diff", "--name-status", "-z",
    "--find-renames", "--no-ext-diff", "--no-textconv",
)

RANGE_ALLOWED_COMMANDS: Tuple[Tuple[str, ...], ...] = (
    GIT_IS_REPO,
    GIT_OBJECT_FORMAT,
    GIT_HEAD,
    GIT_IS_ANCESTOR,
    GIT_DIFF_NAME_STATUS,
)

#: The same closed environment PR4 proved, for the same reasons.
PROBE_ENVIRONMENT = {
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "LC_ALL": "C",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
}

PROBE_TIMEOUT_SECONDS = 15.0
MAX_PROBE_OUTPUT = 262144

#: How many change records are kept. Exceeding it is *recorded* rather than
#: silently dropped — an over-cap range is still a real observation, it is just
#: not a whole one.
MAX_RANGE_RECORDS = 200

#: Same bounded-snapshot discipline as PR4, and the same number: the target is
#: read, the range is observed, the target is read again.
MAX_CAPTURE_ATTEMPTS = 3

OBJECT_FORMAT_LENGTHS = {"sha1": 40, "sha256": 64}
MAX_REVISION_CHARS = max(OBJECT_FORMAT_LENGTHS.values())
MAX_REASON_CHARS = 40

ProbeRunner = Callable[[Sequence[str], Path], Tuple[int, bytes]]


@dataclass(frozen=True)
class CommittedChange:
    """One path the range proves changed, and how."""

    kind: str
    path: str
    previous_path: Optional[str] = None
    status: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind not in RANGE_CHANGE_KINDS:
            raise ValueError("that is not a range change kind")
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("a change needs a path")
        if self.previous_path is not None and self.kind not in (RANGE_RENAMED, RANGE_COPIED):
            raise ValueError("only a rename or a copy has a source path")


@dataclass(frozen=True)
class CommittedRange:
    """One turn's committed-work observation, as the machine read it.

    Carries no root, no absolute path, no file content, no patch and no command
    text — the same exclusions PR4's row makes, for the same reasons.
    """

    capture_state: str
    ancestry: str
    coverage: str
    baseline_revision: Optional[str] = None
    target_revision: Optional[str] = None
    object_format: Optional[str] = None
    changes: Tuple[CommittedChange, ...] = ()
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.capture_state not in RANGE_CAPTURE_STATES:
            raise ValueError("that is not a range capture state")
        if self.ancestry not in ANCESTRIES:
            raise ValueError("that is not a history relation")
        if self.coverage not in RANGE_COVERAGES:
            raise ValueError("that is not a range coverage")
        if self.reason is not None and self.reason not in RANGE_REASONS:
            raise ValueError("that is not a range reason code")
        if self.changes and self.ancestry not in RANGE_ELIGIBLE_ANCESTRIES:
            # The rule this class exists to make unrepresentable: a diverged or
            # unknown history may never carry a change list.
            raise ValueError("a range without a valid history relation has no changes")
        if self.coverage == RANGE_COVERAGE_COMPLETE and self.capture_state != RANGE_CAPTURED:
            raise ValueError("an unavailable range cannot be complete")


def _valid_revision(text: object, object_format: Optional[str] = None) -> bool:
    """A resolved object id, and nothing that could be executable Git syntax.

    Identical in spirit to PR4's validator: lowercase hex of a length some
    supported object format produces. That refuses ``HEAD``, ``HEAD~5``, a branch
    name, ``@{upstream}``, a path, an abbreviation, an option that begins with a
    dash, and anything carrying whitespace or a control character — by
    construction rather than by blocklist.
    """
    if not isinstance(text, str):
        return False
    lengths = (
        [OBJECT_FORMAT_LENGTHS[object_format]]
        if object_format in OBJECT_FORMAT_LENGTHS
        else list(OBJECT_FORMAT_LENGTHS.values())
    )
    if len(text) not in lengths:
        return False
    return all(character in "0123456789abcdef" for character in text)


def _run(
    command: Sequence[str], root: Path, runner: Optional[ProbeRunner]
) -> Tuple[int, bytes]:
    """One probe. Closed command set, no shell, bounded output and time."""
    prefix = tuple(command)[: len(GIT_DIFF_NAME_STATUS)]
    if not any(prefix[: len(allowed)] == allowed for allowed in RANGE_ALLOWED_COMMANDS):
        raise ValueError("that command is not in the fixed set")
    if runner is not None:
        return runner(tuple(command), root)
    completed = subprocess.run(  # noqa: S603 - closed command set, shell=False
        list(command),
        shell=False,
        cwd=str(root),
        env=dict(PROBE_ENVIRONMENT),
        capture_output=True,
        timeout=PROBE_TIMEOUT_SECONDS,
        check=False,
    )
    return completed.returncode, (completed.stdout or b"")[:MAX_PROBE_OUTPUT]


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace").strip()


def parse_name_status(raw: bytes) -> Tuple[CommittedChange, ...]:
    """Parse ``git diff --name-status -z`` output. Raises on a malformed record.

    The grammar, measured rather than assumed::

        <status>\\0<path>\\0                      for A M D T
        <status>\\0<source>\\0<destination>\\0     for R### and C###

    Note the ordering: **source first**. ``git status --porcelain -z`` puts the
    destination first, so a parser shared between the two would swap every
    rename and look correct doing it.

    Raising rather than skipping is deliberate. A dropped record is a path the
    observation silently does not mention, which a reader cannot tell from a
    path that did not change — the exact inference this milestone exists to
    prevent. The caller turns the exception into an explicit
    ``malformed_record`` coverage state.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("range output must be bytes")
    if not bytes(raw).strip(b"\0"):
        # No content between the separators, so there are no records. An empty
        # payload is what Git emits for a range with no changes; a payload of
        # nothing but NULs has the same information in it and is read the same
        # way. Neither is malformed — there is no half-record to lose.
        return ()
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()          # the trailing NUL is a terminator, not a record
    changes = []
    index = 0
    while index < len(fields):
        status = fields[index].decode("utf-8", "replace")
        if not status:
            raise ValueError("empty status field")
        letter = status[0]
        kind = _STATUS_LETTERS.get(letter)
        if kind is None:
            raise ValueError("unsupported status letter")
        wants_two = letter in _TWO_PATH_LETTERS
        needed = 3 if wants_two else 2
        if index + needed > len(fields):
            raise ValueError("truncated record")
        if wants_two:
            source = fields[index + 1].decode("utf-8", "replace")
            destination = fields[index + 2].decode("utf-8", "replace")
            if not source or not destination:
                raise ValueError("a rename or copy needs both paths")
            changes.append(
                CommittedChange(
                    kind=kind, path=destination, previous_path=source, status=status
                )
            )
        else:
            path = fields[index + 1].decode("utf-8", "replace")
            if not path:
                raise ValueError("a change needs a path")
            changes.append(CommittedChange(kind=kind, path=path, status=status))
        index += needed
    return tuple(changes)


def _unavailable(ancestry: str, reason: str, **extra) -> CommittedRange:
    return CommittedRange(
        capture_state=RANGE_UNAVAILABLE,
        ancestry=ancestry,
        coverage=RANGE_COVERAGE_UNAVAILABLE,
        reason=reason,
        **extra,
    )


def capture_committed_range(
    root: object,
    baseline_revision: object,
    *,
    runner: Optional[ProbeRunner] = None,
    attempts: int = MAX_CAPTURE_ATTEMPTS,
) -> CommittedRange:
    """Observe what was committed between a stored boundary and a stable HEAD.

    Never raises, for PR4's reason: ordinary project work must not fail because
    Git evidence was unavailable. Every wall produces a bounded reason code and
    an explicitly unavailable observation instead.
    """
    if not isinstance(root, Path):
        return _unavailable(ANCESTRY_UNKNOWN, REASON_PROBE_FAILED)
    if not _valid_revision(baseline_revision):
        # Covers a revspec, an option, an abbreviation and ``None`` — a baseline
        # that is not a resolved identity is not a baseline.
        return _unavailable(ANCESTRY_UNKNOWN, REASON_BASELINE_UNAVAILABLE)
    try:
        return _capture(root, str(baseline_revision), runner, max(1, int(attempts)))
    except subprocess.TimeoutExpired:
        return _unavailable(ANCESTRY_UNKNOWN, REASON_PROBE_TIMEOUT)
    except (OSError, ValueError, subprocess.SubprocessError):
        return _unavailable(ANCESTRY_UNKNOWN, REASON_PROBE_FAILED)


def _capture(
    root: Path, baseline: str, runner: Optional[ProbeRunner], attempts: int
) -> CommittedRange:
    code, raw = _run(GIT_IS_REPO, root, runner)
    if code != 0 or _text(raw) != "true":
        return _unavailable(ANCESTRY_UNKNOWN, REASON_NOT_A_REPOSITORY)

    code, raw = _run(GIT_OBJECT_FORMAT, root, runner)
    object_format = _text(raw) if code == 0 else ""
    if object_format not in OBJECT_FORMAT_LENGTHS:
        return _unavailable(ANCESTRY_UNKNOWN, REASON_BASELINE_UNAVAILABLE)
    if not _valid_revision(baseline, object_format):
        # A sha1 baseline against a sha256 repository, for instance. The id is
        # well formed but cannot name an object here.
        return _unavailable(ANCESTRY_UNKNOWN, REASON_BASELINE_UNAVAILABLE)

    for _ in range(attempts):
        code, raw = _run(GIT_HEAD, root, runner)
        if code != 0:
            # No commit at all, so nothing can have been committed into a range.
            return _unavailable(ANCESTRY_UNKNOWN, REASON_BASELINE_UNAVAILABLE)
        first = _text(raw)
        if not _valid_revision(first, object_format):
            return _unavailable(ANCESTRY_UNKNOWN, REASON_PROBE_FAILED)

        observed = _observe(root, baseline, first, object_format, runner)

        code, raw = _run(GIT_HEAD, root, runner)
        second = _text(raw) if code == 0 else None
        if second == first:
            return observed
        # HEAD moved across the observation, so neither read describes the
        # moment and the range would span a boundary nobody chose. Try again.

    return _unavailable(ANCESTRY_UNKNOWN, REASON_TARGET_UNSTABLE)


def _observe(
    root: Path,
    baseline: str,
    target: str,
    object_format: str,
    runner: Optional[ProbeRunner],
) -> CommittedRange:
    """The history relation first, and the diff only if it holds."""
    common = {
        "baseline_revision": baseline,
        "target_revision": target,
        "object_format": object_format,
    }

    if baseline == target:
        # A complete range of zero committed changes. Saying "unavailable" here
        # would lose a real and useful fact: the worker committed nothing.
        return CommittedRange(
            capture_state=RANGE_CAPTURED,
            ancestry=ANCESTRY_IDENTICAL,
            coverage=RANGE_COVERAGE_COMPLETE,
            changes=(),
            **common,
        )

    code, _ = _run(GIT_IS_ANCESTOR + (baseline, target), root, runner)
    if code == 1:
        # Branch switch, reset, rebase, or any rewritten history. No diff is run:
        # the tree comparison would describe somebody else's commits as this
        # worker's committed work.
        return CommittedRange(
            capture_state=RANGE_UNAVAILABLE,
            ancestry=ANCESTRY_DIVERGED,
            coverage=RANGE_COVERAGE_UNAVAILABLE,
            reason=REASON_HISTORY_DIVERGED,
            changes=(),
            **common,
        )
    if code != 0:
        # 128 in practice: an object this repository does not have. A different
        # fact from a rewritten history and recorded as one.
        return CommittedRange(
            capture_state=RANGE_UNAVAILABLE,
            ancestry=ANCESTRY_UNKNOWN,
            coverage=RANGE_COVERAGE_UNAVAILABLE,
            reason=REASON_BASELINE_UNAVAILABLE,
            **common,
        )

    code, raw = _run(GIT_DIFF_NAME_STATUS + (baseline, target), root, runner)
    if code != 0:
        return CommittedRange(
            capture_state=RANGE_UNAVAILABLE,
            ancestry=ANCESTRY_LINEAR,
            coverage=RANGE_COVERAGE_UNAVAILABLE,
            reason=REASON_PROBE_FAILED,
            **common,
        )
    try:
        changes = parse_name_status(raw)
    except ValueError:
        return CommittedRange(
            capture_state=RANGE_UNAVAILABLE,
            ancestry=ANCESTRY_LINEAR,
            coverage=RANGE_COVERAGE_UNAVAILABLE,
            reason=REASON_MALFORMED_RECORD,
            **common,
        )

    truncated = len(changes) > MAX_RANGE_RECORDS or len(raw) >= MAX_PROBE_OUTPUT
    kept = changes[:MAX_RANGE_RECORDS]
    return CommittedRange(
        capture_state=RANGE_CAPTURED,
        ancestry=ANCESTRY_LINEAR,
        coverage=RANGE_COVERAGE_INCOMPLETE if truncated else RANGE_COVERAGE_COMPLETE,
        changes=kept,
        reason=REASON_OUTPUT_TRUNCATED if truncated else None,
        **common,
    )


# -- the durable record ------------------------------------------------------
#
# One observation becomes one event's worth of evidence references. Every row is
# `git_observed` and every row carries the committed-range domain, so nothing
# here can be confused with PR3's worktree observation of the same path.
#
# The operations below are the *shapes* a reader matches on, and each names one
# subject with one fact about it. None of them packs two facts into a string:
# `result="linear/complete"` would be a parser waiting to be written, over a
# separator that a future state word could contain.

#: A path the range proves changed. ``evidence_type=file``.
RANGE_OP_PATH = "git diff --name-status"
#: The boundary revision, with the quality of that boundary as its result.
RANGE_OP_BASELINE = "range baseline"
#: The observed target revision, with the history relation as its result.
RANGE_OP_TARGET = "range target"
#: How complete the path list is.
RANGE_OP_COVERAGE = "range coverage"
#: Why, when it is not complete.
RANGE_OP_LIMITATION = "range limitation"

#: The result written on the limitation row when there is nothing to report.
#:
#: The row is emitted **unconditionally**, for the reason PR3 emits its coverage
#: row unconditionally: "no limitation row" and "a build that never wrote one"
#: are indistinguishable to a later reader, and incompleteness must never be
#: inferable only from an absence.
RANGE_LIMITATION_NONE = "none"

#: What a path change means in Task Core's provider-neutral vocabulary.
#:
#: ``copied`` and ``type_changed`` map to ``unknown`` deliberately — Task Core has
#: four words and neither of those is truthfully one of them, and ``unknown`` is
#: the answer that cannot be wrong. Nothing is lost from the durable record: the
#: exact Git token is kept verbatim in ``change_status``, so a reader who wants
#: to know that Git said ``T`` can see that Git said ``T``.
_MODEL_CHANGE_KINDS = {
    RANGE_CREATED: CHANGE_CREATED,
    RANGE_MODIFIED: CHANGE_MODIFIED,
    RANGE_DELETED: CHANGE_DELETED,
    RANGE_RENAMED: CHANGE_RENAMED,
    RANGE_COPIED: CHANGE_UNKNOWN,
    RANGE_TYPE_CHANGED: CHANGE_UNKNOWN,
}

#: Metadata rows, always emitted, always first: baseline, target, coverage,
#: limitation. They are reserved rather than added on top of the budget because
#: :func:`~.store._bounded_evidence` caps the list at :data:`MAX_EVIDENCE_ITEMS`
#: and drops the overflow **silently** — a metadata row pushed over that edge
#: would take the range's interpretability with it.
#:
#: The split is deliberately metadata-heavy. A truncated path list is still a
#: usable observation and says so; a path list whose baseline, target, ancestry
#: and coverage were not recorded is not interpretable at all, and no number of
#: extra paths would make it so.
RANGE_RESERVED_REFERENCES = 4
MAX_RANGE_EVIDENCE_PATHS = MAX_EVIDENCE_ITEMS - RANGE_RESERVED_REFERENCES


def boundary_quality(baseline: Optional[GitBaseline]) -> str:
    """What kind of boundary PR4 recorded, in one word.

    ``None`` — no row at all — is :data:`BOUNDARY_UNAVAILABLE` and never
    ``clean``. Every turn on this host from before schema v6 answers ``None``,
    and so does any turn whose dispatch crashed between capture and persistence;
    reading that absence as a clean tree is the one mistake that would turn a
    missing record into a licence to contradict a worker.

    ``dirty`` wins over coverage: one valid change is enough to know the tree was
    not clean, whatever happened to the rest of the list.
    """
    if not isinstance(baseline, GitBaseline):
        return BOUNDARY_UNAVAILABLE
    if baseline.capture_state != CAPTURE_CAPTURED:
        return BOUNDARY_UNAVAILABLE
    if baseline.head_state != HEAD_PRESENT or not baseline.head_revision:
        # No revision was recorded, so there is nothing for a range to start at.
        return BOUNDARY_UNAVAILABLE
    if baseline.working_tree_state == WORKTREE_DIRTY:
        return BOUNDARY_DIRTY
    if (
        baseline.working_tree_state == WORKTREE_CLEAN
        and baseline.status_coverage == BASELINE_COVERAGE_COMPLETE
    ):
        return BOUNDARY_CLEAN_COMPLETE
    # A revision, but no established statement about what surrounded it.
    return BOUNDARY_INCOMPLETE


#: The longest path and path segment this build will record, matching the claim
#: gate so that both sides of a comparison agree on what a path is.
MAX_OBSERVED_PATH_CHARS = 512
MAX_OBSERVED_SEGMENT_CHARS = 255


def _safe_path(text: object) -> bool:
    """Whether a path Git reported may be recorded as a project-relative name.

    A **lexical** gate, and deliberately not a rewriting one: ``a/../b`` is
    refused rather than folded into ``b``, because folding it would record an
    observation about a file Git did not name.

    The rules are PR1's claim gate, restated so both sides of a comparison agree
    on what a path is. If a claim naming ``tab\\tname.txt`` structurally cannot
    exist, an observation of it can never pair with one — so recording it would
    add a permanently unmatchable row rather than a fact.

    Note what this is *not*: containment. The probe reads no file and resolves no
    path, so there is nothing here to escape from. This keeps the record
    comparable, and the refusal is published as coverage rather than swallowed.
    """
    if not isinstance(text, str) or not text:
        return False
    if len(text) > MAX_OBSERVED_PATH_CHARS:
        return False
    for character in text:
        # Control characters and NUL, checked before anything splits the string
        # so an embedded NUL cannot end a path early for one reader only.
        if character == "\x00" or ord(character) < 0x20 or ord(character) == 0x7F:
            return False
    if "\\" in text:
        # Legal in a Linux filename and a separator on Windows: one path that
        # means two things depending on who reads it.
        return False
    if text.startswith("/") or text.startswith("~"):
        return False
    if len(text) >= 2 and text[1] == ":":
        return False
    for segment in text.split("/"):
        if not segment or segment == "." or segment == "..":
            return False
        if len(segment) > MAX_OBSERVED_SEGMENT_CHARS:
            return False
    return True


def _recordable(change: CommittedChange) -> bool:
    """Both of a rename's paths have to pass, or neither half is usable."""
    if not _safe_path(change.path):
        return False
    if change.previous_path is not None and not _safe_path(change.previous_path):
        return False
    return True


def range_evidence(
    observed: CommittedRange,
    *,
    quality: str,
    observed_at: Optional[str] = None,
) -> Tuple[EvidenceReference, ...]:
    """Turn one range observation into the evidence one event carries.

    ``source`` is not a parameter and never will be. Everything this produces was
    read by Cofferdam running Git against a root only the host chose, from two
    revisions that are both machine-owned — so there is no path by which an
    adapter's report is promoted into a machine observation.

    ``quality`` comes from :func:`boundary_quality` over the turn's stored PR4
    row. It travels *with* the observation rather than being looked up at read
    time, so the record stays a complete machine fact: a bundle assembled after
    the repository has been deleted says exactly what this one says.
    """
    if quality not in BOUNDARY_QUALITIES:  # pragma: no cover - callers pass one
        quality = BOUNDARY_UNAVAILABLE

    # The path gate runs before the budget, so a refusal is not hidden behind a
    # truncation that would have dropped the path anyway.
    safe = [change for change in observed.changes if _recordable(change)]
    refused = len(observed.changes) - len(safe)

    paths = tuple(safe)[:MAX_RANGE_EVIDENCE_PATHS]
    over_budget = len(safe) > MAX_RANGE_EVIDENCE_PATHS
    incomplete = over_budget or refused
    coverage = RANGE_COVERAGE_INCOMPLETE if incomplete else observed.coverage
    # The probe's own reason wins when it has one: it names the first wall the
    # observation hit, and these are later ones. All are true; the earliest is
    # the most useful thing to have recorded.
    reason = observed.reason or (
        REASON_UNSAFE_PATH
        if refused
        else (REASON_EVIDENCE_BUDGET if over_budget else None)
    )

    references: List[EvidenceReference] = [
        EvidenceReference(
            evidence_type=EVIDENCE_COMMIT,
            source=EVIDENCE_GIT_OBSERVED,
            domain=OBSERVATION_DOMAIN_COMMITTED_RANGE,
            identifier=observed.baseline_revision,
            operation=RANGE_OP_BASELINE,
            result=quality,
            observed_at=observed_at,
        ),
        EvidenceReference(
            evidence_type=EVIDENCE_COMMIT,
            source=EVIDENCE_GIT_OBSERVED,
            domain=OBSERVATION_DOMAIN_COMMITTED_RANGE,
            # ``None`` where the target never settled. An unstable target has no
            # revision, and writing one of the two readings would be recording a
            # boundary nobody observed.
            identifier=observed.target_revision,
            operation=RANGE_OP_TARGET,
            result=observed.ancestry,
            observed_at=observed_at,
        ),
        EvidenceReference(
            evidence_type=EVIDENCE_ARTIFACT,
            source=EVIDENCE_GIT_OBSERVED,
            domain=OBSERVATION_DOMAIN_COMMITTED_RANGE,
            identifier=None,
            operation=RANGE_OP_COVERAGE,
            result=coverage,
            observed_at=observed_at,
        ),
        EvidenceReference(
            evidence_type=EVIDENCE_ARTIFACT,
            source=EVIDENCE_GIT_OBSERVED,
            domain=OBSERVATION_DOMAIN_COMMITTED_RANGE,
            identifier=None,
            operation=RANGE_OP_LIMITATION,
            result=reason or RANGE_LIMITATION_NONE,
            observed_at=observed_at,
        ),
    ]

    for change in paths:
        references.append(
            EvidenceReference(
                evidence_type=EVIDENCE_FILE,
                source=EVIDENCE_GIT_OBSERVED,
                domain=OBSERVATION_DOMAIN_COMMITTED_RANGE,
                identifier=change.path,
                operation=RANGE_OP_PATH,
                # Not PR3's "changed". These two words are what a reader sees
                # first, and a range row that said "changed" would read exactly
                # like a worktree row while meaning something else.
                result="committed",
                change_kind=_MODEL_CHANGE_KINDS.get(change.kind, CHANGE_UNKNOWN),
                # A rename's source, and only a rename's. A copy's source still
                # exists and was not replaced, which is why PR3 leaves this empty
                # for one — and copies are not requested by this argv anyway, so
                # Git does not emit them here.
                previous_identifier=(
                    change.previous_path if change.kind == RANGE_RENAMED else None
                ),
                # The Git token verbatim — ``A``, ``M``, ``D``, ``R080``, ``T``.
                # Deliberately **not** porcelain's two-character ``XY``: it is a
                # different alphabet from a different command, and the assembler
                # reads it through the domain rather than through PR3's table.
                change_status=change.status,
                observed_at=observed_at,
            )
        )
    return tuple(references)


__all__ = [
    "ANCESTRIES",
    "BOUNDARY_CLEAN_COMPLETE",
    "BOUNDARY_DIRTY",
    "BOUNDARY_INCOMPLETE",
    "BOUNDARY_QUALITIES",
    "BOUNDARY_UNAVAILABLE",
    "MAX_OBSERVED_PATH_CHARS",
    "MAX_OBSERVED_SEGMENT_CHARS",
    "MAX_RANGE_EVIDENCE_PATHS",
    "RANGE_LIMITATION_NONE",
    "RANGE_OP_BASELINE",
    "RANGE_OP_COVERAGE",
    "RANGE_OP_LIMITATION",
    "RANGE_OP_PATH",
    "RANGE_OP_TARGET",
    "RANGE_RESERVED_REFERENCES",
    "REASON_EVIDENCE_BUDGET",
    "REASON_UNSAFE_PATH",
    "boundary_quality",
    "range_evidence",
    "ANCESTRY_DIVERGED",
    "ANCESTRY_IDENTICAL",
    "ANCESTRY_LINEAR",
    "ANCESTRY_UNKNOWN",
    "CommittedChange",
    "CommittedRange",
    "GIT_DIFF_NAME_STATUS",
    "GIT_HEAD",
    "GIT_IS_ANCESTOR",
    "GIT_IS_REPO",
    "GIT_OBJECT_FORMAT",
    "MAX_CAPTURE_ATTEMPTS",
    "MAX_PROBE_OUTPUT",
    "MAX_RANGE_RECORDS",
    "MAX_REASON_CHARS",
    "MAX_REVISION_CHARS",
    "OBJECT_FORMAT_LENGTHS",
    "PROBE_ENVIRONMENT",
    "PROBE_TIMEOUT_SECONDS",
    "RANGE_ALLOWED_COMMANDS",
    "RANGE_CAPTURED",
    "RANGE_CAPTURE_STATES",
    "RANGE_CHANGE_KINDS",
    "RANGE_COPIED",
    "RANGE_COVERAGE_COMPLETE",
    "RANGE_COVERAGE_INCOMPLETE",
    "RANGE_COVERAGE_UNAVAILABLE",
    "RANGE_COVERAGES",
    "RANGE_CREATED",
    "RANGE_DELETED",
    "RANGE_ELIGIBLE_ANCESTRIES",
    "RANGE_MODIFIED",
    "RANGE_REASONS",
    "RANGE_RENAMED",
    "RANGE_TYPE_CHANGED",
    "RANGE_UNAVAILABLE",
    "REASON_BASELINE_UNAVAILABLE",
    "REASON_HISTORY_DIVERGED",
    "REASON_MALFORMED_RECORD",
    "REASON_NOT_A_REPOSITORY",
    "REASON_OUTPUT_TRUNCATED",
    "REASON_PROBE_FAILED",
    "REASON_PROBE_TIMEOUT",
    "REASON_TARGET_UNSTABLE",
    "capture_committed_range",
    "parse_name_status",
]
