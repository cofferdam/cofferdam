"""What one turn's criteria say about the turn before it. Declared, never inferred.

M2K PR6 froze each turn's acceptance criteria. PR7 evaluated them. PR8 published
the result. PR9 established why none of that composes into a task-level answer:
Cofferdam stores every turn's requirements and **nothing about the relationship
between them**, so *accumulate every turn* makes a task that created and then
deleted a file contradict itself, and *the latest turn wins* silently drops the
feature turn one asked for. This module persists the missing fact.

It computes no aggregate, and there is no code here or downstream that could.
A lineage edge is not a verdict.

Declared, not inferred — and that is the whole design
-----------------------------------------------------

Continuity is an intent. Nothing about two criteria sets reveals it:

* **Identical description does not mean the same criterion.** Two turns may
  require "the login page renders" for unrelated reasons, months apart.
* **Identical fingerprint does not mean lineage.** The criteria fingerprint is a
  hash of content, and content equality is not identity — that is precisely why
  :func:`~.criteria.criteria_fingerprint` excludes the ids.
* **Identical path does not mean the same requirement.** A file named twice is a
  file named twice.
* **Identical ordinal means nothing at all.** It is a position in a list.
* **A model's opinion is not authority.** No model reads any of this.

So a relationship exists only when somebody stated it, and if nobody did, the
answer is :data:`CONTINUITY_NOT_DECLARED` — an explicit, durable "no declaration
was made", which is a different fact from a historical turn that predates this
table entirely.

Who may declare it
------------------

The user, or a future host-owned planner. **Never the worker**: an agent that
could say its new criteria supersede its old ones could retire the requirement it
had just failed, which is the self-grading the whole milestone exists to prevent.
**Never the adapter**: :class:`~.adapters.protocol.AdapterOutcome` has no
continuity field and this build adds none. Never worker prose, never a claim,
never Git evidence, never the evaluator, never a read of the assessment surface.

Like criteria, this is an **internal** ``TaskService`` input with no HTTP request
field. PR6 kept criteria off the wire and PR10 does not widen that.

Frozen before the worker exists
-------------------------------

The declaration is committed against the same reserved turn number as the
criteria snapshot and the Git baseline, before ``dispatch_started``, and freezes
with them. A boundary a worker can move after seeing its own results is not a
boundary.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from .criteria import (
    FINGERPRINT_CHARS,
    _new_id,
    _valid_id,
)

#: Bumped when what a continuity declaration *means* changes — not when a column
#: moves. Separate from ``SCHEMA_VERSION`` (storage shape),
#: ``CRITERIA_MODEL_VERSION`` (what a criterion is), ``ASSEMBLER_VERSION`` (how a
#: bundle is built) and ``EVALUATOR_VERSION`` (how a criterion is answered),
#: because all five change for reasons of their own and a reader must be able to
#: tell which one moved. Bound into the fingerprint, so a declaration recorded
#: under one meaning can never be silently read as another.
#:
#: There is deliberately no ``AGGREGATOR_VERSION`` in this build: nothing
#: aggregates, and the constant would imply something does.
CONTINUITY_MODEL_VERSION = 1


# -- read states --------------------------------------------------------------
#
# Two are stored. The third is what a missing row means, and it must never be
# confused with either — the same three-way shape criteria already use, for the
# same reason.

#: A declaration exists for this turn: :data:`CONTINUITY_MODES` says which.
CONTINUITY_DECLARED = "declared"

#: The turn was dispatched and **no continuity was declared for it**. A recorded
#: fact, written on purpose rather than left as an absent row, because "nobody
#: said" and "we cannot know" are different and only one of them is recoverable.
#:
#: It does **not** mean extend, replace, independent, or preserve-previous. It
#: means a future task-level aggregate has nothing to stand on for this turn,
#: which is the honest and intentionally inconvenient answer.
CONTINUITY_NOT_DECLARED = "not_declared"

#: No row at all: the turn ran before continuity was persisted. Never stored —
#: :func:`turn_continuity` returns it for absence. No backfill exists or may be
#: written: deriving lineage from a prompt, a title, worker prose, a claim, or
#: from "the latest turn wins" would manufacture intent nobody expressed.
CONTINUITY_LEGACY_UNKNOWN = "legacy_unknown"

STORED_CONTINUITY_STATES: Tuple[str, ...] = (
    CONTINUITY_DECLARED,
    CONTINUITY_NOT_DECLARED,
)

CONTINUITY_STATES: Tuple[str, ...] = STORED_CONTINUITY_STATES + (
    CONTINUITY_LEGACY_UNKNOWN,
)


# -- modes --------------------------------------------------------------------
#
# Only meaningful when the state is `declared`. Four, and each one answers
# exactly the question a future aggregate has to ask: *what happens to the
# requirements that were already live?*

#: No predecessor exists. Recorded mechanically for a task's first criteria-
#: bearing turn, and that is safe because it is a **structural fact rather than
#: an inference**: there is no earlier snapshot for this task, so there is no
#: relationship anybody could have meant. It carries no predecessor and no
#: relations.
#:
#: ``root`` describes lineage, not requirements. A first turn whose criteria are
#: ``not_provided`` is still ``root`` — the criteria snapshot already records that
#: nothing was required, and saying otherwise here would be inventing a second,
#: contradictory place to look.
CONTINUITY_ROOT = "root"

#: The predecessor's active requirements **remain**, and this snapshot adds to
#: them. No supersession relations: nothing is being retired.
CONTINUITY_EXTEND = "extend"

#: The predecessor's active requirement set is **wholly superseded** by this
#: snapshot. No relations are enumerated, because naming every prior criterion to
#: say "all of them" would be ceremony that could disagree with itself. Nothing
#: is deleted: the prior criteria remain immutable historical records that were
#: true of their own turn.
CONTINUITY_REPLACE = "replace"

#: The predecessor's active requirements remain **except** the ones named by
#: explicit supersession relations, and this snapshot may add more. This is the
#: mode PR9 concluded a snapshot-level relation alone cannot express, and it is
#: the only mode that requires at least one relation.
CONTINUITY_REVISE = "revise"

CONTINUITY_MODES: Tuple[str, ...] = (
    CONTINUITY_ROOT,
    CONTINUITY_EXTEND,
    CONTINUITY_REPLACE,
    CONTINUITY_REVISE,
)

#: Modes that name a predecessor. ``root`` is exactly the one that cannot.
MODES_REQUIRING_PREDECESSOR: Tuple[str, ...] = (
    CONTINUITY_EXTEND,
    CONTINUITY_REPLACE,
    CONTINUITY_REVISE,
)

#: Modes that carry supersession relations. Exactly one does, and it must.
MODES_REQUIRING_RELATIONS: Tuple[str, ...] = (CONTINUITY_REVISE,)

# `independent` is deliberately absent.
#
# It came up in the PR9 design discussion and does not survive the question a
# mode has to answer: *what happens to the requirements that were already live?*
# "Independent" answers neither "they remain" nor "they are superseded", so an
# aggregate reading it would have to pick one — which is the ambiguity this
# vocabulary exists to remove. If prior requirements remain, that is `extend`
# whatever the intent was called; if they do not, that is `replace`. Adding a
# third word for the same two outcomes would give callers a way to say something
# that cannot be composed, and would leave the aggregate guessing exactly where
# PR9 refused to guess.


#: Server-minted identity prefixes. A caller supplies relationships and never the
#: handles Cofferdam files them under.
CONTINUITY_ID_PREFIX = "ctn_"
SUPERSESSION_ID_PREFIX = "sup_"
CONTINUITY_ID_CHARS = len(CONTINUITY_ID_PREFIX) + 26
SUPERSESSION_ID_CHARS = len(SUPERSESSION_ID_PREFIX) + 26

#: How many supersession relations one turn may declare. Generous against the
#: 32-criteria bound — every criterion in a maximal snapshot could supersede two
#: predecessors — and finite, because an unbounded lineage declaration is an
#: unbounded write a caller controls.
#:
#: Over the bound is **refused before dispatch**, never trimmed. A silently
#: dropped supersession would leave a requirement live that somebody had retired,
#: which is worse than a rejection that says so.
MAX_SUPERSESSIONS_PER_TURN = 64

#: Fields a caller may submit in one supersession relation. Anything else is
#: refused by name rather than ignored, so a typo cannot be silently discarded.
ALLOWED_RELATION_FIELDS = frozenset({"criterion_ordinal", "predecessor_criterion_id"})

#: Durable identities are minted here and never accepted. Submitting one is an
#: error with its own reason code rather than a field that gets overwritten.
SERVER_OWNED_CONTINUITY_FIELDS = frozenset(
    {
        "continuity_id",
        "supersession_id",
        "continuity_fingerprint",
        "current_snapshot_id",
        "recorded_at",
        "relation_count",
        "continuity_state",
    }
)

# -- refusal reasons ----------------------------------------------------------

REASON_SUBMISSION_MALFORMED = "continuity_submission_malformed"
REASON_MODE_INVALID = "continuity_mode_invalid"
REASON_SERVER_OWNED_FIELD = "continuity_server_owned_field"
REASON_UNKNOWN_FIELD = "continuity_unknown_field"
REASON_PREDECESSOR_REQUIRED = "continuity_predecessor_required"
REASON_PREDECESSOR_UNEXPECTED = "continuity_predecessor_unexpected"
REASON_PREDECESSOR_INVALID = "continuity_predecessor_invalid"
REASON_PREDECESSOR_UNKNOWN = "continuity_predecessor_unknown"
REASON_PREDECESSOR_FOREIGN_TASK = "continuity_predecessor_foreign_task"
REASON_PREDECESSOR_NOT_EARLIER = "continuity_predecessor_not_earlier"
REASON_RELATIONS_REQUIRED = "continuity_relations_required"
REASON_RELATIONS_UNEXPECTED = "continuity_relations_unexpected"
REASON_RELATION_MALFORMED = "continuity_relation_malformed"
REASON_RELATION_LIMIT_EXCEEDED = "continuity_relation_limit_exceeded"
REASON_RELATION_DUPLICATE = "continuity_relation_duplicate"
REASON_RELATION_CURRENT_UNKNOWN = "continuity_relation_current_unknown"
REASON_RELATION_PREDECESSOR_UNKNOWN = "continuity_relation_predecessor_unknown"

#: M2K PR12. The named old criterion exists and belongs to this task, but is
#: **not active** in the predecessor's resolved active set — it was retired by an
#: earlier `revise`, or cut away by a `replace`, or has not been reached by the
#: declared lineage at all. Distinct from
#: :data:`REASON_RELATION_PREDECESSOR_UNKNOWN`, which means the id names no
#: criterion of this task: "you may not retire that" and "there is no such
#: requirement" are different mistakes and a caller should be told which it made.
REASON_RELATION_PREDECESSOR_NOT_ACTIVE = "continuity_relation_predecessor_not_active"

#: M2K PR12. A `revise` was declared, but the predecessor's active set cannot be
#: resolved at all — its own continuity is `legacy_unknown` or `not_declared`, or
#: the stored lineage behind it is malformed, cyclic or too deep.
#:
#: Refused **before dispatch** rather than stored and left for the resolver to
#: refuse later. A `revise` is a statement about a set nobody can name, so there
#: is nothing for it to be a revision *of*; storing it would durably record a
#: relationship that can never be honoured. It is emphatically not downgraded to
#: `replace` — that would be Cofferdam declaring something the caller did not.
REASON_PREDECESSOR_LINEAGE_UNAVAILABLE = "continuity_predecessor_lineage_unavailable"
REASON_ROOT_HAS_PREDECESSOR = "continuity_root_has_predecessor"
REASON_NOT_FIRST_TURN = "continuity_root_not_first_turn"


class ContinuitySubmissionInvalid(Exception):
    """A submitted continuity declaration that will not be stored.

    Carries the closed reason code and **never the offending value**, exactly as
    :class:`~.criteria.CriteriaSubmissionInvalid` does and for the same reason.
    The service turns it into a :class:`~.errors.ContinuityInvalid` so a caller
    sees an ordinary Task Core refusal.

    Every rejection happens before the adapter is reached, so a refused
    declaration never leaves a worker running against lineage Cofferdam could not
    record.
    """

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def new_continuity_id(now_ms: Optional[int] = None) -> str:
    """A fresh continuity id. Minted by the store, never supplied."""
    return _new_id(CONTINUITY_ID_PREFIX, now_ms)


def new_supersession_id(now_ms: Optional[int] = None) -> str:
    """A fresh supersession id. Minted by the store, never supplied."""
    return _new_id(SUPERSESSION_ID_PREFIX, now_ms)


def valid_continuity_id(value: object) -> bool:
    return _valid_id(value, CONTINUITY_ID_PREFIX, CONTINUITY_ID_CHARS)


def valid_supersession_id(value: object) -> bool:
    return _valid_id(value, SUPERSESSION_ID_PREFIX, SUPERSESSION_ID_CHARS)


@dataclass(frozen=True)
class SupersessionRelation:
    """One declared "this new criterion retires that old one".

    ``criterion_ordinal`` names a criterion of the **current** snapshot by its
    position in the submitted criteria, because at declaration time the caller
    has not been told the server-minted criterion ids and must never be the one
    choosing them. The store resolves it to a real ``criterion_id``.

    ``predecessor_criterion_id`` is a durable id the caller legitimately knows:
    it was published by an earlier read of the lineage. Since M2K PR12 the store
    validates it against the predecessor's **resolved active set** rather than
    against the predecessor snapshot's own items, so a requirement introduced
    several turns earlier and still live may be retired — and one that was
    already retired, or cut away by a ``replace``, still may not.
    """

    criterion_ordinal: int
    predecessor_criterion_id: str


@dataclass(frozen=True)
class ContinuityDeclaration:
    """A validated declaration, ready to persist. Never constructed from a caller."""

    mode: str
    predecessor_snapshot_id: Optional[str]
    relations: Tuple[SupersessionRelation, ...]


@dataclass(frozen=True)
class StoredSupersession:
    """One persisted lineage edge, as read back."""

    supersession_id: str
    ordinal: int
    criterion_id: str
    predecessor_criterion_id: str


@dataclass(frozen=True)
class TurnContinuity:
    """One turn's continuity fact, in whichever of its three states it is in.

    ``legacy_unknown`` carries no identity: there is nothing to name, and minting
    one would assert a record exists where none does.
    """

    task_id: str
    turn_number: int
    state: str
    mode: Optional[str] = None
    continuity_id: Optional[str] = None
    current_snapshot_id: Optional[str] = None
    predecessor_snapshot_id: Optional[str] = None
    continuity_fingerprint: Optional[str] = None
    relation_count: int = 0
    dispatch_state: Optional[str] = None
    relations: Tuple[StoredSupersession, ...] = ()

    @property
    def recorded(self) -> bool:
        """True when a durable row exists — declared **or** explicitly not."""
        return self.state in STORED_CONTINUITY_STATES


def validate_declaration(submitted: object) -> Optional[ContinuityDeclaration]:
    """A submitted continuity declaration, validated — or a refusal.

    ``None`` means *no declaration was made*, which is a real answer and becomes
    a durable :data:`CONTINUITY_NOT_DECLARED` row rather than a missing one. It is
    not an error: every caller that exists today supplies nothing, and PR10 keeps
    them working exactly as they are.

    Structural validation only. Whether the named predecessor exists, belongs to
    this task and comes from an earlier turn is decided in the store, where the
    database can be asked — see :meth:`~.store.TaskStore.reserve_turn_continuity`.
    """
    if submitted is None:
        return None
    if not isinstance(submitted, dict):
        raise ContinuitySubmissionInvalid(REASON_SUBMISSION_MALFORMED)

    unknown = set(submitted) - {"mode", "predecessor_snapshot_id", "supersedes"}
    server_owned = unknown & SERVER_OWNED_CONTINUITY_FIELDS
    if server_owned:
        raise ContinuitySubmissionInvalid(REASON_SERVER_OWNED_FIELD)
    if unknown:
        raise ContinuitySubmissionInvalid(REASON_UNKNOWN_FIELD)

    mode = submitted.get("mode")
    if not isinstance(mode, str) or mode not in CONTINUITY_MODES:
        raise ContinuitySubmissionInvalid(REASON_MODE_INVALID)

    predecessor = submitted.get("predecessor_snapshot_id")
    if mode == CONTINUITY_ROOT:
        if predecessor is not None:
            # `root` means there is no predecessor. Naming one is a contradiction
            # rather than extra information, and the safe reading of a
            # contradiction is a refusal.
            raise ContinuitySubmissionInvalid(REASON_ROOT_HAS_PREDECESSOR)
    else:
        if predecessor is None:
            raise ContinuitySubmissionInvalid(REASON_PREDECESSOR_REQUIRED)
        from .criteria import SNAPSHOT_ID_CHARS, SNAPSHOT_ID_PREFIX

        if not _valid_id(predecessor, SNAPSHOT_ID_PREFIX, SNAPSHOT_ID_CHARS):
            raise ContinuitySubmissionInvalid(REASON_PREDECESSOR_INVALID)

    raw_relations = submitted.get("supersedes")
    if raw_relations is None:
        raw_relations = ()
    if isinstance(raw_relations, (str, bytes, dict)) or not isinstance(
        raw_relations, Iterable
    ):
        raise ContinuitySubmissionInvalid(REASON_RELATION_MALFORMED)
    raw_relations = tuple(raw_relations)

    if mode in MODES_REQUIRING_RELATIONS and not raw_relations:
        raise ContinuitySubmissionInvalid(REASON_RELATIONS_REQUIRED)
    if mode not in MODES_REQUIRING_RELATIONS and raw_relations:
        # `extend` adds without retiring and `replace` retires everything, so in
        # both a relation would be a statement the mode already contradicts.
        raise ContinuitySubmissionInvalid(REASON_RELATIONS_UNEXPECTED)
    if len(raw_relations) > MAX_SUPERSESSIONS_PER_TURN:
        # Refused, never trimmed.
        raise ContinuitySubmissionInvalid(REASON_RELATION_LIMIT_EXCEEDED)

    from .criteria import CRITERION_ID_CHARS, CRITERION_ID_PREFIX

    relations = []
    seen = set()
    for raw in raw_relations:
        if not isinstance(raw, dict):
            raise ContinuitySubmissionInvalid(REASON_RELATION_MALFORMED)
        extra = set(raw) - ALLOWED_RELATION_FIELDS
        if extra & SERVER_OWNED_CONTINUITY_FIELDS:
            raise ContinuitySubmissionInvalid(REASON_SERVER_OWNED_FIELD)
        if extra:
            raise ContinuitySubmissionInvalid(REASON_UNKNOWN_FIELD)
        ordinal = raw.get("criterion_ordinal")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
            raise ContinuitySubmissionInvalid(REASON_RELATION_MALFORMED)
        prior = raw.get("predecessor_criterion_id")
        if not _valid_id(prior, CRITERION_ID_PREFIX, CRITERION_ID_CHARS):
            raise ContinuitySubmissionInvalid(REASON_RELATION_MALFORMED)
        key = (ordinal, prior)
        if key in seen:
            # The same edge twice is not additional meaning; it is a caller
            # mistake, and accepting it would make `relation_count` a count of
            # submissions rather than of relationships.
            raise ContinuitySubmissionInvalid(REASON_RELATION_DUPLICATE)
        seen.add(key)
        relations.append(
            SupersessionRelation(criterion_ordinal=ordinal, predecessor_criterion_id=prior)
        )

    return ContinuityDeclaration(
        mode=mode,
        predecessor_snapshot_id=predecessor,
        relations=tuple(relations),
    )


class _Fingerprint:
    """The same domain-separated, length-prefixed construction criteria use."""

    def __init__(self) -> None:
        self._digest = hashlib.sha256()

    def field(self, value: object) -> None:
        if value is None:
            self._digest.update(b"\x00")
            return
        encoded = str(value).encode("utf-8")
        self._digest.update(b"\x01")
        self._digest.update(str(len(encoded)).encode("ascii"))
        self._digest.update(b":")
        self._digest.update(encoded)

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


def continuity_fingerprint(
    state: str,
    mode: Optional[str],
    current_snapshot_id: Optional[str],
    predecessor_snapshot_id: Optional[str],
    relations: Sequence[Tuple[str, str]],
) -> str:
    """A stable hash of exactly the lineage facts this declaration stores.

    ``relations`` is a sequence of ``(criterion_id, predecessor_criterion_id)``
    pairs — resolved ids, not ordinals — and is sorted here rather than trusted,
    so the digest cannot depend on submission order or on what a ``SELECT``
    returned.

    What is in it
    -------------

    The model version, the state, the mode, both snapshot identities, the
    relation count, and every relation in canonical order.

    **Both snapshot ids are in, and here that is right** even though
    :func:`~.criteria.criteria_fingerprint` deliberately excludes ids. The two
    hashes answer different questions. That one asks *what was required*, where
    two turns given identical requirements should agree. This one asks *which
    exact prior requirements does this turn stand on*, and that is a statement
    about specific stored rows — the same reason lineage may never be inferred
    from matching text. Criterion ids are in the relations for exactly the same
    reason.

    What is deliberately **not** in it
    ----------------------------------

    * **The continuity id and the supersession ids.** Minted per row, carrying a
      clock and randomness. Hashing them would identify the row rather than the
      relationship and would change on every re-reservation.
    * **``recorded_at``, or any clock.** It must survive a restart unchanged.
    * **Database rowids, insertion order, or the order relations arrived in.**
    * **Provider or session identifiers, and any host path.** Neither is part of
      what was declared, and a deployment that moves must not move a fingerprint.
    * **The dispatch state.** That records how far the turn got, not what was
      declared; freezing a declaration must not change its identity.
    """
    digest = _Fingerprint()
    digest.field("cofferdam.criteria.continuity")
    digest.field(CONTINUITY_MODEL_VERSION)
    digest.field(state)
    digest.field(mode)
    digest.field(current_snapshot_id)
    digest.field(predecessor_snapshot_id)
    digest.field(len(relations))
    for criterion_id, predecessor_criterion_id in sorted(relations):
        digest.field(criterion_id)
        digest.field(predecessor_criterion_id)
    return digest.hexdigest()


__all__ = [
    "ALLOWED_RELATION_FIELDS",
    "CONTINUITY_DECLARED",
    "CONTINUITY_EXTEND",
    "CONTINUITY_ID_CHARS",
    "CONTINUITY_ID_PREFIX",
    "CONTINUITY_LEGACY_UNKNOWN",
    "CONTINUITY_MODEL_VERSION",
    "CONTINUITY_MODES",
    "CONTINUITY_NOT_DECLARED",
    "CONTINUITY_REPLACE",
    "CONTINUITY_REVISE",
    "CONTINUITY_ROOT",
    "CONTINUITY_STATES",
    "MAX_SUPERSESSIONS_PER_TURN",
    "MODES_REQUIRING_PREDECESSOR",
    "MODES_REQUIRING_RELATIONS",
    "REASON_MODE_INVALID",
    "REASON_NOT_FIRST_TURN",
    "REASON_PREDECESSOR_FOREIGN_TASK",
    "REASON_PREDECESSOR_INVALID",
    "REASON_PREDECESSOR_LINEAGE_UNAVAILABLE",
    "REASON_PREDECESSOR_NOT_EARLIER",
    "REASON_PREDECESSOR_REQUIRED",
    "REASON_PREDECESSOR_UNEXPECTED",
    "REASON_PREDECESSOR_UNKNOWN",
    "REASON_RELATION_CURRENT_UNKNOWN",
    "REASON_RELATION_DUPLICATE",
    "REASON_RELATION_LIMIT_EXCEEDED",
    "REASON_RELATION_MALFORMED",
    "REASON_RELATION_PREDECESSOR_NOT_ACTIVE",
    "REASON_RELATION_PREDECESSOR_UNKNOWN",
    "REASON_RELATIONS_REQUIRED",
    "REASON_RELATIONS_UNEXPECTED",
    "REASON_ROOT_HAS_PREDECESSOR",
    "REASON_SERVER_OWNED_FIELD",
    "REASON_SUBMISSION_MALFORMED",
    "REASON_UNKNOWN_FIELD",
    "SERVER_OWNED_CONTINUITY_FIELDS",
    "STORED_CONTINUITY_STATES",
    "SUPERSESSION_ID_CHARS",
    "SUPERSESSION_ID_PREFIX",
    "ContinuityDeclaration",
    "ContinuitySubmissionInvalid",
    "StoredSupersession",
    "SupersessionRelation",
    "TurnContinuity",
    "continuity_fingerprint",
    "new_continuity_id",
    "new_supersession_id",
    "valid_continuity_id",
    "valid_supersession_id",
    "validate_declaration",
]
