"""Looking, instead of believing.

Claude saying "I edited `sandbox.py`" is a claim. It is recorded as one —
``adapter_reported`` — and it stays a claim forever unless something actually
looked. This module is the something.

It runs a fixed, closed set of Git observations inside the approved project root
and reports what they returned. There is no function here that takes a command,
a flag, an argument list or a path from anywhere but the project registry, and
adding one would defeat the entire purpose: an "evidence" mechanism that runs
arbitrary commands is a shell with a reassuring name.

The four probes
---------------

``git rev-parse --is-inside-work-tree`` — is this a repository at all.
``git rev-parse --abbrev-ref HEAD`` — the branch.
``git rev-parse HEAD`` — the commit.
``git status --porcelain=v1 -z`` — the changed paths and **what changed about
them**, relative to the root.

That is the list. Each is a constant tuple in this file, run with ``shell=False``
and a fixed ``cwd``, with output bounded and parsed conservatively. A path that
does not stay inside the root after resolution is dropped rather than reported,
because a path escaping the project is not evidence about the project.

Why ``-z``, and what it fixed (M2K PR3)
---------------------------------------

Until PR3 this module ran plain ``git status --porcelain``, sliced past the two
status characters with ``line[3:]``, and reported a flat tuple of paths. Three
things were wrong with that, and all three were losses rather than errors:

**The operation was thrown away.** ``XY`` is Git telling us whether the file was
added, modified, deleted or renamed, and the parser stepped over it. Downstream,
every observation became the constant word ``"changed"``, which is why an
evidence bundle could only ever say ``operation_agreement: unknown``.

**Renames lost a side, and the human format hides which one.** The old parser
split on ``" -> "`` and kept the right-hand path. That is right for the *human*
output and wrong for the machine one, because the two orders are **reversed**::

    human : R  tomove.txt -> moved.txt          old first, then new
    -z    : "R  moved.txt" NUL "tomove.txt"     NEW first, then old

A parser written by reading the human output and then switched to ``-z`` inverts
every rename silently, with both paths still looking plausible. The order here is
pinned by a test that runs real Git rather than by this comment.

**Ordinary filenames vanished.** Human porcelain **quotes** any path containing a
space, a tab, an arrow or a non-ASCII byte, and the old ``_safe_relative``
refused anything starting with a quote — so a file called ``has space.txt``
produced *no evidence at all*. Under ``-z`` Git emits those paths raw, and they
survive.

``-z`` also removes the framing ambiguity entirely: records are NUL-terminated,
so a newline, a tab or a literal ``->`` inside a filename is just bytes. Nothing
in this module parses human-facing Git output any more.

What this module still cannot see
---------------------------------

``git status`` compares the **index and working tree against the current HEAD**.
It is not a before/after comparison, because Cofferdam has no "before": no
pre-task or pre-turn revision is captured anywhere — ``ClaudeRun`` records none,
and :func:`observe_git` runs once, after a result arrives. ``observation.head``
is the commit as it stands *at observation time*, recorded as a pointer for a
reader, never as a boundary.

The consequence is specific and must not be papered over: **if a worker commits
its work, ``git status`` reports a clean tree and Cofferdam observes nothing.**
That is a real coverage limit, not a bug in this parser, and it is surfaced as a
bundle-level fact rather than allowed to look like "the worker changed nothing".
Closing it needs a durable pre-work revision, which is a separate decision about
what Cofferdam records when a task *starts* — see the M2K PR3 record in
``STATUS.md``.
"""

from __future__ import annotations

import os
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ....runtime.identity import now_iso
from ...models import (
    CHANGE_CREATED,
    CHANGE_DELETED,
    CHANGE_KINDS,
    CHANGE_MODIFIED,
    CHANGE_RENAMED,
    CHANGE_UNKNOWN,
    EVIDENCE_ARTIFACT,
    EVIDENCE_COMMIT,
    EVIDENCE_FILE,
    EVIDENCE_GIT_OBSERVED,
    MAX_EVIDENCE_ITEMS,
    EvidenceReference,
)

#: Every command this module is capable of running. Constants, not templates.
#:
#: ``GIT_IS_REPO`` is first and is its own probe rather than being inferred from
#: whether the others worked, which is the mistake this file made once and a
#: test caught. ``rev-parse --abbrev-ref HEAD`` exits non-zero on a repository
#: with **no commits yet** — HEAD is unborn — so using it as the repository
#: check reported a freshly initialised project as "not a Git repository". That
#: is exactly the state a disposable validation sandbox is in before its first
#: commit, and exactly the case where Cofferdam most needs to be able to see a
#: file change.
GIT_IS_REPO: Tuple[str, ...] = ("git", "rev-parse", "--is-inside-work-tree")
GIT_BRANCH: Tuple[str, ...] = ("git", "rev-parse", "--abbrev-ref", "HEAD")
GIT_HEAD: Tuple[str, ...] = ("git", "rev-parse", "HEAD")
#: ``--porcelain=v1 -z``: Git's documented machine format. The version is pinned
#: explicitly rather than left to the ``--porcelain`` default so that a future
#: Git changing what "porcelain" means cannot change what this parser receives.
#: ``-z`` makes records NUL-terminated and paths raw — see the module docstring
#: for the three specific losses that fixed.
GIT_STATUS: Tuple[str, ...] = ("git", "status", "--porcelain=v1", "-z")

ALLOWED_PROBES: Tuple[Tuple[str, ...], ...] = (
    GIT_IS_REPO,
    GIT_BRANCH,
    GIT_HEAD,
    GIT_STATUS,
)

PROBE_TIMEOUT_SECONDS = 15.0
MAX_PROBE_OUTPUT = 64 * 1024
#: The most changed paths one observation carries, before the emitter's own
#: budget applies. A repository with more changes than this produces a truncated
#: observation, and the truncation is recorded rather than inferred.
MAX_REPORTED_PATHS = 20

#: A project-relative path, matched to the claim side's bound so an observation
#: and a claim are refused at the same length rather than at two.
MAX_OBSERVED_PATH_CHARS = 512

#: One path segment.
MAX_OBSERVED_SEGMENT_CHARS = 255

#: The environment a probe runs in. Minimal and fixed: Git needs almost nothing,
#: and a probe that inherited the daemon's environment would be one more place a
#: variable could leak into a subprocess.
PROBE_ENVIRONMENT: Dict[str, str] = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LC_ALL": "C",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
}


# -- the machine change vocabulary --------------------------------------------
#
# Imported, not defined here. The words are provider-neutral — a second adapter
# observing a different VCS would report the same set — so Task Core owns them
# in `models.py` and this module maps Git's `XY` status onto them. Defining them
# here would also break the layer boundary the other way, since Task Core's
# assembler needs them and may not import from an adapter package.

#: The XY combinations this build is willing to translate, and nothing else.
#:
#: Read as a table rather than as branches, because the alternative — scattered
#: ``if`` statements over status characters — is where a wrong-but-confident
#: answer hides. Every combination that is not in here is :data:`CHANGE_UNKNOWN`,
#: which is the direction that cannot make a false statement.
#:
#: The two columns are the index and the working tree. A single file therefore
#: often carries two letters, and what may be published is the **net effect**:
#:
#: * ``AM`` — added to the index, then edited again. It did not exist before and
#:   it does now: ``created``. The later edit does not change that.
#: * ``MM`` — modified in the index and modified again in the tree. Still
#:   ``modified``.
#: * ``RM`` — renamed, then edited. Still a ``renamed``, and the rename is the
#:   fact with two paths behind it.
#:
#: And the ones that stay ``unknown``, each for its own reason:
#:
#: * ``UU``/``AA``/``DD``/``AU``/``UA``/``DU``/``UD`` — **unmerged**. The file is
#:   mid-conflict; nobody has decided what happened to it yet, least of all this
#:   parser.
#: * ``T`` — **type change**, a regular file becoming a symlink or the reverse.
#:   Real, and none of the four words describes it.
#: * ``C`` — **copy**. It looks like a rename and is not one: the source is still
#:   there. Calling it ``renamed`` would assert a deletion that did not happen.
#: * ``MD`` — modified in the index, then deleted from the tree. Two true facts
#:   that disagree about the file's final state.
#: * ``!!`` — ignored, which is not a change at all.
_STATUS_TABLE: Dict[str, str] = {
    "??": CHANGE_CREATED,
    "A ": CHANGE_CREATED,
    " A": CHANGE_CREATED,
    "AM": CHANGE_CREATED,
    "M ": CHANGE_MODIFIED,
    " M": CHANGE_MODIFIED,
    "MM": CHANGE_MODIFIED,
    "D ": CHANGE_DELETED,
    " D": CHANGE_DELETED,
    "R ": CHANGE_RENAMED,
    " R": CHANGE_RENAMED,
    "RM": CHANGE_RENAMED,
}


def classify_status(status: object) -> str:
    """One ``XY`` status to one machine change kind. Table lookup, never a guess.

    Anything absent from the table — an unmerged state, a type change, a copy, a
    combination Git grew after this was written, or a value that is not two
    characters at all — is :data:`CHANGE_UNKNOWN`. That is the whole safety
    property: a new Git status cannot become a *wrong* operation here, only an
    unestablished one.
    """
    if not isinstance(status, str) or len(status) != 2:
        return CHANGE_UNKNOWN
    return _STATUS_TABLE.get(status, CHANGE_UNKNOWN)


@dataclass(frozen=True)
class GitChange:
    """One path Git reported, and what Git said happened to it.

    Immutable, provider-neutral, and holding only machine facts. There is no
    field for a claim, a verdict, a digest or an absolute location: ``path`` is
    project-relative because that is what Git reports and what
    :func:`_safe_relative` will accept, and it is the only kind of path that ever
    leaves this module.

    ``previous_path`` is set **only** for a rename, and it is the source — the
    path that no longer exists. ``path`` is always the destination. Keeping them
    in separate fields rather than in one ``"old -> new"`` string is the point:
    a single string would need re-parsing by every consumer, and the separator
    is a legal filename character.

    ``status`` is the raw two-character ``XY`` Git emitted. It is kept because it
    is the evidence behind ``kind`` — a reader who disagrees with the mapping can
    see what Git actually said — and it is exactly two characters, so it cannot
    become a payload.
    """

    path: str
    kind: str
    status: str
    previous_path: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "status": self.status,
            "previous_path": self.previous_path,
        }


@dataclass
class GitObservation:
    """What Cofferdam saw for itself, or why it could not see anything.

    ``changes`` replaced a flat ``changed_paths: Tuple[str, ...]`` in M2K PR3.
    The flat tuple had nowhere to put the operation or the second half of a
    rename, which is why every observation downstream collapsed to the word
    "changed".
    """

    is_repository: bool = False
    branch: Optional[str] = None
    head: Optional[str] = None
    changes: Tuple[GitChange, ...] = ()
    clean: Optional[bool] = None
    #: Whether Git reported more changed paths than this observation carries.
    #: Now derived from a real count comparison rather than from a line-versus-
    #: path heuristic that deduplication could trip — see :func:`observe_git`.
    truncated: bool = False
    #: How many paths Git reported in total, before any cap. Kept so a reader
    #: can see the size of what was left out rather than only that something was.
    reported_count: int = 0
    #: How many records Git reported that this build refused to turn into an
    #: observation — an unusable path, a half rename, a name that is not UTF-8.
    #:
    #: Counted rather than dropped in silence. A refused record is still Git
    #: saying *something changed*, and an evidence bundle that showed no
    #: observation for it would read as "Cofferdam saw nothing there", which is a
    #: different and false statement. The path itself is deliberately **not**
    #: kept: it is the value that failed the safety gate, and storing it for
    #: reporting is the second door PR1's deny list exists to keep shut.
    refused_count: int = 0
    problem: Optional[str] = None

    @property
    def complete(self) -> bool:
        """Whether every change Git reported became an observation."""
        return not self.truncated and self.refused_count == 0

    @property
    def changed_paths(self) -> Tuple[str, ...]:
        """The destination paths, for callers that only need the path set.

        Retained so this object still answers the question the pre-PR3 field
        answered. It is derived rather than stored, so it cannot drift from
        ``changes``.
        """
        return tuple(change.path for change in self.changes)

    def to_dict(self) -> Dict[str, object]:
        return {
            "is_repository": self.is_repository,
            "branch": self.branch,
            "head": self.head,
            "changes": [change.to_dict() for change in self.changes],
            "clean": self.clean,
            "truncated": self.truncated,
            "reported_count": self.reported_count,
            "refused_count": self.refused_count,
            "complete": self.complete,
            "problem": self.problem,
        }


def _run(command: Sequence[str], root: Path, *, runner=None) -> Tuple[int, str]:
    """Run one allowlisted probe. Refuses anything not in the closed set.

    The membership check is not decoration. It is what makes "a caller cannot
    run an arbitrary command" true even for a caller inside this package, so a
    future edit that builds a command out of a variable fails immediately rather
    than working quietly.
    """
    code, raw = _run_bytes(command, root, runner=runner)
    if isinstance(raw, str):
        return code, raw
    return code, raw.decode("utf-8", errors="replace")


def _run_bytes(command: Sequence[str], root: Path, *, runner=None):
    """The same closed probe set, returning **bytes**.

    Text-mode decoding is the wrong default for one of the four probes. A
    filename is bytes on Linux, and ``errors="replace"`` turns a name that is not
    UTF-8 into a *different* name — which would make Cofferdam publish a path
    that does not exist and call it an observation. So the status probe is
    decoded field by field in :func:`parse_status_z`, strictly, and the other
    three (which return hex and a branch name) go on using the text view above.
    """
    if tuple(command) not in ALLOWED_PROBES:
        raise ValueError("evidence probes are a fixed set")
    if runner is not None:
        # A test runner may answer in either form; both are accepted so existing
        # doubles keep working.
        return runner(tuple(command), root)
    completed = subprocess.run(  # noqa: S603 - closed command set, shell=False
        list(command),
        shell=False,
        cwd=str(root),
        env=dict(PROBE_ENVIRONMENT),
        capture_output=True,
        timeout=PROBE_TIMEOUT_SECONDS,
    )
    return completed.returncode, (completed.stdout or b"")[:MAX_PROBE_OUTPUT]


def _safe_relative(raw: object, root: Optional[Path] = None) -> Optional[str]:
    """A path from Git, kept only if it is a plain project-relative name.

    **Lexical, and deliberately without a filesystem read.** The pre-PR3 version
    called ``(root / text).resolve()`` and compared the result to the root, which
    touches the filesystem to classify a *string* — and resolves symlinks, so a
    path Git legitimately reported could be refused because something under it
    happened to be a link. Classification here comes from Git's machine output;
    nothing in this module needs to open anything to decide whether a name is a
    name.

    ``root`` is accepted and ignored, so existing callers and tests keep working.

    No arrow handling: under ``-z`` a rename is two NUL-separated fields, so
    ``->`` is never a separator and a filename containing one is just a filename.
    No quote handling either: ``-z`` emits raw bytes, so the quoting rules that
    used to make ``has space.txt`` unreportable do not arise.
    """
    if not isinstance(raw, str) or not raw:
        return None
    text = raw
    if len(text) > MAX_OBSERVED_PATH_CHARS:
        # Refused rather than truncated. A shortened path names a different file,
        # and a different file is not what Git reported.
        return None
    for character in text:
        # NUL would have ended the record early for one reader and not another;
        # the rest are control and formatting characters that make one path
        # display as another.
        if character == "\x00" or unicodedata.category(character) in ("Cc", "Cf"):
            return None
    if text.startswith("/") or text.startswith("~"):
        return None
    if len(text) >= 2 and text[1] == ":":
        return None
    if "\\" in text:
        return None
    for segment in text.split("/"):
        if not segment or segment == "." or segment == "..":
            return None
        if len(segment) > MAX_OBSERVED_SEGMENT_CHARS:
            return None
    return text


def parse_status_z(payload: bytes) -> Tuple[GitChange, ...]:
    """Changes only. See :func:`parse_status_z_counted` for the refusal count."""
    changes, _ = parse_status_z_counted(payload)
    return changes


def parse_status_z_counted(payload: bytes) -> Tuple[Tuple[GitChange, ...], int]:
    """Parse ``git status --porcelain=v1 -z`` output into machine changes.

    The format, exactly
    -------------------

    Records are NUL-terminated. Each ordinary record is::

        X Y SPACE <path> NUL

    and each rename or copy record is **two** fields::

        X Y SPACE <path> NUL <other-path> NUL

    where — and this is the part that inverts the human output — the path inside
    the record is the **destination** and the following field is the **source**.
    ``test_the_z_format_puts_the_destination_first`` pins that against real Git.

    Why bytes rather than text
    --------------------------

    Decoding the whole payload first would mean choosing an error policy for the
    whole payload. With ``errors="replace"`` a filename that is not UTF-8 becomes
    a *different* filename full of U+FFFD, and Cofferdam would then publish a
    path that does not exist as though Git had reported it. Here each field is
    decoded strictly and on its own: one undecodable name is dropped, and every
    other name in the same report survives.

    Ordering is by destination path, so two observations of the same repository
    state produce the same sequence — which is what lets an evidence bundle's
    fingerprint be stable.
    """
    if not payload:
        return (), 0

    fields = payload.split(b"\x00")
    changes: Dict[str, GitChange] = {}
    refused = 0
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if len(record) < 4:
            # "XY " plus at least one character of path. Anything shorter is not
            # a record — including the trailing empty field every NUL-terminated
            # payload ends with.
            continue
        try:
            status = record[:2].decode("utf-8")
            path = record[3:].decode("utf-8")
        except UnicodeDecodeError:
            refused += 1
            continue

        kind = classify_status(status)
        previous: Optional[str] = None
        if status[0] in ("R", "C") or status[1] in ("R", "C"):
            # A rename or copy consumes the next field whatever this parser
            # decides to publish. Not consuming it would leave the source path
            # to be read as the next record's status.
            if index >= len(fields):
                # A rename header with nothing behind it is half a record.
                # Dropped entirely rather than published as a bare creation,
                # which would assert a file appeared from nowhere.
                refused += 1
                continue
            source_raw = fields[index]
            index += 1
            try:
                previous = _safe_relative(source_raw.decode("utf-8"))
            except UnicodeDecodeError:
                previous = None
            if previous is None:
                # Both sides or neither. A rename with an unusable source is not
                # a creation of the destination — that would delete the source
                # from the record and invent a fact.
                refused += 1
                continue

        safe = _safe_relative(path)
        if safe is None:
            refused += 1
            continue
        if kind == CHANGE_RENAMED and previous is None:  # pragma: no cover
            refused += 1
            continue
        if kind != CHANGE_RENAMED:
            # A copy classifies as `unknown`; it keeps no `previous_path`,
            # because the field means "the path this replaced" and a copy
            # replaced nothing.
            previous = None
        if safe not in changes:
            changes[safe] = GitChange(
                path=safe, kind=kind, status=status, previous_path=previous
            )
    return tuple(changes[key] for key in sorted(changes)), refused


def observe_git(root: Path, *, runner=None) -> GitObservation:
    """Run the three probes and report what they returned.

    A directory that is not a Git repository is not a failure: it is a project
    where Git evidence is unavailable, and saying so is more useful than an
    error.
    """
    observation = GitObservation()
    try:
        code, out = _run(GIT_IS_REPO, root, runner=runner)
    except (OSError, ValueError, subprocess.SubprocessError):
        observation.problem = "git could not be run in this project"
        return observation
    if code != 0 or out.strip() != "true":
        observation.problem = "this project is not a Git repository"
        return observation
    observation.is_repository = True

    try:
        code, out = _run(GIT_BRANCH, root, runner=runner)
        # A non-zero exit here is the unborn-HEAD case, and it is not a
        # problem: the repository is real, it simply has no commits yet.
        if code == 0:
            branch = out.strip().splitlines()[:1]
            observation.branch = branch[0][:120] if branch else None
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    try:
        code, out = _run(GIT_HEAD, root, runner=runner)
        if code == 0:
            head = out.strip().splitlines()[:1]
            # Checked for shape before it is stored: a commit id is forty hex
            # characters, and anything else did not come from `rev-parse HEAD`.
            if head and len(head[0]) == 40 and all(
                character in "0123456789abcdef" for character in head[0]
            ):
                observation.head = head[0]
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    try:
        code, out = _run_bytes(GIT_STATUS, root, runner=runner)
    except (OSError, ValueError, subprocess.SubprocessError):
        return observation
    if code != 0:
        return observation
    payload = out.encode("utf-8", errors="surrogateescape") if isinstance(out, str) else out

    parsed, refused = parse_status_z_counted(payload)
    observation.reported_count = len(parsed) + refused
    observation.refused_count = refused
    # Truncation is now a real count comparison. The pre-PR3 version compared
    # output *lines* to kept *paths*, which reported truncation whenever a path
    # was deduplicated or refused — a false positive on an honest observation,
    # and exactly the kind of "incomplete" signal that must not cry wolf.
    observation.truncated = len(parsed) > MAX_REPORTED_PATHS
    observation.changes = parsed[:MAX_REPORTED_PATHS]
    # `clean` describes what Git said, not what survived parsing: a repository
    # whose only change is at a path this parser refused is **not** clean, and
    # saying it was would turn a refusal into a statement about the project.
    observation.clean = not payload.strip(b"\x00")
    return observation


def git_evidence(observation: GitObservation) -> Tuple[EvidenceReference, ...]:
    """Turn an observation into evidence references, all ``git_observed``.

    The source is not a parameter. Everything this function produces was seen by
    Cofferdam running Git itself, and nothing an adapter said can reach it — so
    there is no path by which a claim gets promoted to an observation.
    """
    if not observation.is_repository:
        return ()
    stamp = now_iso()
    references: List[EvidenceReference] = []

    # The HEAD pointer, and the reason it is counted against the budget below
    # rather than added on top of it: `_bounded_evidence` caps the whole list at
    # MAX_EVIDENCE_ITEMS and silently drops the overflow. A budget that ignored
    # this row would push the last path observation over that edge and lose it
    # with no record — the store would not complain, and nothing downstream
    # could tell a dropped observation from an absent one.
    if observation.head:
        references.append(
            EvidenceReference(
                evidence_type=EVIDENCE_COMMIT,
                source=EVIDENCE_GIT_OBSERVED,
                identifier=observation.head[:12],
                operation="rev-parse HEAD",
                result=observation.branch,
                observed_at=stamp,
            )
        )

    reserved = len(references) + 1  # + the coverage row, always emitted below
    room = max(0, MAX_EVIDENCE_ITEMS - reserved)
    emitted = observation.changes[:room]
    for change in emitted:
        references.append(
            EvidenceReference(
                evidence_type=EVIDENCE_FILE,
                source=EVIDENCE_GIT_OBSERVED,
                identifier=change.path,
                operation="git status",
                # `result` keeps its pre-PR3 word so a reader — and any client
                # written against the older shape — still sees a familiar row.
                # The machine semantics live in the two new fields, where an
                # older reader ignores them rather than misreading them.
                result="changed",
                change_kind=change.kind,
                previous_identifier=change.previous_path,
                observed_at=stamp,
            )
        )

    # One row that says how complete this observation is, always. It is emitted
    # whether or not anything was left out, because "no truncation row" and "a
    # build that never wrote one" are indistinguishable to a later reader, and
    # the whole point is that incompleteness must not be inferable only from
    # absence.
    #
    # `identifier` stays None: this row is about the observation, not about a
    # path, and putting a count in an identifier field would be the overloading
    # `change_kind` exists to avoid.
    withheld = len(observation.changes) - len(emitted)
    complete = observation.complete and withheld == 0
    references.append(
        EvidenceReference(
            evidence_type=EVIDENCE_ARTIFACT,
            source=EVIDENCE_GIT_OBSERVED,
            identifier=None,
            operation="git status",
            result=(
                "no files changed"
                if observation.clean and not observation.changes
                else ("observed all changes" if complete else "observed some changes")
            ),
            observed_at=stamp,
        )
    )
    return tuple(references)


__all__ = [
    "ALLOWED_PROBES",
    "CHANGE_CREATED",
    "CHANGE_DELETED",
    "CHANGE_KINDS",
    "CHANGE_MODIFIED",
    "CHANGE_RENAMED",
    "CHANGE_UNKNOWN",
    "GitChange",
    "MAX_OBSERVED_PATH_CHARS",
    "MAX_OBSERVED_SEGMENT_CHARS",
    "classify_status",
    "parse_status_z",
    "parse_status_z_counted",
    "GIT_BRANCH",
    "GIT_HEAD",
    "GIT_IS_REPO",
    "GIT_STATUS",
    "MAX_REPORTED_PATHS",
    "PROBE_ENVIRONMENT",
    "GitObservation",
    "git_evidence",
    "observe_git",
]
