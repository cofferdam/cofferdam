"""M2K PR5 — when the committed range is observed, and what it is attached to.

The probe itself is proven in ``test_git_range_capture.py``. This file is about
the thing the probe cannot prove on its own: that the observation belongs to a
**real turn**, that it was taken while that turn was still open, and that no
lifecycle path can produce one that belongs somewhere else.

Why the timing is the whole point
---------------------------------

A turn's events are attributed by sequence: ``opened_after < sequence <=
closed_through``. So an observation appended while the turn is open lands inside
that window as a matter of arithmetic, and one appended afterwards does not —
it would have to be *assigned* to a window by a later decision, which is a
different and much weaker kind of record.

``_apply`` is the only method that can close a turn. Both dispatch paths call
``_record_committed_range`` after the turn row exists and before ``_apply`` runs,
holding the same lock throughout, so "still open" is structural rather than a
race that usually goes the right way. These tests read the durable rows back and
check the arithmetic.

What must produce nothing
-------------------------

PR4 records dispatches that never became turns — refused ones, and ones where
Cofferdam learned nothing. PR5 does not invent a turn for either. A range
observation against a boundary with no turn would be a machine fact about a
window that does not exist.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    AdapterRefusal,
    TaskAdapter,
    TaskContext,
)
from cofferdam.workstation.tasks.evidence import (
    RANGE_ANCESTRY_DIVERGED,
    RANGE_ANCESTRY_IDENTICAL,
    RANGE_ANCESTRY_LINEAR,
    RANGE_BOUNDARY_CLEAN,
    RANGE_COVERAGE_COMPLETE,
    RANGE_COVERAGE_UNAVAILABLE,
)
from cofferdam.workstation.tasks.gitbaseline import (
    DISPATCH_REFUSED,
    DISPATCH_STARTED,
    DISPATCH_TURN_OPENED,
)
from cofferdam.workstation.tasks.models import (
    EVENT_COMMITTED_RANGE_OBSERVED,
    OBSERVATION_DOMAIN_COMMITTED_RANGE,
)
from cofferdam.workstation.tasks.store import TaskStore

from ._task_doubles import PROJECT_ID, TaskTestCase

GIT = shutil.which("git")
GIT_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "T",
    "GIT_AUTHOR_EMAIL": "t@e.st",
    "GIT_COMMITTER_NAME": "T",
    "GIT_COMMITTER_EMAIL": "t@e.st",
}


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(root), env=dict(GIT_ENV),
        capture_output=True, check=True, text=True,
    ).stdout.strip()


class CommittingAdapter(TaskAdapter):
    """A worker that really commits, because that is the case PR5 exists for.

    PR3's observation goes blind the moment a worker commits: the work is in
    HEAD, ``git status`` is clean, and the clean answer is correct and useless.
    A double that only writes files would never reach the situation this
    milestone is about, so this one runs Git.
    """

    display_name = "Committing adapter"
    description = "A test double that commits."

    def __init__(self, *, adapter_id: str = "committer", script=(), followup_script=()):
        self.adapter_id = adapter_id
        self._script = tuple(script)
        self._followup_script = tuple(followup_script)
        self.contexts = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            start=True, followup=True, cancel=True, final_result=True
        )

    def available(self) -> bool:
        return True

    def _work(self, root: Path, script) -> None:
        for action in script:
            action(Path(root))

    def start(self, context: TaskContext) -> AdapterOutcome:
        self.contexts.append(context)
        self._work(context.project_root, self._script)
        return AdapterOutcome(
            events=(AdapterEvent(text="worked"),),
            requested_state="ready_for_followup",
            final_result="turn one done",
        )

    def send_followup(self, context: TaskContext, followup: str) -> AdapterOutcome:
        self.contexts.append(context)
        self._work(context.project_root, self._followup_script)
        return AdapterOutcome(
            events=(AdapterEvent(text="worked again"),),
            requested_state="completed",
            final_result="turn two done",
        )

    def cancel(self, context: TaskContext) -> AdapterOutcome:  # pragma: no cover
        return AdapterOutcome(requested_state="cancelled")


def write_and_commit(name: str, body: str = "x\n", message: str = "work"):
    def action(root: Path) -> None:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(body, encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", message)
    return action


def write_only(name: str, body: str = "dirty\n"):
    def action(root: Path) -> None:
        (root / name).write_text(body, encoding="utf-8")
    return action


def switch_to_a_divergent_branch(root: Path) -> None:
    """A branch switch that leaves the recorded baseline off the new history."""
    root_commit = git(root, "rev-list", "--max-parents=0", "HEAD")
    git(root, "checkout", "-q", "-b", "elsewhere", root_commit)
    (root / "other.txt").write_text("other\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "elsewhere")


@unittest.skipIf(GIT is None, "git is not installed")
class RangeLifecycle(TaskTestCase):
    """A real store, a real project repository, and a worker that commits."""

    enable_validation_adapter = True
    project_adapters = ("validation", "committer")

    def setUp(self):
        super().setUp()
        git(self.project_root, "init", "-q")
        (self.project_root / "seed.txt").write_text("seed\n", encoding="utf-8")
        git(self.project_root, "add", "-A")
        git(self.project_root, "commit", "-q", "-m", "seed")

    def install(self, **kwargs) -> CommittingAdapter:
        adapter = CommittingAdapter(**kwargs)
        self.install_adapter(adapter)
        return adapter

    # -- reading the durable record ------------------------------------------

    def range_events(self, task_id: str):
        return [
            event
            for event in self.store.events(task_id, limit=200)
            if event.event_type == EVENT_COMMITTED_RANGE_OBSERVED
        ]

    def range_paths(self, event):
        return sorted(
            reference.identifier
            for reference in event.evidence
            if reference.evidence_type == "file"
        )

    def metadata(self, event):
        return {
            reference.operation: (reference.identifier, reference.result)
            for reference in event.evidence
            if reference.evidence_type in ("commit", "artifact")
        }

    # -- the first turn ------------------------------------------------------

    def test_a_first_turn_records_what_the_worker_committed(self):
        self.install(script=(write_and_commit("added.py", "new\n"),))
        row = self.create(adapter_id="committer")

        (event,) = self.range_events(row.task_id)
        self.assertEqual(self.range_paths(event), ["added.py"])
        (change,) = [r for r in event.evidence if r.evidence_type == "file"]
        self.assertEqual(change.change_kind, "created")
        self.assertEqual(change.domain, OBSERVATION_DOMAIN_COMMITTED_RANGE)
        self.assertEqual(change.source, "git_observed")

    def test_the_observation_falls_inside_the_turns_own_sequence_bounds(self):
        """The arithmetic that makes attribution a fact rather than a decision."""
        self.install(script=(write_and_commit("a.py"),))
        row = self.create(adapter_id="committer")

        (event,) = self.range_events(row.task_id)
        bound = self.store.turn_bound(row.task_id, 1)
        self.assertIsNotNone(bound)
        self.assertGreater(event.sequence, bound.opened_after_event_sequence)
        self.assertLessEqual(event.sequence, bound.closed_through_event_sequence)
        self.assertIn(
            event.sequence,
            [e.sequence for e in self.store.events_in_bound(row.task_id, bound)],
        )

    def test_the_observation_is_appended_before_the_turn_closes(self):
        """Not "usually before" — the sequence numbers say which came first."""
        self.install(script=(write_and_commit("a.py"),))
        row = self.create(adapter_id="committer")

        (event,) = self.range_events(row.task_id)
        closing = [
            e.sequence
            for e in self.store.events(row.task_id, limit=200)
            if e.event_type in ("turn_complete", "task_completed")
        ]
        self.assertTrue(closing)
        self.assertLess(event.sequence, min(closing), "the turn closed first")

    def test_the_range_is_taken_against_the_boundary_the_baseline_recorded(self):
        self.install(script=(write_and_commit("a.py"),))
        before = git(self.project_root, "rev-parse", "HEAD")
        row = self.create(adapter_id="committer")
        after = git(self.project_root, "rev-parse", "HEAD")

        (event,) = self.range_events(row.task_id)
        metadata = self.metadata(event)
        self.assertEqual(metadata["range baseline"][0], before)
        self.assertEqual(metadata["range target"][0], after)
        self.assertEqual(metadata["range baseline"][1], RANGE_BOUNDARY_CLEAN)
        self.assertEqual(metadata["range target"][1], RANGE_ANCESTRY_LINEAR)
        self.assertEqual(metadata["range coverage"][1], RANGE_COVERAGE_COMPLETE)
        self.assertEqual(metadata["range limitation"][1], "none")

    def test_a_worker_that_committed_nothing_is_a_complete_empty_range(self):
        """An honest zero, and not the same fact as an unavailable range."""
        self.install(script=())
        row = self.create(adapter_id="committer")

        (event,) = self.range_events(row.task_id)
        self.assertEqual(self.range_paths(event), [])
        metadata = self.metadata(event)
        self.assertEqual(metadata["range target"][1], RANGE_ANCESTRY_IDENTICAL)
        self.assertEqual(metadata["range coverage"][1], RANGE_COVERAGE_COMPLETE)

    def test_uncommitted_work_is_not_in_the_committed_range(self):
        self.install(script=(write_only("scratch.txt"),))
        row = self.create(adapter_id="committer")

        (event,) = self.range_events(row.task_id)
        self.assertEqual(self.range_paths(event), [])
        self.assertEqual(
            self.metadata(event)["range target"][1], RANGE_ANCESTRY_IDENTICAL
        )

    # -- the follow-up turn --------------------------------------------------

    def test_a_follow_up_turn_gets_its_own_observation_in_its_own_bounds(self):
        self.install(
            script=(write_and_commit("one.py", "1\n", "first"),),
            followup_script=(write_and_commit("two.py", "2\n", "second"),),
        )
        row = self.create(adapter_id="committer")
        self.service.send_followup(row.task_id, "keep going")

        events = self.range_events(row.task_id)
        self.assertEqual(len(events), 2, "each turn takes its own observation")

        first, second = events
        self.assertEqual(self.range_paths(first), ["one.py"])
        self.assertEqual(
            self.range_paths(second), ["two.py"],
            "turn two's range restarted from turn two's own boundary",
        )

        for number, event in ((1, first), (2, second)):
            with self.subTest(turn=number):
                bound = self.store.turn_bound(row.task_id, number)
                self.assertGreater(event.sequence, bound.opened_after_event_sequence)
                self.assertLessEqual(
                    event.sequence, bound.closed_through_event_sequence
                )

    def test_turn_twos_evidence_cannot_reach_turn_ones_bundle(self):
        self.install(
            script=(write_and_commit("one.py", "1\n", "first"),),
            followup_script=(write_and_commit("two.py", "2\n", "second"),),
        )
        row = self.create(adapter_id="committer")
        self.service.send_followup(row.task_id, "keep going")

        first = self.store.evidence_bundle(row.task_id, 1)
        second = self.store.evidence_bundle(row.task_id, 2)
        self.assertEqual([o.path for o in first.observations], ["one.py"])
        self.assertEqual([o.path for o in second.observations], ["two.py"])
        self.assertNotEqual(
            first.committed_range.target_revision,
            second.committed_range.target_revision,
        )

    def test_a_follow_up_that_resumes_a_turn_opens_no_second_range(self):
        """A message that unblocks a running turn is not new work."""
        adapter = CommittingAdapter(adapter_id="asker")
        adapter.start = lambda context: AdapterOutcome(  # type: ignore[assignment]
            events=(AdapterEvent(text="asking"),),
            requested_state="waiting_for_user",
            waiting_reason="clarification",
        )
        self.install_adapter(adapter)
        row = self.create(adapter_id="asker")
        self.assertEqual(self.store.get(row.task_id).state, "waiting_for_user")
        self.service.send_followup(row.task_id, "here is the answer")

        self.assertEqual(
            len(self.range_events(row.task_id)), 1,
            "resuming a turn redrew a boundary the turn was already measured from",
        )

    # -- what must produce nothing -------------------------------------------

    def test_a_refused_dispatch_records_no_range(self):
        class Refusing(CommittingAdapter):
            def start(self, context):
                raise AdapterRefusal("no")

        self.install_adapter(Refusing(adapter_id="refuser"))
        self.project_adapters = ("validation", "committer", "refuser")
        row = self.create(adapter_id="refuser")

        self.assertEqual(self.range_events(row.task_id), [])
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(row.task_id, 1), DISPATCH_REFUSED
        )

    def test_a_dispatch_that_never_produced_a_turn_records_no_range(self):
        """The crash PR4 exists to survive: a boundary with no turn behind it.

        Driven through the service's own entry point with the durable state a
        crash would leave, rather than by killing a process — the gate being
        tested is the ``dispatch_state`` check, and this is exactly the state it
        is asked about.
        """
        self.install(script=(write_and_commit("a.py"),))
        row = self.create(adapter_id="committer")
        before = len(self.range_events(row.task_id))

        # Turn 2's boundary, frozen at dispatch and never bound to a turn.
        self.store.reserve_turn_baseline(
            row.task_id, self.store.turn_baseline(row.task_id, 1), captured_at="now"
        )
        self.store.mark_baseline_dispatch_started(row.task_id, 2)
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(row.task_id, 2), DISPATCH_STARTED
        )

        self.service._record_committed_range(
            self.store.get(row.task_id), self.project_root, 2
        )
        self.assertEqual(
            len(self.range_events(row.task_id)), before,
            "a boundary with no turn produced a range observation",
        )

    def test_a_turn_that_exists_is_the_only_thing_observed(self):
        self.install(script=(write_and_commit("a.py"),))
        row = self.create(adapter_id="committer")
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(row.task_id, 1),
            DISPATCH_TURN_OPENED,
        )
        self.assertEqual(len(self.range_events(row.task_id)), 1)

    # -- durability and retries ----------------------------------------------

    def test_the_observation_survives_a_restart_unchanged(self):
        self.install(script=(write_and_commit("kept.py"),))
        row = self.create(adapter_id="committer")
        (before,) = self.range_events(row.task_id)

        self.store.close()
        self.store = TaskStore(self.config)
        self.addCleanup(self.store.close)
        (after,) = self.range_events(row.task_id)

        self.assertEqual(after.sequence, before.sequence)
        self.assertEqual(
            [r.to_dict() for r in after.evidence],
            [r.to_dict() for r in before.evidence],
        )

    def test_a_retry_of_the_capture_appends_no_second_observation(self):
        self.install(script=(write_and_commit("a.py"),))
        row = self.create(adapter_id="committer")
        (first,) = self.range_events(row.task_id)

        for _ in range(3):
            self.service._record_committed_range(
                self.store.get(row.task_id), self.project_root, 1
            )

        events = self.range_events(row.task_id)
        self.assertEqual(len(events), 1, "a retry duplicated the observation")
        self.assertEqual(events[0].sequence, first.sequence)

    def test_the_duplicate_guard_reads_the_database_not_memory(self):
        """Which is what makes it survive the restart a retry usually follows."""
        self.install(script=(write_and_commit("a.py"),))
        row = self.create(adapter_id="committer")

        self.store.close()
        self.store = TaskStore(self.config)
        self.addCleanup(self.store.close)
        service = self.restart()
        service._record_committed_range(
            service._store.get(row.task_id), self.project_root, 1
        )
        self.assertEqual(len(self.range_events(row.task_id)), 1)

    # -- history that does not hold ------------------------------------------

    def test_a_branch_switch_is_recorded_as_diverged_and_diffs_nothing(self):
        """The empirically proven failure: a tree diff inventing a deletion."""
        # A second commit, so the boundary the task records is *not* the root —
        # otherwise every branch cut from the root still descends from it and the
        # history would legitimately be linear.
        (self.project_root / "b.txt").write_text("b\n", encoding="utf-8")
        git(self.project_root, "add", "-A")
        git(self.project_root, "commit", "-q", "-m", "second")

        self.install(script=(switch_to_a_divergent_branch,))
        row = self.create(adapter_id="committer")

        (event,) = self.range_events(row.task_id)
        metadata = self.metadata(event)
        self.assertEqual(metadata["range target"][1], RANGE_ANCESTRY_DIVERGED)
        self.assertEqual(metadata["range coverage"][1], RANGE_COVERAGE_UNAVAILABLE)
        self.assertEqual(metadata["range limitation"][1], "history_diverged")
        self.assertEqual(
            self.range_paths(event), [],
            "a diverged history produced paths labelled as committed work",
        )

    def test_a_project_that_is_not_a_repository_is_explicitly_unavailable(self):
        shutil.rmtree(self.project_root / ".git")
        self.install(script=())
        row = self.create(adapter_id="committer")

        (event,) = self.range_events(row.task_id)
        metadata = self.metadata(event)
        self.assertEqual(metadata["range coverage"][1], RANGE_COVERAGE_UNAVAILABLE)
        self.assertNotEqual(
            metadata["range coverage"][1], RANGE_COVERAGE_COMPLETE,
            "an unreadable repository reported a complete range of zero changes",
        )

    # -- what never enters the record ----------------------------------------

    def test_the_observation_carries_no_host_path_and_no_file_content(self):
        secret = "SUPER-SECRET-RANGE-BODY"
        self.install(script=(write_and_commit("confidential.py", secret + "\n"),))
        row = self.create(adapter_id="committer")

        (event,) = self.range_events(row.task_id)
        blob = repr([r.to_dict() for r in event.evidence])
        self.assertNotIn(secret, blob)
        self.assertNotIn(str(self.project_root), blob)
        self.assertNotIn(str(self.home), blob)

    def test_the_event_writes_no_activity_text(self):
        """Cofferdam's bookkeeping does not overwrite the task's own last word."""
        self.install(script=(write_and_commit("a.py"),))
        row = self.create(adapter_id="committer")

        (event,) = self.range_events(row.task_id)
        self.assertIsNone(event.text)
        activity = self.store.get(row.task_id).latest_activity
        self.assertIsNotNone(activity)
        for word in ("range", "committed", "git", "diff"):
            self.assertNotIn(word, activity.lower(), activity)


class MachineSemanticsSurviveTheServicePath(TaskTestCase):
    """A defect PR3 shipped, found while PR5 was being written.

    ``TaskService._apply`` rebuilds every observation reference field by field
    before storing it. PR3 added three fields to ``EvidenceReference`` —
    ``change_kind``, ``previous_identifier``, ``change_status`` — and that
    reconstruction was never extended, so it kept copying the original six. The
    emitter produced the semantics, the column could hold them, the assembler
    knew how to read them, and the service dropped them in between.

    Nothing failed. Every observation that reached the database through an
    adapter simply arrived shaped exactly like a pre-PR3 one, which the assembler
    correctly reads as "the operation was never established". So
    ``operation_agreement`` was permanently ``unknown`` and ``claim_conflict``
    was unreachable on the only path a real task takes — the store-level tests
    write their evidence directly and never went through here.

    The tests below go through the service, which is the whole point of them.
    """

    def observed(self, **kwargs):
        from cofferdam.workstation.tasks.models import EvidenceReference

        defaults = dict(
            evidence_type="file",
            source="git_observed",
            identifier="src/foo.py",
            operation="git status",
            result="changed",
        )
        defaults.update(kwargs)
        return EvidenceReference(**defaults)

    def run_with(self, *observations):
        from ._task_doubles import ScriptedAdapter
        from cofferdam.workstation.tasks.adapters.protocol import (
            AdapterEvent,
            AdapterOutcome,
        )

        adapter = ScriptedAdapter(
            adapter_id="observer",
            start_outcome=AdapterOutcome(
                events=(AdapterEvent(text="worked"),),
                observations=observations,
                requested_state="running",
            ),
        )
        self.install_adapter(adapter)
        row = self.create(adapter_id="observer")
        return [
            reference
            for event in self.store.events(row.task_id, limit=200)
            for reference in (event.evidence or ())
            if reference.source == "git_observed" and reference.identifier == "src/foo.py"
        ]

    def test_the_change_kind_reaches_the_database(self):
        (stored,) = self.run_with(self.observed(change_kind="deleted"))
        self.assertEqual(stored.change_kind, "deleted")

    def test_the_exact_status_reaches_the_database(self):
        """Which is what carries the second fact a composite proves."""
        (stored,) = self.run_with(self.observed(change_kind="renamed", change_status="RM"))
        self.assertEqual(stored.change_status, "RM")

    def test_the_rename_source_reaches_the_database(self):
        (stored,) = self.run_with(
            self.observed(change_kind="renamed", previous_identifier="src/old.py")
        )
        self.assertEqual(stored.previous_identifier, "src/old.py")

    def test_an_adapter_cannot_claim_the_committed_range_domain(self):
        """The promotion `source` is gated against, one field along.

        An adapter that set this would be dressing its own observation as the
        host's post-work Git reading — which decides whether a claim may be
        contradicted. The domain is not carried from the adapter at all; it is
        set by the channel.
        """
        (stored,) = self.run_with(
            self.observed(change_kind="deleted", domain=OBSERVATION_DOMAIN_COMMITTED_RANGE)
        )
        self.assertEqual(stored.domain, "worktree")
        self.assertNotEqual(stored.domain, OBSERVATION_DOMAIN_COMMITTED_RANGE)

    def test_a_claim_still_cannot_arrive_as_an_observation(self):
        """The older gate, re-asserted beside the new one."""
        (stored,) = self.run_with(
            self.observed(source="adapter_reported", change_kind="created")
        ) or (None,)
        self.assertIsNone(stored, "an adapter_reported row was stored as git_observed")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
