"""M2K PR26 — the committed range is measured when the *worker* finishes, not when the *call* returns.

PR25 fixed the final-state observation and left this one behind, on an argument
that was half true and is quoted here because the half that was false is the
whole defect::

    PR5 measures a boundary that is genuinely fixed by now (the baseline
    revision was frozen before dispatch), while PR14 measures the *worker's*
    effect and the worker may not have had one yet.

A range has **two** revisions. The baseline really is frozen before dispatch.
The target is ``HEAD``, and for an asynchronous adapter ``start`` returning means
the worker has not committed to it yet — so PR5 recorded::

    baseline == target, ancestry = identical, coverage = complete, no paths

Every field of that is a true statement about the two revisions it names, and
the whole of it is a false statement about the turn.

Why it was worse than the final-state defect
--------------------------------------------

PR14's premature observation said ``absent``, which made a criterion
``not_met`` — bad, and reachable only for state predicates, which did not exist
when PR5 shipped. PR5's premature observation said **``coverage = complete``**,
which is a claim that the committed domain was *fully examined*. PR7 requires
both domains closed before it will conclude an absence, and this closed one of
them on evidence that had not been gathered.

The other domain could not save it, and the reason is exact. PR3's ``worktree``
domain compares the index and working tree against the current ``HEAD``, so **a
worker that commits its work leaves a clean tree and is invisible there**.
Committing is precisely the act that blinds the worktree domain — and it is
precisely the act the dispatch-bound range cannot see. The two domains go blind
at the same instant, for the same reason, on the same turn. The doctrine that
one domain may be incomplete while another still holds authority had nothing
left to stand on, and PR7's honest ``unverified`` collapsed into a confident,
wrong ``not_met``.

:class:`AcceptanceCounterexampleTests` below is that proof, for all three change
predicates. Every one of them answered ``not_met`` on merged main against a
worker that had genuinely done the work and committed it.

What is asserted here
---------------------

* **One owner.** ``_capture_terminal_boundary`` takes the range and the final
  state, in that pinned order, at the transition that durably closes a turn. The
  dispatch paths take neither.
* **Async first turn, follow-up, ``waiting_for_user``, failure, cancellation,
  dispatch refusal** — each boundary pinned by lifecycle, not by label.
* **Exactly once and never overwritten**, across duplicate reconciliation, a
  crash on either side of the capture, restart recovery, and a daemon upgraded
  mid-turn.
* **No version moved.** Not the schema, not the assembler, not the evaluator,
  not the assessment, not the aggregator, and PR25's constants are untouched.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from cofferdam.workstation.config import load_config
from cofferdam.workstation.tasks.acceptance import (
    AGGREGATOR_VERSION,
    OUTCOME_MET,
    OUTCOME_NOT_MET,
)
from cofferdam.workstation.tasks.adapters.claude_code.evidence import (
    git_evidence,
    observe_git,
)
from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterOutcome,
    AdapterRefusal,
    TaskAdapter,
)
from cofferdam.workstation.tasks.binding import CURRENT_ASSESSMENT_VERSION
from cofferdam.workstation.tasks.delegated import ClarificationRequest
from cofferdam.workstation.tasks.evaluation import (
    EVALUATOR_VERSION,
    REASON_COMPLETE_CHANGE_ABSENT,
    REASON_MACHINE_CHANGE_OBSERVED,
    REASON_MACHINE_OPERATION_OBSERVED,
    REASON_MACHINE_RENAME_OBSERVED,
    REASON_RANGE_NOT_RECORDED,
    RESULT_MET,
    RESULT_NOT_MET,
    RESULT_UNVERIFIED,
)
from cofferdam.workstation.tasks.evidence import (
    ASSEMBLER_VERSION,
    RANGE_ANCESTRY_IDENTICAL,
    RANGE_ANCESTRY_LINEAR,
    RANGE_BOUNDARY_CLEAN,
    RANGE_COVERAGE_COMPLETE,
)
from cofferdam.workstation.tasks.finalstate import FINAL_STATE_OBSERVER_VERSION
from cofferdam.workstation.tasks.models import (
    EVENT_COMMITTED_RANGE_OBSERVED,
    OBSERVATION_DOMAIN_COMMITTED_RANGE,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_READY_FOR_FOLLOWUP,
    STATE_RUNNING,
    STATE_WAITING_FOR_USER,
)
from cofferdam.workstation.tasks.projects import load_projects
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

PROJECT_ID = "demo"


# -- the deterministic asynchronous adapter -----------------------------------


class AsyncCommitter(TaskAdapter):
    """An adapter whose worker runs *between* calls, under the test's control.

    PR25's fixture, with one addition that is the entire point of this module:
    the terminal report carries the **real** ``git status`` observation, built by
    the same :func:`observe_git` / :func:`git_evidence` pair the Claude Code
    adapter uses in production. Without it the worktree domain is never observed
    at all and PR7 answers ``unverified`` for a reason that has nothing to do
    with the range — which would have hidden the defect rather than proving it.

    There is no thread, no sleep and no clock. ``start`` and ``send_followup``
    return ``running`` and touch nothing; the test calls :meth:`work` to perform
    the worker's actions; the next ``refresh_task`` sees the terminal report.
    """

    adapter_id = "validation"
    display_name = "Async Committer"

    def __init__(
        self, *, terminal=STATE_COMPLETED, on_work=None, on_followup_work=None
    ):
        self._terminal = terminal
        self._on_work = on_work
        self._on_followup_work = on_followup_work
        self.root: Optional[Path] = None
        self._pending: Optional[str] = None
        self.inspect_calls = 0
        self.turns = 0

    def capabilities(self):
        return AdapterCapabilities(
            start=True,
            followup=True,
            cancel=True,
            structured_progress=True,
            final_result=True,
            clarifications=True,
        )

    def available(self):
        return True

    def session_available(self, task_id):
        return True

    def start(self, context):
        self.root = Path(context.project_root)
        self.turns += 1
        return AdapterOutcome(requested_state=STATE_RUNNING)

    def send_followup(self, context, followup):
        self.root = Path(context.project_root)
        self.turns += 1
        return AdapterOutcome(requested_state=STATE_RUNNING)

    def work(self, *, terminal=None, followup=False):
        action = self._on_followup_work if followup else self._on_work
        if action is not None and self.root is not None:
            action(self.root)
        self._pending = terminal if terminal is not None else self._terminal

    def _report(self, context, terminal):
        return AdapterOutcome(
            requested_state=terminal,
            final_result="done" if terminal != STATE_FAILED else None,
            failure_code="worker_failed" if terminal == STATE_FAILED else None,
            failure_message="the worker failed" if terminal == STATE_FAILED else None,
            observations=git_evidence(observe_git(Path(context.project_root))),
        )

    def inspect(self, context):
        self.inspect_calls += 1
        if self._pending is None:
            return AdapterOutcome()
        terminal, self._pending = self._pending, None
        return self._report(context, terminal)

    def cancel(self, context):
        return AdapterOutcome(
            requested_state=STATE_CANCELLED,
            observations=git_evidence(observe_git(Path(context.project_root))),
        )


class SyncCommitter(TaskAdapter):
    """The case PR5 always got right, and which PR26 must not disturb."""

    adapter_id = "validation"
    display_name = "Sync Committer"

    def __init__(self, *, action=None, terminal=STATE_COMPLETED):
        self._action = action
        self._terminal = terminal

    def capabilities(self):
        return AdapterCapabilities(start=True, followup=True, final_result=True)

    def available(self):
        return True

    def session_available(self, task_id):
        return True

    def _run(self, context):
        root = Path(context.project_root)
        if self._action is not None:
            self._action(root)
        return AdapterOutcome(
            requested_state=self._terminal,
            final_result="done",
            observations=git_evidence(observe_git(root)),
        )

    def start(self, context):
        return self._run(context)

    def send_followup(self, context, followup):
        return self._run(context)


class AskingCommitter(AsyncCommitter):
    """Asks a question on the first report, then keeps running the same turn."""

    display_name = "Asking Committer"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._question_pending = True

    def inspect(self, context):
        self.inspect_calls += 1
        if self._question_pending:
            self._question_pending = False
            return AdapterOutcome(
                requested_state=STATE_WAITING_FOR_USER,
                waiting_reason="clarification",
                clarification=ClarificationRequest(
                    question="Which file should I commit?",
                    allows_free_text=True,
                ),
                clarification_token="q-1",
            )
        return super().inspect(context)

    def deliver_clarification_answer(self, context, token, answer):
        return True


# -- the harness --------------------------------------------------------------


class TerminalRangeCase(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.root = self.home / "projects" / PROJECT_ID
        self.root.mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "Test")
        (self.root / "seed.txt").write_text("seed\n", encoding="utf-8")
        # Long enough that `--find-renames` scores a move as a rename rather than
        # as an unrelated add and delete.
        (self.root / "rename-src.txt").write_text(
            "".join("line %d\n" % index for index in range(60)), encoding="utf-8"
        )
        (self.root / "modify-me.txt").write_text("before\n", encoding="utf-8")
        (self.root / "delete-me.txt").write_text("doomed\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")

        config = load_config(self.home)
        config = type(config)(
            **{**config.__dict__, "enable_validation_task_adapter": True}
        )
        config.ensure_dirs()
        (config.config_dir / "task-projects.json").write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "project_id": PROJECT_ID,
                            "display_name": "Demo",
                            "root": str(self.root),
                            "adapters": ["validation"],
                            "enabled": True,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.config = config
        self.store = TaskStore(config)
        self.addCleanup(self.store.close)
        self.store.storage_health()
        self.database = self.store.path

    # -- repository helpers ---------------------------------------------------

    def git(self, *arguments):
        subprocess.run(
            ("git",) + arguments, cwd=self.root, check=True, capture_output=True
        )

    def head(self):
        return subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def commit_all(self, message="worker"):
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    # -- worker actions used by several tests ---------------------------------

    def create_and_commit(self, name="async-change.txt"):
        def action(root):
            (root / name).write_text("done\n", encoding="utf-8")
            self.commit_all()

        return action

    # -- service ---------------------------------------------------------------

    def service(self, adapter):
        module = __import__(
            "cofferdam.workstation.tasks", fromlist=["build_registry"]
        )
        registry = type(module.build_registry(enable_validation_adapter=True))(
            (adapter,)
        )
        return TaskService(
            self.config,
            self.store,
            registry,
            projects=load_projects(self.config, registry.ids()),
        )

    @contextmanager
    def sql(self):
        connection = sqlite3.connect(str(self.database))
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def change_criteria(self, *specs):
        """``("path_changed", "a.txt")`` / ``("path_operation", "a.txt", "created")``."""
        criteria = []
        for spec in specs:
            predicate, path = spec[0], spec[1]
            item = {"kind": "evidence", "predicate": predicate, "path": path}
            if predicate == "path_operation":
                item["operation"] = spec[2]
            elif predicate == "rename":
                item["to_path"] = spec[2]
            criteria.append(item)
        return criteria

    def start(self, adapter, criteria):
        service = self.service(adapter)
        row, _ = service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt="pr26 boundary scenario",
            origin="pwa",
            criteria=criteria,
            continuity={"mode": "root"},
        )
        return service, row

    # -- readers ---------------------------------------------------------------

    def range_events(self, task_id):
        with self.sql() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM task_events WHERE task_id = ? AND event_type = ?"
                    " ORDER BY sequence",
                    (task_id, EVENT_COMMITTED_RANGE_OBSERVED),
                )
            ]

    def span(self, task_id, turn=1):
        return self.store.evidence_bundle(task_id, turn).committed_range

    def committed_paths(self, task_id, turn=1):
        bundle = self.store.evidence_bundle(task_id, turn)
        return sorted(
            item.path
            for item in bundle.observations
            if item.domain == OBSERVATION_DOMAIN_COMMITTED_RANGE and item.path
        )

    def snapshot_id(self, task_id, turn):
        return self.store.turn_criteria(task_id, turn).snapshot_id

    def turn_closed(self, task_id, turn=1):
        with self.sql() as connection:
            row = connection.execute(
                "SELECT completed_at FROM task_turns"
                " WHERE task_id = ? AND turn_number = ?",
                (task_id, turn),
            ).fetchone()
        return row is not None and row["completed_at"] is not None

    def evaluate(self, service, task_id, turn=1):
        """Every PR7 criterion result for a turn, keyed by the path it names."""
        service.evaluate_closed_turns(task_id)
        record = service.turn_evaluation(task_id, turn)
        if record is None:
            return {}
        snapshot = self.store.turn_criteria(task_id, turn)
        paths = {
            criterion.criterion_id: criterion.path
            for criterion in snapshot.criteria
            if criterion.path
        }
        return {
            paths.get(item.criterion_id, item.criterion_id): item
            for item in record.results
        }

    def acceptance(self, service, task_id, turn=1):
        return service.turn_acceptance(task_id, turn)


# -- Stop Gate 2: the acceptance counterexamples ------------------------------


class AcceptanceCounterexampleTests(TerminalRangeCase):
    """The proof that this was a correctness defect and not evidence untidiness.

    Each of these answered ``not_met`` / ``complete_resulting_change_absent`` on
    merged main — the strongest wrong answer PR7 can give, and the one that says
    *we looked everywhere and the work was not done* about a worker that did it
    and committed it.

    Named so that a change reintroducing dispatch-time capture fails *these*
    tests by name and a reader lands on the argument.
    """

    def test_path_changed_is_met_for_an_async_worker_that_commits(self):
        adapter = AsyncCommitter(on_work=self.create_and_commit("async-change.txt"))
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()
        service.refresh_task(row.task_id)

        span = self.span(row.task_id)
        self.assertTrue(span.recorded)
        self.assertEqual(RANGE_ANCESTRY_LINEAR, span.ancestry)
        self.assertEqual(RANGE_COVERAGE_COMPLETE, span.coverage)
        self.assertEqual(RANGE_BOUNDARY_CLEAN, span.boundary_quality)
        self.assertEqual(self.head(), span.target_revision)
        self.assertEqual(["async-change.txt"], self.committed_paths(row.task_id))

        answer = self.evaluate(service, row.task_id)["async-change.txt"]
        self.assertEqual(RESULT_MET, answer.result)
        self.assertEqual(REASON_MACHINE_CHANGE_OBSERVED, answer.reason)
        self.assertEqual(OUTCOME_MET, self.acceptance(service, row.task_id).outcome)

    def test_path_operation_created_is_met_for_an_async_worker_that_commits(self):
        adapter = AsyncCommitter(on_work=self.create_and_commit("async-change.txt"))
        service, row = self.start(
            adapter,
            self.change_criteria(("path_operation", "async-change.txt", "created")),
        )
        adapter.work()
        service.refresh_task(row.task_id)

        answer = self.evaluate(service, row.task_id)["async-change.txt"]
        self.assertEqual(RESULT_MET, answer.result)
        self.assertEqual(REASON_MACHINE_OPERATION_OBSERVED, answer.reason)

    def test_path_operation_modified_is_met_for_an_async_worker_that_commits(self):
        def action(root):
            (root / "modify-me.txt").write_text("after\n", encoding="utf-8")
            self.commit_all()

        adapter = AsyncCommitter(on_work=action)
        service, row = self.start(
            adapter,
            self.change_criteria(("path_operation", "modify-me.txt", "modified")),
        )
        adapter.work()
        service.refresh_task(row.task_id)

        answer = self.evaluate(service, row.task_id)["modify-me.txt"]
        self.assertEqual(RESULT_MET, answer.result)
        self.assertEqual(REASON_MACHINE_OPERATION_OBSERVED, answer.reason)

    def test_path_operation_deleted_is_met_for_an_async_worker_that_commits(self):
        def action(root):
            (root / "delete-me.txt").unlink()
            self.commit_all()

        adapter = AsyncCommitter(on_work=action)
        service, row = self.start(
            adapter,
            self.change_criteria(("path_operation", "delete-me.txt", "deleted")),
        )
        adapter.work()
        service.refresh_task(row.task_id)

        answer = self.evaluate(service, row.task_id)["delete-me.txt"]
        self.assertEqual(RESULT_MET, answer.result)
        self.assertEqual(REASON_MACHINE_OPERATION_OBSERVED, answer.reason)

    def test_rename_is_met_for_an_async_worker_that_commits(self):
        def action(root):
            self.git("mv", "rename-src.txt", "rename-dst.txt")
            self.git("commit", "-qm", "worker")

        adapter = AsyncCommitter(on_work=action)
        service, row = self.start(
            adapter,
            self.change_criteria(("rename", "rename-src.txt", "rename-dst.txt")),
        )
        adapter.work()
        service.refresh_task(row.task_id)

        self.assertEqual(["rename-dst.txt"], self.committed_paths(row.task_id))
        answer = self.evaluate(service, row.task_id)["rename-src.txt"]
        self.assertEqual(RESULT_MET, answer.result)
        self.assertEqual(REASON_MACHINE_RENAME_OBSERVED, answer.reason)

    def test_a_genuine_absence_is_still_not_met(self):
        """The fix must not make ``not_met`` unreachable.

        A worker that reaches a terminal result having committed nothing and left
        nothing in the tree is a complete observation of both domains finding
        nothing, and ``not_met`` is the correct and useful answer.
        """
        adapter = AsyncCommitter(on_work=None)
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "never-touched.txt"))
        )
        adapter.work()
        service.refresh_task(row.task_id)

        span = self.span(row.task_id)
        self.assertEqual(RANGE_ANCESTRY_IDENTICAL, span.ancestry)
        self.assertEqual(RANGE_COVERAGE_COMPLETE, span.coverage)

        answer = self.evaluate(service, row.task_id)["never-touched.txt"]
        self.assertEqual(RESULT_NOT_MET, answer.result)
        self.assertEqual(REASON_COMPLETE_CHANGE_ABSENT, answer.reason)
        self.assertEqual(OUTCOME_NOT_MET, self.acceptance(service, row.task_id).outcome)

    def test_the_uncommitted_worker_still_answers_from_the_worktree_domain(self):
        """The doctrine this defect looked like, and was not.

        A worker that changes a file and does **not** commit leaves the committed
        range legitimately empty, and the worktree domain answers. One domain
        incomplete while another holds authority is the healthy case, and it is
        why the defect could not be assumed from the timing alone: the reason the
        committing worker was different is that committing blinds the *other*
        domain at the same moment.
        """
        adapter = AsyncCommitter(
            on_work=lambda root: (root / "async-change.txt").write_text(
                "done\n", encoding="utf-8"
            )
        )
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()
        service.refresh_task(row.task_id)

        self.assertEqual(RANGE_ANCESTRY_IDENTICAL, self.span(row.task_id).ancestry)
        self.assertEqual([], self.committed_paths(row.task_id))
        answer = self.evaluate(service, row.task_id)["async-change.txt"]
        self.assertEqual(RESULT_MET, answer.result)
        self.assertEqual(REASON_MACHINE_CHANGE_OBSERVED, answer.reason)


# -- the capture owner ---------------------------------------------------------


class CaptureOwnerTests(TerminalRangeCase):
    def test_the_dispatch_paths_record_nothing(self):
        service, row = self.start(
            AsyncCommitter(on_work=self.create_and_commit()),
            self.change_criteria(("path_changed", "async-change.txt")),
        )
        self.assertEqual(STATE_RUNNING, self.store.get(row.task_id).state)
        # The strong form: not an empty range, *no event*. An empty range at
        # dispatch is write-once by `_range_already_recorded` and would block the
        # real measurement the terminal boundary is about to take.
        self.assertEqual([], self.range_events(row.task_id))
        self.assertFalse(self.span(row.task_id).recorded)
        self.assertFalse(self.turn_closed(row.task_id))

    def test_polling_a_still_running_worker_records_nothing(self):
        adapter = AsyncCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        for _ in range(3):
            service.refresh_task(row.task_id)
        self.assertEqual([], self.range_events(row.task_id))
        self.assertEqual(STATE_RUNNING, self.store.get(row.task_id).state)

    def test_the_capture_happens_while_the_turn_is_still_open(self):
        """The v5 bound rule is arithmetic only if the event lands inside it."""
        adapter = AsyncCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()
        service.refresh_task(row.task_id)

        bound = self.store.turn_bound(row.task_id, 1)
        event = self.range_events(row.task_id)[0]
        self.assertLess(bound.opened_after_event_sequence, event["sequence"])
        self.assertLessEqual(event["sequence"], bound.closed_through_event_sequence)

    def test_the_range_is_recorded_before_the_final_state(self):
        """The pinned order, asserted at the durable record rather than the source."""
        adapter = AsyncCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        order = []
        for name in ("_record_committed_range", "_record_final_state"):
            original = getattr(service, name)

            def wrapper(*args, _name=name, _original=original, **kwargs):
                order.append(_name)
                return _original(*args, **kwargs)

            setattr(service, name, wrapper)
        adapter.work()
        service.refresh_task(row.task_id)
        self.assertEqual(["_record_committed_range", "_record_final_state"], order)

    def test_both_observations_are_taken_at_the_same_boundary(self):
        adapter = AsyncCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()
        service.refresh_task(row.task_id)

        self.assertEqual(1, len(self.range_events(row.task_id)))
        self.assertIsNotNone(self.store.turn_final_state(row.task_id, 1))
        # Shared moment, separate records: the range is event evidence and the
        # final state is its own table. Neither is derived from the other.
        self.assertEqual(
            [], [row for row in self.range_events(row.task_id) if row["text"]]
        )


# -- synchronous regression ----------------------------------------------------


class SynchronousRegressionTests(TerminalRangeCase):
    def test_a_synchronous_adapter_is_unchanged(self):
        """PR5's working case: `start` returning really is after the work."""

        def action(root):
            (root / "sync-change.txt").write_text("x\n", encoding="utf-8")
            self.commit_all()

        service, row = self.start(
            SyncCommitter(action=action),
            self.change_criteria(("path_changed", "sync-change.txt")),
        )
        self.assertEqual(1, len(self.range_events(row.task_id)))
        self.assertEqual(RANGE_ANCESTRY_LINEAR, self.span(row.task_id).ancestry)
        self.assertEqual(["sync-change.txt"], self.committed_paths(row.task_id))
        self.assertEqual(
            RESULT_MET, self.evaluate(service, row.task_id)["sync-change.txt"].result
        )


# -- follow-up -----------------------------------------------------------------


class AsyncFollowupTests(TerminalRangeCase):
    def test_the_second_turn_is_measured_from_its_own_baseline(self):
        def first(root):
            (root / "first.txt").write_text("1\n", encoding="utf-8")
            self.commit_all("first")

        def second(root):
            (root / "second.txt").write_text("2\n", encoding="utf-8")
            self.commit_all("second")

        adapter = AsyncCommitter(on_work=first, on_followup_work=second)
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "first.txt"))
        )
        adapter.work(terminal=STATE_READY_FOR_FOLLOWUP)
        service.refresh_task(row.task_id)
        turn_one = self.head()

        service.send_followup(
            row.task_id,
            "now the second one",
            criteria=self.change_criteria(("path_changed", "second.txt")),
            continuity={
                "mode": "extend",
                "predecessor_snapshot_id": self.snapshot_id(row.task_id, 1),
            },
        )
        # The follow-up dispatch measured nothing.
        self.assertEqual(2, adapter.turns)
        self.assertEqual(1, len(self.range_events(row.task_id)))

        adapter.work(followup=True)
        service.refresh_task(row.task_id)

        self.assertEqual(2, len(self.range_events(row.task_id)))
        # Turn two starts where turn one ended: no overlap, no double-counting.
        self.assertEqual(turn_one, self.span(row.task_id, 2).baseline_revision)
        self.assertEqual(self.head(), self.span(row.task_id, 2).target_revision)
        self.assertEqual(["first.txt"], self.committed_paths(row.task_id, 1))
        self.assertEqual(["second.txt"], self.committed_paths(row.task_id, 2))
        self.assertEqual(
            RESULT_MET, self.evaluate(service, row.task_id, 2)["second.txt"].result
        )

    def test_turn_one_evidence_is_untouched_by_turn_two(self):
        def first(root):
            (root / "first.txt").write_text("1\n", encoding="utf-8")
            self.commit_all("first")

        def second(root):
            (root / "first.txt").unlink()
            self.commit_all("second")

        adapter = AsyncCommitter(on_work=first, on_followup_work=second)
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "first.txt"))
        )
        adapter.work(terminal=STATE_READY_FOR_FOLLOWUP)
        service.refresh_task(row.task_id)
        before = self.range_events(row.task_id)

        service.send_followup(
            row.task_id,
            "delete it",
            criteria=self.change_criteria(("path_changed", "first.txt")),
            continuity={
                "mode": "extend",
                "predecessor_snapshot_id": self.snapshot_id(row.task_id, 1),
            },
        )
        adapter.work(followup=True)
        service.refresh_task(row.task_id)

        self.assertEqual(before, self.range_events(row.task_id)[:1])
        self.assertEqual(["first.txt"], self.committed_paths(row.task_id, 1))
        self.assertEqual(["first.txt"], self.committed_paths(row.task_id, 2))


# -- waiting_for_user ----------------------------------------------------------


class WaitingForUserTests(TerminalRangeCase):
    def test_no_range_is_finalized_while_the_turn_waits_for_an_answer(self):
        """A question is a pause inside a turn, not the end of one.

        The turn stays open across the answer, so finalising a range here would
        freeze a mid-work HEAD as the turn's target and then refuse to replace
        it, because the observation is write-once.
        """
        adapter = AskingCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        service.refresh_task(row.task_id)
        self.assertEqual(STATE_WAITING_FOR_USER, self.store.get(row.task_id).state)
        self.assertEqual([], self.range_events(row.task_id))
        self.assertFalse(self.turn_closed(row.task_id))

        pending = service.pending_clarifications(row.task_id)
        self.assertEqual(1, len(pending))
        service.answer_clarification(
            row.task_id, pending[0].question_id, {"answer": "async-change.txt"}
        )
        self.assertEqual(1, len(service.turn_numbers(row.task_id)))
        # Still the same turn, still open, still unmeasured.
        self.assertEqual([], self.range_events(row.task_id))

        adapter.work()
        service.refresh_task(row.task_id)

        self.assertEqual(1, len(self.range_events(row.task_id)))
        self.assertTrue(self.turn_closed(row.task_id))
        self.assertEqual(["async-change.txt"], self.committed_paths(row.task_id))
        self.assertEqual(
            RESULT_MET, self.evaluate(service, row.task_id)["async-change.txt"].result
        )


# -- failure and cancellation --------------------------------------------------


class TerminalFailureTests(TerminalRangeCase):
    """A committed range is a machine fact, and failure does not unmake it.

    PR25 settled this for the final state: a worker that produced side effects
    has them whatever word the lifecycle ends on. A commit is a stronger form of
    the same thing — it is in the object database and reachable from ``HEAD`` —
    so erasing it from the record because the turn ended ``failed`` would delete
    a fact about the repository to make the evidence agree with a status word.
    """

    def test_a_worker_that_committed_then_failed_keeps_its_range(self):
        adapter = AsyncCommitter(
            terminal=STATE_FAILED, on_work=self.create_and_commit()
        )
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()
        service.refresh_task(row.task_id)

        self.assertEqual(STATE_FAILED, self.store.get(row.task_id).state)
        self.assertEqual(1, len(self.range_events(row.task_id)))
        self.assertEqual(["async-change.txt"], self.committed_paths(row.task_id))
        # And the criterion is legitimately met on a turn that failed. Those two
        # sentences are about different subjects.
        self.assertEqual(
            RESULT_MET, self.evaluate(service, row.task_id)["async-change.txt"].result
        )

    def test_an_adapter_fault_mid_turn_still_measures_the_boundary(self):
        class Exploding(AsyncCommitter):
            def inspect(self, context):
                raise RuntimeError("the adapter fell over")

        adapter = Exploding(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()
        service.refresh_task(row.task_id)

        self.assertEqual(STATE_FAILED, self.store.get(row.task_id).state)
        self.assertEqual(1, len(self.range_events(row.task_id)))
        self.assertEqual(["async-change.txt"], self.committed_paths(row.task_id))


class CancellationTests(TerminalRangeCase):
    def test_a_cancelled_turn_with_committed_side_effects_is_measured(self):
        adapter = AsyncCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        # The worker committed, and only then was the turn cancelled.
        adapter.work()
        adapter._pending = None
        service.cancel_task(row.task_id)

        self.assertEqual(STATE_CANCELLED, self.store.get(row.task_id).state)
        self.assertEqual(1, len(self.range_events(row.task_id)))
        self.assertEqual(["async-change.txt"], self.committed_paths(row.task_id))

    def test_a_refused_cancel_leaves_the_turn_open_and_unmeasured(self):
        class Stubborn(AsyncCommitter):
            def cancel(self, context):
                raise AdapterRefusal("the worker will not stop")

        adapter = Stubborn(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()
        adapter._pending = None
        try:
            service.cancel_task(row.task_id)
        except Exception:
            pass

        self.assertFalse(self.turn_closed(row.task_id))
        self.assertEqual([], self.range_events(row.task_id))


# -- dispatch refusal ----------------------------------------------------------


class DispatchRefusalTests(TerminalRangeCase):
    """No worker authority, no terminal machine evidence. Structural, not checked."""

    def test_a_refused_start_measures_nothing(self):
        class Refusing(TaskAdapter):
            adapter_id = "validation"
            display_name = "Refusing"

            def capabilities(self):
                return AdapterCapabilities(start=True)

            def available(self):
                return True

            def session_available(self, task_id):
                return True

            def start(self, context):
                raise AdapterRefusal("not today")

        service, row = self.start(
            Refusing(), self.change_criteria(("path_changed", "async-change.txt"))
        )
        self.assertEqual(STATE_FAILED, self.store.get(row.task_id).state)
        self.assertEqual([], self.range_events(row.task_id))

    def test_an_adapter_fault_during_start_measures_nothing(self):
        class Exploding(TaskAdapter):
            adapter_id = "validation"
            display_name = "Exploding"

            def capabilities(self):
                return AdapterCapabilities(start=True)

            def available(self):
                return True

            def session_available(self, task_id):
                return True

            def start(self, context):
                raise RuntimeError("boom")

        service, row = self.start(
            Exploding(), self.change_criteria(("path_changed", "async-change.txt"))
        )
        self.assertEqual(STATE_FAILED, self.store.get(row.task_id).state)
        self.assertEqual([], self.range_events(row.task_id))


# -- exactly once --------------------------------------------------------------


class ExactlyOnceTests(TerminalRangeCase):
    def test_repeated_terminal_reconciliation_never_produces_a_second_event(self):
        adapter = AsyncCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()
        service.refresh_task(row.task_id)

        first = self.range_events(row.task_id)
        self.assertEqual(1, len(first))
        calls = adapter.inspect_calls

        for _ in range(5):
            service.refresh_task(row.task_id)
            service.get_result(row.task_id)

        self.assertEqual(first, self.range_events(row.task_id))
        self.assertEqual(calls, adapter.inspect_calls)

    def test_the_repository_moving_afterwards_never_remeasures(self):
        """No second historical measurement once the repository has moved on."""
        adapter = AsyncCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()
        service.refresh_task(row.task_id)
        before = self.range_events(row.task_id)
        target = self.span(row.task_id).target_revision

        # Somebody else commits after the turn closed.
        (self.root / "later.txt").write_text("later\n", encoding="utf-8")
        self.commit_all("somebody else")
        service.refresh_task(row.task_id)
        service.get_result(row.task_id)

        self.assertEqual(before, self.range_events(row.task_id))
        self.assertEqual(target, self.span(row.task_id).target_revision)
        self.assertEqual(["async-change.txt"], self.committed_paths(row.task_id))

    def test_reads_never_capture(self):
        """No GET-time capture: a read is a read."""
        adapter = AsyncCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()
        service.refresh_task(row.task_id)
        before = self.range_events(row.task_id)

        (self.root / "later.txt").write_text("later\n", encoding="utf-8")
        self.commit_all("somebody else")
        for _ in range(3):
            service.get_task(row.task_id)
            service.get_result(row.task_id)
            self.store.evidence_bundle(row.task_id, 1)
            service.turn_acceptance(row.task_id, 1)

        self.assertEqual(before, self.range_events(row.task_id))

    def test_a_direct_second_capture_appends_nothing(self):
        """The write-once guard, exercised directly with the world changed."""
        adapter = AsyncCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()
        service.refresh_task(row.task_id)
        before = self.range_events(row.task_id)

        (self.root / "later.txt").write_text("later\n", encoding="utf-8")
        self.commit_all("somebody else")
        service._record_committed_range(self.store.get(row.task_id), self.root, 1)

        self.assertEqual(before, self.range_events(row.task_id))

    def test_a_dispatch_bound_range_from_an_older_build_is_not_replaced(self):
        """A daemon upgraded while a turn was in flight. Conservative, on purpose.

        The pre-PR26 build already wrote a range for this turn. The repository
        has moved since, so measuring again now would be a *new* historical claim
        about a window whose evidence is already durable. The turn keeps the
        weaker range it was given — a known limit of that upgrade, and not a
        licence to rewrite history.
        """
        adapter = AsyncCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        # Exactly what the old build did on the line after `_open_first_turn`.
        service._record_committed_range(self.store.get(row.task_id), self.root, 1)
        early = self.range_events(row.task_id)
        self.assertEqual(1, len(early))
        self.assertEqual(RANGE_ANCESTRY_IDENTICAL, self.span(row.task_id).ancestry)

        adapter.work()
        service.refresh_task(row.task_id)

        self.assertEqual(early, self.range_events(row.task_id))
        self.assertEqual(RANGE_ANCESTRY_IDENTICAL, self.span(row.task_id).ancestry)


# -- crash and retry -----------------------------------------------------------


class CrashAndRetryTests(TerminalRangeCase):
    """The invariant: a recorded range, if present, has a post-worker target that
    was read before the turn was durably closed. A turn must never become closed
    and then be live-probed later to manufacture history."""

    def test_a_crash_before_the_capture_leaves_no_event_and_no_closed_turn(self):
        adapter = AsyncCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()

        def exploding(*arguments, **keywords):
            raise RuntimeError("crashed between the terminal result and the capture")

        service._capture_terminal_boundary = exploding
        with self.assertRaises(RuntimeError):
            service.refresh_task(row.task_id)

        self.assertEqual([], self.range_events(row.task_id))
        self.assertFalse(self.turn_closed(row.task_id))

        del service._capture_terminal_boundary
        adapter._pending = STATE_COMPLETED
        service.refresh_task(row.task_id)

        self.assertEqual(1, len(self.range_events(row.task_id)))
        self.assertTrue(self.turn_closed(row.task_id))
        self.assertEqual(["async-change.txt"], self.committed_paths(row.task_id))

    def test_a_crash_between_the_range_and_the_final_state_is_deterministic(self):
        """The pinned order made visible: the range survives, the final state does not.

        Deterministic is the requirement, not lossless. Every host that crashes
        here loses the same half, so the resulting turn — change criteria
        answerable, state criteria ``unverified`` — is a shape an operator can
        recognise rather than a coin toss.
        """
        adapter = AsyncCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()

        def exploding(*arguments, **keywords):
            raise RuntimeError("crashed between the two observations")

        service._record_final_state = exploding
        with self.assertRaises(RuntimeError):
            service.refresh_task(row.task_id)

        self.assertEqual(1, len(self.range_events(row.task_id)))
        self.assertIsNone(self.store.turn_final_state(row.task_id, 1).recorded_at)
        self.assertFalse(self.turn_closed(row.task_id))

        # The retry finds the range already written and appends nothing.
        del service._record_final_state
        adapter._pending = STATE_COMPLETED
        service.refresh_task(row.task_id)
        self.assertEqual(1, len(self.range_events(row.task_id)))
        self.assertTrue(self.turn_closed(row.task_id))

    def test_a_crash_after_both_facts_and_before_the_close_keeps_them(self):
        adapter = AsyncCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()

        original = self.store.transition
        state = {"armed": True}

        def exploding(task_id, target, **keywords):
            if state["armed"] and keywords.get("close_turn") is not None:
                state["armed"] = False
                raise RuntimeError("crashed after the capture, before the close")
            return original(task_id, target, **keywords)

        self.store.transition = exploding
        try:
            with self.assertRaises(RuntimeError):
                service.refresh_task(row.task_id)
        finally:
            del self.store.transition

        captured = self.range_events(row.task_id)
        self.assertEqual(1, len(captured))
        self.assertFalse(self.turn_closed(row.task_id))

        # The world moves, and the retry reuses the immutable fact.
        (self.root / "later.txt").write_text("later\n", encoding="utf-8")
        self.commit_all("somebody else")
        adapter._pending = STATE_COMPLETED
        service.refresh_task(row.task_id)

        self.assertEqual(captured, self.range_events(row.task_id))
        self.assertTrue(self.turn_closed(row.task_id))

    def test_restart_recovery_closes_the_turn_without_manufacturing_a_range(self):
        """The one closing path with no terminal worker result. It fails closed.

        PR25's rule, inherited exactly. A restart never observed the worker reach
        an end, so ``HEAD`` when the daemon comes back is a fact about *now*.
        The turn closes as ``interrupted`` with no range, and its change criteria
        answer ``unverified`` — where before PR26 they would have answered from a
        range measured before the worker ran.
        """
        adapter = AsyncCommitter(on_work=None)
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "ghost.txt"))
        )
        # Something committed to the project while the daemon was down.
        (self.root / "ghost.txt").write_text("x\n", encoding="utf-8")
        self.commit_all("while the daemon was down")

        service.recover_after_restart()

        self.assertTrue(self.turn_closed(row.task_id))
        self.assertEqual([], self.range_events(row.task_id))
        self.assertFalse(self.span(row.task_id).recorded)
        answer = self.evaluate(service, row.task_id)["ghost.txt"]
        self.assertEqual(RESULT_UNVERIFIED, answer.result)
        self.assertEqual(REASON_RANGE_NOT_RECORDED, answer.reason)

    def test_a_restart_never_overwrites_a_range_already_captured(self):
        adapter = AsyncCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()

        original = self.store.transition

        def exploding(task_id, target, **keywords):
            if keywords.get("close_turn") is not None:
                raise RuntimeError("crash")
            return original(task_id, target, **keywords)

        self.store.transition = exploding
        try:
            with self.assertRaises(RuntimeError):
                service.refresh_task(row.task_id)
        finally:
            del self.store.transition

        captured = self.range_events(row.task_id)
        self.assertEqual(1, len(captured))

        (self.root / "later.txt").write_text("later\n", encoding="utf-8")
        self.commit_all("somebody else")
        service.recover_after_restart()

        self.assertTrue(self.turn_closed(row.task_id))
        self.assertEqual(captured, self.range_events(row.task_id))


# -- versions ------------------------------------------------------------------


class VersionTests(TerminalRangeCase):
    """PR26 moves a capture site. It does not move a single version number.

    The reasoning, because "we bumped it to be safe" is how a version stops
    meaning anything:

    * **ASSEMBLER_VERSION** asks *was this produced by the same rules*. Assembly
      reads a stored range exactly as it did before and runs no Git. The rules
      did not move; the inputs did, and the fingerprint tracks inputs already.
    * **EVALUATOR_VERSION** would re-select every historical closed turn for
      re-evaluation — against the same immutable bundle, producing the same
      answer under a new number — while simultaneously making every existing V1
      record unreadable to the binder, which fails closed on an evaluator it does
      not know. Pure churn plus a regression.
    * **CURRENT_ASSESSMENT_VERSION** and **AGGREGATOR_VERSION** consume PR7 and
      PR14 records whose shape is unchanged.
    * **A committed-range observer version** was considered and rejected. It
      would be load-bearing only if the evaluator read it, and an evaluator that
      reads it is an evaluator that moved.
    """

    def test_no_version_moved(self):
        self.assertEqual(11, SCHEMA_VERSION)
        self.assertEqual(3, ASSEMBLER_VERSION)
        self.assertEqual(1, EVALUATOR_VERSION)
        self.assertEqual(1, AGGREGATOR_VERSION)

    def test_pr25_constants_are_untouched(self):
        self.assertEqual(2, FINAL_STATE_OBSERVER_VERSION)
        self.assertEqual(4, CURRENT_ASSESSMENT_VERSION)

    def test_the_committed_range_event_carries_no_observer_version(self):
        """Pinned as a decision rather than left as an accident.

        A stored range names two revisions and says what differed between them.
        That statement is true whenever it was taken, which is why no field of it
        had to change — the defect was never in what the row said.
        """
        adapter = AsyncCommitter(on_work=self.create_and_commit())
        service, row = self.start(
            adapter, self.change_criteria(("path_changed", "async-change.txt"))
        )
        adapter.work()
        service.refresh_task(row.task_id)

        event = self.range_events(row.task_id)[0]
        payload = json.loads(event["evidence_json"])
        for reference in payload:
            self.assertNotIn("observer_version", reference)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
