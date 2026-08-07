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
``git status --porcelain`` — the changed paths, relative to the root.

That is the list. Each is a constant tuple in this file, run with ``shell=False``
and a fixed ``cwd``, with output bounded and parsed conservatively. A path that
does not stay inside the root after resolution is dropped rather than reported,
because a path escaping the project is not evidence about the project.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ....runtime.identity import now_iso
from ...models import (
    EVIDENCE_ARTIFACT,
    EVIDENCE_COMMIT,
    EVIDENCE_FILE,
    EVIDENCE_GIT_OBSERVED,
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
GIT_STATUS: Tuple[str, ...] = ("git", "status", "--porcelain")

ALLOWED_PROBES: Tuple[Tuple[str, ...], ...] = (
    GIT_IS_REPO,
    GIT_BRANCH,
    GIT_HEAD,
    GIT_STATUS,
)

PROBE_TIMEOUT_SECONDS = 15.0
MAX_PROBE_OUTPUT = 64 * 1024
MAX_REPORTED_PATHS = 20

#: The environment a probe runs in. Minimal and fixed: Git needs almost nothing,
#: and a probe that inherited the daemon's environment would be one more place a
#: variable could leak into a subprocess.
PROBE_ENVIRONMENT: Dict[str, str] = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LC_ALL": "C",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
}


@dataclass
class GitObservation:
    """What Cofferdam saw for itself, or why it could not see anything."""

    is_repository: bool = False
    branch: Optional[str] = None
    head: Optional[str] = None
    changed_paths: Tuple[str, ...] = ()
    clean: Optional[bool] = None
    truncated: bool = False
    problem: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "is_repository": self.is_repository,
            "branch": self.branch,
            "head": self.head,
            "changed_paths": list(self.changed_paths),
            "clean": self.clean,
            "truncated": self.truncated,
            "problem": self.problem,
        }


def _run(command: Sequence[str], root: Path, *, runner=None) -> Tuple[int, str]:
    """Run one allowlisted probe. Refuses anything not in the closed set.

    The membership check is not decoration. It is what makes "a caller cannot
    run an arbitrary command" true even for a caller inside this package, so a
    future edit that builds a command out of a variable fails immediately rather
    than working quietly.
    """
    if tuple(command) not in ALLOWED_PROBES:
        raise ValueError("evidence probes are a fixed set")
    if runner is not None:
        return runner(tuple(command), root)
    completed = subprocess.run(  # noqa: S603 - closed command set, shell=False
        list(command),
        shell=False,
        cwd=str(root),
        env=dict(PROBE_ENVIRONMENT),
        capture_output=True,
        timeout=PROBE_TIMEOUT_SECONDS,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.returncode, (completed.stdout or "")[:MAX_PROBE_OUTPUT]


def _safe_relative(raw: str, root: Path) -> Optional[str]:
    """A path from ``git status``, kept only if it stays inside the root.

    Git reports paths relative to the repository top level, which is normally
    the root — but a quoted path, a rename arrow, or a submodule can produce
    something else, and "normally" is not the standard for a directory boundary.
    """
    text = raw.strip()
    if not text:
        return None
    # Rename/copy entries read "old -> new". The new path is the one that exists.
    if " -> " in text:
        text = text.split(" -> ", 1)[1].strip()
    # Git quotes paths containing unusual bytes. Rather than decode the quoting
    # rules, such a path is reported by shape only.
    if text.startswith('"'):
        return None
    if text.startswith("/") or ".." in Path(text).parts:
        return None
    candidate = (root / text).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return text[:200]


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
        code, out = _run(GIT_STATUS, root, runner=runner)
    except (OSError, ValueError, subprocess.SubprocessError):
        return observation
    if code != 0:
        return observation
    lines = out.splitlines()
    paths: List[str] = []
    for line in lines:
        # Porcelain v1: two status characters, a space, then the path.
        relative = _safe_relative(line[3:] if len(line) > 3 else "", root)
        if relative and relative not in paths:
            paths.append(relative)
        if len(paths) >= MAX_REPORTED_PATHS:
            break
    observation.changed_paths = tuple(paths)
    observation.truncated = len(lines) > len(paths)
    observation.clean = not lines
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
    for path in observation.changed_paths[:6]:
        references.append(
            EvidenceReference(
                evidence_type=EVIDENCE_FILE,
                source=EVIDENCE_GIT_OBSERVED,
                identifier=path,
                operation="git status",
                result="changed",
                observed_at=stamp,
            )
        )
    if not references and observation.clean:
        references.append(
            EvidenceReference(
                evidence_type=EVIDENCE_ARTIFACT,
                source=EVIDENCE_GIT_OBSERVED,
                identifier=None,
                operation="git status",
                result="no files changed",
                observed_at=stamp,
            )
        )
    return tuple(references)


__all__ = [
    "ALLOWED_PROBES",
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
