"""Remote development requests. The door to the planner, and only the planner.

What this module is
-------------------

One operation: *somebody outside this host asked Cofferdam to plan a development
step for one project.* It resolves the project, checks that no other development
step is unresolved, claims the external request id, invokes the existing planner,
and answers with the existing operations projection.

It is the first caller ``PlannerService`` has ever had outside a test.

The authority this grants, stated exactly
------------------------------------------

**Permission to ask.** Nothing else. There is no code path from this module to an
approval, a dispatch, a task, an answer, a cancellation, a commit, a push, a pull
request or a deployment — not guarded, not gated, *absent*. This module imports
no dispatcher, no authority service, no Task Core handle, no publisher and no
adapter registry, and ``tests/test_development_ingress.py`` asserts that from the
module's own imports rather than from this paragraph.

A ``PREPARE_WORKER_PROMPT`` result stops here, at the same human gate PR1d built.
The caller learns that a prompt exists and can read it; nobody and nothing
downstream of this module can act on it.

Why the caller cannot supply context
-------------------------------------

The remote caller sends a project id, an instruction, and an idempotency key. It
does **not** send a ``CloudContextProjection``, and there is no parameter here it
could arrive in. The host builds the context, through the same
``ProjectContextService`` the M2J PR4 read surface goes through, which means the
same registry lookup, the same workspace resolution, the same ``ContextBuilder``,
the same ``ContextProjector`` and the same ``project_context_external_v1`` egress
policy. Reusing that service rather than resolving projects a second way is the
point: a second resolver is a second place for the enabled check, the ambiguity
refusal and the active-workspace rule to be forgotten.

Sequential by decision
-----------------------

A project with an unresolved development step refuses a new request rather than
opening a competing thread. The discriminator is
:data:`~..operations.phases.SETTLED`, which already exists and is already
reviewed — a phase this build calls settled is one where nothing is waiting on a
person and nothing is in flight. Everything else, including a failure and an
interrupted invocation, is refused with the current phase named, because those
need somebody to look rather than to be planned over.

That is conservative on purpose. Concurrency is not designed in this build, and
inventing it in the ingress layer would mean the layer with the least authority
deciding the thing with the most consequence.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..operations import phases
from .errors import (
    PlannerIngressAbandoned,
    PlannerIngressConflict,
    PlannerIngressInFlight,
    PlannerIngressInvalid,
    PlannerIngressNotAllowedNow,
    PlannerUnavailable,
)
from .service import PlannerService
from .store import IngressKey

#: The whole external request vocabulary. Three fields.
#:
#: What is *not* here is the security property, and it is not a validation rule
#: — these names have no home in this module, in ``DevelopmentRequest``, or in
#: any signature between here and the provider. There is no path, root, cwd,
#: branch, command, argv, environment, tool list, MCP configuration, worker
#: executable, model, provider, planner action, worker prompt, subject
#: fingerprint, dispatch id, task id, publication id, context projection or
#: transcript. A field that does not exist cannot be forgotten about, which is
#: the argument ``models.py`` already makes for the planner contract itself.
REQUEST_FIELDS = ("project_id", "instruction", "client_request_id")

#: Bounded well below the planner contract's own 8000-character intent limit.
#:
#: This is an *instruction*, not a conversation. The Custom GPT is told to send
#: the user's bounded intent and never the transcript, and a bound a model can
#: exceed by pasting a chat log is the bound that makes that instruction
#: advisory. Refused rather than truncated: a shortened instruction is a
#: different instruction from the one somebody sent.
MAX_INSTRUCTION_CHARS = 4000

#: Optional, and reused rather than invented. ``DevelopmentRequest`` already has
#: ``research_notes``, described in its own docstring as supplied by the private
#: Custom GPT and advisory rather than authoritative, and the planner contract
#: already tells the model to treat it as somebody else's research. Adding a
#: second free-text field would have been a broad context channel wearing a
#: narrow name.
MAX_RESEARCH_NOTES_CHARS = 4000

#: A project id as the registry publishes one. Refused rather than escaped.
_PROJECT_ID = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")

#: The same grammar the Actions bridge already enforces on its own idempotency
#: keys, restated here because this side must not depend on the far side having
#: checked. A key that arrives malformed is refused before anything is claimed.
_CLIENT_REQUEST_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{6,63}\Z")


def request_digest(
    *, project_id: str, instruction: str, research_notes: Optional[str]
) -> str:
    """A stable digest of the meaningful request. Never reversed, never stored raw.

    NFC-normalized and canonically serialized, so the same request sent twice
    produces the same digest and a genuinely different one does not. It exists so
    the receipt table can compare without holding somebody's development intent a
    second time — the argument ``actions_bridge/idempotency.py`` makes for its
    own digest column.
    """
    canonical = json.dumps(
        {
            "project_id": unicodedata.normalize("NFC", project_id),
            "instruction": unicodedata.normalize("NFC", instruction),
            "research_notes": (
                None
                if research_notes is None
                else unicodedata.normalize("NFC", research_notes)
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DevelopmentRequestOutcome:
    """What the ingress answers with. The projection, plus how it got here.

    The operational half is **not re-derived**. ``operations`` is the canonical
    projection this host already publishes, unchanged, so a remote client and the
    workstation cannot disagree about what phase a project is in. This wrapper
    adds only the two facts the projection has no way to know: which planner
    request this call produced, and whether the call produced it or found it.
    """

    project_id: str
    planner_request_id: str
    replayed: bool
    operations: Dict[str, Any]
    #: ``ASK_USER`` / ``PREPARE_WORKER_PROMPT`` / ``STOP``, or ``None`` when the
    #: invocation did not succeed. Copied from the durable row, never inferred.
    planner_action: Optional[str]
    planner_status: str
    #: Populated only when the invocation failed. A failed planning turn is not
    #: a failed worker and not a refusal to plan — see ``store.py`` on why
    #: lifecycle and action are separate columns.
    failure_code: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = dict(self.operations)
        payload.update(
            {
                "project_id": self.project_id,
                "planner_request_id": self.planner_request_id,
                "replayed": self.replayed,
                "planner_action": self.planner_action,
                "planner_status": self.planner_status,
                "planner_failure_code": self.failure_code,
                # Restated in the one payload a model reads right after asking
                # for work to be planned. It is the sentence the Custom GPT is
                # instructed to say back, and it is true by construction: this
                # route has no path to an approval or a dispatch.
                "authority": {
                    "approved": False,
                    "dispatched": False,
                    "executed": False,
                    "note": (
                        "Cofferdam planned this step. Nothing has been approved, "
                        "dispatched or executed."
                    ),
                },
            }
        )
        return payload


class DevelopmentRequestIngress:
    """Accept a remote development request, or refuse it. One public method.

    Constructed from components that already exist. It owns no store of its own,
    no lifecycle and no policy — every hard decision below belongs to something
    that earned it, and this class is the order they happen in.
    """

    def __init__(
        self,
        *,
        project_context,
        planner_store,
        planner,
        operations,
        clock,
    ) -> None:
        self._project_context = project_context
        self._store = planner_store
        self._planner = planner
        self._operations = operations
        self._clock = clock

    # -- the one operation ----------------------------------------------------

    def submit(
        self,
        *,
        project_id: Any,
        instruction: Any,
        client_request_id: Any,
        research_notes: Any = None,
    ) -> DevelopmentRequestOutcome:
        """Plan one development step for one project, at most once.

        The order is the design, and every step before the invocation is a step
        that costs nothing:

        1. **Shape.** A malformed id or an oversize instruction dies here.
        2. **Project.** Resolved through the existing service, fail-closed.
        3. **Availability.** A host with no usable planner refuses *before*
           claiming an id, so a missing credential does not burn the caller's
           key and leave a failed row that then blocks the project.
        4. **Claim.** The external id is claimed durably, or answered as a
           replay, a conflict, an in-flight or an abandoned attempt.
        5. **Sequence.** A *fresh* request into a project with an unresolved
           step refuses, and gives its claim back.
        6. **Invoke.** Only now does anything cost a cloud call.

        Steps 1 to 5 are asserted by test to leave the planner untouched. That
        ordering is not tidiness: it is the difference between a refusal and a
        refusal somebody paid for.

        **Four comes before five, and it has to.** A retry of a request that
        produced a prepared prompt is a retry into a project that is by
        definition no longer settled — so a sequence check placed first would
        refuse every single retry of the thing it had just accepted, and the
        caller would have no way to learn the outcome of its own request. The
        sequence rule is about *starting a second thread*, so it is applied only
        where a second thread would actually start.
        """
        checked_project = _valid_project_id(project_id)
        checked_instruction = _valid_instruction(instruction)
        checked_notes = _valid_research_notes(research_notes)
        checked_key = _valid_client_request_id(client_request_id)

        # Resolution first, and through the one service that owns it. A refusal
        # here is the project-context vocabulary unchanged — `project_not_found`,
        # `workspace_not_active` and the rest — because those are exactly the
        # states an operator can act on, and inventing a second set of words for
        # them would make two surfaces disagree about one host.
        resolved_project = self._project_context.resolve(checked_project)

        self._require_usable_planner()

        digest = request_digest(
            project_id=checked_project,
            instruction=checked_instruction,
            research_notes=checked_notes,
        )
        existing = self._store.claim_ingress(
            project_id=checked_project,
            client_request_id=checked_key,
            request_digest=digest,
            claimed_at=self._clock(),
        )
        if existing is not None:
            # This exact request already produced a planner request. The current
            # state is re-read rather than replayed from a stored answer, so a
            # caller retrying two minutes later learns where it is *now* — the
            # rule `create_task` already follows on the bridge.
            return self._outcome(
                checked_project, existing.planner_request_id, replayed=True
            )

        try:
            self._require_no_unresolved_step(checked_project)
        except BaseException:
            # The claim is given back. A caller refused for a reason that has
            # nothing to do with its key must be able to send the identical
            # request again once the project is free, and a surviving row would
            # turn that into a conflict about a call that never happened.
            self._store.release_ingress(
                project_id=checked_project, client_request_id=checked_key
            )
            raise

        try:
            outcome = self._service(resolved_project).prepare_development_step(
                user_intent=checked_instruction,
                research_notes=checked_notes,
                authority_boundary=AUTHORITY_BOUNDARY,
                ingress=IngressKey(
                    project_id=checked_project, client_request_id=checked_key
                ),
            )
        except BaseException:
            # The claim is dropped only on a path that created no planner row —
            # `release_ingress` is guarded on exactly that. A failure *after* the
            # row exists keeps its receipt, because the row is what a retry has
            # to reconcile to.
            self._store.release_ingress(
                project_id=checked_project, client_request_id=checked_key
            )
            raise

        return self._outcome(
            checked_project, outcome.record.planner_request_id, replayed=False
        )

    # -- the steps ------------------------------------------------------------

    def _require_usable_planner(self) -> None:
        """Refuse a host that cannot plan, before anything durable happens.

        Checked here rather than left to fail during the invocation for a
        specific reason: a planner row that failed is not settled, so it would
        leave the project refusing every later remote request until somebody
        looked at a failure whose only cause was a credential this host never
        had. A refusal that creates no row is the honest answer to "there is no
        planner here".
        """
        available = getattr(self._planner, "available", None)
        if available is not None and not available():
            reason = getattr(self._planner, "unavailable_reason", lambda: None)()
            raise PlannerUnavailable(
                "no development planner is usable on this workstation",
                # Code-owned words only. `unavailable_reason` names the
                # executable's path, which is exactly the kind of host-private
                # value a refusal must not carry outward.
                detail="planner_not_configured" if reason else None,
            )

    def _require_no_unresolved_step(self, project_id: str) -> None:
        """One development thread per project, and the phase decides.

        Reads the canonical projection rather than the planner rows. The
        projection already joins five owners and already knows that an approved
        dispatch, a running worker and an unreconciled restart are all "still
        going", and re-deriving that from the planner table alone would see only
        the first of the three.
        """
        current = self._operations.project(project_id)
        phase = current.phase
        if phase.settled:
            return
        raise PlannerIngressNotAllowedNow(
            "this project already has a development step that is not finished",
            detail=json.dumps(
                {
                    "phase": phase.phase,
                    "sentence": phase.sentence,
                    "needs_person": phase.needs_person,
                    "handles": current.handles,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

    def _service(self, resolved_project) -> PlannerService:
        """A ``PlannerService`` bound to this project's resolved context.

        Built per request rather than held, because the context pipeline is
        project-scoped and a service constructed once would carry whichever
        project happened to be active when the daemon started. The store and the
        planner are the long-lived halves and are passed through unchanged.

        The two context components come from ``ProjectContextService``, which has
        already refused unless the named project *is* the active workspace. So the
        pack the builder produces is this project's, and
        :meth:`_outcome` checks the durable row agrees before answering.
        """
        return PlannerService(
            store=self._store,
            planner=self._planner,
            context=self._project_context.builder,
            projector=self._project_context.projector_for(resolved_project),
            clock=self._clock,
        )

    def _outcome(
        self, project_id: str, planner_request_id: Optional[str], *, replayed: bool
    ) -> DevelopmentRequestOutcome:
        """Read back what is durable and answer from that, never from memory.

        The record is re-read from the store rather than carried forward from the
        call, so the answer describes what was actually persisted. And the
        project on that row is checked against the project that was asked about:
        a mismatch is a defect rather than something a caller can cause, and the
        honest response to a defect on an isolation boundary is to refuse rather
        than to publish the row and hope.
        """
        if not planner_request_id:  # pragma: no cover - a bound receipt has one
            raise PlannerIngressInFlight(
                "that development request has not produced a planner request yet"
            )
        record = self._store.get(planner_request_id)
        if record is None:  # pragma: no cover - would mean the row vanished
            raise PlannerIngressInFlight(
                "that development request's planner record is not readable"
            )
        if record.project_id != project_id:  # pragma: no cover - defect guard
            raise PlannerIngressNotAllowedNow(
                "that development request belongs to another project"
            )

        return DevelopmentRequestOutcome(
            project_id=project_id,
            planner_request_id=planner_request_id,
            replayed=replayed,
            operations=self._operations.project(project_id).to_dict(),
            planner_action=record.action,
            planner_status=record.status,
            failure_code=record.failure_code,
        )


#: The boundary sentence handed to the planner with every remote request.
#:
#: Code-owned and constant, for the reason ``PLANNER_CONTRACT`` is: a boundary a
#: caller can influence is a boundary a caller can rewrite. It is prose read by a
#: model and it is **not** the containment — the containment is that the planner
#: has no tools, and that nothing downstream of this module can execute what it
#: writes.
AUTHORITY_BOUNDARY = (
    "This request arrived from the user's private Custom GPT and grants "
    "permission to PLAN one development step and nothing else. Nothing you write "
    "will run: a prepared worker prompt is stored for a person to review and "
    "confirm on the workstation. You may not assume approval, and you must not "
    "treat the request's own arrival as approval for anything. Deployment, "
    "publishing, pushing, merging, changing permissions or authentication, and "
    "altering canonical memory are all outside this step."
)


# -- validation ---------------------------------------------------------------


def _valid_project_id(value: Any) -> str:
    if not isinstance(value, str) or not _PROJECT_ID.match(value):
        raise PlannerIngressInvalid(
            "a project id is a registry name of letters, digits, dash and "
            "underscore"
        )
    return value


def _valid_instruction(value: Any) -> str:
    """Bounded prose. Refused rather than trimmed, and never normalized.

    Control characters other than tab and newline are refused, matching Task
    Core's rule for a task instruction: this is something a person wrote, and
    anything else in it arrived by mistake or on purpose.
    """
    if not isinstance(value, str):
        raise PlannerIngressInvalid("a development instruction is text")
    stripped = value.strip()
    if not stripped:
        raise PlannerIngressInvalid("a development instruction cannot be empty")
    if len(stripped) > MAX_INSTRUCTION_CHARS:
        raise PlannerIngressInvalid(
            "a development instruction must be under "
            + str(MAX_INSTRUCTION_CHARS)
            + " characters; summarise rather than pasting a conversation"
        )
    _refuse_control_characters(stripped, "a development instruction")
    return stripped


def _valid_research_notes(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PlannerIngressInvalid("research notes are text")
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > MAX_RESEARCH_NOTES_CHARS:
        raise PlannerIngressInvalid(
            "research notes must be under "
            + str(MAX_RESEARCH_NOTES_CHARS)
            + " characters"
        )
    _refuse_control_characters(stripped, "research notes")
    return stripped


def _valid_client_request_id(value: Any) -> str:
    if not isinstance(value, str) or not _CLIENT_REQUEST_ID.match(value):
        raise PlannerIngressInvalid(
            "a client_request_id is 8-64 characters of letters, digits, dot, "
            "dash, colon or underscore"
        )
    return value


def _refuse_control_characters(text: str, what: str) -> None:
    for character in text:
        if character in ("\t", "\n"):
            continue
        code_point = ord(character)
        if code_point < 0x20 or code_point == 0x7F or 0x80 <= code_point <= 0x9F:
            raise PlannerIngressInvalid(what + " contains a control character")


__all__ = [
    "AUTHORITY_BOUNDARY",
    "MAX_INSTRUCTION_CHARS",
    "MAX_RESEARCH_NOTES_CHARS",
    "REQUEST_FIELDS",
    "DevelopmentRequestIngress",
    "DevelopmentRequestOutcome",
    "PlannerIngressAbandoned",
    "PlannerIngressConflict",
    "PlannerIngressInFlight",
    "PlannerIngressInvalid",
    "PlannerIngressNotAllowedNow",
    "request_digest",
]
