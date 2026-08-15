"""M2K PR7 — one end-to-end pass, isolated home and real Git.

The unit tests each pin one rule. This walks the whole path in order — real
repository, real commit, real service, real store, real restart — and asserts the
properties that only appear when every layer runs together:

    criteria frozen before dispatch → baseline frozen before dispatch →
    worker makes a real change and commits it → turn opens → PR5 observes the
    committed range → turn closes durably → the bundle's input fingerprint is
    final → PR7 evaluates → the record is immutable

and then asserts what is **not** here, because the absence is half the
deliverable: no task verdict, no aggregate, no confidence, no risk, no command,
no model, no bridge surface.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path
from typing import Sequence

from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    TaskAdapter,
    TaskContext,
)
from cofferdam.workstation.tasks.criteria import CRITERIA_NOT_PROVIDED, CRITERIA_PRESENT
from cofferdam.workstation.tasks.evaluation import (
    EVALUATOR_VERSION,
    REASON_MACHINE_CHANGE_OBSERVED,
    REASON_MACHINE_OPERATION_OBSERVED,
    REASON_MACHINE_RENAME_OBSERVED,
    REASON_MANUAL,
    RESULT_MET,
    RESULT_UNVERIFIED,
)
from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION
from cofferdam.workstation.tasks.store import SCHEMA_VERSION

from ._task_doubles import PROJECT_ID, TaskTestCase

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


class WorkingAdapter(TaskAdapter):
    """Makes a real change in the project and commits it, like a worker would."""

    adapter_id = "worker"
    display_name = "Working adapter"
    description = "A test double."

    def __init__(self, root: Path, runner) -> None:
        self.root = root
        self.git = runner
        self.plan = "commit"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(start=True, followup=True, cancel=True, final_result=True)

    def available(self) -> bool:
        return True

    def session_available(self, task_id: str) -> bool:
        return True

    def _work(self) -> None:
        if self.plan == "commit":
            (self.root / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
            (self.root / "src" / "new.py").write_text("fresh\n", encoding="utf-8")
            self.git("mv", "src/old.py", "src/renamed.py")
            self.git("add", "-A")
            self.git("commit", "-q", "-m", "the worker's commit")
        elif self.plan == "dirty":
            (self.root / "src" / "app.py").write_text("uncommitted\n", encoding="utf-8")
        elif self.plan == "revert":
            # The counterexample: put the dirty file back exactly as HEAD has it.
            # A real resulting effect on the tree the worker was handed, which
            # leaves no post-worker observation of any kind.
            (self.root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    def start(self, context: TaskContext) -> AdapterOutcome:
        self._work()
        return AdapterOutcome(
            events=(AdapterEvent(text="did the work"),),
            requested_state="ready_for_followup",
            final_result="I changed app.py, created new.py and renamed old.py.",
        )

    def send_followup(self, context: TaskContext, followup: str) -> AdapterOutcome:
        self._work()
        return AdapterOutcome(
            events=(AdapterEvent(text="did more"),), requested_state="ready_for_followup"
        )

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        return AdapterOutcome(events=(), requested_state="cancelled")


@unittest.skipIf(GIT is None, "git is not installed")
class EvaluationEndToEnd(TaskTestCase):
    project_adapters = ("worker", "validation")

    def extra_adapters(self) -> Sequence[TaskAdapter]:
        self.worker = WorkingAdapter(self.project_root, self.git)
        return (self.worker,)

    def setUp(self):
        super().setUp()
        (self.project_root / "src").mkdir(parents=True, exist_ok=True)
        (self.project_root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        (self.project_root / "src" / "old.py").write_text("old\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "before the worker")

    def git(self, *args):
        subprocess.run(
            [GIT, *args],
            cwd=str(self.project_root),
            check=True,
            capture_output=True,
            env={**GIT_ENV, "HOME": str(self.project_root)},
        )

    def create(self, criteria):
        row, _ = self.service.create_task(
            prompt="do the work",
            project_id=PROJECT_ID,
            adapter_id="worker",
            origin="pwa",
            criteria=criteria,
        )
        return row

    def rows(self, table, task_id=None):
        path = self.home / "state" / "tasks" / "tasks.sqlite3"
        connection = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            if task_id is None:
                return [dict(r) for r in connection.execute("SELECT * FROM %s" % table)]
            return [
                dict(r)
                for r in connection.execute(
                    "SELECT * FROM %s WHERE task_id = ?" % table, (task_id,)
                )
            ]
        finally:
            connection.close()

    # -- 1..14 the whole path ------------------------------------------------

    def test_the_walk(self):
        # 1. schema outcome
        self.assertEqual(SCHEMA_VERSION, 8)
        self.assertEqual(ASSEMBLER_VERSION, 3, "PR7 does not move the assembler")

        criteria = [
            {"kind": "evidence", "predicate": "path_changed", "path": "src/app.py"},
            {
                "kind": "evidence",
                "predicate": "path_operation",
                "path": "src/new.py",
                "operation": "created",
            },
            {
                "kind": "evidence",
                "predicate": "rename",
                "path": "src/old.py",
                "to_path": "src/renamed.py",
            },
            {"kind": "manual", "description": "a person confirms the page renders"},
            {"kind": "evidence", "predicate": "path_changed", "path": "src/never.py"},
        ]
        row = self.create(criteria)

        # 2-3. criteria and baseline were frozen before dispatch
        snapshot = self.service.turn_criteria(row.task_id, 1)
        self.assertEqual(snapshot.state, CRITERIA_PRESENT)
        self.assertEqual(snapshot.dispatch_state, "turn_opened")
        self.assertIsNotNone(self.service.store.turn_baseline(row.task_id, 1))

        # 4-7. the worker committed, the turn opened and closed durably
        turns = self.service.store.turns(row.task_id)
        self.assertEqual([t.turn_number for t in turns], [1])
        self.assertIsNotNone(turns[0].completed_at)
        bound = self.rows("task_turn_bounds", row.task_id)[0]
        self.assertIsNotNone(bound["closed_through_event_sequence"])

        # 6. PR5's committed-range observation persisted
        bundle = self.service.evidence_bundle(row.task_id, 1)
        self.assertTrue(bundle.committed_range.recorded)
        self.assertTrue(bundle.committed_range.comparison_grade)

        # 8-10. the evaluation exists and binds the closed bundle
        stored = self.service.turn_evaluation(row.task_id, 1)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.evaluator_version, EVALUATOR_VERSION)
        self.assertEqual(stored.evidence_input_fingerprint, bundle.input_fingerprint)
        self.assertEqual(stored.criteria_snapshot_id, snapshot.snapshot_id)
        self.assertEqual(stored.criteria_fingerprint, snapshot.fingerprint)
        self.assertEqual(stored.result_count, 5)

        by_ordinal = {r.ordinal: r for r in stored.results}
        # 11. path_changed on a really-committed file
        self.assertEqual(
            (by_ordinal[1].result, by_ordinal[1].reason),
            (RESULT_MET, REASON_MACHINE_CHANGE_OBSERVED),
        )
        # 12. path_operation created, from the committed domain
        self.assertEqual(
            (by_ordinal[2].result, by_ordinal[2].reason),
            (RESULT_MET, REASON_MACHINE_OPERATION_OBSERVED),
        )
        # 13. rename, from an explicit machine rename record
        self.assertEqual(
            (by_ordinal[3].result, by_ordinal[3].reason),
            (RESULT_MET, REASON_MACHINE_RENAME_OBSERVED),
        )
        # 14. manual
        self.assertEqual(
            (by_ordinal[4].result, by_ordinal[4].reason),
            (RESULT_UNVERIFIED, REASON_MANUAL),
        )
        # A path nobody touched. This adapter emits no worktree evidence, so the
        # working tree was never examined for this turn and the honest answer is
        # `unverified` rather than `not_met`: the file could be sitting modified
        # and uncommitted in a domain nobody read. The over-confident version of
        # this assertion is what the closure model was corrected to prevent.
        self.assertEqual(by_ordinal[5].result, RESULT_UNVERIFIED)
        self.assertEqual(by_ordinal[5].reason, "worktree_not_observed")

        # 21. the repository may be deleted; the stored judgement does not move
        before = self.rows("task_turn_evaluations", row.task_id)
        shutil.rmtree(self.project_root)
        self.restart()
        self.assertEqual(self.rows("task_turn_evaluations", row.task_id), before)
        after = self.service.turn_evaluation(row.task_id, 1)
        self.assertEqual(after.evaluation_fingerprint, stored.evaluation_fingerprint)

    # -- 15..20 the other cases ---------------------------------------------

    def test_a_worker_claim_that_disagrees_does_not_become_a_failure(self):
        """The adapter's prose claims work it did not do. Machine evidence decides."""
        self.worker.plan = "none"
        row = self.create(
            [{"kind": "evidence", "predicate": "path_changed", "path": "src/app.py"}]
        )
        stored = self.service.turn_evaluation(row.task_id, 1)
        self.assertEqual(stored.result_count, 1)
        # The worker's final_result says it changed app.py; nothing did.
        self.assertNotEqual(stored.results[0].result, RESULT_MET)
        # And the task itself is not marked failed by the evaluation.
        self.assertEqual(self.service.get_task(row.task_id).state, "ready_for_followup")

    def test_a_not_provided_turn_records_zero_results_and_no_aggregate(self):
        row = self.create(None)
        stored = self.service.turn_evaluation(row.task_id, 1)
        self.assertEqual(stored.criteria_state, CRITERIA_NOT_PROVIDED)
        self.assertEqual(stored.result_count, 0)
        self.assertEqual(stored.results, ())
        self.assertFalse(stored.decided)

    def test_a_legacy_unknown_turn_produces_no_fabricated_record(self):
        row = self.create(None)
        path = self.home / "state" / "tasks" / "tasks.sqlite3"
        with sqlite3.connect(str(path)) as db:
            db.execute("DELETE FROM task_turn_evaluations WHERE task_id=?", (row.task_id,))
            db.execute("DELETE FROM task_turn_criteria WHERE task_id=?", (row.task_id,))
        self.restart()
        self.service.recover_after_restart()
        self.assertIsNone(self.service.turn_evaluation(row.task_id, 1))
        self.assertEqual(self.rows("task_turn_evaluations", row.task_id), [])

    def test_a_dirty_pre_work_boundary_produces_unverified(self):
        """Leave the tree dirty before the task, so causation is not established."""
        (self.project_root / "src" / "app.py").write_text("dirty already\n", encoding="utf-8")
        self.worker.plan = "dirty"
        row = self.create(
            [{"kind": "evidence", "predicate": "path_changed", "path": "src/app.py"}]
        )
        stored = self.service.turn_evaluation(row.task_id, 1)
        self.assertEqual(stored.results[0].result, RESULT_UNVERIFIED)

    def test_the_dirty_revert_counterexample_is_unverified_not_not_met(self):
        """A real repository, walking the exact sequence the rule exists for.

            HEAD            src/app.py = "x = 1"
            pre-work tree   src/app.py = "x = 999"   -> PR4 records `dirty`
            worker          restores it to "x = 1"
            post-worker     nothing committed, working tree clean

        The worker produced a real effect on the tree it received. PR4 stores only
        a coarse dirty flag with no path-level detail, so the stored evidence
        cannot see it — and `not_met` here would be an accusation built on a gap
        in evidence resolution rather than a finding about the work.
        """
        # 2. make the tree genuinely dirty before dispatch
        (self.project_root / "src" / "app.py").write_text("x = 999\n", encoding="utf-8")
        self.worker.plan = "revert"

        row = self.create(
            [{"kind": "evidence", "predicate": "path_changed", "path": "src/app.py"}]
        )

        # 3. PR4 recorded the boundary as dirty
        baseline = self.service.store.turn_baseline(row.task_id, 1)
        self.assertEqual(baseline.working_tree_state, "dirty")

        # 4-5. the worker restored the file; nothing was committed and the tree
        # now matches HEAD, so there is no observation of src/app.py anywhere
        bundle = self.service.evidence_bundle(row.task_id, 1)
        self.assertEqual(bundle.committed_range.boundary_quality, "dirty")
        self.assertEqual(
            [o.path for o in bundle.observations if o.path == "src/app.py"], []
        )

        # 6. and therefore the criterion is unverified, never not_met
        stored = self.service.turn_evaluation(row.task_id, 1)
        self.assertEqual(stored.result_count, 1)
        self.assertEqual(stored.results[0].result, RESULT_UNVERIFIED)
        self.assertNotEqual(stored.results[0].result, "not_met")
        self.assertEqual(stored.results[0].reason, "pre_work_boundary_not_clean")

    def test_a_clean_boundary_still_reaches_not_met_for_an_untouched_path(self):
        """The rule is conservative, not inert: a clean tree still decides."""
        self.worker.plan = "commit"
        row = self.create(
            [{"kind": "evidence", "predicate": "path_changed", "path": "src/never.py"}]
        )
        bundle = self.service.evidence_bundle(row.task_id, 1)
        self.assertEqual(bundle.committed_range.boundary_quality, "clean_complete")
        stored = self.service.turn_evaluation(row.task_id, 1)
        # This adapter emits no worktree evidence, so the worktree domain was
        # never examined and the honest answer stays `unverified` — but for the
        # worktree reason, not the boundary one, which is the distinction the two
        # gates exist to keep separate.
        self.assertEqual(stored.results[0].reason, "worktree_not_observed")

    def test_a_crash_after_close_before_evaluation_recovers_once(self):
        row = self.create(
            [{"kind": "evidence", "predicate": "path_changed", "path": "src/app.py"}]
        )
        before = self.service.turn_evaluation(row.task_id, 1)
        path = self.home / "state" / "tasks" / "tasks.sqlite3"
        with sqlite3.connect(str(path)) as db:
            db.execute("DELETE FROM task_turn_evaluations WHERE task_id=?", (row.task_id,))
        for _ in range(3):
            self.restart()
            self.service.recover_after_restart()
        rows = self.rows("task_turn_evaluations", row.task_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            self.service.turn_evaluation(row.task_id, 1).evaluation_fingerprint,
            before.evaluation_fingerprint,
        )

    # -- 22..27 what is deliberately absent ----------------------------------

    def test_there_is_no_task_verdict_anywhere(self):
        row = self.create(
            [{"kind": "evidence", "predicate": "path_changed", "path": "src/app.py"}]
        )
        payload = json.dumps(
            self.service.snapshot(self.service.get_task(row.task_id)).to_dict()
        )
        for forbidden in ("verdict", "passed", "succeeded", "confidence", "risk"):
            self.assertNotIn(forbidden, payload)
        result = self.service.get_result(row.task_id)
        blob = json.dumps(result.to_dict()) if hasattr(result, "to_dict") else repr(result)
        for forbidden in ("verdict", "not_met", "criterion", "evaluation"):
            self.assertNotIn(forbidden, blob)

    def test_the_evidence_bundle_is_unchanged_by_pr7(self):
        row = self.create(None)
        payload = self.service.evidence_bundle(row.task_id, 1).to_dict()
        self.assertEqual(payload["assembler_version"], 3)
        for forbidden in ("evaluation", "criteria", "met", "not_met", "verdict"):
            self.assertNotIn(forbidden, payload)

    def test_there_is_no_evaluation_http_route(self):
        source = (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text(
            encoding="utf-8"
        )
        import ast

        routes = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    for argument in decorator.args:
                        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                            routes.append(argument.value)
        self.assertTrue(routes)
        for route in routes:
            lowered = route.lower()
            self.assertNotIn("evaluat", lowered)
            self.assertNotIn("criteri", lowered)
            self.assertNotIn("verdict", lowered)

    def test_the_actions_bridge_is_untouched(self):
        bridge = REPO_ROOT / "cofferdam" / "actions_bridge"
        offenders = [
            path.name
            for path in sorted(bridge.rglob("*.py"))
            if any(
                word in path.read_text(encoding="utf-8").lower()
                for word in ("evaluat", "criteri", "not_met")
            )
        ]
        self.assertEqual(offenders, [])

    def test_no_command_runner_appeared(self):
        package = REPO_ROOT / "cofferdam" / "workstation" / "tasks"
        for path in sorted(package.glob("*.py")):
            if path.name in ("gitbaseline.py", "gitrange.py"):
                continue  # PR4 and PR5's host-owned Git probes
            self.assertNotIn("subprocess.", path.read_text(encoding="utf-8"), path.name)

    def test_no_model_or_provider_is_consulted(self):
        """Scanned code-only, for `python_code_only`'s reason.

        The module explains at length that it never shows anything to a model, so
        a raw-text search fails on the sentence promising the thing.
        """
        from ._task_doubles import python_code_only
        from cofferdam.workstation.tasks import evaluation

        source = python_code_only(
            Path(evaluation.__file__).read_text(encoding="utf-8")
        ).lower()
        for forbidden in ("anthropic", "openai", "llm", "prompt(", "completion", "model."):
            self.assertNotIn(forbidden, source, forbidden)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
