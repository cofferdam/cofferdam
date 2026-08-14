"""M2K PR4 — the pre-work ordering guarantee, tested through the real service.

This is the strongest test in PR4 and the reason the milestone splits capture
from consumption. Everything else about a Git baseline can be right — the
revision exact, the dirty state honest, the schema clean — and the whole thing is
still worthless if the boundary was recorded *after* the worker had already
begun. A line drawn behind a moving worker measures nothing.

So the assertions here are not made by the test. They are made by an adapter,
from inside its own ``start`` and ``send_followup``, reading the database
directly at the first instruction it is ever given. If a baseline row is not
already committed and readable at that moment, the adapter records the failure
and the test fails.

Testing it at the store level would prove nothing: ``reserve_turn_baseline``
obviously writes a row. What has to be proven is that **no path exists** from
``start_task`` or ``send_followup`` to a worker that does not pass through the
capture first. That is a property of :class:`TaskService`, so the test drives
:class:`TaskService`.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    TaskAdapter,
    TaskContext,
)
from cofferdam.workstation.tasks.gitbaseline import (
    CAPTURE_CAPTURED,
    HEAD_PRESENT,
    WORKTREE_CLEAN,
    WORKTREE_DIRTY,
)

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

BASELINE_TABLE = "task_turn_git_baselines"


class BaselineWatchingAdapter(TaskAdapter):
    """An adapter that checks, from inside the worker's own moment, what is durable.

    It reads the database on its **own** connection rather than through the
    store, for the same reason a witness is not asked to confirm their own
    account: a row visible only inside the writing connection's uncommitted
    transaction would satisfy the store and prove nothing about durability. A
    separate connection sees committed data only.
    """

    adapter_id = "baseline-watcher"
    display_name = "Baseline watching adapter"
    description = "A test double that inspects durable state at dispatch time."

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        #: One entry per invocation: what the durable baseline table held at the
        #: instant the worker was handed control.
        self.observations: List[Dict[str, Any]] = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            start=True, followup=True, cancel=True, final_result=True
        )

    def available(self) -> bool:
        return True

    def _look(self, context: TaskContext, call: str) -> None:
        connection = sqlite3.connect(
            "file:%s?mode=ro" % self._db_path, uri=True
        )
        try:
            rows = connection.execute(
                "SELECT turn_number, capture_state, head_state, head_revision,"
                " working_tree_state, status_coverage, reason FROM %s"
                " WHERE task_id = ? ORDER BY turn_number" % BASELINE_TABLE,
                (context.task_id,),
            ).fetchall()
            turns = connection.execute(
                "SELECT turn_number FROM task_turns WHERE task_id = ?"
                " ORDER BY turn_number",
                (context.task_id,),
            ).fetchall()
        finally:
            connection.close()
        self.observations.append(
            {
                "call": call,
                "baselines": [tuple(r) for r in rows],
                "turns": [r[0] for r in turns],
            }
        )

    def start(self, context: TaskContext) -> AdapterOutcome:
        self._look(context, "start")
        # Ends its turn immediately and waits, so a test can drive the follow-up
        # path without inventing a lifecycle the service does not offer.
        return AdapterOutcome(
            events=(AdapterEvent(text="watcher start"),),
            requested_state="ready_for_followup",
        )

    def send_followup(self, context: TaskContext, followup: str) -> AdapterOutcome:
        self._look(context, "send_followup")
        return AdapterOutcome(
            events=(AdapterEvent(text="watcher followup"),),
            requested_state="ready_for_followup",
        )

    def session_available(self, task_id: str) -> bool:
        return True

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        return AdapterOutcome(events=(), requested_state="cancelled")


@unittest.skipIf(GIT is None, "git is not installed")
class PreWorkOrderingTests(TaskTestCase):
    """The guarantee, driven through the service exactly as production drives it."""

    project_adapters = ("baseline-watcher",)

    def extra_adapters(self) -> Sequence[TaskAdapter]:
        self.watcher = BaselineWatchingAdapter(
            self.home / "state" / "tasks" / "tasks.sqlite3"
        )
        return (self.watcher,)

    def setUp(self):
        super().setUp()
        self._git("init", "-q", ".")
        self._write("seed.txt", "one\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "base")

    # -- fixture helpers -----------------------------------------------------

    def _git(self, *args: str) -> None:
        subprocess.run(
            [GIT, *args],
            cwd=str(self.project_root),
            check=True,
            capture_output=True,
            env={**GIT_ENV, "HOME": str(self.project_root)},
        )

    def _write(self, relative: str, body: str = "x\n") -> None:
        target = self.project_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def _head(self) -> str:
        completed = subprocess.run(
            [GIT, "rev-parse", "HEAD"],
            cwd=str(self.project_root),
            check=True,
            capture_output=True,
            env={**GIT_ENV, "HOME": str(self.project_root)},
        )
        return completed.stdout.decode().strip()

    def _start_task(self) -> str:
        row, _created = self.service.create_task(
            prompt="do the thing",
            project_id=PROJECT_ID,
            adapter_id="baseline-watcher",
            origin="pwa",
        )
        return row.task_id

    # -- first turn ----------------------------------------------------------

    def test_the_baseline_is_durable_before_the_worker_runs(self):
        """The load-bearing one. Asserted from inside the adapter's first breath."""
        head = self._head()
        task_id = self._start_task()

        self.assertEqual(
            [o["call"] for o in self.watcher.observations], ["start"]
        )
        seen = self.watcher.observations[0]
        self.assertEqual(
            len(seen["baselines"]), 1,
            "no durable baseline existed when the worker was handed control",
        )
        (turn_number, capture_state, head_state, revision, tree, coverage, reason) = (
            seen["baselines"][0]
        )
        self.assertEqual(turn_number, 1)
        self.assertEqual(capture_state, CAPTURE_CAPTURED)
        self.assertEqual(head_state, HEAD_PRESENT)
        self.assertEqual(revision, head)
        self.assertEqual(tree, WORKTREE_CLEAN)
        self.assertEqual(coverage, "complete")
        self.assertIsNone(reason)
        self.assertIsNotNone(task_id)

    def test_the_turn_row_does_not_exist_yet_when_the_worker_runs(self):
        """Which is exactly why the foreign key names `tasks` and not `task_turns`."""
        self._start_task()
        self.assertEqual(self.watcher.observations[0]["turns"], [])

    def test_the_baseline_the_worker_saw_is_the_one_that_survives(self):
        head = self._head()
        task_id = self._start_task()
        stored = self.store.turn_baseline(task_id, 1)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.head_revision, head)
        self.assertEqual(stored.head_state, HEAD_PRESENT)
        self.assertEqual(stored.working_tree_state, WORKTREE_CLEAN)

    def test_a_dirty_tree_is_recorded_as_dirty_before_the_worker_runs(self):
        self._write("seed.txt", "one\nedited\n")
        self._write("untracked.txt", "u\n")
        self._start_task()
        seen = self.watcher.observations[0]["baselines"][0]
        self.assertEqual(seen[4], WORKTREE_DIRTY)
        self.assertEqual(seen[5], "complete")

    # -- follow-up turn ------------------------------------------------------

    def test_the_follow_up_captures_its_own_baseline_before_its_worker_runs(self):
        first_head = self._head()
        task_id = self._start_task()

        # The repository moves between turns, exactly as it would if the worker
        # had committed its own work.
        self._write("worker_made_this.py", "print('work')\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "worker commit")
        second_head = self._head()
        self.assertNotEqual(first_head, second_head)

        self.service.send_followup(task_id, "and now the next thing")

        calls = [o["call"] for o in self.watcher.observations]
        self.assertEqual(calls, ["start", "send_followup"])

        second = self.watcher.observations[1]
        by_turn = {row[0]: row for row in second["baselines"]}
        self.assertIn(
            2, by_turn,
            "turn two's baseline was not durable when its worker was handed control",
        )
        self.assertEqual(by_turn[2][3], second_head)
        # And turn one's boundary is untouched by any of it.
        self.assertEqual(by_turn[1][3], first_head)

    def test_each_turn_keeps_its_own_boundary(self):
        first_head = self._head()
        task_id = self._start_task()
        self._write("more.py", "x\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "second")
        second_head = self._head()
        self.service.send_followup(task_id, "next")

        one = self.store.turn_baseline(task_id, 1)
        two = self.store.turn_baseline(task_id, 2)
        self.assertEqual(one.head_revision, first_head)
        self.assertEqual(two.head_revision, second_head)
        self.assertNotEqual(one.head_revision, two.head_revision)

    def test_an_unchanged_head_may_repeat_across_turns(self):
        """Identity is per-turn. Two turns may honestly share a revision."""
        head = self._head()
        task_id = self._start_task()
        self.service.send_followup(task_id, "next")
        one = self.store.turn_baseline(task_id, 1)
        two = self.store.turn_baseline(task_id, 2)
        self.assertEqual(one.head_revision, head)
        self.assertEqual(two.head_revision, head)

    def test_a_baseline_is_never_overwritten_by_a_later_turn(self):
        first_head = self._head()
        task_id = self._start_task()
        self._write("later.py", "x\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "later")
        self.service.send_followup(task_id, "next")
        self.assertEqual(self.store.turn_baseline(task_id, 1).head_revision, first_head)

    # -- restart -------------------------------------------------------------

    def test_baselines_survive_a_reopen(self):
        head = self._head()
        task_id = self._start_task()
        path = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.store.close()
        from cofferdam.workstation.tasks.store import TaskStore

        reopened = TaskStore(self.config)
        self.addCleanup(reopened.close)
        stored = reopened.turn_baseline(task_id, 1)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.head_revision, head)
        self.assertTrue(path.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
