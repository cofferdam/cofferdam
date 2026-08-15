"""M2K PR7 — what each criterion predicate decides, and what it refuses to.

Every test here calls the **pure** evaluator directly with a hand-built snapshot
and bundle. No database, no service, no Git: the point is the semantics, and a
semantics test that needed a repository would be testing the repository.

Three rules run through all of it.

**Positive and negative evidence are asymmetric.** One attributable observation
can establish that something happened. Establishing that it did *not* happen
needs every domain it could have shown up in to have been read completely.

**Machine evidence is the only authority.** A claim never satisfies a criterion
and a claim's absence never fails one. These tests assert that by building
bundles whose claims say the opposite of the observations.

**Absence of proof is `unverified`, never `not_met`.** The whole reason the
vocabulary has three values is that a two-valued evaluator turns a gap in
Cofferdam's observation into an accusation about the worker.
"""

from __future__ import annotations

import unittest

from cofferdam.workstation.tasks.claims import ChangeClaim
from cofferdam.workstation.tasks.criteria import (
    CRITERIA_NOT_PROVIDED,
    CRITERIA_PRESENT,
    AcceptanceCriterion,
    CriteriaSnapshot,
    validate_criteria,
)
from cofferdam.workstation.tasks.evaluation import (
    REASON_ATTRIBUTION_UNKNOWN,
    REASON_BOUNDARY_NOT_CLEAN,
    REASON_COMPLETE_CHANGE_ABSENT,
    REASON_COMPLETE_OPERATION_INCOMPATIBLE,
    REASON_COMPLETE_RENAME_ABSENT,
    REASON_HISTORY_DIVERGED,
    REASON_MACHINE_CHANGE_OBSERVED,
    REASON_MACHINE_OPERATION_OBSERVED,
    REASON_MACHINE_RENAME_OBSERVED,
    REASON_MANUAL,
    REASON_OBSERVATIONS_INCOMPLETE,
    REASON_OPERATION_NOT_OBSERVED,
    REASON_RANGE_INCOMPLETE,
    REASON_RANGE_NOT_RECORDED,
    REASON_UNSUPPORTED_CAPABILITY,
    REASON_UNSUPPORTED_OBSERVATION,
    REASON_WORKTREE_NOT_OBSERVED,
    REASONS,
    REASONS_FOR_RESULT,
    RESULT_MET,
    RESULT_NOT_MET,
    RESULT_UNVERIFIED,
    RESULTS,
    evaluate,
    evaluate_criterion,
)
from cofferdam.workstation.tasks.evidence import (
    ATTRIBUTION_EXACT,
    ATTRIBUTION_LEGACY_UNKNOWN,
    BUNDLE_VERSION,
    COMPLETENESS_COMPLETE,
    LIMIT_UNSUPPORTED_OBSERVATION,
    OBSERVATION_DOMAIN_COMMITTED_RANGE,
    OBSERVATION_DOMAIN_WORKTREE,
    RANGE_ANCESTRY_DIVERGED,
    RANGE_ANCESTRY_LINEAR,
    RANGE_BOUNDARY_CLEAN,
    RANGE_COVERAGE_COMPLETE,
    RANGE_COVERAGE_INCOMPLETE,
    CommittedRangeSummary,
    EvidenceBundle,
    IngestionSummary,
    MachineObservation,
)
from cofferdam.workstation.tasks.models import (
    CHANGE_CREATED,
    CHANGE_DELETED,
    CHANGE_MODIFIED,
    CHANGE_RENAMED,
    CHANGE_UNKNOWN,
    EVIDENCE_GIT_OBSERVED,
)

CLEAN_RANGE = CommittedRangeSummary(
    recorded=True,
    event_sequence=9,
    baseline_revision="a" * 40,
    target_revision="b" * 40,
    boundary_quality=RANGE_BOUNDARY_CLEAN,
    ancestry=RANGE_ANCESTRY_LINEAR,
    coverage=RANGE_COVERAGE_COMPLETE,
    limitation=None,
)


def observation(path, *, domain, kind=CHANGE_MODIFIED, previous=None, sequence=5, index=0):
    return MachineObservation(
        event_sequence=sequence,
        evidence_index=index,
        path=path,
        source=EVIDENCE_GIT_OBSERVED,
        evidence_type="path",
        operation=None,
        result=path,
        change_kind=kind,
        previous_path=previous,
        change_status=None,
        domain=domain,
    )


def bundle(
    *,
    observations=(),
    committed_range=CLEAN_RANGE,
    attribution=ATTRIBUTION_EXACT,
    machine_complete=True,
    limitations=(),
    claims=(),
    input_fingerprint="f" * 64,
    worktree_observed=True,
):
    return EvidenceBundle(
        version=BUNDLE_VERSION,
        assembler_version=3,
        input_fingerprint=input_fingerprint,
        task_id="task_x",
        turn_number=1,
        turn_attribution=attribution,
        ingestion=IngestionSummary(state=COMPLETENESS_COMPLETE),
        claims=tuple(claims),
        observations=tuple(observations),
        relationships=(),
        limitations=tuple(limitations),
        opened_after_event_sequence=0,
        closed_through_event_sequence=20,
        turn_open=False,
        repository_reported_clean=worktree_observed,
        machine_observations_complete=machine_complete,
        committed_range=committed_range,
    )


def criterion(**fields):
    base = {"ordinal": 1, "kind": "evidence", "criterion_id": "acr_1"}
    base.update(fields)
    return AcceptanceCriterion(**base)


def decide(criterion_value, bundle_value):
    outcome = evaluate_criterion(criterion_value, bundle_value)
    return outcome.result, outcome.reason


class TheVocabulary(unittest.TestCase):
    def test_exactly_three_results_and_no_failed(self):
        self.assertEqual(RESULTS, (RESULT_MET, RESULT_NOT_MET, RESULT_UNVERIFIED))
        self.assertNotIn("failed", RESULTS)
        self.assertNotIn("passed", RESULTS)

    def test_every_reason_belongs_to_exactly_one_result(self):
        seen = [r for group in REASONS_FOR_RESULT.values() for r in group]
        self.assertEqual(sorted(seen), sorted(REASONS))
        self.assertEqual(len(seen), len(set(seen)))

    def test_every_not_met_reason_says_complete(self):
        """Absence is a finding only when the looking was complete."""
        for reason in REASONS_FOR_RESULT[RESULT_NOT_MET]:
            self.assertIn("complete", reason, reason)


class PathChanged(unittest.TestCase):
    """`path_changed(P)` — a resulting repository change for P at the boundary."""

    def setUp(self):
        self.c = criterion(predicate="path_changed", path="src/a.py")

    def test_clean_attributable_committed_change_is_met(self):
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_COMMITTED_RANGE),)
        )
        self.assertEqual(decide(self.c, found), (RESULT_MET, REASON_MACHINE_CHANGE_OBSERVED))

    def test_clean_attributable_worktree_change_is_met(self):
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        self.assertEqual(decide(self.c, found), (RESULT_MET, REASON_MACHINE_CHANGE_OBSERVED))

    def test_the_same_path_in_both_domains_is_met(self):
        found = bundle(
            observations=(
                observation("src/a.py", domain=OBSERVATION_DOMAIN_COMMITTED_RANGE, kind=CHANGE_CREATED),
                observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE, sequence=6),
            )
        )
        self.assertEqual(decide(self.c, found)[0], RESULT_MET)

    def test_complete_trustworthy_absence_is_not_met(self):
        found = bundle(observations=(observation("other.py", domain=OBSERVATION_DOMAIN_WORKTREE),))
        self.assertEqual(
            decide(self.c, found), (RESULT_NOT_MET, REASON_COMPLETE_CHANGE_ABSENT)
        )

    def test_absence_with_no_committed_range_is_unverified(self):
        """A worker that committed leaves a clean tree. Worktree alone cannot close."""
        found = bundle(committed_range=CommittedRangeSummary(recorded=False))
        self.assertEqual(decide(self.c, found), (RESULT_UNVERIFIED, REASON_RANGE_NOT_RECORDED))

    def test_absence_with_diverged_history_is_unverified(self):
        found = bundle(
            committed_range=CommittedRangeSummary(
                recorded=True,
                boundary_quality=RANGE_BOUNDARY_CLEAN,
                ancestry=RANGE_ANCESTRY_DIVERGED,
                coverage=RANGE_COVERAGE_COMPLETE,
            )
        )
        self.assertEqual(decide(self.c, found), (RESULT_UNVERIFIED, REASON_HISTORY_DIVERGED))

    def test_absence_with_incomplete_range_coverage_is_unverified(self):
        found = bundle(
            committed_range=CommittedRangeSummary(
                recorded=True,
                boundary_quality=RANGE_BOUNDARY_CLEAN,
                ancestry=RANGE_ANCESTRY_LINEAR,
                coverage=RANGE_COVERAGE_INCOMPLETE,
            )
        )
        self.assertEqual(decide(self.c, found), (RESULT_UNVERIFIED, REASON_RANGE_INCOMPLETE))

    def test_absence_with_incomplete_worktree_observation_is_unverified(self):
        found = bundle(machine_complete=False)
        self.assertEqual(
            decide(self.c, found), (RESULT_UNVERIFIED, REASON_OBSERVATIONS_INCOMPLETE)
        )

    def test_absence_with_an_unsupported_observation_shape_is_unverified(self):
        found = bundle(limitations=(LIMIT_UNSUPPORTED_OBSERVATION,))
        self.assertEqual(
            decide(self.c, found), (RESULT_UNVERIFIED, REASON_UNSUPPORTED_OBSERVATION)
        )

    def test_absence_on_a_legacy_attributed_turn_is_unverified(self):
        found = bundle(attribution=ATTRIBUTION_LEGACY_UNKNOWN)
        self.assertEqual(
            decide(self.c, found), (RESULT_UNVERIFIED, REASON_ATTRIBUTION_UNKNOWN)
        )

    def test_a_dirty_pre_work_boundary_never_produces_met(self):
        """The change is real; that this turn caused it is not established."""
        dirty = CommittedRangeSummary(
            recorded=True,
            boundary_quality="dirty_or_incomplete",
            ancestry=RANGE_ANCESTRY_LINEAR,
            coverage=RANGE_COVERAGE_COMPLETE,
        )
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_COMMITTED_RANGE),),
            committed_range=dirty,
        )
        self.assertEqual(
            decide(self.c, found), (RESULT_UNVERIFIED, REASON_BOUNDARY_NOT_CLEAN)
        )

    def test_a_dirty_boundary_still_permits_not_met_on_genuine_absence(self):
        """The asymmetry, stated as a test because it is easy to get backwards.

        Boundary quality gates **positive** attribution: a change seen afterwards
        might predate the turn, so it cannot be credited to it. It does not gate a
        **negative**, because a dirty tree beforehand gives the path nowhere to
        hide. If P is absent from a completely-read range and a
        completely-read worktree, then P has no resulting change at the boundary —
        which is exactly what `path_changed` asks about — and that is true no
        matter what else was dirty when the turn began.

        A path that *was* dirty and stayed changed is not absent: it appears in
        the worktree domain and takes the unattributable branch above.
        """
        dirty = CommittedRangeSummary(
            recorded=True,
            boundary_quality="dirty_or_incomplete",
            ancestry=RANGE_ANCESTRY_LINEAR,
            coverage=RANGE_COVERAGE_COMPLETE,
        )
        found = bundle(observations=(), committed_range=dirty)
        self.assertEqual(
            decide(self.c, found), (RESULT_NOT_MET, REASON_COMPLETE_CHANGE_ABSENT)
        )

    def test_a_dirty_boundary_path_that_is_still_changed_is_unverified(self):
        dirty = CommittedRangeSummary(
            recorded=True,
            boundary_quality="dirty_or_incomplete",
            ancestry=RANGE_ANCESTRY_LINEAR,
            coverage=RANGE_COVERAGE_COMPLETE,
        )
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),),
            committed_range=dirty,
        )
        self.assertEqual(
            decide(self.c, found), (RESULT_UNVERIFIED, REASON_BOUNDARY_NOT_CLEAN)
        )

    def test_a_claim_alone_is_not_sufficient_for_met(self):
        claimed = ChangeClaim(
            claim_id="chg_1",
            task_id="task_x",
            turn_number=1,
            operation="modified",
            path="src/a.py",
            to_path=None,
            adapter_label=None,
            reported_at="x",
            artifact_id=None,
            reason="ok",
        )
        found = bundle(claims=(claimed,))
        self.assertEqual(
            decide(self.c, found), (RESULT_NOT_MET, REASON_COMPLETE_CHANGE_ABSENT)
        )

    def test_absence_in_a_worktree_nobody_examined_is_unverified(self):
        """`machine_observations_complete` is True when nobody looked.

        It means "no emitter called its own set partial", not "the tree was read
        and was empty". A path missing from the committed range could be sitting
        modified and uncommitted in a tree no one examined, so this is a gap and
        not a finding.
        """
        found = bundle(worktree_observed=False, observations=())
        self.assertEqual(
            decide(self.c, found), (RESULT_UNVERIFIED, REASON_WORKTREE_NOT_OBSERVED)
        )

    def test_a_worktree_observation_is_itself_evidence_the_domain_was_read(self):
        found = bundle(
            worktree_observed=False,
            observations=(observation("other.py", domain=OBSERVATION_DOMAIN_WORKTREE),),
        )
        self.assertEqual(
            decide(self.c, found), (RESULT_NOT_MET, REASON_COMPLETE_CHANGE_ABSENT)
        )

    def test_an_explicit_clean_tree_statement_closes_the_domain(self):
        found = bundle(worktree_observed=True, observations=())
        self.assertEqual(
            decide(self.c, found), (RESULT_NOT_MET, REASON_COMPLETE_CHANGE_ABSENT)
        )

    def test_an_absent_claim_is_not_sufficient_for_not_met(self):
        """No claims at all, but the machine saw the change. Met."""
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),),
            claims=(),
        )
        self.assertEqual(decide(self.c, found)[0], RESULT_MET)


class PathOperation(unittest.TestCase):
    """`path_operation(P, OP)` — a resulting machine operation, per domain."""

    def test_matching_committed_created_is_met(self):
        c = criterion(predicate="path_operation", path="src/a.py", operation="created")
        found = bundle(
            observations=(
                observation("src/a.py", domain=OBSERVATION_DOMAIN_COMMITTED_RANGE, kind=CHANGE_CREATED),
            )
        )
        self.assertEqual(decide(c, found), (RESULT_MET, REASON_MACHINE_OPERATION_OBSERVED))

    def test_matching_worktree_modified_is_met(self):
        c = criterion(predicate="path_operation", path="src/a.py", operation="modified")
        found = bundle(
            observations=(
                observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE, kind=CHANGE_MODIFIED),
            )
        )
        self.assertEqual(decide(c, found), (RESULT_MET, REASON_MACHINE_OPERATION_OBSERVED))

    def test_committed_created_plus_worktree_modified_satisfies_both(self):
        """Two facts at two moments. Neither cancels the other.

        The file was created in the range this turn committed, and is *still*
        modified in the working tree — committed, then edited again. Collapsing
        the domains to one final operation would destroy one of two true
        statements and make a satisfiable criterion look unsatisfied.
        """
        found = bundle(
            observations=(
                observation("src/a.py", domain=OBSERVATION_DOMAIN_COMMITTED_RANGE, kind=CHANGE_CREATED),
                observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE, kind=CHANGE_MODIFIED, sequence=6),
            )
        )
        created = criterion(predicate="path_operation", path="src/a.py", operation="created")
        modified = criterion(predicate="path_operation", path="src/a.py", operation="modified")
        self.assertEqual(decide(created, found)[0], RESULT_MET)
        self.assertEqual(decide(modified, found)[0], RESULT_MET)

    def test_a_different_operation_in_another_domain_does_not_force_not_met(self):
        deleted = criterion(predicate="path_operation", path="src/a.py", operation="deleted")
        found = bundle(
            observations=(
                observation("src/a.py", domain=OBSERVATION_DOMAIN_COMMITTED_RANGE, kind=CHANGE_CREATED),
                observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE, kind=CHANGE_MODIFIED, sequence=6),
            )
        )
        result, reason = decide(deleted, found)
        self.assertEqual(result, RESULT_NOT_MET)
        self.assertEqual(reason, REASON_COMPLETE_OPERATION_INCOMPATIBLE)

    def test_incompatible_operation_is_not_met_only_under_full_closure(self):
        c = criterion(predicate="path_operation", path="src/a.py", operation="deleted")
        incomplete = bundle(
            observations=(
                observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE, kind=CHANGE_CREATED),
            ),
            machine_complete=False,
        )
        self.assertEqual(decide(c, incomplete)[0], RESULT_UNVERIFIED)

    def test_a_legacy_observation_without_a_change_kind_is_unverified(self):
        """It proves the path changed and says nothing about how."""
        c = criterion(predicate="path_operation", path="src/a.py", operation="created")
        found = bundle(
            observations=(
                observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE, kind=CHANGE_UNKNOWN),
            )
        )
        self.assertEqual(
            decide(c, found), (RESULT_UNVERIFIED, REASON_OPERATION_NOT_OBSERVED)
        )

    def test_absence_under_full_closure_is_not_met(self):
        c = criterion(predicate="path_operation", path="src/a.py", operation="created")
        found = bundle(observations=(observation("other.py", domain=OBSERVATION_DOMAIN_WORKTREE),))
        self.assertEqual(decide(c, found), (RESULT_NOT_MET, REASON_COMPLETE_CHANGE_ABSENT))

    def test_a_claim_operation_is_not_authority(self):
        c = criterion(predicate="path_operation", path="src/a.py", operation="created")
        claimed = ChangeClaim(
            claim_id="chg_1", task_id="task_x", turn_number=1, operation="created",
            path="src/a.py", to_path=None, adapter_label=None, reported_at="x",
            artifact_id=None, reason="ok",
        )
        found = bundle(claims=(claimed,))
        self.assertEqual(decide(c, found)[0], RESULT_NOT_MET)
        self.assertNotEqual(decide(c, found)[0], RESULT_MET)

    def test_a_claim_that_disagrees_with_the_machine_does_not_produce_not_met(self):
        """`claim_conflict` territory. The machine saw `created`; the claim says
        `deleted`. The criterion asks for `created` and the machine decides it."""
        c = criterion(predicate="path_operation", path="src/a.py", operation="created")
        claimed = ChangeClaim(
            claim_id="chg_1", task_id="task_x", turn_number=1, operation="deleted",
            path="src/a.py", to_path=None, adapter_label=None, reported_at="x",
            artifact_id=None, reason="ok",
        )
        found = bundle(
            observations=(
                observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE, kind=CHANGE_CREATED),
            ),
            claims=(claimed,),
        )
        self.assertEqual(decide(c, found), (RESULT_MET, REASON_MACHINE_OPERATION_OBSERVED))


class Rename(unittest.TestCase):
    """`rename(SOURCE, DESTINATION)` — an explicit machine rename record."""

    def setUp(self):
        self.c = criterion(
            predicate="rename", path="src/old.py", to_path="src/new.py"
        )

    def test_an_explicit_machine_rename_is_met(self):
        found = bundle(
            observations=(
                observation(
                    "src/new.py",
                    domain=OBSERVATION_DOMAIN_COMMITTED_RANGE,
                    kind=CHANGE_RENAMED,
                    previous="src/old.py",
                ),
            )
        )
        self.assertEqual(decide(self.c, found), (RESULT_MET, REASON_MACHINE_RENAME_OBSERVED))

    def test_a_rename_from_the_wrong_source_is_not_a_match(self):
        found = bundle(
            observations=(
                observation(
                    "src/new.py",
                    domain=OBSERVATION_DOMAIN_COMMITTED_RANGE,
                    kind=CHANGE_RENAMED,
                    previous="src/elsewhere.py",
                ),
            )
        )
        self.assertEqual(decide(self.c, found), (RESULT_NOT_MET, REASON_COMPLETE_RENAME_ABSENT))

    def test_a_rename_to_the_wrong_destination_is_not_a_match(self):
        found = bundle(
            observations=(
                observation(
                    "src/other.py",
                    domain=OBSERVATION_DOMAIN_COMMITTED_RANGE,
                    kind=CHANGE_RENAMED,
                    previous="src/old.py",
                ),
            )
        )
        self.assertEqual(decide(self.c, found), (RESULT_NOT_MET, REASON_COMPLETE_RENAME_ABSENT))

    def test_created_plus_deleted_is_never_a_rename(self):
        """The load-bearing negative. This is what a rename looks like to a tool
        that was not tracking one, and also what two unrelated changes look like."""
        found = bundle(
            observations=(
                observation("src/new.py", domain=OBSERVATION_DOMAIN_COMMITTED_RANGE, kind=CHANGE_CREATED),
                observation("src/old.py", domain=OBSERVATION_DOMAIN_COMMITTED_RANGE, kind=CHANGE_DELETED, index=1),
            )
        )
        self.assertNotEqual(decide(self.c, found)[0], RESULT_MET)
        self.assertEqual(decide(self.c, found), (RESULT_NOT_MET, REASON_COMPLETE_RENAME_ABSENT))

    def test_incomplete_evidence_is_unverified(self):
        found = bundle(machine_complete=False)
        self.assertEqual(decide(self.c, found)[0], RESULT_UNVERIFIED)

    def test_a_dirty_boundary_makes_a_rename_unverified(self):
        dirty = CommittedRangeSummary(
            recorded=True,
            boundary_quality="dirty_or_incomplete",
            ancestry=RANGE_ANCESTRY_LINEAR,
            coverage=RANGE_COVERAGE_COMPLETE,
        )
        found = bundle(
            observations=(
                observation(
                    "src/new.py",
                    domain=OBSERVATION_DOMAIN_COMMITTED_RANGE,
                    kind=CHANGE_RENAMED,
                    previous="src/old.py",
                ),
            ),
            committed_range=dirty,
        )
        self.assertEqual(decide(self.c, found), (RESULT_UNVERIFIED, REASON_BOUNDARY_NOT_CLEAN))

    def test_a_claimed_rename_is_not_authority(self):
        claimed = ChangeClaim(
            claim_id="chg_1", task_id="task_x", turn_number=1, operation="renamed",
            path="src/old.py", to_path="src/new.py", adapter_label=None,
            reported_at="x", artifact_id=None, reason="ok",
        )
        found = bundle(claims=(claimed,))
        self.assertNotEqual(decide(self.c, found)[0], RESULT_MET)


class Manual(unittest.TestCase):
    def test_a_manual_criterion_is_always_unverified(self):
        for description in ("check the page", "the tests pass", "run pytest -q", "x" * 400):
            c = criterion(kind="manual", predicate=None, path=None, description=description)
            self.assertEqual(decide(c, bundle()), (RESULT_UNVERIFIED, REASON_MANUAL))

    def test_the_description_is_never_inspected(self):
        """A description naming an observed path still does not become evidence."""
        c = criterion(kind="manual", predicate=None, path=None, description="src/a.py changed")
        found = bundle(
            observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),)
        )
        self.assertEqual(decide(c, found), (RESULT_UNVERIFIED, REASON_MANUAL))


class UnsupportedCapability(unittest.TestCase):
    """The seat a future capability occupies, answering honestly today."""

    def test_an_unknown_predicate_is_unverified_not_an_exception(self):
        c = criterion(predicate="tests_pass", path="tests/")
        self.assertEqual(
            decide(c, bundle()), (RESULT_UNVERIFIED, REASON_UNSUPPORTED_CAPABILITY)
        )

    def test_an_unknown_kind_is_unverified(self):
        c = criterion(kind="check", predicate=None, path=None)
        self.assertEqual(
            decide(c, bundle()), (RESULT_UNVERIFIED, REASON_UNSUPPORTED_CAPABILITY)
        )

    def test_nothing_is_executed_to_answer_it(self):
        """Asserted structurally elsewhere; asserted behaviourally here."""
        c = criterion(predicate="build_succeeds", path="Makefile")
        result, reason = decide(c, bundle())
        self.assertEqual(result, RESULT_UNVERIFIED)
        self.assertEqual(reason, REASON_UNSUPPORTED_CAPABILITY)


class WholeSnapshots(unittest.TestCase):
    def test_a_present_snapshot_answers_every_criterion_in_ordinal_order(self):
        criteria = validate_criteria(
            [
                {"kind": "evidence", "predicate": "path_changed", "path": "src/a.py"},
                {"kind": "manual", "description": "somebody looks"},
                {
                    "kind": "evidence",
                    "predicate": "path_operation",
                    "path": "src/b.py",
                    "operation": "created",
                },
            ]
        )
        criteria = tuple(c.with_id("acr_%d" % c.ordinal) for c in criteria)
        snapshot = CriteriaSnapshot(
            task_id="task_x",
            turn_number=1,
            state=CRITERIA_PRESENT,
            snapshot_id="acs_1",
            fingerprint="c" * 64,
            criterion_count=3,
            criteria=criteria,
        )
        found = bundle(
            observations=(
                observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),
                observation("src/b.py", domain=OBSERVATION_DOMAIN_COMMITTED_RANGE, kind=CHANGE_CREATED, index=1),
            )
        )
        results = evaluate(snapshot, found)
        self.assertEqual([r.ordinal for r in results], [1, 2, 3])
        self.assertEqual([r.result for r in results], [RESULT_MET, RESULT_UNVERIFIED, RESULT_MET])
        self.assertEqual([r.criterion_id for r in results], ["acr_1", "acr_2", "acr_3"])

    def test_a_not_provided_snapshot_yields_no_results_and_no_verdict(self):
        snapshot = CriteriaSnapshot(
            task_id="task_x",
            turn_number=1,
            state=CRITERIA_NOT_PROVIDED,
            snapshot_id="acs_1",
            fingerprint="c" * 64,
            criterion_count=0,
        )
        self.assertEqual(evaluate(snapshot, bundle()), ())

    def test_determinism(self):
        c = criterion(predicate="path_changed", path="src/a.py")
        found = bundle(observations=(observation("src/a.py", domain=OBSERVATION_DOMAIN_WORKTREE),))
        first = [decide(c, found) for _ in range(20)]
        self.assertEqual(len(set(first)), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
