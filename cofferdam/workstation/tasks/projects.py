"""Where a task may run, decided on the host and never by a client.

The single most dangerous field an agent task API could have is a working
directory. A client that can name a path can name ``/``, ``~/.ssh``, or a
directory containing somebody else's data, and every later safety property would
have to be re-derived from a value that arrived over the network.

So there is no such field. A task creation request names a **project id**, and
this module is the only thing that turns one into a directory. The mapping lives
in a host-owned configuration file, exactly like the M2A registries and for the
same reason: it is *semantic machine configuration*, structured, cross-checked,
and never editable through the API.

What is verified, and when
--------------------------

Twice, deliberately.

At **load** the document is validated: strict fields, bounded strings, an
absolute path, no relative segments, no ``~``, no environment variables. A
project that fails validation is dropped with a reason rather than silently
repaired, and one bad entry never takes the others down.

At **use** the root is re-verified, immediately before a task is created. A
directory can be deleted, replaced by a symlink, or swapped for a file between
service start and Play; and the check that matters is the one closest to the
work. That re-verification walks every component looking for symlinks, then
confirms the resolved real path is still the configured path — which is what
turns "the root is a symlink into someone else's home" into a refusal rather
than a surprise.

Which adapter, and who decides
------------------------------

A project also says which delegated adapters may run in it, and — since M2I.5
PR3 — **which one actually does**. The second half is new and it replaced
something that should never have been load-bearing.

Task Core requires an ``adapter_id`` on every create and has no "pick one for
me", correctly: a default would mean a new adapter silently gaining every
existing project the day it was registered. So a caller with no adapter field —
the Actions bridge, on behalf of a model provider — had to get one from
somewhere, and what it got was the *first* entry of ``adapters``. That was
defensible only while every delegated project permitted exactly one adapter.

It was also not what it looked like. ``adapters`` is sorted at load, so "the
first one the operator wrote" was really "the first one alphabetically" — with
both Claude transports permitted, ``claude-agent-sdk`` would have beaten
``claude-code`` because of where the letters fall. A workstation should not
choose which agent runs on it that way.

:data:`DELEGATED_ADAPTER_FIELD` is the fix, and it is deliberately the smallest
one that removes ordering from the answer. One optional key naming one adapter
the project already permits. A project permitting exactly one adapter still
needs nothing — that case is unambiguous and stays implicit, so no existing
registry has to be rewritten. A project permitting several and naming none
resolves to *nothing at all*, and a create there is refused rather than guessed.

What this file will never do
----------------------------

Auto-register directories it finds. A registry that scans ``~`` and offers
everything it finds is a registry that grants access by accident, and the whole
value of this boundary is that somebody wrote each entry down on purpose.
"""

from __future__ import annotations

import json
import os
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .errors import ProjectDisabled, ProjectRootInvalid, ProjectUnknown
from .models import MAX_LABEL_CHARS

#: The host-owned document, beside the M2A registries because it is the same
#: kind of thing: machine configuration with stable ids, edited in a text editor
#: on the workstation.
PROJECTS_FILENAME = "task-projects.json"

#: Same id grammar as the M2A registries — lowercase, digits, dash — so an id is
#: safe in a path segment, a filename, a log line and a JSON key without
#: escaping, and cannot be confused for free text.
MAX_PROJECT_ID_CHARS = 64
_ID_ALLOWED = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_")

MAX_PROJECTS = 64

#: The registry key that names **which** adapter delegated work in this project
#: runs under, when the project permits more than one.
#:
#: It is a *selection among things already permitted*, never a grant. Writing it
#: cannot give a project an adapter its ``adapters`` list does not contain, and
#: cannot reach an adapter this build did not register — both of those are
#: refusals below rather than promotions.
DELEGATED_ADAPTER_FIELD = "delegated_adapter"

#: The four outcomes of asking "which adapter would a delegated task here run
#: under?". Published as plain words on the project projection so that a client
#: — the PWA, or the Actions bridge — can say *why* a project cannot take work
#: without being told anything about the host's configuration.

#: Exactly one adapter is authoritative, either named or unambiguous.
DELEGATION_OK = "ok"
#: The project permits no adapter at all. Not a misconfiguration: it is what a
#: project used only for Remote Control, or only for M2F validation, looks like.
DELEGATION_NO_ADAPTER = "no_adapter"
#: Several adapters are permitted and none was delegated. **This is the case
#: this field exists for.** Before M2I.5 PR3 the answer here was "whichever
#: sorted first", which made a *deployment detail* the authority over which
#: agent ran on somebody's workstation.
DELEGATION_AMBIGUOUS = "ambiguous_adapter"
#: An adapter was delegated and cannot be used: it is not in the project's own
#: permitted list, or this build never registered it. Fails closed rather than
#: falling back, because a fallback would mean the *named* choice was advisory.
DELEGATION_UNAVAILABLE = "delegated_adapter_unavailable"

DELEGATION_STATUSES: Tuple[str, ...] = (
    DELEGATION_OK,
    DELEGATION_NO_ADAPTER,
    DELEGATION_AMBIGUOUS,
    DELEGATION_UNAVAILABLE,
)

#: Fields that must never appear in a project entry. Present by name so that an
#: attempt to smuggle execution into *configuration* is refused loudly instead
#: of being ignored — the same list-by-name defence the M2A registries use.
FORBIDDEN_PROJECT_FIELDS = frozenset(
    {
        "argv",
        "cmd",
        "command",
        "env",
        "environment",
        "exec",
        "executable",
        "prompt",
        "script",
        "secret",
        "secrets",
        "shell",
        "token",
    }
)


def valid_project_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_PROJECT_ID_CHARS
        and all(character in _ID_ALLOWED for character in value)
    )


def valid_adapter_id(value: Any) -> bool:
    """Whether a string is *shaped* like an adapter id. Not whether one exists.

    Grammar only, and the same grammar as a project id — the shipped ids
    (``validation``, ``claude-code``, ``claude-agent-sdk``) are all of that form.
    Existence is a different question with a different answer per build, and it
    is asked by :meth:`TaskProject.delegation` against the adapters this process
    actually registered. Conflating the two here would mean a registry written
    for a build with one more adapter failed to parse rather than failing to
    resolve, and only one of those is a useful message.
    """
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_PROJECT_ID_CHARS
        and all(character in _ID_ALLOWED for character in value)
    )


@dataclass(frozen=True)
class TaskProject:
    """One place a task may run, as configured on this host."""

    project_id: str
    display_name: str
    root: Path
    enabled: bool = True
    adapters: Tuple[str, ...] = ()
    notes: Optional[str] = None

    #: Whether this project may host a **native** Claude Remote Control session
    #: (Lane A — see :mod:`..sessions`). Default off, and off for every project
    #: that predates the field: a registry entry written before Lane A existed
    #: cannot have consented to it, and "absent means enabled" is how a
    #: capability arrives somewhere nobody chose.
    #:
    #: Deliberately not folded into :attr:`adapters`. That list is Lane B — the
    #: delegated task adapters Task Core may run in this project — and a native
    #: interactive session is not one of them. Sharing the field would mean
    #: enabling a headless adapter also granted an interactive host, which is a
    #: strictly larger permission than the one being asked for.
    #:
    #: A boolean, and only a boolean. It carries no flag, path, model, effort,
    #: command or environment value; those belong to the M2J run-profile work,
    #: and a capability that could carry them would be an execution field in a
    #: configuration file — exactly what :data:`FORBIDDEN_PROJECT_FIELDS` exists
    #: to refuse.
    remote_control_enabled: bool = False

    #: Which of :attr:`adapters` a delegated task here actually runs under, when
    #: the operator has said. ``None`` means they have not, which is only an
    #: answer when there is nothing to choose between — see :meth:`delegation`.
    #:
    #: A *selection*, never a grant. A value absent from :attr:`adapters`, or
    #: naming an adapter this build did not register, resolves to nothing rather
    #: than to itself: the two lists disagreeing is a configuration mistake, and
    #: the safe reading of a mistake about which agent runs is that none does.
    #:
    #: It carries an adapter **id** and only an id. No flag, path, model, effort,
    #: tool list, permission mode or budget — those are execution values, they
    #: belong in the adapter's own source where they can be reviewed once, and a
    #: configuration file that could carry them is what
    #: :data:`FORBIDDEN_PROJECT_FIELDS` exists to refuse.
    delegated_adapter: Optional[str] = None

    def delegation(self) -> Tuple[Optional[str], str]:
        """Which adapter runs here, and the word for why. Never guesses.

        Returns ``(adapter_id, status)`` where ``adapter_id`` is ``None`` for
        every status except :data:`DELEGATION_OK`. Ordering is not consulted at
        any point: the two-or-more case without a delegation is
        :data:`DELEGATION_AMBIGUOUS`, not "the first one".

        The single-adapter case resolves without a delegation on purpose. It is
        not a fallback — there is nothing to fall back *from* — and requiring the
        key there would mean every registry written before this field existed
        stopped working the day a host upgraded.
        """
        if self.delegated_adapter is not None:
            if self.delegated_adapter in self.adapters:
                return self.delegated_adapter, DELEGATION_OK
            return None, DELEGATION_UNAVAILABLE
        if not self.adapters:
            return None, DELEGATION_NO_ADAPTER
        if len(self.adapters) == 1:
            return self.adapters[0], DELEGATION_OK
        return None, DELEGATION_AMBIGUOUS

    @property
    def delegated_adapter_id(self) -> Optional[str]:
        """The resolved adapter, or ``None``. The short form of :meth:`delegation`."""
        return self.delegation()[0]

    def permits(self, adapter_id: str) -> bool:
        """Whether this project allows that adapter.

        An empty list means *none*, not *all*. A project that has not said which
        adapters may run in it has not authorized any, and defaulting to "all"
        would mean every future adapter silently gained access to every existing
        project the day it was added.

        Distinct from :meth:`delegation`, and the pair is not redundant. This
        answers "may this adapter run here" and is the gate for a caller that
        *named* one — the PWA, which shows the choice and sends it. That answers
        "which one runs here when nobody named one", which is the only question
        the Actions bridge is allowed to ask, because a model provider naming an
        agent is the shape M2I.5 exists to prevent.
        """
        return adapter_id in self.adapters

    def to_dict(self) -> Dict[str, Any]:
        """The client-facing shape. **The root path is not in it.**

        A phone chooses a project by name; it never learns where that project
        lives on disk. Publishing the path would put the machine's directory
        layout in a response that is cached, rendered and screenshotted, and
        would give a client the vocabulary it is specifically denied.
        """
        delegated_adapter, delegation_status = self.delegation()
        return {
            "project_id": self.project_id,
            "display_name": self.display_name,
            "enabled": self.enabled,
            # Published so a client can render the Remote Control control matrix
            # truthfully — a disabled Start with a reason, rather than an enabled
            # Start that discovers the refusal by being pressed.
            #
            # A capability boolean is safe to publish where the root path is not:
            # it says *whether* this project may host a session, and nothing about
            # where that session would run. The server still re-checks it on every
            # start; this is for the shape of the button, never the authority.
            "remote_control_enabled": self.remote_control_enabled,
            "adapters": list(self.adapters),
            # The resolved answer, computed here rather than left for each
            # client to re-derive from `adapters`. A second implementation of
            # "which one runs" is a second place for ordering to creep back in,
            # and the client that matters most — the Actions bridge — is the one
            # that must never own this decision.
            #
            # `delegated_adapter` is an adapter id, which is a code-owned word
            # like `claude-code`. `delegation` is one of a closed set of four.
            # Neither is a path, a flag or a registry excerpt.
            "delegated_adapter": delegated_adapter,
            "delegation": delegation_status,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ProjectRegistry:
    """The loaded projects, plus what was rejected and why.

    Rejections are kept rather than discarded so the PWA can say "three projects
    are configured and one of them is broken", which is what someone needs to
    fix it. They carry the project id and a reason — never the path, which stays
    on the host.
    """

    projects: Tuple[TaskProject, ...] = ()
    problems: Tuple[Dict[str, str], ...] = ()
    source_present: bool = False

    def get(self, project_id: object) -> TaskProject:
        """Resolve an id to a project, or refuse. Never returns a default."""
        if not valid_project_id(project_id):
            raise ProjectUnknown()
        for project in self.projects:
            if project.project_id == project_id:
                if not project.enabled:
                    raise ProjectDisabled()
                return project
        raise ProjectUnknown()

    def enabled_projects(self) -> Tuple[TaskProject, ...]:
        return tuple(project for project in self.projects if project.enabled)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "projects": [project.to_dict() for project in self.enabled_projects()],
            "configured": len(self.projects),
            "problems": [dict(problem) for problem in self.problems],
            "source_present": self.source_present,
        }


def _bounded_label(value: Any, limit: int = MAX_LABEL_CHARS) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(
        "".join(
            character
            for character in unicodedata.normalize("NFC", value)
            if not (
                ord(character) < 0x20
                or ord(character) == 0x7F
                or 0x80 <= ord(character) <= 0x9F
            )
        ).split()
    )
    if not cleaned or len(cleaned) > limit:
        return None
    return cleaned


def _read_root(value: Any) -> Optional[Path]:
    """A configured root, checked lexically. Nothing is touched on disk here.

    Absolute and literal: no ``~``, no ``$VAR``, no ``..``. Expanding either of
    the first two would make the configured value depend on the environment the
    daemon happens to have, which is how a root ends up somewhere nobody chose.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if "~" in text or "$" in text:
        return None
    path = Path(text)
    if not path.is_absolute():
        return None
    if any(part in ("..", ".") for part in path.parts):
        return None
    return path


def verify_root(root: Path) -> Path:
    """Confirm a configured root is a real directory, right now, and return it.

    Raises :class:`~.errors.ProjectRootInvalid` for every way it can fail to be
    one. The symlink walk is the part that matters: ``realpath`` alone would
    happily follow a link out of the configured location and report success, so
    each component is checked with ``lstat`` before the resolution is trusted,
    and the resolved path must equal the configured one.
    """
    try:
        current = Path(root.anchor)
        for part in root.parts[1:]:
            current = current / part
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode):
                raise ProjectRootInvalid(
                    "the configured folder is reached through a link, which is not accepted"
                )
        resolved = Path(os.path.realpath(root))
    except ProjectRootInvalid:
        raise
    except FileNotFoundError:
        raise ProjectRootInvalid("the configured folder does not exist")
    except OSError:
        raise ProjectRootInvalid("the configured folder cannot be read")

    if resolved != root:
        # Reachable when a component is a mount trick or the path normalizes
        # differently; either way the folder in use would not be the folder that
        # was configured, and running there is not something to guess about.
        raise ProjectRootInvalid("the configured folder does not resolve to itself")
    if not resolved.is_dir():
        raise ProjectRootInvalid("the configured folder is not a directory")
    if not os.access(resolved, os.R_OK | os.X_OK):
        raise ProjectRootInvalid("the configured folder cannot be entered")
    return resolved


def _read_project(entry: Any, known_adapters: Sequence[str]) -> Tuple[Optional[TaskProject], Optional[Dict[str, str]]]:
    if not isinstance(entry, dict):
        return None, {"project_id": "", "problem": "entry is not an object"}

    project_id = entry.get("project_id")
    label = project_id if isinstance(project_id, str) else ""
    if not valid_project_id(project_id):
        return None, {"project_id": label[:MAX_PROJECT_ID_CHARS], "problem": "invalid project_id"}

    forbidden = sorted(set(entry) & FORBIDDEN_PROJECT_FIELDS)
    if forbidden:
        # Named in the reason. A configuration file that tried to carry a
        # command should produce a message saying exactly that, so whoever wrote
        # it learns the boundary rather than wondering why the project vanished.
        return None, {
            "project_id": project_id,
            "problem": "projects describe where, never what to run: remove " + forbidden[0],
        }

    display_name = _bounded_label(entry.get("display_name")) or project_id
    root = _read_root(entry.get("root"))
    if root is None:
        return None, {
            "project_id": project_id,
            "problem": "root must be a plain absolute path, with no ~, $ or ..",
        }

    raw_adapters = entry.get("adapters")
    if raw_adapters is None:
        adapters: Tuple[str, ...] = ()
    elif isinstance(raw_adapters, list) and all(isinstance(a, str) for a in raw_adapters):
        # Unknown adapter names are dropped rather than failing the project: an
        # entry naming an adapter this build does not have is a configuration
        # written for a later version, and the right behaviour is to keep the
        # project working with the adapters that do exist.
        adapters = tuple(sorted({a for a in raw_adapters if a in known_adapters}))
    else:
        return None, {"project_id": project_id, "problem": "adapters must be a list of names"}

    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        return None, {"project_id": project_id, "problem": "enabled must be true or false"}

    # Absent means off. A malformed value is a rejection rather than a coerced
    # default: ``"true"``, ``1`` and ``"yes"`` all look like consent to a person
    # reading the file, and quietly treating any of them as ``False`` would hide
    # a capability that did not take effect, while treating them as ``True``
    # would grant one from a typo.
    remote_control_enabled = entry.get("remote_control_enabled", False)
    if not isinstance(remote_control_enabled, bool):
        return None, {
            "project_id": project_id,
            "problem": "remote_control_enabled must be true or false",
        }

    # Read but deliberately **not** cross-checked against `adapters` here.
    #
    # A malformed value — a number, a list, an empty string, something that is
    # not an adapter id at all — rejects the project, exactly like a malformed
    # `enabled`: it is a sentence somebody wrote wrong, and repairing it would
    # hide the mistake.
    #
    # A well-formed value naming an adapter that is not permitted, or that this
    # build never registered, is kept. That looks lax and is the opposite: it
    # survives to `TaskProject.delegation`, which resolves it to
    # DELEGATION_UNAVAILABLE and therefore to *no adapter at all*. Dropping the
    # project instead would make it vanish from the PWA and from listProjects,
    # and "the project disappeared" is a much worse diagnostic than "the project
    # is there and says its delegated adapter is unavailable". Either way no
    # task runs, which is the property that has to hold.
    raw_delegated = entry.get(DELEGATED_ADAPTER_FIELD)
    if raw_delegated is None:
        delegated_adapter: Optional[str] = None
    elif isinstance(raw_delegated, str) and valid_adapter_id(raw_delegated):
        delegated_adapter = raw_delegated
    else:
        return None, {
            "project_id": project_id,
            "problem": DELEGATED_ADAPTER_FIELD + " must be a single adapter name",
        }

    return (
        TaskProject(
            project_id=project_id,
            display_name=display_name,
            root=root,
            enabled=enabled,
            adapters=adapters,
            notes=_bounded_label(entry.get("notes"), 200),
            remote_control_enabled=remote_control_enabled,
            delegated_adapter=delegated_adapter,
        ),
        None,
    )


def load_projects(config, known_adapters: Sequence[str] = ()) -> ProjectRegistry:
    """Read and validate the host's project configuration.

    A missing file is not an error: a workstation with no projects configured
    has no projects, and the PWA says so and offers nothing rather than showing
    a broken selector. That is also the shipped default — see the guide.
    """
    path = Path(config.config_dir) / PROJECTS_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ProjectRegistry(source_present=False)
    except OSError:
        return ProjectRegistry(
            problems=({"project_id": "", "problem": "the project file cannot be read"},),
            source_present=True,
        )

    try:
        document = json.loads(raw)
    except ValueError:
        return ProjectRegistry(
            problems=({"project_id": "", "problem": "the project file is not valid JSON"},),
            source_present=True,
        )

    entries = document.get("projects") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return ProjectRegistry(
            problems=({"project_id": "", "problem": "the project file has no projects list"},),
            source_present=True,
        )

    projects: List[TaskProject] = []
    problems: List[Dict[str, str]] = []
    seen = set()
    for entry in entries[:MAX_PROJECTS]:
        project, problem = _read_project(entry, known_adapters)
        if problem is not None:
            problems.append(problem)
            continue
        assert project is not None
        if project.project_id in seen:
            problems.append(
                {"project_id": project.project_id, "problem": "duplicate project_id"}
            )
            continue
        seen.add(project.project_id)
        projects.append(project)

    return ProjectRegistry(
        projects=tuple(projects), problems=tuple(problems), source_present=True
    )


__all__ = [
    "DELEGATED_ADAPTER_FIELD",
    "DELEGATION_AMBIGUOUS",
    "DELEGATION_NO_ADAPTER",
    "DELEGATION_OK",
    "DELEGATION_STATUSES",
    "DELEGATION_UNAVAILABLE",
    "FORBIDDEN_PROJECT_FIELDS",
    "MAX_PROJECTS",
    "MAX_PROJECT_ID_CHARS",
    "PROJECTS_FILENAME",
    "ProjectRegistry",
    "TaskProject",
    "load_projects",
    "valid_adapter_id",
    "valid_project_id",
    "verify_root",
]
