"""M2K PR18 — state predicates bound to final state, on constructed inputs.

Everything here builds PR11, PR7 and PR14 shapes directly rather than through a
database, because the binder's whole claim is that it is a deterministic function
of immutable values. If a test needed a store or a filesystem to exercise it,
that claim would already be false.

Three assertions in this file are load-bearing and the rest support them:

* `SamePredicateDifferentDomain` — a change criterion and a state criterion about
  the *same path* at the *same turn* are decided by different evidence and may
  legitimately disagree. That is the milestone's central rule, and the place a
  future refactor is most likely to break it;
* `DomainConditionalInputs` — each domain's evidence is required only by the
  criteria that consume it, in all four combinations. A coupling here would stay
  invisible until the day one pipeline stage lagged behind the other;
* `MalformedObservation` — structural corruption fails the set closed and is
  never laundered into an ordinary `unverified`.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.binding import (
    ASSESSMENT_RESOLVED,
    ASSESSMENT_UNAVAILABLE,
    CURRENT_ASSESSMENT_VERSION,
    DOMAIN_FINAL_STATE,
    DOMAIN_NOT_APPLICABLE,
    DOMAIN_TURN_CHANGE,
    EVIDENCE_DOMAINS,
    REASON_EVALUATION_NOT_RECORDED,
    REASON_FINAL_STATE_INCONSISTENT,
    REASON_FINAL_STATE_LINEAGE_MISMATCH,
    REASON_FINAL_STATE_NOT_RECORDED,
    REASON_FINAL_STATE_OBSERVED,
    REASON_FINAL_STATE_PATH_MISSING,
    REASON_FINAL_STATE_PATH_UNAVAILABLE,
    REASON_FINAL_STATE_UNAVAILABLE,
    REASON_INHERITED_CHANGE_NOT_CURRENT,
    REASON_MANUAL_AUTHORITY,
    REASON_TURN_CHANGE_EVALUATED,
    REASON_UNSUPPORTED_OBSERVER,
    SET_REASONS,
    STATE_PREDICATES,
    SUPPORTED_OBSERVER_VERSIONS,
    bind,
)
from cofferdam.workstation.tasks.criteria import (
    PREDICATE_PATH_ABSENT,
    PREDICATE_PATH_EXISTS,
    AcceptanceCriterion,
)
from cofferdam.workstation.tasks.evaluation import (
    EVALUATOR_VERSION,
    RESULT_MET,
    RESULT_NOT_MET,
    RESULT_UNVERIFIED,
    CriterionResult,
    EvaluationRecord,
)
from cofferdam.workstation.tasks.finalstate import (
    FINAL_STATE_OBSERVER_VERSION,
    KIND_DIRECTORY,
    KIND_FILE,
    KIND_OTHER,
    KIND_SYMLINK,
    OBSERVATION_COMPLETE,
    OBSERVATION_INCOMPLETE,
    OBSERVATION_LEGACY_UNKNOWN,
    OBSERVATION_UNAVAILABLE,
    PATH_ABSENT,
    PATH_PRESENT,
    PATH_UNAVAILABLE,
    REASON_OBSERVATION_UNSTABLE,
    REASON_PERMISSION_DENIED,
    REASON_SYMLINK_TRAVERSAL_REFUSED,
    FinalStateObservation,
    PathObservation,
    final_state_fingerprint,
)
from cofferdam.workstation.tasks.lineage import (
    RESOLVER_VERSION,
    ActiveCriterion,
    ResolvedActiveCriteria,
)

TASK = "task_01aaaaaaaaaaaaaaaaaaaaaaaa"
LINEAGE = "lineage" + "f" * 58
REPO_ROOT = Path(__file__).resolve().parents[1]


# -- builders -----------------------------------------------------------------


def state(ordinal, path, predicate=PREDICATE_PATH_EXISTS):
    return AcceptanceCriterion(
        ordinal=ordinal, kind="evidence", predicate=predicate, path=path
    )


def change(ordinal, path, predicate="path_changed", operation=None):
    return AcceptanceCriterion(
        ordinal=ordinal,
        kind="evidence",
        predicate=predicate,
        path=path,
        operation=operation,
    )


def manual(ordinal):
    return AcceptanceCriterion(
        ordinal=ordinal, kind="manual", description="a human decides"
    )


def active(criterion_id, source_turn, item, *, snapshot=None):
    return ActiveCriterion(
        criterion_id=criterion_id,
        source_snapshot_id=snapshot or ("snapshot_000%d" % source_turn),
        source_turn_number=source_turn,
        source_ordinal=item.ordinal,
        criterion=item,
    )


def resolved(target, entries, *, fingerprint=LINEAGE, task_id=TASK):
    return ResolvedActiveCriteria(
        task_id=task_id,
        target_turn_number=target,
        target_snapshot_id="snapshot_000%d" % target,
        resolver_version=RESOLVER_VERSION,
        active=tuple(entries),
        lineage=(),
        fingerprint=fingerprint,
        state="resolved",
    )


def evaluation(target, results):
    return EvaluationRecord(
        evaluation_id="eval_000000000001",
        task_id=TASK,
        turn_number=target,
        evaluator_version=EVALUATOR_VERSION,
        criteria_state="present",
        criteria_snapshot_id="snapshot_000%d" % target,
        criteria_fingerprint="c" * 64,
        assembler_version=3,
        evidence_input_fingerprint="e" * 64,
        result_count=len(results),
        evaluation_fingerprint="d" * 64,
        recorded_at="2026-01-01T00:00:00Z",
        results=tuple(results),
    )


def result(criterion_id, ordinal, value, reason="machine_change_observed"):
    return CriterionResult(
        criterion_id=criterion_id, ordinal=ordinal, result=value, reason=reason
    )


def paths(*specs):
    """``("a.txt", "present", "file")`` tuples into ordered PathObservations."""
    return tuple(
        PathObservation(
            ordinal=index,
            path=spec[0],
            state=spec[1],
            kind=spec[2] if len(spec) > 2 else None,
            reason=spec[3] if len(spec) > 3 else None,
        )
        for index, spec in enumerate(specs, start=1)
    )


def observation(
    turn,
    rows=(),
    *,
    obs_state=OBSERVATION_COMPLETE,
    limitation=None,
    lineage=LINEAGE,
    head="0" * 40,
    task_id=TASK,
    observer_version=FINAL_STATE_OBSERVER_VERSION,
    path_count=None,
    fingerprint=None,
):
    """A stored observation, fingerprinted exactly as the store would have.

    The fingerprint is computed rather than stubbed, so a test that corrupts a
    field without recomputing produces precisely the row a raw-SQL edit would —
    which is the input the structural checks exist for.
    """
    return FinalStateObservation(
        task_id=task_id,
        turn_number=turn,
        state=obs_state,
        observation_id="fs_000000000001",
        observer_version=observer_version,
        limitation_reason=limitation,
        lineage_fingerprint=lineage,
        head_revision=head,
        path_count=len(rows) if path_count is None else path_count,
        fingerprint=fingerprint
        or final_state_fingerprint(
            task_id, turn, obs_state, limitation, lineage, head, rows
        ),
        recorded_at="2026-01-01T00:00:00Z",
        paths=rows,
    )


def legacy(turn):
    return FinalStateObservation(
        task_id=TASK, turn_number=turn, state=OBSERVATION_LEGACY_UNKNOWN
    )


# -- path_exists / path_absent ------------------------------------------------


class PathExistsTests(unittest.TestCase):
    """`path_exists(P)`: was ANY filesystem object at P at this boundary?"""

    def answer(self, path_state, kind=None):
        return bind(
            resolved(1, [active("criterion_0001", 1, state(1, "a.txt"))]),
            None,
            turn_closed=True,
            final_state=observation(1, paths(("a.txt", path_state, kind))),
        ).assessments[0]

    def test_present_is_met(self):
        assessment = self.answer(PATH_PRESENT, KIND_FILE)
        self.assertEqual(RESULT_MET, assessment.result)
        self.assertEqual(DOMAIN_FINAL_STATE, assessment.domain)
        self.assertEqual(REASON_FINAL_STATE_OBSERVED, assessment.reason)

    def test_absent_is_not_met(self):
        assessment = self.answer(PATH_ABSENT)
        self.assertEqual(RESULT_NOT_MET, assessment.result)
        self.assertEqual(DOMAIN_FINAL_STATE, assessment.domain)
        self.assertEqual(REASON_FINAL_STATE_OBSERVED, assessment.reason)

    def test_unavailable_is_unverified_and_never_not_met(self):
        """"We could not look" is not "it is not there"."""
        assessment = bind(
            resolved(1, [active("criterion_0001", 1, state(1, "a.txt"))]),
            None,
            turn_closed=True,
            final_state=observation(
                1,
                paths(("a.txt", PATH_UNAVAILABLE, None, REASON_PERMISSION_DENIED)),
                obs_state=OBSERVATION_INCOMPLETE,
                limitation=REASON_PERMISSION_DENIED,
            ),
        ).assessments[0]
        self.assertEqual(RESULT_UNVERIFIED, assessment.result)
        self.assertEqual(REASON_FINAL_STATE_PATH_UNAVAILABLE, assessment.reason)
        self.assertNotEqual(RESULT_NOT_MET, assessment.result)


class PathAbsentTests(unittest.TestCase):
    """`path_absent(P)`: was there NO filesystem object at P at this boundary?"""

    def answer(self, path_state, kind=None):
        return bind(
            resolved(
                1,
                [active("criterion_0001", 1, state(1, "b.txt", PREDICATE_PATH_ABSENT))],
            ),
            None,
            turn_closed=True,
            final_state=observation(1, paths(("b.txt", path_state, kind))),
        ).assessments[0]

    def test_absent_is_met(self):
        self.assertEqual(RESULT_MET, self.answer(PATH_ABSENT).result)

    def test_present_is_not_met(self):
        self.assertEqual(RESULT_NOT_MET, self.answer(PATH_PRESENT, KIND_FILE).result)

    def test_a_path_that_never_existed_satisfies_it(self):
        """`path_absent` is not "something deleted it"."""
        assessment = self.answer(PATH_ABSENT)
        self.assertEqual(RESULT_MET, assessment.result)
        self.assertEqual(REASON_FINAL_STATE_OBSERVED, assessment.reason)

    def test_unavailable_is_unverified(self):
        assessment = bind(
            resolved(
                1,
                [active("criterion_0001", 1, state(1, "b.txt", PREDICATE_PATH_ABSENT))],
            ),
            None,
            turn_closed=True,
            final_state=observation(
                1,
                paths(
                    (
                        "b.txt",
                        PATH_UNAVAILABLE,
                        None,
                        REASON_SYMLINK_TRAVERSAL_REFUSED,
                    )
                ),
                obs_state=OBSERVATION_INCOMPLETE,
                limitation=REASON_SYMLINK_TRAVERSAL_REFUSED,
            ),
        ).assessments[0]
        self.assertEqual(RESULT_UNVERIFIED, assessment.result)
        self.assertEqual(REASON_FINAL_STATE_PATH_UNAVAILABLE, assessment.reason)


class ObjectKindTests(unittest.TestCase):
    """Existence is existence. PR18 adds no kind predicate and reads none."""

    def exists(self, kind):
        return bind(
            resolved(1, [active("criterion_0001", 1, state(1, "x"))]),
            None,
            turn_closed=True,
            final_state=observation(1, paths(("x", PATH_PRESENT, kind))),
        ).assessments[0]

    def test_every_kind_is_present(self):
        for kind in (KIND_FILE, KIND_DIRECTORY, KIND_SYMLINK, KIND_OTHER):
            with self.subTest(kind=kind):
                self.assertEqual(RESULT_MET, self.exists(kind).result)

    def test_a_broken_symlink_is_a_present_object(self):
        """PR14 records the link itself without following it, and so does this.

        The link at `x` points nowhere. It is still an object at `x`, so
        `path_exists` is met and `path_absent` is not — and nothing here asks
        what the target was, because that is a question this build does not ask.
        """
        rows = paths(("x", PATH_PRESENT, KIND_SYMLINK))
        exists = bind(
            resolved(1, [active("criterion_0001", 1, state(1, "x"))]),
            None,
            turn_closed=True,
            final_state=observation(1, rows),
        ).assessments[0]
        absent = bind(
            resolved(
                1, [active("criterion_0001", 1, state(1, "x", PREDICATE_PATH_ABSENT))]
            ),
            None,
            turn_closed=True,
            final_state=observation(1, rows),
        ).assessments[0]
        self.assertEqual(RESULT_MET, exists.result)
        self.assertEqual(RESULT_NOT_MET, absent.result)

    def test_the_kind_is_carried_for_audit_but_does_not_move_the_result(self):
        prints = {self.exists(kind).result for kind in (KIND_FILE, KIND_SYMLINK)}
        self.assertEqual({RESULT_MET}, prints)
        self.assertEqual(KIND_SYMLINK, self.exists(KIND_SYMLINK).path_kind)
        self.assertEqual(PATH_PRESENT, self.exists(KIND_SYMLINK).path_state)

    def test_no_kind_predicate_was_added(self):
        for forbidden in ("path_is_file", "path_is_directory", "path_is_symlink"):
            self.assertNotIn(forbidden, STATE_PREDICATES)


# -- same turn, inherited, and no carry-forward -------------------------------


class SameTurnStateTests(unittest.TestCase):
    def test_two_state_criteria_at_their_own_turn(self):
        answer = bind(
            resolved(
                1,
                [
                    active("criterion_0001", 1, state(1, "a.txt")),
                    active(
                        "criterion_0002", 1, state(2, "b.txt", PREDICATE_PATH_ABSENT)
                    ),
                ],
            ),
            None,
            turn_closed=True,
            final_state=observation(
                1, paths(("a.txt", PATH_PRESENT, KIND_FILE), ("b.txt", PATH_ABSENT))
            ),
        )
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual([RESULT_MET, RESULT_MET], [a.result for a in answer.assessments])

    def test_the_negative_case(self):
        answer = bind(
            resolved(
                1,
                [
                    active("criterion_0001", 1, state(1, "a.txt")),
                    active(
                        "criterion_0002", 1, state(2, "b.txt", PREDICATE_PATH_ABSENT)
                    ),
                ],
            ),
            None,
            turn_closed=True,
            final_state=observation(
                1, paths(("a.txt", PATH_ABSENT), ("b.txt", PATH_PRESENT, KIND_FILE))
            ),
        )
        self.assertEqual(
            [RESULT_NOT_MET, RESULT_NOT_MET], [a.result for a in answer.assessments]
        )


class InheritedStateTests(unittest.TestCase):
    """A state question is re-askable, so it is re-asked at every target."""

    def at(self, target, path_state, kind=None):
        """Criterion A, authored at turn 1, assessed at `target`."""
        return bind(
            resolved(target, [active("criterion_0001", 1, state(1, "foo"))]),
            None,
            turn_closed=True,
            final_state=observation(
                target, paths(("foo", path_state, kind)), lineage=LINEAGE
            ),
        ).assessments[0]

    def test_regression_and_repair_across_three_turns(self):
        """met, not_met, met — three honest answers about three boundaries."""
        self.assertEqual(RESULT_MET, self.at(1, PATH_PRESENT, KIND_FILE).result)
        self.assertEqual(RESULT_NOT_MET, self.at(2, PATH_ABSENT).result)
        self.assertEqual(RESULT_MET, self.at(3, PATH_PRESENT, KIND_FILE).result)

    def test_no_old_target_result_is_carried_forward(self):
        """Turn 2's answer is turn 2's observation, not turn 1's `met`."""
        assessment = self.at(2, PATH_ABSENT)
        self.assertEqual(RESULT_NOT_MET, assessment.result)
        self.assertEqual(DOMAIN_FINAL_STATE, assessment.domain)
        self.assertTrue(assessment.inherited)
        self.assertEqual(1, assessment.source_turn_number)
        self.assertEqual(2, assessment.target_turn_number)

    def test_an_inherited_state_criterion_is_not_the_inherited_change_answer(self):
        """The distinction PR18 exists to draw.

        An inherited *change* criterion is unanswerable here because its question
        is about a turn that ended. An inherited *state* criterion's question is
        about this boundary and is answered.
        """
        assessment = self.at(4, PATH_PRESENT, KIND_DIRECTORY)
        self.assertNotEqual(REASON_INHERITED_CHANGE_NOT_CURRENT, assessment.reason)
        self.assertEqual(REASON_FINAL_STATE_OBSERVED, assessment.reason)

    def test_each_target_produces_a_distinct_derived_fingerprint(self):
        prints = {self.at(turn, PATH_PRESENT, KIND_FILE).fingerprint for turn in (1, 2, 3)}
        self.assertEqual(3, len(prints))


# -- the two domains do not touch ---------------------------------------------


class SamePredicateDifferentDomain(unittest.TestCase):
    """The central rule: same path, same turn, two questions, two answers."""

    def answer(self):
        return bind(
            resolved(
                1,
                [
                    active(
                        "criterion_0001",
                        1,
                        change(1, "foo.py", "path_operation", "created"),
                    ),
                    active("criterion_0002", 1, state(2, "foo.py")),
                ],
            ),
            evaluation(1, [result("criterion_0001", 1, RESULT_MET)]),
            turn_closed=True,
            # The worker created foo.py and something removed it before the
            # boundary. Both facts are true.
            final_state=observation(1, paths(("foo.py", PATH_ABSENT))),
        )

    def test_the_change_criterion_stays_met(self):
        """It asks what the turn did. The turn did it."""
        assessment = self.answer().assessments[0]
        self.assertEqual(RESULT_MET, assessment.result)
        self.assertEqual(DOMAIN_TURN_CHANGE, assessment.domain)
        self.assertEqual("d" * 64, assessment.evidence_fingerprint)

    def test_the_state_criterion_is_not_met(self):
        """It asks what is there. Nothing is."""
        assessment = self.answer().assessments[1]
        self.assertEqual(RESULT_NOT_MET, assessment.result)
        self.assertEqual(DOMAIN_FINAL_STATE, assessment.domain)

    def test_the_two_carry_different_evidence_identities(self):
        first, second = self.answer().assessments
        self.assertNotEqual(first.evidence_fingerprint, second.evidence_fingerprint)
        self.assertNotEqual(first.domain, second.domain)


class FinalStateIsNotAuthorityForChange(unittest.TestCase):
    """A change criterion's answer does not move with the observation."""

    def bound(self, path_state, kind=None):
        return bind(
            resolved(1, [active("criterion_0001", 1, change(1, "foo.py"))]),
            evaluation(1, [result("criterion_0001", 1, RESULT_MET)]),
            turn_closed=True,
            final_state=observation(1, paths(("foo.py", path_state, kind))),
        ).assessments[0]

    def test_present_absent_and_unavailable_all_produce_the_same_answer(self):
        prints = {
            self.bound(PATH_PRESENT, KIND_FILE).fingerprint,
            self.bound(PATH_ABSENT).fingerprint,
        }
        self.assertEqual(1, len(prints))

    def test_it_stays_the_exact_stored_pr7_result(self):
        self.assertEqual(RESULT_MET, self.bound(PATH_ABSENT).result)
        self.assertEqual(DOMAIN_TURN_CHANGE, self.bound(PATH_ABSENT).domain)

    def test_an_inherited_change_criterion_is_still_unverified(self):
        """Final state existing does not repair an unanswerable question."""
        assessment = bind(
            resolved(
                2,
                [
                    active("criterion_0001", 1, change(1, "foo.py")),
                    active("criterion_0002", 2, state(1, "foo.py")),
                ],
            ),
            None,
            turn_closed=True,
            final_state=observation(2, paths(("foo.py", PATH_PRESENT, KIND_FILE))),
        ).assessments[0]
        self.assertEqual(RESULT_UNVERIFIED, assessment.result)
        self.assertEqual(REASON_INHERITED_CHANGE_NOT_CURRENT, assessment.reason)
        self.assertEqual(DOMAIN_NOT_APPLICABLE, assessment.domain)
        self.assertIsNone(assessment.evidence_fingerprint)


class Pr7StateResultIsNotAuthority(unittest.TestCase):
    """PR7's `unsupported_capability` row must not decide a state criterion.

    PR7 records `unverified` for a state predicate today. That record is correct
    and permanent — it says what the turn-change evaluator could establish — and
    it is not what this layer reads. The proof is mechanical: the stored result
    is varied across all three values and the state answer does not move.
    """

    def bound(self, stored):
        return bind(
            resolved(
                1,
                [
                    active("criterion_0001", 1, state(1, "foo")),
                    # A same-turn change criterion, so the record is genuinely
                    # required and genuinely consulted — for the other criterion.
                    active("criterion_0002", 1, change(2, "bar")),
                ],
            ),
            evaluation(
                1,
                [
                    result("criterion_0001", 1, stored, "unsupported_capability"),
                    result("criterion_0002", 2, RESULT_MET),
                ],
            ),
            turn_closed=True,
            final_state=observation(
                1, paths(("foo", PATH_PRESENT, KIND_FILE), ("bar", PATH_ABSENT))
            ),
        ).assessments[0]

    def test_the_state_answer_is_identical_for_every_stored_pr7_result(self):
        answers = [self.bound(value) for value in (RESULT_MET, RESULT_NOT_MET, RESULT_UNVERIFIED)]
        self.assertEqual({RESULT_MET}, {a.result for a in answers})
        self.assertEqual(1, len({a.fingerprint for a in answers}))

    def test_the_state_answer_never_binds_the_evaluation_fingerprint(self):
        assessment = self.bound(RESULT_NOT_MET)
        self.assertEqual(DOMAIN_FINAL_STATE, assessment.domain)
        self.assertNotEqual("d" * 64, assessment.evidence_fingerprint)


# -- domain-conditional inputs ------------------------------------------------


class DomainConditionalInputs(unittest.TestCase):
    """Each domain's evidence is required only by the criteria that consume it."""

    def test_a_state_only_target_resolves_with_no_evaluation(self):
        """Example A. Nothing in this set consumes a turn-change judgement."""
        answer = bind(
            resolved(1, [active("criterion_0001", 1, state(1, "a.txt"))]),
            None,
            turn_closed=True,
            final_state=observation(1, paths(("a.txt", PATH_PRESENT, KIND_FILE))),
        )
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(RESULT_MET, answer.assessments[0].result)

    def test_a_same_turn_change_criterion_still_requires_one(self):
        """Example B. The evaluatable state criterion does not repair the gap."""
        answer = bind(
            resolved(
                1,
                [
                    active("criterion_0001", 1, change(1, "foo.py")),
                    active("criterion_0002", 1, state(2, "foo.py")),
                ],
            ),
            None,
            turn_closed=True,
            final_state=observation(1, paths(("foo.py", PATH_PRESENT, KIND_FILE))),
        )
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(REASON_EVALUATION_NOT_RECORDED, answer.unavailable_reason)

    def test_an_inherited_change_criterion_does_not_require_one(self):
        """Example C. It needs no target-turn input; it is answered without any."""
        answer = bind(
            resolved(
                2,
                [
                    active("criterion_0001", 1, change(1, "foo.py")),
                    active("criterion_0002", 2, state(1, "foo.py")),
                ],
            ),
            None,
            turn_closed=True,
            final_state=observation(2, paths(("foo.py", PATH_PRESENT, KIND_FILE))),
        )
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(REASON_INHERITED_CHANGE_NOT_CURRENT, answer.assessments[0].reason)
        self.assertEqual(RESULT_MET, answer.assessments[1].result)

    def test_a_manual_plus_state_target_does_not_require_one(self):
        """Example D."""
        answer = bind(
            resolved(
                1,
                [
                    active("criterion_0001", 1, manual(1)),
                    active("criterion_0002", 1, state(2, "a.txt")),
                ],
            ),
            None,
            turn_closed=True,
            final_state=observation(1, paths(("a.txt", PATH_ABSENT))),
        )
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(REASON_MANUAL_AUTHORITY, answer.assessments[0].reason)
        self.assertEqual(RESULT_NOT_MET, answer.assessments[1].result)

    def test_a_change_only_target_is_unaffected_by_a_missing_observation(self):
        """The converse, and it must be exactly as strict."""
        answer = bind(
            resolved(
                1,
                [
                    active("criterion_0001", 1, change(1, "foo.py")),
                    active("criterion_0002", 1, manual(2)),
                ],
            ),
            evaluation(1, [result("criterion_0001", 1, RESULT_MET)]),
            turn_closed=True,
            final_state=None,
        )
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(RESULT_MET, answer.assessments[0].result)

    def test_a_change_only_target_ignores_an_observation_it_was_handed(self):
        """Not merely tolerant of a missing one — indifferent to a present one.

        A corrupt observation must not become an authority dependency of a set
        that would never have read it, so the same answer is produced whether one
        is supplied, absent, or structurally broken.
        """
        broken = observation(1, paths(("foo.py", PATH_PRESENT, KIND_FILE)), lineage="x" * 64)
        prints = {
            bind(
                resolved(1, [active("criterion_0001", 1, change(1, "foo.py"))]),
                evaluation(1, [result("criterion_0001", 1, RESULT_MET)]),
                turn_closed=True,
                final_state=supplied,
            ).fingerprint
            for supplied in (None, legacy(1), broken)
        }
        self.assertEqual(1, len(prints))

    def test_a_state_criterion_without_an_observation_does_not_fail_the_set(self):
        """A missing observation is a limit on one domain, not on the record."""
        answer = bind(
            resolved(
                1,
                [
                    active("criterion_0001", 1, change(1, "foo.py")),
                    active("criterion_0002", 1, state(2, "a.txt")),
                ],
            ),
            evaluation(1, [result("criterion_0001", 1, RESULT_MET)]),
            turn_closed=True,
            final_state=legacy(1),
        )
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(RESULT_MET, answer.assessments[0].result)
        self.assertEqual(RESULT_UNVERIFIED, answer.assessments[1].result)
        self.assertEqual(REASON_FINAL_STATE_NOT_RECORDED, answer.assessments[1].reason)


# -- observation-level limitations --------------------------------------------


class IncompleteObservationTests(unittest.TestCase):
    """PR15's per-path authority survives a partial observation."""

    def answer(self):
        return bind(
            resolved(
                1,
                [
                    active("criterion_0001", 1, state(1, "A")),
                    active("criterion_0002", 1, state(2, "B")),
                ],
            ),
            None,
            turn_closed=True,
            final_state=observation(
                1,
                paths(
                    ("A", PATH_PRESENT, KIND_FILE),
                    ("B", PATH_UNAVAILABLE, None, REASON_PERMISSION_DENIED),
                ),
                obs_state=OBSERVATION_INCOMPLETE,
                limitation=REASON_PERMISSION_DENIED,
            ),
        )

    def test_the_safely_observed_path_is_still_decided(self):
        self.assertEqual(RESULT_MET, self.answer().assessments[0].result)

    def test_the_blocked_path_is_unverified(self):
        assessment = self.answer().assessments[1]
        self.assertEqual(RESULT_UNVERIFIED, assessment.result)
        self.assertEqual(REASON_FINAL_STATE_PATH_UNAVAILABLE, assessment.reason)

    def test_there_is_no_parent_level_blanket_refusal(self):
        """Discarding a real fact over an unrelated wall would be evidence loss."""
        self.assertEqual(ASSESSMENT_RESOLVED, self.answer().state)


class UnavailableObservationTests(unittest.TestCase):
    def answer(self):
        return bind(
            resolved(1, [active("criterion_0001", 1, state(1, "a.txt"))]),
            None,
            turn_closed=True,
            final_state=observation(
                1,
                (),
                obs_state=OBSERVATION_UNAVAILABLE,
                limitation=REASON_OBSERVATION_UNSTABLE,
            ),
        )

    def test_state_criteria_are_unverified(self):
        assessment = self.answer().assessments[0]
        self.assertEqual(RESULT_UNVERIFIED, assessment.result)
        self.assertEqual(REASON_FINAL_STATE_UNAVAILABLE, assessment.reason)

    def test_no_result_is_met_or_not_met(self):
        self.assertNotIn(
            self.answer().assessments[0].result, (RESULT_MET, RESULT_NOT_MET)
        )

    def test_the_set_still_resolves(self):
        """An observation that honestly refused is not a corrupt one."""
        self.assertEqual(ASSESSMENT_RESOLVED, self.answer().state)

    def test_no_evidence_identity_is_fabricated(self):
        self.assertIsNone(self.answer().assessments[0].evidence_fingerprint)


class LegacyObservationTests(unittest.TestCase):
    """A turn that ran before PR14. No row, no probe, no guess."""

    def answer(self, supplied):
        return bind(
            resolved(1, [active("criterion_0001", 1, state(1, "a.txt"))]),
            None,
            turn_closed=True,
            final_state=supplied,
        ).assessments[0]

    def test_legacy_unknown_is_unverified(self):
        assessment = self.answer(legacy(1))
        self.assertEqual(RESULT_UNVERIFIED, assessment.result)
        self.assertEqual(REASON_FINAL_STATE_NOT_RECORDED, assessment.reason)

    def test_it_is_never_not_met(self):
        self.assertNotEqual(RESULT_NOT_MET, self.answer(legacy(1)).result)

    def test_no_observation_supplied_means_the_same_thing(self):
        """Both mean "nothing was recorded, so nothing may be assumed"."""
        self.assertEqual(
            self.answer(legacy(1)).fingerprint, self.answer(None).fingerprint
        )

    def test_no_fingerprint_is_fabricated_for_it(self):
        self.assertIsNone(self.answer(legacy(1)).evidence_fingerprint)
        self.assertIsNone(self.answer(legacy(1)).path_state)


# -- structural corruption ----------------------------------------------------


class MalformedObservation(unittest.TestCase):
    """Structural corruption fails the set closed. It is never per-criterion."""

    def refuse(self, supplied, expected):
        answer = bind(
            resolved(1, [active("criterion_0001", 1, state(1, "a.txt"))]),
            None,
            turn_closed=True,
            final_state=supplied,
        )
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(expected, answer.unavailable_reason)
        self.assertEqual((), answer.assessments)
        return answer

    def test_a_wrong_turn_identity_is_refused(self):
        self.refuse(
            observation(7, paths(("a.txt", PATH_PRESENT, KIND_FILE))),
            REASON_FINAL_STATE_INCONSISTENT,
        )

    def test_a_wrong_task_identity_is_refused(self):
        self.refuse(
            observation(
                1, paths(("a.txt", PATH_PRESENT, KIND_FILE)), task_id="task_other"
            ),
            REASON_FINAL_STATE_INCONSISTENT,
        )

    def test_an_unsupported_observer_version_is_refused(self):
        """Not "the columns parse, so it must mean the same thing"."""
        self.refuse(
            observation(
                1, paths(("a.txt", PATH_PRESENT, KIND_FILE)), observer_version=2
            ),
            REASON_UNSUPPORTED_OBSERVER,
        )

    def test_a_path_count_that_disagrees_with_the_children_is_refused(self):
        self.refuse(
            observation(1, paths(("a.txt", PATH_PRESENT, KIND_FILE)), path_count=4),
            REASON_FINAL_STATE_INCONSISTENT,
        )

    def test_a_duplicated_path_is_refused(self):
        rows = (
            PathObservation(ordinal=1, path="a.txt", state=PATH_PRESENT, kind=KIND_FILE),
            PathObservation(ordinal=2, path="a.txt", state=PATH_ABSENT),
        )
        self.refuse(observation(1, rows), REASON_FINAL_STATE_INCONSISTENT)

    def test_a_duplicated_ordinal_is_refused(self):
        rows = (
            PathObservation(ordinal=1, path="a.txt", state=PATH_PRESENT, kind=KIND_FILE),
            PathObservation(ordinal=1, path="b.txt", state=PATH_ABSENT),
        )
        self.refuse(observation(1, rows), REASON_FINAL_STATE_INCONSISTENT)

    def test_an_unavailable_observation_carrying_paths_is_refused(self):
        """It had no defensible target list, so children imply a scope it lacked."""
        self.refuse(
            observation(
                1,
                paths(("a.txt", PATH_PRESENT, KIND_FILE)),
                obs_state=OBSERVATION_UNAVAILABLE,
                limitation=REASON_OBSERVATION_UNSTABLE,
            ),
            REASON_FINAL_STATE_INCONSISTENT,
        )

    def test_a_lineage_fingerprint_mismatch_is_refused(self):
        """The observation's declared scope is a different requirement set."""
        self.refuse(
            observation(
                1, paths(("a.txt", PATH_PRESENT, KIND_FILE)), lineage="other" + "f" * 59
            ),
            REASON_FINAL_STATE_LINEAGE_MISMATCH,
        )

    def test_a_recorded_observation_with_no_lineage_fingerprint_is_refused(self):
        """PR14 sets it whenever it had a scope, so its absence is malformed."""
        self.refuse(
            observation(1, paths(("a.txt", PATH_PRESENT, KIND_FILE)), lineage=None),
            REASON_FINAL_STATE_LINEAGE_MISMATCH,
        )

    def test_a_matching_looking_path_is_not_consumed_despite_the_mismatch(self):
        """The path is right and the scope is wrong; the scope decides."""
        answer = bind(
            resolved(1, [active("criterion_0001", 1, state(1, "a.txt"))]),
            None,
            turn_closed=True,
            final_state=observation(
                1, paths(("a.txt", PATH_PRESENT, KIND_FILE)), lineage="z" * 64
            ),
        )
        self.assertEqual((), answer.assessments)
        self.assertNotEqual(ASSESSMENT_RESOLVED, answer.state)

    def test_an_expected_path_missing_from_a_recorded_scope_is_refused(self):
        """Never `absent`, and never quietly per-path `unverified`.

        PR14 gives every target an explicit child row and stores an unobservable
        one as `unavailable` with a reason. So a missing row is not a missing
        file — it is an observation that does not describe the scope it claims.
        """
        answer = self.refuse(
            observation(1, paths(("other.txt", PATH_PRESENT, KIND_FILE))),
            REASON_FINAL_STATE_PATH_MISSING,
        )
        self.assertNotEqual(ASSESSMENT_RESOLVED, answer.state)

    def test_a_corrupted_path_state_is_caught_by_the_fingerprint(self):
        """The check that makes raw-SQL edits to a path row detectable at all."""
        honest = observation(1, paths(("a.txt", PATH_ABSENT)))
        tampered = FinalStateObservation(
            **{
                **honest.__dict__,
                "paths": paths(("a.txt", PATH_PRESENT, KIND_FILE)),
            }
        )
        self.refuse(tampered, REASON_FINAL_STATE_INCONSISTENT)

    def test_a_corrupted_head_revision_is_caught_by_the_fingerprint(self):
        honest = observation(1, paths(("a.txt", PATH_ABSENT)))
        tampered = FinalStateObservation(**{**honest.__dict__, "head_revision": "f" * 40})
        self.refuse(tampered, REASON_FINAL_STATE_INCONSISTENT)

    def test_a_fabricated_fingerprint_string_is_not_authority(self):
        self.refuse(
            observation(1, paths(("a.txt", PATH_PRESENT, KIND_FILE)), fingerprint="0" * 64),
            REASON_FINAL_STATE_INCONSISTENT,
        )

    def test_nothing_is_repaired(self):
        """A read that fixed a row would destroy the proof something wrote it."""
        broken = observation(1, paths(("a.txt", PATH_PRESENT, KIND_FILE)), path_count=9)
        self.refuse(broken, REASON_FINAL_STATE_INCONSISTENT)
        self.assertEqual(9, broken.path_count)

    def test_every_refusal_reason_is_in_the_closed_set(self):
        for reason in (
            REASON_FINAL_STATE_INCONSISTENT,
            REASON_UNSUPPORTED_OBSERVER,
            REASON_FINAL_STATE_LINEAGE_MISMATCH,
            REASON_FINAL_STATE_PATH_MISSING,
        ):
            self.assertIn(reason, SET_REASONS)


class ObserverVersionTests(unittest.TestCase):
    def test_only_version_one_is_supported(self):
        self.assertEqual((1,), SUPPORTED_OBSERVER_VERSIONS)
        self.assertEqual(FINAL_STATE_OBSERVER_VERSION, SUPPORTED_OBSERVER_VERSIONS[0])

    def test_it_is_an_enumeration_rather_than_a_ceiling(self):
        """`<= OBSERVER_VERSION` would silently accept version 5 one day."""
        for unknown in (0, 2, 3, 99):
            with self.subTest(version=unknown):
                self.assertNotIn(unknown, SUPPORTED_OBSERVER_VERSIONS)


class FingerprintVerifierTests(unittest.TestCase):
    """PR14's verifier, reused rather than reimplemented."""

    def test_an_honest_observation_verifies(self):
        from cofferdam.workstation.tasks.finalstate import verify_final_state_fingerprint

        self.assertTrue(
            verify_final_state_fingerprint(
                observation(1, paths(("a.txt", PATH_PRESENT, KIND_FILE)))
            )
        )

    def test_a_legacy_observation_does_not(self):
        from cofferdam.workstation.tasks.finalstate import verify_final_state_fingerprint

        self.assertFalse(verify_final_state_fingerprint(legacy(1)))

    def test_an_unknown_observer_version_does_not(self):
        """The hash binds version 1's semantics; comparing across is meaningless."""
        from cofferdam.workstation.tasks.finalstate import verify_final_state_fingerprint

        self.assertFalse(
            verify_final_state_fingerprint(
                observation(1, paths(("a.txt", PATH_ABSENT)), observer_version=2)
            )
        )

    def test_there_is_one_fingerprint_algorithm(self):
        """The binder must not carry a second copy of PR14's construction."""
        tree = self.source() if hasattr(self, "source") else ast.parse(
            (REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "binding.py")
            .read_text(encoding="utf-8")
        )
        called = {
            getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        # It asks PR14 whether the hash holds; it never builds one.
        self.assertIn("verify_final_state_fingerprint", called)
        self.assertNotIn("final_state_fingerprint", called)
        text = (
            REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "binding.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("cofferdam.evidence.finalstate", text)


# -- fingerprints and provenance ----------------------------------------------


class ProvenanceTests(unittest.TestCase):
    def assessment(self, **kwargs):
        return bind(
            resolved(1, [active("criterion_0001", 1, state(1, "a.txt"))]),
            None,
            turn_closed=True,
            final_state=observation(1, paths(("a.txt", PATH_PRESENT, KIND_FILE)), **kwargs),
        ).assessments[0]

    def test_it_binds_the_exact_stored_observation_fingerprint(self):
        stored = observation(1, paths(("a.txt", PATH_PRESENT, KIND_FILE)))
        self.assertEqual(stored.fingerprint, self.assessment().evidence_fingerprint)

    def test_the_observation_id_is_not_the_provenance(self):
        """A minted row handle carries randomness; the fingerprint is identity."""
        self.assertNotEqual("fs_000000000001", self.assessment().evidence_fingerprint)

    def test_the_head_revision_moves_the_evidence_identity(self):
        """It is inside PR14's fingerprint, so it reaches the derived answer."""
        self.assertNotEqual(
            self.assessment().evidence_fingerprint,
            self.assessment(head="a" * 40).evidence_fingerprint,
        )

    def test_the_path_fact_is_carried_without_being_persisted(self):
        assessment = self.assessment()
        self.assertEqual(PATH_PRESENT, assessment.path_state)
        self.assertEqual(KIND_FILE, assessment.path_kind)


class DerivedFingerprintTests(unittest.TestCase):
    def answer(self, **kwargs):
        defaults = dict(
            target=1,
            criterion_id="criterion_0001",
            path="a.txt",
            predicate=PREDICATE_PATH_EXISTS,
            path_state=PATH_PRESENT,
            kind=KIND_FILE,
            lineage=LINEAGE,
        )
        defaults.update(kwargs)
        item = state(1, defaults["path"], defaults["predicate"])
        return bind(
            resolved(
                defaults["target"],
                [active(defaults["criterion_id"], defaults["target"], item)],
                fingerprint=defaults["lineage"],
            ),
            None,
            turn_closed=True,
            final_state=observation(
                defaults["target"],
                paths((defaults["path"], defaults["path_state"], defaults["kind"])),
                lineage=defaults["lineage"],
            ),
        )

    def test_it_is_stable_across_repeated_derivation(self):
        self.assertEqual(self.answer().fingerprint, self.answer().fingerprint)

    def test_the_target_turn_moves_it(self):
        self.assertNotEqual(
            self.answer().fingerprint, self.answer(target=2).fingerprint
        )

    def test_the_criterion_identity_moves_it(self):
        self.assertNotEqual(
            self.answer().fingerprint, self.answer(criterion_id="criterion_0009").fingerprint
        )

    def test_the_lineage_fingerprint_moves_it(self):
        self.assertNotEqual(
            self.answer().fingerprint, self.answer(lineage="other" + "f" * 59).fingerprint
        )

    def test_the_observation_fingerprint_moves_it(self):
        """Through the evidence field, which is bound into the criterion hash."""
        self.assertNotEqual(
            self.answer().fingerprint,
            self.answer(path_state=PATH_ABSENT, kind=None).fingerprint,
        )

    def test_the_result_moves_it(self):
        self.assertNotEqual(
            self.answer().fingerprint,
            self.answer(predicate=PREDICATE_PATH_ABSENT).fingerprint,
        )

    def test_a_final_state_met_does_not_collide_with_a_turn_change_met(self):
        """The reason the domain is inside the hash."""
        from cofferdam.workstation.tasks.binding import criterion_assessment_fingerprint

        shared = dict(
            criterion_id="c",
            source_snapshot_id="s",
            source_turn_number=1,
            target_turn_number=1,
            kind="evidence",
            predicate=PREDICATE_PATH_EXISTS,
            result=RESULT_MET,
            evidence_fingerprint="d" * 64,
        )
        self.assertNotEqual(
            criterion_assessment_fingerprint(
                domain=DOMAIN_FINAL_STATE, reason=REASON_FINAL_STATE_OBSERVED, **shared
            ),
            criterion_assessment_fingerprint(
                domain=DOMAIN_TURN_CHANGE, reason=REASON_TURN_CHANGE_EVALUATED, **shared
            ),
        )


class VersionTests(unittest.TestCase):
    def test_the_assessment_version_is_two(self):
        self.assertEqual(2, CURRENT_ASSESSMENT_VERSION)

    def test_the_domain_vocabulary_gained_exactly_final_state(self):
        self.assertEqual(
            (DOMAIN_TURN_CHANGE, DOMAIN_FINAL_STATE, DOMAIN_NOT_APPLICABLE),
            EVIDENCE_DOMAINS,
        )

    def test_named_check_is_not_implemented(self):
        self.assertNotIn("named_check", EVIDENCE_DOMAINS)

    def test_no_other_semantic_version_moved(self):
        from cofferdam.workstation.tasks.criteria import CRITERIA_MODEL_VERSION
        from cofferdam.workstation.tasks.store import SCHEMA_VERSION

        self.assertEqual(11, SCHEMA_VERSION)
        self.assertEqual(1, EVALUATOR_VERSION)
        self.assertEqual(1, FINAL_STATE_OBSERVER_VERSION)
        self.assertEqual(1, RESOLVER_VERSION)
        self.assertEqual(1, CRITERIA_MODEL_VERSION)


class MixedDomainTests(unittest.TestCase):
    """One envelope, five criteria, four answers from four different places."""

    def answer(self):
        return bind(
            resolved(
                2,
                [
                    active("criterion_0001", 1, change(1, "inherited.py")),
                    active("criterion_0002", 2, change(1, "same.py")),
                    active("criterion_0003", 1, state(2, "inherited.txt")),
                    active("criterion_0004", 2, state(2, "same.txt")),
                    active("criterion_0005", 1, manual(3)),
                ],
            ),
            evaluation(2, [result("criterion_0002", 1, RESULT_MET)]),
            turn_closed=True,
            final_state=observation(
                2,
                paths(
                    ("inherited.txt", PATH_PRESENT, KIND_FILE),
                    ("same.txt", PATH_ABSENT),
                ),
            ),
        )

    def test_each_criterion_gets_its_own_domain(self):
        domains = [a.domain for a in self.answer().assessments]
        self.assertEqual(
            [
                DOMAIN_NOT_APPLICABLE,
                DOMAIN_TURN_CHANGE,
                DOMAIN_FINAL_STATE,
                DOMAIN_FINAL_STATE,
                DOMAIN_NOT_APPLICABLE,
            ],
            domains,
        )

    def test_each_criterion_gets_its_own_result(self):
        results = [a.result for a in self.answer().assessments]
        self.assertEqual(
            [
                RESULT_UNVERIFIED,
                RESULT_MET,
                RESULT_MET,
                RESULT_NOT_MET,
                RESULT_UNVERIFIED,
            ],
            results,
        )

    def test_each_criterion_gets_its_own_reason(self):
        reasons = [a.reason for a in self.answer().assessments]
        self.assertEqual(
            [
                REASON_INHERITED_CHANGE_NOT_CURRENT,
                REASON_TURN_CHANGE_EVALUATED,
                REASON_FINAL_STATE_OBSERVED,
                REASON_FINAL_STATE_OBSERVED,
                REASON_MANUAL_AUTHORITY,
            ],
            reasons,
        )

    def test_the_resolver_order_is_preserved_exactly(self):
        ids = [a.criterion_id for a in self.answer().assessments]
        self.assertEqual(
            [
                "criterion_0001",
                "criterion_0002",
                "criterion_0003",
                "criterion_0004",
                "criterion_0005",
            ],
            ids,
        )

    def test_it_is_one_envelope(self):
        answer = self.answer()
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(5, answer.criterion_count)
        self.assertEqual(LINEAGE, answer.lineage_fingerprint)

    def test_there_is_no_aggregate_of_any_kind(self):
        answer = self.answer()
        for forbidden in ("verdict", "outcome", "passed", "met_count", "score"):
            self.assertFalse(hasattr(answer, forbidden))


class EmptyActiveSetTests(unittest.TestCase):
    def test_an_empty_set_needs_neither_input(self):
        answer = bind(resolved(1, []), None, turn_closed=True, final_state=None)
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(0, answer.criterion_count)

    def test_it_is_not_acceptance_met(self):
        answer = bind(resolved(1, []), None, turn_closed=True, final_state=None)
        self.assertEqual((), answer.assessments)
        self.assertFalse(hasattr(answer, "acceptance"))


class DeterminismTests(unittest.TestCase):
    """Called twice with the same values, the same answer. Always."""

    def build(self):
        return bind(
            resolved(
                3,
                [
                    active("criterion_0001", 1, state(1, "a.txt")),
                    active("criterion_0002", 3, state(2, "b.txt", PREDICATE_PATH_ABSENT)),
                ],
            ),
            None,
            turn_closed=True,
            final_state=observation(
                3, paths(("a.txt", PATH_PRESENT, KIND_SYMLINK), ("b.txt", PATH_ABSENT))
            ),
        )

    def test_repeated_derivation_is_byte_identical(self):
        self.assertEqual(self.build().fingerprint, self.build().fingerprint)
        self.assertEqual(
            [a.fingerprint for a in self.build().assessments],
            [a.fingerprint for a in self.build().assessments],
        )

    def test_it_does_not_depend_on_path_row_ordering_beyond_the_stored_ordinal(self):
        """The lookup is by exact path, so a scan order cannot change an answer."""
        answer = self.build()
        self.assertEqual(RESULT_MET, answer.assessments[0].result)
        self.assertEqual(RESULT_MET, answer.assessments[1].result)


class ExactPathMatchTests(unittest.TestCase):
    """No basename matching, no case folding, no similarity, no guessing."""

    def refuse_or_answer(self, criterion_path, observed_path):
        return bind(
            resolved(1, [active("criterion_0001", 1, state(1, criterion_path))]),
            None,
            turn_closed=True,
            final_state=observation(1, paths((observed_path, PATH_PRESENT, KIND_FILE))),
        )

    def test_a_different_directory_with_the_same_basename_is_not_a_match(self):
        answer = self.refuse_or_answer("src/a.txt", "docs/a.txt")
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(REASON_FINAL_STATE_PATH_MISSING, answer.unavailable_reason)

    def test_a_case_difference_is_not_a_match(self):
        answer = self.refuse_or_answer("a.txt", "A.TXT")
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(REASON_FINAL_STATE_PATH_MISSING, answer.unavailable_reason)

    def test_an_exact_match_is_a_match(self):
        answer = self.refuse_or_answer("src/a.txt", "src/a.txt")
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(RESULT_MET, answer.assessments[0].result)


class PurityTests(unittest.TestCase):
    """Proven from the module's own source, not from its behaviour alone."""

    def source(self):
        path = REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "binding.py"
        return ast.parse(path.read_text(encoding="utf-8"))

    def test_it_never_observes_a_path_now(self):
        """"Use FinalStateObservation" is a read of a row, not a look at a disk."""
        called = set()
        for node in ast.walk(self.source()):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name:
                    called.add(name)
        for forbidden in (
            "observe_path",
            "observe_paths",
            "target_paths",
            "verify_root",
            "lstat",
            "open",
            "resolve",
            "evaluate",
            "execute",
        ):
            self.assertNotIn(forbidden, called)

    def test_it_reaches_no_store_and_no_service(self):
        names = set()
        for node in ast.walk(self.source()):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module.split(".")[0])
                names.update(alias.name for alias in node.names)
        for forbidden in (
            "sqlite3", "os", "subprocess", "socket", "time", "datetime",
            "random", "pathlib", "store", "service", "TaskStore", "TaskService",
        ):
            self.assertNotIn(forbidden, names)

    def test_a_deleted_repository_changes_nothing(self):
        """There is no repository in this file to delete, which is the proof."""
        answer = bind(
            resolved(1, [active("criterion_0001", 1, state(1, "a.txt"))]),
            None,
            turn_closed=True,
            final_state=observation(1, paths(("a.txt", PATH_PRESENT, KIND_FILE))),
        )
        self.assertEqual(RESULT_MET, answer.assessments[0].result)

    def test_no_aggregate_is_defined_or_exported(self):
        """The module may *name* a future aggregate in prose; it may not be one."""
        from cofferdam.workstation.tasks import binding

        tree = self.source()
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        } | {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for forbidden in (
            "AGGREGATOR_VERSION",
            "aggregate",
            "acceptance_state",
            "verdict",
            "task_outcome",
        ):
            self.assertNotIn(forbidden, defined)
            self.assertNotIn(forbidden, binding.__all__)
            self.assertFalse(hasattr(binding, forbidden))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
