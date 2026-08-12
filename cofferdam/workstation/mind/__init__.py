"""Cofferdam Mind: memory Cofferdam may read, and memory it may be allowed to change.

Two authorities, kept apart on purpose (D-2026-08-11-3):

* **Project mind** — the project's own repository, canonical for its own memory,
  reached through the active workspace's project and addressed by **role**
  rather than by filename. Cofferdam's own `STATUS.md`, `ROADMAP.md`,
  `DECISIONS.md` and `DESIGN.md` are role-mapped; no document is added to
  satisfy a naming convention.
* **Global mind** — a dedicated, Obsidian-compatible vault outside
  `$COFFERDAM_HOME`, readable only under an explicit host-owned grant. Absent by
  default, and absence means the vault does not exist as far as this process is
  concerned.

Markdown stays canonical (D-2026-08-08-6). The SQLite database this package owns
holds **workflow state only** — which changes have been proposed and not yet
decided. Delete it and the memory is untouched; where it and the Markdown ever
disagree, the Markdown is right.

Writing is proposal → explicit acceptance → hash-bound atomic apply
(D-2026-08-11-4). Nothing here writes a document as a side effect of anything,
acceptance lives on the device-token surface alone, and a document that changed
since it was reviewed refuses rather than being overwritten.

Obsidian-compatible, Obsidian-independent: plain CommonMark in an ordinary
directory. Cofferdam never invokes Obsidian, never reads `.obsidian/`, and never
writes its configuration.
"""

from .errors import MindError
from .grant import GlobalVaultGrant, MindGrant, load_mind_grant
from .roles import (
    GLOBAL_ROLES,
    PROJECT_ROLES,
    SCOPES,
    SCOPE_GLOBAL,
    SCOPE_PROJECT,
    valid_document_name,
    valid_role,
    valid_scope,
)
from .service import MIND_API_VERSION, MindService
from .store import MindStore, PROPOSAL_STATES

__all__ = [
    "GLOBAL_ROLES",
    "MIND_API_VERSION",
    "PROJECT_ROLES",
    "PROPOSAL_STATES",
    "SCOPES",
    "SCOPE_GLOBAL",
    "SCOPE_PROJECT",
    "GlobalVaultGrant",
    "MindError",
    "MindGrant",
    "MindService",
    "MindStore",
    "load_mind_grant",
    "valid_document_name",
    "valid_role",
    "valid_scope",
]
