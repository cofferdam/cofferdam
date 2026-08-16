"""Which criteria are active at one turn. Derived from declarations, never guessed.

M2K PR6 froze **what each turn required**. PR10 persisted **what each turn said
about the turn before it**. Neither answers the question a task-level view has to
ask first:

    given those immutable declarations, which immutable criteria are in force at
    *this* turn?

This module answers exactly that and nothing beyond it. It composes two frozen
records into a third, deterministic one:

.. code-block:: text

    criteria snapshots  +  continuity declarations  +  supersessions
                              |
                       pure lineage resolver
                              |
              ResolvedActiveCriteria   or   LineageUnavailable

What it is not
--------------

It is **not** an aggregate, and there is no code here or downstream that could
become one. There is no ``AGGREGATOR_VERSION``, no task verdict, no ``all_met``,
no acceptance outcome, no pass, no fail, no score. A resolved active set says
*what is currently required*; whether any of it happened is
:mod:`~.evaluation`'s per-criterion question, and what a mixture of those results
means is still unavailable by design — see PR9's doctrine in ``STATUS.md``.

The one rule worth stating twice: **a known empty active set is not success.**
:data:`RESOLUTION_RESOLVED` with zero active criteria means "the currently
declared requirement set is empty", which is a fact about declarations. Reading
it as "the task passed" is exactly the smuggling this milestone exists to
prevent, and there is deliberately no vocabulary in this module a reader could
mistake for one.

It never invents continuity
---------------------------

Every one of these is refused:

* *the latest turn wins* — PR9 showed it silently drops turn 1's requirements
* *accumulate every turn* — PR9 showed it makes a task contradict itself
* *matching description means the same criterion*
* *matching fingerprint means lineage* — content equality is not identity
* *matching path means the same requirement*
* *a missing declaration means extend*
* *a best-effort partial answer across an unknown link*

When a required dependency is unknown the answer is
:class:`LineageUnavailable` with a closed reason. It is never a guessed set.

Replace is a cut point, and that is load-bearing
------------------------------------------------

An explicit ``replace`` says the prior requirement set is wholly superseded, so
resolving it does **not** need the predecessor's active set. That is what lets a
task recover: a turn that ran before continuity existed
(``legacy_unknown``), or one nobody declared anything for (``not_declared``),
does not poison every later turn forever. The moment somebody declares
``replace``, the requirement set is knowable again.

The predecessor's *identity* is still validated — it must exist, belong to this
task and come from an earlier turn — because cutting a dependency is not the same
as ignoring a malformed declaration. What is skipped is only the traversal.

``extend`` and ``revise`` are the opposite: both are statements *about* the prior
active set, so an unknown predecessor makes them unanswerable.

Purity
------

:func:`resolve` takes an immutable :class:`LineageGraph` and returns a result. It
opens no database, runs no process, reads no file, touches no socket, consults no
provider and reads no clock. Fetching the graph is
:meth:`~.store.TaskStore.lineage_inputs`'s job, under one coherent read snapshot,
and keeping the two apart is what makes "resolution is replayable from stored
rows" checkable rather than promised. ``tests/test_lineage_purity.py`` asserts it
from the syntax tree and again at runtime.

Derived, not persisted
----------------------

Nothing here is written down. The sources are immutable, the function is
deterministic and versioned, so the answer can always be recomputed — and
persisting it would add a write path, a recovery path and a second place for the
truth to live. Schema stays at v9.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .continuity import (
    CONTINUITY_DECLARED,
    CONTINUITY_EXTEND,
    CONTINUITY_LEGACY_UNKNOWN,
    CONTINUITY_MODES,
    CONTINUITY_NOT_DECLARED,
    CONTINUITY_REPLACE,
    CONTINUITY_REVISE,
    CONTINUITY_ROOT,
    TurnContinuity,
)
from .criteria import (
    CRITERIA_LEGACY_UNKNOWN,
    CRITERIA_NOT_PROVIDED,
    CRITERIA_PRESENT,
    AcceptanceCriterion,
    CriteriaSnapshot,
)

#: Bumped when what "active at this turn" *means* changes: a mode resolved
#: differently, an ordering rule altered, a supersession rule relaxed or
#: tightened, a new unavailable reason given a different force.
#:
#: Deliberately distinct from :data:`~.store.SCHEMA_VERSION` (the shape of the
#: tables), :data:`~.criteria.CRITERIA_MODEL_VERSION` (what a criterion is),
#: :data:`~.continuity.CONTINUITY_MODEL_VERSION` (what a declaration means),
#: :data:`~.evidence.ASSEMBLER_VERSION` (how a bundle is built) and
#: :data:`~.evaluation.EVALUATOR_VERSION` (how a criterion is answered). All six
#: move for reasons of their own, and a reader must be able to tell which one
#: did.
#:
#: It is bound into :func:`resolved_fingerprint`, so a future version 2 that
#: resolved the same immutable rows into a different active set produces a
#: **visibly different** identity rather than silently reinterpreting a stored
#: one.
#:
#: There is still no ``AGGREGATOR_VERSION`` in this build, and this constant is
#: not a step towards one: it versions *which requirements are live*, not any
#: judgement about them.
RESOLVER_VERSION = 1

#: How many turns one resolution may walk. Cycles should be structurally
#: impossible — PR10 refuses a predecessor that is not strictly earlier, so every
#: chain is strictly decreasing and therefore finite — but "should be impossible"
#: is not a termination proof for a read that runs at start-up. A corrupted or
#: hand-written database row must make this **answer**, not hang.
#:
#: Generous against any plausible task: 256 turns of declared lineage, against
#: the 32-criteria-per-turn bound, so the largest walkable active set is bounded
#: too. Over the bound is :data:`REASON_DEPTH_EXCEEDED` rather than a truncated
#: answer, because a partial active set is exactly the guess this module refuses.
MAX_LINEAGE_DEPTH = 256


# -- result states ------------------------------------------------------------
#
# Two, closed, and the second is not a failure of the resolver: it is the honest
# answer when the declarations do not determine one.

#: The active criterion set for this turn is known exactly.
RESOLUTION_RESOLVED = "resolved"

#: The active criterion set cannot be determined from what is stored. A closed
#: :data:`REASONS` code says why. Never a partial set, never an empty one.
RESOLUTION_UNAVAILABLE = "unavailable"

RESOLUTION_STATES: Tuple[str, ...] = (RESOLUTION_RESOLVED, RESOLUTION_UNAVAILABLE)


# -- unavailable reasons ------------------------------------------------------
#
# Closed and code-owned. Exception prose is never semantic authority here, for
# the reason `evaluation.py` gives about free-form explanation columns: a
# sentence a reader might argue with is how a deterministic record stops being
# one.

#: The turn predates continuity persistence. No durable question exists, so there
#: is no answer — and inventing one from turn order would be the backfill PR10
#: refused to write.
REASON_LEGACY_UNKNOWN = "continuity_legacy_unknown"

#: The turn recorded an explicit "nobody declared a relationship". Distinct from
#: the above on purpose, and equally unresolvable: `not_declared` is not
#: `extend`, not `replace` and not `root`.
REASON_NOT_DECLARED = "continuity_not_declared"

#: A required predecessor's active set could not be resolved. ``cause`` carries
#: the underlying reason and ``at_turn_number`` says where.
REASON_PREDECESSOR_UNAVAILABLE = "predecessor_unavailable"

#: The declaration names a predecessor snapshot that does not exist.
REASON_PREDECESSOR_MISSING = "predecessor_missing"

#: The named predecessor snapshot belongs to a different task. Lineage never
#: leaves the task: one task's requirements must not be retirable by another's.
REASON_PREDECESSOR_FOREIGN_TASK = "predecessor_foreign_task"

#: The named predecessor is the same turn or a later one. A predecessor is
#: strictly earlier; anything else is a loop rather than a lineage.
REASON_PREDECESSOR_NOT_EARLIER = "predecessor_not_earlier"

#: A turn in the walked lineage has a continuity declaration but no criteria
#: snapshot row. A declaration about a snapshot that does not exist describes
#: nothing.
REASON_CRITERIA_SNAPSHOT_MISSING = "criteria_snapshot_missing"

#: The declaration's ``current_snapshot_id`` is not this turn's criteria
#: snapshot. The stored rows disagree about what the declaration is about.
REASON_SNAPSHOT_MISMATCH = "continuity_snapshot_mismatch"

#: A ``root`` declaration names a predecessor. ``root`` means there is none.
REASON_ROOT_HAS_PREDECESSOR = "root_has_predecessor"

#: A ``root`` declaration sits after an earlier criteria snapshot for the same
#: task. ``root`` is a structural first-snapshot claim, so a later one is a
#: contradiction rather than extra information — and it is never reinterpreted as
#: ``replace``.
REASON_ROOT_NOT_FIRST = "root_not_first_snapshot"

#: Supersession relations exist for a mode that forbids them, or are absent for
#: ``revise``, which requires at least one.
REASON_RELATIONS_MODE_MISMATCH = "relations_mode_mismatch"

#: A relation's old-side criterion is **not active** in the resolved predecessor
#: set. Historical membership is not active membership: a criterion retired two
#: turns ago cannot be retired again, and silently ignoring the stale edge would
#: leave the resolver asserting an active set nobody declared.
REASON_SUPERSESSION_TARGET_NOT_ACTIVE = "supersession_target_not_active"

#: A relation's old-side criterion is not a criterion of the walked lineage at
#: all.
REASON_SUPERSESSION_PREDECESSOR_UNKNOWN = "supersession_predecessor_unknown"

#: A relation's new-side criterion does not belong to this turn's snapshot.
REASON_SUPERSESSION_CURRENT_UNKNOWN = "supersession_current_unknown"

#: The assembled active set would contain one criterion id twice. Impossible
#: from valid rows — a criterion belongs to exactly one snapshot — and refused
#: rather than deduplicated, because a duplicate means the stored rows are not
#: what this resolver's arithmetic assumes.
REASON_DUPLICATE_ACTIVE_CRITERION = "duplicate_active_criterion"

#: Stored lineage that no valid write could have produced and that no other
#: reason names precisely: an unknown state or mode, a count disagreeing with its
#: rows, a criterion with no id, a missing node in a chain that asked for one.
REASON_MALFORMED_LINEAGE = "malformed_lineage"

#: The chain is longer than :data:`MAX_LINEAGE_DEPTH`.
REASON_DEPTH_EXCEEDED = "lineage_depth_exceeded"

#: The walk re-entered a turn it had already visited. Defence in depth against a
#: corrupted fixture; the strictly-earlier rule should make it unreachable.
REASON_CYCLE_DETECTED = "cycle_detected"

REASONS: Tuple[str, ...] = (
    REASON_LEGACY_UNKNOWN,
    REASON_NOT_DECLARED,
    REASON_PREDECESSOR_UNAVAILABLE,
    REASON_PREDECESSOR_MISSING,
    REASON_PREDECESSOR_FOREIGN_TASK,
    REASON_PREDECESSOR_NOT_EARLIER,
    REASON_CRITERIA_SNAPSHOT_MISSING,
    REASON_SNAPSHOT_MISMATCH,
    REASON_ROOT_HAS_PREDECESSOR,
    REASON_ROOT_NOT_FIRST,
    REASON_RELATIONS_MODE_MISMATCH,
    REASON_SUPERSESSION_TARGET_NOT_ACTIVE,
    REASON_SUPERSESSION_PREDECESSOR_UNKNOWN,
    REASON_SUPERSESSION_CURRENT_UNKNOWN,
    REASON_DUPLICATE_ACTIVE_CRITERION,
    REASON_MALFORMED_LINEAGE,
    REASON_DEPTH_EXCEEDED,
    REASON_CYCLE_DETECTED,
)

# -- what a resolution is made of ---------------------------------------------


@dataclass(frozen=True)
class ActiveCriterion:
    """One criterion that is in force at the target turn, with its provenance.

    Deliberately not just text. An active entry has to say **which stored row**
    it is, because a later reader has to be able to point an evaluation, an audit
    or a person at the exact criterion — and because two identical descriptions
    from different turns are different requirements, which is the whole reason
    lineage is declared rather than inferred.

    ``criterion`` is the immutable :class:`~.criteria.AcceptanceCriterion` as
    stored. It is reused rather than copied into a new shape so there is one
    definition of what a criterion is; nothing mutable and nothing free-form is
    carried alongside it.

    ``source_turn_number`` and ``source_ordinal`` are where it came from, not
    where it sits now. Its position in the resolved set is the position of the
    entry in :attr:`ResolvedActiveCriteria.active`.
    """

    criterion_id: str
    source_snapshot_id: str
    source_turn_number: int
    source_ordinal: int
    criterion: AcceptanceCriterion


@dataclass(frozen=True)
class LineageStep:
    """One turn the resolution actually consumed, for audit and for the hash.

    Internal. There is no route, no bridge Action and no PWA control that
    publishes this in PR11 — a lineage read surface is its own review, and a
    serializer written before anything needs one is how an internal shape becomes
    a contract by accident.

    A step exists only for a turn whose criteria or whose declaration genuinely
    contributed. In particular a ``replace``'s predecessor is **not** a step: its
    active set was never traversed, and recording it would make the trace claim a
    dependency the resolution did not have.
    """

    turn_number: int
    snapshot_id: str
    criteria_fingerprint: str
    continuity_fingerprint: str
    mode: str
    #: The minted row handle. Present for the audit trail and deliberately
    #: **not** in the fingerprint: it carries a clock and randomness, so it
    #: identifies the row rather than the relationship.
    continuity_id: Optional[str] = None


@dataclass(frozen=True)
class ResolvedActiveCriteria:
    """The criteria in force at one turn, in deterministic order.

    ``active`` may legitimately be **empty**, and that is a resolved answer
    rather than a degenerate one: a ``root`` or ``replace`` turn whose snapshot
    is ``not_provided`` has a known, empty requirement set. It does not mean the
    task passed, that acceptance was met, or that anything succeeded — see this
    module's docstring.
    """

    task_id: str
    target_turn_number: int
    target_snapshot_id: str
    resolver_version: int
    active: Tuple[ActiveCriterion, ...]
    lineage: Tuple[LineageStep, ...]
    fingerprint: str
    state: str = RESOLUTION_RESOLVED

    @property
    def resolved(self) -> bool:
        return True

    @property
    def active_count(self) -> int:
        return len(self.active)

    @property
    def active_criterion_ids(self) -> Tuple[str, ...]:
        return tuple(entry.criterion_id for entry in self.active)


@dataclass(frozen=True)
class LineageUnavailable:
    """The active set is not determinable, and exactly why.

    ``reason`` is one of :data:`REASONS` and never free prose. ``at_turn_number``
    is the turn the walk stopped at, which may be the target or any predecessor
    it depended on. ``cause`` is set only for
    :data:`REASON_PREDECESSOR_UNAVAILABLE`, where the outer reason says *a
    dependency failed* and the inner one says *how*.

    There is no partial active set here on purpose. A caller that got one would
    have every incentive to use it.
    """

    task_id: str
    target_turn_number: int
    resolver_version: int
    reason: str
    at_turn_number: Optional[int] = None
    cause: Optional[str] = None
    state: str = RESOLUTION_UNAVAILABLE

    @property
    def resolved(self) -> bool:
        return False


# -- the immutable input graph ------------------------------------------------


@dataclass(frozen=True)
class LineageNode:
    """One turn's two frozen records, as read.

    Both are always present in shape — :meth:`~.store.TaskStore.turn_continuity`
    and :meth:`~.store.TaskStore.turn_criteria` never return ``None`` — so a node
    can describe a turn that has neither, and :func:`resolve` decides what that
    means rather than the fetcher.
    """

    turn_number: int
    continuity: TurnContinuity
    snapshot: CriteriaSnapshot


@dataclass(frozen=True)
class LineageGraph:
    """Everything one resolution may look at, fetched under one read snapshot.

    Closed by construction: :func:`resolve` reads these fields and nothing else,
    which is what makes the purity claim checkable. A node that is absent is
    absent — the resolver cannot go and fetch it.

    ``snapshot_owners`` maps every predecessor snapshot id named anywhere in the
    walk to the ``(task_id, turn_number)`` that owns it. It is how a foreign-task
    or non-existent predecessor is detected without a database.

    ``earliest_snapshot_turn`` is the lowest turn number this task has a criteria
    snapshot for, or ``None`` if it has none. It exists for exactly one check:
    whether a ``root`` declaration really is the first one.
    """

    task_id: str
    target_turn_number: int
    nodes: Mapping[int, LineageNode]
    snapshot_owners: Mapping[str, Tuple[str, int]]
    earliest_snapshot_turn: Optional[int] = None


# -- the fingerprint ----------------------------------------------------------
#
# The same domain-tagged, length-prefixed construction `criteria.py` records at
# length, with a tag of this module's own. Length-prefixed rather than
# delimited, because a delimiter is a character that can appear in a value.

TAG_FINGERPRINT = b"cofferdam.criteria.lineage.v1"
LENGTH_PREFIX_WIDTH = 8
FINGERPRINT_CHARS = 64


class _Fingerprint:
    """A domain-tagged, length-prefixed SHA-256 over an ordered field list."""

    __slots__ = ("_hasher",)

    def __init__(self) -> None:
        self._hasher = hashlib.sha256()
        self._hasher.update(TAG_FINGERPRINT)

    def field(self, value: object) -> "_Fingerprint":
        if value is None:
            data = b"\x00none"
        elif isinstance(value, bool):
            data = b"\x00bool:" + (b"1" if value else b"0")
        elif isinstance(value, int):
            data = b"\x00int:" + str(value).encode("utf-8")
        else:
            data = b"\x00str:" + str(value).encode("utf-8")
        self._hasher.update(len(data).to_bytes(LENGTH_PREFIX_WIDTH, "big"))
        self._hasher.update(data)
        return self

    def hexdigest(self) -> str:
        return self._hasher.hexdigest()


def resolved_fingerprint(
    task_id: str,
    target_turn_number: int,
    target_snapshot_id: str,
    active: Sequence[ActiveCriterion],
    lineage: Sequence[LineageStep],
) -> str:
    """A stable hash of exactly this resolution's identity.

    What is in it
    -------------

    The resolver version; the exact target (task, turn, snapshot id); the active
    set as **ordered** ``(source snapshot id, criterion id)`` pairs; and every
    lineage step actually consumed, oldest first, by turn number, snapshot id,
    mode, criteria fingerprint and continuity fingerprint.

    Ids are in, and here that is right even though
    :func:`~.criteria.criteria_fingerprint` deliberately keeps them out. The two
    hashes answer different questions. That one asks *what was required*, where
    two turns given identical requirements should agree. This one asks *which
    exact stored criteria are live at this exact turn*, which is a statement
    about specific rows — the same reason
    :func:`~.continuity.continuity_fingerprint` binds snapshot ids, and the same
    reason lineage may never be inferred from matching text.

    The order is hashed as an explicit position rather than implied by field
    order, so the same set in a different order is a visibly different value.
    That matters: ordering is part of what a resolution asserts.

    The consumed steps are in because the *same* active set reached by different
    lineage is not the same fact. Two turns can arrive at criteria ``A, B`` — one
    by ``replace``, one by ``extend`` over a predecessor that happened to be
    empty — and an audit must be able to tell them apart.

    What is deliberately **not** in it
    ----------------------------------

    * **A predecessor a ``replace`` did not traverse.** Binding it would make the
      hash claim a dependency the resolution never had. ``replace`` cuts the
      chain, and the fingerprint says so honestly.
    * **Continuity ids and supersession ids.** Minted per row, carrying a clock
      and randomness; they identify the row rather than the relationship, and
      would move on every re-reservation.
    * **Any clock.** Not the read time, not ``recorded_at``. It must survive a
      restart and a process boundary unchanged.
    * **Database rowids or ``SELECT`` order.** Every ordering here is a stored
      ``ordinal`` or the walk's own.
    * **Absolute host paths, deployment slots, provider or session identifiers.**
      None is part of what is required, and a deployment that moves must not move
      a resolved identity.
    * **The repository.** Nothing about the working tree is an input, so deleting
      it changes nothing.
    """
    digest = _Fingerprint()
    digest.field("cofferdam.criteria.lineage")
    digest.field(RESOLVER_VERSION)
    digest.field(task_id)
    digest.field(target_turn_number)
    digest.field(target_snapshot_id)
    digest.field(len(active))
    for position, entry in enumerate(active, start=1):
        digest.field(position)
        digest.field(entry.source_snapshot_id)
        digest.field(entry.criterion_id)
    digest.field(len(lineage))
    for step in lineage:
        digest.field(step.turn_number)
        digest.field(step.snapshot_id)
        digest.field(step.mode)
        digest.field(step.criteria_fingerprint)
        digest.field(step.continuity_fingerprint)
    return digest.hexdigest()


# -- validation of one stored node --------------------------------------------


def _state_problem(node: LineageNode) -> Optional[str]:
    """Whether a durable declaration exists for this turn at all.

    Checked before anything else, because it is the question Stop Gate 2 asks
    first and because the other two answers would be misleading in its absence: a
    turn with no rows whatsoever is a turn that predates continuity, not a turn
    whose snapshot went missing.
    """
    state = node.continuity.state
    if state == CONTINUITY_LEGACY_UNKNOWN:
        return REASON_LEGACY_UNKNOWN
    if state == CONTINUITY_NOT_DECLARED:
        return REASON_NOT_DECLARED
    if state != CONTINUITY_DECLARED:
        return REASON_MALFORMED_LINEAGE
    return None


def _snapshot_problem(node: LineageNode) -> Optional[str]:
    """Whether this turn's criteria snapshot is internally coherent."""
    snapshot = node.snapshot
    if snapshot.state == CRITERIA_LEGACY_UNKNOWN or snapshot.snapshot_id is None:
        return REASON_CRITERIA_SNAPSHOT_MISSING
    if snapshot.state not in (CRITERIA_PRESENT, CRITERIA_NOT_PROVIDED):
        return REASON_MALFORMED_LINEAGE
    criteria = snapshot.criteria
    if (snapshot.state == CRITERIA_PRESENT) != bool(criteria):
        # The one invariant that stops `not_provided` being read as an empty
        # criterion SET, restated here because a read must not depend on the
        # CHECK constraint having been enforced by the writer that made the row.
        return REASON_MALFORMED_LINEAGE
    if snapshot.criterion_count != len(criteria):
        return REASON_MALFORMED_LINEAGE
    ordinals = set()
    for criterion in criteria:
        if not criterion.criterion_id:
            return REASON_MALFORMED_LINEAGE
        if criterion.ordinal in ordinals:
            return REASON_MALFORMED_LINEAGE
        ordinals.add(criterion.ordinal)
    return None


def _declaration_problem(graph: LineageGraph, node: LineageNode) -> Optional[str]:
    """Whether this turn's declaration is one a valid write could have produced.

    Runs after :func:`_state_problem` and :func:`_snapshot_problem`, so a
    declaration is only examined once it is known to exist and to have a snapshot
    to be about.

    Applied to **every** mode including ``replace``, whose predecessor is checked
    for existence, ownership and ordering here even though its active set is
    never traversed. Cutting a dependency does not mean believing a malformed
    declaration.

    The strictly-earlier rule is checked here only for the modes that do not
    traverse. For ``extend`` and ``revise`` it is checked at the moment the link
    is followed, *after* the cycle guard — refusing to re-enter the walk takes
    precedence over explaining which way the link pointed.
    """
    continuity = node.continuity
    mode = continuity.mode
    if mode not in CONTINUITY_MODES:
        return REASON_MALFORMED_LINEAGE
    if not continuity.continuity_fingerprint:
        return REASON_MALFORMED_LINEAGE

    if continuity.current_snapshot_id != node.snapshot.snapshot_id:
        return REASON_SNAPSHOT_MISMATCH

    relations = continuity.relations
    if len(relations) != continuity.relation_count:
        return REASON_MALFORMED_LINEAGE
    if bool(relations) != (mode == CONTINUITY_REVISE):
        return REASON_RELATIONS_MODE_MISMATCH

    predecessor = continuity.predecessor_snapshot_id
    if mode == CONTINUITY_ROOT:
        if predecessor is not None:
            return REASON_ROOT_HAS_PREDECESSOR
        if (
            graph.earliest_snapshot_turn is not None
            and graph.earliest_snapshot_turn < node.turn_number
        ):
            # A `root` that is not the first snapshot is a contradiction, and it
            # is never quietly reinterpreted as `replace` — that would be the
            # resolver inventing the very declaration PR10 requires somebody to
            # make.
            return REASON_ROOT_NOT_FIRST
        return None

    if predecessor is None:
        return REASON_MALFORMED_LINEAGE
    owner = graph.snapshot_owners.get(predecessor)
    if owner is None:
        return REASON_PREDECESSOR_MISSING
    if owner[0] != graph.task_id:
        return REASON_PREDECESSOR_FOREIGN_TASK
    if mode == CONTINUITY_REPLACE and owner[1] >= node.turn_number:
        return REASON_PREDECESSOR_NOT_EARLIER
    return None


# -- the walk -----------------------------------------------------------------


def _unavailable(
    graph: LineageGraph, reason: str, at_turn: int, cause: Optional[str] = None
) -> LineageUnavailable:
    return LineageUnavailable(
        task_id=graph.task_id,
        target_turn_number=graph.target_turn_number,
        resolver_version=RESOLVER_VERSION,
        reason=reason,
        at_turn_number=at_turn,
        cause=cause,
    )


def _for_target(
    graph: LineageGraph, reason: str, at_turn: int
) -> LineageUnavailable:
    """A failure at a predecessor, reported as the target's answer.

    The outer reason says *a dependency this turn stands on is unknown*; the
    cause and the turn number say which and why. Both matter: a caller needs to
    know it may not proceed, and an audit needs to know where the chain broke.
    """
    if at_turn == graph.target_turn_number:
        return _unavailable(graph, reason, at_turn)
    return _unavailable(graph, REASON_PREDECESSOR_UNAVAILABLE, at_turn, cause=reason)


def _chain(graph: LineageGraph):
    """The turns this resolution depends on, target first — or a refusal.

    Stops at ``root`` and at ``replace``: neither needs the turn before it, and
    walking past a cut point would make the trace and the fingerprint claim a
    dependency the resolution does not have.
    """
    path: List[LineageNode] = []
    visited: Dict[int, bool] = {}
    turn = graph.target_turn_number

    while True:
        if len(path) >= MAX_LINEAGE_DEPTH:
            return _for_target(graph, REASON_DEPTH_EXCEEDED, turn)
        node = graph.nodes.get(turn)
        if node is None:
            # The fetcher was asked for this turn and did not produce it. Never a
            # reason to go and look: the graph is the whole world here.
            return _for_target(graph, REASON_MALFORMED_LINEAGE, turn)

        problem = (
            _state_problem(node)
            or _snapshot_problem(node)
            or _declaration_problem(graph, node)
        )
        if problem is not None:
            return _for_target(graph, problem, turn)

        visited[turn] = True
        path.append(node)

        mode = node.continuity.mode
        if mode in (CONTINUITY_ROOT, CONTINUITY_REPLACE):
            return path

        # `extend` and `revise` are statements about the prior active set, so the
        # link is followed rather than cut.
        owner = graph.snapshot_owners[node.continuity.predecessor_snapshot_id]
        previous = owner[1]
        if previous in visited:
            return _for_target(graph, REASON_CYCLE_DETECTED, turn)
        if previous >= turn:
            return _for_target(graph, REASON_PREDECESSOR_NOT_EARLIER, turn)
        turn = previous


def _entries(node: LineageNode) -> Tuple[ActiveCriterion, ...]:
    """This turn's own criteria as active entries, in stored ordinal order."""
    snapshot_id = node.snapshot.snapshot_id
    return tuple(
        ActiveCriterion(
            criterion_id=criterion.criterion_id,
            source_snapshot_id=snapshot_id,
            source_turn_number=node.turn_number,
            source_ordinal=criterion.ordinal,
            criterion=criterion,
        )
        # Stored ordinal, never rowid and never the order a SELECT returned.
        for criterion in sorted(node.snapshot.criteria, key=lambda item: item.ordinal)
    )


def resolve(graph: LineageGraph):
    """Which criteria are active at the target turn. Pure.

    Returns :class:`ResolvedActiveCriteria` or :class:`LineageUnavailable`, and
    never raises for stored data it dislikes — a corrupted row is an
    *unavailable* answer with a closed reason, not an exception whose message a
    caller might treat as authority.

    The four modes, exactly:

    ``root``
        active = this snapshot's criteria. No predecessor is traversed, because
        there is none.

    ``extend``
        active = the predecessor's resolved active set, **then** this snapshot's
        criteria. Nothing is deduplicated by text, path or fingerprint: criterion
        ids are the only identity, and two turns may legitimately require the
        same-looking thing for different reasons.

    ``replace``
        active = this snapshot's criteria. The predecessor's active set is not
        required, so an unknown or undeclared history does not block it. This is
        the cut point that lets a task recover a knowable requirement set.

    ``revise``
        active = the predecessor's resolved active set **minus** every criterion
        an explicit relation retires, in place, **then** this snapshot's
        criteria. A relation whose old side is not *active* in that set is
        refused rather than ignored.

    Ordering is inheritance-first and stable: surviving inherited entries keep
    their relative order, removals happen in place, and this turn's own criteria
    follow in stored ordinal order. Nothing is sorted by id, text, path or
    fingerprint — the order a person submitted requirements in is a fact about
    the requirements.
    """
    chain = _chain(graph)
    if isinstance(chain, LineageUnavailable):
        return chain

    known_ids = {
        criterion.criterion_id
        for node in chain
        for criterion in node.snapshot.criteria
    }

    active: Tuple[ActiveCriterion, ...] = ()
    steps: List[LineageStep] = []

    # Oldest first: the cut point (or the root) establishes a set, and each later
    # turn transforms it.
    for node in reversed(chain):
        mode = node.continuity.mode
        current = _entries(node)

        if mode in (CONTINUITY_ROOT, CONTINUITY_REPLACE):
            active = current
        elif mode == CONTINUITY_EXTEND:
            active = active + current
        else:  # revise — the only mode with relations, enforced above.
            current_ids = {entry.criterion_id for entry in current}
            active_ids = {entry.criterion_id for entry in active}
            retired = set()
            for relation in node.continuity.relations:
                if relation.criterion_id not in current_ids:
                    return _for_target(
                        graph, REASON_SUPERSESSION_CURRENT_UNKNOWN, node.turn_number
                    )
                target = relation.predecessor_criterion_id
                if target not in active_ids:
                    # Historical membership is not active membership. A criterion
                    # that some earlier turn retired cannot be retired again, and
                    # the stale edge is refused rather than skipped: skipping it
                    # would leave the resolver asserting an active set that no
                    # declaration produces.
                    reason = (
                        REASON_SUPERSESSION_TARGET_NOT_ACTIVE
                        if target in known_ids
                        else REASON_SUPERSESSION_PREDECESSOR_UNKNOWN
                    )
                    return _for_target(graph, reason, node.turn_number)
                retired.add(target)
            # Removed in place, so the survivors keep the order they were
            # inherited in. A relation that names the same old criterion twice
            # removes it once, and several relations naming one new criterion do
            # not add it more than once: `current` is appended whole, exactly
            # once, whatever the relations say.
            active = tuple(
                entry for entry in active if entry.criterion_id not in retired
            ) + current

        steps.append(
            LineageStep(
                turn_number=node.turn_number,
                snapshot_id=node.snapshot.snapshot_id,
                criteria_fingerprint=node.snapshot.fingerprint or "",
                continuity_fingerprint=node.continuity.continuity_fingerprint or "",
                mode=mode,
                continuity_id=node.continuity.continuity_id,
            )
        )

    seen = set()
    for entry in active:
        if entry.criterion_id in seen:
            return _for_target(
                graph, REASON_DUPLICATE_ACTIVE_CRITERION, graph.target_turn_number
            )
        seen.add(entry.criterion_id)

    target = chain[0]
    lineage = tuple(steps)
    return ResolvedActiveCriteria(
        task_id=graph.task_id,
        target_turn_number=graph.target_turn_number,
        target_snapshot_id=target.snapshot.snapshot_id,
        resolver_version=RESOLVER_VERSION,
        active=active,
        lineage=lineage,
        fingerprint=resolved_fingerprint(
            graph.task_id,
            graph.target_turn_number,
            target.snapshot.snapshot_id,
            active,
            lineage,
        ),
    )


__all__ = [
    "FINGERPRINT_CHARS",
    "MAX_LINEAGE_DEPTH",
    "REASONS",
    "REASON_CRITERIA_SNAPSHOT_MISSING",
    "REASON_CYCLE_DETECTED",
    "REASON_DEPTH_EXCEEDED",
    "REASON_DUPLICATE_ACTIVE_CRITERION",
    "REASON_LEGACY_UNKNOWN",
    "REASON_MALFORMED_LINEAGE",
    "REASON_NOT_DECLARED",
    "REASON_PREDECESSOR_FOREIGN_TASK",
    "REASON_PREDECESSOR_MISSING",
    "REASON_PREDECESSOR_NOT_EARLIER",
    "REASON_PREDECESSOR_UNAVAILABLE",
    "REASON_RELATIONS_MODE_MISMATCH",
    "REASON_ROOT_HAS_PREDECESSOR",
    "REASON_ROOT_NOT_FIRST",
    "REASON_SNAPSHOT_MISMATCH",
    "REASON_SUPERSESSION_CURRENT_UNKNOWN",
    "REASON_SUPERSESSION_PREDECESSOR_UNKNOWN",
    "REASON_SUPERSESSION_TARGET_NOT_ACTIVE",
    "RESOLUTION_RESOLVED",
    "RESOLUTION_STATES",
    "RESOLUTION_UNAVAILABLE",
    "RESOLVER_VERSION",
    "TAG_FINGERPRINT",
    "ActiveCriterion",
    "LineageGraph",
    "LineageNode",
    "LineageStep",
    "LineageUnavailable",
    "ResolvedActiveCriteria",
    "resolve",
    "resolved_fingerprint",
]
