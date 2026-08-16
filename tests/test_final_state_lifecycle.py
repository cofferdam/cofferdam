"""M2K PR14 — when the observation is taken, what it is taken of, and that it never moves.

Three properties, each of which is the whole point of a design decision:

* **Taken at the boundary.** After the worker returns, after PR5's committed-range
  observation, and before the turn is durably closed — pinned mechanically, not
  by reading the call order.
* **Selected by the resolved active set.** Not the whole repository, not the
  current snapshot; and where the lineage is unavailable, an explicit refusal
  rather than a plausible substitute.
* **Frozen.** Reading it later returns the stored row. It cannot be replaced, it
  cannot be re-derived from a repository that has since changed, and deleting the
  project does not alter a single answer.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from cofferdam.workstation.config import load_config
from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterOutcome,
    TaskAdapter,
)
from cofferdam.workstation.tasks.finalstate import (
    FINAL_STATE_OBSERVER_VERSION,
    KIND_FILE,
    KIND_SYMLINK,
    OBSERVATION_COMPLETE,
    OBSERVATION_INCOMPLETE,
    OBSERVATION_LEGACY_UNKNOWN,
    OBSERVATION_UNAVAILABLE,
    PATH_ABSENT,
    PATH_PRESENT,
    PATH_UNAVAILABLE,
    REASON_LINEAGE_UNAVAILABLE,
    REASON_SYMLINK_TRAVERSAL_REFUSED,
    REASON_TARGET_LIMIT_EXCEEDED,
)
from cofferdam.workstation.tasks.projects import load_projects
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

PROJECT_ID = "demo"


class Worker(TaskAdapter):
    """An adapter that changes the project, so there is something to observe."""

    adapter_id = "validation"
    display_name = "Worker"

    def __init__(self, action=None, followup_action=None, watcher=None,
                 requested_state="completed"):
        self._action = action
        self._followup_action = followup_action
        self._watcher = watcher
        self._requested_state = requested_state
        self.root = None
        self.observed = {}

    def capabilities(self):
        return AdapterCapabilities(start=True, followup=True, final_result=True)

    def available(self):
        return True

    def session_available(self, task_id):
        return True

    def _run(self, context, action):
        self.root = Path(context.project_root)
        if self._watcher is not None:
            self._watcher(self, context)
        if action is not None:
            action(self.root)
        return AdapterOutcome(
            requested_state=self._requested_state, final_result="done"
        )

    def start(self, context):
        return self._run(context, self._action)

    def send_followup(self, context, followup):
        return self._run(context, self._followup_action)


class FinalStateCase(unittest.TestCase):
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
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def criteria_for(self, *paths):
        return [
            {"kind": "evidence", "predicate": "path_changed", "path": path}
            for path in paths
        ]

    def start(self, adapter, paths, continuity=None):
        service = self.service(adapter)
        row, _ = service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt="scenario: complete",
            origin="pwa",
            criteria=self.criteria_for(*paths),
            continuity=continuity if continuity is not None else {"mode": "root"},
        )
        return service, row

    def final(self, task_id, turn=1):
        return self.store.turn_final_state(task_id, turn)

    def states(self, task_id, turn=1):
        return {
            item.path: (item.state, item.kind)
            for item in self.final(task_id, turn).paths
        }

    def digest(self):
        return hashlib.sha256(self.database.read_bytes()).hexdigest()


# -- the capture boundary -----------------------------------------------------


class CaptureOrderingTests(FinalStateCase):
    def test_the_observation_exists_and_the_turn_is_open_when_it_is_taken(self):
        """The ordering, pinned from inside the write rather than by reading code.

        A store double records the state of the turn row at the instant the
        final-state row is written: the turn must already exist (so the fact has
        somewhere to belong) and must not yet be closed (so it describes the
        boundary rather than something observed afterwards).
        """
        seen = {}
        original = self.store.record_final_state

        def watched(task_id, turn_number, **kwargs):
            with self.sql() as connection:
                row = connection.execute(
                    "SELECT completed_at FROM task_turns"
                    " WHERE task_id = ? AND turn_number = ?",
                    (task_id, turn_number),
                ).fetchone()
                seen["turn_exists"] = row is not None
                seen["turn_open"] = row is not None and row["completed_at"] is None
            return original(task_id, turn_number, **kwargs)

        self.store.record_final_state = watched
        try:
            adapter = Worker(action=lambda root: (root / "a.txt").write_text("x"))
            self.start(adapter, ["a.txt"])
        finally:
            del self.store.record_final_state

        self.assertTrue(seen["turn_exists"], "the turn row did not exist yet")
        self.assertTrue(seen["turn_open"], "the turn was already closed")

    def test_the_committed_range_is_observed_before_the_final_state(self):
        """PR5 first, then PR14, then the close. Recorded, not read off the source."""
        order = []
        service = self.service(
            Worker(action=lambda root: (root / "a.txt").write_text("x"))
        )
        for name in ("_record_committed_range", "_record_final_state"):
            original = getattr(service, name)

            def wrapper(*args, _name=name, _original=original, **kwargs):
                order.append(_name)
                return _original(*args, **kwargs)

            setattr(service, name, wrapper)
        original_apply = service._apply

        def apply(*args, **kwargs):
            order.append("_apply")
            return original_apply(*args, **kwargs)

        service._apply = apply
        service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt="scenario: complete",
            origin="pwa",
            criteria=self.criteria_for("a.txt"),
            continuity={"mode": "root"},
        )
        self.assertEqual(
            order, ["_record_committed_range", "_record_final_state", "_apply"]
        )

    def test_the_worker_ran_before_the_observation(self):
        adapter = Worker(action=lambda root: (root / "made.txt").write_text("x"))
        _, row = self.start(adapter, ["made.txt"])
        self.assertEqual(
            self.states(row.task_id)["made.txt"], (PATH_PRESENT, KIND_FILE)
        )

    def test_the_turn_is_closed_afterwards(self):
        adapter = Worker(action=lambda root: (root / "a.txt").write_text("x"))
        _, row = self.start(adapter, ["a.txt"])
        with self.sql() as connection:
            completed = connection.execute(
                "SELECT completed_at FROM task_turns WHERE task_id = ?",
                (row.task_id,),
            ).fetchone()["completed_at"]
        self.assertIsNotNone(completed)
        self.assertTrue(self.final(row.task_id).recorded)


class WorktreeAuthorityTests(FinalStateCase):
    def test_an_uncommitted_creation_is_present(self):
        adapter = Worker(action=lambda root: (root / "bar.py").write_text("x"))
        _, row = self.start(adapter, ["bar.py"])
        self.assertEqual(self.states(row.task_id)["bar.py"], (PATH_PRESENT, KIND_FILE))
        # HEAD does not contain it, and that changes nothing.
        listed = subprocess.run(
            ("git", "ls-tree", "--name-only", "HEAD"),
            cwd=self.root,
            capture_output=True,
            text=True,
        ).stdout.split()
        self.assertNotIn("bar.py", listed)

    def test_an_uncommitted_deletion_is_absent(self):
        """HEAD still has it. The effective project does not."""
        adapter = Worker(action=lambda root: (root / "seed.txt").unlink())
        _, row = self.start(adapter, ["seed.txt"])
        self.assertEqual(self.states(row.task_id)["seed.txt"], (PATH_ABSENT, None))
        listed = subprocess.run(
            ("git", "ls-tree", "--name-only", "HEAD"),
            cwd=self.root,
            capture_output=True,
            text=True,
        ).stdout.split()
        self.assertIn("seed.txt", listed)

    def test_the_head_anchor_is_recorded_but_is_not_the_authority(self):
        adapter = Worker(action=lambda root: (root / "seed.txt").unlink())
        _, row = self.start(adapter, ["seed.txt"])
        observation = self.final(row.task_id)
        self.assertTrue(observation.head_revision)
        # The anchor names a revision that *does* contain the file, while the
        # observation says it is gone. Both are true of different things.
        self.assertEqual(observation.paths[0].state, PATH_ABSENT)

    def test_the_index_does_not_override_the_worktree(self):
        """`git rm --cached` empties the index and leaves the file on disk."""

        def stage_removal(root):
            subprocess.run(
                ("git", "rm", "--cached", "-q", "seed.txt"),
                cwd=root,
                check=True,
                capture_output=True,
            )

        adapter = Worker(action=stage_removal)
        _, row = self.start(adapter, ["seed.txt"])
        self.assertTrue((self.root / "seed.txt").exists())
        self.assertEqual(
            self.states(row.task_id)["seed.txt"], (PATH_PRESENT, KIND_FILE)
        )

    def test_a_worker_created_symlink(self):
        def link(root):
            os.symlink("seed.txt", root / "alias.txt")

        adapter = Worker(action=link)
        _, row = self.start(adapter, ["alias.txt"])
        self.assertEqual(
            self.states(row.task_id)["alias.txt"], (PATH_PRESENT, KIND_SYMLINK)
        )


# -- target scope -------------------------------------------------------------


class ActiveScopeTests(FinalStateCase):
    def follow_up(self, service, task_id, adapter, paths, continuity):
        return service.send_followup(
            task_id, "more", criteria=self.criteria_for(*paths), continuity=continuity
        )

    def snapshot_id(self, task_id, turn):
        return self.store.turn_criteria(task_id, turn).snapshot_id

    def test_root_observes_its_own_criteria(self):
        adapter = Worker(action=lambda root: (root / "a.txt").write_text("x"))
        _, row = self.start(adapter, ["a.txt"])
        self.assertEqual(sorted(self.states(row.task_id)), ["a.txt"])

    def test_extend_observes_the_inherited_set_too(self):
        adapter = Worker(
            action=lambda root: (root / "a.txt").write_text("x"),
            followup_action=lambda root: (root / "b.txt").write_text("y"),
            requested_state="ready_for_followup",
        )
        service, row = self.start(adapter, ["a.txt"])
        self.follow_up(
            service,
            row.task_id,
            adapter,
            ["b.txt"],
            {"mode": "extend", "predecessor_snapshot_id": self.snapshot_id(row.task_id, 1)},
        )
        self.assertEqual(sorted(self.states(row.task_id, 2)), ["a.txt", "b.txt"])

    def test_revise_observes_the_survivors_and_the_new_criterion(self):
        adapter = Worker(
            action=lambda root: None,
            followup_action=lambda root: None,
            requested_state="ready_for_followup",
        )
        service, row = self.start(adapter, ["a.txt", "keep.txt"])
        criterion = self.store.turn_criteria(row.task_id, 1).criteria[0].criterion_id
        self.follow_up(
            service,
            row.task_id,
            adapter,
            ["c.txt"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snapshot_id(row.task_id, 1),
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": criterion}
                ],
            },
        )
        self.assertEqual(sorted(self.states(row.task_id, 2)), ["c.txt", "keep.txt"])

    def test_replace_cuts_the_older_scope(self):
        adapter = Worker(
            action=lambda root: None,
            followup_action=lambda root: None,
            requested_state="ready_for_followup",
        )
        service, row = self.start(adapter, ["a.txt", "b.txt"])
        self.follow_up(
            service,
            row.task_id,
            adapter,
            ["only.txt"],
            {
                "mode": "replace",
                "predecessor_snapshot_id": self.snapshot_id(row.task_id, 1),
            },
        )
        self.assertEqual(sorted(self.states(row.task_id, 2)), ["only.txt"])

    def test_an_undeclared_turn_has_no_guessed_scope(self):
        """No fallback to the current snapshot: that is not the active set."""
        adapter = Worker(
            action=lambda root: None,
            followup_action=lambda root: None,
            requested_state="ready_for_followup",
        )
        service, row = self.start(adapter, ["a.txt"])
        service.send_followup(
            row.task_id, "more", criteria=self.criteria_for("b.txt"), continuity=None
        )
        observation = self.final(row.task_id, 2)
        self.assertEqual(observation.state, OBSERVATION_UNAVAILABLE)
        self.assertEqual(observation.limitation_reason, REASON_LINEAGE_UNAVAILABLE)
        self.assertEqual(observation.paths, ())
        self.assertEqual(observation.path_count, 0)

    def test_an_empty_active_set_is_complete_with_no_paths(self):
        """A real answer: nothing was required, so nothing needed observing."""
        adapter = Worker(action=lambda root: None)
        _, row = self.start(adapter, [])
        observation = self.final(row.task_id)
        self.assertEqual(observation.state, OBSERVATION_COMPLETE)
        self.assertEqual(observation.path_count, 0)
        self.assertIsNone(observation.limitation_reason)
        # And it says nothing whatever about acceptance.
        for forbidden in ("met", "passed", "outcome", "verdict"):
            self.assertFalse(hasattr(observation, forbidden))

    def test_a_rename_criterion_contributes_both_endpoints(self):
        adapter = Worker(action=lambda root: None)
        service = self.service(adapter)
        row, _ = service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt="scenario: complete",
            origin="pwa",
            criteria=[
                {
                    "kind": "evidence",
                    "predicate": "rename",
                    "path": "old.txt",
                    "to_path": "new.txt",
                }
            ],
            continuity={"mode": "root"},
        )
        self.assertEqual(sorted(self.states(row.task_id)), ["new.txt", "old.txt"])

    def test_over_the_target_bound_is_refused_rather_than_truncated(self):
        import cofferdam.workstation.tasks.service as service_module
        import cofferdam.workstation.tasks.finalstate as module

        original = module.MAX_FINAL_STATE_TARGETS
        module.MAX_FINAL_STATE_TARGETS = 1
        try:
            adapter = Worker(action=lambda root: None)
            _, row = self.start(adapter, ["a.txt", "b.txt"])
        finally:
            module.MAX_FINAL_STATE_TARGETS = original
        observation = self.final(row.task_id)
        self.assertEqual(observation.state, OBSERVATION_UNAVAILABLE)
        self.assertEqual(observation.limitation_reason, REASON_TARGET_LIMIT_EXCEEDED)
        self.assertEqual(observation.paths, ())
        self.assertTrue(service_module is not None)


# -- partial observation ------------------------------------------------------


class IncompleteTests(FinalStateCase):
    def test_one_unobservable_path_makes_the_whole_thing_incomplete(self):
        """The observed paths are kept; the snapshot says it is not whole."""
        outside = Path(self._home.name) / "outside"
        outside.mkdir()
        (outside / "data.txt").write_text("s", encoding="utf-8")

        def link(root):
            os.symlink(str(outside), root / "external")
            (root / "a.txt").write_text("x", encoding="utf-8")

        adapter = Worker(action=link)
        _, row = self.start(adapter, ["a.txt", "external/data.txt"])
        observation = self.final(row.task_id)
        self.assertEqual(observation.state, OBSERVATION_INCOMPLETE)
        self.assertEqual(
            observation.limitation_reason, REASON_SYMLINK_TRAVERSAL_REFUSED
        )
        self.assertFalse(observation.complete)
        states = self.states(row.task_id)
        self.assertEqual(states["a.txt"], (PATH_PRESENT, KIND_FILE))
        self.assertEqual(states["external/data.txt"], (PATH_UNAVAILABLE, None))

    def test_an_unobservable_path_is_never_recorded_as_absent(self):
        outside = Path(self._home.name) / "outside"
        outside.mkdir()
        (outside / "here.txt").write_text("s", encoding="utf-8")
        adapter = Worker(action=lambda root: os.symlink(str(outside), root / "external"))
        _, row = self.start(adapter, ["external/here.txt"])
        self.assertEqual(
            self.states(row.task_id)["external/here.txt"], (PATH_UNAVAILABLE, None)
        )

    def test_an_observation_failure_does_not_fail_the_task(self):
        outside = Path(self._home.name) / "outside"
        outside.mkdir()
        adapter = Worker(action=lambda root: os.symlink(str(outside), root / "external"))
        _, row = self.start(adapter, ["external/x.txt"])
        refreshed = self.store.get(row.task_id)
        self.assertEqual(refreshed.state, "completed")
        self.assertEqual(self.final(row.task_id).state, OBSERVATION_INCOMPLETE)


# -- immutability and read behaviour -----------------------------------------


class StoredAndFrozenTests(FinalStateCase):
    def test_an_existing_observation_is_never_replaced(self):
        adapter = Worker(action=lambda root: (root / "a.txt").write_text("x"))
        _, row = self.start(adapter, ["a.txt"])
        before = self.final(row.task_id)

        (self.root / "a.txt").unlink()
        self.store.record_final_state(
            row.task_id,
            1,
            state=OBSERVATION_UNAVAILABLE,
            limitation_reason=REASON_LINEAGE_UNAVAILABLE,
            lineage_fingerprint=None,
            head_revision=None,
            paths=(),
            recorded_at="2099-01-01T00:00:00Z",
        )
        self.assertEqual(self.final(row.task_id), before)

    def test_deleting_the_repository_does_not_change_the_stored_answer(self):
        adapter = Worker(action=lambda root: (root / "a.txt").write_text("x"))
        _, row = self.start(adapter, ["a.txt"])
        before = self.final(row.task_id)
        shutil.rmtree(self.root)
        self.assertFalse(self.root.exists())
        self.assertEqual(self.final(row.task_id), before)
        self.assertEqual(
            self.states(row.task_id)["a.txt"], (PATH_PRESENT, KIND_FILE)
        )

    def test_changing_the_repository_does_not_change_the_stored_answer(self):
        adapter = Worker(action=lambda root: (root / "a.txt").write_text("x"))
        _, row = self.start(adapter, ["a.txt"])
        before = self.final(row.task_id)
        (self.root / "a.txt").unlink()
        (self.root / "surprise.txt").write_text("y", encoding="utf-8")
        self.assertEqual(self.final(row.task_id), before)

    def test_reading_mutates_nothing(self):
        adapter = Worker(action=lambda root: (root / "a.txt").write_text("x"))
        _, row = self.start(adapter, ["a.txt"])
        self.store.close()
        before = self.digest()
        store = TaskStore(self.config)
        self.addCleanup(store.close)
        for _ in range(5):
            store.turn_final_state(row.task_id, 1)
        store.close()
        self.assertEqual(self.digest(), before)

    def test_reading_never_touches_the_filesystem(self):
        adapter = Worker(action=lambda root: (root / "a.txt").write_text("x"))
        _, row = self.start(adapter, ["a.txt"])
        import cofferdam.workstation.tasks.finalstate as module

        def poison(*args, **kwargs):
            raise AssertionError("the read path invoked the observer")

        real_path, real_paths = module.observe_path, module.observe_paths
        module.observe_path, module.observe_paths = poison, poison
        try:
            observation = self.store.turn_final_state(row.task_id, 1)
        finally:
            module.observe_path, module.observe_paths = real_path, real_paths
        self.assertEqual(observation.paths[0].state, PATH_PRESENT)

    def test_the_fingerprint_survives_a_reopen(self):
        adapter = Worker(action=lambda root: (root / "a.txt").write_text("x"))
        _, row = self.start(adapter, ["a.txt"])
        before = self.final(row.task_id).fingerprint
        self.store.close()
        reopened = TaskStore(self.config)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.turn_final_state(row.task_id, 1).fingerprint, before)

    def test_a_turn_with_no_observation_reads_legacy_unknown(self):
        adapter = Worker(action=lambda root: None)
        _, row = self.start(adapter, ["a.txt"])
        with self.sql() as connection:
            connection.execute(
                "DELETE FROM task_turn_final_state WHERE task_id = ?", (row.task_id,)
            )
        observation = self.final(row.task_id)
        self.assertEqual(observation.state, OBSERVATION_LEGACY_UNKNOWN)
        self.assertFalse(observation.recorded)
        self.assertIsNone(observation.fingerprint)
        self.assertEqual(observation.paths, ())

    def test_a_lost_boundary_is_never_repaired_by_looking_later(self):
        """No recovery path re-probes. The gap stays a gap."""
        adapter = Worker(action=lambda root: (root / "a.txt").write_text("x"))
        _, row = self.start(adapter, ["a.txt"])
        with self.sql() as connection:
            connection.execute(
                "DELETE FROM task_turn_final_state WHERE task_id = ?", (row.task_id,)
            )
        service = self.service(Worker())
        service.recover_after_restart()
        self.assertEqual(self.final(row.task_id).state, OBSERVATION_LEGACY_UNKNOWN)

    def test_the_observer_version_is_stored(self):
        adapter = Worker(action=lambda root: None)
        _, row = self.start(adapter, ["a.txt"])
        self.assertEqual(
            self.final(row.task_id).observer_version, FINAL_STATE_OBSERVER_VERSION
        )

    def test_the_service_read_matches_the_store_read(self):
        adapter = Worker(action=lambda root: (root / "a.txt").write_text("x"))
        service, row = self.start(adapter, ["a.txt"])
        self.assertEqual(
            service.turn_final_state(row.task_id, 1),
            self.store.turn_final_state(row.task_id, 1),
        )


class NoSemanticConversionTests(FinalStateCase):
    def test_a_present_path_does_not_satisfy_a_change_criterion(self):
        """`path_operation(x, created)` asks what the worker did. This does not.

        The file exists before the turn and the worker never touches it, so the
        criterion is honestly not met while the path is honestly present. The two
        facts coexist and nothing joins them.
        """
        service = self.service(Worker(action=lambda root: None))
        row, _ = service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt="scenario: complete",
            origin="pwa",
            criteria=[
                {
                    "kind": "evidence",
                    "predicate": "path_operation",
                    "path": "seed.txt",
                    "operation": "created",
                }
            ],
            continuity={"mode": "root"},
        )
        self.assertEqual(
            self.states(row.task_id)["seed.txt"], (PATH_PRESENT, KIND_FILE)
        )
        record = self.store.evaluation(row.task_id, 1)
        if record is not None and record.results:
            self.assertNotEqual(record.results[0].result, "met")

    def test_no_evaluation_row_is_written_by_the_observer(self):
        adapter = Worker(action=lambda root: (root / "a.txt").write_text("x"))
        _, row = self.start(adapter, ["a.txt"])
        with self.sql() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM task_turn_evaluations WHERE task_id = ?"
                    " AND evaluator_version <> 1",
                    (row.task_id,),
                ).fetchone()[0],
                0,
            )

    def test_the_schema_version_is_at_least_ten(self):
        # PR14 arrived at v10; v11 is PR17's.
        self.assertGreaterEqual(SCHEMA_VERSION, 10)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
