"""The machine-owned Git boundary that exists before a worker turn begins.

Why this module exists
----------------------

M2K PR3 gave Cofferdam structured machine observations of the working tree:
``git status --porcelain=v1 -z --untracked-files=all``, parsed into created,
modified, deleted and renamed with the exact XY status preserved. That closed a
real gap and left one open, which the PR3 deployment demonstrated on a live
host rather than argued on paper:

    a worker may modify files **and commit them**.

After that commit the working tree is clean, ``git status`` reports nothing, and
the work the worker actually did is invisible. PR3 observes the index and
worktree relative to the *current* HEAD, and once the worker has committed, the
current HEAD **is** the worker's commit. There is nothing wrong with the
observation; it is answering a question whose answer stopped being interesting.

The missing fact is a revision the machine recorded *before the worker was
allowed to start*. This module captures exactly that, and stores nothing else.

What this module is not
-----------------------

It does not diff. There is no ``git diff``, no ``--name-status``, no commit-range
walk anywhere in this build. Deriving committed-work evidence from a stored
boundary is M2K PR5's problem, and it is a separate problem on purpose: a
boundary that is wrong, late, adapter-influenced or silently absent would make
every observation derived from it wrong in a way that looks authoritative. So
PR4 establishes the boundary and proves it, and nothing consumes it yet.

Machine-owned, and what that forbids
------------------------------------

The baseline is **observed by the host**. It is not reported, suggested,
defaulted or overridden by anything downstream. In particular the adapter, the
provider, the task prompt and the remote API caller may not choose:

* the repository root — that comes from :func:`~.projects.verify_root` against
  the host's own project registry, re-verified at dispatch;
* the revision — no ``HEAD~5``, no branch name, no revspec of any kind reaches
  Git from outside this module. Every argv below is a module constant;
* whether the tree was dirty;
* whether capture succeeded, or why it did not.

That is the whole doctrine, and the tests in ``test_git_baseline_authority.py``
are what keep it true rather than merely intended.

Why the layering is this way round
----------------------------------

The store does not run Git and this module does not touch SQLite. The service
captures a validated :class:`GitBaseline` value and hands it to the store, which
persists it. Inverting either half would put subprocess authority inside the
transaction layer, or let a persistence layer decide what is true about a
repository.

This module deliberately does **not** import
``adapters.claude_code.evidence``. Nothing under ``cofferdam/workstation`` imports
that module today and a host-owned probe must not be the first thing to, because
that would make the daemon's Git authority a detail of one adapter's package. The
four environment keys and the argv doctrine below are therefore stated again
here. Two short constant tuples are a smaller price than an inverted dependency,
and PR3's module is left exactly as it was proven.

The limit this cannot exceed
----------------------------

A clean host-owned snapshot does not prove that only the worker changed the
repository afterwards. A person with a shell, an editor autosave, a background
formatter or another tool can modify the same working tree at the same time.
What a stored boundary supports is **machine-observed change since a recorded
point**, which is a statement about records. It is not proof of causation, and
nothing built on top of it may say "the worker did this" in those words.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

# -- capture state -----------------------------------------------------------

#: The probe ran and produced a boundary that can be relied on.
CAPTURE_CAPTURED = "captured"
#: The probe ran and produced no usable boundary. ``reason`` says which wall it
#: hit. This is a terminal, durable, honest answer — never a retry marker.
CAPTURE_UNAVAILABLE = "unavailable"

CAPTURE_STATES: Tuple[str, ...] = (CAPTURE_CAPTURED, CAPTURE_UNAVAILABLE)

# -- dispatch state ----------------------------------------------------------
#
# A **different dimension** from ``capture_state``, and conflating the two is the
# mistake this vocabulary exists to prevent. ``capture_state`` says how well the
# repository could be read. ``dispatch_state`` says how far the *worker dispatch*
# got, which is what decides whether the boundary may still be replaced.
#
# The reason it has to be durable, rather than inferred from whether a turn row
# exists: on both dispatch paths the adapter is invoked before the turn row is
# written, so "no turn row" covers two situations that could not be more
# different — one where the worker was never called, and one where the worker
# ran, possibly committed, and Cofferdam crashed before recording the turn.
# Treating the second as replaceable would let a retry capture the worker's own
# commit as the "pre-work" boundary and silently destroy the real one.

#: The boundary is recorded and the adapter has **not** been invoked for this
#: turn number. The only state in which a baseline may be replaced, and it is
#: provable rather than assumed: :data:`DISPATCH_STARTED` is committed *before*
#: the adapter call, so a row still saying ``captured`` is a row whose adapter
#: had not been reached.
DISPATCH_CAPTURED = "captured"
#: The adapter has been invoked at least once against this boundary. Whether the
#: worker did anything is **unknown** — that is the point. Immutable from here.
DISPATCH_STARTED = "dispatch_started"
#: The adapter was invoked and reported a refusal or fault, and no turn opened.
#:
#: Recorded because "we learned the outcome" and "we crashed and never learned"
#: are different facts and a reader deserves to know which. It does **not**
#: re-open replacement. `AdapterRefusal` is a statement of intent, not a proof
#: about side effects: the Claude Code adapter raises it when ``send_turn``
#: fails, *after* bytes may already have reached the running worker's stdin. The
#: core cannot distinguish that from a refusal raised before anything happened
#: without pattern-matching an adapter's message text, which it must never do.
DISPATCH_REFUSED = "dispatch_refused"
#: A real Cofferdam turn exists for this boundary, written in the same
#: transaction as the turn row itself.
DISPATCH_TURN_OPENED = "turn_opened"

DISPATCH_STATES: Tuple[str, ...] = (
    DISPATCH_CAPTURED,
    DISPATCH_STARTED,
    DISPATCH_REFUSED,
    DISPATCH_TURN_OPENED,
)

#: The whole replacement rule, in one tuple. Everything else is immutable.
REPLACEABLE_DISPATCH_STATES: Tuple[str, ...] = (DISPATCH_CAPTURED,)

# -- head state --------------------------------------------------------------

#: HEAD resolved to a commit, and that commit id is stored.
HEAD_PRESENT = "present"
#: A real Git repository with no commit yet. There is no revision to store and
#: **none is invented** — in particular not the empty-tree object, which would
#: be a boundary Cofferdam made up rather than read. PR5 decides what, if
#: anything, can be compared against an unborn HEAD.
HEAD_UNBORN = "unborn"
#: A repository whose HEAD could not be read: the probe failed, timed out, or
#: moved while it was being read. No revision is stored.
HEAD_UNAVAILABLE = "unavailable"
#: The project root is not a Git working tree at all. Distinguished from
#: ``unavailable`` because it is a permanent property of the project rather than
#: a transient failure, and a reader should not be left wondering whether
#: retrying would help.
HEAD_NOT_A_REPOSITORY = "not_a_repository"

HEAD_STATES: Tuple[str, ...] = (
    HEAD_PRESENT,
    HEAD_UNBORN,
    HEAD_UNAVAILABLE,
    HEAD_NOT_A_REPOSITORY,
)

#: Only this one carries a revision. The schema enforces it as a CHECK rather
#: than trusting every future writer to remember.
HEAD_STATES_WITH_REVISION: Tuple[str, ...] = (HEAD_PRESENT,)

# -- working tree state ------------------------------------------------------

#: Status ran and reported nothing.
WORKTREE_CLEAN = "clean"
#: Status ran and reported at least one change. Whether the *list* was complete
#: is a separate fact — see ``status_coverage``. Dirty stays dirty even when the
#: list was truncated, because one valid change is enough to know.
WORKTREE_DIRTY = "dirty"
#: Status did not run, or could not be believed.
WORKTREE_UNKNOWN = "unknown"

WORKTREE_STATES: Tuple[str, ...] = (WORKTREE_CLEAN, WORKTREE_DIRTY, WORKTREE_UNKNOWN)

# -- status coverage ---------------------------------------------------------

#: Every record Git emitted was read and understood.
COVERAGE_COMPLETE = "complete"
#: Status ran, but the reading of it was bounded or something in it was refused.
#: The repository may still be honestly *dirty*; what is not known is the full
#: extent. ``clean`` + ``incomplete`` is the one combination that would be a
#: lie — a truncated read cannot conclude "nothing changed" — and the schema
#: refuses it.
COVERAGE_INCOMPLETE = "incomplete"
#: Status did not produce a usable answer.
COVERAGE_UNAVAILABLE = "unavailable"

STATUS_COVERAGES: Tuple[str, ...] = (
    COVERAGE_COMPLETE,
    COVERAGE_INCOMPLETE,
    COVERAGE_UNAVAILABLE,
)

# -- reasons -----------------------------------------------------------------
#
# A closed vocabulary of machine-readable codes. Deliberately **not** exception
# text and never raw Git stderr: stderr carries absolute host paths, and this
# value is persisted, read back and rendered. A code a reader can look up beats
# a sentence that leaked a filesystem layout.

REASON_NOT_A_REPOSITORY = "not_a_repository"
REASON_UNBORN_HEAD = "unborn_head"
REASON_PROBE_FAILED = "probe_failed"
REASON_PROBE_TIMEOUT = "probe_timeout"
REASON_HEAD_UNSTABLE = "head_unstable"
REASON_MALFORMED_REVISION = "malformed_revision"
REASON_ROOT_UNAVAILABLE = "root_unavailable"

REASONS: Tuple[str, ...] = (
    REASON_NOT_A_REPOSITORY,
    REASON_UNBORN_HEAD,
    REASON_PROBE_FAILED,
    REASON_PROBE_TIMEOUT,
    REASON_HEAD_UNSTABLE,
    REASON_MALFORMED_REVISION,
    REASON_ROOT_UNAVAILABLE,
)

# -- object format -----------------------------------------------------------

#: Git 2.29 shipped SHA-256 repositories and this host runs 2.53, so "a commit
#: id is forty hex characters" is no longer true. The length is therefore read
#: from the repository via ``--show-object-format`` rather than assumed, and the
#: validator below is told what to expect instead of guessing.
OBJECT_FORMAT_LENGTHS = {"sha1": 40, "sha256": 64}
OBJECT_FORMATS: Tuple[str, ...] = tuple(sorted(OBJECT_FORMAT_LENGTHS))

#: The widest id any supported format produces. Used as the storage bound.
MAX_REVISION_CHARS = max(OBJECT_FORMAT_LENGTHS.values())
MAX_REASON_CHARS = 40
MAX_STATE_CHARS = 20

# -- the probe ---------------------------------------------------------------

#: Literal argv. Every one of these is a constant tuple: nothing is formatted,
#: joined, interpolated or read from configuration, so there is no path by which
#: adapter, prompt or caller text becomes a Git argument. ``shell=False`` at the
#: call site is the other half.
GIT_IS_REPO: Tuple[str, ...] = ("git", "rev-parse", "--is-inside-work-tree")
GIT_OBJECT_FORMAT: Tuple[str, ...] = ("git", "rev-parse", "--show-object-format")
#: ``--verify`` makes an unborn HEAD a clean non-zero exit rather than an error
#: string to pattern-match, and ``--quiet`` keeps that failure off stderr.
GIT_HEAD: Tuple[str, ...] = ("git", "rev-parse", "--verify", "--quiet", "HEAD")
#: The same machine format PR3 proved, for the same reasons: ``-z`` so paths are
#: raw rather than quoted, ``--porcelain=v1`` pinned so a future Git redefining
#: "porcelain" cannot silently change the parse, ``--untracked-files=all`` so a
#: wholly new directory is not collapsed into one entry.
GIT_STATUS: Tuple[str, ...] = (
    "git", "status", "--porcelain=v1", "-z", "--untracked-files=all",
)

ALLOWED_COMMANDS: Tuple[Tuple[str, ...], ...] = (
    GIT_IS_REPO, GIT_OBJECT_FORMAT, GIT_HEAD, GIT_STATUS,
)

#: A closed environment. The process environment is **not** inherited: a probe
#: that picked up ``GIT_DIR``, ``GIT_WORK_TREE`` or ``GIT_INDEX_FILE`` from
#: whatever launched the daemon would be reading a repository nobody chose.
#:
#: ``GIT_OPTIONAL_LOCKS=0`` is the one that makes this observation and not a
#: mutation: without it ``git status`` may refresh and rewrite ``.git/index``,
#: which is a write to the worker's repository performed by the thing whose
#: entire job is to watch without touching.
PROBE_ENVIRONMENT = {
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "LC_ALL": "C",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
}

PROBE_TIMEOUT_SECONDS = 15.0
MAX_PROBE_OUTPUT = 65536

#: How many status records are read before the reading is called bounded. The
#: number matters less than the fact that exceeding it is *recorded* rather than
#: silently dropped.
MAX_STATUS_RECORDS = 200

#: HEAD is read, status is inspected, HEAD is read again. If the two reads agree
#: the boundary is stable across the observation. If they do not, something
#: committed, checked out or rebased underneath the probe, and neither revision
#: describes the moment — so the attempt is retried, a bounded number of times,
#: and then given up on explicitly. Three, not "until it settles": a repository
#: being rewritten in a loop must not be able to hold a worker's dispatch open.
MAX_CAPTURE_ATTEMPTS = 3

ProbeRunner = Callable[[Sequence[str], Path], Tuple[int, bytes]]


@dataclass(frozen=True)
class GitBaseline:
    """One turn's pre-work Git boundary, as the machine read it.

    Frozen because a baseline is a record of a moment that has passed. Once a
    worker has been allowed to start against it, changing it would rewrite the
    boundary that the worker's own changes are measured from.

    There is deliberately no file list, no diff, no patch, no blob, no path and
    no repository root here. A path is project content and a root is host
    filesystem layout; neither belongs in a durable evidence row, and PR5 does
    not need either to ask Git what changed between a stored revision and now.
    """

    capture_state: str
    head_state: str
    head_revision: Optional[str] = None
    object_format: Optional[str] = None
    working_tree_state: str = WORKTREE_UNKNOWN
    status_coverage: str = COVERAGE_UNAVAILABLE
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.capture_state not in CAPTURE_STATES:
            raise ValueError("unknown capture state")
        if self.head_state not in HEAD_STATES:
            raise ValueError("unknown head state")
        if self.working_tree_state not in WORKTREE_STATES:
            raise ValueError("unknown working tree state")
        if self.status_coverage not in STATUS_COVERAGES:
            raise ValueError("unknown status coverage")
        if self.reason is not None and self.reason not in REASONS:
            raise ValueError("unknown reason code")
        if self.head_state in HEAD_STATES_WITH_REVISION:
            if not self.head_revision:
                raise ValueError("a present head must carry its revision")
        elif self.head_revision is not None:
            raise ValueError("only a present head may carry a revision")
        if self.head_revision is not None:
            if self.object_format not in OBJECT_FORMATS:
                raise ValueError("a revision must name the format that produced it")
            if not _valid_revision(self.head_revision, self.object_format):
                raise ValueError("that is not a resolved object id")
        # The one dishonest combination. A bounded read cannot conclude that
        # nothing changed, so it is refused here as well as in the schema.
        if (
            self.working_tree_state == WORKTREE_CLEAN
            and self.status_coverage != COVERAGE_COMPLETE
        ):
            raise ValueError("a clean tree cannot rest on an incomplete status")

    @property
    def preexisting_dirty(self) -> bool:
        """Whether the repository already had uncommitted work at the boundary.

        Load-bearing for PR5 and not an accusation: a project can be dirty for a
        hundred innocent reasons and none of them are the worker's doing. What it
        buys is the ability to say "changes since this revision did not
        necessarily start from a clean tree" instead of implying they did.
        """
        return self.working_tree_state == WORKTREE_DIRTY


def _valid_revision(text: object, object_format: Optional[str]) -> bool:
    """A resolved object id, and nothing that could be executable Git syntax.

    Lowercase hex of exactly the length the repository's object format produces.
    That refuses, by construction rather than by blocklist: ``HEAD``, ``HEAD~5``,
    ``main``, ``@{upstream}``, ``v1.0^{commit}``, a path, an abbreviation, and
    anything carrying whitespace or a control character. A revspec is a program;
    a boundary must be an identity.
    """
    if not isinstance(text, str):
        return False
    expected = OBJECT_FORMAT_LENGTHS.get(object_format or "")
    if expected is None or len(text) != expected:
        return False
    return all(c in "0123456789abcdef" for c in text)


def _run(command: Sequence[str], root: Path, runner: Optional[ProbeRunner]) -> Tuple[int, bytes]:
    """One probe. Closed command set, no shell, bounded output and time."""
    if tuple(command) not in ALLOWED_COMMANDS:  # pragma: no cover - defensive
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


def _unavailable(head_state: str, reason: str) -> GitBaseline:
    return GitBaseline(
        capture_state=CAPTURE_UNAVAILABLE,
        head_state=head_state,
        head_revision=None,
        object_format=None,
        working_tree_state=WORKTREE_UNKNOWN,
        status_coverage=COVERAGE_UNAVAILABLE,
        reason=reason,
    )


def _read_status(
    root: Path, runner: Optional[ProbeRunner]
) -> Tuple[str, str]:
    """``(working_tree_state, status_coverage)`` from one status probe."""
    code, raw = _run(GIT_STATUS, root, runner)
    if code != 0:
        return WORKTREE_UNKNOWN, COVERAGE_UNAVAILABLE
    # `-z` frames records with NUL. Trailing empty field after the final NUL is
    # normal and is not a record.
    records = [chunk for chunk in raw.split(b"\0") if chunk]
    if not records:
        return WORKTREE_CLEAN, COVERAGE_COMPLETE
    # A rename record is followed by its source as a second NUL-framed field, so
    # the record count is an upper bound on changed paths rather than an exact
    # one. That is fine here: PR4 stores *whether* the tree was dirty, not what
    # was in it, and the exact enumeration is PR3's job on the observation path.
    if len(records) > MAX_STATUS_RECORDS or len(raw) >= MAX_PROBE_OUTPUT:
        # Dirty is still certain — a record exists. The extent is not.
        return WORKTREE_DIRTY, COVERAGE_INCOMPLETE
    return WORKTREE_DIRTY, COVERAGE_COMPLETE


def capture_baseline(
    root: object,
    *,
    runner: Optional[ProbeRunner] = None,
    attempts: int = MAX_CAPTURE_ATTEMPTS,
) -> GitBaseline:
    """Read the pre-work boundary for one turn. Never raises.

    Never raising is the contract, and it is what lets the caller put this
    immediately before dispatch without wrapping it: ordinary project work must
    not fail because Git evidence was unavailable. A repository that cannot be
    read produces a baseline that says so, durably, and the turn proceeds with
    its coverage explicitly unavailable.

    ``runner`` exists for tests that need to script Git's answers — a HEAD that
    moves between reads is not something a test can arrange reliably against a
    real repository. It is keyword-only and defaults to the real subprocess;
    nothing in the service passes it.
    """
    if not isinstance(root, Path):
        return _unavailable(HEAD_UNAVAILABLE, REASON_ROOT_UNAVAILABLE)

    try:
        return _capture(root, runner, max(1, int(attempts)))
    except subprocess.TimeoutExpired:
        return _unavailable(HEAD_UNAVAILABLE, REASON_PROBE_TIMEOUT)
    except (OSError, ValueError, subprocess.SubprocessError):
        # Includes the repository being deleted underneath the probe: the cwd
        # vanishes and the spawn fails. A bounded code, not the exception text.
        return _unavailable(HEAD_UNAVAILABLE, REASON_PROBE_FAILED)


def _capture(
    root: Path, runner: Optional[ProbeRunner], attempts: int
) -> GitBaseline:
    code, raw = _run(GIT_IS_REPO, root, runner)
    if code != 0 or _text(raw) != "true":
        return _unavailable(HEAD_NOT_A_REPOSITORY, REASON_NOT_A_REPOSITORY)

    code, raw = _run(GIT_OBJECT_FORMAT, root, runner)
    object_format = _text(raw) if code == 0 else ""
    if object_format not in OBJECT_FORMATS:
        # An object format this build does not know how to validate. Refusing is
        # right: storing an id of unknown shape would defeat the point of
        # validating it at all.
        return _unavailable(HEAD_UNAVAILABLE, REASON_MALFORMED_REVISION)

    for _ in range(attempts):
        code, raw = _run(GIT_HEAD, root, runner)
        if code != 0:
            # A repository with no commit yet. Status is still worth reading —
            # a fresh repo full of untracked files is dirty, and PR5 should know
            # that the boundary began that way.
            tree, coverage = _read_status(root, runner)
            return GitBaseline(
                capture_state=CAPTURE_CAPTURED,
                head_state=HEAD_UNBORN,
                head_revision=None,
                object_format=None,
                working_tree_state=tree,
                status_coverage=coverage,
                reason=REASON_UNBORN_HEAD,
            )
        first = _text(raw)
        if not _valid_revision(first, object_format):
            return _unavailable(HEAD_UNAVAILABLE, REASON_MALFORMED_REVISION)

        tree, coverage = _read_status(root, runner)

        code, raw = _run(GIT_HEAD, root, runner)
        second = _text(raw) if code == 0 else None
        if second == first:
            return GitBaseline(
                capture_state=CAPTURE_CAPTURED,
                head_state=HEAD_PRESENT,
                head_revision=first,
                object_format=object_format,
                working_tree_state=tree,
                status_coverage=coverage,
                reason=None,
            )
        # HEAD moved across the observation. Neither read describes the moment,
        # and picking one would be inventing a boundary. Try again.

    return _unavailable(HEAD_UNAVAILABLE, REASON_HEAD_UNSTABLE)


__all__ = [
    "ALLOWED_COMMANDS",
    "CAPTURE_CAPTURED",
    "CAPTURE_STATES",
    "CAPTURE_UNAVAILABLE",
    "DISPATCH_CAPTURED",
    "DISPATCH_REFUSED",
    "DISPATCH_STARTED",
    "DISPATCH_STATES",
    "DISPATCH_TURN_OPENED",
    "REPLACEABLE_DISPATCH_STATES",
    "COVERAGE_COMPLETE",
    "COVERAGE_INCOMPLETE",
    "COVERAGE_UNAVAILABLE",
    "GIT_HEAD",
    "GIT_IS_REPO",
    "GIT_OBJECT_FORMAT",
    "GIT_STATUS",
    "GitBaseline",
    "HEAD_NOT_A_REPOSITORY",
    "HEAD_PRESENT",
    "HEAD_STATES",
    "HEAD_UNAVAILABLE",
    "HEAD_UNBORN",
    "MAX_CAPTURE_ATTEMPTS",
    "MAX_REASON_CHARS",
    "MAX_REVISION_CHARS",
    "MAX_STATUS_RECORDS",
    "OBJECT_FORMATS",
    "OBJECT_FORMAT_LENGTHS",
    "PROBE_ENVIRONMENT",
    "PROBE_TIMEOUT_SECONDS",
    "REASONS",
    "REASON_HEAD_UNSTABLE",
    "REASON_MALFORMED_REVISION",
    "REASON_NOT_A_REPOSITORY",
    "REASON_PROBE_FAILED",
    "REASON_PROBE_TIMEOUT",
    "REASON_ROOT_UNAVAILABLE",
    "REASON_UNBORN_HEAD",
    "STATUS_COVERAGES",
    "WORKTREE_CLEAN",
    "WORKTREE_DIRTY",
    "WORKTREE_STATES",
    "WORKTREE_UNKNOWN",
    "capture_baseline",
]
