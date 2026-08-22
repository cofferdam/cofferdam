"""The one place a project id becomes cloud-eligible project context (M2J PR4).

This module is the whole of PR4's server-side authority, and it is deliberately
small. It answers exactly one question — *given a project id somebody outside
this host named, what bounded project context may leave?* — and it answers it by
delegating every hard part to a component that already earned it:

```
project_id  →  resolve  →  ContextBuilder.build_without_message()
                              →  LocalContextPack        (rich, local, unsendable)
                              →  ContextProjector.project()
                              →  CloudContextProjection   (bounded, eligible)
```

What this module owns
---------------------

**Resolution, fail-closed.** A project id is matched against the registry and
then against the *enabled* workspaces that name it. Nothing is guessed and
nothing is picked by position: zero matches, several matches and a match that is
not the active workspace are three different refusals with three different
reason codes. "Pick the first one" is the bug this shape exists to prevent.

**The redaction environment**, assembled from host-owned configuration only —
the operational home, the resolved project root, the granted vault root and both
A/B slot roots. See :meth:`ProjectContextService._redaction_environment`.

What this module refuses to own
-------------------------------

**Selection.** There is no parameter here that names a role, a source, a policy,
a path, an allowlist or a redaction rule, because a caller that could name what
it receives would be the authority on its own permissions. The only input is an
opaque project id.

**Egress policy.** `project_context_external_v1` decides what may leave, and it
lives in :mod:`.context.projection`. This module calls it; it does not configure it,
extend it or pass it options.

**Transport.** Nothing here opens a socket. It returns an object.

Why it lives here and not under ``context/``
--------------------------------------------

``cofferdam/workstation/context/`` is guarded: `tests/test_context_builder.py`
asserts that **no module in that package imports the projection subpackage**, so
a `LocalContextPack` can never acquire a route to becoming a
`CloudContextProjection` from the inside. This module imports both halves on
purpose — it is the composition, not a context component — so it belongs outside
the package that guarantee is about. Putting it under ``context/`` would have
meant weakening that test, which is the wrong direction entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .context.projection import ContextProjector, HostRedactionEnvironment

#: The external request vocabulary, in full. One opaque id and nothing else.
#:
#: Bounded and character-restricted for the same reason `source_ref` is: an id
#: that could carry a separator, a dot-dot or a scheme is an id that could be
#: aimed somewhere. The registry lookup would refuse it anyway — this refuses it
#: earlier and says so in a code a client can act on.
MAX_PROJECT_ID_CHARS = 64

#: Reason codes for the read. Closed set, machine-readable, path-free.
#:
#: Each names a state the operator can act on, and none of them says *where*
#: anything is: "this project has no workspace" is actionable, and the directory
#: it would have pointed at is not the caller's business.
REASON_PROJECT_NOT_FOUND = "project_not_found"
REASON_PROJECT_DISABLED = "project_disabled"
REASON_WORKSPACE_NOT_CONFIGURED = "workspace_not_configured"
REASON_WORKSPACE_AMBIGUOUS = "workspace_ambiguous"
REASON_WORKSPACE_DISABLED = "workspace_disabled"
REASON_WORKSPACE_NOT_ACTIVE = "workspace_not_active"
REASON_CONTEXT_UNAVAILABLE = "context_unavailable"
REASON_PROJECTION_FAILED = "projection_failed"
REASON_INVALID_PROJECT_ID = "invalid_project_id"
REASON_RESPONSE_TOO_LARGE = "response_too_large"

READ_REASONS: Tuple[str, ...] = (
    REASON_INVALID_PROJECT_ID,
    REASON_PROJECT_NOT_FOUND,
    REASON_PROJECT_DISABLED,
    REASON_WORKSPACE_NOT_CONFIGURED,
    REASON_WORKSPACE_AMBIGUOUS,
    REASON_WORKSPACE_DISABLED,
    REASON_WORKSPACE_NOT_ACTIVE,
    REASON_CONTEXT_UNAVAILABLE,
    REASON_PROJECTION_FAILED,
    REASON_RESPONSE_TOO_LARGE,
)


class ProjectContextUnavailable(Exception):
    """A read that cannot be answered, with a reason a client may see.

    The message is code-owned text. It never carries a path, a root, a registry
    entry or an upstream exception string — the three places a refusal usually
    leaks the thing it was refusing to disclose.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class ResolvedContext:
    """A projection plus the safe identity it was produced for."""

    workspace_id: str
    project_id: str
    projection: Any


@dataclass(frozen=True)
class ResolvedProject:
    """A project id that survived resolution, and what it resolved to.

    Published (M2M PR4) so a second caller — the remote development request
    ingress — can reuse this module's resolution instead of writing its own. It
    carries the registry entry because the redaction environment is derived from
    the project's root, and that derivation stays in this module: the entry is
    handed straight back to :meth:`ProjectContextService.projector_for` and is
    not something a caller reads a path out of.
    """

    project_id: str
    workspace_id: str
    project: Any


def _valid_project_id(value: object) -> str:
    """An opaque registry id. Not a path, and never treated as one."""
    if not isinstance(value, str):
        raise ProjectContextUnavailable(
            REASON_INVALID_PROJECT_ID, "a project id is text"
        )
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_PROJECT_ID_CHARS:
        raise ProjectContextUnavailable(
            REASON_INVALID_PROJECT_ID,
            "a project id is between 1 and " + str(MAX_PROJECT_ID_CHARS) + " characters",
        )
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if not set(candidate) <= allowed:
        raise ProjectContextUnavailable(
            REASON_INVALID_PROJECT_ID,
            "a project id may contain letters, digits, dot, hyphen and underscore",
        )
    if candidate.startswith(".") or ".." in candidate:
        raise ProjectContextUnavailable(
            REASON_INVALID_PROJECT_ID, "a project id is a name, never a location"
        )
    return candidate


class ProjectContextService:
    """Resolves a project id and returns a `CloudContextProjection`. Reads only.

    Takes services rather than stores or paths, for the reason the Context
    Builder does: a second resolver is a second place for the grant check and
    the containment walk to be forgotten.
    """

    def __init__(
        self,
        *,
        config,
        workspaces,
        projects,
        builder,
        clock=None,
    ) -> None:
        self._config = config
        self._workspaces = workspaces
        self._projects = projects
        self._builder = builder
        self._clock = clock

    # -- redaction environment ------------------------------------------------

    def _redaction_environment(self, project_root) -> HostRedactionEnvironment:
        """Host-owned values only. **Never anything a caller sent.**

        `HostRedactionEnvironment.none()` is legal and is not used here. It would
        leave the literal rules with nothing to recognise while the generic
        patterns went on matching, so the projector would look like it was
        working — the failure mode that argument has no default in order to
        prevent.

        Every value below comes from configuration this host owns: the
        operational home from `Config`, the project root the registry resolved,
        the vault root from the host-owned grant when one is granted, and the two
        A/B slot roots derived from the home. Nothing here reads a document to
        find a path, so no memory content can influence what gets redacted.
        """
        home = getattr(self._config, "home", None)
        home_text = str(home) if home is not None else None

        slot_roots: Tuple[str, ...] = ()
        if home is not None:
            slot_roots = (str(home / "slots" / "a"), str(home / "slots" / "b"))

        vault_roots: Tuple[str, ...] = ()
        vault_root = self._vault_root()
        if vault_root:
            vault_roots = (vault_root,)

        project_roots: Tuple[str, ...] = ()
        if project_root is not None:
            project_roots = (str(project_root),)

        home_directories: Tuple[str, ...] = ()
        if home_text:
            parent = getattr(home, "parent", None)
            if parent is not None:
                home_directories = (str(parent),)

        return HostRedactionEnvironment(
            cofferdam_home=home_text,
            project_roots=project_roots,
            vault_roots=vault_roots,
            slot_roots=slot_roots,
            home_directories=home_directories,
        )

    def _vault_root(self) -> Optional[str]:
        """The granted vault root, or nothing. A missing grant is not an error.

        An ungranted host has no vault root to redact and no vault content in the
        pack either, so the honest environment is one without it rather than a
        refusal.
        """
        try:
            from .mind.grant import load_mind_grant

            grant = load_mind_grant(self._config)
        except Exception:
            return None
        vault = getattr(grant, "vault", None)
        if vault is None or not getattr(vault, "enabled", False):
            return None
        root = getattr(vault, "root", None)
        return str(root) if root is not None else None

    # -- resolution -----------------------------------------------------------

    def _resolve(self, project_id: str):
        """project id → the one enabled, active workspace that may answer.

        Five refusals, and not one of them is a fallback. The last is the one a
        reader will not expect: the Context Builder is scoped to the **active**
        workspace by PR3's design — Working Context is a property of what is
        being worked on — so a request naming a configured-but-inactive workspace
        is refused rather than answered from the wrong state. Building for an
        arbitrary workspace would mean a caller choosing whose memory is read,
        which is the authority this whole milestone withholds.
        """
        project = None
        try:
            project = self._projects.get(project_id)
        except Exception:
            project = None
        if project is None:
            raise ProjectContextUnavailable(
                REASON_PROJECT_NOT_FOUND, "no project is configured under that id"
            )
        if not getattr(project, "enabled", False):
            raise ProjectContextUnavailable(
                REASON_PROJECT_DISABLED, "that project is disabled"
            )

        registry = self._workspaces.workspaces
        matches = [
            workspace
            for workspace in getattr(registry, "workspaces", ())
            if getattr(workspace, "project_id", None) == project_id
        ]
        if not matches:
            raise ProjectContextUnavailable(
                REASON_WORKSPACE_NOT_CONFIGURED,
                "no workspace is configured for that project",
            )
        enabled = [w for w in matches if getattr(w, "enabled", False)]
        if not enabled:
            raise ProjectContextUnavailable(
                REASON_WORKSPACE_DISABLED, "the workspace for that project is disabled"
            )
        if len(enabled) > 1:
            raise ProjectContextUnavailable(
                REASON_WORKSPACE_AMBIGUOUS,
                "more than one enabled workspace names that project",
            )

        workspace = enabled[0]
        snapshot = self._workspaces.current()
        active = snapshot.get("workspace") or {}
        active_id = active.get("workspace_id") if snapshot.get("active") else None
        if active_id != getattr(workspace, "workspace_id", None):
            raise ProjectContextUnavailable(
                REASON_WORKSPACE_NOT_ACTIVE,
                "that project's workspace is not the active one",
            )
        return project, workspace

    # -- reusable halves (M2M PR4) --------------------------------------------
    #
    # Published so the remote development request ingress can go through *this*
    # resolution and *this* redaction environment rather than assembling its own.
    # A second resolver would be a second place for the enabled check, the
    # ambiguity refusal and the active-workspace rule to be forgotten — the same
    # argument this module's docstring already makes about the Context Builder.
    #
    # Neither of these widens anything. `resolve` performs exactly the checks
    # `project_context` performs and refuses with exactly the same codes, and
    # `projector_for` takes a project this module resolved rather than a root, a
    # policy or a rule from anywhere else.

    @property
    def builder(self):
        """The host's Context Builder. Read-only, and not replaceable from here."""
        return self._builder

    def resolve(self, project_id: object) -> ResolvedProject:
        """A project id → the one enabled, active workspace that may answer it."""
        identifier = _valid_project_id(project_id)
        project, workspace = self._resolve(identifier)
        return ResolvedProject(
            project_id=identifier,
            workspace_id=getattr(workspace, "workspace_id", ""),
            project=project,
        )

    def projector_for(self, resolved: ResolvedProject) -> ContextProjector:
        """A projector carrying this project's host-owned redaction environment.

        The environment is assembled by :meth:`_redaction_environment` from
        configuration this host owns and nothing a caller sent, exactly as it is
        for :meth:`project_context`.
        """
        if not isinstance(resolved, ResolvedProject):
            raise ProjectContextUnavailable(
                REASON_PROJECTION_FAILED,
                "a projector is built for a resolved project, not for an id",
            )
        redaction = self._redaction_environment(
            getattr(resolved.project, "root", None)
        )
        if self._clock is not None:
            return ContextProjector(redaction=redaction, clock=self._clock)
        return ContextProjector(redaction=redaction)

    # -- the one public method ------------------------------------------------

    def project_context(self, project_id: object) -> ResolvedContext:
        """Resolve, build locally, project, and return only the projection.

        The `LocalContextPack` produced in the middle is a local variable and
        stays one. It is never returned, never stored and never handed to a
        serializer — the only thing that leaves this method is the projection.
        """
        resolved = self.resolve(project_id)

        try:
            pack = self._builder.build_without_message()
        except Exception:
            raise ProjectContextUnavailable(
                REASON_CONTEXT_UNAVAILABLE, "project context could not be assembled"
            )

        projector = self.projector_for(resolved)
        try:
            projection = projector.project(pack)
        except Exception:
            raise ProjectContextUnavailable(
                REASON_PROJECTION_FAILED, "project context could not be projected"
            )

        return ResolvedContext(
            workspace_id=resolved.workspace_id,
            project_id=resolved.project_id,
            projection=projection,
        )


#: The versioned wire contract for the read surface.
PROJECT_CONTEXT_API_VERSION = 1

#: The hard ceiling on the serialized response, in UTF-8 bytes.
#:
#: **Not** the projection's 16 KiB. That number bounds *content*; this one bounds
#: the HTTP body, and between them sit JSON escaping, per-part provenance,
#: omission rows, timestamps, the carried limitations and the envelope.
#:
#: Derived rather than chosen. The worst case for JSON escaping is a character
#: that encodes to `\uXXXX` — six bytes out for what may be one byte in — so
#: 16 KiB of content can reach 96 KiB of body. Add the fixed overhead (thirteen
#: limitation strings, per-part provenance, omission rows, two ISO timestamps and
#: the envelope) and 128 KiB clears it with margin. A first draft of this number
#: was 64 KiB and the test below measured 88 KB against it; the bound is the
#: measurement's, not the draft's.
#:
#: It encodes no provider's limit. There is no tokenizer here and no assumption
#: about which destination a later surface picks; this is a transport bound, and
#: the Actions bridge applies its own smaller one on top.
MAX_SERIALIZED_RESPONSE_BYTES = 128 * 1024


def serialize_project_context(resolved: object) -> Dict[str, Any]:
    """The only function that turns a projection into a wire payload.

    **The type check is the point.** It accepts a :class:`ResolvedContext`
    carrying a `CloudContextProjection` and refuses everything else — including,
    specifically, a `LocalContextPack`, which is the object this whole milestone
    exists to keep off the wire. A duck-typed check would pass one: the pack has
    `to_dict`, `version` and `parts` too. So the check is on the type.

    The payload is built field by field from the projection's own `to_dict`.
    Nothing is copied from the pack, no `fields` mapping survives, no
    `SectionRef.heading` exists to copy, and no root, objective object or Working
    Context row is reachable from here.
    """
    from .context.projection import CloudContextProjection

    if not isinstance(resolved, ResolvedContext):
        raise ProjectContextUnavailable(
            REASON_PROJECTION_FAILED, "project context could not be serialized"
        )
    projection = resolved.projection
    if not isinstance(projection, CloudContextProjection):
        raise ProjectContextUnavailable(
            REASON_PROJECTION_FAILED,
            "only a CloudContextProjection may be serialized as project context",
        )

    body = projection.to_dict()
    payload = {
        "version": PROJECT_CONTEXT_API_VERSION,
        "project_id": resolved.project_id,
        "workspace_id": resolved.workspace_id,
        "context": body,
    }

    # Measured on the exact bytes that would be sent, after escaping, and
    # **refused** rather than trimmed. Slicing a JSON document to fit a byte
    # count produces something that is not JSON, and dropping parts here would
    # be a second egress policy sitting underneath the named one — the projector
    # already decided what may leave, and transport does not get to revise it.
    size = serialized_size(payload)
    if size > MAX_SERIALIZED_RESPONSE_BYTES:
        raise ProjectContextUnavailable(
            REASON_RESPONSE_TOO_LARGE,
            "the projected context does not fit the response contract",
        )
    return payload


def serialized_size(payload: Dict[str, Any]) -> int:
    import json

    return len(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )


__all__ = [
    "MAX_PROJECT_ID_CHARS",
    "MAX_SERIALIZED_RESPONSE_BYTES",
    "PROJECT_CONTEXT_API_VERSION",
    "REASON_RESPONSE_TOO_LARGE",
    "serialize_project_context",
    "serialized_size",
    "READ_REASONS",
    "REASON_CONTEXT_UNAVAILABLE",
    "REASON_INVALID_PROJECT_ID",
    "REASON_PROJECTION_FAILED",
    "REASON_PROJECT_DISABLED",
    "REASON_PROJECT_NOT_FOUND",
    "REASON_WORKSPACE_AMBIGUOUS",
    "REASON_WORKSPACE_DISABLED",
    "REASON_WORKSPACE_NOT_ACTIVE",
    "REASON_WORKSPACE_NOT_CONFIGURED",
    "ProjectContextService",
    "ProjectContextUnavailable",
    "ResolvedContext",
    "ResolvedProject",
]
