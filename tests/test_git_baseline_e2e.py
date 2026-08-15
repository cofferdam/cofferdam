"""M2K PR4 — one end-to-end pass, isolated home and real Git.

The unit tests each pin one rule. This walks the whole path in order — real
repository, real service, real store, real restart — and asserts the properties
that only appear when every layer runs together against a repository nobody here
wrote by hand.

No provider, no model, no network, no deployment.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path
from typing import Any, Dict, List, Sequence

from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    TaskAdapter,
    TaskContext,
)
from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION
from cofferdam.workstation.tasks.gitbaseline import (
    CAPTURE_CAPTURED,
    CAPTURE_UNAVAILABLE,
    HEAD_NOT_A_REPOSITORY,
    HEAD_PRESENT,
    HEAD_UNBORN,
    WORKTREE_CLEAN,
    WORKTREE_DIRTY,
)
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

from ._task_doubles import PROJECT_ID, TaskTestCase, python_code_only

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


class WalkingAdapter(TaskAdapter):
    """Records what was durable each time it was handed control."""

    adapter_id = "walker"
    display_name = "Walking adapter"
    description = "A test double."

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self.seen: List[Dict[str, Any]] = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            start=True, followup=True, cancel=True, final_result=True
        )

    def available(self) -> bool:
        return True

    def _look(self, context: TaskContext, call: str) -> None:
        connection = sqlite3.connect("file:%s?mode=ro" % self._db_path, uri=True)
        try:
            rows = connection.execute(
                "SELECT turn_number, capture_state, head_state, head_revision,"
                " working_tree_state FROM task_turn_git_baselines"
                " WHERE task_id = ? ORDER BY turn_number",
                (context.task_id,),
            ).fetchall()
        finally:
            connection.close()
        self.seen.append({"call": call, "rows": [tuple(r) for r in rows]})

    def start(self, context: TaskContext) -> AdapterOutcome:
        self._look(context, "start")
        return AdapterOutcome(
            events=(AdapterEvent(text="walk"),),
            requested_state="ready_for_followup",
        )

    def send_followup(self, context: TaskContext, followup: str) -> AdapterOutcome:
        self._look(context, "send_followup")
        return AdapterOutcome(
            events=(AdapterEvent(text="walk"),),
            requested_state="ready_for_followup",
        )

    def session_available(self, task_id: str) -> bool:
        return True

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        return AdapterOutcome(events=(), requested_state="cancelled")


@unittest.skipIf(GIT is None, "git is not installed")
class BaselineEndToEnd(TaskTestCase):
    project_adapters = ("walker",)

    def extra_adapters(self) -> Sequence[TaskAdapter]:
        self.walker = WalkingAdapter(self.home / "state" / "tasks" / "tasks.sqlite3")
        return (self.walker,)

    def git(self, *args, root=None):
        target = root or self.project_root
        subprocess.run(
            [GIT, *args], cwd=str(target), check=True, capture_output=True,
            env={**GIT_ENV, "HOME": str(target)},
        )

    def head(self, root=None):
        target = root or self.project_root
        return subprocess.run(
            [GIT, "rev-parse", "HEAD"], cwd=str(target), check=True,
            capture_output=True, env={**GIT_ENV, "HOME": str(target)},
        ).stdout.decode().strip()

    def write(self, relative, body="x\n", root=None):
        target = (root or self.project_root) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def create(self):
        row, _ = self.service.create_task(
            prompt="do the work", project_id=PROJECT_ID,
            adapter_id="walker", origin="pwa",
        )
        return row.task_id

    def test_the_whole_path(self):
        # 1. Schema v6.
        # At least, not exactly: each additive bump since this walk was
        # written leaves it true. M2K PR6 took it to 7. The literal pin for
        # the current version lives in `test_task_core.py`.
        self.assertGreaterEqual(SCHEMA_VERSION, 6)
        self.assertGreaterEqual(self.store.storage_health()["schema_version"], 6)

        self.git("init", "-q", ".")
        self.write("seed.txt", "one\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        first_head = self.head()

        # 2/3. A task is created and its first turn starts.
        task_id = self.create()

        # 4/5/6/7/8. The machine-owned baseline was captured, was durable before
        # the adapter's first instruction, and names the exact revision.
        self.assertEqual([s["call"] for s in self.walker.seen], ["start"])
        rows = self.walker.seen[0]["rows"]
        self.assertEqual(len(rows), 1, "no baseline was durable before the worker ran")
        self.assertEqual(rows[0][0], 1)
        self.assertEqual(rows[0][1], CAPTURE_CAPTURED)
        self.assertEqual(rows[0][2], HEAD_PRESENT)
        self.assertEqual(rows[0][3], first_head)
        self.assertEqual(rows[0][4], WORKTREE_CLEAN)

        # 9/10/11. The turn closes, a follow-up is requested, and the repository
        # HEAD moves in between — the worker committing its own work.
        self.write("worker_made_this.py", "print('work')\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "worker commit")
        second_head = self.head()
        self.assertNotEqual(first_head, second_head)

        self.service.send_followup(task_id, "now the next thing")

        # 12/13. Turn two's baseline differs and was durable before its worker ran.
        self.assertEqual([s["call"] for s in self.walker.seen], ["start", "send_followup"])
        by_turn = {r[0]: r for r in self.walker.seen[1]["rows"]}
        self.assertIn(2, by_turn, "turn two's baseline was not durable in time")
        self.assertEqual(by_turn[2][3], second_head)
        self.assertEqual(by_turn[1][3], first_head)

        # 18. A restart preserves both.
        self.store.close()
        reopened = TaskStore(self.config)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.turn_baseline(task_id, 1).head_revision, first_head)
        self.assertEqual(reopened.turn_baseline(task_id, 2).head_revision, second_head)

        # 19. A turn from before v6 has no baseline, and none is invented.
        legacy, _ = reopened.create_task(
            origin="pwa", adapter_id="validation", project_id="synth",
            prompt="p", title="legacy",
        )
        reopened.open_turn(
            legacy.task_id, provider="validation", source="internal_test",
            started_at="2026-08-15T00:00:00Z",
        )
        self.assertIsNone(reopened.turn_baseline(legacy.task_id, 1))

        # 20/21. The range still gets no relational table of its own. M2K PR5
        # consumes this boundary and persists what it reads as immutable task
        # event evidence, so none of these tables appeared then either — which is
        # the fact worth keeping asserted, since inventing one is the change this
        # line exists to notice.
        with sqlite3.connect(str(self.home / "state" / "tasks" / "tasks.sqlite3")) as db:
            tables = {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        for forbidden in ("task_turn_git_diffs", "task_revision_changes",
                          "task_commit_observations"):
            self.assertNotIn(forbidden, tables)
        # 3 since PR5 taught the assembler that evidence. The version is pinned
        # rather than compared to itself so that a bump stays a deliberate edit.
        self.assertEqual(ASSEMBLER_VERSION, 3)

        # 22/23. No evaluator and no check runner came with any of it. Matched on
        # whole words: `REASON_PROBE_FAILED` is a capture reason, not a verdict,
        # and a substring scan would read it as one.
        import inspect

        from cofferdam.workstation.tasks import gitbaseline

        # Code only. `python_code_only` drops comments and docstrings, which is
        # what stops ordinary prose — "a moment that has passed" — from reading
        # as evaluator vocabulary.
        source = python_code_only(inspect.getsource(gitbaseline))
        for forbidden in ("verdict", "confidence", "risk", "check_runner",
                          "acceptance", "evaluator"):
            self.assertNotIn(forbidden, source.lower(), forbidden)
        # And no vocabulary constant anywhere in the module is a verdict.
        for name in dir(gitbaseline):
            value = getattr(gitbaseline, name)
            if isinstance(value, str):
                self.assertNotIn(value.lower(), {"pass", "fail", "passed", "failed"})

    def test_a_dirty_starting_tree_is_recorded_before_the_worker_runs(self):
        """14. And it is not an accusation — it is what PR5 needs to stay honest."""
        self.git("init", "-q", ".")
        self.write("seed.txt", "one\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.write("seed.txt", "one\nsomebody was already editing this\n")
        self.write("untracked_already_here.txt", "u\n")

        task_id = self.create()
        row = self.walker.seen[0]["rows"][0]
        self.assertEqual(row[4], WORKTREE_DIRTY)
        stored = self.store.turn_baseline(task_id, 1)
        self.assertTrue(stored.preexisting_dirty)

    def test_an_unborn_repository_is_explicit(self):
        """15."""
        self.git("init", "-q", ".")
        task_id = self.create()
        stored = self.store.turn_baseline(task_id, 1)
        self.assertEqual(stored.head_state, HEAD_UNBORN)
        self.assertIsNone(stored.head_revision)
        self.assertEqual(stored.capture_state, CAPTURE_CAPTURED)

    def test_a_project_that_is_not_a_repository_is_explicit(self):
        """16. And the work still runs."""
        task_id = self.create()
        stored = self.store.turn_baseline(task_id, 1)
        self.assertEqual(stored.capture_state, CAPTURE_UNAVAILABLE)
        self.assertEqual(stored.head_state, HEAD_NOT_A_REPOSITORY)
        self.assertIsNone(stored.head_revision)
        self.assertEqual(len(self.walker.seen), 1, "the task did not run")

    def test_no_bridge_operation_was_added(self):
        """25. PR4 is foundational persistence and has no surface at all.

        Read from disk rather than imported, following the same rule the other
        structural scans use: importing the bridge would drag in FastAPI, and the
        stdlib-only CI path must be able to run this.
        """
        source = (
            REPO_ROOT / "cofferdam" / "actions_bridge" / "service.py"
        ).read_text("utf-8")
        for forbidden in ("baseline", "getGitBaseline", "head_revision"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_no_new_http_route_was_added(self):
        """24/25. The daemon's route table is untouched by this PR."""
        source = (
            REPO_ROOT / "cofferdam" / "workstation" / "service.py"
        ).read_text("utf-8")
        for forbidden in ("/baseline", "git-baseline", "gitbaseline"):
            self.assertNotIn(forbidden, source, forbidden)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
