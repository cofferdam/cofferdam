"""The workspace: a stable name for "what are we working on", bound to a project.

A workspace is the smallest thing that could carry an objective. It is a stable
id, a label a person recognises, and the id of a project that already exists —
and in this PR it is deliberately nothing else.

Why so little
-------------

The recorded M2J scope is larger: document-role mappings for the mind, an
Auto/Safe/Review profile, a code-owned model allowlist. None of those can be
*honest* yet. A ``plan_doc`` field would name a file nothing reads until PR3. A
``profile`` field would name an evaluation depth that has no evaluator until
M2K. A model allowlist would select for a planner that does not exist until M2L.
Each would be a field that validates, persists, appears in an API response, and
means nothing — and a field that means nothing is one somebody will later build
on before it means something.

So the rule this file follows is the one the replan wrote down: add a field when
the thing that reads it exists. The schema is additive and a later PR widens it
without migrating anything, because a workspace is a JSON object in a host-owned
file.

What a workspace may never carry
--------------------------------

:data:`FORBIDDEN_WORKSPACE_FIELDS` is the same defence ``task-projects.json``
uses, aimed at this file's own specific temptations:

* ``root``, ``path``, ``directory`` — a workspace reaches a directory only
  through its project, where root validation, the symlink walk and the
  re-verification before every task already live. A second path field would be a
  second place to get that wrong.
* ``adapters``, ``delegated_adapter``, ``model``, ``provider`` — **the important
  one.** PR #34 made "which agent runs here" an explicit host decision on the
  *project*, after discovering that list ordering had quietly been the authority.
  A workspace that could name an adapter would recreate that problem one level
  up, and it would be worse: the workspace is the thing a client switches.
* ``argv``, ``cmd``, ``command``, ``env``, ``exec``, ``script``, ``shell`` — the
  execution words, refused here for the reason they are refused everywhere.
* ``secret``, ``secrets``, ``token`` — configuration is not a credential store.

Refused **by name**, so somebody who writes one gets a sentence telling them
where that decision actually lives, rather than a key that is silently dropped.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..tasks.models import MAX_LABEL_CHARS
from ..tasks.projects import valid_project_id

#: The host-owned document, beside ``task-projects.json`` and the M2A registries
#: because it is the same kind of thing: machine configuration with stable ids,
#: edited in a text editor on the workstation and never through the API.
WORKSPACES_FILENAME = "workspaces.json"

#: Same id grammar as projects and the M2A registries, so an id is safe in a
#: path segment, a filename, a log line and a JSON key without escaping.
MAX_WORKSPACE_ID_CHARS = 64
_ID_ALLOWED = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_")

MAX_WORKSPACES = 64

#: Bounded like the project notes field, and for the same reason: it is rendered.
MAX_NOTES_CHARS = 200

#: Fields that must never appear in a workspace entry. See the module docstring
#: for why each one is here — every entry is a decision that already has a home,
#: and naming them makes the refusal a sentence instead of a shrug.
FORBIDDEN_WORKSPACE_FIELDS = frozenset(
    {
        "adapter",
        "adapters",
        "argv",
        "cmd",
        "command",
        "delegated_adapter",
        "directory",
        "env",
        "environment",
        "exec",
        "executable",
        "model",
        "models",
        "path",
        "prompt",
        "provider",
        "root",
        "script",
        "secret",
        "secrets",
        "shell",
        "token",
    }
)

#: Where a forbidden field's decision actually lives, so the refusal can say so.
#: Anything not named here gets the general sentence.
_FORBIDDEN_ADVICE = {
    "adapter": "which adapter runs is the project's delegated_adapter, not the workspace's",
    "adapters": "which adapters are permitted is the project's adapters list",
    "delegated_adapter": "which adapter runs is the project's delegated_adapter",
    "directory": "a workspace reaches a directory only through the project it names",
    "model": "a model allowlist is code-owned and arrives with the planner, not here",
    "models": "a model allowlist is code-owned and arrives with the planner, not here",
    "path": "a workspace reaches a directory only through the project it names",
    "provider": "a workspace never selects a provider",
    "root": "a workspace reaches a directory only through the project it names",
}


def valid_workspace_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_WORKSPACE_ID_CHARS
        and all(character in _ID_ALLOWED for character in value)
    )


def _bounded_label(value: Any, limit: int = MAX_LABEL_CHARS) -> Optional[str]:
    """A single-line label with control characters removed, or nothing.

    The same normalization the project loader applies, so a workspace name and a
    project name cannot differ in what they accept.
    """
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


@dataclass(frozen=True)
class Workspace:
    """One configured answer to "what are we working on", as written on the host."""

    workspace_id: str
    display_name: str
    project_id: str
    enabled: bool = True
    notes: Optional[str] = None

    def to_dict(self, *, project_available: Optional[bool] = None) -> Dict[str, Any]:
        """The client-facing shape. Names and ids only.

        ``project_available`` is resolved by the service against the live project
        registry rather than stored here, because whether a project exists is a
        fact about *another* file that can change between one request and the
        next. ``None`` means nobody asked — the raw configuration view.
        """
        payload: Dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "display_name": self.display_name,
            "project_id": self.project_id,
            "enabled": self.enabled,
            "notes": self.notes,
        }
        if project_available is not None:
            payload["project_available"] = project_available
        return payload


@dataclass(frozen=True)
class WorkspaceRegistry:
    """The loaded workspaces, plus what was rejected and why.

    Rejections are kept rather than discarded, exactly as ``ProjectRegistry``
    keeps them: "two workspaces are configured and one of them is broken" is the
    sentence somebody needs in order to fix it. A problem carries the workspace
    id and a reason and never a path.
    """

    workspaces: Tuple[Workspace, ...] = ()
    problems: Tuple[Dict[str, str], ...] = ()
    source_present: bool = False

    def get(self, workspace_id: object) -> Workspace:
        """Resolve an id to a workspace, or refuse. Never returns a default.

        Import-local so that :mod:`.errors` does not have to be imported by
        every consumer of the plain data model.
        """
        from .errors import WorkspaceDisabled, WorkspaceUnknown

        if not valid_workspace_id(workspace_id):
            raise WorkspaceUnknown()
        for workspace in self.workspaces:
            if workspace.workspace_id == workspace_id:
                if not workspace.enabled:
                    raise WorkspaceDisabled()
                return workspace
        raise WorkspaceUnknown()

    def find(self, workspace_id: object) -> Optional[Workspace]:
        """The same lookup without the refusal, including disabled entries.

        Used where a *stored* id has to be rendered truthfully — the active
        workspace may have been disabled or removed since it was chosen, and the
        honest answer is to say so rather than to raise on a read.
        """
        if not valid_workspace_id(workspace_id):
            return None
        for workspace in self.workspaces:
            if workspace.workspace_id == workspace_id:
                return workspace
        return None

    def enabled_workspaces(self) -> Tuple[Workspace, ...]:
        return tuple(workspace for workspace in self.workspaces if workspace.enabled)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspaces": [w.to_dict() for w in self.enabled_workspaces()],
            "configured": len(self.workspaces),
            "problems": [dict(problem) for problem in self.problems],
            "source_present": self.source_present,
        }


def _read_workspace(entry: Any) -> Tuple[Optional[Workspace], Optional[Dict[str, str]]]:
    if not isinstance(entry, dict):
        return None, {"workspace_id": "", "problem": "entry is not an object"}

    workspace_id = entry.get("workspace_id")
    label = workspace_id if isinstance(workspace_id, str) else ""
    if not valid_workspace_id(workspace_id):
        return None, {
            "workspace_id": label[:MAX_WORKSPACE_ID_CHARS],
            "problem": "invalid workspace_id",
        }

    forbidden = sorted(set(entry) & FORBIDDEN_WORKSPACE_FIELDS)
    if forbidden:
        advice = _FORBIDDEN_ADVICE.get(
            forbidden[0], "workspaces name a project, never what runs in it"
        )
        return None, {
            "workspace_id": workspace_id,
            "problem": "remove " + forbidden[0] + ": " + advice,
        }

    project_id = entry.get("project_id")
    if not valid_project_id(project_id):
        # Shape only. Whether that project *exists* is a question about another
        # file, asked at use rather than at load — a workspace written before
        # its project is added should stay in the file and report the missing
        # project, not vanish from the list.
        return None, {
            "workspace_id": workspace_id,
            "problem": "project_id must be a plain lowercase project name",
        }

    display_name = _bounded_label(entry.get("display_name")) or workspace_id

    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        # A rejection rather than a coerced default, for the reason the project
        # loader gives: "true", 1 and "yes" all read as consent to a person, and
        # guessing either way hides a mistake about whether something is on.
        return None, {
            "workspace_id": workspace_id,
            "problem": "enabled must be true or false",
        }

    notes = entry.get("notes")
    if notes is not None and _bounded_label(notes, MAX_NOTES_CHARS) is None:
        return None, {
            "workspace_id": workspace_id,
            "problem": "notes must be a short single-line string",
        }

    return (
        Workspace(
            workspace_id=workspace_id,
            display_name=display_name,
            project_id=project_id,
            enabled=enabled,
            notes=_bounded_label(notes, MAX_NOTES_CHARS) if notes is not None else None,
        ),
        None,
    )


def load_workspaces(config) -> WorkspaceRegistry:
    """Read and validate the host's workspace configuration.

    A missing file is not an error and is the shipped default. A workstation
    with no workspaces configured has no workspaces, every existing flow keeps
    working, and the API says so rather than inventing one — which is the whole
    backward-compatibility posture of this PR in one branch.
    """
    path = Path(config.config_dir) / WORKSPACES_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return WorkspaceRegistry(source_present=False)
    except OSError:
        return WorkspaceRegistry(
            problems=({"workspace_id": "", "problem": "the workspace file cannot be read"},),
            source_present=True,
        )

    try:
        document = json.loads(raw)
    except ValueError:
        return WorkspaceRegistry(
            problems=({"workspace_id": "", "problem": "the workspace file is not valid JSON"},),
            source_present=True,
        )

    entries = document.get("workspaces") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return WorkspaceRegistry(
            problems=({"workspace_id": "", "problem": "the workspace file has no workspaces list"},),
            source_present=True,
        )

    workspaces: List[Workspace] = []
    problems: List[Dict[str, str]] = []
    seen = set()
    for entry in entries[:MAX_WORKSPACES]:
        workspace, problem = _read_workspace(entry)
        if problem is not None:
            problems.append(problem)
            continue
        assert workspace is not None
        if workspace.workspace_id in seen:
            # The duplicate is dropped and *both* are not: the first one wins,
            # which is the only choice that does not make load order authority
            # over which of two identically-named workspaces you get.
            problems.append(
                {"workspace_id": workspace.workspace_id, "problem": "duplicate workspace_id"}
            )
            continue
        seen.add(workspace.workspace_id)
        workspaces.append(workspace)

    return WorkspaceRegistry(
        workspaces=tuple(workspaces), problems=tuple(problems), source_present=True
    )


__all__ = [
    "FORBIDDEN_WORKSPACE_FIELDS",
    "MAX_NOTES_CHARS",
    "MAX_WORKSPACES",
    "MAX_WORKSPACE_ID_CHARS",
    "WORKSPACES_FILENAME",
    "Workspace",
    "WorkspaceRegistry",
    "load_workspaces",
    "valid_workspace_id",
]
