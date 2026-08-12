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

import os
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
    DocumentState,
    inspect_document,
    open_target,
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
    TargetAuthorityChanged,
)
from .grant import load_mind_grant
from .hashing import document_hash, target_binding_hash
from .identity import new_proposal_id
from .roles import SCOPE_GLOBAL, SCOPE_PROJECT, roles_for_scope, valid_role, valid_scope
from .store import (
    DEFAULT_LIST_LIMIT,
    PROPOSAL_STATES,
    REASON_AUTHORITY_CHANGED,
    REASON_CONTENT_DRIFTED,
    REASON_INTERRUPTED,
    REASON_RECOVERED_APPLIED,
    REASON_RECOVERY_CONFLICTED,
    REASON_TARGET_MISSING,
    SOURCE_USER,
    STATE_APPLIED,
    STATE_APPLYING,
    STATE_INTERRUPTED,
    STATE_PENDING,
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

#: How many interrupted applies one start-up will classify. Bounded because
#: recovery runs before the first request is served, and an unbounded scan on a
#: damaged database would turn a start into a hang. Reaching it would mean
#: hundreds of applies were interrupted, which is a different problem.
MAX_RECOVERY_PER_START = 100


@dataclass(frozen=True)
class _Target:
    """An approved target, resolved from host authority. Never published.

    :attr:`root` and :attr:`relative` exist for exactly as long as one operation
    takes. Nothing that leaves this module carries either of them.
    """

    scope: str
    workspace_id: Optional[str]
    project_id: Optional[str]
    role: str
    root: Path
    relative: str

    @property
    def binding_hash(self) -> str:
        """A fingerprint of the host authority that produced this target.

        Everything a change to which would mean the role now names a *different
        document*, and nothing else:

        * the scope, so a project target and a global one can never alias;
        * the workspace and the project it binds to — project scope only;
        * the canonical root, as raw filesystem bytes, so a re-pointed project
          root or a re-granted vault is a different binding even when the
          relative name is unchanged;
        * the role;
        * the configured relative document name for that role.

        The root goes in as ``os.fsencode`` bytes rather than a decoded string:
        a path is bytes on this platform, and decoding it would make two
        genuinely different roots that differ only in undecodable bytes hash the
        same. **It is hashed, never stored and never published** — a one-way
        digest is what is durable, and the location it came from stays on the
        host.

        Absent fields are written as empty rather than skipped, so a global
        target (no workspace, no project) cannot produce the same field sequence
        as some project target that happens to line up. Length prefixing makes
        that unambiguous either way; this makes it obvious.
        """
        return target_binding_hash(
            [
                self.scope.encode("utf-8"),
                (self.workspace_id or "").encode("utf-8"),
                (self.project_id or "").encode("utf-8"),
                self.role.encode("utf-8"),
                os.fsencode(self.root),
                self.relative.encode("utf-8"),
            ]
        )


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
                project_id=None,
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
            project_id=project.project_id,
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
        # One descriptor for the bytes and the metadata: the size and hash
        # published here describe the same read that produced the text, rather
        # than a second look at the name a moment later.
        with open_target(target.root, target.relative) as handle:
            data, modified = handle.read()
        state = DocumentState(
            size=len(data),
            content_hash=document_hash(data),
            modified_at=modified,
        )
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
            # The second half of what acceptance is bound to. Computed from the
            # authority that produced `state`, in the same call, so the two
            # cannot describe different resolutions of the same role.
            target_binding_hash=target.binding_hash,
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
        """Refuse a proposal. Touches no document, ever."""
        proposal = self._require(proposal_id)
        if not proposal.decidable:
            raise ProposalNotPending(proposal.state)
        decided = self._store.decide(proposal.proposal_id, state=STATE_REJECTED)
        if decided is None:
            # Lost a race with another decision on the same proposal. Re-read and
            # report what actually happened rather than guessing.
            raise ProposalNotPending(self._require(proposal_id).state)
        return self._publish(decided, include_content=True)

    def accept_proposal(self, proposal_id: object) -> Dict[str, Any]:
        """Apply a proposal, atomically, bound to what it was reviewed against.

        The order is the design. Everything that could make this the wrong write
        is checked *before* the claim, and the claim happens before any byte
        moves:

        1. the proposal is decidable — pending, or interrupted and re-approved;
        2. it belongs to the workspace that is active now;
        3. the role still resolves, from configuration re-read at this moment —
           a revoked grant, a removed mapping or a disabled project refuses here
           with its own code, because each is a statement about *permission*
           rather than about drift;
        4. **the host authority that resolved it is the same one** — the role
           still names the same document. A content check cannot see this: remap
           a role to a byte-identical file and the base hash still matches;
        5. the document on disk still hashes to the recorded base;
        6. the apply is **claimed** durably — this is the concurrency boundary;
        7. the parent directory is opened and held, and the replace happens
           through that descriptor;
        8. only then is `applied` recorded.

        Steps 5 and 7 use the **same held descriptor**, so nothing is
        re-resolved between the hash that authorized the write and the write
        itself.

        Failure at 4 marks it `stale` with `authority_changed`; at 5 with
        `content_drifted`; at 7 it goes back to `pending` with the document
        byte-identical; a crash anywhere after 6 is classified by
        :meth:`recover_after_restart` rather than assumed.
        """
        proposal = self._require(proposal_id)
        if not proposal.decidable:
            raise ProposalNotPending(proposal.state)

        if proposal.scope == SCOPE_PROJECT:
            self._workspaces.reload_workspaces()
            workspace = self._workspaces.require_active_workspace()
            if workspace.workspace_id != proposal.workspace_id:
                # Left decidable, not marked stale: nothing is wrong with the
                # proposal, the wrong workspace is active. Switching back and
                # accepting is a legitimate thing to do.
                raise ProposalWorkspaceChanged()

        target = self._resolve(proposal.scope, proposal.role)

        if target.binding_hash != proposal.target_binding_hash:
            # The role now names a different document. Terminal, because the
            # review was of a different file — and reported with its own code so
            # the operator is not sent to look for an edit that never happened.
            self._store.decide(
                proposal.proposal_id,
                state=STATE_STALE,
                reason=REASON_AUTHORITY_CHANGED,
            )
            raise TargetAuthorityChanged()

        try:
            with open_target(target.root, target.relative) as handle:
                current = handle.inspect()

                if current.content_hash != proposal.base_hash:
                    self._store.decide(
                        proposal.proposal_id,
                        state=STATE_STALE,
                        reason=REASON_CONTENT_DRIFTED,
                    )
                    raise ProposalStale()

                # Claimed only once every check has passed, and *inside* the
                # held descriptor so the file that was hashed is the file that
                # is written. A second accept arriving now finds the proposal no
                # longer decidable and is refused rather than queued.
                claimed = self._store.claim(proposal.proposal_id)
                if claimed is None:
                    raise ProposalNotPending(self._require(proposal_id).state)

                try:
                    handle.replace(proposal.content.encode("utf-8"))
                except MindError:
                    # The document is byte-identical to what it was, so the claim
                    # is given back and the proposal is decidable again.
                    #
                    # Best-effort *and the original failure always wins*: if the
                    # store is what broke, a raising release would replace "the
                    # document could not be written" with a database error while
                    # leaving the row claimed — the state this is undoing.
                    try:
                        self._store.release(
                            proposal.proposal_id, to_state=STATE_PENDING
                        )
                    except Exception:  # pragma: no cover - store failure in repair
                        pass
                    raise
        except RoleUnavailable:
            # The document the base hash describes is gone, or became something
            # that is not an ordinary file. That is drift of the most complete
            # kind, so it is recorded as staleness.
            self._store.decide(
                proposal.proposal_id, state=STATE_STALE, reason=REASON_TARGET_MISSING
            )
            raise ProposalStale("the document is no longer there")

        # The bytes have landed. If this commit fails the row stays `applying`
        # and recovery reconciles it truthfully from the file's own hash — which
        # is exactly the case that used to be recorded as a completed apply.
        decided = self._store.finalize_applied(
            proposal.proposal_id, applied_hash=proposal.content_hash
        )
        if decided is None:  # pragma: no cover - the claim was ours
            raise ProposalNotPending(self._require(proposal_id).state)
        return self._publish(decided, include_content=True)

    # -- recovery ------------------------------------------------------------

    def recover_after_restart(self) -> Dict[str, int]:
        """Classify every apply that was claimed and never finished.

        Called once at start-up, before the first request. Nothing is resumed:
        **recovery never performs a canonical write.** A consequential operation
        continued by a restart is one nobody authorized at the moment it
        happened, and the whole point of this milestone is that a person
        authorizes each one.

        What it does instead is decide which of three things is true, from the
        durable row plus the file's own hash:

        ``applied``
            the file already hashes to the proposed content — the bytes landed
            and only the record was lost. Reconciled to `applied`, which writes
            no canonical memory and removes a row that would otherwise claim
            less than actually happened.

        ``interrupted``
            the file still hashes to the recorded base — the mutation did not
            land. Left for a person to decide again on the private surface.

        ``stale``
            the file is neither — somebody else changed it. Terminal, and no
            write.

        A target that cannot be resolved or whose authority moved is also
        `interrupted`: the honest answer is that Cofferdam could not determine
        what happened, which is not the same as knowing nothing happened.
        """
        tally = {STATE_APPLIED: 0, STATE_INTERRUPTED: 0, STATE_STALE: 0}
        for proposal in self._store.list_proposals(
            state=STATE_APPLYING, limit=MAX_RECOVERY_PER_START
        ):
            outcome = self._classify_interrupted(proposal)
            if outcome == STATE_APPLIED:
                self._store.finalize_applied(
                    proposal.proposal_id,
                    applied_hash=proposal.content_hash,
                    reason=REASON_RECOVERED_APPLIED,
                )
            elif outcome == STATE_STALE:
                self._store.decide(
                    proposal.proposal_id,
                    state=STATE_STALE,
                    reason=REASON_RECOVERY_CONFLICTED,
                    from_states=(STATE_APPLYING,),
                )
            else:
                self._store.release(
                    proposal.proposal_id,
                    to_state=STATE_INTERRUPTED,
                    reason=REASON_INTERRUPTED,
                )
            tally[outcome] += 1
        return tally

    def _classify_interrupted(self, proposal) -> str:
        """Which of the three outcomes an interrupted apply had. Reads only.

        **Total by construction.** This runs during start-up, before the first
        request is served, so anything it fails to handle is a daemon that will
        not start. The refusals it must absorb are not only this package's: a
        project-scope proposal resolves through the workspace service, which
        raises its *own* error type when no workspace is active — a completely
        ordinary state for a host that was restarted with nothing selected.

        So every failure resolves to `interrupted`, which is the conservative
        answer and the truthful one: Cofferdam could not see the document, so it
        cannot say whether the write landed, and saying so is not the same as
        claiming nothing happened.
        """
        try:
            target = self._resolve(proposal.scope, proposal.role)
            if target.binding_hash != proposal.target_binding_hash:
                # A different document now. Comparing hashes against it would
                # answer a question about the wrong file.
                return STATE_INTERRUPTED
            current = inspect_document(target.root, target.relative)
        except Exception:
            return STATE_INTERRUPTED
        if current.content_hash == proposal.content_hash:
            return STATE_APPLIED
        if current.content_hash == proposal.base_hash:
            return STATE_INTERRUPTED
        return STATE_STALE

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
        """Whether accepting this now would refuse. Derived, never stored.

        Covers **both** halves of the binding: the bytes drifting, and the
        authority moving so that the role names a different document. A client
        rendering an Accept button needs one answer to "would this work", and a
        flag that only watched content would say yes to a proposal the apply is
        going to refuse.

        Total and quiet: a proposal that is not decidable is not stale (it is
        decided, or claimed), and anything that cannot be resolved counts as
        drifted rather than raising — this is a rendering hint on a read, and a
        read must not fail because a project was disabled.
        """
        if not proposal.decidable:
            return False
        try:
            target = self._resolve(proposal.scope, proposal.role)
            if proposal.scope == SCOPE_PROJECT and target.workspace_id != proposal.workspace_id:
                return True
            if target.binding_hash != proposal.target_binding_hash:
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
