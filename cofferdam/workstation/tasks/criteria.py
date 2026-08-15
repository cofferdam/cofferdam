"""What a turn was supposed to achieve, frozen before the worker was told to start.

Why this module exists
----------------------

M2K PR1 through PR5 built the *evidence* half of this milestone. Cofferdam can
now answer machine questions about a turn — did this path change, was it created
or deleted, was A renamed to B, was that change committed inside this turn's own
revision range, does the worker's claim agree with what the host observed.

What the database has never held is anything to evaluate that evidence
**against**. There is no acceptance criterion type, no criterion set, no
criterion identity and no per-turn criteria authority, so "did the work meet what
was asked" has no durable question to be an answer to.

This module is that question, and only that question. **Nothing here evaluates
anything.** There is no result record, no met/not_met, no pass, no verdict, no
confidence and no risk in this build — those are a later PR's problem, and they
are a separate problem for the same reason PR4's boundary was separate from PR5's
range: criteria that are wrong, late, adapter-influenced or silently absent would
make every verdict derived from them wrong in a way that looks authoritative.

The invariant the whole design exists to hold
---------------------------------------------

    A future evaluation must refer to the exact criteria snapshot that was
    already in force **before worker dispatch began**.

A worker evaluated against criteria that changed after it started has been
judged against a moving target, and no amount of care in the evaluator can
repair that afterwards. So criteria are a **pre-work durable fact**,
conceptually parallel to PR4's Git baseline and frozen by the same event: once
``dispatch_started`` is durable for a reserved turn, that turn's criteria
snapshot is immutable. See :mod:`.gitbaseline` for the argument about why that
particular event, and why an ``AdapterRefusal`` does not re-open it.

Where criteria may come from, and where they may never
------------------------------------------------------

Criteria may originate from a trusted caller's intent, or one day from a
host-owned planner. They may **never** originate from:

* an :class:`~.adapters.protocol.AdapterOutcome` — there is no field for them
  there and this build adds none;
* a worker's final prose — nothing in this package parses a result into
  criteria, and no model runs anywhere near this module;
* a provider session, a Git observation, or machine evidence itself.

Evidence and requirements have to come from different places or the exercise is
circular: a worker that both did the work and set the bar has not been checked
against anything.

What a criterion may say, in version 1
--------------------------------------

Two kinds, and the vocabulary is closed:

``evidence``
    A structured predicate over a project-relative path that the *stored*
    EvidenceBundle could decide deterministically — ``path_changed``,
    ``path_operation`` with a closed operation, or ``rename``. Three predicates,
    each naming a path, and no expression language of any kind.

``manual``
    A bounded human-readable description of something today's deterministic
    evidence cannot decide. ``manual`` means **undecidable by machine**; it does
    not mean failed and it does not mean passed, and a future evaluator is
    required to return it as unverified.

What is deliberately absent
---------------------------

**No command criteria.** There is no shell string, no argv, no script, no test
command, no executable path, and no generic ``command`` criterion carrying
arbitrary text. That is not an oversight to be filled in later by adding a
column: a criterion that carries a command is dormant execution authority
sitting in the database waiting for a runner to be written, and I-16 (no
user-controlled argv for a spawned process) is a frozen Trust Core invariant.
When a check runner exists it will run **host-owned named checks with literal
argv**, and a future criterion kind may name a bounded ``check_id`` from that
host-owned table.
Naming a check the host defines is a different thing from carrying a command, and
this build invents neither.

**No arbitrary expression strings.** ``"foo.py changed AND tests passed"`` is not
a criterion, it is a small programming language, and storing one would mean the
evaluator has to become an interpreter. Structured fields are the authority.

**No negative or set criteria.** "Nothing changed outside the allowed set S" is a
question current evidence can *sometimes* answer, and the "sometimes" is the
problem: it needs a bounded structured path set — a third relational layer — and
a completeness semantics stronger than anything PR2's ``machine_complete`` or
PR4's ``status_coverage`` establish today. Deferred deliberately. A small correct
v1 beats a generic criteria DSL.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .claims import (
    MAX_CLAIM_PATH_CHARS,
    ClaimPathInvalid,
    is_denied_path,
    normalize_claim_path,
)
from .identity import _ALPHABET, _encode

# -- identifiers --------------------------------------------------------------
#
# Same construction as `task_id`, `claim_id` and `artifact_id`, for the same
# three reasons: unpredictable, not derived from anything, sortable.
#
# **Server-minted, always.** A caller supplies criterion *content*; it does not
# get to name the handle Cofferdam files that content under. A caller-chosen id
# would be a way to address another task's snapshot, and an id derived from the
# criterion's own fields would make two identical criteria on two different turns
# share an identity — which is wrong, since a snapshot row is about one turn at
# one moment.
#
# The two prefixes are deliberately not one character apart. `acs_`/`acr_` are
# hard to confuse in a log line; `crs_`/`crt_` would not have been.

SNAPSHOT_ID_PREFIX = "acs_"
CRITERION_ID_PREFIX = "acr_"

_TIME_BITS = 48
_RANDOM_BITS = 80
_ID_BODY_CHARS = (_TIME_BITS + _RANDOM_BITS) // 5 + 1  # 26

SNAPSHOT_ID_CHARS = len(SNAPSHOT_ID_PREFIX) + _ID_BODY_CHARS
CRITERION_ID_CHARS = len(CRITERION_ID_PREFIX) + _ID_BODY_CHARS

_ALPHABET_SET = frozenset(_ALPHABET)


def _new_id(prefix: str, now_ms: Optional[int] = None) -> str:
    stamp = int(time.time() * 1000) if now_ms is None else int(now_ms)
    stamp &= (1 << _TIME_BITS) - 1
    randomness = secrets.randbits(_RANDOM_BITS)
    return prefix + _encode((stamp << _RANDOM_BITS) | randomness, _ID_BODY_CHARS)


def new_snapshot_id(now_ms: Optional[int] = None) -> str:
    """A fresh criteria snapshot id. Minted by the store, never supplied."""
    return _new_id(SNAPSHOT_ID_PREFIX, now_ms)


def new_criterion_id(now_ms: Optional[int] = None) -> str:
    """A fresh criterion id. Minted by the store, never supplied."""
    return _new_id(CRITERION_ID_PREFIX, now_ms)


def _valid_id(value: object, prefix: str, length: int) -> bool:
    if not isinstance(value, str) or len(value) != length:
        return False
    if not value.startswith(prefix):
        return False
    return all(character in _ALPHABET_SET for character in value[len(prefix) :])


def valid_snapshot_id(value: object) -> bool:
    return _valid_id(value, SNAPSHOT_ID_PREFIX, SNAPSHOT_ID_CHARS)


def valid_criterion_id(value: object) -> bool:
    return _valid_id(value, CRITERION_ID_PREFIX, CRITERION_ID_CHARS)


# -- the criteria state vocabulary --------------------------------------------
#
# Three values, and the distinction between the last two is the reason this
# vocabulary exists rather than a nullable column.

#: Cofferdam holds an immutable criteria snapshot for this turn, with at least
#: one criterion in it.
CRITERIA_PRESENT = "present"

#: Cofferdam durably recorded, **before dispatch**, that no criteria were
#: supplied for this turn. This is a fact somebody wrote down, not a gap.
#:
#: It is emphatically **not** "an empty criterion set that therefore succeeds".
#: A future evaluator handed ``not_provided`` has nothing to evaluate and must
#: say so; it must not conclude that every requirement was met because there
#: were none.
CRITERIA_NOT_PROVIDED = "not_provided"

#: There is **no snapshot row at all** for this turn, which on this host means
#: the turn predates schema v7. Never stored — this is the *read* answer for a
#: missing row, and the schema's CHECK forbids writing it.
#:
#: The difference from :data:`CRITERIA_NOT_PROVIDED` is the whole point of the
#: three-valued vocabulary: one says "Cofferdam knew there were no structured
#: criteria", the other says "this turn predates criteria persistence and nobody
#: knows". Collapsing them would let a historical turn be read as deliberately
#: unconstrained.
CRITERIA_LEGACY_UNKNOWN = "legacy_unknown"

#: What may be written. ``legacy_unknown`` is absent on purpose.
STORED_CRITERIA_STATES: Tuple[str, ...] = (CRITERIA_PRESENT, CRITERIA_NOT_PROVIDED)

#: What may be read.
CRITERIA_STATES: Tuple[str, ...] = STORED_CRITERIA_STATES + (CRITERIA_LEGACY_UNKNOWN,)


# -- the criterion kind vocabulary --------------------------------------------

#: A structured predicate the stored EvidenceBundle can decide deterministically.
KIND_EVIDENCE = "evidence"

#: Something deterministic evidence cannot decide, described for a person.
#:
#: A future evaluator returns this as *unverified*. It is not a failure and it is
#: not a pass, and the description is not parsed to turn it into either.
KIND_MANUAL = "manual"

CRITERION_KINDS: Tuple[str, ...] = (KIND_EVIDENCE, KIND_MANUAL)


# -- the evidence predicate vocabulary ----------------------------------------
#
# Closed, and small on purpose. Every predicate here is one the *already stored*
# claim, artifact, worktree-observation and committed-range rows can decide in
# the next PR without any new capture, any new subprocess, or any new column.
# A predicate that needed evidence this build does not collect would be a
# promise the foundation cannot keep.

#: The path changed somehow, by any operation. The weakest and most robust of
#: the three: it survives an operation disagreement between the claim and the
#: machine observation, which is a case PR2's relationships already surface.
PREDICATE_PATH_CHANGED = "path_changed"

#: The path changed by a **specific** operation, named in ``operation``.
PREDICATE_PATH_OPERATION = "path_operation"

#: ``path`` was renamed to ``to_path``. Distinct from a delete plus a create,
#: which is what a rename looks like to anything that does not track it, and
#: which PR3 deliberately keeps apart.
PREDICATE_RENAME = "rename"

EVIDENCE_PREDICATES: Tuple[str, ...] = (
    PREDICATE_PATH_CHANGED,
    PREDICATE_PATH_OPERATION,
    PREDICATE_RENAME,
)

#: The operations ``path_operation`` may name. The same three words the claim
#: vocabulary uses for non-rename changes, and identical spelling is the point:
#: a criterion saying ``modified`` and a claim saying ``modified`` must be the
#: same fact, not two words an evaluator has to reconcile.
OPERATION_CREATED = "created"
OPERATION_MODIFIED = "modified"
OPERATION_DELETED = "deleted"

CRITERION_OPERATIONS: Tuple[str, ...] = (
    OPERATION_CREATED,
    OPERATION_MODIFIED,
    OPERATION_DELETED,
)

#: ``renamed`` is **not** here, and its absence is deliberate rather than an
#: omission. A rename is a two-path fact and ``path_operation`` carries one path;
#: expressing it there would produce a criterion that names a source and silently
#: says nothing about the destination. :data:`PREDICATE_RENAME` is how a rename is
#: asked for.
EXCLUDED_OPERATIONS: Tuple[str, ...] = ("renamed",)


# -- bounds -------------------------------------------------------------------
#
# Code-owned, and refusal-shaped rather than truncation-shaped. This is the one
# place in M2K where silent truncation would be **wrong** in a way it is not
# wrong elsewhere: an observation that was bounded can be represented honestly as
# `incomplete`, because a partial view of what happened is still a true partial
# view. A partial view of what was *required* is not — thirty-two criteria stored
# out of forty submitted would read afterwards as the complete set of things the
# work had to do, and eight requirements would have disappeared without anybody
# being told. So over-bound submissions are refused, before dispatch, and no
# snapshot is written at all.

#: How many criteria one turn may carry.
MAX_CRITERIA_PER_TURN = 32

#: How long a criterion's human-readable description may be.
MAX_CRITERION_DESCRIPTION_CHARS = 500

#: Inherited from the claim path doctrine rather than restated, so a path that is
#: acceptable as a criterion is exactly a path that is acceptable as a claim.
MAX_CRITERION_PATH_CHARS = MAX_CLAIM_PATH_CHARS


# -- refusal reasons ----------------------------------------------------------
#
# Closed codes, and a refusal **never echoes the offending value**. The rule
# `ClaimPathInvalid` already follows: a message that repeats a rejected path is a
# way to describe the host's filesystem one attempt at a time, and a message that
# repeats a rejected description hands back whatever text was submitted.

REASON_SUBMISSION_MALFORMED = "criteria_submission_malformed"
REASON_LIMIT_EXCEEDED = "criteria_limit_exceeded"
REASON_CRITERION_MALFORMED = "criterion_malformed"
REASON_UNKNOWN_FIELD = "criterion_unknown_field"
REASON_SERVER_OWNED_FIELD = "criterion_server_owned_field"
REASON_KIND_INVALID = "criterion_kind_invalid"
REASON_PREDICATE_INVALID = "criterion_predicate_invalid"
REASON_PREDICATE_UNEXPECTED = "criterion_predicate_unexpected"
REASON_OPERATION_INVALID = "criterion_operation_invalid"
REASON_OPERATION_UNEXPECTED = "criterion_operation_unexpected"
REASON_PATH_INVALID = "criterion_path_invalid"
REASON_PATH_ESCAPE = "criterion_path_escape"
REASON_PATH_DENIED_SENSITIVE = "criterion_path_denied_sensitive"
REASON_PATH_REQUIRED = "criterion_path_required"
REASON_PATH_UNEXPECTED = "criterion_path_unexpected"
REASON_DESTINATION_REQUIRED = "criterion_destination_required"
REASON_DESTINATION_UNEXPECTED = "criterion_destination_unexpected"
REASON_DESTINATION_IDENTICAL = "criterion_destination_identical"
REASON_DESCRIPTION_REQUIRED = "criterion_description_required"
REASON_DESCRIPTION_INVALID = "criterion_description_invalid"
REASON_DESCRIPTION_TOO_LONG = "criterion_description_too_long"
REASON_DUPLICATE = "criterion_duplicate"
REASON_COMMAND_NOT_SUPPORTED = "criterion_command_not_supported"

#: Field names that would mean somebody is trying to store execution authority.
#: Refused by name with their own reason code rather than falling through to
#: "unknown field", so the refusal says what actually happened.
COMMAND_FIELD_NAMES: Tuple[str, ...] = (
    "command",
    "argv",
    "script",
    "shell",
    "cmd",
    "executable",
    "test_command",
    "run",
    "check_id",
)

#: The only keys a submitted criterion may carry. ``criterion_id`` is not among
#: them and never will be: storage identity is the server's.
ALLOWED_CRITERION_FIELDS: Tuple[str, ...] = (
    "kind",
    "predicate",
    "path",
    "to_path",
    "operation",
    "description",
)

#: Keys a caller might reasonably *think* it can supply, refused with a reason
#: that says why rather than as an unknown field.
SERVER_OWNED_FIELDS: Tuple[str, ...] = (
    "criterion_id",
    "snapshot_id",
    "criteria_snapshot_id",
    "fingerprint",
    "criteria_fingerprint",
    "ordinal",
    "task_id",
    "turn_number",
)


class CriteriaSubmissionInvalid(Exception):
    """A submitted criteria set that will not be stored, with a closed reason.

    Carries the reason code and **never the offending value**, for the reason
    :class:`~.claims.ClaimPathInvalid` gives. The service turns this into a
    :class:`~.errors.CriteriaInvalid` so a route or an internal caller sees an
    ordinary Task Core refusal.
    """

    __slots__ = ("reason", "ordinal")

    def __init__(self, reason: str, ordinal: Optional[int] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        #: Which criterion in the submission, 1-based, or ``None`` for a refusal
        #: about the set as a whole. A position is not content.
        self.ordinal = ordinal


# -- the criterion --------------------------------------------------------------


@dataclass(frozen=True)
class AcceptanceCriterion:
    """One thing the work was required to do.

    ``ordinal`` is the criterion's position in the snapshot, 1-based, and it is
    **stored**. Ordering is explicit rather than left to SQLite's rowid, because
    rowid order is an implementation detail that a VACUUM is entitled to change
    and a fingerprint must not depend on.

    The order is the caller's own, preserved rather than sorted. Sorting would be
    the same class of mistake as normalizing ``a/../b`` into ``b`` in
    :func:`~.claims.normalize_claim_path`: it would store a list nobody
    submitted. A consequence worth stating plainly is that reordering the same
    criteria produces a **different** snapshot and a different fingerprint,
    because ``ordinal`` is one of the stored facts the fingerprint covers.

    ``criterion_id`` is ``None`` on a validated draft and a real id once stored.
    It is minted by the store; there is no path by which a caller sets it.
    """

    ordinal: int
    kind: str
    predicate: Optional[str] = None
    path: Optional[str] = None
    to_path: Optional[str] = None
    operation: Optional[str] = None
    description: Optional[str] = None
    criterion_id: Optional[str] = None

    @property
    def evidence_evaluable(self) -> bool:
        """Whether a deterministic evaluator is expected to decide this at all.

        ``False`` for ``manual``, which a future evaluator returns as
        *unverified* — never as not_met. An evidence limitation is a statement
        about Cofferdam's reach, not about the worker's work.
        """
        return self.kind == KIND_EVIDENCE

    def with_id(self, criterion_id: str) -> "AcceptanceCriterion":
        return AcceptanceCriterion(
            ordinal=self.ordinal,
            kind=self.kind,
            predicate=self.predicate,
            path=self.path,
            to_path=self.to_path,
            operation=self.operation,
            description=self.description,
            criterion_id=criterion_id,
        )


@dataclass(frozen=True)
class CriteriaSnapshot:
    """The criteria in force for one reserved turn, as stored — or their absence.

    One shape for all three states, so a reader never has to branch on ``None``
    before it can ask what the state was. A ``legacy_unknown`` snapshot carries
    no identity, no fingerprint and no criteria, because none were ever recorded.

    Deliberately **not** published anywhere. There is no ``to_dict`` and no route
    that serialises this in PR6: the foundation is internal, and a serializer
    written before anything needs one is how an internal shape becomes a
    contract by accident.
    """

    task_id: str
    turn_number: int
    state: str
    snapshot_id: Optional[str] = None
    fingerprint: Optional[str] = None
    criterion_count: int = 0
    dispatch_state: Optional[str] = None
    recorded_at: Optional[str] = None
    criteria: Tuple[AcceptanceCriterion, ...] = ()

    @property
    def recorded(self) -> bool:
        """Whether Cofferdam wrote anything about criteria for this turn.

        ``False`` only for :data:`CRITERIA_LEGACY_UNKNOWN`. Both ``present`` and
        ``not_provided`` are recorded facts.
        """
        return self.state in STORED_CRITERIA_STATES


# -- validation ---------------------------------------------------------------


def _text(value: object, reason: str, ordinal: int) -> str:
    if not isinstance(value, str):
        raise CriteriaSubmissionInvalid(reason, ordinal)
    return value


def _criterion_path(value: object, ordinal: int, *, reason_missing: str) -> str:
    """A criterion path, held to exactly the claim path doctrine.

    Reusing :func:`~.claims.normalize_claim_path` rather than restating it is the
    point: "a path Cofferdam will talk about" must mean one thing across claims,
    artifacts, observations and criteria, or the safety argument is made three
    times and holds in two places.

    The sensitive-name deny list applies here too. A criterion naming
    ``secrets/id_rsa`` would put that name in the database by a second door, and
    the deny list exists to keep exactly that out — the fact that this door is
    labelled "requirement" rather than "claim" does not change what lands in the
    row.
    """
    if value is None:
        raise CriteriaSubmissionInvalid(reason_missing, ordinal)
    try:
        relative = normalize_claim_path(value)
    except ClaimPathInvalid as invalid:
        # Remapped to this module's own codes so a reader can tell a criterion
        # refusal from a claim refusal in a log line.
        if invalid.reason.endswith("escape"):
            raise CriteriaSubmissionInvalid(REASON_PATH_ESCAPE, ordinal) from None
        raise CriteriaSubmissionInvalid(REASON_PATH_INVALID, ordinal) from None
    if is_denied_path(relative):
        raise CriteriaSubmissionInvalid(REASON_PATH_DENIED_SENSITIVE, ordinal)
    return relative


def _description(value: object, ordinal: int, *, required: bool) -> Optional[str]:
    if value is None:
        if required:
            raise CriteriaSubmissionInvalid(REASON_DESCRIPTION_REQUIRED, ordinal)
        return None
    text = _text(value, REASON_DESCRIPTION_INVALID, ordinal)
    stripped = text.strip()
    if not stripped:
        if required:
            raise CriteriaSubmissionInvalid(REASON_DESCRIPTION_REQUIRED, ordinal)
        return None
    if len(stripped) > MAX_CRITERION_DESCRIPTION_CHARS:
        # Refused, not trimmed. A description is fingerprinted as part of the
        # exact snapshot, so quietly shortening one would store a requirement
        # the caller did not write and hash it as though they had.
        raise CriteriaSubmissionInvalid(REASON_DESCRIPTION_TOO_LONG, ordinal)
    for character in stripped:
        if character == "\x00" or (character < " " and character not in "\t\n"):
            raise CriteriaSubmissionInvalid(REASON_DESCRIPTION_INVALID, ordinal)
    return stripped


def _one_criterion(submitted: object, ordinal: int) -> AcceptanceCriterion:
    if not isinstance(submitted, Mapping):
        raise CriteriaSubmissionInvalid(REASON_CRITERION_MALFORMED, ordinal)

    keys = set(submitted.keys())
    for name in COMMAND_FIELD_NAMES:
        if name in keys:
            # Named explicitly rather than swept up by the unknown-field check,
            # because "this build does not store commands" is a different answer
            # from "I do not recognise that key", and the first is the one worth
            # giving. There is no dormant execution authority here to enable
            # later by relaxing a validator.
            raise CriteriaSubmissionInvalid(REASON_COMMAND_NOT_SUPPORTED, ordinal)
    for name in SERVER_OWNED_FIELDS:
        if name in keys:
            raise CriteriaSubmissionInvalid(REASON_SERVER_OWNED_FIELD, ordinal)
    unknown = keys - set(ALLOWED_CRITERION_FIELDS)
    if unknown:
        raise CriteriaSubmissionInvalid(REASON_UNKNOWN_FIELD, ordinal)

    kind = submitted.get("kind")
    if kind not in CRITERION_KINDS:
        raise CriteriaSubmissionInvalid(REASON_KIND_INVALID, ordinal)

    if kind == KIND_MANUAL:
        # A manual criterion is a sentence and nothing else. Every structured
        # field is refused rather than ignored: a manual criterion carrying a
        # path would look evidence-shaped to a future evaluator that branches on
        # the column rather than the kind.
        for name, reason in (
            ("predicate", REASON_PREDICATE_UNEXPECTED),
            ("path", REASON_PATH_UNEXPECTED),
            ("to_path", REASON_DESTINATION_UNEXPECTED),
            ("operation", REASON_OPERATION_UNEXPECTED),
        ):
            if submitted.get(name) is not None:
                raise CriteriaSubmissionInvalid(reason, ordinal)
        return AcceptanceCriterion(
            ordinal=ordinal,
            kind=KIND_MANUAL,
            # Required. A manual criterion with no description is a row that says
            # "a person must check something" and never says what.
            description=_description(
                submitted.get("description"), ordinal, required=True
            ),
        )

    predicate = submitted.get("predicate")
    if predicate not in EVIDENCE_PREDICATES:
        raise CriteriaSubmissionInvalid(REASON_PREDICATE_INVALID, ordinal)

    path = _criterion_path(
        submitted.get("path"), ordinal, reason_missing=REASON_PATH_REQUIRED
    )

    operation = submitted.get("operation")
    if predicate == PREDICATE_PATH_OPERATION:
        if operation not in CRITERION_OPERATIONS:
            raise CriteriaSubmissionInvalid(REASON_OPERATION_INVALID, ordinal)
    elif operation is not None:
        raise CriteriaSubmissionInvalid(REASON_OPERATION_UNEXPECTED, ordinal)

    to_path: Optional[str] = None
    if predicate == PREDICATE_RENAME:
        to_path = _criterion_path(
            submitted.get("to_path"), ordinal, reason_missing=REASON_DESTINATION_REQUIRED
        )
        if to_path == path:
            # A rename to itself is not a rename, and storing one would produce a
            # criterion no evidence can ever satisfy for a reason nobody reading
            # it would guess.
            raise CriteriaSubmissionInvalid(REASON_DESTINATION_IDENTICAL, ordinal)
    elif submitted.get("to_path") is not None:
        raise CriteriaSubmissionInvalid(REASON_DESTINATION_UNEXPECTED, ordinal)

    return AcceptanceCriterion(
        ordinal=ordinal,
        kind=KIND_EVIDENCE,
        predicate=predicate,
        path=path,
        to_path=to_path,
        operation=operation if predicate == PREDICATE_PATH_OPERATION else None,
        # Optional here, and explicitly **not** a rule. It may explain intent for
        # a person reading the audit; the structured fields above are what an
        # evaluator decides on, and no fallback reads this string when they are
        # ambiguous.
        description=_description(submitted.get("description"), ordinal, required=False),
    )


def _identity(criterion: AcceptanceCriterion) -> Tuple[Any, ...]:
    """Everything about a criterion except where it sits in the list."""
    return (
        criterion.kind,
        criterion.predicate,
        criterion.path,
        criterion.to_path,
        criterion.operation,
        criterion.description,
    )


def validate_criteria(submitted: object) -> Tuple[AcceptanceCriterion, ...]:
    """A submitted criteria set, validated into stored shape — or a refusal.

    ``None`` and an empty sequence both mean *no criteria were supplied*, which
    is a real answer rather than an error: the caller gets an empty tuple and the
    store writes an explicit :data:`CRITERIA_NOT_PROVIDED` snapshot.

    Everything else is refused rather than repaired. Nothing here truncates,
    dedupes, sorts, rewrites a path or shortens a description — see
    :data:`MAX_CRITERIA_PER_TURN` for why acceptance requirements are the one
    thing in this milestone that may not be silently reduced.
    """
    if submitted is None:
        return ()
    if isinstance(submitted, (str, bytes, Mapping)):
        # A single mapping is a plausible mistake and a string is a hopeless one;
        # both are refused rather than guessed at, because guessing here would
        # mean inventing a criteria set.
        raise CriteriaSubmissionInvalid(REASON_SUBMISSION_MALFORMED)
    if not isinstance(submitted, Sequence):
        raise CriteriaSubmissionInvalid(REASON_SUBMISSION_MALFORMED)
    if len(submitted) > MAX_CRITERIA_PER_TURN:
        raise CriteriaSubmissionInvalid(REASON_LIMIT_EXCEEDED)

    criteria: list = []
    seen: Dict[Tuple[Any, ...], int] = {}
    for index, item in enumerate(submitted):
        criterion = _one_criterion(item, index + 1)
        identity = _identity(criterion)
        if identity in seen:
            # Refused rather than collapsed. Dropping the second one would store
            # fewer criteria than were submitted, which is the silent-reduction
            # failure this module exists to avoid; keeping both would produce two
            # rows that can never disagree and only inflate the count.
            raise CriteriaSubmissionInvalid(REASON_DUPLICATE, criterion.ordinal)
        seen[identity] = criterion.ordinal
        criteria.append(criterion)
    return tuple(criteria)


def criteria_state(criteria: Sequence[AcceptanceCriterion]) -> str:
    """Which stored state a validated set represents.

    Total over the two writable states, and there is no third branch: a set is
    either non-empty and ``present`` or empty and ``not_provided``.
    :data:`CRITERIA_LEGACY_UNKNOWN` is not reachable from here because it is not
    a thing this build can write.
    """
    return CRITERIA_PRESENT if criteria else CRITERIA_NOT_PROVIDED


# -- the fingerprint ----------------------------------------------------------
#
# The same discipline `mind/hashing.py`, `claims.artifact_digest` and
# `evidence.input_fingerprint` record, with a tag of this module's own:
#
#     SHA256( tag || length-prefixed field || length-prefixed field || ... )
#
# Length-prefixed rather than delimited, because a delimiter is a character that
# can appear in a value. A path and a description are both caller-influenced
# text; joining fields with `:` would let `a/b` + `c` and `a` + `b/c` produce one
# string, and two different criteria sets would fingerprint the same.

TAG_FINGERPRINT = b"cofferdam.criteria.snapshot.v1"
LENGTH_PREFIX_WIDTH = 8
FINGERPRINT_CHARS = 64

#: Bumped when what is hashed changes shape. Domain separation inside the hash as
#: well as in the tag, so a v1 fingerprint and a v2 fingerprint over identical
#: criteria are visibly different values rather than an accidental collision.
CRITERIA_MODEL_VERSION = 1


class _Fingerprint:
    """A domain-tagged, length-prefixed SHA-256 over an ordered field list.

    Deliberately not ``json.dumps`` of a dict. Key order, separator whitespace
    and float formatting are all things a serializer may change between
    releases, and any of them changing would move every stored fingerprint
    without a single criterion having changed.
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


def criteria_fingerprint(
    state: str, criteria: Sequence[AcceptanceCriterion]
) -> str:
    """A stable hash of exactly the criteria facts this snapshot stores.

    What is in it
    -------------

    The model version, the criteria state, the count, and then every criterion in
    stored order by ``ordinal`` — kind, predicate, operation, path, destination
    path and description. The **description is in**, deliberately: it is part of
    what somebody asked for, two snapshots whose descriptions differ are not the
    same requirements, and a person editing a description has changed the
    criteria even when no structured field moved.

    The count is hashed as well as the criteria themselves. Belt and braces, but
    cheap, and it makes a truncated read visibly different rather than silently
    equal to a shorter set.

    What is deliberately **not** in it
    ----------------------------------

    * **The snapshot id and the criterion ids.** Those are minted per row and
      carry a clock and randomness, so hashing them would make the fingerprint
      change on every re-reservation and identify the *row* rather than the
      *criteria*. Two turns given the same requirements should share a
      fingerprint — that is what makes "this retry used the same criteria"
      checkable — and each still has its own snapshot id.
    * **The task id and the turn number.** Same reason, and this is the one place
      the criteria fingerprint deliberately differs from
      :func:`~.evidence.input_fingerprint`, which does bind them. That one
      identifies *the inputs one bundle was assembled from*; this one identifies
      *what was asked for*, which is a fact about requirements and not about
      which turn happened to receive them. A future EvaluationRecord binds both
      the turn and this fingerprint, so nothing is lost by keeping each value
      about one thing.
    * **Absolute host paths.** No project root, no ``/home/…``, no deployment
      slot. Every path here is project-relative by construction, so a deployment
      that moves does not move a stored fingerprint.
    * **Provider or session identifiers.** Not an input to what was asked for,
      and Task Core keeps its own turn identity precisely so it need not depend
      on a provider's.
    * **Any clock reading.** Not the capture time, not the recorded time, not the
      read time. A fingerprint that included one would differ on every write and
      identify nothing, and it must survive a restart unchanged.
    * **Database row order.** The hash walks ``ordinal``, which is stored, rather
      than whatever order a ``SELECT`` returned.
    """
    digest = _Fingerprint()
    digest.field("cofferdam.criteria.snapshot")
    digest.field(CRITERIA_MODEL_VERSION)
    digest.field(state)
    digest.field(len(criteria))
    for criterion in sorted(criteria, key=lambda item: item.ordinal):
        digest.field(criterion.ordinal)
        digest.field(criterion.kind)
        digest.field(criterion.predicate)
        digest.field(criterion.operation)
        digest.field(criterion.path)
        digest.field(criterion.to_path)
        digest.field(criterion.description)
    return digest.hexdigest()


__all__ = [
    "ALLOWED_CRITERION_FIELDS",
    "COMMAND_FIELD_NAMES",
    "CRITERIA_LEGACY_UNKNOWN",
    "CRITERIA_MODEL_VERSION",
    "CRITERIA_NOT_PROVIDED",
    "CRITERIA_PRESENT",
    "CRITERIA_STATES",
    "CRITERION_ID_CHARS",
    "CRITERION_ID_PREFIX",
    "CRITERION_KINDS",
    "CRITERION_OPERATIONS",
    "EVIDENCE_PREDICATES",
    "EXCLUDED_OPERATIONS",
    "FINGERPRINT_CHARS",
    "KIND_EVIDENCE",
    "KIND_MANUAL",
    "MAX_CRITERIA_PER_TURN",
    "MAX_CRITERION_DESCRIPTION_CHARS",
    "MAX_CRITERION_PATH_CHARS",
    "OPERATION_CREATED",
    "OPERATION_DELETED",
    "OPERATION_MODIFIED",
    "PREDICATE_PATH_CHANGED",
    "PREDICATE_PATH_OPERATION",
    "PREDICATE_RENAME",
    "REASON_COMMAND_NOT_SUPPORTED",
    "REASON_CRITERION_MALFORMED",
    "REASON_DESCRIPTION_INVALID",
    "REASON_DESCRIPTION_REQUIRED",
    "REASON_DESCRIPTION_TOO_LONG",
    "REASON_DESTINATION_IDENTICAL",
    "REASON_DESTINATION_REQUIRED",
    "REASON_DESTINATION_UNEXPECTED",
    "REASON_DUPLICATE",
    "REASON_KIND_INVALID",
    "REASON_LIMIT_EXCEEDED",
    "REASON_OPERATION_INVALID",
    "REASON_OPERATION_UNEXPECTED",
    "REASON_PATH_DENIED_SENSITIVE",
    "REASON_PATH_ESCAPE",
    "REASON_PATH_INVALID",
    "REASON_PATH_REQUIRED",
    "REASON_PATH_UNEXPECTED",
    "REASON_PREDICATE_INVALID",
    "REASON_PREDICATE_UNEXPECTED",
    "REASON_SERVER_OWNED_FIELD",
    "REASON_SUBMISSION_MALFORMED",
    "REASON_UNKNOWN_FIELD",
    "SERVER_OWNED_FIELDS",
    "SNAPSHOT_ID_CHARS",
    "SNAPSHOT_ID_PREFIX",
    "STORED_CRITERIA_STATES",
    "TAG_FINGERPRINT",
    "AcceptanceCriterion",
    "CriteriaSnapshot",
    "CriteriaSubmissionInvalid",
    "criteria_fingerprint",
    "criteria_state",
    "new_criterion_id",
    "new_snapshot_id",
    "valid_criterion_id",
    "valid_snapshot_id",
    "validate_criteria",
]
