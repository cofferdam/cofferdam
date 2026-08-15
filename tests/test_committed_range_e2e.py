"""M2K PR5 — one end-to-end pass, isolated home and real Git.

The unit tests each pin one rule. This walks the whole path in order — real
repository, real worker that really commits, real service, real store, real
restart — and asserts the properties that only appear when every layer runs
together against a repository nobody here wrote by hand.

The numbered comments are the milestone's own acceptance points, in order, so a
reader can check the list against the code rather than against a summary.

No provider, no model, no network, no deployment.
"""

from __future__ import annotations

import inspect
import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path

from cofferdam.workstation.tasks import evidence as evidence_module
from cofferdam.workstation.tasks import gitrange as gitrange_module
from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    TaskAdapter,
    TaskContext,
)
from cofferdam.workstation.tasks.claims import CLAIM_CREATED, CLAIM_MODIFIED
from cofferdam.workstation.tasks.evidence import (
    ASSEMBLER_VERSION,
    LIMIT_RANGE_BOUNDARY_NOT_CLEAN,
    LIMIT_RANGE_HISTORY_DIVERGED,
    LIMIT_RANGE_INCOMPLETE,
    OPERATION_AGREED,
    OPERATION_DIFFERS,
    OPERATION_UNKNOWN,
    RANGE_ANCESTRY_DIVERGED,
    RANGE_ANCESTRY_LINEAR,
    RANGE_BOUNDARY_CLEAN,
    RANGE_COVERAGE_COMPLETE,
    RELATIONSHIP_CLAIM_CONFLICT,
)
from cofferdam.workstation.tasks.models import (
    CHANGE_MODIFIED,
    EVENT_COMMITTED_RANGE_OBSERVED,
    EVIDENCE_ARTIFACT,
    EVIDENCE_FILE,
    EVIDENCE_GIT_OBSERVED,
    OBSERVATION_DOMAIN_COMMITTED_RANGE,
    OBSERVATION_DOMAIN_WORKTREE,
    EvidenceReference,
)
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

from ._task_doubles import TaskTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]

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


class WorkerThatCommits(TaskAdapter):
    """Commits on turn one, then commits and leaves the tree dirty on turn two.

    Shaped that way on purpose: turn two produces the same path in **both**
    observation domains, which is the case the bundle must represent without
    letting either fact overwrite the other.
    """

    adapter_id = "worker"
    display_name = "Committing worker"
    description = "A test double."

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            start=True, followup=True, cancel=True, final_result=True
        )

    def available(self) -> bool:
        return True

    def start(self, context: TaskContext) -> AdapterOutcome:
        root = Path(context.project_root)
        (root / "feature.py").write_text("def feature():\n    return 1\n", encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "add the feature")
        return AdapterOutcome(
            events=(AdapterEvent(text="added the feature"),),
            requested_state="ready_for_followup",
            final_result="turn one done",
        )

    def send_followup(self, context: TaskContext, followup: str) -> AdapterOutcome:
        root = Path(context.project_root)
        (root / "feature.py").write_text("def feature():\n    return 2\n", encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "change the feature")
        # And then keep editing, without committing. The same path is now a fact
        # in both domains, at two different moments.
        (root / "feature.py").write_text("def feature():\n    return 3\n", encoding="utf-8")
        return AdapterOutcome(
            events=(AdapterEvent(text="changed the feature again"),),
            requested_state="completed",
            final_result="turn two done",
            # The worktree domain, delivered the way the Claude Code adapter
            # delivers it — through `observations`, where the service keeps the
            # `git_observed` source rather than demoting it.
            observations=(
                EvidenceReference(
                    evidence_type=EVIDENCE_FILE,
                    source=EVIDENCE_GIT_OBSERVED,
                    identifier="feature.py",
                    operation="git status",
                    result="changed",
                    change_kind=CHANGE_MODIFIED,
                    change_status=" M",
                ),
                EvidenceReference(
                    evidence_type=EVIDENCE_ARTIFACT,
                    source=EVIDENCE_GIT_OBSERVED,
                    identifier=None,
                    operation="git status",
                    result="observed all changes",
                ),
            ),
        )

    def cancel(self, context: TaskContext) -> AdapterOutcome:  # pragma: no cover
        return AdapterOutcome(requested_state="cancelled")


@unittest.skipIf(GIT is None, "git is not installed")
class CommittedRangeEndToEnd(TaskTestCase):
    enable_validation_adapter = True
    project_adapters = ("validation", "worker")

    def setUp(self):
        super().setUp()
        git(self.project_root, "init", "-q")
        (self.project_root / "README.md").write_text("start\n", encoding="utf-8")
        git(self.project_root, "add", "-A")
        git(self.project_root, "commit", "-q", "-m", "seed")
        self.install_adapter(WorkerThatCommits())

    def range_events(self, task_id):
        return [
            event
            for event in self.store.events(task_id, limit=200)
            if event.event_type == EVENT_COMMITTED_RANGE_OBSERVED
        ]

    def test_the_whole_path(self):
        # 1. The schema did not move. PR5 persists into the immutable evidence
        #    column that already existed, so there is no migration to roll back.
        # At least, not exactly: each additive bump since this walk was
        # written leaves it true. M2K PR6 took it to 7. The literal pin for
        # the current version lives in `test_task_core.py`.
        self.assertGreaterEqual(SCHEMA_VERSION, 6)

        # 3. A boundary exists before the worker is ever handed control. Captured
        #    here for comparison; PR4 proves the ordering itself.
        h0 = git(self.project_root, "rev-parse", "HEAD")

        # 2, 4, 5. The task starts, the worker commits, and a real turn opens.
        row = self.create(adapter_id="worker")

        # No relational shape was invented for any of it: the observation lives
        # in the immutable evidence column that already existed.
        with sqlite3.connect(str(self.store.path)) as database:
            tables = {
                row_[0]
                for row_ in database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        for invented in (
            "task_committed_ranges", "task_range_observations", "task_turn_git_diffs"
        ):
            self.assertNotIn(invented, tables)

        h1 = git(self.project_root, "rev-parse", "HEAD")
        self.assertNotEqual(h0, h1, "the worker did not commit")
        self.assertIsNotNone(self.store.turn_bound(row.task_id, 1))

        # 6. The observation was taken while the turn was open — proven by the
        #    sequence, not by a timestamp or an ordering convention.
        (event,) = self.range_events(row.task_id)
        bound = self.store.turn_bound(row.task_id, 1)
        self.assertGreater(event.sequence, bound.opened_after_event_sequence)
        self.assertLessEqual(event.sequence, bound.closed_through_event_sequence)
        closing = min(
            e.sequence
            for e in self.store.events(row.task_id, limit=200)
            if e.event_type in ("turn_complete", "task_completed")
        )
        self.assertLess(event.sequence, closing, "the turn closed before the capture")

        # 7. It is immutable history: an ordinary task event, in the append-only
        #    stream, with no update path of its own.
        self.assertEqual(event.actor, "system")
        self.assertEqual(event.source, "cofferdam")

        # 8. The assembler reads it, at version 3.
        bundle = self.store.evidence_bundle(row.task_id, 1)
        self.assertEqual(bundle.assembler_version, 3)
        self.assertEqual(ASSEMBLER_VERSION, 3)
        summary = bundle.committed_range
        self.assertTrue(summary.recorded)
        self.assertEqual(summary.baseline_revision, h0)
        self.assertEqual(summary.target_revision, h1)
        self.assertEqual(summary.ancestry, RANGE_ANCESTRY_LINEAR)
        self.assertEqual(summary.boundary_quality, RANGE_BOUNDARY_CLEAN)
        self.assertEqual(summary.coverage, RANGE_COVERAGE_COMPLETE)
        self.assertTrue(summary.comparison_grade)
        self.assertEqual([o.path for o in bundle.observations], ["feature.py"])
        self.assertEqual(
            bundle.observations[0].domain, OBSERVATION_DOMAIN_COMMITTED_RANGE
        )

        # 9. A matching claim agrees with the committed operation.
        self.store.record_change_claims(
            row.task_id,
            [self._claim(CLAIM_CREATED, "feature.py")],
            project_root=self.project_root,
            turn_number=1,
        )
        group = self._group(row.task_id, 1, "feature.py")
        self.assertEqual(group.operation_agreement, OPERATION_AGREED)

        # 19. The repository is not consulted at assembly time — which is what
        #     makes the bundle historical. Proven by deleting it. (Taken here,
        #     before turn two needs the repository, and restored immediately.)
        before = self.store.evidence_bundle(row.task_id, 1).to_dict()
        moved = self.home / "moved-away"
        shutil.move(str(self.project_root), str(moved))
        try:
            after = self.store.evidence_bundle(row.task_id, 1).to_dict()
            self.assertEqual(after, before, "assembly read the repository")
        finally:
            shutil.move(str(moved), str(self.project_root))

        # 12, 13. Turn two commits the same path and then edits it again, so the
        #     path is a fact in both domains. Neither overwrites the other.
        self.service.send_followup(row.task_id, "now change it")
        h2 = git(self.project_root, "rev-parse", "HEAD")

        second = self.store.evidence_bundle(row.task_id, 2)
        self.assertEqual(second.committed_range.baseline_revision, h1)
        self.assertEqual(second.committed_range.target_revision, h2)

        domains = sorted(o.domain for o in second.observations if o.path == "feature.py")
        self.assertEqual(
            domains,
            [OBSERVATION_DOMAIN_COMMITTED_RANGE, OBSERVATION_DOMAIN_WORKTREE],
            "the two domains were merged into one observation",
        )

        # 18. Turn two's evidence cannot reach turn one's bundle.
        first_again = self.store.evidence_bundle(row.task_id, 1)
        self.assertEqual(first_again.committed_range.target_revision, h1)
        self.assertEqual([o.path for o in first_again.observations], ["feature.py"])
        self.assertEqual(
            first_again.observations[0].event_sequence, event.sequence
        )

        # 17. A retry appends nothing.
        self.service._record_committed_range(
            self.store.get(row.task_id), self.project_root, 1
        )
        self.assertEqual(len(self.range_events(row.task_id)), 2)

        # 20, 21. Nothing that decides anything came with it. Checked the way
        #     PR4 checks it: code only, so ordinary prose — "the probe failed" —
        #     does not read as evaluator vocabulary, and then over the module's
        #     own vocabulary constants, where a verdict would actually live.
        from ._task_doubles import python_code_only

        #     `verdict` is on the probe's list and not the assembler's, on
        #     purpose: the assembler has a local named `verdicts` holding the
        #     answer of *one comparison*, which is the word doing honest work.
        #     What neither may have is a judgement about the task.
        for module, forbidden_words in (
            (
                gitrange_module,
                ("verdict", "confidence", "risk", "check_runner", "acceptance",
                 "evaluator", "evaluate"),
            ),
            (
                evidence_module,
                ("confidence", "check_runner", "acceptance", "evaluator",
                 "evaluate", "task_verdict"),
            ),
        ):
            code = python_code_only(inspect.getsource(module)).lower()
            for forbidden in forbidden_words:
                self.assertNotIn(forbidden, code, module.__name__ + ": " + forbidden)
            for name in dir(module):
                value = getattr(module, name)
                if isinstance(value, str):
                    self.assertNotIn(
                        value.lower(), {"pass", "fail", "passed", "failed"},
                        module.__name__ + "." + name,
                    )

    # -- the cases that need their own repository ----------------------------

    def test_a_dirty_boundary_cannot_produce_a_conflict(self):
        """11. The worker inherits somebody else's uncommitted change."""
        (self.project_root / "inherited.py").write_text("someone else\n", encoding="utf-8")

        row = self.create(adapter_id="worker")
        bundle = self.store.evidence_bundle(row.task_id, 1)
        self.assertEqual(bundle.committed_range.boundary_quality, "dirty")
        self.assertFalse(bundle.committed_range.comparison_grade)
        self.assertIn(LIMIT_RANGE_BOUNDARY_NOT_CLEAN, bundle.limitations)

        # A claim that flatly disagrees with the committed operation. On a clean
        # boundary this is a conflict; here it must not be.
        self.store.record_change_claims(
            row.task_id,
            [self._claim(CLAIM_MODIFIED, "feature.py")],
            project_root=self.project_root,
            turn_number=1,
        )
        group = self._group(row.task_id, 1, "feature.py")
        self.assertEqual(group.operation_agreement, OPERATION_UNKNOWN)
        self.assertNotEqual(group.relationship, RELATIONSHIP_CLAIM_CONFLICT)
        self.assertTrue(group.path_agreement, "the change itself is still recorded")

    def test_an_incompatible_operation_on_a_clean_boundary_can_conflict(self):
        """10. The same shape as above, with the boundary clean. It conflicts."""
        row = self.create(adapter_id="worker")
        bundle = self.store.evidence_bundle(row.task_id, 1)
        self.assertEqual(bundle.committed_range.boundary_quality, RANGE_BOUNDARY_CLEAN)

        self.store.record_change_claims(
            row.task_id,
            [self._claim("deleted", "feature.py")],
            project_root=self.project_root,
            turn_number=1,
        )
        group = self._group(row.task_id, 1, "feature.py")
        self.assertEqual(group.operation_agreement, OPERATION_DIFFERS)
        self.assertEqual(group.relationship, RELATIONSHIP_CLAIM_CONFLICT)

    def test_branch_divergence_is_explicit_rather_than_diffed(self):
        """14. The failure that produced a deletion nobody performed."""
        (self.project_root / "second.txt").write_text("second\n", encoding="utf-8")
        git(self.project_root, "add", "-A")
        git(self.project_root, "commit", "-q", "-m", "second")

        class Switcher(WorkerThatCommits):
            adapter_id = "switcher"

            def start(self, context):
                root = Path(context.project_root)
                base = git(root, "rev-list", "--max-parents=0", "HEAD")
                git(root, "checkout", "-q", "-b", "other", base)
                (root / "elsewhere.txt").write_text("e\n", encoding="utf-8")
                git(root, "add", "-A")
                git(root, "commit", "-q", "-m", "elsewhere")
                return AdapterOutcome(
                    events=(AdapterEvent(text="switched"),),
                    requested_state="ready_for_followup",
                    final_result="done",
                )

        self.install_adapter(Switcher())
        row = self.create(adapter_id="switcher")

        bundle = self.store.evidence_bundle(row.task_id, 1)
        self.assertEqual(bundle.committed_range.ancestry, RANGE_ANCESTRY_DIVERGED)
        self.assertFalse(bundle.committed_range.history_valid)
        self.assertIn(LIMIT_RANGE_HISTORY_DIVERGED, bundle.limitations)
        self.assertEqual(
            [o.path for o in bundle.observations], [],
            "a divergence was diffed and published as committed work",
        )

    def test_an_over_budget_range_is_incomplete_and_says_so(self):
        """16. Truncation is recorded, never presented as a complete short list."""

        class Prolific(WorkerThatCommits):
            adapter_id = "prolific"

            def start(self, context):
                root = Path(context.project_root)
                for index in range(gitrange_module.MAX_RANGE_EVIDENCE_PATHS + 3):
                    (root / ("f%02d.py" % index)).write_text("x\n", encoding="utf-8")
                git(root, "add", "-A")
                git(root, "commit", "-q", "-m", "many")
                return AdapterOutcome(
                    events=(AdapterEvent(text="many"),),
                    requested_state="ready_for_followup",
                    final_result="done",
                )

        self.install_adapter(Prolific())
        row = self.create(adapter_id="prolific")

        bundle = self.store.evidence_bundle(row.task_id, 1)
        self.assertEqual(bundle.committed_range.coverage, "incomplete")
        self.assertEqual(
            bundle.committed_range.limitation, gitrange_module.REASON_EVIDENCE_BUDGET
        )
        self.assertIn(LIMIT_RANGE_INCOMPLETE, bundle.limitations)
        self.assertEqual(
            len(bundle.observations), gitrange_module.MAX_RANGE_EVIDENCE_PATHS
        )

    def test_no_bridge_operation_came_with_any_of_it(self):
        """22. The range is readable at the workstation and nowhere else."""
        bridge = REPO_ROOT / "cofferdam" / "actions_bridge"
        if not bridge.is_dir():  # pragma: no cover - layout guard
            self.skipTest("no actions bridge in this layout")
        for path in bridge.rglob("*.py"):
            source = path.read_text("utf-8")
            for forbidden in (
                "committed_range", "range_evidence", "capture_committed_range",
                "gitrange",
            ):
                self.assertNotIn(forbidden, source, str(path) + " names " + forbidden)

    # -- helpers -------------------------------------------------------------

    def _claim(self, operation, path):
        from cofferdam.workstation.tasks.claims import ClaimSubmission

        return ClaimSubmission(operation=operation, path=path)

    def _group(self, task_id, turn, path):
        bundle = self.store.evidence_bundle(task_id, turn)
        for group in bundle.relationships:
            if group.path == path:
                return group
        raise AssertionError("no relationship group for " + path)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
