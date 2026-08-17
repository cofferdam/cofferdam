"""M2K PR21 — the acceptance fold, on constructed envelopes.

Everything here builds `CurrentAssessment` values directly, because the
aggregate's whole claim is that it is a deterministic function of one immutable
envelope. If a test needed a store or a filesystem to exercise it, that claim
would already be false.

Three groups are load-bearing:

* `KnownZeroVersusUnknownPopulation` — the red line. A resolved empty set and an
  unavailable envelope are both `not_assessable`, and everything else about them
  differs: counts, `requires_human`, and identity. Reporting the second as four
  zeros would state an observation nobody made;
* `NotMetDominance` — one demonstrably unmet criterion settles the turn however
  much else is unverified, across every combination and both evidence domains;
* `CompositionalIdentity` — two envelopes that fold to identical visible answers
  from different evidence must not share an aggregate fingerprint.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.acceptance import (
    AGGREGATE_REASONS,
    AGGREGATOR_VERSION,
    AVAILABILITIES,
    AVAILABILITY_ASSESSABLE,
    AVAILABILITY_NOT_ASSESSABLE,
    AVAILABILITY_REASONS,
    OUTCOMES,
    OUTCOME_INCOMPLETE,
    OUTCOME_MET,
    OUTCOME_NOT_MET,
    REASON_ASSESSMENT_INPUT_INVALID,
    REASON_NO_STRUCTURED_CRITERIA,
    REASON_UNSUPPORTED_ASSESSMENT_VERSION,
    SUPPORTED_ASSESSMENT_VERSIONS,
    AcceptanceAggregate,
    CriterionCounts,
    acceptance_fingerprint,
    aggregate,
)
from cofferdam.workstation.tasks.binding import (
    ASSESSMENT_RESOLVED,
    ASSESSMENT_UNAVAILABLE,
    CURRENT_ASSESSMENT_VERSION,
    DOMAIN_FINAL_STATE,
    DOMAIN_NOT_APPLICABLE,
    DOMAIN_TURN_CHANGE,
    LINEAGE_REASONS,
    SET_REASONS,
    CriterionAssessment,
    CurrentAssessment,
)
from cofferdam.workstation.tasks.evaluation import (
    RESULT_MET,
    RESULT_NOT_MET,
    RESULT_UNVERIFIED,
)
from cofferdam.workstation.tasks.lineage import (
    REASON_LEGACY_UNKNOWN,
    REASON_NOT_DECLARED,
    REASON_PREDECESSOR_UNAVAILABLE,
)

TASK = "task_01aaaaaaaaaaaaaaaaaaaaaaaa"
REPO_ROOT = Path(__file__).resolve().parents[1]


# -- builders -----------------------------------------------------------------


def criterion(
    identity,
    result,
    *,
    kind="evidence",
    predicate="path_changed",
    domain=DOMAIN_TURN_CHANGE,
    reason="turn_change_evaluated",
    source_turn=1,
    target_turn=1,
):
    return CriterionAssessment(
        criterion_id=identity,
        source_snapshot_id="snapshot_0001",
        source_turn_number=source_turn,
        target_turn_number=target_turn,
        kind=kind,
        predicate=None if kind == "manual" else predicate,
        domain=domain,
        result=result,
        reason=reason,
        fingerprint="c" * 64,
    )


def manual(identity, result=RESULT_UNVERIFIED, **kwargs):
    return criterion(
        identity,
        result,
        kind="manual",
        domain=DOMAIN_NOT_APPLICABLE,
        reason="manual_criterion_no_machine_authority",
        **kwargs,
    )


def state_criterion(identity, result, **kwargs):
    return criterion(
        identity,
        result,
        predicate="path_exists",
        domain=DOMAIN_FINAL_STATE,
        reason="final_state_observed",
        **kwargs,
    )


def inherited_change(identity, **kwargs):
    return criterion(
        identity,
        RESULT_UNVERIFIED,
        domain=DOMAIN_NOT_APPLICABLE,
        reason="inherited_change_not_current_state_evaluable",
        source_turn=1,
        target_turn=2,
        **kwargs,
    )


def resolved(items, *, target=1, fingerprint="e" * 64, version=CURRENT_ASSESSMENT_VERSION):
    return CurrentAssessment(
        task_id=TASK,
        target_turn_number=target,
        assessment_version=version,
        state=ASSESSMENT_RESOLVED,
        lineage_fingerprint="l" * 64,
        assessments=tuple(items),
        fingerprint=fingerprint,
    )


def unavailable(
    reason, *, cause=None, at_turn=None, target=1, fingerprint="u" * 64,
    version=CURRENT_ASSESSMENT_VERSION, items=(),
):
    return CurrentAssessment(
        task_id=TASK,
        target_turn_number=target,
        assessment_version=version,
        state=ASSESSMENT_UNAVAILABLE,
        unavailable_reason=reason,
        unavailable_cause=cause,
        unavailable_at_turn_number=at_turn,
        assessments=tuple(items),
        fingerprint=fingerprint,
    )


# -- vocabulary ---------------------------------------------------------------


class VocabularyTests(unittest.TestCase):
    def test_the_aggregator_version_is_one(self):
        self.assertEqual(1, AGGREGATOR_VERSION)

    def test_it_supports_exactly_assessment_version_three(self):
        self.assertEqual((4,), SUPPORTED_ASSESSMENT_VERSIONS)
        self.assertEqual((CURRENT_ASSESSMENT_VERSION,), SUPPORTED_ASSESSMENT_VERSIONS)

    def test_availability_has_two_values(self):
        self.assertEqual(("assessable", "not_assessable"), AVAILABILITIES)

    def test_the_outcome_vocabulary_is_closed_and_borrows_two_result_words(self):
        self.assertEqual(("met", "not_met", "incomplete"), OUTCOMES)
        for forbidden in ("success", "failed", "passed", "pass", "fail"):
            self.assertNotIn(forbidden, OUTCOMES)

    def test_the_reason_vocabulary_is_this_layers_three_plus_the_envelopes(self):
        """No translation table: the envelope's twenty-seven arrive verbatim."""
        self.assertEqual(AGGREGATE_REASONS + SET_REASONS, AVAILABILITY_REASONS)
        self.assertEqual(3, len(AGGREGATE_REASONS))
        self.assertEqual(30, len(AVAILABILITY_REASONS))
        self.assertEqual(len(set(AVAILABILITY_REASONS)), len(AVAILABILITY_REASONS))

    def test_the_three_owned_reasons_describe_this_layers_own_failures(self):
        self.assertEqual(
            (
                REASON_NO_STRUCTURED_CRITERIA,
                REASON_ASSESSMENT_INPUT_INVALID,
                REASON_UNSUPPORTED_ASSESSMENT_VERSION,
            ),
            AGGREGATE_REASONS,
        )
        for owned in AGGREGATE_REASONS:
            self.assertNotIn(owned, SET_REASONS)


# -- the fold -----------------------------------------------------------------


class AllMet(unittest.TestCase):
    def test_a_nonempty_all_met_set_is_met(self):
        answer = aggregate(resolved([criterion("c1", RESULT_MET),
                                     criterion("c2", RESULT_MET)]))
        self.assertEqual(AVAILABILITY_ASSESSABLE, answer.availability)
        self.assertIsNone(answer.availability_reason)
        self.assertEqual(OUTCOME_MET, answer.outcome)
        self.assertEqual(CriterionCounts(2, 2, 0, 0), answer.counts)

    def test_a_single_met_criterion_is_met(self):
        self.assertEqual(OUTCOME_MET, aggregate(resolved([criterion("c1", RESULT_MET)])).outcome)

    def test_met_is_never_reached_vacuously(self):
        """An empty set does not reach the outcome dimension at all."""
        self.assertIsNone(aggregate(resolved([])).outcome)


class NotMetDominance(unittest.TestCase):
    """One demonstrably unmet criterion settles the turn. Every combination."""

    def outcome(self, *results):
        items = [criterion("c%d" % n, r) for n, r in enumerate(results, start=1)]
        return aggregate(resolved(items)).outcome

    def test_not_met_alone(self):
        self.assertEqual(OUTCOME_NOT_MET, self.outcome(RESULT_NOT_MET))

    def test_not_met_with_met(self):
        self.assertEqual(OUTCOME_NOT_MET, self.outcome(RESULT_NOT_MET, RESULT_MET))
        self.assertEqual(OUTCOME_NOT_MET, self.outcome(RESULT_MET, RESULT_NOT_MET))

    def test_not_met_with_unverified(self):
        self.assertEqual(OUTCOME_NOT_MET, self.outcome(RESULT_NOT_MET, RESULT_UNVERIFIED))
        self.assertEqual(OUTCOME_NOT_MET, self.outcome(RESULT_UNVERIFIED, RESULT_NOT_MET))

    def test_all_three_together(self):
        self.assertEqual(
            OUTCOME_NOT_MET,
            self.outcome(RESULT_MET, RESULT_NOT_MET, RESULT_UNVERIFIED),
        )

    def test_order_never_matters(self):
        """The fold counts; it does not scan for a first hit."""
        self.assertEqual(
            {OUTCOME_NOT_MET},
            {
                self.outcome(*order)
                for order in (
                    (RESULT_NOT_MET, RESULT_MET, RESULT_UNVERIFIED),
                    (RESULT_UNVERIFIED, RESULT_MET, RESULT_NOT_MET),
                    (RESULT_MET, RESULT_UNVERIFIED, RESULT_NOT_MET),
                )
            },
        )

    def test_uncertainty_never_erases_a_known_failure(self):
        """Nine unverified do not soften one not_met."""
        items = [criterion("c%d" % n, RESULT_UNVERIFIED) for n in range(9)]
        items.append(criterion("cx", RESULT_NOT_MET))
        self.assertEqual(OUTCOME_NOT_MET, aggregate(resolved(items)).outcome)


class Incomplete(unittest.TestCase):
    def test_unverified_only(self):
        answer = aggregate(resolved([criterion("c1", RESULT_UNVERIFIED)]))
        self.assertEqual(OUTCOME_INCOMPLETE, answer.outcome)
        self.assertEqual(AVAILABILITY_ASSESSABLE, answer.availability)

    def test_met_plus_unverified(self):
        answer = aggregate(resolved([criterion("c1", RESULT_MET),
                                     criterion("c2", RESULT_UNVERIFIED)]))
        self.assertEqual(OUTCOME_INCOMPLETE, answer.outcome)
        self.assertEqual(CriterionCounts(2, 1, 0, 1), answer.counts)

    def test_it_is_never_not_met(self):
        """An evidence limitation is not a finding about the work."""
        self.assertNotEqual(
            OUTCOME_NOT_MET,
            aggregate(resolved([criterion("c1", RESULT_UNVERIFIED)])).outcome,
        )


class InheritedChange(unittest.TestCase):
    """It folds like any other `unverified`, and needs no person."""

    def test_it_contributes_as_unverified(self):
        answer = aggregate(resolved([state_criterion("c1", RESULT_MET),
                                     inherited_change("c2")]))
        self.assertEqual(OUTCOME_INCOMPLETE, answer.outcome)

    def test_it_does_not_ask_for_a_human(self):
        """Nobody can resolve it, so sending somebody to look would be wrong."""
        answer = aggregate(resolved([inherited_change("c1")]))
        self.assertIs(False, answer.requires_human)

    def test_its_reason_is_not_special_cased(self):
        """Same result, same fold, whatever the criterion reason says."""
        plain = aggregate(resolved([criterion("c1", RESULT_UNVERIFIED)]))
        inherited = aggregate(resolved([inherited_change("c1")]))
        self.assertEqual(plain.outcome, inherited.outcome)
        self.assertEqual(plain.counts, inherited.counts)


# -- the red line -------------------------------------------------------------


class KnownZeroVersusUnknownPopulation(unittest.TestCase):
    """Both `not_assessable`. Nothing else about them is the same."""

    def zero(self):
        return aggregate(resolved([]))

    def unknown(self):
        return aggregate(unavailable(REASON_NOT_DECLARED, at_turn=1))

    def test_known_zero_is_not_assessable_for_no_structured_criteria(self):
        answer = self.zero()
        self.assertEqual(AVAILABILITY_NOT_ASSESSABLE, answer.availability)
        self.assertEqual(REASON_NO_STRUCTURED_CRITERIA, answer.availability_reason)
        self.assertIsNone(answer.outcome)

    def test_known_zero_has_genuinely_zero_counts(self):
        self.assertEqual(CriterionCounts(0, 0, 0, 0), self.zero().counts)
        self.assertTrue(self.zero().population_known)

    def test_known_zero_needs_no_human(self):
        self.assertIs(False, self.zero().requires_human)

    def test_an_unknown_population_has_no_counts_at_all(self):
        """Not four zeros — an observation nobody made."""
        self.assertIsNone(self.unknown().counts)
        self.assertFalse(self.unknown().population_known)

    def test_an_unknown_population_does_not_claim_no_human_is_needed(self):
        self.assertIsNone(self.unknown().requires_human)
        self.assertIsNot(False, self.unknown().requires_human)

    def test_the_two_are_not_the_same_semantic_state(self):
        zero, unknown = self.zero(), self.unknown()
        self.assertEqual(zero.availability, unknown.availability)
        self.assertNotEqual(zero.availability_reason, unknown.availability_reason)
        self.assertNotEqual(zero.counts, unknown.counts)
        self.assertNotEqual(zero.requires_human, unknown.requires_human)
        self.assertNotEqual(zero.fingerprint, unknown.fingerprint)

    def test_zero_counts_and_absent_counts_hash_differently(self):
        """Pinned at the fingerprint, not only on the dataclass."""
        base = dict(
            task_id=TASK, target_turn_number=1, assessment_fingerprint="f" * 64,
            availability=AVAILABILITY_NOT_ASSESSABLE,
            availability_reason=REASON_NO_STRUCTURED_CRITERIA,
            unavailable_cause=None, unavailable_at_turn_number=None, outcome=None,
            requires_human=False,
        )
        self.assertNotEqual(
            acceptance_fingerprint(counts=CriterionCounts(0, 0, 0, 0), **base),
            acceptance_fingerprint(counts=None, **base),
        )

    def test_neither_is_ever_met(self):
        for answer in (self.zero(), self.unknown()):
            self.assertIsNone(answer.outcome)
            self.assertNotEqual(OUTCOME_MET, answer.outcome)


class RequiresHumanTriState(unittest.TestCase):
    """Derived from criterion kind, never from uncertainty, and never guessed."""

    def test_a_manual_criterion_asks_for_a_person(self):
        self.assertIs(True, aggregate(resolved([manual("m1")])).requires_human)

    def test_manual_alone_is_incomplete(self):
        answer = aggregate(resolved([manual("m1")]))
        self.assertEqual(OUTCOME_INCOMPLETE, answer.outcome)
        self.assertIs(True, answer.requires_human)

    def test_manual_plus_met_is_incomplete(self):
        answer = aggregate(resolved([criterion("c1", RESULT_MET), manual("m1")]))
        self.assertEqual(OUTCOME_INCOMPLETE, answer.outcome)
        self.assertIs(True, answer.requires_human)

    def test_manual_plus_not_met_is_not_met_and_still_wants_a_person(self):
        """Orthogonal: the outcome is decided, and somebody is still needed."""
        answer = aggregate(resolved([criterion("c1", RESULT_NOT_MET), manual("m1")]))
        self.assertEqual(OUTCOME_NOT_MET, answer.outcome)
        self.assertIs(True, answer.requires_human)

    def test_it_is_not_derived_from_unverified(self):
        answer = aggregate(resolved([criterion("c1", RESULT_UNVERIFIED)]))
        self.assertEqual(OUTCOME_INCOMPLETE, answer.outcome)
        self.assertIs(False, answer.requires_human)

    def test_it_is_never_a_fourth_outcome(self):
        for forbidden in ("requires_human", "needs_human", "human"):
            self.assertNotIn(forbidden, OUTCOMES)

    def test_none_false_and_true_are_three_distinct_answers(self):
        base = dict(
            task_id=TASK, target_turn_number=1, assessment_fingerprint="f" * 64,
            availability=AVAILABILITY_NOT_ASSESSABLE, availability_reason=None,
            unavailable_cause=None, unavailable_at_turn_number=None, outcome=None,
            counts=None,
        )
        prints = {
            acceptance_fingerprint(requires_human=value, **base)
            for value in (None, False, True)
        }
        self.assertEqual(3, len(prints))


# -- unavailable envelopes ----------------------------------------------------


class SetUnavailable(unittest.TestCase):
    def test_every_envelope_reason_is_preserved_verbatim(self):
        for reason in SET_REASONS:
            with self.subTest(reason=reason):
                answer = aggregate(unavailable(reason))
                self.assertEqual(AVAILABILITY_NOT_ASSESSABLE, answer.availability)
                self.assertEqual(reason, answer.availability_reason)

    def test_none_of_them_produces_an_outcome(self):
        for reason in SET_REASONS:
            with self.subTest(reason=reason):
                self.assertIsNone(aggregate(unavailable(reason)).outcome)

    def test_none_of_them_is_incomplete(self):
        """The mistake this dimension exists to prevent."""
        for reason in SET_REASONS:
            with self.subTest(reason=reason):
                self.assertNotEqual(
                    OUTCOME_INCOMPLETE, aggregate(unavailable(reason)).outcome
                )

    def test_none_of_them_claims_counts(self):
        for reason in SET_REASONS:
            with self.subTest(reason=reason):
                answer = aggregate(unavailable(reason))
                self.assertIsNone(answer.counts)
                self.assertIsNone(answer.requires_human)

    def test_every_reason_is_in_the_closed_vocabulary(self):
        for reason in SET_REASONS:
            self.assertIn(reason, AVAILABILITY_REASONS)


class DirectLineageReasons(unittest.TestCase):
    def test_not_declared_and_legacy_unknown_stay_apart(self):
        undeclared = aggregate(unavailable(REASON_NOT_DECLARED, at_turn=1))
        legacy = aggregate(unavailable(REASON_LEGACY_UNKNOWN, at_turn=1))
        self.assertEqual(REASON_NOT_DECLARED, undeclared.availability_reason)
        self.assertEqual(REASON_LEGACY_UNKNOWN, legacy.availability_reason)
        self.assertNotEqual(undeclared.availability_reason, legacy.availability_reason)

    def test_they_are_not_flattened_back_into_one(self):
        """PR20 stopped this happening one layer down; it must not restart here."""
        self.assertNotEqual("lineage_unavailable",
                            aggregate(unavailable(REASON_NOT_DECLARED)).availability_reason)


class NestedCauseFidelity(unittest.TestCase):
    """PR20's cause survives the fold, or PR20's fix would end here."""

    def nested(self, cause, at_turn=2):
        return aggregate(
            unavailable(REASON_PREDECESSOR_UNAVAILABLE, cause=cause, at_turn=at_turn)
        )

    def test_the_cause_is_carried(self):
        self.assertEqual(REASON_NOT_DECLARED, self.nested(REASON_NOT_DECLARED).unavailable_cause)
        self.assertEqual(REASON_LEGACY_UNKNOWN, self.nested(REASON_LEGACY_UNKNOWN).unavailable_cause)

    def test_the_turn_is_carried(self):
        self.assertEqual(2, self.nested(REASON_NOT_DECLARED, at_turn=2).unavailable_at_turn_number)
        self.assertEqual(7, self.nested(REASON_NOT_DECLARED, at_turn=7).unavailable_at_turn_number)

    def test_the_two_nested_causes_are_different_facts(self):
        undeclared = self.nested(REASON_NOT_DECLARED)
        legacy = self.nested(REASON_LEGACY_UNKNOWN)
        self.assertEqual(undeclared.availability_reason, legacy.availability_reason)
        self.assertNotEqual(undeclared.unavailable_cause, legacy.unavailable_cause)
        self.assertNotEqual(undeclared.fingerprint, legacy.fingerprint)

    def test_nothing_is_translated(self):
        self.assertIn(self.nested(REASON_NOT_DECLARED).unavailable_cause, LINEAGE_REASONS)

    def test_no_structured_criteria_carries_no_cause(self):
        answer = aggregate(resolved([]))
        self.assertIsNone(answer.unavailable_cause)
        self.assertIsNone(answer.unavailable_at_turn_number)


class OperationalAndStructural(unittest.TestCase):
    """Different families, same refusal, and none of them an outcome."""

    OPERATIONAL = ("turn_not_closed", "evaluation_not_recorded")
    STRUCTURAL = (
        "evaluation_inconsistent",
        "unsupported_evaluator_version",
        "final_state_inconsistent",
        "unsupported_final_state_observer_version",
        "final_state_lineage_mismatch",
        "final_state_path_missing",
        "malformed_lineage",
        "cycle_detected",
    )

    def test_operational_reasons_are_not_assessable(self):
        for reason in self.OPERATIONAL:
            with self.subTest(reason=reason):
                answer = aggregate(unavailable(reason))
                self.assertEqual(AVAILABILITY_NOT_ASSESSABLE, answer.availability)
                self.assertEqual(reason, answer.availability_reason)
                self.assertIsNone(answer.outcome)

    def test_structural_reasons_are_not_assessable(self):
        for reason in self.STRUCTURAL:
            with self.subTest(reason=reason):
                answer = aggregate(unavailable(reason))
                self.assertEqual(reason, answer.availability_reason)
                self.assertIsNone(answer.outcome)

    def test_a_pipeline_lag_is_never_reported_as_a_finding_about_the_work(self):
        for reason in self.OPERATIONAL + self.STRUCTURAL:
            with self.subTest(reason=reason):
                answer = aggregate(unavailable(reason))
                self.assertNotIn(answer.outcome, (OUTCOME_INCOMPLETE, OUTCOME_NOT_MET))

    def test_every_family_keeps_its_own_identity(self):
        prints = {aggregate(unavailable(r)).fingerprint
                  for r in self.OPERATIONAL + self.STRUCTURAL}
        self.assertEqual(len(self.OPERATIONAL) + len(self.STRUCTURAL), len(prints))


# -- domain agnosticism -------------------------------------------------------


class DomainAgnostic(unittest.TestCase):
    """The fold reads `result`. It has never heard of an evidence domain."""

    def test_two_domains_both_met_is_met(self):
        answer = aggregate(resolved([criterion("c1", RESULT_MET),
                                     state_criterion("c2", RESULT_MET)]))
        self.assertEqual(OUTCOME_MET, answer.outcome)

    def test_a_final_state_not_met_beats_a_turn_change_unverified(self):
        answer = aggregate(resolved([state_criterion("c1", RESULT_NOT_MET),
                                     criterion("c2", RESULT_UNVERIFIED)]))
        self.assertEqual(OUTCOME_NOT_MET, answer.outcome)

    def test_a_turn_change_not_met_beats_a_final_state_met(self):
        answer = aggregate(resolved([criterion("c1", RESULT_NOT_MET),
                                     state_criterion("c2", RESULT_MET)]))
        self.assertEqual(OUTCOME_NOT_MET, answer.outcome)

    def test_final_state_met_plus_inherited_change_unverified_is_incomplete(self):
        answer = aggregate(resolved([state_criterion("c1", RESULT_MET),
                                     inherited_change("c2")]))
        self.assertEqual(OUTCOME_INCOMPLETE, answer.outcome)

    def test_swapping_domains_changes_no_visible_answer(self):
        """Same results and kinds, different domains: identical fold."""
        one = aggregate(resolved([criterion("c1", RESULT_MET),
                                  criterion("c2", RESULT_UNVERIFIED)]))
        two = aggregate(resolved([state_criterion("c1", RESULT_MET),
                                  state_criterion("c2", RESULT_UNVERIFIED)]))
        self.assertEqual(one.outcome, two.outcome)
        self.assertEqual(one.counts, two.counts)
        self.assertEqual(one.requires_human, two.requires_human)
        self.assertEqual(one.availability, two.availability)

    def test_the_module_never_names_a_domain_in_its_logic(self):
        tree = ast.parse(
            (REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "acceptance.py")
            .read_text(encoding="utf-8")
        )
        referenced = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        for forbidden in ("domain", "DOMAIN_FINAL_STATE", "DOMAIN_TURN_CHANGE",
                          "predicate", "evidence_fingerprint", "path_state"):
            self.assertNotIn(forbidden, referenced)


class CriterionReasonsAreNotInputs(unittest.TestCase):
    def test_the_module_never_reads_a_criterion_reason(self):
        tree = ast.parse(
            (REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "acceptance.py")
            .read_text(encoding="utf-8")
        )
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        # `unavailable_reason` is a set-level field and is legitimately read;
        # the per-criterion `reason` is not.
        self.assertNotIn("reason", attributes)

    def test_the_same_results_fold_the_same_whatever_the_reasons(self):
        a = aggregate(resolved([criterion("c1", RESULT_UNVERIFIED, reason="turn_change_evaluated")]))
        b = aggregate(resolved([criterion("c1", RESULT_UNVERIFIED, reason="final_state_not_recorded")]))
        self.assertEqual(a.outcome, b.outcome)
        self.assertEqual(a.counts, b.counts)


# -- input contract -----------------------------------------------------------


class UnsupportedInputVersion(unittest.TestCase):
    def test_a_newer_envelope_is_refused(self):
        answer = aggregate(resolved([criterion("c1", RESULT_MET)], version=5))
        self.assertEqual(AVAILABILITY_NOT_ASSESSABLE, answer.availability)
        self.assertEqual(REASON_UNSUPPORTED_ASSESSMENT_VERSION, answer.availability_reason)
        self.assertIsNone(answer.outcome)

    def test_an_older_envelope_is_refused_too(self):
        """Not `<= 3`: V2 collapsed lineage reasons and meant something else."""
        for version in (1, 2):
            with self.subTest(version=version):
                answer = aggregate(resolved([criterion("c1", RESULT_MET)], version=version))
                self.assertEqual(
                    REASON_UNSUPPORTED_ASSESSMENT_VERSION, answer.availability_reason
                )

    def test_a_shape_that_still_fits_is_not_evidence_of_compatibility(self):
        """The envelope parses perfectly; it is still refused."""
        answer = aggregate(resolved([criterion("c1", RESULT_MET)], version=99))
        self.assertIsNone(answer.counts)
        self.assertIsNone(answer.requires_human)

    def test_a_missing_version_is_refused(self):
        answer = aggregate(resolved([criterion("c1", RESULT_MET)], version=None))
        self.assertEqual(REASON_UNSUPPORTED_ASSESSMENT_VERSION, answer.availability_reason)


class MalformedInput(unittest.TestCase):
    """Envelopes the service cannot produce. Refused, never normalised."""

    def refuse(self, envelope):
        answer = aggregate(envelope)
        self.assertEqual(AVAILABILITY_NOT_ASSESSABLE, answer.availability)
        self.assertEqual(REASON_ASSESSMENT_INPUT_INVALID, answer.availability_reason)
        self.assertIsNone(answer.outcome)
        self.assertIsNone(answer.counts)
        self.assertIsNone(answer.requires_human)
        return answer

    def test_a_resolved_envelope_carrying_an_unavailable_reason(self):
        envelope = resolved([criterion("c1", RESULT_MET)])
        self.refuse(CurrentAssessment(**{**envelope.__dict__,
                                         "unavailable_reason": "turn_not_closed"}))

    def test_a_resolved_envelope_carrying_a_cause(self):
        envelope = resolved([criterion("c1", RESULT_MET)])
        self.refuse(CurrentAssessment(**{**envelope.__dict__,
                                         "unavailable_cause": REASON_NOT_DECLARED}))

    def test_an_unavailable_envelope_carrying_criteria(self):
        """A partial set is one a caller would use."""
        self.refuse(unavailable("turn_not_closed", items=[criterion("c1", RESULT_MET)]))

    def test_an_unavailable_envelope_with_no_reason(self):
        self.refuse(unavailable(None))

    def test_an_unavailable_envelope_with_a_reason_outside_the_closed_set(self):
        self.refuse(unavailable("something_invented"))

    def test_a_cause_on_a_reason_that_never_carries_one(self):
        self.refuse(unavailable("turn_not_closed", cause=REASON_NOT_DECLARED))

    def test_a_cause_outside_the_lineage_vocabulary(self):
        self.refuse(unavailable(REASON_PREDECESSOR_UNAVAILABLE, cause="invented"))

    def test_an_at_turn_on_a_non_lineage_reason(self):
        self.refuse(unavailable("evaluation_not_recorded", at_turn=2))

    def test_an_unknown_state(self):
        envelope = resolved([])
        self.refuse(CurrentAssessment(**{**envelope.__dict__, "state": "partial"}))

    def test_an_invalid_criterion_result(self):
        self.refuse(resolved([criterion("c1", "probably")]))

    def test_an_invalid_criterion_kind(self):
        self.refuse(resolved([criterion("c1", RESULT_MET, kind="automatic")]))

    def test_a_criterion_answered_twice(self):
        self.refuse(resolved([criterion("c1", RESULT_MET),
                              criterion("c1", RESULT_NOT_MET)]))

    def test_a_criterion_with_no_identity(self):
        self.refuse(resolved([criterion("", RESULT_MET)]))

    def test_nothing_is_normalised(self):
        """A refusal does not quietly become a usable answer."""
        answer = aggregate(resolved([criterion("c1", "probably")]))
        self.assertNotEqual(OUTCOME_MET, answer.outcome)
        self.assertFalse(answer.assessable)

    def test_it_never_raises(self):
        for envelope in (unavailable(None), resolved([criterion("c1", "nonsense")])):
            self.assertIsInstance(aggregate(envelope), AcceptanceAggregate)


# -- identity -----------------------------------------------------------------


class CompositionalIdentity(unittest.TestCase):
    """The aggregate composes on the envelope fingerprint rather than re-deriving."""

    def test_the_consumed_fingerprint_is_recorded(self):
        answer = aggregate(resolved([criterion("c1", RESULT_MET)], fingerprint="a" * 64))
        self.assertEqual("a" * 64, answer.assessment_fingerprint)

    def test_identical_visible_answers_from_different_evidence_differ(self):
        """The load-bearing composition property.

        Two turns whose criteria all read the same way are not the same fact if
        they stood on different requirement sets or different observations. The
        envelope fingerprint already knows that; the aggregate inherits it.
        """
        one = aggregate(resolved([criterion("c1", RESULT_MET),
                                  criterion("c2", RESULT_UNVERIFIED)], fingerprint="a" * 64))
        two = aggregate(resolved([criterion("c1", RESULT_MET),
                                  criterion("c2", RESULT_UNVERIFIED)], fingerprint="b" * 64))
        self.assertEqual(one.outcome, two.outcome)
        self.assertEqual(one.counts, two.counts)
        self.assertNotEqual(one.fingerprint, two.fingerprint)

    def test_it_does_not_rebind_low_level_evidence(self):
        source = (REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "acceptance.py")
        text = source.read_text(encoding="utf-8")
        tree = ast.parse(text)
        referenced = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        for forbidden in ("observation_fingerprint", "evaluation_fingerprint",
                          "lineage_fingerprint", "criteria_fingerprint"):
            self.assertNotIn(forbidden, referenced)

    def test_the_outcome_moves_the_identity(self):
        met = aggregate(resolved([criterion("c1", RESULT_MET)], fingerprint="a" * 64))
        not_met = aggregate(resolved([criterion("c1", RESULT_NOT_MET)], fingerprint="a" * 64))
        self.assertNotEqual(met.fingerprint, not_met.fingerprint)

    def test_the_aggregator_version_is_bound(self):
        self.assertEqual(1, AGGREGATOR_VERSION)
        self.assertNotEqual(
            "0" * 64,
            aggregate(resolved([criterion("c1", RESULT_MET)])).fingerprint,
        )

    def test_it_is_stable_across_repeated_derivation(self):
        envelope = resolved([criterion("c1", RESULT_MET), manual("m1")])
        self.assertEqual(aggregate(envelope).fingerprint, aggregate(envelope).fingerprint)

    def test_the_counts_move_the_identity(self):
        one = aggregate(resolved([criterion("c1", RESULT_MET)], fingerprint="a" * 64))
        two = aggregate(resolved([criterion("c1", RESULT_MET),
                                  criterion("c2", RESULT_MET)], fingerprint="a" * 64))
        self.assertNotEqual(one.fingerprint, two.fingerprint)


class PurityTests(unittest.TestCase):
    def source(self):
        path = REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "acceptance.py"
        return ast.parse(path.read_text(encoding="utf-8"))

    def imported(self):
        names = set()
        for node in ast.walk(self.source()):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module.split(".")[0])
                names.update(alias.name for alias in node.names)
        return names

    def test_it_imports_nothing_that_touches_the_world(self):
        forbidden = {
            "sqlite3", "os", "subprocess", "shutil", "socket", "urllib", "requests",
            "httpx", "time", "datetime", "random", "pathlib", "tempfile", "threading",
        }
        self.assertEqual(set(), self.imported() & forbidden)

    def test_it_imports_no_store_service_or_observer(self):
        forbidden = {
            "store", "service", "TaskStore", "TaskService", "finalstate", "observe",
            "gitbaseline", "gitrange", "evidence", "claims", "resolve",
        }
        self.assertEqual(set(), self.imported() & forbidden)

    def test_it_calls_nothing_that_could_reach_a_disk_or_a_clock(self):
        called = {
            getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            for node in ast.walk(self.source())
            if isinstance(node, ast.Call)
        }
        for forbidden in ("execute", "open", "run", "Popen", "resolve", "evaluate",
                          "observe_path", "observe_paths", "now", "now_iso", "connect"):
            self.assertNotIn(forbidden, called)

    def test_it_mutates_nothing_it_is_given(self):
        envelope = resolved([criterion("c1", RESULT_MET)])
        before = (envelope.state, envelope.assessments, envelope.fingerprint)
        aggregate(envelope)
        self.assertEqual(before, (envelope.state, envelope.assessments, envelope.fingerprint))

    def test_a_deleted_repository_changes_nothing(self):
        """There is no repository in this file to delete, which is the proof."""
        self.assertEqual(
            OUTCOME_MET, aggregate(resolved([criterion("c1", RESULT_MET)])).outcome
        )


class NoGlobalVerdictTests(unittest.TestCase):
    def test_the_result_names_a_target_turn_and_nothing_larger(self):
        answer = aggregate(resolved([criterion("c1", RESULT_MET)]))
        self.assertEqual(1, answer.target_turn_number)
        for forbidden in ("task_outcome", "task_verdict", "passed", "succeeded",
                          "merge_ready", "deploy_ready", "project_state", "latest"):
            self.assertFalse(hasattr(answer, forbidden))

    def test_no_task_level_symbol_is_exported(self):
        from cofferdam.workstation.tasks import acceptance

        for forbidden in ("task_acceptance", "aggregate_task", "task_verdict",
                          "latest_acceptance", "TaskAcceptance"):
            self.assertFalse(hasattr(acceptance, forbidden))
            self.assertNotIn(forbidden, acceptance.__all__)

    def test_no_lifecycle_word_appears_in_the_vocabulary(self):
        for value in AVAILABILITIES + OUTCOMES + AVAILABILITY_REASONS:
            for forbidden in ("success", "succeeded", "failed", "passed"):
                self.assertNotIn(forbidden, value)

    def test_no_named_check_machinery(self):
        """From the AST — the module may *name* a subprocess in its prose."""
        from cofferdam.workstation.tasks import acceptance

        tree = ast.parse(Path(acceptance.__file__).read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        names |= {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for forbidden in ("check_id", "named_check", "run_check", "subprocess",
                          "Popen", "command"):
            self.assertNotIn(forbidden, names)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
