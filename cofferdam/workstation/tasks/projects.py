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


@dataclass(frozen=True)
class TaskProject:
    """One place a task may run, as configured on this host."""

    project_id: str
    display_name: str
    root: Path
    enabled: bool = True
    adapters: Tuple[str, ...] = ()
    notes: Optional[str] = None

    def permits(self, adapter_id: str) -> bool:
        """Whether this project allows that adapter.

        An empty list means *none*, not *all*. A project that has not said which
        adapters may run in it has not authorized any, and defaulting to "all"
        would mean every future adapter silently gained access to every existing
        project the day it was added.
        """
        return adapter_id in self.adapters

    def to_dict(self) -> Dict[str, Any]:
        """The client-facing shape. **The root path is not in it.**

        A phone chooses a project by name; it never learns where that project
        lives on disk. Publishing the path would put the machine's directory
        layout in a response that is cached, rendered and screenshotted, and
        would give a client the vocabulary it is specifically denied.
        """
        return {
            "project_id": self.project_id,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "adapters": list(self.adapters),
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

    return (
        TaskProject(
            project_id=project_id,
            display_name=display_name,
            root=root,
            enabled=enabled,
            adapters=adapters,
            notes=_bounded_label(entry.get("notes"), 200),
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
    "FORBIDDEN_PROJECT_FIELDS",
    "MAX_PROJECTS",
    "MAX_PROJECT_ID_CHARS",
    "PROJECTS_FILENAME",
    "ProjectRegistry",
    "TaskProject",
    "load_projects",
    "valid_project_id",
    "verify_root",
]
