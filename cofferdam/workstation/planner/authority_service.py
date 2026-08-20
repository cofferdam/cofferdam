"""Recording what a person decided. Nothing else, and structurally nothing else.

Why this is a separate service
------------------------------

:class:`~.service.PlannerService` is constructed with a provider, a context
builder and a projector, because invoking a planner needs all three. This
service is constructed with **the store and a clock**, and that is the whole
point: it has no planner to call, no context to build and no projection to send.

"An answer does not trigger a replan" and "an approval does not dispatch" are
therefore not promises kept by a code path that carefully avoids doing something.
They are properties of the object's dependencies. There is no attribute on this
class through which a worker, a task, an adapter or a provider could be reached,
so recording a decision cannot start one — not because it does not, but because
there is nothing here to start.

What a decision is bound to
---------------------------

Every write goes through the same steps, in this order:

1. read the durable planner result;
2. derive the gate from it — :func:`~.authority.derive_gate` is the only mapping,
   so the persisted result determines the vocabulary and the caller does not;
3. compute the subject fingerprint from the persisted text and compare it with
   the one the caller **states** it is authorizing — a mismatch is a refusal;
4. only then, resolve an existing terminal decision (retry or conflict);
5. record the decision in one transaction, and read back the gate.

``expected_subject_fingerprint`` is a **required argument**, on every operation,
with no default and no "use whatever is current" fallback. That is the whole
difference between two properties that are easy to confuse:

*The stored event binds the subject that existed when the write happened.* That
is true from the fingerprint column alone, and it was true before this argument
was mandatory.

*The person intended to authorize the subject they were shown.* Only the caller
can assert that, by naming the digest it displayed. A caller that omits it is not
saying "I approve this text"; it is saying "I approve whatever is there", and the
gap between those two is exactly where a stale view becomes an approval nobody
gave.

Making it optional would put stale-view protection in the hands of every future
caller, forever, and this module is the canonical authority primitive — the place
that boundary should be enforced once rather than remembered repeatedly. So the
argument is required, ``None`` and the empty string are refused rather than
treated as "unspecified", and there is no code path that substitutes the current
value for a missing one.

Step 3 runs **before** step 4 on purpose. A retry does not bypass the check: an
approval resubmitted against a subject that has since moved is a stale view
whether or not a decision already exists, and short-circuiting on the existing
row would answer "already approved" to a caller looking at something else.

Terminal, and honest about it
-----------------------------

One decision per gate. A repeat of the *same* decision returns the existing state
truthfully rather than writing a second record — a double tap on a phone is one
approval, not two. A *contradicting* decision is refused: an approval that turned
into a rejection because a later request arrived is a history that lost the first
decision, and durability that can be overwritten by a retry is not durability.

Correcting a decision is a real need and is deliberately absent. It wants an
explicit superseding-authority workflow with its own record of who changed their
mind and when, and quietly reusing ``approve`` for it would produce exactly the
silent rewrite this milestone is built to prevent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .authority import (
    AUTHORITY_ANSWER,
    AUTHORITY_APPROVE,
    AUTHORITY_REJECT,
    GATE_ANSWER,
    GATE_CONFIRMATION,
    AuthorityProvenance,
    HumanGate,
    clean_answer,
    clean_rejection_reason,
    derive_gate,
    gate_subject,
    new_authority_event_id,
    valid_fingerprint,
)
from .errors import (
    PlannerAuthorityConflict,
    PlannerAuthorityInvalid,
    PlannerAuthorityRefused,
    PlannerAuthorityStale,
)
from .store import PlannerRecord, PlannerStore


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True)
class PlannerGateView:
    """One planner request and its human gate, side by side and still separate.

    Two keys, never merged. ``planner_request`` is what the model produced;
    ``human_gate`` is what a person decided about it. A single flattened object
    would be the first step back towards writing one over the other.
    """

    record: PlannerRecord
    gate: HumanGate

    @property
    def planner_request_id(self) -> str:
        return self.record.planner_request_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "planner_request": self.record.to_dict(),
            "human_gate": self.gate.to_dict(),
        }


class PlannerAuthorityService:
    """The human authority gate. Records decisions; starts nothing."""

    def __init__(self, *, store: PlannerStore, clock=_utc_now) -> None:
        self._store = store
        self._clock = clock

    # -- reads ---------------------------------------------------------------

    def view(self, planner_request_id: str) -> Optional[PlannerGateView]:
        """The planner result and its gate, or ``None`` if there is no request."""
        record = self._store.get(planner_request_id)
        if record is None:
            return None
        return PlannerGateView(record=record, gate=self._gate(record))

    def gate(self, planner_request_id: str) -> Optional[HumanGate]:
        record = self._store.get(planner_request_id)
        return None if record is None else self._gate(record)

    def _gate(self, record: PlannerRecord) -> HumanGate:
        return derive_gate(
            record, event=self._store.authority_event(record.planner_request_id)
        )

    # -- writes --------------------------------------------------------------

    def answer_planner_question(
        self,
        planner_request_id: str,
        *,
        answer: str,
        expected_subject_fingerprint: str,
        provenance: AuthorityProvenance,
    ) -> HumanGate:
        """Record a person's answer to an ``ASK_USER`` question.

        ``expected_subject_fingerprint`` is required here for the same reason it
        is on an approval, and it is worth saying in the answer's own terms: an
        answer is authority for **this exact persisted question**, not for
        whatever question currently belongs to this request id. "Postgres"
        answers *which database should this use?*; attached to a different
        question it is a sentence with no meaning, recorded as though it had one.

        The answer is **semantic data and stays data**. It may hold prose, a code
        snippet, a URL or a line that reads exactly like a shell command; all of
        it is stored as text and read back as text. Nothing in this package
        parses it as argv, a provider flag, an MCP method, a path or an
        instruction to anything.

        Recording it does not invoke the planner again and does not create a new
        planner request — see this module's docstring for why that is a property
        of the object rather than a claim about this method.
        """
        return self._record(
            planner_request_id,
            authority_action=AUTHORITY_ANSWER,
            expected_gate_kind=GATE_ANSWER,
            provenance=provenance,
            expected_subject_fingerprint=expected_subject_fingerprint,
            answer_text=clean_answer(answer),
        )

    def approve_prepared_worker_prompt(
        self,
        planner_request_id: str,
        *,
        expected_subject_fingerprint: str,
        provenance: AuthorityProvenance,
    ) -> HumanGate:
        """Record that a person approves this exact prepared worker prompt.

        Approval means: *a person authorizes these exact bytes for potential use
        by a later bounded dispatch layer.* The word "these" is carried by
        ``expected_subject_fingerprint``, which is why it is required: without it
        the sentence degrades to "a person authorizes whatever prompt this
        request currently holds", which is not something anybody agreed to.

        It does not mean "run it now", and nothing here could — no task is
        created, no adapter is selected, no provider is started, no subprocess is
        spawned, no filesystem change occurs. What is produced is an authority
        record for a layer that does not exist yet.
        """
        return self._record(
            planner_request_id,
            authority_action=AUTHORITY_APPROVE,
            expected_gate_kind=GATE_CONFIRMATION,
            provenance=provenance,
            expected_subject_fingerprint=expected_subject_fingerprint,
        )

    def reject_prepared_worker_prompt(
        self,
        planner_request_id: str,
        *,
        expected_subject_fingerprint: str,
        provenance: AuthorityProvenance,
        reason: Optional[str] = None,
    ) -> HumanGate:
        """Record that a person does not authorize this prepared prompt.

        A refusal is authority too, and it is about a specific prompt. Requiring
        the fingerprint keeps it from becoming a standing objection that outlives
        what it objected to: a rejection recorded against text the person never
        saw would read, later, as a considered judgement of that text.

        Durable and terminal, and it does nothing else: no planner rerun, no new
        prompt, no task, no dispatch. Deciding what should happen next after a
        refusal is orchestration, and orchestration is a later layer's decision
        to make with this record in hand.
        """
        return self._record(
            planner_request_id,
            authority_action=AUTHORITY_REJECT,
            expected_gate_kind=GATE_CONFIRMATION,
            provenance=provenance,
            expected_subject_fingerprint=expected_subject_fingerprint,
            rejection_reason=clean_rejection_reason(reason),
        )

    # -- the one write path --------------------------------------------------

    def _record(
        self,
        planner_request_id: str,
        *,
        authority_action: str,
        expected_gate_kind: str,
        provenance: AuthorityProvenance,
        expected_subject_fingerprint: str,
        answer_text: Optional[str] = None,
        rejection_reason: Optional[str] = None,
    ) -> HumanGate:
        """Validate, bind, write, read back. Every refusal happens before a write.

        Nothing partial can be left behind: the checks all run against values
        already in hand, and the single ``INSERT`` that follows is the only
        mutation — so a refusal at any point leaves the gate exactly as it was,
        and a crash mid-write leaves no row rather than half of one.
        """
        if not isinstance(provenance, AuthorityProvenance):
            # Not coerced from a dict. Provenance is assigned by a trusted
            # surface from its own authenticated context; accepting a mapping
            # here is how a caller-chosen `source` eventually arrives.
            raise PlannerAuthorityInvalid("provenance must be an AuthorityProvenance")

        record = self._store.get(planner_request_id)
        if record is None:
            raise PlannerAuthorityRefused(
                "no planner request by that id", detail=planner_request_id
            )

        existing = self._store.authority_event(planner_request_id)
        gate = derive_gate(record, event=existing)

        if gate.kind != expected_gate_kind:
            # The persisted result decides what may be done to it. A STOP, a
            # failed or interrupted invocation, and a result whose action is the
            # other one all land here, each with its own reason.
            raise PlannerAuthorityRefused(
                self._refusal_message(gate, authority_action),
                detail=gate.no_gate_reason or gate.kind,
            )

        subject = gate_subject(record)
        fingerprint = gate.subject_fingerprint
        if subject is None or fingerprint is None:  # pragma: no cover - derive_gate
            raise PlannerAuthorityRefused("this planner result carries no subject")

        # Before the terminal-decision check, not after. A retry submitted
        # against a subject that has since moved is a stale view whether or not
        # somebody already decided, and answering "already approved" to a caller
        # looking at different text would be the same lie the check exists to
        # prevent.
        self._verify_subject(expected_subject_fingerprint, fingerprint)

        if existing is not None:
            return self._existing(gate, existing, authority_action)

        stored = self._store.record_authority_event(
            authority_event_id=new_authority_event_id(),
            planner_request_id=planner_request_id,
            gate_kind=gate.kind,
            authority_action=authority_action,
            subject_fingerprint=fingerprint,
            result_schema_version=int(record.result_schema_version),
            actor=provenance.actor,
            source=provenance.source,
            recorded_at=self._clock(),
            answer_text=answer_text,
            rejection_reason=rejection_reason,
            expected_action=record.action,
        )
        if stored is None:
            # Lost the race. Somebody else's decision is the one that stands, and
            # what this caller gets back is the truth about it rather than a
            # second record or a silent overwrite.
            raced = self._store.authority_event(planner_request_id)
            return self._existing(derive_gate(record, event=raced), raced,
                                  authority_action)
        return derive_gate(record, event=stored)

    # -- the stale-view check -------------------------------------------------

    @staticmethod
    def _verify_subject(expected: str, current: str) -> None:
        """Refuse unless the caller named the subject that is actually there.

        ``None`` and the empty string are refused as *malformed*, not treated as
        "unspecified". That distinction is the point: there is no value meaning
        "I did not look", because a decision made without looking is not a
        decision this module will record.
        """
        if expected is None:
            raise PlannerAuthorityInvalid(
                "an authority decision must name the subject fingerprint it "
                "authorizes"
            )
        if not valid_fingerprint(expected):
            raise PlannerAuthorityInvalid(
                "that is not a subject fingerprint",
                detail=type(expected).__name__ if not isinstance(expected, str)
                else f"{len(expected)} characters",
            )
        if expected != current:
            # What the caller read is not what would now be authorized. The same
            # refusal Mind's base hash makes, and for the same reason: re-reading
            # is the point.
            #
            # Three different causes land here and all are the same answer: the
            # subject changed, the caller held a digest for another request, or
            # it held one for the other action on this request. In every case the
            # decision would attach to something the person did not see.
            raise PlannerAuthorityStale(
                "the planner result is not the one this decision was made against",
                detail=current,
            )

    # -- what an already-decided gate answers ---------------------------------

    def _existing(self, gate: HumanGate, existing, authority_action: str) -> HumanGate:
        """A retry gets the truth; a contradiction gets a refusal.

        The distinction is the same decision twice versus two different
        decisions. An identical repeat is a double tap, a lost response, a
        refreshed page — recording it again would turn one decision into two, and
        refusing it would report a failure for something that in fact succeeded.
        A different decision is a person's earlier authority being overwritten by
        their later one with nothing recording that it happened, which this build
        will not do quietly.
        """
        if existing.authority_action == authority_action:
            return gate
        raise PlannerAuthorityConflict(
            "this planner result was already "
            + existing.authority_action
            + "; a contradicting decision is not recorded over it",
            detail=existing.authority_event_id,
        )

    @staticmethod
    def _refusal_message(gate: HumanGate, authority_action: str) -> str:
        if gate.kind == GATE_ANSWER:
            return (
                "that planner result is a question: it is answered, not approved "
                "or rejected"
            )
        if gate.kind == GATE_CONFIRMATION:
            return (
                "that planner result is a prepared worker prompt: it is approved "
                "or rejected, not answered"
            )
        return "that planner result asks nothing of a person"


__all__ = ["PlannerAuthorityService", "PlannerGateView"]
