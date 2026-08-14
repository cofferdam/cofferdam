"""M2K PR3 — observation completeness, fingerprint sensitivity, and legacy rows.

Three things that are easy to get subtly wrong and hard to notice afterwards:

**Truncation must be durable.** PR2 made *claim* incompleteness a stored fact
because absence has to survive a restart. The machine side owed the same debt:
a bundle that shows no observation at a path must be able to distinguish "we
looked and it was not there" from "we stopped looking". The emitter therefore
writes a coverage row with every observation, and the assembler reads it.

**The fingerprint must bind the new facts.** If `change_kind` did not enter the
hash, the same path observed as `deleted` and as `modified` would fingerprint
alike, and a stored evidence identity would stop identifying the evidence.

**Old rows must still read.** Production holds 99 evidence-bearing events written
before PR3, and assembler v2 has to produce the same answer for them that v1 did:
`operation_agreement: unknown`.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.adapters.claude_code.evidence import (
    GitChange,
    GitObservation,
    git_evidence,
)
from cofferdam.workstation.tasks.claims import CLAIM_MODIFIED, ClaimSubmission
from cofferdam.workstation.tasks.evidence import (
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    LIMIT_OBSERVATIONS_INCOMPLETE,
    OPERATION_UNKNOWN,
    is_coverage_observation,
)
from cofferdam.workstation.tasks.models import (
    CHANGE_CREATED,
    CHANGE_DELETED,
    CHANGE_MODIFIED,
    CHANGE_RENAMED,
    MAX_EVIDENCE_ITEMS,
)
from cofferdam.workstation.tasks.store import TaskStore, _TurnClose

from tests.test_evidence_operations import coverage, legacy_observation, observation


class TheEmitterBudget(unittest.TestCase):
    """The cap arithmetic, which a naive 6 -> 8 change would have broken."""

    def _observation(self, count, head=True):
        return GitObservation(
            is_repository=True,
            branch="main",
            head=("a" * 40) if head else None,
            changes=tuple(
                GitChange(path="f%02d.py" % i, kind=CHANGE_MODIFIED, status="M ")
                for i in range(count)
            ),
            reported_count=count,
            clean=False,
        )

    def test_the_emitted_set_never_exceeds_what_the_store_keeps(self):
        """`_bounded_evidence` silently drops the overflow, so nothing may overflow."""
        for count in (0, 1, 5, 6, 7, 8, 20):
            with self.subTest(count=count):
                refs = git_evidence(self._observation(count))
                self.assertLessEqual(len(refs), MAX_EVIDENCE_ITEMS)

    def test_the_head_row_is_counted_against_the_budget(self):
        with_head = git_evidence(self._observation(20, head=True))
        without = git_evidence(self._observation(20, head=False))
        paths_with = [r for r in with_head if r.evidence_type == "file"]
        paths_without = [r for r in without if r.evidence_type == "file"]
        self.assertEqual(len(paths_with) + 1, len(paths_without))

    def test_a_coverage_row_is_always_present(self):
        """Even when nothing was left out: absence of a row must not be the signal."""
        for count in (0, 1, 20):
            with self.subTest(count=count):
                refs = git_evidence(self._observation(count))
                self.assertEqual(len([r for r in refs if is_coverage_observation(r)]), 1)

    def test_a_complete_observation_says_so(self):
        refs = git_evidence(self._observation(3))
        row = next(r for r in refs if is_coverage_observation(r))
        self.assertEqual(row.result, COVERAGE_COMPLETE)

    def test_a_truncated_observation_says_so(self):
        refs = git_evidence(self._observation(20))
        row = next(r for r in refs if is_coverage_observation(r))
        self.assertEqual(row.result, COVERAGE_PARTIAL)

    def test_a_refused_path_makes_the_observation_partial(self):
        observation_with_refusal = self._observation(2)
        observation_with_refusal.refused_count = 1
        refs = git_evidence(observation_with_refusal)
        row = next(r for r in refs if is_coverage_observation(r))
        self.assertEqual(row.result, COVERAGE_PARTIAL)

    def test_a_clean_tree_still_says_no_files_changed(self):
        clean = GitObservation(is_repository=True, head="a" * 40, changes=(), clean=True)
        refs = git_evidence(clean)
        row = next(r for r in refs if r.evidence_type == "artifact")
        self.assertEqual(row.result, "no files changed")

    def test_every_emitted_reference_is_git_observed(self):
        for r in git_evidence(self._observation(20)):
            self.assertEqual(r.source, "git_observed")
            self.assertTrue(r.verified)


class CoverageFixture(unittest.TestCase):
    def setUp(self):
        from cofferdam.workstation.config import load_config

        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr3-cov-")
        self.addCleanup(self._temp.cleanup)
        self.home = Path(self._temp.name)
        self.root = self.home / "project"
        self.root.mkdir()
        self.database = self.home / "state" / "tasks" / "tasks.sqlite3"
        config = load_config(self.home)
        config.ensure_dirs()
        self.config = config
        self.store = TaskStore(config)
        self.addCleanup(self._close)
        self.task_id = self._task()

    def _close(self):
        try:
            self.store.close()
        except Exception:
            pass

    def _task(self):
        row, _ = self.store.create_task(
            origin="pwa", adapter_id="validation", project_id="s", prompt="p", title="t"
        )
        for state in ("queued", "starting", "running"):
            self.store.transition(
                row.task_id, state, event_type="task_" + state,
                actor="system", source="cofferdam",
            )
        self.store.open_turn(
            row.task_id, provider="validation", source="internal_test",
            started_at="2026-08-14T00:00:00Z",
        )
        return row.task_id

    def observe(self, *refs, text="looked"):
        return self.store.append_event(
            self.task_id, "progress", actor="system", source="cofferdam",
            text=text, evidence=refs,
        )

    def claim(self, *submissions):
        for s in submissions:
            target = self.root / s.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x", encoding="utf-8")
        return self.store.record_change_claims(
            self.task_id, submissions, project_root=self.root, turn_number=1
        )

    def close(self):
        self.store.transition(
            self.task_id, "ready_for_followup", event_type="turn_complete",
            actor="adapter", source="adapter",
            close_turn=_TurnClose(outcome="completed", completed_at="2026-08-14T00:05:00Z"),
        )

    def bundle(self):
        return self.store.evidence_bundle(self.task_id, 1)


class CompletenessInTheBundle(CoverageFixture):
    def test_a_complete_set_is_reported_complete(self):
        self.observe(observation("a.py", CHANGE_MODIFIED), coverage(COVERAGE_COMPLETE))
        self.close()
        bundle = self.bundle()
        self.assertTrue(bundle.machine_observations_complete)
        self.assertNotIn(LIMIT_OBSERVATIONS_INCOMPLETE, bundle.limitations)

    def test_a_partial_set_is_reported_incomplete(self):
        self.observe(observation("a.py", CHANGE_MODIFIED), coverage(COVERAGE_PARTIAL))
        self.close()
        bundle = self.bundle()
        self.assertFalse(bundle.machine_observations_complete)
        self.assertIn(LIMIT_OBSERVATIONS_INCOMPLETE, bundle.limitations)

    def test_the_truncation_signal_survives_a_reopen(self):
        self.observe(observation("a.py", CHANGE_MODIFIED), coverage(COVERAGE_PARTIAL))
        self.close()
        before = self.bundle().to_dict()
        self.store.close()
        self.store = TaskStore(self.config)
        self.assertEqual(self.bundle().to_dict(), before)
        self.assertFalse(self.bundle().machine_observations_complete)

    def test_a_legacy_turn_with_no_coverage_row_defaults_to_complete(self):
        """Pre-PR3 events carry no coverage row. Silence is not a truncation claim.

        The honest default is `complete`: an older build's observation was whole
        as far as it knew, and inventing an incompleteness that was never
        recorded would be as wrong as hiding one that was.
        """
        self.observe(legacy_observation("a.py"))
        self.close()
        bundle = self.bundle()
        self.assertTrue(bundle.machine_observations_complete)
        self.assertNotIn(LIMIT_OBSERVATIONS_INCOMPLETE, bundle.limitations)

    def test_the_coverage_row_is_not_itself_an_observation(self):
        self.observe(observation("a.py", CHANGE_MODIFIED), coverage())
        self.close()
        bundle = self.bundle()
        self.assertEqual([o.path for o in bundle.observations], ["a.py"])

    def test_the_coverage_row_is_not_an_unsupported_shape(self):
        from cofferdam.workstation.tasks.evidence import LIMIT_UNSUPPORTED_OBSERVATION

        self.observe(coverage())
        self.close()
        self.assertNotIn(LIMIT_UNSUPPORTED_OBSERVATION, self.bundle().limitations)


class FingerprintBindsMachineFacts(CoverageFixture):
    def _fingerprint(self):
        """The bundle's own fingerprint, as a caller sees it."""
        return self.bundle().input_fingerprint

    def _neutralised(self):
        """The same inputs with the minted task id replaced by a constant.

        Both sides of every comparison below go through this. An earlier version
        compared a real-task-id fingerprint against a neutralised one, which
        differ *whatever* the machine facts are — so the tests passed even with
        `change_kind` removed from the hash entirely. A mutation run caught it.
        """
        from cofferdam.workstation.tasks.evidence import input_fingerprint

        bundle = self.bundle()
        return input_fingerprint(
            task_id="FIXED",
            turn_number=bundle.turn_number,
            attribution=bundle.turn_attribution,
            bound=self.store.turn_bound(self.task_id, 1),
            claims=bundle.claims,
            observations=bundle.observations,
            ingestion=bundle.ingestion,
            machine_complete=bundle.machine_observations_complete,
        )

    def test_the_change_kind_is_an_input(self):
        """`deleted` and `modified` at one path are different facts."""
        self.observe(observation("a.py", CHANGE_MODIFIED), coverage())
        self.close()
        first = self._neutralised()
        second = _rebuild(self, CHANGE_DELETED)
        self.assertNotEqual(first, second)

    def test_the_previous_path_is_an_input(self):
        self.observe(observation("new.py", CHANGE_RENAMED, previous="old.py"), coverage())
        self.close()
        first = self._neutralised()
        second = _rebuild(self, CHANGE_RENAMED, previous="different.py", path="new.py")
        self.assertNotEqual(first, second)
        # And the control: identical inputs give an identical value, so the
        # inequality above is about `previous_path` and not about the rebuild.
        self.assertEqual(
            first, _rebuild(self, CHANGE_RENAMED, previous="old.py", path="new.py")
        )

    def test_the_completeness_state_is_an_input(self):
        self.observe(observation("a.py", CHANGE_MODIFIED), coverage(COVERAGE_COMPLETE))
        self.close()
        first = self._neutralised()
        second = _rebuild(self, CHANGE_MODIFIED, cover=COVERAGE_PARTIAL)
        self.assertNotEqual(first, second)

    def test_a_legacy_observation_and_a_kinded_one_differ(self):
        self.observe(legacy_observation("a.py"))
        self.close()
        first = self._neutralised()
        second = _rebuild(self, CHANGE_MODIFIED, cover=None)
        self.assertNotEqual(first, second)

    def test_repeated_reads_are_still_identical(self):
        self.observe(observation("a.py", CHANGE_MODIFIED), coverage())
        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="a.py"))
        self.close()
        self.assertEqual(len({self._fingerprint() for _ in range(10)}), 1)

    def test_a_restart_is_still_identical(self):
        self.observe(observation("a.py", CHANGE_MODIFIED), coverage())
        self.close()
        before = self._fingerprint()
        self.store.close()
        self.store = TaskStore(self.config)
        self.assertEqual(self._fingerprint(), before)

    def test_no_absolute_host_path_enters_the_hash(self):
        from cofferdam.workstation.tasks import evidence as module

        self.observe(observation("src/a.py", CHANGE_RENAMED, previous="src/b.py"), coverage())
        self.close()
        recorded = []

        class Recorder(module._Fingerprint):
            def field(self, value):
                recorded.append(value)
                return super().field(value)

        original = module._Fingerprint
        module._Fingerprint = Recorder
        try:
            self.bundle()
        finally:
            module._Fingerprint = original
        blob = " ".join(str(v) for v in recorded)
        self.assertNotIn(str(self.home), blob)
        self.assertNotIn("/home/", blob)
        self.assertNotIn("/tmp/", blob)
        self.assertFalse(any(isinstance(v, str) and v.startswith("/") for v in recorded))
        # And the semantic ones ARE there.
        self.assertIn("src/a.py", recorded)
        self.assertIn("src/b.py", recorded)
        self.assertIn(CHANGE_RENAMED, recorded)


def _rebuild(case, kind, previous=None, path="a.py", cover=COVERAGE_COMPLETE):
    """A fresh store with one differing machine fact, otherwise identical."""
    import tempfile as _tempfile

    from cofferdam.workstation.config import load_config
    from cofferdam.workstation.tasks.evidence import input_fingerprint

    temp = _tempfile.TemporaryDirectory(prefix="m2k-pr3-fp-")
    case.addCleanup(temp.cleanup)
    home = Path(temp.name)
    config = load_config(home)
    config.ensure_dirs()
    store = TaskStore(config)
    case.addCleanup(store.close)
    row, _ = store.create_task(
        origin="pwa", adapter_id="validation", project_id="s", prompt="p", title="t"
    )
    for state in ("queued", "starting", "running"):
        store.transition(row.task_id, state, event_type="task_" + state,
                         actor="system", source="cofferdam")
    store.open_turn(row.task_id, provider="validation", source="internal_test",
                    started_at="2026-08-14T00:00:00Z")
    refs = [observation(path, kind, previous=previous)]
    if cover is not None:
        refs.append(coverage(cover))
    store.append_event(row.task_id, "progress", actor="system", source="cofferdam",
                       text="looked", evidence=tuple(refs))
    store.transition(row.task_id, "ready_for_followup", event_type="turn_complete",
                     actor="adapter", source="adapter",
                     close_turn=_TurnClose(outcome="completed",
                                           completed_at="2026-08-14T00:05:00Z"))
    bundle = store.evidence_bundle(row.task_id, 1)
    # Neutralise the minted task id so exactly one machine fact differs.
    return input_fingerprint(
        task_id="FIXED",
        turn_number=bundle.turn_number,
        attribution=bundle.turn_attribution,
        bound=store.turn_bound(row.task_id, 1),
        claims=bundle.claims,
        observations=bundle.observations,
        ingestion=bundle.ingestion,
        machine_complete=bundle.machine_observations_complete,
    )


class LegacyRowsStillRead(CoverageFixture):
    def test_a_v1_era_evidence_row_deserializes_with_no_machine_semantics(self):
        """The exact JSON a pre-PR3 build wrote, read by this one."""
        from cofferdam.workstation.tasks.store import _evidence_from_json

        raw = json.dumps([
            {
                "evidence_type": "file",
                "source": "git_observed",
                "identifier": "src/legacy.py",
                "operation": "git status",
                "result": "changed",
                "observed_at": "2026-08-01T00:00:00Z",
            }
        ])
        reference = _evidence_from_json(raw)[0]
        self.assertEqual(reference.identifier, "src/legacy.py")
        self.assertIsNone(reference.change_kind)
        self.assertIsNone(reference.previous_identifier)
        self.assertTrue(reference.verified)

    def test_a_reference_without_machine_semantics_serializes_to_the_old_key_set(self):
        """No gratuitous churn in the column for rows that carry nothing new."""
        from cofferdam.workstation.tasks.store import _bounded_evidence

        payload = json.loads(_bounded_evidence((legacy_observation("a.py"),)))
        self.assertEqual(
            sorted(payload[0]),
            ["evidence_type", "identifier", "observed_at", "operation", "result", "source"],
        )

    def test_assembler_v2_gives_legacy_evidence_the_v1_answer(self):
        self.observe(legacy_observation("src/legacy.py"))
        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="src/legacy.py"))
        self.close()
        group = {g.path: g for g in self.bundle().relationships}["src/legacy.py"]
        self.assertEqual(group.operation_agreement, OPERATION_UNKNOWN)
        self.assertEqual(group.observed_kinds, ())
        self.assertTrue(group.path_agreement)

    def test_a_row_written_now_round_trips_through_the_database(self):
        self.observe(observation("new.py", CHANGE_RENAMED, previous="old.py"), coverage())
        self.close()
        self.store.close()
        self.store = TaskStore(self.config)
        found = [o for o in self.bundle().observations if o.path == "new.py"][0]
        self.assertEqual(found.change_kind, CHANGE_RENAMED)
        self.assertEqual(found.previous_path, "old.py")

    def test_an_unknown_future_change_kind_reads_as_no_semantics(self):
        """A value this build does not know is `unknown`, never trusted through."""
        self.observe(observation("a.py", "teleported"), coverage())
        self.close()
        found = [o for o in self.bundle().observations if o.path == "a.py"][0]
        self.assertIsNone(found.change_kind)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
