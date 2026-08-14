"""The derived evidence bundle: what was claimed, what was seen, and the gaps.

M2K PR2. PR1 gave Cofferdam the two halves of a sentence — a ``ChangeClaim``
saying what a worker reported, and a ``git_observed`` ``EvidenceReference``
saying what Cofferdam went and looked at — and deliberately left them sitting
beside each other with nothing noticing they agreed. This module is the noticing.

**Model-free.** Nothing here calls a provider, and there is no import that could
reach one. What it does is set arithmetic over stored rows.

Derived, never persisted
------------------------

There is no ``task_evidence_bundles`` table and there is not going to be one.
A bundle is built on read from facts that are already durable and already
immutable:

* ``task_change_claims`` — stored, append-only in practice, never rewritten.
* ``task_claim_ingestion`` — stored, one row per recording.
* ``task_events.evidence_json`` — append-only history of what was observed.
* ``task_turn_bounds`` — the schema-v5 sequence range the turn owns.

Assembly re-runs nothing. It does not execute Git, it does not open a file, it
does not consult the working tree. That is what makes the result *historical*
rather than *current*: a repository edited an hour after a task finished cannot
change what this says about that task, because none of its inputs live in the
repository. A bundle assembled today and the same bundle assembled next year
from the same rows are the same bundle, and :func:`input_fingerprint` is how a
caller can prove that without diffing them.

Persisting a bundle would create a second source of truth that could drift from
the first, and would have to be migrated every time the assembler improved. A
future ``EvaluationRecord`` that needs to refer to *this* evidence refers to it
by ``(task_id, turn_number, assembler_version, input_fingerprint)`` — four small
values that identify a snapshot without copying it.

What agreement means, and what it does not
------------------------------------------

This is the part that is easy to get wrong in a way nobody notices for a year.

Today's machine observation is ``git status --porcelain``, reduced to *this
project-relative path appears in the changed set*. That proves the path changed.
It does **not** prove the file was created, or modified, or deleted, or renamed
— porcelain's status letters are not carried into the stored
:class:`~.models.EvidenceReference`, so the durable record genuinely does not
contain that information.

So a claim of ``modified src/foo.py`` matched against an observation that
``src/foo.py`` changed produces:

    relationship      = ``path_agreed``
    path_agreement    = ``True``
    operation_agreement = ``unknown``

and it must never be rendered, summarised or re-encoded as "modification
verified". The vocabulary here is deliberately ``path_agreed`` rather than a
generic ``agreed`` for exactly that reason: an unqualified word invites a reader
to supply the qualification themselves, and they will supply the strongest one.

``claim_only`` means unmatched. Not false, not dishonest, not contradicted —
**unverified**. A worker may have changed a file and committed it, in which case
``git status`` correctly reports a clean tree and there is nothing to match.

``observed_only`` means Cofferdam saw a path change that no claim named. That is
not evidence of a worker concealing anything either: the claim set may simply be
incomplete, which is what :class:`~.claims.ClaimIngestion` records, and the
completeness state travels next to every ``observed_only`` group so the two are
read together.

There is no ``claim_conflict`` in this build. Not because conflicts do not
matter but because **no currently supported observation can prove one**. To say
a claim and an observation are incompatible, the observation would have to carry
semantics like "this path does not exist" or "this path was created, not
modified", and the stored evidence carries neither. Emitting a conflict from the
absence of a match would be manufacturing the strongest possible statement out
of the weakest possible evidence. When an observation type arrives that can
prove incompatibility, this is where it goes.

No verdict
----------

No pass, no fail, no score, no confidence, no risk level. There is no field for
one, no function that returns one, and no caller that could compute one from
what is published here without inventing the missing half itself.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .claims import (
    CLAIM_CREATED,
    CLAIM_DELETED,
    CLAIM_MODIFIED,
    CLAIM_RENAMED,
    ChangeClaim,
    ClaimIngestion,
    normalize_claim_path,
)
from .models import (
    CHANGE_CREATED,
    STATUS_FACTS,
    CHANGE_DELETED,
    CHANGE_KINDS,
    CHANGE_MODIFIED,
    CHANGE_RENAMED,
    CHANGE_UNKNOWN,
    EVIDENCE_ARTIFACT,
    EVIDENCE_FILE,
    EVIDENCE_GIT_OBSERVED,
    EvidenceReference,
    TaskEvent,
)
from .turns import TaskTurn, TurnBound

# -- versions -----------------------------------------------------------------

#: The shape of the published bundle. Bumped when a **field** changes, so a
#: client that stored one can tell whether it is reading the shape it knows.
BUNDLE_VERSION = 1

#: The code that assembled it. Bumped when the **rules** change — what counts as
#: an eligible observation, how relationships are grouped, what enters the
#: fingerprint — even if no field moves.
#:
#: Its own number, separate from ``BUNDLE_VERSION``, because the two answer
#: different questions. A client asks "can I parse this"; a stored
#: ``input_fingerprint`` asks "was this produced by the same rules". Two bundles
#: with the same inputs and different assembler versions may legitimately differ,
#: and a caller that could not see that would treat an improvement as corruption.
ASSEMBLER_VERSION = 2

# -- turn attribution ---------------------------------------------------------

#: The turn's exact event range is recorded and was used.
ATTRIBUTION_EXACT = "exact"

#: The turn predates schema v5. **No range was recorded**, so no event can be
#: attributed to it, and none is guessed at from timestamps, event types, the
#: nearest sequence or the task's state. A legacy turn's bundle carries its
#: claims — those have their own durable ``turn_number`` — and no machine
#: observations at all. That is a smaller answer than a reader might want and it
#: is the only true one.
ATTRIBUTION_LEGACY_UNKNOWN = "legacy_unknown"

TURN_ATTRIBUTIONS: Tuple[str, ...] = (ATTRIBUTION_EXACT, ATTRIBUTION_LEGACY_UNKNOWN)

# -- claim-set completeness ---------------------------------------------------
#
# Four states, and the third and fourth are the reason this is not a boolean.

#: Every submitted claim in this turn's ingestion rows became a stored claim.
COMPLETENESS_COMPLETE = "complete"

#: At least one submission was rejected or truncated. The counts say how many
#: and against which closed reason code; the rejected payloads are not stored,
#: by design.
COMPLETENESS_INCOMPLETE = "incomplete"

#: The turn predates schema v5, so its ingestion rows cannot be attributed to it
#: with certainty even when they exist.
COMPLETENESS_LEGACY_UNKNOWN = "legacy_unknown"

#: A post-v5 turn with **no ingestion row at all**.
#:
#: This state exists because the PR1 write path can legitimately produce it, and
#: calling it ``complete`` would be a lie the record cannot support.
#: ``TaskService._record_change_claims`` returns without writing anything when
#: the adapter reported no claims, when the task's project has been removed or
#: disabled, and when the project root fails re-verification — three different
#: facts that leave the same absence behind. "The worker reported nothing" and
#: "Cofferdam could not establish where to look" are not the same, and neither
#: of them is "the claim set is complete".
COMPLETENESS_INGESTION_MISSING = "ingestion_missing"

COMPLETENESS_STATES: Tuple[str, ...] = (
    COMPLETENESS_COMPLETE,
    COMPLETENESS_INCOMPLETE,
    COMPLETENESS_LEGACY_UNKNOWN,
    COMPLETENESS_INGESTION_MISSING,
)

# -- relationships ------------------------------------------------------------

#: A claim and a machine observation name the same project-relative path.
#: **Path agreement only.** See the module docstring.
RELATIONSHIP_PATH_AGREED = "path_agreed"

#: A stored claim with no eligible machine path observation in this turn.
#: Unmatched and unverified — not false, not dishonest, not contradicted.
RELATIONSHIP_CLAIM_ONLY = "claim_only"

#: An eligible machine path observation that no claim in this turn names. Read
#: together with the claim-set completeness beside it.
RELATIONSHIP_OBSERVED_ONLY = "observed_only"

#: A claim and a machine observation name the same path and describe
#: **explicitly incompatible** operations (M2K PR3).
#:
#: The bar is deliberately high, and it is the reason PR2 emitted none of these:
#: a conflict requires the machine to have *said* something, not to have stayed
#: silent. A claim with no observation is `claim_only`. A claim matched against a
#: pre-PR3 observation, which carries no operation at all, is `path_agreed` with
#: `operation_agreement: unknown`. A claim matched against an unmerged or
#: type-changed path is the same. Only two positive machine facts that cannot
#: both describe one file produce this.
#:
#: **It is not a verdict.** It does not mean the task failed, the acceptance
#: criteria failed, or the worker was dishonest. It means two records disagree,
#: which is a thing a person should look at and nothing this code may conclude
#: from. A worker that deleted a file it had earlier said it modified produced a
#: conflict and did nothing wrong.
RELATIONSHIP_CLAIM_CONFLICT = "claim_conflict"

RELATIONSHIPS: Tuple[str, ...] = (
    RELATIONSHIP_PATH_AGREED,
    RELATIONSHIP_CLAIM_ONLY,
    RELATIONSHIP_OBSERVED_ONLY,
    RELATIONSHIP_CLAIM_CONFLICT,
)

#: What the evidence says about the *operation*, as opposed to the path.
#:
#: Only ever ``unknown`` in this build, and that is a finding rather than a
#: placeholder: no supported observation carries an operation, so there is
#: nothing that could set it to anything else. It is published as an explicit
#: field rather than omitted so that a reader is told the question was asked and
#: could not be answered, instead of being left to assume it was not asked.
OPERATION_UNKNOWN = "unknown"

#: The claim's operation and the machine's change kind describe the same thing.
OPERATION_AGREED = "true"

#: They describe things that cannot both be true of one path.
OPERATION_DIFFERS = "false"

OPERATION_AGREEMENTS: Tuple[str, ...] = (
    OPERATION_AGREED,
    OPERATION_DIFFERS,
    OPERATION_UNKNOWN,
)

# -- the operation compatibility table ----------------------------------------
#
# One table and one helper. The alternative — `if` statements over claim
# operations and change kinds spread through the assembler — is where a
# wrong-but-confident answer hides, and this is the comparison the whole
# milestone exists to make honestly.
#
# What is compared, and why it is a SET
# -------------------------------------
#
# Git's two-character `XY` is two columns: `X` is the index against HEAD, `Y` is
# the working tree against the index. So one status routinely proves **two**
# facts — `RM` is *renamed and then modified*, `AM` is *added and then
# modified*, `MD` is *modified and then deleted*.
#
# Reducing that to one label and then comparing the label is what produces a
# false conflict: a worker who truthfully reports "modified" after a rename is
# contradicted by an observation that *also* proves a modification, purely
# because the label said "renamed". So agreement is decided against the whole
# fact set:
#
#   * the claim matches **any** proven fact          -> true
#   * the claim is incompatible with **every** fact  -> false
#   * anything else                                  -> unknown
#
# One reconciling fact is enough to stop a contradiction. That is the safety
# property this shape exists for.
#
# Pairwise incompatibility below is only about what cannot both be true of one
# path against one HEAD. `created` versus `modified` is deliberately **not** in
# it: a worker that creates a file and then edits it truthfully says "created"
# while Git reports whichever the state supports, and calling that a
# contradiction would manufacture conflicts out of ordinary work.
#
# Why `renamed` appears in no pair: a rename is a fact about two paths, and
# :func:`_rename_agreement` answers it using both. Nothing here can contradict
# a rename claim, which is the conservative direction.
_INCOMPATIBLE: frozenset = frozenset(
    {
        (CLAIM_CREATED, CHANGE_DELETED),
        (CLAIM_MODIFIED, CHANGE_DELETED),
        (CLAIM_DELETED, CHANGE_CREATED),
        (CLAIM_DELETED, CHANGE_MODIFIED),
    }
)


def status_facts(status: object) -> frozenset:
    """Every machine fact one raw status proves. Empty when it proves none."""
    if not isinstance(status, str) or len(status) != 2:
        return frozenset()
    return STATUS_FACTS.get(status, frozenset())


def _facts_for(change_kind: object, status: object) -> frozenset:
    """Everything the machine proved about one path.

    The exact status is authoritative when present, because it carries both
    columns. A row written without one — anything from before that field
    existed — falls back to its single label, which is all it ever knew.
    """
    facts = status_facts(status)
    if facts:
        return facts
    if isinstance(change_kind, str) and change_kind in CHANGE_KINDS:
        if change_kind == CHANGE_UNKNOWN:
            return frozenset()
        return frozenset({change_kind})
    return frozenset()


def operation_agreement(
    claim_operation: object, change_kind: object, status: object = None
) -> str:
    """Whether one claim operation and one machine observation agree.

    Three answers and no fourth. ``unknown`` covers every case the evidence does
    not settle, which includes every observation recorded before machine
    semantics existed — those carry neither a kind nor a status, so nothing is
    proven and nothing is concluded.
    """
    if not isinstance(claim_operation, str):
        return OPERATION_UNKNOWN
    if claim_operation == CLAIM_RENAMED:
        # A rename is a fact about **two** paths, and this function is given
        # one. Even a machine status that proves `renamed` does not establish
        # that it is *this* rename — same destination, different source is a
        # different event. :func:`_rename_agreement` is the only thing that may
        # answer it, and it uses both paths. Returning `unknown` here rather
        # than agreeing on the word keeps that structural: a caller that reached
        # this function with a rename claim cannot get an unqualified agreement
        # out of it.
        return OPERATION_UNKNOWN
    facts = _facts_for(change_kind, status)
    if not facts:
        return OPERATION_UNKNOWN
    if claim_operation in facts:
        return OPERATION_AGREED
    if all((claim_operation, fact) in _INCOMPATIBLE for fact in facts):
        return OPERATION_DIFFERS
    return OPERATION_UNKNOWN

# -- limitations --------------------------------------------------------------
#
# Closed, code-owned reason codes. A limitation is what the bundle carries
# *instead of* a fact it could not establish; it never contains a path, and it
# is never an exception a caller has to interpret.

#: The turn predates schema v5 and owns no attributable events.
LIMIT_LEGACY_TURN = "legacy_turn_attribution_unavailable"

#: Ingestion rows exist for this task but none names this turn.
LIMIT_INGESTION_MISSING = "claim_ingestion_record_missing"

#: The stored claim set for this turn is known to be short.
LIMIT_CLAIM_SET_INCOMPLETE = "claim_set_incomplete"

#: A ``git_observed`` reference this build cannot interpret as a path change.
LIMIT_UNSUPPORTED_OBSERVATION = "unsupported_observation_shape"

#: Git reported more changed paths than the emitter recorded, or the emitter
#: refused some of them (M2K PR3). The machine observation set for this turn is
#: **not** known to be whole, so the absence of an observation at a path is not
#: evidence that nothing happened there.
LIMIT_OBSERVATIONS_INCOMPLETE = "machine_observations_incomplete"

#: More claims, observations or paths than the bundle publishes. The fact is
#: kept even though the items are not, because a truncated list that does not
#: say it is truncated reads as a complete one.
LIMIT_CLAIMS_TRUNCATED = "claims_truncated"
LIMIT_OBSERVATIONS_TRUNCATED = "observations_truncated"
LIMIT_PATHS_TRUNCATED = "relationship_paths_truncated"
LIMIT_EVENTS_TRUNCATED = "events_truncated"
LIMIT_SOURCES_TRUNCATED = "relationship_sources_truncated"

LIMITATIONS: Tuple[str, ...] = (
    LIMIT_LEGACY_TURN,
    LIMIT_OBSERVATIONS_INCOMPLETE,
    LIMIT_INGESTION_MISSING,
    LIMIT_CLAIM_SET_INCOMPLETE,
    LIMIT_UNSUPPORTED_OBSERVATION,
    LIMIT_CLAIMS_TRUNCATED,
    LIMIT_OBSERVATIONS_TRUNCATED,
    LIMIT_PATHS_TRUNCATED,
    LIMIT_EVENTS_TRUNCATED,
    LIMIT_SOURCES_TRUNCATED,
)

# -- bounds -------------------------------------------------------------------
#
# Every list this module publishes is capped, and every cap that bites emits a
# limitation. Applied to the *assembled* bundle rather than trusted from the
# store, because this is the last thing between a row and a serialized response.

#: Claims published per bundle.
MAX_BUNDLE_CLAIMS = 64

#: Machine observations published per bundle.
MAX_BUNDLE_OBSERVATIONS = 64

#: Distinct semantic paths that get their own relationship group.
MAX_BUNDLE_PATHS = 64

#: Source identities listed inside one relationship group. This is the bound
#: that stops an N x M explosion from becoming a payload: eight duplicate claims
#: and eight duplicate observations of one path is one group with two capped
#: lists, not sixty-four pairs.
MAX_GROUP_SOURCES = 8

#: Events scanned for eligible observations inside the turn window.
MAX_BUNDLE_EVENTS = 200

#: Ingestion rows aggregated per turn.
MAX_BUNDLE_INGESTION_ROWS = 32

# -- the fingerprint ----------------------------------------------------------
#
# The same discipline `mind/hashing.py` and `claims.artifact_digest` record, with
# a tag of this module's own:
#
#     SHA256( tag || length-prefixed field || length-prefixed field || ... )
#
# Length-prefixed rather than delimited, because a delimiter is a character that
# can appear in a value. A path is attacker-influenced text; joining fields with
# `:` would let `a/b:c` and `a` + `b:c` produce one string, and two different
# input sets would fingerprint the same.

TAG_FINGERPRINT = b"cofferdam.evidence.bundle.v1"
LENGTH_PREFIX_WIDTH = 8
FINGERPRINT_CHARS = 64

#: Written into the fingerprint where a turn is still open, so "closed through
#: sequence 12" and "open above sequence 12" cannot hash alike.
MARKER_OPEN = "OPEN"

#: Written where no bound exists at all.
MARKER_LEGACY_UNKNOWN = "LEGACY_UNKNOWN"


class _Fingerprint:
    """A domain-tagged, length-prefixed SHA-256 over an ordered field list.

    Deliberately not ``json.dumps`` of a dict. Key order, separator whitespace
    and float formatting are all things a serializer is entitled to change
    between releases, and any of them changing would move every stored
    fingerprint without a single input having changed.
    """

    __slots__ = ("_hasher",)

    def __init__(self) -> None:
        self._hasher = hashlib.sha256()
        self._hasher.update(TAG_FINGERPRINT)

    def field(self, value: object) -> "_Fingerprint":
        """One field, length-prefixed. ``None`` is its own value, not empty."""
        if value is None:
            data = b"\x00none"
        elif isinstance(value, bool):
            # Before `int`: `bool` is a subclass, and `True` and `1` are
            # different inputs that must not hash alike.
            data = b"\x00bool:" + (b"1" if value else b"0")
        elif isinstance(value, int):
            data = b"\x00int:" + str(value).encode("utf-8")
        else:
            data = b"\x00str:" + str(value).encode("utf-8")
        self._hasher.update(len(data).to_bytes(LENGTH_PREFIX_WIDTH, "big"))
        self._hasher.update(data)
        return self

    def fields(self, values: Sequence[object]) -> "_Fingerprint":
        for value in values:
            self.field(value)
        return self

    def hexdigest(self) -> str:
        return self._hasher.hexdigest()


# -- observation eligibility --------------------------------------------------


def observation_path(reference: object) -> Optional[str]:
    """The project-relative path a ``git_observed`` reference proves changed.

    ``None`` for **everything else**, and the everything else is the point of
    this function existing rather than being three inline conditions.

    ``git_evidence`` in the Claude Code adapter emits three different shapes and
    all three carry ``source="git_observed"``:

    1. ``evidence_type="file"``, ``operation="git status"``, ``result="changed"``
       and ``identifier`` a project-relative path — the path-change observation,
       and the only one that participates in matching.
    2. ``evidence_type="commit"``, ``operation="rev-parse HEAD"`` and
       ``identifier`` a twelve-character abbreviated commit id. Its identifier
       is a hex string, not a path, and a matcher that accepted any non-empty
       identifier from a ``git_observed`` reference would happily compare a
       commit id to a claimed filename. That is the specific mistake this
       function exists to make impossible.
    3. ``evidence_type="artifact"``, ``operation="git status"``,
       ``identifier=None``, ``result="no files changed"`` — the clean-tree
       statement. Note that it shares ``operation`` with shape 1, so eligibility
       cannot be decided on the operation alone either.

    Every condition below is checked rather than assumed, and the path is put
    through the same lexical gate a claim goes through, so an observation and a
    claim are compared on the same notion of "path" or not compared at all.
    """
    if not isinstance(reference, EvidenceReference):
        return None
    if reference.source != EVIDENCE_GIT_OBSERVED:
        return None
    if reference.evidence_type != EVIDENCE_FILE:
        return None
    if reference.operation != "git status":
        return None
    if reference.result != "changed":
        return None
    try:
        return normalize_claim_path(reference.identifier)
    except Exception:
        # A stored identifier this build cannot read as a plain project-relative
        # path is not silently reshaped into one. The caller records an
        # `unsupported_observation_shape` limitation instead.
        return None


def observation_change_kind(reference: object) -> Optional[str]:
    """The machine change kind on an eligible observation, or ``None``.

    ``None`` for every observation written before M2K PR3, and for any value a
    later build might invent that this one does not know. Both are the same
    honest answer — the operation is not established — and neither is allowed to
    become a guess.
    """
    if not isinstance(reference, EvidenceReference):
        return None
    kind = reference.change_kind
    if not isinstance(kind, str) or kind not in CHANGE_KINDS:
        return None
    return kind


def observation_status(reference: object) -> Optional[str]:
    """The raw machine status on an eligible observation, or ``None``.

    Bounded to what a status can be, so a stored value that is not one cannot
    reach the fact table and cannot become a payload.
    """
    if not isinstance(reference, EvidenceReference):
        return None
    status = reference.change_status
    if not isinstance(status, str) or len(status) != 2:
        return None
    return status


def observation_previous_path(reference: object) -> Optional[str]:
    """The rename source on an eligible observation, or ``None``.

    Put through the same lexical gate as every other path, so a stored value
    that is not a plain project-relative name never reaches a comparison.
    """
    if not isinstance(reference, EvidenceReference):
        return None
    if not reference.previous_identifier:
        return None
    try:
        return normalize_claim_path(reference.previous_identifier)
    except Exception:
        return None


def is_git_head_observation(reference: object) -> bool:
    """Whether this is the commit/HEAD observation rather than a path change.

    Recognised explicitly so it can be excluded *by name* — and so a reader of
    this module can see that the exclusion was a decision rather than an
    oversight.
    """
    return (
        isinstance(reference, EvidenceReference)
        and reference.source == EVIDENCE_GIT_OBSERVED
        and reference.operation == "rev-parse HEAD"
    )


#: The emitter's coverage statements (M2K PR3). One of these accompanies every
#: Git observation, so "the set was truncated" is a recorded fact rather than
#: something a reader has to infer from a count that looks suspiciously round.
COVERAGE_COMPLETE = "observed all changes"
COVERAGE_PARTIAL = "observed some changes"


def is_coverage_observation(reference: object) -> bool:
    """Whether this is the emitter's observation-completeness statement."""
    return (
        isinstance(reference, EvidenceReference)
        and reference.source == EVIDENCE_GIT_OBSERVED
        and reference.evidence_type == EVIDENCE_ARTIFACT
        and reference.operation == "git status"
        and reference.result in (COVERAGE_COMPLETE, COVERAGE_PARTIAL)
    )


def is_clean_tree_observation(reference: object) -> bool:
    """Whether this is the "nothing changed" statement.

    Not a path observation, and equally not an *absence* of observation: it says
    Cofferdam looked and the tree was clean. Recorded as a bundle-level fact
    rather than matched against anything.
    """
    return (
        isinstance(reference, EvidenceReference)
        and reference.source == EVIDENCE_GIT_OBSERVED
        and reference.operation == "git status"
        and reference.result == "no files changed"
    )


# -- the published pieces -----------------------------------------------------


@dataclass(frozen=True)
class MachineObservation:
    """One eligible path-change observation, tied to the event that recorded it.

    ``event_sequence`` is the durable identity. There is no synthetic id: the
    event sequence and the position within that event's evidence list already
    name this observation uniquely and permanently, and minting a second handle
    would be a second thing that could disagree.
    """

    event_sequence: int
    evidence_index: int
    path: str
    source: str = EVIDENCE_GIT_OBSERVED
    evidence_type: str = EVIDENCE_FILE
    operation: Optional[str] = None
    result: Optional[str] = None
    #: What Git said happened here (M2K PR3), or ``None`` for an observation
    #: recorded before PR3. ``None`` means the operation was never established —
    #: it does not mean nothing happened, and the matrix treats it that way.
    change_kind: Optional[str] = None
    #: The source path of a rename. ``path`` is always the destination.
    previous_path: Optional[str] = None
    #: The exact machine status, carrying both facts a composite proves.
    change_status: Optional[str] = None

    @property
    def reference(self) -> str:
        """A stable, opaque handle a client can key on. Derived, not stored."""
        return "evt%d.%d" % (self.event_sequence, self.evidence_index)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reference": self.reference,
            "event_sequence": self.event_sequence,
            "evidence_index": self.evidence_index,
            "path": self.path,
            "source": self.source,
            "evidence_type": self.evidence_type,
            "operation": self.operation,
            "result": self.result,
            "change_kind": self.change_kind,
            "previous_path": self.previous_path,
            "change_status": self.change_status,
            # Said out loud rather than derived, the same way
            # `EvidenceReference.to_dict` says it. `git_observed` is a verified
            # source — Cofferdam ran Git — and that is a statement about *this
            # observation*, never about a claim it happens to match.
            "verified": True,
        }


@dataclass(frozen=True)
class PathRelationship:
    """What is known about one semantic path in one turn.

    **Grouped by path, not by pair.** Three claims and four observations naming
    ``src/foo.py`` produce one of these carrying three claim ids and four
    observation references — not twelve rows saying the same thing twelve times.
    Every source identity is preserved so a reader can get back to the durable
    record; only the *combinations* are not enumerated, because a combination is
    not a fact anybody recorded.
    """

    path: str
    relationship: str
    claim_ids: Tuple[str, ...] = ()
    claim_operations: Tuple[str, ...] = ()
    observation_refs: Tuple[str, ...] = ()
    path_agreement: bool = False
    operation_agreement: str = OPERATION_UNKNOWN
    #: The machine change kinds observed at this path (M2K PR3), deduplicated
    #: and sorted. Empty when every observation here predates PR3 — which is the
    #: visible reason `operation_agreement` is `unknown` for legacy evidence.
    observed_kinds: Tuple[str, ...] = ()
    claim_count: int = 0
    observation_count: int = 0
    sources_truncated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "relationship": self.relationship,
            "claim_ids": list(self.claim_ids),
            "claim_operations": list(self.claim_operations),
            "observation_refs": list(self.observation_refs),
            "path_agreement": self.path_agreement,
            # `true`, `false` or `unknown` since M2K PR3. Published as its own
            # field, separate from `path_agreement`, because the two questions
            # have different answers and always did — PR2 simply could not
            # answer the second one.
            "operation_agreement": self.operation_agreement,
            "observed_kinds": list(self.observed_kinds),
            "claim_count": self.claim_count,
            "observation_count": self.observation_count,
            "sources_truncated": self.sources_truncated,
        }


@dataclass(frozen=True)
class IngestionSummary:
    """How complete this turn's stored claim set is, aggregated deterministically.

    One turn can produce several ingestion rows — an adapter that reports twice
    within a turn writes two — so the counts are summed, ``truncated`` is true if
    any row was, and the reason counts are merged by integer addition. Row
    identities are kept in ``sequences`` so the fingerprint binds to *which* rows
    were aggregated rather than only to their totals: two different sets of rows
    that happen to sum alike are different inputs.
    """

    state: str
    submitted: int = 0
    accepted: int = 0
    rejected: int = 0
    truncated: bool = False
    reason_counts: Dict[str, int] = field(default_factory=dict)
    sequences: Tuple[int, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "submitted": self.submitted,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "truncated": self.truncated,
            "reason_counts": dict(sorted(self.reason_counts.items())),
            "sequences": list(self.sequences),
        }


@dataclass(frozen=True)
class EvidenceBundle:
    """One turn's evidence, derived. Immutable, bounded, and without a verdict.

    ``input_fingerprint`` identifies the inputs this was built from. Same stored
    rows plus same ``assembler_version`` gives the same value across a restart,
    across a process, and across a year. A change to any assembly-relevant
    immutable input changes it; a change to something outside the turn window, or
    to the wall clock, does not.

    There is deliberately **no ``built_at``**. A read-time timestamp would make
    every bundle differ from every other bundle, which would make the fingerprint
    the only thing that could tell two bundles apart and then invite somebody to
    put the timestamp into the fingerprint to "make it complete". Response
    metadata belongs on the HTTP envelope, labelled as presentation, and it is
    put there rather than here.
    """

    version: int
    assembler_version: int
    input_fingerprint: str
    task_id: str
    turn_number: int
    turn_attribution: str
    ingestion: IngestionSummary
    claims: Tuple[ChangeClaim, ...] = ()
    observations: Tuple[MachineObservation, ...] = ()
    relationships: Tuple[PathRelationship, ...] = ()
    limitations: Tuple[str, ...] = ()
    opened_after_event_sequence: Optional[int] = None
    closed_through_event_sequence: Optional[int] = None
    turn_open: bool = False
    repository_reported_clean: bool = False
    #: Whether the machine observation set for this turn is known to be whole
    #: (M2K PR3). False when Git reported more than was recorded.
    machine_observations_complete: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "assembler_version": self.assembler_version,
            "input_fingerprint": self.input_fingerprint,
            "task_id": self.task_id,
            "turn_number": self.turn_number,
            "turn_attribution": self.turn_attribution,
            "opened_after_event_sequence": self.opened_after_event_sequence,
            "closed_through_event_sequence": self.closed_through_event_sequence,
            "turn_open": self.turn_open,
            "ingestion": self.ingestion.to_dict(),
            # The claim's own `to_dict` already says `source: adapter_reported`
            # and `verified: false`, and this does not override either. A matched
            # claim is published byte-identically to an unmatched one — matching
            # produces a *relationship*, never a change to a claim.
            "claims": [claim.to_dict() for claim in self.claims],
            "observations": [item.to_dict() for item in self.observations],
            "relationships": [item.to_dict() for item in self.relationships],
            "limitations": list(self.limitations),
            "repository_reported_clean": self.repository_reported_clean,
            "machine_observations_complete": self.machine_observations_complete,
        }


# -- assembly -----------------------------------------------------------------


def _claim_paths(claim: ChangeClaim) -> Tuple[str, ...]:
    """The semantic targets one claim makes a statement about.

    A rename is **two** targets and both are claims: the source path the worker
    says stopped being there, and the destination path it says is there now.
    Represented as two entries rather than one string like ``"a -> b"``, because
    a matcher that had to parse that back apart would be a parser over
    adapter-influenced text, and because the two halves genuinely can be
    observed independently.

    Generic changed-path evidence cannot confirm a rename in either direction. If
    both paths appear in the changed set, both are ``path_agreed`` and the
    *operation* stays ``unknown`` — two paths changing is not proof they changed
    into each other. If only one appears, that one is ``path_agreed`` and the
    other is ``claim_only``, which is a gap and **not** a conflict: an untracked
    destination, an ignored path or a partially staged rename all produce it.
    """
    if claim.operation == CLAIM_RENAMED and claim.to_path:
        return (claim.path, claim.to_path)
    return (claim.path,)


def _eligible_observations(
    events: Sequence[TaskEvent],
) -> Tuple[Tuple[MachineObservation, ...], bool, bool, bool]:
    """Every eligible path observation in the window, in event order.

    Returns the observations, whether any unsupported ``git_observed`` shape was
    seen, whether a clean-tree statement was seen, and whether the emitter said
    its own observation set was **partial**. Ordering is by event
    sequence and then by position inside that event's evidence list — both
    durable, neither a timestamp.
    """
    observations: List[MachineObservation] = []
    unsupported = False
    clean = False
    partial = False
    for event in sorted(events, key=lambda item: item.sequence):
        for index, reference in enumerate(event.evidence or ()):
            path = observation_path(reference)
            if path is not None:
                observations.append(
                    MachineObservation(
                        event_sequence=int(event.sequence),
                        evidence_index=index,
                        path=path,
                        operation=reference.operation,
                        result=reference.result,
                        change_kind=observation_change_kind(reference),
                        previous_path=observation_previous_path(reference),
                        change_status=observation_status(reference),
                    )
                )
                continue
            if not isinstance(reference, EvidenceReference):  # pragma: no cover
                continue
            if reference.source != EVIDENCE_GIT_OBSERVED:
                # An `adapter_reported` reference is not an observation and its
                # absence from the observation list is not a limitation. It is
                # a claim, and claims come from the claim table.
                continue
            if is_clean_tree_observation(reference):
                clean = True
                continue
            if is_coverage_observation(reference):
                # The emitter's own statement about how complete this
                # observation was. Not a path, not a limitation in itself — the
                # caller reads it to decide whether the machine set is whole.
                if reference.result == COVERAGE_PARTIAL:
                    partial = True
                continue
            if is_git_head_observation(reference):
                # Known, understood, and deliberately not a path. Not a
                # limitation either — nothing was lost by not matching it.
                continue
            unsupported = True
    return tuple(observations), unsupported, clean, partial


def _aggregate_ingestion(
    rows: Sequence[ClaimIngestion], *, attribution: str, turn_number: int
) -> IngestionSummary:
    """One turn's claim-set completeness, from every ingestion row that names it."""
    if attribution == ATTRIBUTION_LEGACY_UNKNOWN:
        return IngestionSummary(state=COMPLETENESS_LEGACY_UNKNOWN)

    mine = [row for row in rows if row.turn_number == turn_number]
    if not mine:
        # Not `complete`. See `COMPLETENESS_INGESTION_MISSING` — the PR1 write
        # path produces this absence for three different reasons and none of
        # them establishes that the claim set is whole.
        return IngestionSummary(state=COMPLETENESS_INGESTION_MISSING)

    mine = mine[:MAX_BUNDLE_INGESTION_ROWS]
    submitted = accepted = rejected = 0
    truncated = False
    counts: Dict[str, int] = {}
    sequences: List[int] = []
    for index, row in enumerate(mine):
        submitted += int(row.submitted)
        accepted += int(row.accepted)
        rejected += int(row.rejected)
        truncated = truncated or bool(row.truncated)
        for code, amount in (row.reason_counts or {}).items():
            counts[str(code)] = counts.get(str(code), 0) + int(amount)
        # `ClaimIngestion` does not carry its own row sequence, so the position
        # within this task's ordered rows is the identity available. The store
        # returns them ordered by `sequence`, so it is stable.
        sequences.append(index)

    state = (
        COMPLETENESS_COMPLETE
        if rejected == 0 and not truncated
        else COMPLETENESS_INCOMPLETE
    )
    return IngestionSummary(
        state=state,
        submitted=submitted,
        accepted=accepted,
        rejected=rejected,
        truncated=truncated,
        reason_counts=counts,
        sequences=tuple(sequences),
    )


def _rename_agreement(
    claims: Sequence[ChangeClaim], observations: Sequence[MachineObservation], path: str
) -> Optional[str]:
    """Operation agreement for a rename, which is a fact about **two** paths.

    One table cell cannot answer this, because a rename claim of ``A -> B`` and a
    machine rename are only the same event if *both* sides match. So this is
    asked of the pair, at the destination path, where both records name the same
    file.

    * Both sides match — the machine renamed exactly what the claim said it
      renamed: ``true``.
    * The machine renamed this destination from a **different** source: ``false``.
      Two rename records that disagree about where the file came from cannot both
      describe one event.
    * The machine saw a rename but recorded no source (a pre-PR3 observation, or
      one whose source failed the path gate): ``unknown``. Half a rename proves
      nothing about the other half.

    Returns ``None`` when this pair is not a rename comparison at all, so the
    caller falls through to the ordinary table.
    """
    rename_claims = [c for c in claims if c.operation == CLAIM_RENAMED]
    rename_observations = [o for o in observations if o.change_kind == CHANGE_RENAMED]
    if not rename_claims or not rename_observations:
        return None
    # Only the destination carries the pairing; a rename's source appears as its
    # own group and is never where agreement is decided.
    if not any(c.to_path == path for c in rename_claims):
        return None

    claimed_sources = {c.path for c in rename_claims if c.to_path == path}
    observed_sources = {o.previous_path for o in rename_observations if o.path == path}
    if not observed_sources or None in observed_sources:
        return OPERATION_UNKNOWN
    if claimed_sources & observed_sources:
        return OPERATION_AGREED
    return OPERATION_DIFFERS


def _group_agreement(
    claims: Sequence[ChangeClaim], observations: Sequence[MachineObservation], path: str
) -> str:
    """The operation agreement for one path group.

    Resolved across every claim and every observation at this path rather than
    pairwise, because a path group can legitimately hold several of each. The
    resolution is deliberately **conservative**: one pair that agrees does not
    overrule one that contradicts, and anything unestablished leaves the group
    unestablished.

    Order of precedence:

    1. If any pair is explicitly incompatible, the group is ``false``. A
       contradiction is the fact a reader most needs, and burying it under a
       simultaneous agreement would hide it.
    2. Otherwise, if every pair that could be judged agreed, the group is
       ``true``.
    3. Otherwise ``unknown``.
    """
    rename = _rename_agreement(claims, observations, path)
    if rename is not None:
        return rename

    verdicts = [
        operation_agreement(
            claim.operation, observation.change_kind, status=observation.change_status
        )
        for claim in claims
        for observation in observations
        # A rename claim's *source* path is compared to nothing here: the claim
        # says the file left, and a machine record at that path is its own fact.
        if claim.operation != CLAIM_RENAMED
    ]
    if not verdicts:
        return OPERATION_UNKNOWN
    if OPERATION_DIFFERS in verdicts:
        return OPERATION_DIFFERS
    if all(verdict == OPERATION_AGREED for verdict in verdicts):
        return OPERATION_AGREED
    return OPERATION_UNKNOWN


def _relationships(
    claims: Sequence[ChangeClaim], observations: Sequence[MachineObservation]
) -> Tuple[Tuple[PathRelationship, ...], bool, bool]:
    """Path-grouped relationships, deterministically ordered.

    Ordered by path, then by relationship word. Both are content, so the order
    is a property of the inputs rather than of the iteration that produced them
    — which is what makes a repeated read byte-identical.
    """
    by_path: Dict[str, Dict[str, List[Any]]] = {}

    def _slot(path: str) -> Dict[str, List[Any]]:
        return by_path.setdefault(
            path,
            {"claims": [], "operations": [], "observations": [], "claim_rows": [], "obs_rows": []},
        )

    for claim in claims:
        for path in _claim_paths(claim):
            slot = _slot(path)
            slot["claims"].append(claim.claim_id)
            slot["operations"].append(claim.operation)
            slot["claim_rows"].append(claim)
    for observation in observations:
        slot = _slot(observation.path)
        slot["observations"].append(observation.reference)
        slot["obs_rows"].append(observation)

    paths_truncated = len(by_path) > MAX_BUNDLE_PATHS
    sources_truncated_anywhere = False
    built: List[PathRelationship] = []
    for path in sorted(by_path)[:MAX_BUNDLE_PATHS]:
        slot = by_path[path]
        claim_ids = slot["claims"]
        operations = slot["operations"]
        refs = slot["observations"]

        agreement = (
            _group_agreement(slot["claim_rows"], slot["obs_rows"], path)
            if claim_ids and refs
            else OPERATION_UNKNOWN
        )

        if claim_ids and refs:
            # The path is agreed either way — both records name this file. What
            # the operation evidence says decides whether the relationship is a
            # plain agreement or a recorded disagreement.
            relationship = (
                RELATIONSHIP_CLAIM_CONFLICT
                if agreement == OPERATION_DIFFERS
                else RELATIONSHIP_PATH_AGREED
            )
        elif claim_ids:
            relationship = RELATIONSHIP_CLAIM_ONLY
        else:
            relationship = RELATIONSHIP_OBSERVED_ONLY

        truncated = len(claim_ids) > MAX_GROUP_SOURCES or len(refs) > MAX_GROUP_SOURCES
        sources_truncated_anywhere = sources_truncated_anywhere or truncated
        built.append(
            PathRelationship(
                path=path,
                relationship=relationship,
                claim_ids=tuple(claim_ids[:MAX_GROUP_SOURCES]),
                # Deduplicated and sorted: which operations were claimed for
                # this path is the fact, and how many times each was repeated is
                # already in `claim_count`.
                claim_operations=tuple(sorted(set(operations))),
                observation_refs=tuple(refs[:MAX_GROUP_SOURCES]),
                # Both `path_agreed` and `claim_conflict` mean the two records
                # name the same file, so path agreement is true for both. The
                # difference between them is entirely about the operation.
                path_agreement=relationship
                in (RELATIONSHIP_PATH_AGREED, RELATIONSHIP_CLAIM_CONFLICT),
                operation_agreement=agreement,
                # Deduplicated and sorted, like the claim operations.
                # Every fact the machine proved at this path, not just the
                # primary labels — a composite `RM` proves a modification too,
                # and a reader deciding whether a claim was corroborated needs
                # to see it.
                observed_kinds=tuple(
                    sorted(
                        {
                            fact
                            for o in slot["obs_rows"]
                            for fact in _facts_for(o.change_kind, o.change_status)
                        }
                    )
                ),
                claim_count=len(claim_ids),
                observation_count=len(refs),
                sources_truncated=truncated,
            )
        )
    built.sort(key=lambda item: (item.path, item.relationship))
    return tuple(built), paths_truncated, sources_truncated_anywhere


def input_fingerprint(
    *,
    task_id: str,
    turn_number: int,
    attribution: str,
    bound: Optional[TurnBound],
    claims: Sequence[ChangeClaim],
    observations: Sequence[MachineObservation],
    ingestion: IngestionSummary,
    machine_complete: bool = True,
) -> str:
    """A stable hash of exactly the immutable inputs assembly used.

    What is in it, and why each thing is
    ------------------------------------

    **Turn provenance.** The task, the turn, and the exact sequence range —
    with an explicit ``OPEN`` marker where there is no upper bound and an
    explicit ``LEGACY_UNKNOWN`` marker where there is no bound at all, so those
    two cannot hash like a closed range or like each other.

    **Claims**, including the **project-relative path**. Paths are semantic
    identifiers and changing one changes what was claimed: ``src/foo.py``
    becoming ``src/bar.py`` is a different statement about different work, and a
    fingerprint that ignored it would call two different claim sets identical.
    The operation, the rename destination and the record's own reason code are
    in for the same reason. The artifact's **preview content is not** — the
    bundle does not publish it, so it is not an input to what the bundle says.

    **Ingestion**, by aggregated counts *and* by the row identities that were
    aggregated, so two different row sets that happen to sum alike are still
    different inputs.

    **Eligible evidence**, by durable event sequence and evidence index plus the
    matched path and the fields eligibility turned on. An event outside the turn
    window never reaches this function, so appending one cannot change the value.

    What is deliberately **not** in it
    ----------------------------------

    * **Absolute host paths.** No project root, no ``/home/...``, no deployment
      slot path, no Global Mind path, no filesystem authority of any kind. None
      of them is an input to assembly, and hashing one would make a fingerprint
      that changed when a deployment moved and would leak the host's layout into
      a value that gets stored and compared.
    * **Provider or session identifiers.** Not published, not assembly-relevant,
      and Task Core keeps its own turn identity precisely so it does not have to
      depend on a provider's.
    * **Read time.** A clock reading is not an input. Including one would make
      every read produce a different value and the fingerprint would identify
      nothing.
    * **Live Git state**, because assembly never runs Git. The stored
      observation is the input; the repository as it stands now is not.
    * **Artifact preview bodies**, per above.
    """
    digest = _Fingerprint()
    digest.fields(
        [
            "cofferdam.evidence.bundle",
            BUNDLE_VERSION,
            ASSEMBLER_VERSION,
            task_id,
            turn_number,
            attribution,
        ]
    )

    if bound is None:
        digest.field(MARKER_LEGACY_UNKNOWN)
    else:
        digest.field(bound.opened_after_event_sequence)
        digest.field(
            MARKER_OPEN
            if bound.closed_through_event_sequence is None
            else bound.closed_through_event_sequence
        )

    digest.field("claims")
    digest.field(len(claims))
    for claim in claims:
        digest.fields(
            [
                claim.claim_id,
                claim.turn_number,
                claim.operation,
                claim.path,
                claim.to_path,
                claim.reason,
                claim.adapter_label,
                # Constant by construction, hashed anyway so that a future build
                # which somehow produced a differently-sourced claim could not
                # fingerprint it as though it were adapter-reported.
                claim.source,
            ]
        )

    digest.field("ingestion")
    digest.fields(
        [
            ingestion.state,
            ingestion.submitted,
            ingestion.accepted,
            ingestion.rejected,
            ingestion.truncated,
        ]
    )
    digest.field(len(ingestion.sequences))
    for sequence in ingestion.sequences:
        digest.field(sequence)
    for code in sorted(ingestion.reason_counts):
        digest.field(code)
        digest.field(int(ingestion.reason_counts[code]))

    digest.field("observations")
    digest.field(len(observations))
    for observation in observations:
        digest.fields(
            [
                observation.event_sequence,
                observation.evidence_index,
                observation.source,
                observation.evidence_type,
                observation.path,
                observation.operation,
                observation.result,
                # M2K PR3. The machine semantics are assembly-relevant immutable
                # inputs: the same path observed as `deleted` rather than
                # `modified` is a different fact and must not fingerprint alike.
                # `previous_path` is a project-relative semantic path, so it
                # belongs here for the reason the claim's path does.
                observation.change_kind,
                observation.previous_path,
                # The exact status is an assembly-relevant immutable input: it is
                # what agreement is decided from for a composite state, so `RM`
                # and a bare `renamed` must not fingerprint alike.
                observation.change_status,
            ]
        )
    # Whether the machine set was whole is an input to what the bundle says
    # about absence, so it binds too.
    digest.field("machine_complete")
    digest.field(bool(machine_complete))
    return digest.hexdigest()


def assemble(
    *,
    task_id: str,
    turn: TaskTurn,
    bound: Optional[TurnBound],
    events: Sequence[TaskEvent],
    claims: Sequence[ChangeClaim],
    ingestion_rows: Sequence[ClaimIngestion],
) -> EvidenceBundle:
    """Build one turn's bundle from stored facts. **Pure.**

    No filesystem, no subprocess, no socket, no database write, no clock. Every
    input arrives as an argument, which is what makes that checkable by reading
    the signature rather than by trusting the body — and what the purity tests
    assert against the module's imports.

    ``events`` must already be restricted to the turn's window; the caller is
    the store, which expresses the range in SQL. Passing a wider list would not
    silently widen the bundle — the fingerprint would move and the tests would
    say so — but it would be the caller's bug, not this function's.
    """
    limitations: List[str] = []
    legacy = bound is None
    attribution = ATTRIBUTION_LEGACY_UNKNOWN if legacy else ATTRIBUTION_EXACT

    turn_claims = tuple(
        claim for claim in claims if claim.turn_number == turn.turn_number
    )
    claims_truncated = len(turn_claims) > MAX_BUNDLE_CLAIMS
    turn_claims = turn_claims[:MAX_BUNDLE_CLAIMS]

    if legacy:
        # No range was recorded, so **no event is attributable to this turn**.
        # Not the events whose timestamps fall between the turn's, not the ones
        # whose type suggests they belong, and not the task's events at large.
        # A legacy turn shown task-wide observations would be a turn-scoped
        # claim built out of task-scoped evidence, which is the specific
        # falsehood this milestone exists to prevent.
        observations: Tuple[MachineObservation, ...] = ()
        unsupported = False
        clean = False
        partial = False
        limitations.append(LIMIT_LEGACY_TURN)
    else:
        window = list(events)[:MAX_BUNDLE_EVENTS]
        if len(events) > MAX_BUNDLE_EVENTS:
            limitations.append(LIMIT_EVENTS_TRUNCATED)
        observations, unsupported, clean, partial = _eligible_observations(window)
        if len(observations) > MAX_BUNDLE_OBSERVATIONS:
            limitations.append(LIMIT_OBSERVATIONS_TRUNCATED)
            observations = observations[:MAX_BUNDLE_OBSERVATIONS]

    ingestion = _aggregate_ingestion(
        ingestion_rows, attribution=attribution, turn_number=turn.turn_number
    )
    if ingestion.state == COMPLETENESS_INGESTION_MISSING:
        limitations.append(LIMIT_INGESTION_MISSING)
    elif ingestion.state == COMPLETENESS_INCOMPLETE:
        limitations.append(LIMIT_CLAIM_SET_INCOMPLETE)

    if claims_truncated:
        limitations.append(LIMIT_CLAIMS_TRUNCATED)
    if unsupported:
        limitations.append(LIMIT_UNSUPPORTED_OBSERVATION)
    if partial:
        # Git reported more than Cofferdam recorded. Carried so that an
        # `observed_only` absence is read as "possibly not looked at" rather
        # than "looked at and not there" — the distinction PR2 made for claims
        # and PR3 owes to observations.
        limitations.append(LIMIT_OBSERVATIONS_INCOMPLETE)

    relationships, paths_truncated, sources_truncated = _relationships(
        turn_claims, observations
    )
    if paths_truncated:
        limitations.append(LIMIT_PATHS_TRUNCATED)
    if sources_truncated:
        limitations.append(LIMIT_SOURCES_TRUNCATED)

    fingerprint = input_fingerprint(
        task_id=task_id,
        turn_number=turn.turn_number,
        attribution=attribution,
        bound=bound,
        claims=turn_claims,
        observations=observations,
        ingestion=ingestion,
        machine_complete=not partial,
    )

    return EvidenceBundle(
        version=BUNDLE_VERSION,
        assembler_version=ASSEMBLER_VERSION,
        input_fingerprint=fingerprint,
        task_id=task_id,
        turn_number=turn.turn_number,
        turn_attribution=attribution,
        ingestion=ingestion,
        claims=turn_claims,
        observations=observations,
        relationships=relationships,
        # Sorted and deduplicated: a limitation is a fact about the bundle, and
        # the order it was noticed in is not part of that fact.
        limitations=tuple(sorted(set(limitations))),
        opened_after_event_sequence=(
            None if bound is None else bound.opened_after_event_sequence
        ),
        closed_through_event_sequence=(
            None if bound is None else bound.closed_through_event_sequence
        ),
        turn_open=bool(bound is not None and bound.open),
        repository_reported_clean=clean,
        machine_observations_complete=not partial,
    )


__all__ = [
    "ASSEMBLER_VERSION",
    "ATTRIBUTION_EXACT",
    "ATTRIBUTION_LEGACY_UNKNOWN",
    "BUNDLE_VERSION",
    "COMPLETENESS_COMPLETE",
    "COMPLETENESS_INCOMPLETE",
    "COMPLETENESS_INGESTION_MISSING",
    "COMPLETENESS_LEGACY_UNKNOWN",
    "COMPLETENESS_STATES",
    "EvidenceBundle",
    "FINGERPRINT_CHARS",
    "IngestionSummary",
    "LIMITATIONS",
    "LIMIT_CLAIMS_TRUNCATED",
    "LIMIT_CLAIM_SET_INCOMPLETE",
    "LIMIT_EVENTS_TRUNCATED",
    "LIMIT_INGESTION_MISSING",
    "LIMIT_LEGACY_TURN",
    "LIMIT_OBSERVATIONS_INCOMPLETE",
    "LIMIT_OBSERVATIONS_TRUNCATED",
    "LIMIT_PATHS_TRUNCATED",
    "LIMIT_SOURCES_TRUNCATED",
    "LIMIT_UNSUPPORTED_OBSERVATION",
    "MARKER_LEGACY_UNKNOWN",
    "MARKER_OPEN",
    "MAX_BUNDLE_CLAIMS",
    "MAX_BUNDLE_EVENTS",
    "MAX_BUNDLE_INGESTION_ROWS",
    "MAX_BUNDLE_OBSERVATIONS",
    "MAX_BUNDLE_PATHS",
    "MAX_GROUP_SOURCES",
    "MachineObservation",
    "OPERATION_AGREED",
    "OPERATION_AGREEMENTS",
    "OPERATION_DIFFERS",
    "OPERATION_UNKNOWN",
    "RELATIONSHIP_CLAIM_CONFLICT",
    "COVERAGE_COMPLETE",
    "COVERAGE_PARTIAL",
    "is_coverage_observation",
    "observation_change_kind",
    "observation_status",
    "status_facts",
    "observation_previous_path",
    "operation_agreement",
    "PathRelationship",
    "RELATIONSHIPS",
    "RELATIONSHIP_CLAIM_ONLY",
    "RELATIONSHIP_OBSERVED_ONLY",
    "RELATIONSHIP_PATH_AGREED",
    "TAG_FINGERPRINT",
    "TURN_ATTRIBUTIONS",
    "assemble",
    "input_fingerprint",
    "is_clean_tree_observation",
    "is_git_head_observation",
    "observation_path",
]
