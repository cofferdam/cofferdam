"""M2K PR4 — who owns the boundary, and the immutability that follows.

The baseline is only worth storing if nothing downstream can choose it. An
adapter that could name the revision could name a convenient one; a prompt that
could name the repository root could point the boundary at a repository nobody
audited. So this module tests the negative space: what the adapter, the provider,
the task prompt and the API caller are structurally unable to influence.

It also pins the immutability rule, which has one deliberate hole in it. A
dispatch that captured a boundary and then had the adapter refuse leaves a row
for a turn that never opened, and the next attempt must be allowed to re-capture
— otherwise a refused start would poison a task forever. That window closes the
moment the turn exists.
"""

from __future__ import annotations

import inspect
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
    AdapterRefusal,
    TaskAdapter,
    TaskContext,
)
from cofferdam.workstation.tasks import gitbaseline, service as service_module
from cofferdam.workstation.tasks.gitbaseline import (
    CAPTURE_CAPTURED,
    CAPTURE_UNAVAILABLE,
    HEAD_NOT_A_REPOSITORY,
    HEAD_PRESENT,
    HEAD_UNAVAILABLE,
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


class TheAdapterHasNoSay(unittest.TestCase):
    """Proven by signature and by import graph, not by asking adapters nicely."""

    def test_capture_takes_only_a_root_and_a_test_runner(self):
        parameters = inspect.signature(gitbaseline.capture_baseline).parameters
        self.assertEqual(list(parameters), ["root", "runner", "attempts"])
        self.assertEqual(parameters["runner"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(parameters["attempts"].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_no_public_entry_point_accepts_a_revision(self):
        """Tested on the callable surface rather than by grepping prose."""
        for name in dir(gitbaseline):
            value = getattr(gitbaseline, name)
            if not callable(value) or name.startswith("_"):
                continue
            try:
                parameters = set(inspect.signature(value).parameters)
            except (TypeError, ValueError):  # dataclasses, builtins
                continue
            for forbidden in ("revision", "revspec", "ref", "commit", "since", "range"):
                self.assertNotIn(forbidden, parameters, "%s(%s)" % (name, forbidden))

    def test_the_service_passes_only_the_verified_root(self):
        """The call itself, with the docstring stripped so prose cannot pass or fail it."""
        method = service_module.TaskService._record_pre_work_baseline
        # Everything after the docstring's closing quotes is the executable body.
        body = inspect.getsource(method).split('"""')[2]
        self.assertIn("capture_baseline(root)", body)
        # Nothing from the adapter's context, its outcome, the prompt or the
        # follow-up text reaches the probe, and no test hook is wired in.
        for forbidden in ("context.", "outcome.", "followup", "prompt", "runner="):
            self.assertNotIn(forbidden, body)

    def test_the_store_never_runs_git(self):
        from cofferdam.workstation.tasks import store as store_module

        source = inspect.getsource(store_module)
        for forbidden in ("subprocess", "Popen", "os.system"):
            self.assertNotIn(forbidden, source)

    def test_the_baseline_module_never_touches_the_database(self):
        source = inspect.getsource(gitbaseline)
        for forbidden in ("sqlite3", "INSERT", "UPDATE", "DELETE", "commit()"):
            self.assertNotIn(forbidden, source)

    def test_the_host_probe_does_not_depend_on_any_adapter(self):
        """A daemon's Git authority must not be a detail of one adapter's package."""
        source = inspect.getsource(gitbaseline)
        self.assertNotIn("adapters", source.split('"""', 2)[-1])


class RefusingAdapter(TaskAdapter):
    """Refuses to start, after the host has already captured a boundary."""

    adapter_id = "refuser"
    display_name = "Refusing adapter"
    description = "A test double that refuses."

    def __init__(self) -> None:
        self.refuse = True
        self.starts = 0

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            start=True, followup=True, cancel=True, final_result=True
        )

    def available(self) -> bool:
        return True

    def start(self, context: TaskContext) -> AdapterOutcome:
        self.starts += 1
        if self.refuse:
            raise AdapterRefusal("not today")
        return AdapterOutcome(
            events=(AdapterEvent(text="ok"),), requested_state="ready_for_followup"
        )

    def session_available(self, task_id: str) -> bool:
        return True

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        return AdapterOutcome(events=(), requested_state="cancelled")


@unittest.skipIf(GIT is None, "git is not installed")
class TheRootComesFromProjectAuthority(TaskTestCase):
    project_adapters = ("refuser",)

    def extra_adapters(self) -> Sequence[TaskAdapter]:
        self.refuser = RefusingAdapter()
        return (self.refuser,)

    def setUp(self):
        super().setUp()
        subprocess.run(
            [GIT, "init", "-q", "."], cwd=str(self.project_root), check=True,
            capture_output=True, env={**GIT_ENV, "HOME": str(self.project_root)},
        )
        (self.project_root / "seed.txt").write_text("one\n")
        for args in (("add", "-A"), ("commit", "-qm", "base")):
            subprocess.run(
                [GIT, *args], cwd=str(self.project_root), check=True,
                capture_output=True, env={**GIT_ENV, "HOME": str(self.project_root)},
            )

    def _create(self, prompt="do it"):
        row, _created = self.service.create_task(
            prompt=prompt,
            project_id=PROJECT_ID,
            adapter_id="refuser",
            origin="pwa",
        )
        return row.task_id

    def test_a_prompt_that_looks_like_a_revision_changes_nothing(self):
        """The prompt is user content. It reaches no Git argument."""
        head = subprocess.run(
            [GIT, "rev-parse", "HEAD"], cwd=str(self.project_root), check=True,
            capture_output=True, env={**GIT_ENV, "HOME": str(self.project_root)},
        ).stdout.decode().strip()
        self.refuser.refuse = False
        task_id = self._create(prompt="use HEAD~5 and /etc/passwd as the baseline")
        stored = self.store.turn_baseline(task_id, 1)
        self.assertEqual(stored.head_revision, head)
        self.assertEqual(stored.head_state, HEAD_PRESENT)

    def test_a_refused_start_still_leaves_the_boundary_it_captured(self):
        """Captured before the refusal, and honestly kept: the turn never opened."""
        task_id = self._create()
        self.assertEqual(self.refuser.starts, 1)
        stored = self.store.turn_baseline(task_id, 1)
        self.assertIsNotNone(stored, "the pre-work boundary was lost on refusal")
        self.assertEqual(self.store.turns(task_id), [])

    def test_a_non_git_project_records_an_explicit_unavailable_boundary(self):
        """And the task still runs. Git evidence is not a precondition for work."""
        shutil.rmtree(self.project_root / ".git")
        self.refuser.refuse = False
        task_id = self._create()
        stored = self.store.turn_baseline(task_id, 1)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.capture_state, CAPTURE_UNAVAILABLE)
        self.assertEqual(stored.head_state, HEAD_NOT_A_REPOSITORY)
        self.assertIsNone(stored.head_revision)
        # The work itself was not blocked by it.
        self.assertEqual(self.refuser.starts, 1)


class TheStoreEnforcesTheLifecycle(unittest.TestCase):
    """Replacement is allowed only in the pre-work window, and never after."""

    def setUp(self):
        import tempfile

        from cofferdam.workstation.config import load_config

        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr4-auth-")
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

    def test_it_reserves_the_number_the_turn_will_take(self):
        number = self.store.reserve_turn_baseline(
            self.task_id, self._baseline("a" * 40), captured_at="2026-08-15T00:00:00Z"
        )
        self.assertEqual(number, 1)
        self.store.open_turn(
            self.task_id, provider="validation", source="internal_test",
            started_at="2026-08-15T00:00:01Z",
        )
        self.assertEqual(self.store.turns(self.task_id)[0].turn_number, 1)

    def test_a_retry_before_the_turn_opens_replaces_the_transient_attempt(self):
        self.store.reserve_turn_baseline(
            self.task_id, self._baseline("a" * 40), captured_at="2026-08-15T00:00:00Z"
        )
        self.store.reserve_turn_baseline(
            self.task_id, self._baseline("b" * 40), captured_at="2026-08-15T00:00:02Z"
        )
        stored = self.store.turn_baseline(self.task_id, 1)
        self.assertEqual(stored.head_revision, "b" * 40)
        with sqlite3.connect(str(self.path)) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM task_turn_git_baselines WHERE task_id=?",
                (self.task_id,),
            ).fetchone()[0]
        self.assertEqual(count, 1, "a retry must not leave two boundaries behind")

    def test_once_the_turn_is_open_the_boundary_is_immutable(self):
        self.store.reserve_turn_baseline(
            self.task_id, self._baseline("a" * 40), captured_at="2026-08-15T00:00:00Z"
        )
        self.store.open_turn(
            self.task_id, provider="validation", source="internal_test",
            started_at="2026-08-15T00:00:01Z",
        )
        # The next reservation is turn two's, and it cannot touch turn one.
        number = self.store.reserve_turn_baseline(
            self.task_id, self._baseline("c" * 40), captured_at="2026-08-15T00:00:03Z"
        )
        self.assertEqual(number, 2)
        self.assertEqual(
            self.store.turn_baseline(self.task_id, 1).head_revision, "a" * 40
        )
        self.assertEqual(
            self.store.turn_baseline(self.task_id, 2).head_revision, "c" * 40
        )

    def test_a_missing_baseline_reads_as_none_and_never_as_clean(self):
        self.assertIsNone(self.store.turn_baseline(self.task_id, 1))
        self.assertIsNone(self.store.turn_baseline("task_does_not_exist", 1))

    def test_an_unknown_task_is_refused(self):
        from cofferdam.workstation.tasks.errors import TaskUnknown

        with self.assertRaises(TaskUnknown):
            self.store.reserve_turn_baseline(
                "task_nope", self._baseline("a" * 40),
                captured_at="2026-08-15T00:00:00Z",
            )

    def test_only_a_host_captured_value_is_accepted(self):
        """A dict, a tuple or an adapter's own object is not a baseline."""
        from cofferdam.workstation.tasks.errors import TaskError

        for impostor in ({"head_revision": "a" * 40}, ("a" * 40,), "a" * 40, None):
            with self.assertRaises(TaskError, msg=repr(impostor)):
                self.store.reserve_turn_baseline(
                    self.task_id, impostor, captured_at="2026-08-15T00:00:00Z"
                )

    def test_deleting_the_task_cascades(self):
        self.store.reserve_turn_baseline(
            self.task_id, self._baseline("a" * 40), captured_at="2026-08-15T00:00:00Z"
        )
        with sqlite3.connect(str(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("DELETE FROM tasks WHERE task_id = ?", (self.task_id,))
            remaining = db.execute(
                "SELECT COUNT(*) FROM task_turn_git_baselines"
            ).fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_an_unavailable_capture_is_stored_as_such(self):
        self.store.reserve_turn_baseline(
            self.task_id,
            GitBaseline(
                capture_state=CAPTURE_UNAVAILABLE,
                head_state=HEAD_UNAVAILABLE,
                reason="probe_timeout",
            ),
            captured_at="2026-08-15T00:00:00Z",
        )
        stored = self.store.turn_baseline(self.task_id, 1)
        self.assertEqual(stored.capture_state, CAPTURE_UNAVAILABLE)
        self.assertIsNone(stored.head_revision)
        self.assertEqual(stored.reason, "probe_timeout")
        self.assertEqual(stored.working_tree_state, "unknown")

    def test_the_stored_row_carries_no_path_and_no_root(self):
        self.store.reserve_turn_baseline(
            self.task_id, self._baseline("a" * 40), captured_at="2026-08-15T00:00:00Z"
        )
        with sqlite3.connect(str(self.path)) as db:
            row = db.execute(
                "SELECT * FROM task_turn_git_baselines WHERE task_id=?",
                (self.task_id,),
            ).fetchone()
        rendered = " ".join(str(value) for value in row)
        self.assertNotIn(str(self.home), rendered)
        self.assertNotIn("/", rendered.replace("2026-08-15T00:00:00Z", ""))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
