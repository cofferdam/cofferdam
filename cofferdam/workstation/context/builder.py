"""One producer for "what bounded local context is appropriate right now".

The Context Builder reads. That is the whole of it. It holds no state, owns no
database, creates nothing, changes nothing, and has exactly one public method.

Where its material comes from
-----------------------------

Two services, and no third source of truth:

* :class:`~..workspace.service.WorkspaceService` for the snapshot — the active
  workspace, its project, and Working Context with the derived half derived.
* :class:`~..mind.service.MindService` for memory, addressed **by role**.

It is deliberately not a filesystem authority. There is no path in this module,
no root, no `open`, no walk, no glob. It cannot read `.obsidian/`, an unmapped
vault note or a document outside a granted root for the simplest possible
reason: it has no way to name one. `MindService` takes a scope and a role from a
closed vocabulary and the host decides which file that is, and this module is
just another caller of that boundary.

What it will never be
---------------------

**An egress path.** D-2026-08-11-5 makes anything leaving the host a separate
type built by an explicit projection, and that type does not exist. There is no
provider client here, no serializer to a wire format, no prompt, no message
array, no system string, no template. The pack stops at structured local
context, and the component that would turn it into a request has not been
written.

**A relevance engine.** Selection is a stored reference somebody wrote, or a
bounded structural slice, and each part says which. Nothing here scores, ranks
or guesses, and no keyword heuristic is labelled "relevant" (D-2026-08-12-4:
semantic retrieval is M2N, and it is required rather than approximated).

**A writer.** No proposal, no task, no objective, no Working Context field, no
Markdown. Reading somebody's memory to answer a question must not change the
memory or the state, and the way to guarantee that is to call only the read
methods of two services that keep their writes behind separate ones.

Nothing is logged
-----------------

Not "logged carefully" — **not logged**. The mind and workspace packages emit no
log records either, and a component whose working material is a person's memory
is the worst place to start. :meth:`~..models.LocalContextPack.summary` exists
for the caller that wants a line in a journal, and it carries no content.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ..mind.errors import GlobalGrantMissing, MindError, RoleUnavailable, RoleUnconfigured
from ..runtime.identity import now_iso
from ..workspace.errors import WorkspaceError
from .errors import ContextBudgetInvalid, CurrentMessageInvalid, CurrentMessageOversize
from .kinds import (
    KIND_USER_INSTRUCTION,
    KIND_WORKING_STATE,
    OMIT_BUDGET_EXHAUSTED,
    OMIT_EXPLICIT_SECTION_MISSING,
    OMIT_GRANT_ABSENT,
    OMIT_NOT_IN_THIS_BUILD,
    OMIT_NO_ACTIVE_WORKSPACE,
    OMIT_SOURCE_ABSENT,
    OMIT_SOURCE_EMPTY,
    OMIT_SOURCE_UNREADABLE,
    SELECTION_EXPLICIT,
    SELECTION_RETRIEVED,
    SELECTION_STRUCTURAL,
    SELECTION_WHOLE,
    global_ref,
    project_ref,
    user_ref,
    working_context_ref,
)
from .models import (
    ContextBudget,
    ContextOmission,
    ContextPart,
    LocalContextPack,
    RetrievedCandidate,
    SectionRef,
    encoded_length,
)
from .policy import (
    APPEND_ORDERED_ROLES,
    DEFAULT_TOTAL_BUDGET_BYTES,
    EVALUATION_SLOT_REF,
    EXPLICIT_REFERENCE_FIELDS,
    GLOBAL_CONTEXT_ROLES,
    PROJECT_CONTEXT_ROLES,
    ROLE_KINDS,
    SOURCE_CAPS,
)
from .sections import Section, find_section, reference_slug, split_sections

#: A message is authored text a person just typed. Bounded far above any real
#: one so that the bound is a sanity limit rather than a policy, and refused
#: rather than trimmed when it is exceeded — the rule the workspace objective
#: and the proposal reason already follow.
MAX_MESSAGE_BYTES = 256 * 1024

#: The Working Context lines, in the order they are rendered, paired with the
#: label a reader sees. Absent values produce **no line at all**: a rendering
#: that printed `objective: None` would be inventing a value where PR1 was
#: careful to record that there is not one.
_WORKING_CONTEXT_LINES: Tuple[Tuple[str, str], ...] = (
    ("workspace", "workspace_id"),
    ("project", "project_id"),
    ("delegated worker", "delegated_worker"),
    ("objective", "objective"),
    ("objective set at", "objective_set_at"),
    ("active task", "active_task_line"),
    ("plan checkpoint", "plan_checkpoint"),
    ("pending decision", "pending_decision_ref"),
    ("latest evidence", "latest_evidence_ref"),
    ("expected next step", "expected_next_step"),
)


class ContextBuilder:
    """Assembles a :class:`~.models.LocalContextPack`. Reads only.

    Takes the two services rather than their stores, for the reason
    :class:`~..mind.service.MindService` takes the workspace *service*: a second
    resolver is a second place for the containment walk, the grant check and the
    derived-not-stored rule to be forgotten.

    ``clock`` is injectable so a test can freeze observation time and assert
    that two builds over unchanged inputs are identical. It is the only
    non-determinism in the component.
    """

    def __init__(
        self,
        *,
        workspaces,
        mind,
        clock: Callable[[], str] = now_iso,
        total_budget_bytes: int = DEFAULT_TOTAL_BUDGET_BYTES,
    ) -> None:
        self._workspaces = workspaces
        self._mind = mind
        self._clock = clock
        self._total = _valid_budget(total_budget_bytes)

    # -- the one public method ------------------------------------------------

    def build(
        self,
        current_message: object,
        *,
        budget_bytes: Optional[int] = None,
        candidates: Sequence[RetrievedCandidate] = (),
    ) -> LocalContextPack:
        """Assemble the pack, in priority order, under one budget.

        The order of operations is the design:

        1. the budget is validated, so every later bound is a real number;
        2. the message is validated and measured, and a message that does not
           fit **refuses here** — before a single document is read, so an
           oversize message never causes memory to be touched at all;
        3. each source below it is offered the remaining budget in order, and
           each either becomes a part or becomes an omission with a reason.

        Step 3 has no path where a source is silently skipped. Every source in
        the policy is visited, and every visit ends in one of the two lists.
        """
        total = _valid_budget(self._total if budget_bytes is None else budget_bytes)
        message = _valid_message(current_message)
        observed = self._clock()

        used = encoded_length(message)
        if used > total:
            raise CurrentMessageOversize(used, total)

        parts: List[ContextPart] = [
            ContextPart(
                source_kind=KIND_USER_INSTRUCTION,
                source_ref=user_ref(),
                observed_at=observed,
                selection=SELECTION_WHOLE,
                text=message,
            )
        ]
        omissions: List[ContextOmission] = []

        snapshot = self._workspaces.current()
        workspace = snapshot.get("workspace") or {}
        workspace_id = workspace.get("workspace_id") if snapshot.get("active") else None
        project_id = workspace.get("project_id") if snapshot.get("active") else None

        if workspace_id is None:
            self._omit_everything(omissions, snapshot.get("problem"))
            used += self._append_candidates(parts, omissions, candidates, observed, total - used)
            return self._pack(observed, None, None, parts, omissions, total, used)

        working = snapshot.get("working_context") or {}

        used += self._append_working_context(
            parts, omissions, workspace, working, observed, total - used
        )
        for role in PROJECT_CONTEXT_ROLES:
            used += self._append_role(
                parts,
                omissions,
                scope="project",
                role=role,
                reference=project_ref(project_id, role),
                cap=SOURCE_CAPS["project:" + role],
                remaining=total - used,
                observed=observed,
                explicit=working.get(EXPLICIT_REFERENCE_FIELDS.get(role, "")),
                project_id=project_id,
            )

        omissions.append(
            ContextOmission(
                source_ref=EVALUATION_SLOT_REF,
                reason=OMIT_NOT_IN_THIS_BUILD,
                detail="evidence and evaluation are M2K and do not exist in this build",
            )
        )

        for role in GLOBAL_CONTEXT_ROLES:
            used += self._append_role(
                parts,
                omissions,
                scope="global",
                role=role,
                reference=global_ref(role),
                cap=SOURCE_CAPS["global:" + role],
                remaining=total - used,
                observed=observed,
                explicit=None,
                project_id=None,
            )

        used += self._append_candidates(parts, omissions, candidates, observed, total - used)
        return self._pack(observed, workspace_id, project_id, parts, omissions, total, used)

    # -- sources --------------------------------------------------------------

    def _omit_everything(self, omissions: List[ContextOmission], problem: Optional[str]) -> None:
        """No workspace, so nothing below the message has an authority.

        Each source still gets its own row. A single "no workspace" line would
        be shorter and would make a consumer work out for itself which six
        things are therefore missing.
        """
        detail = problem or "no_active_workspace"
        omissions.append(
            ContextOmission(
                source_ref="workspace:none:working_context",
                reason=OMIT_NO_ACTIVE_WORKSPACE,
                source_kind=KIND_WORKING_STATE,
                detail=detail,
            )
        )
        for role in PROJECT_CONTEXT_ROLES:
            omissions.append(
                ContextOmission(
                    source_ref=project_ref("none", role),
                    reason=OMIT_NO_ACTIVE_WORKSPACE,
                    source_kind=ROLE_KINDS[role],
                    detail=detail,
                )
            )
        omissions.append(
            ContextOmission(
                source_ref=EVALUATION_SLOT_REF,
                reason=OMIT_NOT_IN_THIS_BUILD,
                detail="evidence and evaluation are M2K and do not exist in this build",
            )
        )
        for role in GLOBAL_CONTEXT_ROLES:
            omissions.append(
                ContextOmission(
                    source_ref=global_ref(role),
                    reason=OMIT_NO_ACTIVE_WORKSPACE,
                    source_kind=ROLE_KINDS[role],
                    detail=detail,
                )
            )

    def _append_working_context(
        self,
        parts: List[ContextPart],
        omissions: List[ContextOmission],
        workspace: Dict[str, Any],
        working: Dict[str, Any],
        observed: str,
        remaining: int,
    ) -> int:
        """Cofferdam's own state, structured and rendered from the same values.

        ``fields`` carries the values as they are, ``None`` included, because
        "there is no objective" is a fact a consumer must be able to read. The
        rendered ``text`` omits those lines entirely rather than printing a
        placeholder, so the budgeted content never claims a value that is not
        there.
        """
        reference = working_context_ref(workspace["workspace_id"])
        fields = _working_context_fields(workspace, working)
        text = _render_working_context(fields)
        if not text.strip():
            omissions.append(
                ContextOmission(
                    source_ref=reference,
                    reason=OMIT_SOURCE_EMPTY,
                    source_kind=KIND_WORKING_STATE,
                    detail="nothing has been recorded for this workspace yet",
                )
            )
            return 0

        limit = min(SOURCE_CAPS["workspace:working_context"], remaining)
        bounded, truncated = _bounded_text(text, limit)
        if not bounded.strip():
            omissions.append(
                ContextOmission(
                    source_ref=reference,
                    reason=OMIT_BUDGET_EXHAUSTED,
                    source_kind=KIND_WORKING_STATE,
                )
            )
            return 0

        parts.append(
            ContextPart(
                source_kind=KIND_WORKING_STATE,
                source_ref=reference,
                observed_at=observed,
                selection=SELECTION_WHOLE,
                text=bounded,
                truncated=truncated,
                fields=fields,
            )
        )
        return encoded_length(bounded)

    def _append_role(
        self,
        parts: List[ContextPart],
        omissions: List[ContextOmission],
        *,
        scope: str,
        role: str,
        reference: str,
        cap: int,
        remaining: int,
        observed: str,
        explicit: object,
        project_id: Optional[str],
    ) -> int:
        """Read one role, select from it, bound it, and record what happened.

        Every refusal the mind or the workspace can raise is turned into an
        omission rather than propagated. A pack must describe a host that has
        not mapped a role, has revoked a grant or has disabled a project — those
        are ordinary states, and the same reasoning makes
        ``GET /api/workspace/current`` answer 200 for all three.
        """
        kind = ROLE_KINDS[role]

        try:
            payload = self._mind.read_document(scope, role)
        except GlobalGrantMissing:
            omissions.append(
                ContextOmission(
                    source_ref=reference,
                    reason=OMIT_GRANT_ABSENT,
                    source_kind=kind,
                    detail="the global vault is not granted on this host",
                )
            )
            return 0
        except RoleUnconfigured:
            omissions.append(
                ContextOmission(
                    source_ref=reference,
                    reason=OMIT_SOURCE_ABSENT,
                    source_kind=kind,
                    detail="no document is mapped to this role",
                )
            )
            return 0
        except RoleUnavailable:
            omissions.append(
                ContextOmission(
                    source_ref=reference,
                    reason=OMIT_SOURCE_UNREADABLE,
                    source_kind=kind,
                    detail="the mapped document cannot be read right now",
                )
            )
            return 0
        except WorkspaceError:
            omissions.append(
                ContextOmission(
                    source_ref=reference,
                    reason=OMIT_SOURCE_ABSENT,
                    source_kind=kind,
                    detail="the workspace or its project is not available",
                )
            )
            return 0
        except MindError:
            omissions.append(
                ContextOmission(
                    source_ref=reference,
                    reason=OMIT_SOURCE_UNREADABLE,
                    source_kind=kind,
                    detail="the role did not resolve to a readable document",
                )
            )
            return 0

        content = payload.get("content") or ""
        if not content.strip():
            omissions.append(
                ContextOmission(
                    source_ref=reference,
                    reason=OMIT_SOURCE_EMPTY,
                    source_kind=kind,
                    detail="the mapped document has no content",
                )
            )
            return 0

        sections = split_sections(content)
        wanted = reference_slug(explicit)

        if wanted is not None:
            chosen = find_section(sections, wanted) if sections else None
            if chosen is None:
                omissions.append(
                    ContextOmission(
                        source_ref=reference,
                        reason=OMIT_EXPLICIT_SECTION_MISSING,
                        source_kind=kind,
                        detail="the recorded reference names a section this document does not have",
                    )
                )
                return 0
            return self._append_selected(
                parts,
                omissions,
                kind=kind,
                reference=_with_section(scope, role, project_id, chosen.section_id),
                observed=observed,
                selection=SELECTION_EXPLICIT,
                text=chosen.body,
                section=SectionRef(
                    section_id=chosen.section_id,
                    heading=chosen.heading,
                    level=chosen.level,
                    ordinal=chosen.ordinal,
                ),
                limit=min(cap, remaining),
            )

        text = _structural_text(sections, content, min(cap, remaining), tail=role in APPEND_ORDERED_ROLES)
        return self._append_selected(
            parts,
            omissions,
            kind=kind,
            reference=reference,
            observed=observed,
            selection=SELECTION_STRUCTURAL,
            text=text,
            section=None,
            limit=min(cap, remaining),
            whole=content,
        )

    def _append_selected(
        self,
        parts: List[ContextPart],
        omissions: List[ContextOmission],
        *,
        kind: str,
        reference: str,
        observed: str,
        selection: str,
        text: str,
        section: Optional[SectionRef],
        limit: int,
        whole: Optional[str] = None,
    ) -> int:
        """Bound a selection, mark it if it was cut, or say there was no room."""
        bounded, truncated = _bounded_text(text, limit)
        if not bounded.strip():
            omissions.append(
                ContextOmission(
                    source_ref=reference,
                    reason=OMIT_BUDGET_EXHAUSTED,
                    source_kind=kind,
                    detail="no budget remained when this source's turn came",
                )
            )
            return 0
        if whole is not None and not truncated:
            # Structural selection may have dropped whole sections before this
            # point without the final slice being cut. Partial is partial.
            truncated = encoded_length(bounded) < encoded_length(whole)
        parts.append(
            ContextPart(
                source_kind=kind,
                source_ref=reference,
                observed_at=observed,
                selection=selection,
                text=bounded,
                truncated=truncated,
                section=section,
            )
        )
        return encoded_length(bounded)

    def _append_candidates(
        self,
        parts: List[ContextPart],
        omissions: List[ContextOmission],
        candidates: Iterable[RetrievedCandidate],
        observed: str,
        remaining: int,
    ) -> int:
        """The M2N seam, exercised the same way as everything else.

        No producer in this build supplies candidates. When one does, its
        material arrives here already carrying provenance, is validated by
        :class:`~.models.ContextPart` like any other part, and is budgeted last.
        A retrieval component therefore cannot widen what a pack contains
        without going through the same accounting — which is the property worth
        building the seam for.
        """
        used = 0
        for candidate in candidates:
            if not isinstance(candidate, RetrievedCandidate):
                raise TypeError("a retrieval candidate must be a RetrievedCandidate")
            if not candidate.text.strip():
                omissions.append(
                    ContextOmission(
                        source_ref=candidate.source_ref,
                        reason=OMIT_SOURCE_EMPTY,
                        source_kind=candidate.source_kind,
                    )
                )
                continue
            used += self._append_selected(
                parts,
                omissions,
                kind=candidate.source_kind,
                reference=candidate.source_ref,
                observed=observed,
                selection=SELECTION_RETRIEVED,
                text=candidate.text,
                section=candidate.section,
                limit=min(SOURCE_CAPS["retrieved"], remaining - used),
            )
        return used

    # -- assembly -------------------------------------------------------------

    @staticmethod
    def _pack(
        observed: str,
        workspace_id: Optional[str],
        project_id: Optional[str],
        parts: List[ContextPart],
        omissions: List[ContextOmission],
        total: int,
        used: int,
    ) -> LocalContextPack:
        counted = sum(part.content_bytes for part in parts)
        if counted != used or counted > total:  # pragma: no cover - an accounting bug
            raise AssertionError(
                "context budget accounting disagreed with the parts it produced"
            )
        return LocalContextPack(
            built_at=observed,
            workspace_id=workspace_id,
            project_id=project_id,
            parts=tuple(parts),
            omissions=tuple(omissions),
            budget=ContextBudget(total=total, consumed=counted),
        )


# -- validation ---------------------------------------------------------------


def _valid_budget(value: object) -> int:
    """A positive whole number of bytes. Booleans are not integers here."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContextBudgetInvalid()
    return value


def _valid_message(value: object) -> str:
    """The person's own words, unchanged. Validated, never normalised.

    Whitespace is **not** stripped and the text is **not** collapsed: this is
    the one part of the pack whose bytes must match what was typed, so the only
    checks here are the ones that decide whether a pack can exist at all.
    """
    if not isinstance(value, str):
        raise CurrentMessageInvalid("expected the message as text")
    if not value.strip():
        raise CurrentMessageInvalid("there is nothing in the message")
    if "\x00" in value:
        raise CurrentMessageInvalid("a message is text")
    if encoded_length(value) > MAX_MESSAGE_BYTES:
        raise CurrentMessageInvalid(
            "at most " + str(MAX_MESSAGE_BYTES) + " bytes, and this is much more than a message"
        )
    return value


# -- text handling ------------------------------------------------------------


def _bounded_text(text: str, limit: int) -> Tuple[str, bool]:
    """Cut to at most ``limit`` UTF-8 bytes, never through a character.

    The slice is taken on the encoded bytes and decoded with ``errors="ignore"``,
    which discards exactly the trailing partial sequence and nothing else for
    input that is already valid UTF-8 — which it is, because
    :meth:`MindService.read_document` refuses anything that is not.
    """
    if limit <= 0:
        return "", True
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def _structural_text(
    sections: Tuple[Section, ...], content: str, limit: int, *, tail: bool
) -> str:
    """A bounded slice of a document, chosen by position and nothing else.

    Whole sections are taken until the next one would not fit, from the top for
    an ordinary document and from the **end** for an append-ordered one, where
    "recent" means the newest. Output is always in document order regardless of
    which end it was gathered from.

    If not even one section fits, the text is cut mid-section by the caller and
    marked truncated. Nothing is summarised, reordered or rewritten.
    """
    if limit <= 0:
        return ""
    if not sections:
        return content

    ordered = list(reversed(sections)) if tail else list(sections)
    taken: List[Section] = []
    used = 0
    for section in ordered:
        size = encoded_length(section.body)
        if taken and used + size > limit:
            break
        taken.append(section)
        used += size
        if used >= limit:
            break

    if tail:
        taken.reverse()
    return "".join(section.body for section in taken)


def _with_section(
    scope: str, role: str, project_id: Optional[str], section_id: str
) -> str:
    if scope == "project":
        return project_ref(project_id or "none", role, section_id)
    return global_ref(role, section_id)


def _working_context_fields(
    workspace: Dict[str, Any], working: Dict[str, Any]
) -> Dict[str, Any]:
    """The Working Context as values, with absence preserved as absence.

    The derived half stays derived. ``delegated_worker`` is read from the
    snapshot, which reads it from the project on every request — a copy here
    would be the second adapter authority PR #34 removed. ``active_task`` is
    likewise whatever Task Core said a moment ago, or ``None``.
    """
    task = working.get("active_task")
    return {
        "workspace_id": workspace.get("workspace_id"),
        "project_id": workspace.get("project_id"),
        "delegated_worker": workspace.get("delegated_worker"),
        "delegation": workspace.get("delegation"),
        "objective": working.get("objective"),
        "objective_set_at": working.get("objective_set_at"),
        "objective_source": working.get("objective_source"),
        "active_task": dict(task) if task else None,
        "plan_checkpoint": working.get("plan_checkpoint"),
        "pending_decision_ref": working.get("pending_decision_ref"),
        "latest_evidence_ref": working.get("latest_evidence_ref"),
        "expected_next_step": working.get("expected_next_step"),
        "revision": working.get("revision"),
    }


def _render_working_context(fields: Dict[str, Any]) -> str:
    """One line per recorded value. **Absent values produce no line.**"""
    task = fields.get("active_task")
    values = dict(fields)
    values["active_task_line"] = (
        None
        if not task
        else str(task.get("task_id"))
        + " ("
        + str(task.get("status"))
        + ("" if not task.get("state") else ", " + str(task.get("state")))
        + ")"
    )
    lines = [
        label + ": " + str(values[key])
        for label, key in _WORKING_CONTEXT_LINES
        if values.get(key) is not None
    ]
    return "\n".join(lines)


__all__ = ["MAX_MESSAGE_BYTES", "ContextBuilder"]
