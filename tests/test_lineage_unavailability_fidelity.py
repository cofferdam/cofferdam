"""M2K PR20 — a lineage failure keeps the reason PR11 gave it.

V2 answered every one of PR11's eighteen distinct lineage failures with the
single string `lineage_unavailable`, so at one task and one turn
`continuity_not_declared`, `continuity_legacy_unknown`, `cycle_detected` and the
rest produced **one identical envelope fingerprint**. That was measured before
this PR was written, and `CollapseIsGone` measures it again from the other side.

Two assertions here are load-bearing:

* `NestedPredecessorCause` — PR11 reports a failure inherited from a predecessor
  as `predecessor_unavailable` and puts the real reason in `cause`. Preserving
  only the top-level reason would have fixed the direct cases and left the nested
  ones exactly as collapsed as V2 had them, which is why the cause is carried;
* `NoTranslationLayer` — the envelope reports PR11's own vocabulary rather than a
  parallel one. A second closed set would have to be kept in step with the first,
  and this repository already tracks what that costs.

Everything else pins that PR20 changed nothing else: no criterion result moved,
no assessment-layer reason was renamed or absorbed, and the binder stayed pure.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.binding import (
    ASSESSMENT_RESOLVED,
    ASSESSMENT_SET_REASONS,
    ASSESSMENT_UNAVAILABLE,
    CURRENT_ASSESSMENT_VERSION,
    LINEAGE_REASONS,
    REASON_EVALUATION_INCONSISTENT,
    REASON_EVALUATION_NOT_RECORDED,
    REASON_FINAL_STATE_INCONSISTENT,
    REASON_FINAL_STATE_LINEAGE_MISMATCH,
    REASON_FINAL_STATE_PATH_MISSING,
    REASON_LINEAGE_UNAVAILABLE,
    REASON_TURN_NOT_CLOSED,
    REASON_UNSUPPORTED_EVALUATOR,
    REASON_UNSUPPORTED_OBSERVER,
    SET_REASONS,
    bind,
    current_assessment_fingerprint,
)
from cofferdam.workstation.tasks.evaluation import RESULT_MET
from cofferdam.workstation.tasks.lineage import (
    REASONS as RESOLVER_REASONS,
    RESOLVER_VERSION,
    REASON_CYCLE_DETECTED,
    REASON_DEPTH_EXCEEDED,
    REASON_DUPLICATE_ACTIVE_CRITERION,
    REASON_LEGACY_UNKNOWN,
    REASON_MALFORMED_LINEAGE,
    REASON_NOT_DECLARED,
    REASON_PREDECESSOR_UNAVAILABLE,
    REASON_SUPERSESSION_TARGET_NOT_ACTIVE,
    LineageUnavailable,
)

# The PR18 fixtures, reused rather than rebuilt: PR20 must not change any of the
# resolved-path behaviour they pin, so borrowing them is itself a regression test.
from tests.test_final_state_assessment import (  # noqa: E402
    TASK,
    active,
    change,
    evaluation,
    manual,
    observation,
    paths,
    resolved,
    result,
    state,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def unresolvable(reason, *, cause=None, at_turn=3, target=3, task_id=TASK):
    return LineageUnavailable(
        task_id=task_id,
        target_turn_number=target,
        resolver_version=RESOLVER_VERSION,
        reason=reason,
        at_turn_number=at_turn,
        cause=cause,
    )


def envelope(reason, **kwargs):
    return bind(unresolvable(reason, **kwargs), None, turn_closed=True)


# -- the vocabulary -----------------------------------------------------------


class VocabularyTests(unittest.TestCase):
    def test_the_lineage_vocabulary_is_pr11s_own(self):
        """Imported, not restated. One closed set, one owner."""
        self.assertEqual(tuple(RESOLVER_REASONS), LINEAGE_REASONS)

    def test_the_set_vocabulary_is_the_union_and_nothing_more(self):
        self.assertEqual(ASSESSMENT_SET_REASONS + LINEAGE_REASONS, SET_REASONS)
        self.assertEqual(len(set(SET_REASONS)), len(SET_REASONS))

    def test_it_grew_by_exactly_the_resolver_vocabulary(self):
        self.assertEqual(9, len(ASSESSMENT_SET_REASONS))
        self.assertEqual(18, len(LINEAGE_REASONS))
        self.assertEqual(27, len(SET_REASONS))

    def test_the_generic_reason_survives_only_as_a_fallback(self):
        """It is still in the vocabulary, and nothing routine produces it."""
        self.assertIn(REASON_LINEAGE_UNAVAILABLE, ASSESSMENT_SET_REASONS)
        self.assertNotIn(REASON_LINEAGE_UNAVAILABLE, LINEAGE_REASONS)


class NoTranslationLayer(unittest.TestCase):
    """The envelope reports PR11's word, not a synonym for it.

    A translation table would be a second closed vocabulary to keep in step with
    the first — the shape of the `ContinuityInvalid` → `ContinuityUnrecorded`
    debt this repository already carries. Asserted for every resolver reason so a
    later "nicer wording" pass cannot quietly introduce one.
    """

    def test_every_resolver_reason_reaches_the_envelope_unchanged(self):
        for reason in RESOLVER_REASONS:
            with self.subTest(reason=reason):
                self.assertEqual(reason, envelope(reason).unavailable_reason)

    def test_no_resolver_reason_is_renamed_at_the_boundary(self):
        produced = {envelope(reason).unavailable_reason for reason in RESOLVER_REASONS}
        self.assertEqual(set(RESOLVER_REASONS), produced)

    def test_the_binder_defines_no_lineage_reason_of_its_own(self):
        """It may name PR11's constants; it may not mint parallel ones."""
        source = (REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "binding.py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        assigned = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for invented in (
            "REASON_NOT_DECLARED",
            "REASON_LEGACY_UNKNOWN",
            "REASON_CYCLE_DETECTED",
            "REASON_MALFORMED_LINEAGE",
            "REASON_PREDECESSOR_UNAVAILABLE",
        ):
            self.assertNotIn(invented, assigned)


# -- the two PR9 cases --------------------------------------------------------


class DirectNotDeclared(unittest.TestCase):
    """"Nobody stated a relationship." Recoverable, and it is not the other one."""

    def test_the_reason_is_preserved(self):
        answer = envelope(REASON_NOT_DECLARED)
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(REASON_NOT_DECLARED, answer.unavailable_reason)

    def test_it_is_not_the_generic_reason(self):
        self.assertNotEqual(
            REASON_LINEAGE_UNAVAILABLE, envelope(REASON_NOT_DECLARED).unavailable_reason
        )

    def test_a_failure_at_the_target_carries_no_cause(self):
        """`cause` exists for an inherited failure. Inventing one would be noise."""
        answer = envelope(REASON_NOT_DECLARED)
        self.assertIsNone(answer.unavailable_cause)
        self.assertEqual(3, answer.unavailable_at_turn_number)

    def test_no_criterion_assessments_are_produced(self):
        self.assertEqual((), envelope(REASON_NOT_DECLARED).assessments)
        self.assertIsNone(envelope(REASON_NOT_DECLARED).lineage_fingerprint)


class DirectLegacyUnknown(unittest.TestCase):
    """"This turn predates continuity." Not recoverable, and not the other one."""

    def test_the_reason_is_preserved(self):
        self.assertEqual(
            REASON_LEGACY_UNKNOWN, envelope(REASON_LEGACY_UNKNOWN).unavailable_reason
        )

    def test_it_is_a_different_fact_from_not_declared(self):
        """The distinction PR9 named and V2 could not express."""
        undeclared = envelope(REASON_NOT_DECLARED)
        legacy = envelope(REASON_LEGACY_UNKNOWN)
        self.assertNotEqual(undeclared.unavailable_reason, legacy.unavailable_reason)
        self.assertNotEqual(undeclared.fingerprint, legacy.fingerprint)


class NestedPredecessorCause(unittest.TestCase):
    """The reason preserving the top-level reason alone would not have been enough.

    PR11 reports an inherited failure as `predecessor_unavailable` and puts *how*
    it failed in `cause`. Without carrying the cause, a target whose predecessor
    was never declared and one whose predecessor predates continuity would both
    read `predecessor_unavailable` — the exact pair PR9 required to stay apart,
    collapsed one level down instead of at the top.
    """

    def nested(self, cause, at_turn=2):
        return envelope(REASON_PREDECESSOR_UNAVAILABLE, cause=cause, at_turn=at_turn)

    def test_the_outer_reason_says_a_dependency_failed(self):
        answer = self.nested(REASON_NOT_DECLARED)
        self.assertEqual(REASON_PREDECESSOR_UNAVAILABLE, answer.unavailable_reason)

    def test_the_cause_says_how(self):
        self.assertEqual(
            REASON_NOT_DECLARED, self.nested(REASON_NOT_DECLARED).unavailable_cause
        )
        self.assertEqual(
            REASON_LEGACY_UNKNOWN, self.nested(REASON_LEGACY_UNKNOWN).unavailable_cause
        )

    def test_the_two_nested_causes_are_different_facts(self):
        undeclared = self.nested(REASON_NOT_DECLARED)
        legacy = self.nested(REASON_LEGACY_UNKNOWN)
        self.assertEqual(undeclared.unavailable_reason, legacy.unavailable_reason)
        self.assertNotEqual(undeclared.unavailable_cause, legacy.unavailable_cause)
        self.assertNotEqual(undeclared.fingerprint, legacy.fingerprint)

    def test_the_turn_says_where_the_chain_broke(self):
        self.assertEqual(2, self.nested(REASON_NOT_DECLARED, at_turn=2).unavailable_at_turn_number)
        self.assertEqual(5, self.nested(REASON_NOT_DECLARED, at_turn=5).unavailable_at_turn_number)

    def test_the_same_failure_at_a_different_turn_is_a_different_fact(self):
        """Otherwise a chain breaking at turn 2 and at turn 5 hash identically."""
        self.assertNotEqual(
            self.nested(REASON_NOT_DECLARED, at_turn=2).fingerprint,
            self.nested(REASON_NOT_DECLARED, at_turn=5).fingerprint,
        )

    def test_a_nested_cause_is_never_promoted_to_the_reason(self):
        """The outer fact is still *a dependency failed*, and stays sayable."""
        answer = self.nested(REASON_LEGACY_UNKNOWN)
        self.assertNotEqual(REASON_LEGACY_UNKNOWN, answer.unavailable_reason)


class StructuralReasons(unittest.TestCase):
    """Integrity failures keep their own names too."""

    def test_each_structural_reason_is_preserved(self):
        for reason in (
            REASON_MALFORMED_LINEAGE,
            REASON_CYCLE_DETECTED,
            REASON_DEPTH_EXCEEDED,
            REASON_DUPLICATE_ACTIVE_CRITERION,
            REASON_SUPERSESSION_TARGET_NOT_ACTIVE,
        ):
            with self.subTest(reason=reason):
                self.assertEqual(reason, envelope(reason).unavailable_reason)

    def test_they_are_not_confused_with_the_unknown_family(self):
        structural = {envelope(r).fingerprint for r in
                      (REASON_MALFORMED_LINEAGE, REASON_CYCLE_DETECTED)}
        unknown = {envelope(r).fingerprint for r in
                   (REASON_NOT_DECLARED, REASON_LEGACY_UNKNOWN)}
        self.assertEqual(set(), structural & unknown)


class CollapseIsGone(unittest.TestCase):
    """The measurement that motivated PR20, from the other side.

    Under V2 every one of these produced one string and one fingerprint at the
    same task and turn. Holding task and target fixed is the point: a difference
    that came from anywhere else would prove nothing.
    """

    CASES = (
        (REASON_LEGACY_UNKNOWN, None, 3),
        (REASON_NOT_DECLARED, None, 3),
        (REASON_PREDECESSOR_UNAVAILABLE, REASON_LEGACY_UNKNOWN, 2),
        (REASON_PREDECESSOR_UNAVAILABLE, REASON_NOT_DECLARED, 2),
        (REASON_CYCLE_DETECTED, None, 3),
        (REASON_MALFORMED_LINEAGE, None, 3),
        (REASON_DEPTH_EXCEEDED, None, 3),
    )

    def answers(self):
        return [
            envelope(reason, cause=cause, at_turn=at_turn)
            for reason, cause, at_turn in self.CASES
        ]

    def test_every_case_now_has_its_own_identity(self):
        prints = {a.fingerprint for a in self.answers()}
        self.assertEqual(len(self.CASES), len(prints))

    def test_every_case_now_has_its_own_reason_or_cause(self):
        pairs = {(a.unavailable_reason, a.unavailable_cause) for a in self.answers()}
        self.assertEqual(len(self.CASES), len(pairs))

    def test_none_of_them_is_the_generic_reason(self):
        for answer in self.answers():
            self.assertNotEqual(REASON_LINEAGE_UNAVAILABLE, answer.unavailable_reason)

    def test_all_of_them_are_still_unavailable(self):
        """Fidelity changed; safety did not. None became assessable."""
        for answer in self.answers():
            self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
            self.assertEqual((), answer.assessments)


class UnknownResolverReason(unittest.TestCase):
    """A newer resolver's classification is refused honestly, not passed through.

    The same totality discipline the module applies to predicates and evaluator
    versions: an older build must answer rather than crash, and must not imply it
    understood a word it does not know.
    """

    def test_it_falls_back_to_the_generic_reason(self):
        answer = envelope("a_reason_from_a_newer_resolver")
        self.assertEqual(REASON_LINEAGE_UNAVAILABLE, answer.unavailable_reason)

    def test_it_does_not_leak_the_unknown_string(self):
        answer = envelope("a_reason_from_a_newer_resolver")
        self.assertIn(answer.unavailable_reason, SET_REASONS)

    def test_an_unrecognised_outer_reason_drops_its_cause_and_turn(self):
        """An unknown outer reason makes no promise about its inner one."""
        answer = envelope("a_reason_from_a_newer_resolver", cause=REASON_NOT_DECLARED)
        self.assertIsNone(answer.unavailable_cause)
        self.assertIsNone(answer.unavailable_at_turn_number)

    def test_an_unrecognised_cause_is_dropped_but_the_reason_is_kept(self):
        answer = envelope(REASON_PREDECESSOR_UNAVAILABLE, cause="something_newer")
        self.assertEqual(REASON_PREDECESSOR_UNAVAILABLE, answer.unavailable_reason)
        self.assertIsNone(answer.unavailable_cause)

    def test_it_never_raises(self):
        self.assertEqual(ASSESSMENT_UNAVAILABLE, envelope("").state)


# -- everything PR20 did not change -------------------------------------------


class AssessmentReasonsUnchanged(unittest.TestCase):
    """The other eight refusals keep their exact names and their own layer."""

    def test_none_was_renamed_or_absorbed(self):
        for reason in (
            REASON_TURN_NOT_CLOSED,
            REASON_EVALUATION_NOT_RECORDED,
            REASON_EVALUATION_INCONSISTENT,
            REASON_UNSUPPORTED_EVALUATOR,
            REASON_FINAL_STATE_INCONSISTENT,
            REASON_UNSUPPORTED_OBSERVER,
            REASON_FINAL_STATE_LINEAGE_MISMATCH,
            REASON_FINAL_STATE_PATH_MISSING,
        ):
            with self.subTest(reason=reason):
                self.assertIn(reason, ASSESSMENT_SET_REASONS)
                self.assertNotIn(reason, LINEAGE_REASONS)

    def test_turn_not_closed_is_still_reported_before_the_lineage_is_read(self):
        """An open turn is this layer's refusal, whatever the lineage says."""
        answer = bind(unresolvable(REASON_NOT_DECLARED), None, turn_closed=False)
        self.assertEqual(REASON_TURN_NOT_CLOSED, answer.unavailable_reason)

    def test_an_assessment_layer_refusal_manufactures_no_context(self):
        answer = bind(
            resolved(1, [active("criterion_0001", 1, change(1, "x"))]),
            None,
            turn_closed=True,
        )
        self.assertEqual(REASON_EVALUATION_NOT_RECORDED, answer.unavailable_reason)
        self.assertIsNone(answer.unavailable_cause)
        self.assertIsNone(answer.unavailable_at_turn_number)

    def test_a_final_state_refusal_manufactures_no_context(self):
        answer = bind(
            resolved(1, [active("criterion_0001", 1, state(1, "a.txt"))]),
            None,
            turn_closed=True,
            final_state=observation(1, paths(("a.txt", "present", "file")),
                                    lineage="z" * 64),
        )
        self.assertEqual(REASON_FINAL_STATE_LINEAGE_MISMATCH, answer.unavailable_reason)
        self.assertIsNone(answer.unavailable_cause)
        self.assertIsNone(answer.unavailable_at_turn_number)


class ResolvedPathUnchanged(unittest.TestCase):
    """Criterion semantics are identical to V2. Only the version moved."""

    def mixed(self):
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
                paths(("inherited.txt", "present", "file"), ("same.txt", "absent")),
            ),
        )

    def test_every_criterion_result_is_what_pr18_produced(self):
        self.assertEqual(
            ["unverified", "met", "met", "not_met", "unverified"],
            [a.result for a in self.mixed().assessments],
        )

    def test_every_domain_and_reason_is_what_pr18_produced(self):
        answer = self.mixed()
        self.assertEqual(
            [
                "not_applicable",
                "turn_change",
                "final_state",
                "final_state",
                "not_applicable",
            ],
            [a.domain for a in answer.assessments],
        )
        self.assertEqual(
            [
                "inherited_change_not_current_state_evaluable",
                "turn_change_evaluated",
                "final_state_observed",
                "final_state_observed",
                "manual_criterion_no_machine_authority",
            ],
            [a.reason for a in answer.assessments],
        )

    def test_the_order_is_still_pr11s(self):
        self.assertEqual(
            ["criterion_000%d" % n for n in range(1, 6)],
            [a.criterion_id for a in self.mixed().assessments],
        )

    def test_a_resolved_envelope_carries_no_unavailable_context(self):
        answer = self.mixed()
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertIsNone(answer.unavailable_reason)
        self.assertIsNone(answer.unavailable_cause)
        self.assertIsNone(answer.unavailable_at_turn_number)

    def test_a_zero_active_set_is_still_a_resolved_answer(self):
        answer = bind(resolved(1, []), None, turn_closed=True)
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(0, answer.criterion_count)
        self.assertIsNone(answer.unavailable_reason)


class FingerprintTests(unittest.TestCase):
    def test_the_cause_is_bound(self):
        self.assertNotEqual(
            current_assessment_fingerprint(
                task_id=TASK, target_turn_number=3, state=ASSESSMENT_UNAVAILABLE,
                unavailable_reason=REASON_PREDECESSOR_UNAVAILABLE,
                unavailable_cause=REASON_NOT_DECLARED,
                unavailable_at_turn_number=2,
                lineage_fingerprint=None, assessments=(),
            ),
            current_assessment_fingerprint(
                task_id=TASK, target_turn_number=3, state=ASSESSMENT_UNAVAILABLE,
                unavailable_reason=REASON_PREDECESSOR_UNAVAILABLE,
                unavailable_cause=REASON_LEGACY_UNKNOWN,
                unavailable_at_turn_number=2,
                lineage_fingerprint=None, assessments=(),
            ),
        )

    def test_the_at_turn_is_bound(self):
        base = dict(
            task_id=TASK, target_turn_number=3, state=ASSESSMENT_UNAVAILABLE,
            unavailable_reason=REASON_PREDECESSOR_UNAVAILABLE,
            unavailable_cause=REASON_NOT_DECLARED,
            lineage_fingerprint=None, assessments=(),
        )
        self.assertNotEqual(
            current_assessment_fingerprint(unavailable_at_turn_number=2, **base),
            current_assessment_fingerprint(unavailable_at_turn_number=5, **base),
        )

    def test_it_is_stable_across_repeated_derivation(self):
        first = envelope(REASON_PREDECESSOR_UNAVAILABLE, cause=REASON_NOT_DECLARED)
        second = envelope(REASON_PREDECESSOR_UNAVAILABLE, cause=REASON_NOT_DECLARED)
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_no_resolved_lineage_fingerprint_is_fabricated(self):
        """Reason specificity is not a substitute for a resolved identity."""
        for reason in RESOLVER_REASONS:
            with self.subTest(reason=reason):
                self.assertIsNone(envelope(reason).lineage_fingerprint)

    def test_the_version_is_bound_and_has_moved(self):
        self.assertEqual(3, CURRENT_ASSESSMENT_VERSION)


class PurityTests(unittest.TestCase):
    def source(self):
        path = REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "binding.py"
        return ast.parse(path.read_text(encoding="utf-8"))

    def test_it_imports_the_vocabulary_and_not_the_resolver(self):
        taken = set()
        for node in ast.walk(self.source()):
            if isinstance(node, ast.ImportFrom) and node.module == "lineage":
                taken.update(alias.name for alias in node.names)
        self.assertEqual({"REASONS"}, taken)
        self.assertNotIn("resolve", taken)
        self.assertNotIn("LineageGraph", taken)

    def test_it_never_calls_the_resolver(self):
        called = {
            getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            for node in ast.walk(self.source())
            if isinstance(node, ast.Call)
        }
        for forbidden in ("resolve", "lineage_inputs", "execute", "open", "run"):
            self.assertNotIn(forbidden, called)

    def test_it_reclassifies_nothing(self):
        """PR11 owns *why* a lineage failed; this module only preserves it."""
        text = (
            REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "binding.py"
        ).read_text(encoding="utf-8")
        for forbidden in ("_classify_lineage", "def _lineage_reason_for"):
            self.assertNotIn(forbidden, text)

    def test_a_deleted_repository_changes_nothing(self):
        """No world access: these are values, not lookups."""
        self.assertEqual(
            REASON_NOT_DECLARED, envelope(REASON_NOT_DECLARED).unavailable_reason
        )


class NegativeSpaceTests(unittest.TestCase):
    def test_no_schema_change(self):
        from cofferdam.workstation.tasks.store import SCHEMA_VERSION

        self.assertEqual(11, SCHEMA_VERSION)

    def test_no_other_semantic_version_moved(self):
        from cofferdam.workstation.tasks.continuity import CONTINUITY_MODEL_VERSION
        from cofferdam.workstation.tasks.criteria import CRITERIA_MODEL_VERSION
        from cofferdam.workstation.tasks.evaluation import EVALUATOR_VERSION
        from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION
        from cofferdam.workstation.tasks.finalstate import FINAL_STATE_OBSERVER_VERSION

        self.assertEqual(1, EVALUATOR_VERSION)
        self.assertEqual(1, FINAL_STATE_OBSERVER_VERSION)
        self.assertEqual(1, RESOLVER_VERSION)
        self.assertEqual(1, CONTINUITY_MODEL_VERSION)
        self.assertEqual(1, CRITERIA_MODEL_VERSION)
        self.assertEqual(3, ASSEMBLER_VERSION)

    def test_the_resolver_vocabulary_was_not_edited(self):
        """PR20 preserves PR11's judgement; it does not extend or rename it."""
        self.assertEqual(18, len(RESOLVER_REASONS))
        for expected in (REASON_NOT_DECLARED, REASON_LEGACY_UNKNOWN,
                         REASON_PREDECESSOR_UNAVAILABLE, REASON_CYCLE_DETECTED):
            self.assertIn(expected, RESOLVER_REASONS)

    def test_no_aggregate_exists(self):
        from cofferdam.workstation.tasks import binding

        for forbidden in ("AGGREGATOR_VERSION", "aggregate", "acceptance_state",
                          "AcceptanceAggregate", "requires_human"):
            self.assertFalse(hasattr(binding, forbidden))
            self.assertNotIn(forbidden, binding.__all__)

    def test_no_public_surface_reaches_it(self):
        surfaces = [
            REPO_ROOT / "cofferdam" / "workstation" / "service.py",
            REPO_ROOT / "cofferdam" / "workstation" / "actions.py",
        ]
        bridge = REPO_ROOT / "cofferdam" / "actions_bridge"
        if bridge.exists():
            surfaces.extend(sorted(bridge.rglob("*.py")))
        for path in surfaces:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("unavailable_cause", text, str(path))
            self.assertNotIn("CurrentAssessment", text, str(path))

    def test_the_pwa_gained_nothing(self):
        base = REPO_ROOT / "cofferdam" / "workstation"
        for pattern in ("*.js", "*.html", "*.css"):
            for path in base.rglob(pattern):
                text = path.read_text(encoding="utf-8", errors="ignore")
                self.assertNotIn("unavailable_cause", text, str(path))
                self.assertNotIn("current_criterion_assessment", text, str(path))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
