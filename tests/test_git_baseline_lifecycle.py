"""M2K PR4 — the dispatch lifecycle, and the crash it exists to survive.

The first draft of this PR allowed a baseline to be replaced whenever no turn row
existed. That was wrong, and the way it was wrong is the reason this module is
the longest one in the change.

The adapter is invoked **before** the turn row is written. So "no row in
``task_turns``" describes two situations that could not be more different:

* the worker was never called, and
* the worker ran, possibly committed, and Cofferdam died before recording the
  turn.

Treating the second as replaceable lets a retry read the worker's *own commit*
and store it as the **pre-work** boundary. The real boundary is gone, nothing
complains, and every observation PR5 later derives from it is wrong while looking
exactly as authoritative as a correct one.

So the permission to replace is a durable fact of its own — ``dispatch_state`` —
and it is committed *before* the adapter call, which is what makes ``captured``
mean "the adapter had provably not been reached" rather than "we did not find a
turn row".

Note what is deliberately **not** claimed anywhere here: that an adapter refusal
proves the worker was untouched. It does not. ``ClaudeCodeAdapter.send_followup``
raises ``AdapterRefusal`` when ``send_turn`` fails, *after* bytes may already have
reached a live worker's stdin, and the core cannot tell that apart from an early
refusal without reading an adapter's message text. So a refusal is recorded, and
it does not re-open replacement.
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
    AdapterRefusal,
    TaskAdapter,
    TaskContext,
)
from cofferdam.workstation.tasks.gitbaseline import (
    CAPTURE_CAPTURED,
    DISPATCH_CAPTURED,
    DISPATCH_REFUSED,
    DISPATCH_STARTED,
    DISPATCH_STATES,
    DISPATCH_TURN_OPENED,
    HEAD_PRESENT,
    REPLACEABLE_DISPATCH_STATES,
    GitBaseline,
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
BASELINE_TABLE = "task_turn_git_baselines"


class TheVocabulary(unittest.TestCase):
    def test_only_captured_permits_replacement(self):
        """The entire rule, in one assertion."""
        self.assertEqual(REPLACEABLE_DISPATCH_STATES, (DISPATCH_CAPTURED,))

    def test_the_states_are_closed_and_distinct(self):
        self.assertEqual(len(set(DISPATCH_STATES)), len(DISPATCH_STATES))
        self.assertEqual(
            set(DISPATCH_STATES),
            {DISPATCH_CAPTURED, DISPATCH_STARTED, DISPATCH_REFUSED,
             DISPATCH_TURN_OPENED},
        )

    def test_dispatch_state_is_not_capture_state(self):
        """Different dimensions. Capture quality is not dispatch progress."""
        from cofferdam.workstation.tasks.gitbaseline import CAPTURE_STATES

        self.assertNotEqual(set(CAPTURE_STATES), set(DISPATCH_STATES))
        # A capture can be `unavailable` and still be dispatched against, and a
        # capture can be perfect and never dispatched. Neither implies the other.
        self.assertNotIn(DISPATCH_STARTED, CAPTURE_STATES)
        self.assertNotIn(DISPATCH_TURN_OPENED, CAPTURE_STATES)


class StoreLifecycle(unittest.TestCase):
    """Enforced by the store, not by service convention."""

    def setUp(self):
        import tempfile

        from cofferdam.workstation.config import load_config

        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr4-life-")
        self.addCleanup(self._temp.cleanup)
        self.home = Path(self._temp.name)
        self.config = load_config(self.home)
        self.config.ensure_dirs()
        self.store = TaskStore(self.config)
        self.addCleanup(self.store.close)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"
        row, _ = self.store.create_task(
            origin="pwa", adapter_id="validation", project_id="synth",
            prompt="p", title="t",
        )
        self.task_id = row.task_id

    def _baseline(self, revision):
        return GitBaseline(
            capture_state=CAPTURE_CAPTURED,
            head_state=HEAD_PRESENT,
            head_revision=revision,
            object_format="sha1",
            working_tree_state="clean",
            status_coverage="complete",
        )

    def _reserve(self, revision):
        return self.store.reserve_turn_baseline(
            self.task_id, self._baseline(revision), captured_at="2026-08-15T00:00:00Z"
        )

    # -- pre-dispatch --------------------------------------------------------

    def test_a_fresh_reservation_is_captured_and_replaceable(self):
        self._reserve("a" * 40)
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(self.task_id, 1), DISPATCH_CAPTURED
        )
        self._reserve("b" * 40)
        self.assertEqual(
            self.store.turn_baseline(self.task_id, 1).head_revision, "b" * 40
        )

    # -- the blocker ---------------------------------------------------------

    def test_dispatch_started_freezes_the_boundary(self):
        """The fix. A missing turn row is no longer permission to overwrite."""
        self._reserve("a" * 40)
        self.store.mark_baseline_dispatch_started(self.task_id, 1)
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(self.task_id, 1), DISPATCH_STARTED
        )
        # No turn row exists — exactly the crash shape — and the retry must not win.
        self.assertEqual(self.store.turns(self.task_id), [])
        number = self._reserve("f" * 40)
        self.assertEqual(number, 1)
        self.assertEqual(
            self.store.turn_baseline(self.task_id, 1).head_revision, "a" * 40,
            "a post-dispatch retry overwrote the pre-work boundary",
        )

    def test_every_machine_fact_survives_a_post_dispatch_retry(self):
        original = GitBaseline(
            capture_state=CAPTURE_CAPTURED,
            head_state=HEAD_PRESENT,
            head_revision="a" * 40,
            object_format="sha1",
            working_tree_state="dirty",
            status_coverage="incomplete",
        )
        self.store.reserve_turn_baseline(
            self.task_id, original, captured_at="2026-08-15T00:00:00Z"
        )
        self.store.mark_baseline_dispatch_started(self.task_id, 1)
        self._reserve("c" * 40)
        stored = self.store.turn_baseline(self.task_id, 1)
        self.assertEqual(stored.head_revision, "a" * 40)
        self.assertEqual(stored.head_state, HEAD_PRESENT)
        self.assertEqual(stored.object_format, "sha1")
        self.assertEqual(stored.working_tree_state, "dirty")
        self.assertEqual(stored.status_coverage, "incomplete")
        self.assertEqual(stored.capture_state, CAPTURE_CAPTURED)

    def test_a_refusal_is_recorded_and_still_does_not_unfreeze(self):
        self._reserve("a" * 40)
        self.store.mark_baseline_dispatch_started(self.task_id, 1)
        self.store.mark_baseline_dispatch_refused(self.task_id, 1)
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(self.task_id, 1), DISPATCH_REFUSED
        )
        self._reserve("d" * 40)
        self.assertEqual(
            self.store.turn_baseline(self.task_id, 1).head_revision, "a" * 40,
            "a refusal was treated as proof the worker was untouched",
        )

    def test_a_retry_after_refusal_redispatches_the_same_boundary(self):
        self._reserve("a" * 40)
        self.store.mark_baseline_dispatch_started(self.task_id, 1)
        self.store.mark_baseline_dispatch_refused(self.task_id, 1)
        number = self._reserve("e" * 40)
        self.assertEqual(number, 1, "the retry took a different turn number")
        self.store.mark_baseline_dispatch_started(self.task_id, 1)
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(self.task_id, 1), DISPATCH_STARTED
        )
        self.assertEqual(
            self.store.turn_baseline(self.task_id, 1).head_revision, "a" * 40
        )

    def test_a_refusal_cannot_be_recorded_against_a_boundary_never_dispatched(self):
        self._reserve("a" * 40)
        self.store.mark_baseline_dispatch_refused(self.task_id, 1)
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(self.task_id, 1), DISPATCH_CAPTURED
        )

    # -- turn opened ---------------------------------------------------------

    def test_opening_the_turn_marks_the_boundary_in_the_same_transaction(self):
        self._reserve("a" * 40)
        self.store.mark_baseline_dispatch_started(self.task_id, 1)
        self.store.open_turn(
            self.task_id, provider="validation", source="internal_test",
            started_at="2026-08-15T00:00:01Z",
        )
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(self.task_id, 1),
            DISPATCH_TURN_OPENED,
        )

    def test_an_opened_turn_never_regresses(self):
        self._reserve("a" * 40)
        self.store.mark_baseline_dispatch_started(self.task_id, 1)
        self.store.open_turn(
            self.task_id, provider="validation", source="internal_test",
            started_at="2026-08-15T00:00:01Z",
        )
        self.store.mark_baseline_dispatch_started(self.task_id, 1)
        self.store.mark_baseline_dispatch_refused(self.task_id, 1)
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(self.task_id, 1),
            DISPATCH_TURN_OPENED,
        )

    def test_the_next_turn_gets_its_own_reservation(self):
        self._reserve("a" * 40)
        self.store.mark_baseline_dispatch_started(self.task_id, 1)
        self.store.open_turn(
            self.task_id, provider="validation", source="internal_test",
            started_at="2026-08-15T00:00:01Z",
        )
        number = self._reserve("b" * 40)
        self.assertEqual(number, 2)
        self.assertEqual(
            self.store.turn_baseline(self.task_id, 1).head_revision, "a" * 40
        )
        self.assertEqual(
            self.store.turn_baseline(self.task_id, 2).head_revision, "b" * 40
        )

    # -- restart -------------------------------------------------------------

    def test_every_state_survives_a_reopen(self):
        """One task per state, so nothing is asserted about a state it is not in."""
        wanted = {}
        for state in DISPATCH_STATES:
            row, _ = self.store.create_task(
                origin="pwa", adapter_id="validation", project_id="synth",
                prompt="p", title=state,
            )
            self.store.reserve_turn_baseline(
                row.task_id, self._baseline("a" * 40),
                captured_at="2026-08-15T00:00:00Z",
            )
            if state in (DISPATCH_STARTED, DISPATCH_REFUSED, DISPATCH_TURN_OPENED):
                self.store.mark_baseline_dispatch_started(row.task_id, 1)
            if state == DISPATCH_REFUSED:
                self.store.mark_baseline_dispatch_refused(row.task_id, 1)
            if state == DISPATCH_TURN_OPENED:
                self.store.open_turn(
                    row.task_id, provider="validation", source="internal_test",
                    started_at="2026-08-15T00:00:01Z",
                )
            wanted[row.task_id] = state

        for task_id, state in wanted.items():
            self.assertEqual(
                self.store.turn_baseline_dispatch_state(task_id, 1), state
            )

        self.store.close()
        reopened = TaskStore(self.config)
        self.addCleanup(reopened.close)
        for task_id, state in wanted.items():
            self.assertEqual(
                reopened.turn_baseline_dispatch_state(task_id, 1), state,
                "state %s did not survive the reopen" % state,
            )
            # And nothing that survived as frozen came back replaceable.
            if state != DISPATCH_CAPTURED:
                reopened.reserve_turn_baseline(
                    task_id, self._baseline("7" * 40),
                    captured_at="2026-08-15T03:00:00Z",
                )
                self.assertEqual(
                    reopened.turn_baseline(task_id, 1).head_revision, "a" * 40,
                    "%s became replaceable after a restart" % state,
                )

    def test_a_frozen_state_survives_a_reopen_and_stays_frozen(self):
        self._reserve("a" * 40)
        self.store.mark_baseline_dispatch_started(self.task_id, 1)
        self.store.close()
        reopened = TaskStore(self.config)
        self.addCleanup(reopened.close)
        self.assertEqual(
            reopened.turn_baseline_dispatch_state(self.task_id, 1), DISPATCH_STARTED
        )
        reopened.reserve_turn_baseline(
            self.task_id, self._baseline("9" * 40), captured_at="2026-08-15T01:00:00Z"
        )
        self.assertEqual(
            reopened.turn_baseline(self.task_id, 1).head_revision, "a" * 40,
            "a restart made the boundary replaceable again",
        )

    def test_a_missing_dispatch_state_is_not_writable(self):
        """No DEFAULT: a row that fails to say how far it got is refused outright."""
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (task_id, turn_number, capture_state, head_state,"
                    " head_revision, object_format, working_tree_state,"
                    " status_coverage, reason, captured_at) VALUES"
                    " ('task_x', 1, 'captured', 'unborn', NULL, NULL, 'unknown',"
                    " 'unavailable', 'unborn_head', '2026-08-15T00:00:00Z')"
                    % BASELINE_TABLE
                )

    def test_the_dispatch_vocabulary_is_closed_in_the_schema(self):
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO %s (task_id, turn_number, capture_state, head_state,"
                    " head_revision, object_format, working_tree_state,"
                    " status_coverage, reason, dispatch_state, captured_at) VALUES"
                    " ('task_x', 1, 'captured', 'unborn', NULL, NULL, 'unknown',"
                    " 'unavailable', 'unborn_head', 'probably_started',"
                    " '2026-08-15T00:00:00Z')" % BASELINE_TABLE
                )

    def test_a_missing_baseline_has_no_dispatch_state(self):
        self.assertIsNone(self.store.turn_baseline_dispatch_state(self.task_id, 1))
        self.assertIsNone(self.store.turn_baseline_dispatch_state("task_nope", 1))


# -- through the real service ------------------------------------------------


class LifecycleAdapter(TaskAdapter):
    """Reads durable state at its first instruction, then behaves as scripted."""

    adapter_id = "lifecycle"
    display_name = "Lifecycle adapter"
    description = "A test double."

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self.seen: List[Dict[str, Any]] = []
        #: What to do once control arrives: "accept", "refuse", or "crash".
        self.behaviour = "accept"
        #: Run before answering — used to simulate a worker moving HEAD.
        self.side_effect = None

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
                "SELECT turn_number, head_revision, dispatch_state FROM %s"
                " WHERE task_id = ? ORDER BY turn_number" % BASELINE_TABLE,
                (context.task_id,),
            ).fetchall()
            turns = connection.execute(
                "SELECT turn_number FROM task_turns WHERE task_id = ?",
                (context.task_id,),
            ).fetchall()
        finally:
            connection.close()
        self.seen.append(
            {"call": call, "rows": [tuple(r) for r in rows],
             "turns": [r[0] for r in turns]}
        )

    def _answer(self, context, call):
        self._look(context, call)
        if self.side_effect is not None:
            self.side_effect()
        if self.behaviour == "refuse":
            raise AdapterRefusal("not today")
        if self.behaviour == "crash":
            raise RuntimeError("the worker host died")
        return AdapterOutcome(
            events=(AdapterEvent(text="ok"),),
            requested_state="ready_for_followup",
        )

    def start(self, context: TaskContext) -> AdapterOutcome:
        return self._answer(context, "start")

    def send_followup(self, context: TaskContext, followup: str) -> AdapterOutcome:
        return self._answer(context, "send_followup")

    def session_available(self, task_id: str) -> bool:
        return True

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        return AdapterOutcome(events=(), requested_state="cancelled")


@unittest.skipIf(GIT is None, "git is not installed")
class ServiceLifecycle(TaskTestCase):
    project_adapters = ("lifecycle",)

    def extra_adapters(self) -> Sequence[TaskAdapter]:
        self.adapter = LifecycleAdapter(
            self.home / "state" / "tasks" / "tasks.sqlite3"
        )
        return (self.adapter,)

    def setUp(self):
        super().setUp()
        self.git("init", "-q", ".")
        self.write("seed.txt", "one\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")

    def git(self, *args):
        subprocess.run(
            [GIT, *args], cwd=str(self.project_root), check=True,
            capture_output=True, env={**GIT_ENV, "HOME": str(self.project_root)},
        )

    def write(self, relative, body="x\n"):
        target = self.project_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def head(self):
        return subprocess.run(
            [GIT, "rev-parse", "HEAD"], cwd=str(self.project_root), check=True,
            capture_output=True, env={**GIT_ENV, "HOME": str(self.project_root)},
        ).stdout.decode().strip()

    def create(self):
        row, _ = self.service.create_task(
            prompt="do it", project_id=PROJECT_ID,
            adapter_id="lifecycle", origin="pwa",
        )
        return row.task_id

    # -- the dispatch boundary ----------------------------------------------

    def test_dispatch_started_is_durable_before_the_first_instruction(self):
        """Separate read-only connection, from inside the adapter's own moment."""
        head = self.head()
        self.create()
        seen = self.adapter.seen[0]
        self.assertEqual(len(seen["rows"]), 1)
        turn_number, revision, dispatch_state = seen["rows"][0]
        self.assertEqual(turn_number, 1)
        self.assertEqual(revision, head)
        self.assertEqual(dispatch_state, DISPATCH_STARTED)
        self.assertEqual(seen["turns"], [], "the turn row existed too early")

    def test_a_successful_start_ends_at_turn_opened(self):
        task_id = self.create()
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(task_id, 1), DISPATCH_TURN_OPENED
        )

    def test_a_refused_start_ends_at_refused_with_no_turn(self):
        self.adapter.behaviour = "refuse"
        task_id = self.create()
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(task_id, 1), DISPATCH_REFUSED
        )
        self.assertEqual(self.store.turns(task_id), [])
        self.assertIsNotNone(self.store.turn_baseline(task_id, 1))

    def test_a_faulting_start_ends_at_refused_with_no_turn(self):
        self.adapter.behaviour = "crash"
        task_id = self.create()
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(task_id, 1), DISPATCH_REFUSED
        )
        self.assertEqual(self.store.turns(task_id), [])

    # -- the crash case, pinned exactly --------------------------------------

    def test_a_worker_that_moved_head_then_crashed_keeps_its_original_boundary(self):
        """The exact scenario the blocker describes, start to finish."""
        h0 = self.head()

        def worker_commits_then_the_host_dies():
            self.write("worker_made_this.py", "print('work')\n")
            self.git("add", "-A")
            self.git("commit", "-qm", "worker commit")

        self.adapter.side_effect = worker_commits_then_the_host_dies
        self.adapter.behaviour = "crash"
        task_id = self.create()
        h1 = self.head()
        self.assertNotEqual(h0, h1)

        # No turn row — the crash shape — and the boundary is frozen.
        self.assertEqual(self.store.turns(task_id), [])
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(task_id, 1), DISPATCH_REFUSED
        )

        # Restart, and try to reserve again exactly as a retry would.
        self.store.close()
        reopened = TaskStore(self.config)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.turns(task_id), [])
        reopened.reserve_turn_baseline(
            task_id,
            GitBaseline(
                capture_state=CAPTURE_CAPTURED, head_state=HEAD_PRESENT,
                head_revision=h1, object_format="sha1",
                working_tree_state="clean", status_coverage="complete",
            ),
            captured_at="2026-08-15T02:00:00Z",
        )
        stored = reopened.turn_baseline(task_id, 1)
        self.assertEqual(
            stored.head_revision, h0,
            "the worker's own commit became the pre-work boundary",
        )
        self.assertNotEqual(stored.head_revision, h1)

    # -- follow-up -----------------------------------------------------------

    def test_the_follow_up_freezes_its_own_boundary_before_dispatch(self):
        task_id = self.create()
        self.write("between.py", "x\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "between turns")
        second = self.head()

        self.service.send_followup(task_id, "next")
        seen = self.adapter.seen[1]
        self.assertEqual(seen["call"], "send_followup")
        by_turn = {r[0]: r for r in seen["rows"]}
        self.assertEqual(by_turn[2][1], second)
        self.assertEqual(by_turn[2][2], DISPATCH_STARTED)
        self.assertEqual(seen["turns"], [1], "turn two existed too early")
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(task_id, 2), DISPATCH_TURN_OPENED
        )

    def test_a_refused_follow_up_freezes_and_a_retry_keeps_the_boundary(self):
        from cofferdam.workstation.tasks.errors import SessionUnavailable

        task_id = self.create()
        first_boundary = self.head()
        self.adapter.behaviour = "refuse"
        with self.assertRaises(SessionUnavailable):
            self.service.send_followup(task_id, "next")
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(task_id, 2), DISPATCH_REFUSED
        )
        self.assertEqual(self.store.turn_baseline(task_id, 2).head_revision,
                         first_boundary)

        # The repository moves, and the follow-up is retried.
        self.write("after_refusal.py", "x\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "after refusal")
        self.adapter.behaviour = "accept"
        self.service.send_followup(task_id, "next, again")

        self.assertEqual(
            self.store.turn_baseline(task_id, 2).head_revision, first_boundary,
            "the retry redrew the line after a refusal that proves nothing",
        )
        self.assertEqual(
            self.store.turn_baseline_dispatch_state(task_id, 2), DISPATCH_TURN_OPENED
        )
        self.assertEqual([t.turn_number for t in self.store.turns(task_id)], [1, 2])

    def test_a_follow_up_worker_that_committed_then_crashed_keeps_its_boundary(self):
        task_id = self.create()
        h0 = self.head()

        def commits(self=self):
            self.write("followup_work.py", "x\n")
            self.git("add", "-A")
            self.git("commit", "-qm", "followup work")

        self.adapter.side_effect = commits
        self.adapter.behaviour = "crash"
        self.service.send_followup(task_id, "go")
        h1 = self.head()
        self.assertNotEqual(h0, h1)
        self.assertEqual(
            self.store.turn_baseline(task_id, 2).head_revision, h0
        )
        self.assertEqual(self.store.turn_baseline_dispatch_state(task_id, 2),
                         DISPATCH_REFUSED)
        self.assertEqual([t.turn_number for t in self.store.turns(task_id)], [1])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
