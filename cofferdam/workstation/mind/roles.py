"""What a caller may name: a scope and a role, and never a file.

This is the smallest module in the package and it carries the whole access
model. Everything else — the grant, the containment walk, the proposal queue —
is machinery that exists to make the sentence below true:

    **A request identifies a semantic role. The host decides which file that
    is.**

Why a closed vocabulary rather than a validated string
------------------------------------------------------

A validated filename is still a filename. Every containment bug this product
could have starts with a caller supplying text that becomes a path component,
and the only defence that does not depend on getting the validation exactly
right is for the request text never to reach a path at all. ``status`` is not a
sanitised ``STATUS.md``; it is a word that this module either recognises or
does not, and the mapping from it to a file lives in host-owned configuration.

So a role that arrives as ``../../etc/passwd`` is not *cleaned* — it is not a
role, and the refusal happens before anything is resolved. The tests assert
exactly that, with a path in the role field.

Why two vocabularies rather than one
------------------------------------

D-2026-08-11-3 makes global mind and project mind different authorities: the
project's repository is canonical for the project, and the vault is user-level
memory read under a separate grant. One shared vocabulary would let
``read(scope="global", role="status")`` be a *sensible-looking* question, and the
answer to a sensible-looking question about somebody else's authority is the kind
of thing that gets implemented by accident. Keeping the sets disjoint makes the
cross-scope request a refusal in a lookup table.

Why these six project roles
---------------------------

D-2026-08-11-3 names both shapes a project can have. Cofferdam itself is
role-mapped onto documents it already has — ``plan`` → ``ROADMAP.md``, ``status``
→ ``STATUS.md``, ``decisions`` → ``DECISIONS.md``, ``design`` → ``DESIGN.md`` —
and a project with no established vocabulary may use the compact template
(``PROJECT.md``, ``RESEARCH.md``, ``PLAN.md``, ``DECISIONS.md``, ``STATUS.md``).
The union of those two is this list. **No ``PLAN.md`` is created anywhere** to
satisfy a convention; the mapping is what makes that unnecessary.

Why the vault has three roles and no freeform note
--------------------------------------------------

``USER.md``, ``COMMUNICATION_STYLE.md`` and ``PREFERENCES.md`` are the durable
user-level documents D-2026-08-11-3 names, and each is a role the operator maps
once. Freeform vault notes are deliberately absent: a freeform note is addressed
by *filename*, and a caller-supplied filename is the authority this module
exists to remove. Retrieval over the vault is M2N's problem, not a hole to leave
open here.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

#: The project's own repository, reached through the active workspace's project.
SCOPE_PROJECT = "project"

#: The dedicated Cofferdam vault, reachable only under a host-owned grant.
SCOPE_GLOBAL = "global"

SCOPES: Tuple[str, ...] = (SCOPE_PROJECT, SCOPE_GLOBAL)

PROJECT_ROLES: Tuple[str, ...] = (
    "project",
    "plan",
    "research",
    "decisions",
    "status",
    "design",
)

GLOBAL_ROLES: Tuple[str, ...] = (
    "user",
    "communication_style",
    "preferences",
)

_ROLES_BY_SCOPE = {
    SCOPE_PROJECT: PROJECT_ROLES,
    SCOPE_GLOBAL: GLOBAL_ROLES,
}

#: A configured document name is a relative path *inside* an already-approved
#: root. Bounded because it is written by a person and rendered in a problem
#: message, and depth-limited because a mapping is a document, not a tree walk.
MAX_DOCUMENT_NAME_CHARS = 200
MAX_DOCUMENT_NAME_SEGMENTS = 8

_NAME_FORBIDDEN = ("~", "$", "\\", "\x00")


def roles_for_scope(scope: Any) -> Tuple[str, ...]:
    """The roles that exist in a scope. An unknown scope has none, not all."""
    if not isinstance(scope, str):
        return ()
    return _ROLES_BY_SCOPE.get(scope, ())


def valid_scope(scope: Any) -> bool:
    return isinstance(scope, str) and scope in SCOPES


def valid_role(scope: Any, role: Any) -> bool:
    """Whether this exact word is a role in this exact scope.

    Membership, not a pattern. There is no shape a hostile string can take that
    passes this and is not one of nine literals.
    """
    return isinstance(role, str) and role in roles_for_scope(scope)


def valid_document_name(value: Any) -> Optional[str]:
    """A configured relative document name, or ``None``.

    Lexical only — nothing is touched on disk here, exactly like
    :func:`~..tasks.projects._read_root`. The containment that matters is
    enforced again at use, against the real filesystem, in
    :mod:`.documents`; this is the cheap check that keeps an obviously wrong
    mapping from ever becoming a candidate path.

    Refused: absolute paths, ``~``, ``$``, backslashes, NUL and control
    characters, any ``.`` or ``..`` segment, empty segments (which is what a
    doubled separator is), and anything past the length or depth bound.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_DOCUMENT_NAME_CHARS:
        return None
    if any(marker in text for marker in _NAME_FORBIDDEN):
        return None
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in text):
        return None
    if text.startswith("/"):
        return None
    segments = text.split("/")
    if not (0 < len(segments) <= MAX_DOCUMENT_NAME_SEGMENTS):
        return None
    for segment in segments:
        if not segment or segment in (".", ".."):
            return None
    return text


__all__ = [
    "GLOBAL_ROLES",
    "MAX_DOCUMENT_NAME_CHARS",
    "MAX_DOCUMENT_NAME_SEGMENTS",
    "PROJECT_ROLES",
    "SCOPES",
    "SCOPE_GLOBAL",
    "SCOPE_PROJECT",
    "roles_for_scope",
    "valid_document_name",
    "valid_role",
    "valid_scope",
]
