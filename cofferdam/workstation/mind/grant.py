"""The global vault grant: the product's second filesystem grant.

The first is ``task-projects.json``, which decides where a task may run. This one
decides where the user's own cross-project memory lives, and it gets the same
treatment for the same reason — an absolute literal path, no ``~``, no ``$``, no
``..``, a symlink walk over every component at use, and a re-verification
immediately before anything is read or written.

**Absent by default, and absence is the whole design.** There is no built-in
vault location. Cofferdam does not scan ``~``, ``~/Documents``, or an existing
Obsidian vault; it does not offer a directory it found; it does not create one.
A workstation with no ``config/mind-grant.json`` has no global mind at all — and
that is an *absence* rather than a refusal, so there is nothing for a later
change to relax. :class:`MindGrant.vault` is ``None`` and the service has no
root to resolve against.

``enabled: true`` is required, and writing the file is not enough
-----------------------------------------------------------------

This is deliberately **stricter than the project and workspace convention**,
where ``enabled`` defaults to ``true`` and omitting it means on. Those files
describe places work happens on this machine. This one describes the user's
personal, cross-project memory, and it is the only configuration on the host
that can make it readable at all — so the convenient default is the wrong
default, and the operator decision recorded as D-2026-08-12-2 makes the
activating act explicit rather than incidental.

Five states, and only one of them grants anything:

======================================  ==========================
file absent                             no vault
present, ``enabled`` omitted            no vault (reported)
``enabled: false``                      no vault (reported)
``enabled`` not a boolean               no vault (reported)
``enabled: true`` and otherwise valid   **the vault**
======================================  ==========================

The middle three are *reported* rather than silent: each is somebody having
written the file and not got what they expected, and the difference between "I
never granted one" and "I granted one and it is off" is what tells them which
line to fix. The type check is an ``isinstance`` against ``bool`` rather than a
truthiness test, because ``1`` and ``"true"`` are exactly the values a person
would write meaning yes and exactly the ones that must not be read as consent.

Its own file, not a field somewhere else
----------------------------------------

Not in ``workspaces.json``: the vault is global, and a workspace refuses ``root``
and ``path`` by name precisely so that path authority cannot move there. Not in
``task-projects.json``: those entries are places a *task* may run, and a vault is
not one. A separate file also makes revocation a deletion — remove it, restart,
and the daemon has no vault — which is the same revocability argument
``secrets/actions-bridge-internal-token`` is shaped by.

There is no route that writes this file, for the reason there is no route that
writes a project: a grant that can be written over the network is a grant that
can be taken over the network.

The vault stays Obsidian-*compatible* and Obsidian-*independent*
----------------------------------------------------------------

Plain CommonMark files in an ordinary directory. Cofferdam never invokes
Obsidian, never reads ``.obsidian/``, and never writes its configuration — see
D-2026-08-11-3. Nothing in this file looks for one, which is why a directory
containing ``.obsidian/`` is not detected as anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .roles import GLOBAL_ROLES, valid_document_name

#: The host-owned document, beside ``task-projects.json`` and ``workspaces.json``
#: because it is the same kind of thing: machine configuration with a stable
#: shape, edited in a text editor on the workstation.
GRANT_FILENAME = "mind-grant.json"

#: Fields that must never appear inside the grant. The same list-by-name defence
#: the project and workspace loaders use, aimed at this file's own temptation:
#: a grant is the most powerful line of configuration on the host, and it must
#: stay a *location* and nothing else.
FORBIDDEN_GRANT_FIELDS = frozenset(
    {
        "adapter",
        "adapters",
        "argv",
        "cmd",
        "command",
        "delegated_adapter",
        "env",
        "environment",
        "exec",
        "executable",
        "model",
        "models",
        "prompt",
        "provider",
        "script",
        "secret",
        "secrets",
        "shell",
        "token",
    }
)

MAX_VAULT_DOCUMENTS = len(GLOBAL_ROLES)

#: The key that actually activates the grant. Named as a constant because it is
#: the single most consequential word in the host's configuration and it is
#: asserted by name in the tests.
ENABLED_FIELD = "enabled"


class DuplicateKey(ValueError):
    """A JSON object named the same key twice.

    Worth its own exception because the default behaviour is silent: ``json``
    keeps the last occurrence, which would make *file order* the authority over
    which document a role resolves to. Two mappings for one role is exactly the
    ambiguity that must fail closed rather than be settled by position.
    """


def reject_duplicate_keys(pairs):
    """``object_pairs_hook`` that refuses a repeated key."""
    seen: Dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise DuplicateKey(key)
        seen[key] = value
    return seen


@dataclass(frozen=True)
class GlobalVaultGrant:
    """One granted vault: where it is, whether it is on, and what it maps."""

    root: Path
    enabled: bool = True
    documents: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """The client-facing shape. **The root is not in it.**

        Same rule as :meth:`~..tasks.projects.TaskProject.to_dict`: a phone
        learns that a vault has been granted and which roles it carries, never
        where on disk it lives.
        """
        return {
            "granted": True,
            "roles": sorted(self.documents),
        }


@dataclass(frozen=True)
class MindGrant:
    """The loaded grant, plus what was rejected and why.

    Problems are kept rather than discarded, exactly as the project and workspace
    registries keep theirs: "a grant is configured and it is broken" is the
    sentence somebody needs in order to fix it, and it is very different from
    "no grant is configured". Neither a problem nor any other published value
    ever carries the configured path.
    """

    vault: Optional[GlobalVaultGrant] = None
    problems: Tuple[Dict[str, str], ...] = ()
    source_present: bool = False

    def to_dict(self) -> Dict[str, Any]:
        if self.vault is None:
            return {
                "granted": False,
                "roles": [],
                "source_present": self.source_present,
                "problems": [dict(problem) for problem in self.problems],
            }
        payload = self.vault.to_dict()
        payload["source_present"] = self.source_present
        payload["problems"] = [dict(problem) for problem in self.problems]
        return payload


def _read_root(value: Any) -> Optional[Path]:
    """A configured vault root, checked lexically. Nothing is touched on disk.

    Copied from :func:`~..tasks.projects._read_root` rather than shared, because
    the two are the same rule about different authorities and a shared helper
    would make widening one widen the other. Absolute and literal: expanding
    ``~`` or ``$VAR`` would make the granted location depend on the environment
    the daemon happens to have, which is how a grant ends up pointing somewhere
    nobody chose.
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


def _read_documents(value: Any) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """The role → filename map for the vault, or a reason it was refused."""
    if value is None:
        return {}, None
    if not isinstance(value, dict):
        return None, "documents must be an object mapping roles to file names"
    if len(value) > MAX_VAULT_DOCUMENTS:
        return None, "more document roles than this version has"
    mapping: Dict[str, str] = {}
    for role, name in value.items():
        if role not in GLOBAL_ROLES:
            # Named in the reason, but the *role* is safe to name — it is a
            # code-owned word, not a path. A project role here is refused for
            # the same reason it is refused in a workspace: the two minds are
            # different authorities and one file may not map the other's roles.
            return None, "'" + str(role)[:64] + "' is not a global memory role"
        cleaned = valid_document_name(name)
        if cleaned is None:
            return None, "the file name for role '" + role + "' is not a plain relative name"
        mapping[role] = cleaned
    return mapping, None


def load_mind_grant(config) -> MindGrant:
    """Read and validate the host's vault grant.

    A missing file is not an error and is the shipped default: a workstation
    with no grant has no global mind, every existing flow keeps working, and the
    API says so rather than inventing a location.
    """
    path = Path(config.config_dir) / GRANT_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return MindGrant(source_present=False)
    except OSError:
        return MindGrant(
            problems=({"problem": "the mind grant file cannot be read"},),
            source_present=True,
        )

    try:
        document = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except DuplicateKey as duplicate:
        return MindGrant(
            problems=(
                {
                    "problem": "the mind grant file names '"
                    + str(duplicate.args[0])[:64]
                    + "' twice"
                },
            ),
            source_present=True,
        )
    except ValueError:
        return MindGrant(
            problems=({"problem": "the mind grant file is not valid JSON"},),
            source_present=True,
        )

    entry = document.get("global_vault") if isinstance(document, dict) else None
    if entry is None:
        return MindGrant(
            problems=({"problem": "the mind grant file has no global_vault entry"},),
            source_present=True,
        )
    if not isinstance(entry, dict):
        return MindGrant(
            problems=({"problem": "global_vault must be an object"},),
            source_present=True,
        )

    forbidden = sorted(set(entry) & FORBIDDEN_GRANT_FIELDS)
    if forbidden:
        return MindGrant(
            problems=(
                {
                    "problem": "a grant says where memory lives, never what runs: remove "
                    + forbidden[0]
                },
            ),
            source_present=True,
        )

    root = _read_root(entry.get("root"))
    if root is None:
        # The refused value is deliberately not echoed. It is a filesystem path
        # somebody wrote, and a problem list is rendered on a phone.
        return MindGrant(
            problems=({"problem": "root must be a plain absolute path, with no ~, $ or .."},),
            source_present=True,
        )

    # `enabled` is REQUIRED and must be literally `true`. See the module
    # docstring: writing the file is not the grant, `enabled: true` is.
    if ENABLED_FIELD not in entry:
        return MindGrant(
            problems=(
                {
                    "problem": "the grant is inactive: add \"enabled\": true to turn"
                    " global memory access on"
                },
            ),
            source_present=True,
        )

    enabled = entry.get(ENABLED_FIELD)
    if not isinstance(enabled, bool):
        # A rejection rather than a coerced default, for the reason the project
        # loader gives and more so here: "true", 1 and "yes" all read as consent
        # to a person, and guessing either way hides a mistake about whether the
        # user's personal memory is reachable.
        #
        # `True` and `False` are `bool`, and `1`/`0` are not — `isinstance(1,
        # bool)` is False in Python, which is the type confusion this branch has
        # to catch rather than rely on truthiness for.
        return MindGrant(
            problems=({"problem": "enabled must be true or false"},),
            source_present=True,
        )

    documents, problem = _read_documents(entry.get("documents"))
    if documents is None:
        return MindGrant(problems=({"problem": problem or "invalid documents"},), source_present=True)

    if enabled is not True:
        # Configured and switched off. Distinct from absent in the problems list
        # so an operator can tell "I never granted one" from "I turned it off",
        # and identical to absent in effect: there is no vault.
        return MindGrant(
            problems=({"problem": "the global vault grant is turned off"},),
            source_present=True,
        )

    return MindGrant(
        vault=GlobalVaultGrant(root=root, enabled=True, documents=documents),
        source_present=True,
    )


__all__ = [
    "ENABLED_FIELD",
    "FORBIDDEN_GRANT_FIELDS",
    "GRANT_FILENAME",
    "DuplicateKey",
    "GlobalVaultGrant",
    "MindGrant",
    "load_mind_grant",
    "reject_duplicate_keys",
]
