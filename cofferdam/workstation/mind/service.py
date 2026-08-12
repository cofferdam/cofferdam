"""One producer for "what may Cofferdam read, and what may it be allowed to write".

This is the only place a role becomes a file. Every route, and later the Context
Builder and the planner, comes through here rather than resolving anything
itself — not for tidiness, but because a second resolver is a second place for
the containment walk to be forgotten, and the containment walk is the whole
boundary.

The authority order, restated
-----------------------------

**The host decides where.** A project's directory comes from
``task-projects.json`` through the workspace's project — this module never reads
that file itself and never holds a root of its own. A vault's directory comes
from the grant. Neither is reachable from a request.

**The workspace decides which file plays which role.** ``documents`` on a
workspace entry, validated at load and re-read here on every resolution, because
somebody edits that file in a text editor while the daemon runs.

**The caller decides nothing except the role.** Two closed vocabularies, nine
words between them.

Why resolution happens twice
----------------------------

Once when a proposal is created, to record the base hash; again when it is
accepted, from configuration re-read at that moment. The second one is not
paranoia about the first: between the two, somebody can edit the document, edit
the mapping, disable the project, revoke the grant, or switch workspace. Each of
those means the change a person reviewed is not the change that would land, and
each has its own refusal below.

What this module will never do
------------------------------

Send anything anywhere. Reading the mind is a **local** operation and this
milestone adds no egress path of any kind: no provider client, no bridge Action,
no worker context, no projection. D-2026-08-11-5 makes the outbound object a
separate type built by an explicit egress policy, and that type does not exist
yet — which is the honest state to be in, and the reason a caller cannot
accidentally be in a different one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..workspace.errors import (
    ActiveWorkspaceUnset,
    WorkspaceDisabled,
    WorkspaceProjectMissing,
    WorkspaceUnknown,
)
from .documents import (
    MAX_DOCUMENT_BYTES,
    inspect_document,
    read_document,
    replace_document,
)
from .errors import (
    ContentInvalid,
    GlobalGrantMissing,
    MindError,
    ProposalNotPending,
    ProposalStale,
    ProposalUnknown,
    ProposalWorkspaceChanged,
    ReasonInvalid,
    RoleInvalid,
    RoleUnavailable,
    RoleUnconfigured,
    ScopeInvalid,
    SourceInvalid,
)
from .grant import load_mind_grant
from .hashing import document_hash
from .identity import new_proposal_id
from .roles import SCOPE_GLOBAL, SCOPE_PROJECT, roles_for_scope, valid_role, valid_scope
from .store import (
    DEFAULT_LIST_LIMIT,
    PROPOSAL_STATES,
    SOURCE_USER,
    STATE_APPLIED,
    STATE_REJECTED,
    STATE_STALE,
    MindStore,
)

#: The version of the mind payload shape, published so a client can branch on it
#: rather than sniffing for keys — the contract Task Core and the workspace
#: payload both publish.
MIND_API_VERSION = 1

#: A reason is one line. Long enough for a real sentence in Turkish or English,
#: short enough that it cannot become a second document.
MAX_REASON_CHARS = 300


@dataclass(frozen=True)
class _Target:
    """An approved target, resolved from host authority. Never published.

    :attr:`root` and :attr:`relative` exist for exactly as long as one operation
    takes. Nothing that leaves this module carries either of them.
    """

    scope: str
    workspace_id: Optional[str]
    role: str
    root: Path
    relative: str


class MindService:
    """Reads approved memory, and queues proposed changes to it.

    Takes the workspace service rather than the workspace store, so that "which
    project is this workspace over, and is it usable" stays one implementation
    with one set of refusals. Duplicating that resolution here is precisely the
    second-authority mistake the workspace module was written to avoid.
    """

    def __init__(self, config, store: MindStore, *, workspaces) -> None:
        self._config = config
        self._store = store
        self._workspaces = workspaces

    @property
    def store(self) -> MindStore:
        return self._store

    # -- resolution ----------------------------------------------------------

    def _vault(self):
        """The granted vault, or a refusal. Re-read from the host every time.

        Not cached. The grant is the most consequential line of configuration on
        the workstation, and revoking it should take effect when the file
        changes rather than when the daemon next restarts.
        """
        grant = load_mind_grant(self._config)
        if grant.vault is None:
            raise GlobalGrantMissing()
        return grant.vault

    def _resolve(self, scope: object, role: object) -> _Target:
        """Turn a scope and a role into an approved location, or refuse.

        The order matters. Scope and role are checked **first**, against closed
        vocabularies, so a hostile value in either is refused before anything
        looks at a grant, a workspace or a filesystem — which is what makes the
        claim "request text never becomes a path component" structural rather
        than a consequence of validation being thorough enough.
        """
        if not valid_scope(scope):
            raise ScopeInvalid()
        if not valid_role(scope, role):
            raise RoleInvalid()

        if scope == SCOPE_GLOBAL:
            vault = self._vault()
            relative = vault.documents.get(role)
            if relative is None:
                raise RoleUnconfigured(
                    "the granted vault does not map a document to '" + str(role) + "'"
                )
            return _Target(
                scope=SCOPE_GLOBAL,
                workspace_id=None,
                role=str(role),
                root=vault.root,
                relative=relative,
            )

        # Re-read, not cached: `workspaces.json` is edited by hand while the
        # daemon runs, and a role mapping is exactly the kind of thing somebody
        # fixes and then expects to take effect.
        self._workspaces.reload_workspaces()
        workspace = self._workspaces.require_active_workspace()
        project = self._workspaces.require_project(workspace)
        relative = workspace.documents.get(role)
        if relative is None:
            raise RoleUnconfigured(
                "this workspace does not map a document to '" + str(role) + "'"
            )
        return _Target(
            scope=SCOPE_PROJECT,
            workspace_id=workspace.workspace_id,
            role=str(role),
            root=project.root,
            relative=relative,
        )

    # -- reading -------------------------------------------------------------

    def available(self) -> Dict[str, Any]:
        """Which roles are readable right now. Metadata only, never a path.

        Never an error for an ordinary state — no workspace active, no grant, a
        role mapped to a file that is not there. Each of those is a real state of
        a working host and a client has to render it, so each is a word in the
        payload on a 200. That is the posture ``GET /api/workspace/current``
        established.
        """
        grant = load_mind_grant(self._config)
        payload: Dict[str, Any] = {
            "version": MIND_API_VERSION,
            "problem": None,
            "workspace_id": None,
            "global_vault": grant.to_dict(),
            "documents": [],
            "proposals": self._store.counts(),
            "limitations": list(LIMITATIONS),
        }

        entries: List[Dict[str, Any]] = []

        workspace = None
        try:
            self._workspaces.reload_workspaces()
            workspace = self._workspaces.require_active_workspace()
        except ActiveWorkspaceUnset:
            payload["problem"] = "no_active_workspace"
        except WorkspaceUnknown:
            payload["problem"] = "active_workspace_unconfigured"
        except WorkspaceDisabled:
            payload["problem"] = "active_workspace_disabled"

        if workspace is not None:
            payload["workspace_id"] = workspace.workspace_id
            try:
                project = self._workspaces.require_project(workspace)
            except WorkspaceProjectMissing:
                project = None
                payload["problem"] = "active_workspace_project_missing"
            if project is not None:
                for role in roles_for_scope(SCOPE_PROJECT):
                    relative = workspace.documents.get(role)
                    if relative is not None:
                        entries.append(self._describe(SCOPE_PROJECT, role, project.root, relative))

        if grant.vault is not None:
            for role in roles_for_scope(SCOPE_GLOBAL):
                relative = grant.vault.documents.get(role)
                if relative is not None:
                    entries.append(self._describe(SCOPE_GLOBAL, role, grant.vault.root, relative))

        payload["documents"] = entries
        return payload

    @staticmethod
    def _describe(scope: str, role: str, root: Path, relative: str) -> Dict[str, Any]:
        """One row of the overview. Six keys, and none of them is a location.

        An unreadable document reports ``available: false`` and nothing else —
        not *why*, because the reasons are all facts about the host's
        filesystem, and publishing them one row at a time would describe it just
        as well as publishing the path.
        """
        try:
            state = inspect_document(root, relative)
        except MindError:
            return {
                "scope": scope,
                "role": role,
                "available": False,
                "bytes": None,
                "content_hash": None,
                "modified_at": None,
            }
        return {
            "scope": scope,
            "role": role,
            "available": True,
            "bytes": state.size,
            "content_hash": state.content_hash,
            "modified_at": state.modified_iso,
        }

    def read_document(self, scope: object, role: object) -> Dict[str, Any]:
        """One approved document's content, read from disk right now.

        The content leaves through the device-token surface and nowhere else.
        This method has no destination parameter and no caller that forwards its
        result off the host.
        """
        target = self._resolve(scope, role)
        state = inspect_document(target.root, target.relative)
        data = read_document(state.path)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            # Canonical memory is Markdown. A file that is not UTF-8 is not a
            # document this product can show or safely round-trip, and reporting
            # it as unavailable is truthful — refusing beats rendering mojibake
            # and then proposing an edit against it.
            raise RoleUnavailable("the document is not UTF-8 text")
        return {
            "version": MIND_API_VERSION,
            "scope": target.scope,
            "workspace_id": target.workspace_id,
            "role": target.role,
            "content": text,
            "bytes": state.size,
            "content_hash": state.content_hash,
            "modified_at": state.modified_iso,
        }

    # -- proposals -----------------------------------------------------------

    def create_proposal(
        self,
        scope: object,
        role: object,
        content: object,
        reason: object,
        *,
        source: str = SOURCE_USER,
    ) -> Dict[str, Any]:
        """Queue an intended change. **Writes no Markdown.**

        Not "writes no Markdown if nothing goes wrong" — there is no code path
        from here to a document write. The target is resolved and *read* so the
        base hash is real, and the only thing written is a database row.

        ``source`` is keyword-only and assigned by the caller that authenticated
        the request, never taken from a body. A caller describing its own
        provenance is the opposite of what provenance is for, and it is why the
        workspace routes assign it the same way.
        """
        target = self._resolve(scope, role)
        text = self._validate_content(content)
        why = self._validate_reason(reason)
        if source != SOURCE_USER:
            # Only the device-token surface exists in this milestone, so `user`
            # is the only provenance this build can honestly record. `planner`
            # and `cofferdam` are reserved in the store's vocabulary and stay
            # unreachable until something that is one of them exists.
            raise SourceInvalid()

        # The base. Resolving again inside the same call rather than trusting a
        # value from `available()` — a client could have polled that minutes ago.
        state = inspect_document(target.root, target.relative)

        proposal = self._store.create_proposal(
            proposal_id=new_proposal_id(),
            scope=target.scope,
            workspace_id=target.workspace_id,
            role=target.role,
            content=text,
            content_hash=document_hash(text.encode("utf-8")),
            base_hash=state.content_hash,
            base_bytes=state.size,
            reason=why,
            source=source,
        )
        return self._publish(proposal, include_content=True)

    def list_proposals(
        self, *, state: object = None, limit: int = DEFAULT_LIST_LIMIT
    ) -> Dict[str, Any]:
        """Proposals, newest first, bounded, without their content.

        An unrecognised state filter matches nothing rather than being ignored:
        a filter that silently returned everything would be a client showing a
        person the wrong queue and having no way to tell.
        """
        chosen: Optional[str]
        if state is None:
            chosen = None
        elif isinstance(state, str) and state in PROPOSAL_STATES:
            chosen = state
        else:
            return {
                "version": MIND_API_VERSION,
                "proposals": [],
                "limit": max(1, min(int(limit), 100)),
                "state": None,
                "counts": self._store.counts(),
            }

        rows = self._store.list_proposals(state=chosen, limit=limit)
        return {
            "version": MIND_API_VERSION,
            "proposals": [row.to_dict() for row in rows],
            "limit": max(1, min(int(limit), 100)),
            "state": chosen,
            "counts": self._store.counts(),
        }

    def get_proposal(self, proposal_id: object) -> Dict[str, Any]:
        """One proposal, with its content and whether it has drifted **now**.

        ``stale`` is derived on every read and never stored. A stored flag would
        be correct for a few seconds and then wrong with nothing announcing it —
        and this particular flag is the one a person uses to decide whether to
        press Accept.
        """
        proposal = self._require(proposal_id)
        return self._publish(proposal, include_content=True)

    def reject_proposal(self, proposal_id: object) -> Dict[str, Any]:
        """Refuse a pending proposal. Touches no document, ever."""
        proposal = self._require(proposal_id)
        if not proposal.pending:
            raise ProposalNotPending(proposal.state)
        decided = self._store.decide(proposal.proposal_id, state=STATE_REJECTED)
        if decided is None:
            # Lost a race with another decision on the same proposal. Re-read and
            # report what actually happened rather than guessing.
            raise ProposalNotPending(self._require(proposal_id).state)
        return self._publish(decided, include_content=True)

    def accept_proposal(self, proposal_id: object) -> Dict[str, Any]:
        """Apply a pending proposal, atomically, bound to the hash it was made against.

        The order is the design. Everything that could make this the wrong write
        is checked *before* any byte is written, and the last thing checked is
        the one most likely to have changed:

        1. the proposal is pending;
        2. it belongs to the workspace that is active now;
        3. the role still resolves, from configuration re-read at this moment;
        4. the document on disk still hashes to the recorded base;
        5. only then, the atomic replace.

        A failure at 4 marks the proposal **stale** and writes nothing. A failure
        at 5 leaves it **pending** — the document is unchanged, so accepting
        again once the disk problem is fixed is the right thing to do, and
        marking it decided would have thrown away a change nobody rejected.
        """
        proposal = self._require(proposal_id)
        if not proposal.pending:
            raise ProposalNotPending(proposal.state)

        if proposal.scope == SCOPE_PROJECT:
            self._workspaces.reload_workspaces()
            workspace = self._workspaces.require_active_workspace()
            if workspace.workspace_id != proposal.workspace_id:
                # Left pending, not marked stale: nothing is wrong with the
                # proposal, the wrong workspace is active. Switching back and
                # accepting is a legitimate thing to do.
                raise ProposalWorkspaceChanged()

        # Resolved again from current host authority. A revoked grant, a removed
        # mapping or a disabled project raises out of here with its own code —
        # each is a statement about *permission*, which is not the same thing as
        # drift and should not be recorded as it.
        target = self._resolve(proposal.scope, proposal.role)

        try:
            state = inspect_document(target.root, target.relative)
        except RoleUnavailable:
            # The document the base hash describes is gone. That is drift of the
            # most complete kind, so it is recorded as staleness rather than as
            # a configuration problem.
            self._store.decide(proposal.proposal_id, state=STATE_STALE)
            raise ProposalStale("the document is no longer there")

        if state.content_hash != proposal.base_hash:
            self._store.decide(proposal.proposal_id, state=STATE_STALE)
            raise ProposalStale()

        # Every check has passed and the state is claimed *before* the write, so
        # two concurrent accepts cannot both reach the filesystem: the second
        # `decide` changes no row and returns None.
        decided = self._store.decide(
            proposal.proposal_id, state=STATE_APPLIED, applied_hash=proposal.content_hash
        )
        if decided is None:
            raise ProposalNotPending(self._require(proposal_id).state)

        try:
            replace_document(state.path, proposal.content.encode("utf-8"))
        except MindError:
            # The write failed and the document is untouched, so the row is put
            # back to pending. Recording it as applied would claim a change that
            # is not on disk — the one lie this whole path exists to prevent.
            #
            # The repair is best-effort *and the original failure always wins*.
            # If the store itself is what broke, a raising `reopen` would replace
            # "the document could not be written" with a database error, and the
            # caller would be told the wrong thing about the wrong subsystem —
            # while the row stayed `applied`, which is the very state this is
            # trying to undo. Losing the repair is bad; losing the report of the
            # failure that caused it is worse.
            try:
                self._store.reopen(proposal.proposal_id)
            except Exception:  # pragma: no cover - a store failure during repair
                pass
            raise

        return self._publish(decided, include_content=True)

    # -- helpers -------------------------------------------------------------

    def _require(self, proposal_id: object):
        proposal = self._store.get_proposal(proposal_id)
        if proposal is None:
            raise ProposalUnknown()
        return proposal

    def _publish(self, proposal, *, include_content: bool) -> Dict[str, Any]:
        payload = proposal.to_dict(include_content=include_content)
        payload["version"] = MIND_API_VERSION
        payload["stale"] = self._is_stale(proposal)
        return payload

    def _is_stale(self, proposal) -> bool:
        """Whether the target has drifted from the recorded base, right now.

        Total and quiet: a decided proposal is not stale (it is decided), and a
        target that cannot be resolved counts as drifted rather than raising —
        this is a rendering hint on a read, and a read must not fail because a
        project was disabled.
        """
        if not proposal.pending:
            return False
        try:
            target = self._resolve(proposal.scope, proposal.role)
            if proposal.scope == SCOPE_PROJECT and target.workspace_id != proposal.workspace_id:
                return True
            state = inspect_document(target.root, target.relative)
        except Exception:
            return True
        return state.content_hash != proposal.base_hash

    @staticmethod
    def _validate_content(value: object) -> str:
        """The proposed document, or a refusal.

        Empty is refused rather than accepted, and that is a safety rule rather
        than a tidiness one: a replace with nothing in it empties a memory
        document, which is deletion in everything but name. D-2026-08-11-4 point
        6 says deletion is never proposable, so the empty case has to be closed
        here — otherwise the operation vocabulary having one word would be a
        technicality.
        """
        if not isinstance(value, str):
            raise ContentInvalid("expected the whole document as text")
        if not value.strip():
            raise ContentInvalid("an empty document would be a deletion, which is not proposable")
        encoded = value.encode("utf-8")
        if len(encoded) > MAX_DOCUMENT_BYTES:
            raise ContentInvalid(
                "at most " + str(MAX_DOCUMENT_BYTES) + " bytes, and this is " + str(len(encoded))
            )
        if "\x00" in value:
            raise ContentInvalid("a memory document is text")
        return value

    @staticmethod
    def _validate_reason(value: object) -> str:
        """One line saying why. Refused rather than truncated.

        The same rule the workspace objective follows: this is *authored* text,
        and silently storing half of it would show somebody a sentence they did
        not write, in the record that exists to explain a change to their own
        memory.
        """
        if not isinstance(value, str):
            raise ReasonInvalid("expected one short line of text")
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ReasonInvalid("say why in one line")
        if len(cleaned) > MAX_REASON_CHARS:
            raise ReasonInvalid(
                "at most " + str(MAX_REASON_CHARS) + " characters, and this is " + str(len(cleaned))
            )
        return cleaned

    def health(self) -> Dict[str, Any]:
        grant = load_mind_grant(self._config)
        return {
            "granted": grant.vault is not None,
            "grant_present": grant.source_present,
            "grant_problems": len(grant.problems),
            "proposals": self._store.counts(),
        }


#: What this PR can and cannot say, published in the payload rather than only in
#: the docs — the posture Task Core and the workspace payload both take.
LIMITATIONS = (
    "A request names a role; the host decides which file that is.",
    "The global vault is readable only under a host-owned grant, and there is no route that grants one.",
    "A proposal writes nothing. Applying happens only on explicit acceptance, and only if the document still matches the hash it was reviewed against.",
    "Deletion, renaming and creation of memory documents are not proposable in this milestone.",
    "Nothing here sends memory anywhere. Context assembly and any egress projection are later work.",
)


__all__ = [
    "LIMITATIONS",
    "MAX_REASON_CHARS",
    "MIND_API_VERSION",
    "MindService",
]
