"""M2K PR25 — the final-state observation is taken when the *worker* finishes, not when the *call* returns.

PR14 captured immediately after ``adapter.start()`` / ``adapter.send_followup()``
returned. For a synchronous adapter that really is after the work. For an
asynchronous one it is before it, and the observation was persisted as
``complete`` anyway — a pre-work filesystem fact wearing a post-worker label.

A production smoke on 2026-08-17 proved it end to end:

===============  ==========================================================
16:55:43.192     ``FinalStateObservation`` persisted: ``deploy-smoke.txt``
                 ``absent``, ``observation_state = complete``
16:55:46.446     the asynchronous worker creates the file
16:56:04.827     the turn reaches completion
===============  ==========================================================

The observer was never wrong. Asked after the work it answered ``present`` /
``file``. The defect was the capture boundary, and this module pins the boundary
rather than the observer.

What is asserted here
---------------------

* **One owner.** ``_capture_terminal_boundary`` takes every observation, at the
  transition that durably closes a turn, for every adapter family. The dispatch
  paths take none.
* **Async first turn, follow-up, absence, failure, cancellation, refusal,
  ``waiting_for_user``** — each boundary pinned by lifecycle, not by label.
* **Exactly once and immutable**, across duplicate reconciliation and across a
  crash on either side of the capture.
* **V1 refused, V2 accepted**, with no backfill, no rewrite and no re-probe.
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
    AVAILABILITY_ASSESSABLE,
    AVAILABILITY_NOT_ASSESSABLE,
    OUTCOME_MET,
    OUTCOME_NOT_MET,
    SUPPORTED_ASSESSMENT_VERSIONS,
)
from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterOutcome,
    TaskAdapter,
)
from cofferdam.workstation.tasks.delegated import ClarificationRequest
from cofferdam.workstation.tasks.binding import (
    CURRENT_ASSESSMENT_VERSION,
    REASON_UNSUPPORTED_OBSERVER,
    RESULT_MET,
    RESULT_NOT_MET,
    RESULT_UNVERIFIED,
    SUPPORTED_OBSERVER_VERSIONS,
)
from cofferdam.workstation.tasks.finalstate import (
    FINAL_STATE_OBSERVER_VERSION,
    KIND_FILE,
    OBSERVATION_COMPLETE,
    OBSERVATION_LEGACY_UNKNOWN,
    PATH_ABSENT,
    PATH_PRESENT,
)
from cofferdam.workstation.tasks.models import (
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


class AsyncWorker(TaskAdapter):
    """An adapter whose worker runs *between* calls, under the test's control.

    There is no thread, no sleep and no clock. ``start`` and ``send_followup``
    return ``running`` and touch nothing; the test then calls :meth:`work` to
    perform whatever the worker was going to do; and the next
    ``TaskService.refresh_task`` sees ``inspect`` report the terminal state.

    That ordering is the whole fixture. A real asynchronous worker's mutation
    lands at an unpredictable moment after ``start`` returns and before the
    terminal report; here it lands at an exactly known one, so "the observation
    saw the worker's effect" is a deterministic assertion rather than a race the
    suite would have to tolerate.
    """

    adapter_id = "validation"
    display_name = "Async Worker"

    def __init__(self, *, terminal=STATE_COMPLETED, on_work=None, on_followup_work=None):
        self._terminal = terminal
        self._on_work = on_work
        self._on_followup_work = on_followup_work
        self.root: Optional[Path] = None
        #: ``None`` until the worker has finished; the terminal state afterwards.
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

    # -- dispatch: accepted, and nothing has happened yet ---------------------

    def start(self, context):
        self.root = Path(context.project_root)
        self.turns += 1
        return AdapterOutcome(requested_state=STATE_RUNNING)

    def send_followup(self, context, followup):
        self.root = Path(context.project_root)
        self.turns += 1
        return AdapterOutcome(requested_state=STATE_RUNNING)

    # -- the worker, driven by the test ---------------------------------------

    def work(self, *, terminal=None, followup=False):
        """Do the work, then arm the terminal report the next inspect returns."""
        action = self._on_followup_work if followup else self._on_work
        if action is not None and self.root is not None:
            action(self.root)
        self._pending = terminal if terminal is not None else self._terminal

    def inspect(self, context):
        self.inspect_calls += 1
        if self._pending is None:
            # Still running. An adapter with nothing to say says nothing, and
            # `refresh_task` leaves the task exactly where it was.
            return AdapterOutcome()
        terminal, self._pending = self._pending, None
        return AdapterOutcome(
            requested_state=terminal,
            final_result="done" if terminal != STATE_FAILED else None,
            failure_code="worker_failed" if terminal == STATE_FAILED else None,
            failure_message="the worker failed" if terminal == STATE_FAILED else None,
        )

    def cancel(self, context):
        return AdapterOutcome(requested_state=STATE_CANCELLED)


class SyncWorker(TaskAdapter):
    """The PR14-era shape: everything happens inside ``start``."""

    adapter_id = "validation"
    display_name = "Sync Worker"

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
        if self._action is not None:
            self._action(Path(context.project_root))
        return AdapterOutcome(requested_state=self._terminal, final_result="done")

    def start(self, context):
        return self._run(context)

    def send_followup(self, context, followup):
        return self._run(context)


class AskingWorker(AsyncWorker):
    """Asks a question on the first report, then keeps running the same turn."""

    display_name = "Asking Worker"

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
                    question="Which file should I create?",
                    allows_free_text=True,
                ),
                clarification_token="q-1",
            )
        return super().inspect(context)

    def deliver_clarification_answer(self, context, token, answer):
        # The answer goes back into the same session and the same turn. No new
        # turn is opened, which is the property the deployment audit proved and
        # this module depends on.
        return True


# -- the harness --------------------------------------------------------------


class TerminalBoundCase(unittest.TestCase):
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

    def git(self, *arguments):
        subprocess.run(
            ("git",) + arguments, cwd=self.root, check=True, capture_output=True
        )

    def service(self, adapter):
        registry = type(
            __import__(
                "cofferdam.workstation.tasks", fromlist=["build_registry"]
            ).build_registry(enable_validation_adapter=True)
        )((adapter,))
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

    def state_criteria(self, *paths, predicate="path_exists"):
        return [
            {"kind": "evidence", "predicate": predicate, "path": path}
            for path in paths
        ]

    def start(self, adapter, criteria):
        service = self.service(adapter)
        row, _ = service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt="pr25 boundary scenario",
            origin="pwa",
            criteria=criteria,
            continuity={"mode": "root"},
        )
        return service, row

    # -- readers --------------------------------------------------------------

    def observation(self, task_id, turn=1):
        return self.store.turn_final_state(task_id, turn)

    def observation_rows(self, task_id):
        with self.sql() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM task_turn_final_state WHERE task_id = ?"
                    " ORDER BY turn_number",
                    (task_id,),
                )
            ]

    def path_states(self, task_id, turn=1):
        return {
            item.path: (item.state, item.kind)
            for item in self.observation(task_id, turn).paths
        }

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

    def assessment(self, service, task_id, turn=1):
        return service.current_criterion_assessment(task_id, turn)

    def acceptance(self, service, task_id, turn=1):
        return service.turn_acceptance(task_id, turn)

    def results(self, service, task_id, turn=1):
        """Every criterion assessment for a turn, keyed by the path it names.

        The envelope keys by ``criterion_id``, which is a hash nobody can write
        down in a test, so the resolved active set supplies the path for each id.
        """
        envelope = self.assessment(service, task_id, turn)
        resolved = service.resolve_active_criteria(task_id, turn)
        paths = {
            item.criterion_id: item.criterion.path
            for item in resolved.active
            if item.criterion.path
        }
        return {
            paths.get(item.criterion_id, item.criterion_id): item
            for item in envelope.assessments
        }


# -- the counterexample the deployment produced -------------------------------


class FailedDeploymentCounterexampleTests(TerminalBoundCase):
    """The exact 2026-08-17 production sequence, replayed against PR25.

    T0 the target is absent; the adapter's ``start`` returns ``running``; T1 is
    the instant PR14 would have observed and recorded ``absent`` as ``complete``;
    T2 the worker writes the file; T3 the worker reports terminal completion.

    Named so that a future change that reintroduces dispatch-time capture fails
    *this* test by name and a reader lands on the incident.
    """

    def test_deploy_smoke_txt_is_present_at_the_boundary_and_acceptance_is_met(self):
        target = "deploy-smoke.txt"
        adapter = AsyncWorker(
            on_work=lambda root: (root / target).write_text("smoke\n", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria(target))

        # T0/T1 — dispatch returned, the worker has not run, and the file that
        # the production incident recorded as `absent` is genuinely absent now.
        self.assertFalse((self.root / target).exists())
        self.assertEqual(
            OBSERVATION_LEGACY_UNKNOWN,
            self.observation(row.task_id).state,
            "PR14 recorded a complete pre-work observation at exactly this point",
        )
        self.assertEqual([], self.observation_rows(row.task_id))
        self.assertFalse(self.turn_closed(row.task_id))

        # T2 — the worker does its work, three seconds late in production.
        adapter.work()
        self.assertTrue((self.root / target).exists())

        # T3 — terminal reconciliation.
        service.refresh_task(row.task_id)

        observation = self.observation(row.task_id)
        self.assertEqual(OBSERVATION_COMPLETE, observation.state)
        self.assertEqual(2, observation.observer_version)
        self.assertEqual({target: (PATH_PRESENT, KIND_FILE)}, self.path_states(row.task_id))
        self.assertTrue(self.turn_closed(row.task_id))

        answer = self.results(service, row.task_id)[target]
        self.assertEqual(RESULT_MET, answer.result)
        self.assertEqual(OUTCOME_MET, self.acceptance(service, row.task_id).outcome)


# -- asynchronous first turn ---------------------------------------------------


class AsyncFirstTurnTests(TerminalBoundCase):
    def test_nothing_is_recorded_while_the_worker_is_still_running(self):
        adapter = AsyncWorker(
            on_work=lambda root: (root / "async.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("async.txt"))

        self.assertEqual(STATE_RUNNING, self.store.get(row.task_id).state)
        # The strong form of the rule: not "an incomplete row", *no row*. An
        # incomplete row at dispatch would be write-once and would then block the
        # real observation the terminal boundary is about to take.
        self.assertEqual([], self.observation_rows(row.task_id))
        self.assertEqual(
            OBSERVATION_LEGACY_UNKNOWN, self.observation(row.task_id).state
        )
        self.assertFalse(self.turn_closed(row.task_id))

        # And nothing downstream invents an answer from the absence. The whole
        # envelope is unavailable because the turn is still open, so there is no
        # criterion-level result to be wrong in either direction.
        envelope = self.assessment(service, row.task_id)
        self.assertEqual("unavailable", envelope.state)
        self.assertEqual("turn_not_closed", envelope.unavailable_reason)
        self.assertEqual((), envelope.assessments)
        self.assertEqual(
            AVAILABILITY_NOT_ASSESSABLE, self.acceptance(service, row.task_id).availability
        )

    def test_polling_a_still_running_worker_records_nothing(self):
        """`refresh_task` is not a capture trigger; a *closing* transition is."""
        adapter = AsyncWorker(
            on_work=lambda root: (root / "async.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("async.txt"))

        for _ in range(3):
            service.refresh_task(row.task_id)

        self.assertEqual(3, adapter.inspect_calls)
        self.assertEqual([], self.observation_rows(row.task_id))
        self.assertFalse(self.turn_closed(row.task_id))

    def test_the_observation_sees_the_worker_effect_and_acceptance_is_met(self):
        adapter = AsyncWorker(
            on_work=lambda root: (root / "async.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("async.txt"))
        adapter.work()
        service.refresh_task(row.task_id)

        rows = self.observation_rows(row.task_id)
        self.assertEqual(1, len(rows))
        self.assertEqual(FINAL_STATE_OBSERVER_VERSION, rows[0]["observer_version"])
        self.assertEqual(2, rows[0]["observer_version"])
        self.assertEqual(OBSERVATION_COMPLETE, rows[0]["observation_state"])
        self.assertEqual({"async.txt": (PATH_PRESENT, KIND_FILE)}, self.path_states(row.task_id))
        self.assertTrue(self.turn_closed(row.task_id))
        self.assertEqual(STATE_COMPLETED, self.store.get(row.task_id).state)

        self.assertEqual(RESULT_MET, self.results(service, row.task_id)["async.txt"].result)
        self.assertEqual(OUTCOME_MET, self.acceptance(service, row.task_id).outcome)

    def test_the_observation_is_recorded_after_the_worker_wrote(self):
        """Ordering, taken from the fixture rather than from a wall clock.

        The worker's write and the observation cannot be compared by timestamp
        without inviting a same-millisecond flake, so the fixture records the
        sequence directly: the file must already exist at the instant
        ``record_final_state`` is entered.
        """
        seen = {}
        original = self.store.record_final_state

        def watched(task_id, turn_number, **kwargs):
            seen["existed_when_observed"] = (self.root / "async.txt").exists()
            seen["turn_open"] = not self.turn_closed(task_id, turn_number)
            return original(task_id, turn_number, **kwargs)

        self.store.record_final_state = watched
        try:
            adapter = AsyncWorker(
                on_work=lambda root: (root / "async.txt").write_text("x", encoding="utf-8")
            )
            service, row = self.start(adapter, self.state_criteria("async.txt"))
            self.assertEqual({}, seen, "dispatch must not observe anything")
            adapter.work()
            service.refresh_task(row.task_id)
        finally:
            del self.store.record_final_state

        self.assertTrue(seen["existed_when_observed"], "observed before the worker wrote")
        self.assertTrue(seen["turn_open"], "observed after the turn was closed")


class AsyncAbsenceTests(TerminalBoundCase):
    def test_a_worker_that_never_creates_the_target_yields_not_met(self):
        """Absence is legitimate *now*, because it was observed after the worker."""
        adapter = AsyncWorker(on_work=None)
        service, row = self.start(adapter, self.state_criteria("never.txt"))
        adapter.work()
        service.refresh_task(row.task_id)

        self.assertEqual({"never.txt": (PATH_ABSENT, None)}, self.path_states(row.task_id))
        self.assertEqual(OBSERVATION_COMPLETE, self.observation(row.task_id).state)
        self.assertEqual(
            RESULT_NOT_MET, self.results(service, row.task_id)["never.txt"].result
        )
        self.assertEqual(OUTCOME_NOT_MET, self.acceptance(service, row.task_id).outcome)


# -- asynchronous follow-up ----------------------------------------------------


class AsyncFollowupTests(TerminalBoundCase):
    def test_the_second_turn_is_observed_at_its_own_terminal_boundary(self):
        adapter = AsyncWorker(
            on_work=lambda root: (root / "first.txt").write_text("1", encoding="utf-8"),
            on_followup_work=lambda root: (root / "second.txt").write_text(
                "2", encoding="utf-8"
            ),
        )
        service, row = self.start(adapter, self.state_criteria("first.txt"))
        adapter.work(terminal=STATE_READY_FOR_FOLLOWUP)
        service.refresh_task(row.task_id)
        self.assertEqual(STATE_READY_FOR_FOLLOWUP, self.store.get(row.task_id).state)

        service.send_followup(
            row.task_id,
            "now the second one",
            criteria=self.state_criteria("second.txt"),
            continuity={
                "mode": "extend",
                "predecessor_snapshot_id": self.snapshot_id(row.task_id, 1),
            },
        )

        # A genuinely new turn, dispatched and still running.
        self.assertEqual(2, adapter.turns)
        self.assertFalse((self.root / "second.txt").exists())
        self.assertEqual(
            OBSERVATION_LEGACY_UNKNOWN,
            self.observation(row.task_id, 2).state,
            "the follow-up dispatch must not observe",
        )
        self.assertEqual(1, len(self.observation_rows(row.task_id)))

        adapter.work(followup=True)
        service.refresh_task(row.task_id)

        rows = self.observation_rows(row.task_id)
        self.assertEqual([1, 2], [item["turn_number"] for item in rows])
        self.assertTrue(all(item["observer_version"] == 2 for item in rows))

        # Turn identity, exactly: turn one saw only its own path, turn two sees
        # the inherited set because `extend` accumulates.
        self.assertEqual({"first.txt": (PATH_PRESENT, KIND_FILE)}, self.path_states(row.task_id, 1))
        self.assertEqual(
            {
                "first.txt": (PATH_PRESENT, KIND_FILE),
                "second.txt": (PATH_PRESENT, KIND_FILE),
            },
            self.path_states(row.task_id, 2),
        )
        self.assertEqual(OUTCOME_MET, self.acceptance(service, row.task_id, 2).outcome)

    def test_turn_one_observation_is_untouched_by_turn_two(self):
        """Immutability across turns: a later boundary never edits an earlier one."""
        adapter = AsyncWorker(
            on_work=lambda root: (root / "first.txt").write_text("1", encoding="utf-8"),
            on_followup_work=lambda root: (root / "first.txt").unlink(),
        )
        service, row = self.start(adapter, self.state_criteria("first.txt"))
        adapter.work(terminal=STATE_READY_FOR_FOLLOWUP)
        service.refresh_task(row.task_id)
        before = dict(self.observation_rows(row.task_id)[0])

        service.send_followup(
            row.task_id,
            "delete it",
            criteria=self.state_criteria("first.txt"),
            continuity={
                "mode": "extend",
                "predecessor_snapshot_id": self.snapshot_id(row.task_id, 1),
            },
        )
        adapter.work(followup=True)
        service.refresh_task(row.task_id)

        self.assertEqual(before, self.observation_rows(row.task_id)[0])
        self.assertEqual({"first.txt": (PATH_PRESENT, KIND_FILE)}, self.path_states(row.task_id, 1))
        self.assertEqual({"first.txt": (PATH_ABSENT, None)}, self.path_states(row.task_id, 2))


# -- synchronous regression ----------------------------------------------------


class SynchronousRegressionTests(TerminalBoundCase):
    def test_a_synchronous_adapter_still_captures_exactly_once_before_the_close(self):
        """PR14's working case must not be delayed, lost or duplicated."""
        adapter = SyncWorker(
            action=lambda root: (root / "sync.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("sync.txt"))

        rows = self.observation_rows(row.task_id)
        self.assertEqual(1, len(rows))
        self.assertEqual(2, rows[0]["observer_version"])
        self.assertEqual(OBSERVATION_COMPLETE, rows[0]["observation_state"])
        self.assertEqual({"sync.txt": (PATH_PRESENT, KIND_FILE)}, self.path_states(row.task_id))
        self.assertTrue(self.turn_closed(row.task_id))
        self.assertEqual(RESULT_MET, self.results(service, row.task_id)["sync.txt"].result)

    def test_the_synchronous_observation_is_still_taken_while_the_turn_is_open(self):
        seen = {}
        original = self.store.record_final_state

        def watched(task_id, turn_number, **kwargs):
            with self.sql() as connection:
                turn = connection.execute(
                    "SELECT completed_at FROM task_turns"
                    " WHERE task_id = ? AND turn_number = ?",
                    (task_id, turn_number),
                ).fetchone()
            seen["exists"] = turn is not None
            seen["open"] = turn is not None and turn["completed_at"] is None
            return original(task_id, turn_number, **kwargs)

        self.store.record_final_state = watched
        try:
            self.start(
                SyncWorker(
                    action=lambda root: (root / "sync.txt").write_text("x", encoding="utf-8")
                ),
                self.state_criteria("sync.txt"),
            )
        finally:
            del self.store.record_final_state

        self.assertTrue(seen["exists"])
        self.assertTrue(seen["open"])


# -- waiting_for_user ----------------------------------------------------------


class WaitingForUserTests(TerminalBoundCase):
    """A question is a pause inside a turn, not the end of one."""

    def test_no_observation_is_finalized_while_the_turn_waits_for_an_answer(self):
        adapter = AskingWorker(
            on_work=lambda root: (root / "answered.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("answered.txt"))
        service.refresh_task(row.task_id)

        self.assertEqual(STATE_WAITING_FOR_USER, self.store.get(row.task_id).state)
        self.assertEqual(
            [],
            self.observation_rows(row.task_id),
            "waiting_for_user is not a turn boundary",
        )
        self.assertFalse(self.turn_closed(row.task_id))

        pending = service.pending_clarifications(row.task_id)
        self.assertEqual(1, len(pending))
        service.answer_clarification(
            row.task_id, pending[0].question_id, {"answer": "answered.txt"}
        )

        # Same turn, resumed. Still nothing observed.
        self.assertEqual(1, len(service.turn_numbers(row.task_id)))
        self.assertEqual([], self.observation_rows(row.task_id))

        adapter.work()
        service.refresh_task(row.task_id)

        rows = self.observation_rows(row.task_id)
        self.assertEqual(1, len(rows), "exactly one observation for the whole turn")
        self.assertEqual(1, rows[0]["turn_number"])
        self.assertEqual(2, rows[0]["observer_version"])
        self.assertEqual(
            {"answered.txt": (PATH_PRESENT, KIND_FILE)}, self.path_states(row.task_id)
        )
        self.assertEqual(
            RESULT_MET, self.results(service, row.task_id)["answered.txt"].result
        )


# -- terminal failure and cancellation, with side effects ----------------------


class TerminalFailureTests(TerminalBoundCase):
    """Doctrine, pinned: the observation describes the worktree, not the worker.

    A worker that wrote a file and then failed left the file behind. That is the
    effective state at the turn's boundary, and the turn's outcome word does not
    change what is on disk. So the observation is taken, and a state criterion may
    legitimately be ``met`` on a failed turn — which is a statement about the
    project, never about the worker having succeeded.
    """

    def test_a_worker_that_wrote_then_failed_has_its_side_effect_observed(self):
        adapter = AsyncWorker(
            on_work=lambda root: (root / "partial.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("partial.txt"))
        adapter.work(terminal=STATE_FAILED)
        service.refresh_task(row.task_id)

        self.assertEqual(STATE_FAILED, self.store.get(row.task_id).state)
        self.assertTrue(self.turn_closed(row.task_id))

        rows = self.observation_rows(row.task_id)
        self.assertEqual(1, len(rows))
        self.assertEqual(2, rows[0]["observer_version"])
        self.assertEqual(OBSERVATION_COMPLETE, rows[0]["observation_state"])
        self.assertEqual(
            {"partial.txt": (PATH_PRESENT, KIND_FILE)}, self.path_states(row.task_id)
        )
        self.assertEqual(
            RESULT_MET,
            self.results(service, row.task_id)["partial.txt"].result,
            "the file is there; the turn failing does not remove it",
        )

    def test_an_adapter_fault_mid_turn_still_observes_the_boundary(self):
        """The `_fail` closing path, reached without an adapter-reported state."""

        class Exploding(AsyncWorker):
            def inspect(self, context):
                raise RuntimeError("provider died")

        adapter = Exploding(on_work=None)
        service, row = self.start(adapter, self.state_criteria("wrote.txt"))
        (self.root / "wrote.txt").write_text("x", encoding="utf-8")
        service.refresh_task(row.task_id)

        self.assertEqual(STATE_FAILED, self.store.get(row.task_id).state)
        rows = self.observation_rows(row.task_id)
        self.assertEqual(1, len(rows))
        self.assertEqual(2, rows[0]["observer_version"])
        self.assertEqual({"wrote.txt": (PATH_PRESENT, KIND_FILE)}, self.path_states(row.task_id))


class CancellationTests(TerminalBoundCase):
    """Same doctrine, derived from the same rule and not from the status label."""

    def test_a_cancelled_turn_with_side_effects_is_observed(self):
        adapter = AsyncWorker(on_work=None)
        service, row = self.start(adapter, self.state_criteria("half.txt"))
        # The worker got as far as writing this before the cancel arrived.
        (self.root / "half.txt").write_text("x", encoding="utf-8")

        service.cancel_task(row.task_id)

        self.assertEqual(STATE_CANCELLED, self.store.get(row.task_id).state)
        self.assertTrue(self.turn_closed(row.task_id))
        rows = self.observation_rows(row.task_id)
        self.assertEqual(1, len(rows))
        self.assertEqual(2, rows[0]["observer_version"])
        self.assertEqual({"half.txt": (PATH_PRESENT, KIND_FILE)}, self.path_states(row.task_id))

    def test_a_refused_cancel_leaves_the_turn_open_and_unobserved(self):
        class Stubborn(AsyncWorker):
            def cancel(self, context):
                from cofferdam.workstation.tasks.adapters.protocol import AdapterRefusal

                raise AdapterRefusal("cannot stop")

        adapter = Stubborn(on_work=None)
        service, row = self.start(adapter, self.state_criteria("half.txt"))
        (self.root / "half.txt").write_text("x", encoding="utf-8")
        service.cancel_task(row.task_id)

        self.assertFalse(self.turn_closed(row.task_id))
        self.assertEqual(
            [],
            self.observation_rows(row.task_id),
            "a cancel that did not stop the worker did not end the turn",
        )


# -- dispatch refusal ----------------------------------------------------------


class DispatchRefusalTests(TerminalBoundCase):
    """No worker authority began, so there is no post-worker boundary to claim."""

    def test_a_refused_start_records_no_observation(self):
        class Refusing(TaskAdapter):
            adapter_id = "validation"
            display_name = "Refusing"

            def capabilities(self):
                return AdapterCapabilities(start=True, final_result=True)

            def available(self):
                return True

            def session_available(self, task_id):
                return True

            def start(self, context):
                from cofferdam.workstation.tasks.adapters.protocol import AdapterRefusal

                raise AdapterRefusal("not taking this one")

        service, row = self.start(Refusing(), self.state_criteria("seed.txt"))

        self.assertEqual(STATE_FAILED, self.store.get(row.task_id).state)
        # `seed.txt` genuinely exists, so a fabricated observation here would
        # have been *complete and present* — a confidently wrong post-worker
        # claim about a worker that never ran.
        self.assertTrue((self.root / "seed.txt").exists())
        self.assertEqual([], self.observation_rows(row.task_id))
        self.assertEqual([], service.turn_numbers(row.task_id))

    def test_an_adapter_fault_during_start_records_no_observation(self):
        class Exploding(TaskAdapter):
            adapter_id = "validation"
            display_name = "Exploding"

            def capabilities(self):
                return AdapterCapabilities(start=True, final_result=True)

            def available(self):
                return True

            def session_available(self, task_id):
                return True

            def start(self, context):
                raise RuntimeError("boom")

        service, row = self.start(Exploding(), self.state_criteria("seed.txt"))
        self.assertEqual(STATE_FAILED, self.store.get(row.task_id).state)
        self.assertEqual([], self.observation_rows(row.task_id))


# -- exactly once --------------------------------------------------------------


class ExactlyOnceTests(TerminalBoundCase):
    def test_repeated_terminal_reconciliation_never_produces_a_second_row(self):
        adapter = AsyncWorker(
            on_work=lambda root: (root / "once.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("once.txt"))
        adapter.work()
        service.refresh_task(row.task_id)

        first = self.observation_rows(row.task_id)
        self.assertEqual(1, len(first))
        calls_after_first = adapter.inspect_calls

        for _ in range(5):
            service.refresh_task(row.task_id)
            service.get_result(row.task_id)

        self.assertEqual(first, self.observation_rows(row.task_id))
        self.assertEqual(
            calls_after_first,
            adapter.inspect_calls,
            "a finished task is never asked again",
        )

    def test_the_filesystem_changing_afterwards_never_recaptures(self):
        adapter = AsyncWorker(
            on_work=lambda root: (root / "once.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("once.txt"))
        adapter.work()
        service.refresh_task(row.task_id)
        before = self.observation_rows(row.task_id)

        (self.root / "once.txt").unlink()
        service.refresh_task(row.task_id)
        service.get_result(row.task_id)
        self.assertEqual(RESULT_MET, self.results(service, row.task_id)["once.txt"].result)
        self.assertEqual(before, self.observation_rows(row.task_id))

    def test_an_existing_row_is_reused_rather_than_overwritten_on_retry(self):
        """The store's write-once guard is the backstop, exercised directly."""
        adapter = AsyncWorker(on_work=None)
        service, row = self.start(adapter, self.state_criteria("late.txt"))
        adapter.work()
        service.refresh_task(row.task_id)

        before = self.observation_rows(row.task_id)
        self.assertEqual((PATH_ABSENT, None), self.path_states(row.task_id)["late.txt"])

        # A second capture attempt for the same turn, with the world changed.
        (self.root / "late.txt").write_text("x", encoding="utf-8")
        service._record_final_state(self.store.get(row.task_id), self.root, 1)

        self.assertEqual(before, self.observation_rows(row.task_id))
        self.assertEqual((PATH_ABSENT, None), self.path_states(row.task_id)["late.txt"])


# -- crash and retry -----------------------------------------------------------


class CrashAndRetryTests(TerminalBoundCase):
    """Failure injected at each side of the capture, and the invariant held.

    The invariant: *a final-state observation, if present, corresponds to a
    post-worker point before the turn was durably closed.* A turn must never
    become durably closed and then be live-probed later to manufacture history.
    """

    def test_a_crash_before_the_capture_leaves_no_row_and_no_closed_turn(self):
        adapter = AsyncWorker(
            on_work=lambda root: (root / "crash.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("crash.txt"))
        adapter.work()

        boom = RuntimeError("crashed between the terminal result and the capture")

        def exploding(*arguments, **keywords):
            raise boom

        service._capture_terminal_boundary = exploding
        with self.assertRaises(RuntimeError):
            service.refresh_task(row.task_id)

        self.assertEqual([], self.observation_rows(row.task_id))
        self.assertFalse(self.turn_closed(row.task_id))

        # The retry, on a fresh service exactly as a restart would build one.
        del service._capture_terminal_boundary
        adapter._pending = STATE_COMPLETED
        service.refresh_task(row.task_id)

        rows = self.observation_rows(row.task_id)
        self.assertEqual(1, len(rows))
        self.assertEqual(2, rows[0]["observer_version"])
        self.assertTrue(self.turn_closed(row.task_id))

    def test_a_crash_after_the_capture_and_before_the_close_keeps_the_observation(self):
        adapter = AsyncWorker(
            on_work=lambda root: (root / "crash.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("crash.txt"))
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

        # The observation is durable and the turn is not yet closed — the exact
        # ordering the invariant demands.
        captured = self.observation_rows(row.task_id)
        self.assertEqual(1, len(captured))
        self.assertFalse(self.turn_closed(row.task_id))

        # The retry reuses the immutable boundary fact rather than re-probing,
        # which the changed world below makes visible.
        (self.root / "crash.txt").unlink()
        adapter._pending = STATE_COMPLETED
        service.refresh_task(row.task_id)

        self.assertEqual(captured, self.observation_rows(row.task_id))
        self.assertEqual({"crash.txt": (PATH_PRESENT, KIND_FILE)}, self.path_states(row.task_id))
        self.assertTrue(self.turn_closed(row.task_id))

    def test_restart_recovery_closes_the_turn_without_manufacturing_a_boundary(self):
        """The one closing path with no terminal worker result. It fails closed.

        A restart never observed the worker reach an end, so whatever the
        worktree holds when the daemon comes back is a fact about *now*. Recording
        it as the turn's post-worker boundary would be the reconstruction PR14's
        no-fallback doctrine forbids, so the turn closes as ``interrupted`` with
        nothing recorded and every state criterion on it stays ``unverified``.
        """
        adapter = AsyncWorker(on_work=None)
        service, row = self.start(adapter, self.state_criteria("ghost.txt"))
        # Something touched the project while the daemon was down.
        (self.root / "ghost.txt").write_text("x", encoding="utf-8")

        service.recover_after_restart()

        self.assertTrue(self.turn_closed(row.task_id))
        self.assertEqual([], self.observation_rows(row.task_id))
        self.assertEqual(
            OBSERVATION_LEGACY_UNKNOWN, self.observation(row.task_id).state
        )
        answer = self.results(service, row.task_id)["ghost.txt"]
        self.assertEqual(RESULT_UNVERIFIED, answer.result)

    def test_a_restart_never_overwrites_an_observation_already_captured(self):
        adapter = AsyncWorker(
            on_work=lambda root: (root / "kept.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("kept.txt"))
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

        captured = self.observation_rows(row.task_id)
        self.assertEqual(1, len(captured))

        (self.root / "kept.txt").unlink()
        service.recover_after_restart()

        self.assertTrue(self.turn_closed(row.task_id))
        self.assertEqual(captured, self.observation_rows(row.task_id))
        self.assertEqual({"kept.txt": (PATH_PRESENT, KIND_FILE)}, self.path_states(row.task_id))


# -- observer version identity -------------------------------------------------


class ObserverVersionTests(TerminalBoundCase):
    def test_the_constants_are_where_pr25_left_them(self):
        self.assertEqual(2, FINAL_STATE_OBSERVER_VERSION)
        self.assertEqual((2,), SUPPORTED_OBSERVER_VERSIONS)
        self.assertEqual(4, CURRENT_ASSESSMENT_VERSION)
        self.assertEqual((4,), SUPPORTED_ASSESSMENT_VERSIONS)
        self.assertEqual(1, AGGREGATOR_VERSION)
        self.assertEqual(11, SCHEMA_VERSION)

    def test_the_same_path_facts_hash_differently_under_v1_and_v2(self):
        """Version binding already does this; PR25 adds no parallel algorithm."""
        from cofferdam.workstation.tasks import finalstate
        from cofferdam.workstation.tasks.finalstate import (
            PathObservation,
            final_state_fingerprint,
        )

        paths = (
            PathObservation(
                ordinal=1, path="a.txt", state=PATH_PRESENT, kind=KIND_FILE, reason=None
            ),
        )
        arguments = ("task-1", 1, OBSERVATION_COMPLETE, None, "lineage", "abc", paths)

        v2 = final_state_fingerprint(*arguments)
        original = finalstate.FINAL_STATE_OBSERVER_VERSION
        finalstate.FINAL_STATE_OBSERVER_VERSION = 1
        try:
            v1 = final_state_fingerprint(*arguments)
        finally:
            finalstate.FINAL_STATE_OBSERVER_VERSION = original

        self.assertNotEqual(v1, v2)
        self.assertEqual(v2, final_state_fingerprint(*arguments))


class HistoricalV1Tests(TerminalBoundCase):
    """A V1 row is a historical fact and is no longer current-state authority."""

    def _demote_to_v1(self, task_id, turn=1):
        """Rewrite one stored row to exactly what PR14 would have written.

        Both the version and the fingerprint, because a row carrying a V2 hash
        under a V1 version would be refused as corruption and would prove the
        wrong thing. This produces an *internally valid* V1 observation, which is
        the only interesting case: the failed deployment's row was valid too.
        """
        from cofferdam.workstation.tasks import finalstate
        from cofferdam.workstation.tasks.finalstate import final_state_fingerprint

        stored = self.observation(task_id, turn)
        original = finalstate.FINAL_STATE_OBSERVER_VERSION
        finalstate.FINAL_STATE_OBSERVER_VERSION = 1
        try:
            fingerprint = final_state_fingerprint(
                stored.task_id,
                stored.turn_number,
                stored.state,
                stored.limitation_reason,
                stored.lineage_fingerprint,
                stored.head_revision,
                stored.paths,
            )
        finally:
            finalstate.FINAL_STATE_OBSERVER_VERSION = original
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_final_state"
                " SET observer_version = 1, observation_fingerprint = ?"
                " WHERE task_id = ? AND turn_number = ?",
                (fingerprint, task_id, turn),
            )

    def test_a_valid_v1_observation_is_refused_as_state_authority(self):
        adapter = AsyncWorker(
            on_work=lambda root: (root / "legacy.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("legacy.txt"))
        adapter.work()
        service.refresh_task(row.task_id)
        self.assertEqual(RESULT_MET, self.results(service, row.task_id)["legacy.txt"].result)

        self._demote_to_v1(row.task_id)

        # The whole set fails closed rather than each criterion degrading, which
        # is `_final_state_defect`'s existing rule for a row that may not be
        # interpreted at all. Stronger than per-criterion `unverified`: there is
        # no result to be misread, and the reason names the actual objection.
        envelope = self.assessment(service, row.task_id)
        self.assertEqual("unavailable", envelope.state)
        self.assertEqual(REASON_UNSUPPORTED_OBSERVER, envelope.unavailable_reason)
        self.assertEqual((), envelope.assessments)
        self.assertEqual(4, envelope.assessment_version)

        aggregate = self.acceptance(service, row.task_id)
        self.assertEqual(AVAILABILITY_NOT_ASSESSABLE, aggregate.availability)
        self.assertEqual(REASON_UNSUPPORTED_OBSERVER, aggregate.availability_reason)
        self.assertIsNone(aggregate.outcome)

    def test_the_exact_failed_deployment_row_is_refused(self):
        """``observer_version=1``, ``complete``, ``deploy-smoke.txt``, ``absent``.

        The shape of the row the failed deployment persisted. Under V1 semantics
        this would answer ``path_exists`` with ``not_met`` — a confident negative
        about a file the worker created three seconds later. It must not be
        consumed at all.
        """
        adapter = AsyncWorker(on_work=None)
        service, row = self.start(adapter, self.state_criteria("deploy-smoke.txt"))
        adapter.work()
        service.refresh_task(row.task_id)

        stored = self.observation(row.task_id)
        self.assertEqual(OBSERVATION_COMPLETE, stored.state)
        self.assertEqual(
            (PATH_ABSENT, None), self.path_states(row.task_id)["deploy-smoke.txt"]
        )
        self.assertEqual(
            RESULT_NOT_MET, self.results(service, row.task_id)["deploy-smoke.txt"].result
        )

        self._demote_to_v1(row.task_id)

        envelope = self.assessment(service, row.task_id)
        self.assertEqual("unavailable", envelope.state)
        self.assertEqual(REASON_UNSUPPORTED_OBSERVER, envelope.unavailable_reason)
        self.assertEqual(
            (),
            envelope.assessments,
            "the confident negative must not survive in any form",
        )
        self.assertEqual(
            AVAILABILITY_NOT_ASSESSABLE,
            self.acceptance(service, row.task_id).availability,
        )

    def test_refusing_a_v1_row_neither_rewrites_nor_re_probes_it(self):
        adapter = AsyncWorker(
            on_work=lambda root: (root / "legacy.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("legacy.txt"))
        adapter.work()
        service.refresh_task(row.task_id)
        self._demote_to_v1(row.task_id)

        before = self.observation_rows(row.task_id)
        with self.sql() as connection:
            paths_before = [
                dict(item)
                for item in connection.execute(
                    "SELECT * FROM task_turn_final_state_paths WHERE task_id = ?"
                    " ORDER BY ordinal",
                    (row.task_id,),
                )
            ]

        for _ in range(3):
            self.assessment(service, row.task_id)
            self.acceptance(service, row.task_id)
            service.turn_assessment(row.task_id, 1)

        with self.sql() as connection:
            paths_after = [
                dict(item)
                for item in connection.execute(
                    "SELECT * FROM task_turn_final_state_paths WHERE task_id = ?"
                    " ORDER BY ordinal",
                    (row.task_id,),
                )
            ]
        self.assertEqual(before, self.observation_rows(row.task_id))
        self.assertEqual(1, before[0]["observer_version"])
        self.assertEqual(paths_before, paths_after)

    def test_opening_a_database_holding_v1_rows_migrates_nothing(self):
        adapter = AsyncWorker(
            on_work=lambda root: (root / "legacy.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("legacy.txt"))
        adapter.work()
        service.refresh_task(row.task_id)
        self._demote_to_v1(row.task_id)

        before = self.observation_rows(row.task_id)
        self.store.close()

        reopened = TaskStore(self.config)
        self.addCleanup(reopened.close)
        reopened.storage_health()

        with self.sql() as connection:
            after = [
                dict(item)
                for item in connection.execute(
                    "SELECT * FROM task_turn_final_state WHERE task_id = ?",
                    (row.task_id,),
                )
            ]
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()

        self.assertEqual(before, after, "a V1 row must survive field-identical")
        self.assertEqual(1, after[0]["observer_version"])
        self.assertEqual("11", version["value"])
        self.store = reopened

    def test_a_v2_observation_is_accepted_normally(self):
        adapter = AsyncWorker(
            on_work=lambda root: (root / "modern.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("modern.txt"))
        adapter.work()
        service.refresh_task(row.task_id)

        self.assertEqual(2, self.observation(row.task_id).observer_version)
        answer = self.results(service, row.task_id)["modern.txt"]
        self.assertEqual(RESULT_MET, answer.result)
        self.assertNotEqual(REASON_UNSUPPORTED_OBSERVER, answer.reason)
        self.assertEqual(OUTCOME_MET, self.acceptance(service, row.task_id).outcome)


# -- the read surface is unchanged ---------------------------------------------


class HistoricalReadTests(TerminalBoundCase):
    def test_deleting_the_repository_changes_no_answer(self):
        import shutil

        adapter = AsyncWorker(
            on_work=lambda root: (root / "gone.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("gone.txt"))
        adapter.work()
        service.refresh_task(row.task_id)

        before = (
            self.assessment(service, row.task_id).fingerprint,
            self.acceptance(service, row.task_id).fingerprint,
            self.path_states(row.task_id),
        )

        shutil.rmtree(self.root)

        after = (
            self.assessment(service, row.task_id).fingerprint,
            self.acceptance(service, row.task_id).fingerprint,
            self.path_states(row.task_id),
        )
        self.assertEqual(before, after)

    def test_repeated_reads_write_nothing_and_observe_nothing(self):
        adapter = AsyncWorker(
            on_work=lambda root: (root / "read.txt").write_text("x", encoding="utf-8")
        )
        service, row = self.start(adapter, self.state_criteria("read.txt"))
        adapter.work()
        service.refresh_task(row.task_id)

        import hashlib

        def digest():
            return hashlib.sha256(self.database.read_bytes()).hexdigest()

        observed = []
        original = self.store.record_final_state

        def watched(*arguments, **keywords):
            observed.append(arguments)
            return original(*arguments, **keywords)

        self.store.record_final_state = watched
        before = digest()
        try:
            for _ in range(3):
                service.turn_assessment(row.task_id, 1)
                service.turn_acceptance(row.task_id, 1)
                service.current_criterion_assessment(row.task_id, 1)
                service.turn_final_state(row.task_id, 1)
        finally:
            del self.store.record_final_state

        self.assertEqual([], observed, "a read must never invoke the observer")
        self.assertEqual(before, digest(), "a read must never write")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
